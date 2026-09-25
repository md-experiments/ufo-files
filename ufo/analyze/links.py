"""Links between records: similarity, clusters of similar cases, and a 2-D map.

Two records are similar when they are described in similar words (TF-IDF over
title, description and summary) *and* share structured details: reported
shapes, observables (halo, hum...), domain, sensors, witnesses, region, era.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from ..classify.taxonomy import label

# facets used as structured features, with weights
FACET_WEIGHTS = {
    "observable": 1.4, "shape": 1.2, "domain": 0.8, "sensor": 0.8, "witness": 0.8,
    "region": 1.0, "era": 1.0, "topic": 0.5, "kind": 0.4,
}
TEXT_WEIGHT = 0.55

# words that appear in nearly every PURSUE description and carry no signal
STOPWORDS_EXTRA = {
    "aaro", "uap", "uaps", "anomalous", "phenomenon", "phenomena", "unidentified", "unresolved", "report",
    "reported", "reports", "submitted", "record", "records", "document", "file", "files", "domain",
    "resolution", "office", "department", "war", "comment", "video", "footage", "seconds", "description",
    "contained", "descriptive", "estimative", "language", "reflects", "does", "necessarily", "represent",
    "assessment", "aboard", "platform", "military", "military's", "accompanying", "mission",
}


@dataclass
class DocFeatures:
    id: int
    record_id: str
    title: str
    text: str
    tags: dict[str, set[str]] = field(default_factory=dict)
    series: str = ""  # records sharing a series are already linked by the publisher
    related: set[str] = field(default_factory=set)
    year: int | None = None


def series_key(record_id: str, description: str | None, shared_descriptions: set[str]) -> str:
    """Records that belong to one published series (FBI file sections, Blue Book
    boxes) or share a collection-wide description."""
    if description and description in shared_descriptions:
        return "desc:" + str(hash(description))
    m = re.match(r"^(\d+_[A-Za-z0-9-]+_[A-Za-z0-9-]+)", record_id)  # NARA "65_HS1-834228961_62-HQ-83894_Section_001"
    if m:
        return "nara:" + m.group(1)
    return "rec:" + record_id


def _tag_matrix(docs: list[DocFeatures]) -> tuple[np.ndarray, list[str]]:
    vocab: dict[str, int] = {}
    for d in docs:
        for facet, values in d.tags.items():
            if facet in FACET_WEIGHTS:
                for v in values:
                    vocab.setdefault(f"{facet}:{v}", len(vocab))
    m = np.zeros((len(docs), len(vocab)), dtype=np.float32)
    for i, d in enumerate(docs):
        for facet, values in d.tags.items():
            w = FACET_WEIGHTS.get(facet)
            if w is None:
                continue
            for v in values:
                m[i, vocab[f"{facet}:{v}"]] = w
    # idf weighting so ubiquitous tags ("military encounter") count less
    df = (m > 0).sum(axis=0)
    idf = np.log((1 + len(docs)) / (1 + df)) + 1
    m *= idf
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return m / norms, list(vocab)


def similarity_matrix(docs: list[DocFeatures]) -> np.ndarray:
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer

    n = len(docs)
    if n < 2:
        return np.zeros((n, n), dtype=np.float32)
    stop = list(ENGLISH_STOP_WORDS | STOPWORDS_EXTRA)
    try:
        tf = TfidfVectorizer(stop_words=stop, min_df=2, max_df=0.4, ngram_range=(1, 2), sublinear_tf=True,
                             token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z-]{2,}\b")
        x = tf.fit_transform([f"{d.title} {d.text}" for d in docs])
        text_sim = (x @ x.T).toarray().astype(np.float32)
    except ValueError:  # empty vocabulary (tiny corpora in tests)
        text_sim = np.zeros((n, n), dtype=np.float32)
    tags, _ = _tag_matrix(docs)
    tag_sim = tags @ tags.T
    sim = TEXT_WEIGHT * text_sim + (1 - TEXT_WEIGHT) * tag_sim
    np.fill_diagonal(sim, 1.0)
    return sim


def shared_details(a: DocFeatures, b: DocFeatures) -> list[str]:
    out = []
    for facet in ("observable", "shape", "sensor", "witness", "domain", "region", "era"):
        for v in sorted(a.tags.get(facet, set()) & b.tags.get(facet, set())):
            out.append(f"{facet}:{v}")
    return out


def cluster(sim: np.ndarray, threshold: float) -> np.ndarray:
    """Average-linkage agglomerative clustering on 1 - similarity."""
    from sklearn.cluster import AgglomerativeClustering

    n = sim.shape[0]
    if n < 2:
        return np.zeros(n, dtype=int)
    dist = np.clip(1 - sim, 0, None)
    model = AgglomerativeClustering(n_clusters=None, metric="precomputed", linkage="average",
                                    distance_threshold=1 - threshold)
    return model.fit_predict(dist)


def layout(sim: np.ndarray, seed: int = 7) -> np.ndarray:
    """2-D positions in [0, 1] where similar records sit close together."""
    n = sim.shape[0]
    if n < 5:
        return np.column_stack([np.linspace(0.1, 0.9, n), np.full(n, 0.5)])
    from sklearn.manifold import TSNE

    dist = np.clip(1 - sim, 0, None)
    np.fill_diagonal(dist, 0)
    pos = TSNE(n_components=2, metric="precomputed", init="random", random_state=seed,
               perplexity=min(30, max(5, n // 8))).fit_transform(dist)
    pos -= pos.min(axis=0)
    span = pos.max(axis=0)
    span[span == 0] = 1
    return 0.04 + 0.92 * pos / span


def describe_cluster(members: list[DocFeatures]) -> dict:
    """Name a cluster by the details most of its members share."""
    n = len(members)
    counts: dict[str, Counter] = defaultdict(Counter)
    for d in members:
        for facet, values in d.tags.items():
            for v in values:
                counts[facet][v] += 1
    common: list[tuple[str, str, float]] = []
    seen_labels: set[str] = set()
    for facet in ("observable", "shape", "region", "era", "domain", "sensor", "witness", "topic"):
        for v, c in counts[facet].most_common(3):
            if c / n >= 0.5 and _tag_label(facet, v) not in seen_labels:
                seen_labels.add(_tag_label(facet, v))
                common.append((facet, v, c / n))
    name_parts: list[str] = []
    for facet in ("shape", "observable", "sensor", "region", "era", "domain", "witness", "topic"):
        for f, v, share in common:
            lab = _tag_label(f, v)
            if f == facet and len(name_parts) < 3 and share >= 0.6 and lab not in name_parts:
                name_parts.append(lab)
    if not name_parts:
        name_parts = [_tag_label(f, v) for f, v, _ in common[:2]] or ["Mixed cases"]
    years = sorted(d.year for d in members if d.year)
    return {
        "name": " · ".join(name_parts),
        "common": [{"facet": f, "value": v, "label": _tag_label(f, v), "share": round(s, 2)} for f, v, s in common],
        "years": [years[0], years[-1]] if years else None,
    }


def _tag_label(facet: str, value: str) -> str:
    if facet == "observable":
        from .features import BY_KEY

        return BY_KEY[value].label if value in BY_KEY else value
    if facet == "place":
        from .places import place_label

        return place_label(value)
    return label(facet, value)
