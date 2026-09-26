"""Similarity between sighting accounts, sighting types, and links between records.

Everything here works on the event tags of accounts (see ``events``): what was
seen and how it behaved. Archive metadata (agency, series, region, decade)
and document wording play no part in the scores; agency and years are only
used afterwards to pick out links that bridge agencies or decades.

* Two accounts match when they share distinctive details: each tag weighs
  more the rarer it is (``log(N / df)``) and by its dimension (movement,
  sound, light and effects weigh most; time of day and duration least). A
  match needs at least two shared details of the phenomenon itself, one of
  them about its light, sound, movement, structure or effects.
* Sighting types are combinations of details that recur across records far
  more often than chance would give (lift), e.g. "Disc · Wobbling ·
  Metallic". Every account of a type has all of the type's details.
* Two records are similar through their best-matching pair of accounts.
"""
from __future__ import annotations

import difflib
import itertools
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from .events import BEHAVIOUR, BY_TAG, CORE, DIMENSIONS

MIN_SHARED_CORE = 2
MIN_SHARED_BEHAVIOUR = 1
MATCH_SCORE = 5.0  # evidence x cosine; ~the 80th percentile of qualifying pairs
LINK_SCORE = 7.5  # stronger bar for the "connections" list
LINK_MIN_DETAILS = 3  # ...and at least this many shared details of the phenomenon itself
SIGNATURE_DIMS = CORE | {"colour"}  # dimensions that define a sighting type
DEFINING = {"shape", "motion", "sound", "structure", "effect"}  # colour + brightness alone is not a type
WEAK = {"trail"}  # a vapour trail plus a colour is ordinary; it needs something else


@dataclass
class Acc:
    id: int
    doc: int
    page: int
    tags: list[str]
    year: int | None = None


def tag_weights(accounts: list[Acc]) -> dict[str, float]:
    n = len(accounts)
    df = Counter(t for a in accounts for t in set(a.tags))
    return {t: round(DIMENSIONS[BY_TAG[t].dim][1] * math.log(max(n, 2) / c), 4)
            for t, c in df.items() if t in BY_TAG}


def pair_detail(ta: list[str], tb: list[str], w: dict[str, float]) -> dict | None:
    """Score two accounts' tags; None when they don't share enough of substance."""
    shared = set(ta) & set(tb)
    core = [t for t in shared if BY_TAG[t].dim in CORE]
    beh = [t for t in shared if BY_TAG[t].dim in BEHAVIOUR]
    if len(core) < MIN_SHARED_CORE or len(beh) < MIN_SHARED_BEHAVIOUR:
        return None
    wa = math.sqrt(sum(w.get(t, 0) ** 2 for t in ta))
    wb = math.sqrt(sum(w.get(t, 0) ** 2 for t in tb))
    cos = sum(w.get(t, 0) ** 2 for t in shared) / (wa * wb) if wa and wb else 0.0
    evidence = sum(w.get(t, 0) for t in shared)
    order = sorted(shared, key=lambda t: (list(DIMENSIONS).index(BY_TAG[t].dim), -w.get(t, 0)))
    return {"score": round(evidence * cos, 3), "cosine": round(cos, 3), "shared": order}


_NORM = re.compile(r"[^a-z0-9]+")


def same_report(text_a: str, text_b: str) -> bool:
    """The two passages are copies of one report (OCR differences aside)."""
    a = _NORM.sub(" ", text_a.lower()).split()
    b = _NORM.sub(" ", text_b.lower()).split()
    if len(a) < 25 or len(b) < 25:
        return False
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(bl.size for bl in sm.get_matching_blocks() if bl.size >= 3)
    return matched / min(len(a), len(b)) >= 0.45


def _matrix(accounts: list[Acc], w: dict[str, float]):
    vocab = sorted(w)
    idx = {t: i for i, t in enumerate(vocab)}
    x = np.zeros((len(accounts), len(vocab)), dtype=np.float32)
    for i, a in enumerate(accounts):
        for t in a.tags:
            if t in idx:
                x[i, idx[t]] = w[t]
    dims = [BY_TAG[t].dim for t in vocab]
    core = np.array([d in CORE for d in dims], dtype=np.float32)
    beh = np.array([d in BEHAVIOUR for d in dims], dtype=np.float32)
    return x, core, beh


def account_pairs(accounts: list[Acc], w: dict[str, float], series: dict[int, str], min_score: float = MATCH_SCORE):
    """Yield (score, i, j) for qualifying account pairs from different records
    that are not in the same published series."""
    n = len(accounts)
    if n < 2:
        return
    x, core, beh = _matrix(accounts, w)
    b = (x > 0).astype(np.float32)
    norms = np.linalg.norm(x, axis=1)
    norms[norms == 0] = 1
    xn = x / norms[:, None]
    wv = x.max(axis=0)  # per-tag weight
    docs = np.array([a.doc for a in accounts])
    ser = np.array([series.get(a.doc, str(a.doc)) for a in accounts])
    bc, bb, bw = b * core, b * beh, b * wv
    step = 500
    for s in range(0, n, step):
        e = min(n, s + step)
        cos = xn[s:e] @ xn.T
        ev = bw[s:e] @ b.T
        ok = ((bc[s:e] @ bc.T) >= MIN_SHARED_CORE) & ((bb[s:e] @ bb.T) >= MIN_SHARED_BEHAVIOUR)
        ok &= docs[s:e, None] != docs[None, :]
        ok &= ser[s:e, None] != ser[None, :]
        ok &= np.arange(s, e)[:, None] < np.arange(n)[None, :]  # each pair once
        score = np.where(ok, ev * cos, 0)
        ii, jj = np.nonzero(score >= min_score)
        for i, j in zip(ii, jj):
            yield float(score[i, j]), s + int(i), int(j)


def record_similarity(accounts: list[Acc], w: dict[str, float], series: dict[int, str],
                      min_score: float = MATCH_SCORE) -> dict[tuple[int, int], dict]:
    """Best-matching account pair for every pair of records that has one."""
    best: dict[tuple[int, int], dict] = {}
    counts: Counter = Counter()
    for score, i, j in account_pairs(accounts, w, series, min_score):
        a, b = accounts[i], accounts[j]
        key = (a.doc, b.doc) if a.doc < b.doc else (b.doc, a.doc)
        counts[key] += 1
        if key not in best or score > best[key]["score"]:
            first, second = (a, b) if a.doc < b.doc else (b, a)
            best[key] = {"score": round(score, 3), "acc_a": first.id, "acc_b": second.id}
    for key, v in best.items():
        v["pairs"] = counts[key]
    return best


# ---------------------------------------------------------------------------
# sighting types

def sighting_types(accounts: list[Acc], w: dict[str, float], max_types: int = 16,
                   min_accounts: int = 6, min_records: int = 3) -> list[dict]:
    """Combinations of 2-3 details that recur across records more often than
    chance, chosen so that the types overlap little."""
    n = len(accounts)
    if n < min_accounts:
        return []
    sig = [sorted({t for t in a.tags if BY_TAG[t].dim in SIGNATURE_DIMS}, key=lambda t: -w.get(t, 0))[:10]
           for a in accounts]
    p = Counter(t for s in sig for t in s)
    members: dict[tuple, list[int]] = defaultdict(list)
    for i, s in enumerate(sig):
        for k in (2, 3):
            for combo in itertools.combinations(sorted(s), k):
                dims = {BY_TAG[t].dim for t in combo}
                defining = {BY_TAG[t].dim for t in combo if t not in WEAK} & DEFINING
                if len(dims) == k and dims & BEHAVIOUR and defining:
                    members[combo].append(i)
    cands = []
    for combo, idx in members.items():
        records = {accounts[i].doc for i in idx}
        if len(idx) < min_accounts or len(records) < min_records:
            continue
        expected = n * math.prod(p[t] / n for t in combo)
        lift = len(idx) / expected
        if lift < 1.6:
            continue
        # distinctive (lift) and recurring (records); triples need to earn their keep
        score = math.log(lift) * len(records) * (1.0 if len(combo) == 2 else 1.2)
        cands.append((score, combo, idx, lift))
    cands.sort(key=lambda c: -c[0])
    chosen: list[tuple] = []
    for score, combo, idx, lift in cands:
        s = set(idx)
        if any(len(s & c[4]) / len(s | c[4]) > 0.35 or set(combo) <= set(c[1]) or set(c[1]) <= set(combo)
               or (len(set(combo) & set(c[1])) >= 2 and len(s & c[4]) / len(s) > 0.2)
               for c in chosen):
            continue
        chosen.append((score, combo, idx, lift, s))
        if len(chosen) >= max_types:
            break
    types = []
    for n_, (score, combo, idx, lift, _) in enumerate(chosen, start=1):
        also = Counter(t for i in idx for t in set(accounts[i].tags) if t not in combo)
        years = sorted(accounts[i].year for i in idx if accounts[i].year)
        types.append({
            "id": n_,
            "signature": list(combo),
            "accounts": [accounts[i].id for i in idx],
            "records": sorted({accounts[i].doc for i in idx}),
            "lift": round(lift, 1),
            "also": [[t, round(c / len(idx), 2)] for t, c in also.most_common(8) if c / len(idx) >= 0.25],
            "years": [years[0], years[-1]] if years else None,
        })
    return types


def type_of_account(types: list[dict]) -> dict[int, int]:
    """Each account's type (the first, i.e. strongest, it belongs to)."""
    out: dict[int, int] = {}
    for t in types:
        for a in t["accounts"]:
            out.setdefault(a, t["id"])
    return out


def layout(accounts: list[Acc], w: dict[str, float], seed: int = 7) -> np.ndarray:
    """2-D positions in [0, 1]; accounts describing similar events sit close."""
    n = len(accounts)
    if n < 5:
        return np.column_stack([np.linspace(0.1, 0.9, n), np.full(n, 0.5)])
    from sklearn.manifold import TSNE

    x, _, _ = _matrix(accounts, w)
    norms = np.linalg.norm(x, axis=1)
    norms[norms == 0] = 1
    xn = x / norms[:, None]
    dist = np.clip(1 - xn @ xn.T, 0, None)
    np.fill_diagonal(dist, 0)
    pos = TSNE(n_components=2, metric="precomputed", init="random", random_state=seed,
               perplexity=min(30, max(5, n // 10))).fit_transform(dist)
    pos -= pos.min(axis=0)
    span = pos.max(axis=0)
    span[span == 0] = 1
    return 0.03 + 0.94 * pos / span
