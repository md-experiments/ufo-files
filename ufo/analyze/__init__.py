"""Cross-record analysis: the pipeline stage that looks for connections.

After new records are processed, this stage
1. extracts recurring observable details (halo, hum, smell, right-angle turns,
   engine failure...), dates and places from every page that reads like a
   sighting report, keeping the evidence sentence for each;
2. finds *waves* — periods when many more reports were made than usual — and
   what distinguished them;
3. measures which details tend to be reported together;
4. links similar records across different collections and groups them into
   clusters of similar cases, laid out on a 2-D "map of cases".

Everything is recomputed from the database, so it stays in step with new data.
"""
from __future__ import annotations

import logging
import re
import statistics
from collections import Counter, defaultdict
from datetime import date

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import AnalysisResult, Document, Mention, Observation, Tag, session_scope, utcnow
from .extract import extract_all
from .features import BY_KEY, GROUPS, OBSERVABLES
from .links import DocFeatures, cluster, describe_cluster, layout, series_key, shared_details, similarity_matrix
from .places import STATES, place_label

log = logging.getLogger(__name__)

CLUSTER_THRESHOLD = 0.34  # min average similarity inside a cluster
LINK_THRESHOLD = 0.36  # min similarity for a "discovered link" between collections
MIN_PAIR_COUNT = 4  # co-occurrence pairs seen fewer times are noise


_SENT = re.compile(r"(?<=[.!?])\s+")


def _norm_sentence(s: str) -> str:
    return re.sub(r"[\d,]+", "#", s.lower()).strip()


def boilerplate_sentences(texts, min_docs: int = 5) -> set[str]:
    """Sentences (numbers normalised) repeated across many publisher descriptions:
    templates like "... submitted a report ... from an infrared sensor aboard a
    U.S. military platform"."""
    c: Counter = Counter()
    for t in texts:
        c.update({_norm_sentence(s) for s in _SENT.split(t) if len(s) > 30})
    return {s for s, n in c.items() if n >= min_docs}


def strip_boilerplate(text: str, boiler: set[str]) -> str:
    return " ".join(s for s in _SENT.split(text) if _norm_sentence(s) not in boiler)


def run_analysis() -> dict:
    """Extract, analyse and store all results. Returns a short summary."""
    with session_scope() as db:
        totals = extract_all(db)
    with session_scope() as db:
        results = compute(db)
        db.execute(delete(AnalysisResult))
        now = utcnow()
        for key, data in results.items():
            db.add(AnalysisResult(key=key, data=data, computed_at=now))
    summary = results["overview"]
    log.info("analysis: %d sighting pages, %d observations, %d waves, %d clusters, %d links",
             totals["sighting_units"], totals["observations"], len(results["timeline"]["waves"]),
             len(results["clusters"]), len(results["links"]))
    return summary


def load_results(db: Session) -> dict:
    rows = db.scalars(select(AnalysisResult)).all()
    out = {r.key: r.data for r in rows}
    if rows:
        out["computed_at"] = max(r.computed_at for r in rows)
    return out


# ---------------------------------------------------------------------------

def compute(db: Session) -> dict:
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.status == "classified"))}
    obs = db.execute(select(Observation.document_id, Observation.page_no, Observation.feature, Observation.snippet)).all()
    dates = db.execute(select(Mention.document_id, Mention.page_no, Mention.value, Mention.precision)
                       .where(Mention.kind == "date")).all()
    places = db.execute(select(Mention.document_id, Mention.page_no, Mention.value).where(Mention.kind == "place")).all()
    tags: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for doc_id, facet, value in db.execute(select(Tag.document_id, Tag.facet, Tag.value)):
        tags[doc_id][facet].add(value)

    # a "unit" is one sighting page (or a record's description, page 0)
    unit_features: dict[tuple[int, int], set[str]] = defaultdict(set)
    for doc_id, page_no, feature, _ in obs:
        unit_features[(doc_id, page_no)].add(feature)
    unit_years: dict[tuple[int, int], list[int]] = defaultdict(list)
    unit_months: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    for doc_id, page_no, value, precision in dates:
        d = date.fromisoformat(value)
        unit_years[(doc_id, page_no)].append(d.year)
        if precision in ("day", "month"):
            unit_months[(doc_id, page_no)].add((d.year, d.month))
    unit_places: dict[tuple[int, int], set[str]] = defaultdict(set)
    for doc_id, page_no, value in places:
        unit_places[(doc_id, page_no)].add(value)

    def unit_year(u: tuple[int, int]) -> int | None:
        ys = unit_years.get(u)
        if ys:
            return int(statistics.median_low(ys))
        doc = docs.get(u[0])
        return doc.incident_year if doc else None

    all_units = set(unit_features) | set(unit_years) | set(unit_places)
    results = {
        "features": feature_summary(docs, obs, unit_year),
        "cooccurrence": cooccurrence(unit_features),
        "feature_decades": feature_by_decade(unit_features, unit_year, all_units),
        "timeline": timeline(docs, unit_months, unit_years, unit_features, unit_places),
        "places": place_summary(unit_places, unit_year),
    }
    results.update(connections(docs, tags, obs, unit_year))
    from .matching import link_previews

    link_previews(db, results["links"], boilerplate_sentences(d.description or "" for d in docs.values()))
    results["overview"] = {
        "sighting_units": len(all_units),
        "observations": len(obs),
        "records_with_observations": len({o[0] for o in obs}),
        "date_mentions": len(dates),
        "waves": len(results["timeline"]["waves"]),
        "clusters": len(results["clusters"]),
        "links": len(results["links"]),
    }
    return results


def _decade(year: int | None) -> str | None:
    if not year or year < 1940:
        return None
    return f"{year // 10 * 10}s"


def feature_summary(docs, obs, unit_year) -> list[dict]:
    by_feature: dict[str, list] = defaultdict(list)
    for doc_id, page_no, feature, snippet in obs:
        by_feature[feature].append((doc_id, page_no, snippet))
    out = []
    for o in OBSERVABLES:
        hits = by_feature.get(o.key, [])
        years = [y for y in (unit_year((d, p)) for d, p, _ in hits) if y]
        decades = Counter(_decade(y) for y in years if _decade(y))
        # evidence: one example per record, records with most hits first
        per_doc = Counter(d for d, _, _ in hits)
        examples, seen = [], set()
        for doc_id, page_no, snippet in sorted(hits, key=lambda h: (-per_doc[h[0]], h[0], h[1])):
            if doc_id in seen or doc_id not in docs:
                continue
            seen.add(doc_id)
            doc = docs[doc_id]
            examples.append({"doc_id": doc_id, "record_id": doc.record_id, "title": doc.title,
                             "page": page_no, "snippet": snippet, "year": unit_year((doc_id, page_no))})
        out.append({
            "key": o.key, "label": o.label, "group": o.group, "group_label": GROUPS[o.group],
            "description": o.description, "pages": len({(d, p) for d, p, _ in hits}),
            "records": len(per_doc), "first_year": min(years) if years else None,
            "last_year": max(years) if years else None, "decades": dict(decades), "examples": examples,
        })
    return out


def cooccurrence(unit_features: dict) -> list[dict]:
    units = [f for f in unit_features.values() if f]
    n = len(units)
    if not n:
        return []
    single = Counter(k for u in units for k in u)
    pair = Counter((a, b) for u in units for a in u for b in u if a < b)
    out = []
    for (a, b), c in pair.items():
        if c < MIN_PAIR_COUNT:
            continue
        lift = (c / n) / ((single[a] / n) * (single[b] / n))
        out.append({"a": a, "b": b, "a_label": BY_KEY[a].label, "b_label": BY_KEY[b].label,
                    "count": c, "lift": round(lift, 2)})
    out.sort(key=lambda r: (-r["lift"], -r["count"]))
    return out


def feature_by_decade(unit_features, unit_year, all_units) -> dict:
    """Share of sighting pages from each decade that mention each observable."""
    per_decade = Counter(_decade(unit_year(u)) for u in all_units)
    per_decade.pop(None, None)
    decades = sorted(per_decade)
    counts: dict[str, Counter] = defaultdict(Counter)
    for u, feats in unit_features.items():
        dec = _decade(unit_year(u))
        if dec:
            for f in feats:
                counts[f][dec] += 1
    rows = []
    for o in OBSERVABLES:
        cells = []
        for dec in decades:
            c = counts[o.key][dec]
            cells.append({"decade": dec, "count": c, "share": round(c / per_decade[dec], 4) if per_decade[dec] else 0})
        if sum(c["count"] for c in cells) >= 5:  # rarer details would look falsely dramatic
            rows.append({"key": o.key, "label": o.label, "group": o.group, "cells": cells})
    return {"decades": decades, "totals": [per_decade[d] for d in decades], "rows": rows}


def timeline(docs, unit_months, unit_years, unit_features, unit_places) -> dict:
    """Reports per year and month, weighting each sighting page by 1/(months it mentions).

    A "wave" is a year with far more reports than the surrounding decade."""
    by_year: Counter = Counter()
    by_month: Counter = Counter()
    for u, months in unit_months.items():
        w = 1 / len(months)
        for y, m in months:
            by_month[(y, m)] += w
            by_year[y] += w
    # units with only a year (e.g. modern videos) count on the year alone
    for u, ys in unit_years.items():
        if u not in unit_months:
            yset = set(ys)
            for y in yset:
                by_year[y] += 1 / len(yset)
    if not by_year:
        return {"years": [], "waves": [], "months": {}}
    first, last = min(by_year), max(by_year)
    years = list(range(max(1940, first), last + 1))
    counts = [by_year.get(y, 0.0) for y in years]

    waves: list[dict] = []
    flagged = []
    for i, y in enumerate(years):
        window = [counts[j] for j in range(max(0, i - 10), min(len(years), i + 11)) if abs(j - i) > 1]
        base = statistics.median(window) if window else 0
        if counts[i] >= 10 and counts[i] >= 2.5 * base + 3:
            flagged.append((y, counts[i], base))
    # merge consecutive flagged years into one wave
    for y, c, base in flagged:
        if waves and y - waves[-1]["end"] <= 1:
            w = waves[-1]
            w["end"], w["count"] = y, w["count"] + c
            if c > w["peak_count"]:
                w["peak_year"], w["peak_count"] = y, c
            w["baseline"] = max(w["baseline"], base)
        else:
            waves.append({"start": y, "end": y, "count": c, "peak_year": y, "peak_count": c, "baseline": base})

    # what distinguished each wave
    total_units = [u for u in unit_features]
    overall = Counter(f for u in total_units for f in unit_features[u])
    n_all = max(1, len(total_units))
    for w in waves:
        in_wave = [u for u in set(unit_months) | set(unit_years)
                   if any(w["start"] <= y <= w["end"] for y in unit_years.get(u, []))]
        feats = Counter(f for u in in_wave for f in unit_features.get(u, ()))
        n_w = max(1, len(in_wave))
        distinct = []
        for f, c in feats.items():
            if c >= 3:
                lift = (c / n_w) / (overall[f] / n_all)
                distinct.append({"key": f, "label": BY_KEY[f].label, "count": c, "lift": round(lift, 2)})
        common = sorted(distinct, key=lambda r: -r["count"])[:6]
        distinct = sorted((r for r in distinct if r["lift"] >= 1.3), key=lambda r: -r["lift"])[:6]
        places = Counter(p for u in in_wave for p in unit_places.get(u, ()))
        month_counts = [round(sum(by_month.get((y, m), 0) for y in range(w["start"], w["end"] + 1)), 2)
                        for m in range(1, 13)]
        peak_month = max(range(12), key=lambda m: month_counts[m]) + 1 if any(month_counts) else None
        recs = Counter(u[0] for u in in_wave)
        w.update({
            "count": round(w["count"], 1), "peak_count": round(w["peak_count"], 1), "baseline": round(w["baseline"], 1),
            "ratio": round(w["peak_count"] / max(1.0, w["baseline"]), 1),
            "pages": len(in_wave), "records": len(recs),
            "features": common, "distinctive": distinct,
            "places": [{"key": p, "label": place_label(p), "count": c} for p, c in places.most_common(6)],
            "month_counts": month_counts, "peak_month": peak_month,
            "top_records": [{"doc_id": d, "record_id": docs[d].record_id, "title": docs[d].title, "pages": c}
                            for d, c in recs.most_common(5) if d in docs],
        })
    months = {str(y): [round(by_month.get((y, m), 0), 2) for m in range(1, 13)]
              for y in years if any(by_month.get((y, m)) for m in range(1, 13))}
    return {"years": [{"year": y, "count": round(c, 2)} for y, c in zip(years, counts)],
            "waves": waves, "months": months}


def place_summary(unit_places, unit_year) -> dict:
    counts: Counter = Counter()
    decades: dict[str, Counter] = defaultdict(Counter)
    for u, ps in unit_places.items():
        dec = _decade(unit_year(u))
        for p in ps:
            counts[p] += 1
            if dec:
                decades[p][dec] += 1
    states = {k[3:]: c for k, c in counts.items() if k.startswith("US-")}
    grid = [{"code": code, "name": name, "row": r, "col": c, "count": states.get(code, 0)}
            for code, (name, _, r, c) in STATES.items()]
    world = [{"key": k, "label": place_label(k), "count": c} for k, c in counts.most_common() if not k.startswith("US-")]
    top = [k for k, _ in counts.most_common(12)]
    all_decades = sorted({d for p in top for d in decades[p]})
    return {
        "us_grid": grid,
        "world": world[:16],
        "top": [{"key": k, "label": place_label(k), "count": counts[k],
                 "decades": [decades[k].get(d, 0) for d in all_decades]} for k in top],
        "decades": all_decades,
    }


def connections(docs, tags, obs, unit_year) -> dict:
    ids = sorted(docs)
    if not ids:
        return {"clusters": [], "map": [], "links": [], "similar": {}}
    doc_obs: dict[int, set[str]] = defaultdict(set)
    for doc_id, _, feature, _ in obs:
        doc_obs[doc_id].add(feature)
    desc_counts = Counter(docs[i].description for i in ids if docs[i].description)
    shared = {d for d, c in desc_counts.items() if c >= 4}
    boiler = boilerplate_sentences(docs[i].description or "" for i in ids)
    feats: list[DocFeatures] = []
    for i in ids:
        d = docs[i]
        t = {facet: set(v) for facet, v in tags.get(i, {}).items() if facet not in ("agency", "assessment")}
        t["observable"] = doc_obs.get(i, set())
        desc = strip_boilerplate(d.description or "", boiler) if d.description not in shared else ""
        summary = d.summary if d.summary and d.summary != (d.description or "")[: len(d.summary)] else ""
        text = " ".join(x for x in (desc, summary) if x)
        feats.append(DocFeatures(
            id=i, record_id=d.record_id, title=d.title, text=text, tags=t,
            series=series_key(d.record_id, d.description, shared),
            related=set(d.related_ids or []), year=d.incident_year,
        ))
    sim = similarity_matrix(feats)
    labels = cluster(sim, CLUSTER_THRESHOLD)
    pos = layout(sim)

    groups: dict[int, list[int]] = defaultdict(list)
    for idx, lab in enumerate(labels):
        groups[int(lab)].append(idx)
    clusters = []
    for lab, members in groups.items():
        if len(members) < 3:
            continue
        # a cluster that is just one published series is not a discovery
        series = {feats[m].series for m in members}
        sub = sim[np.ix_(members, members)]
        cohesion = float((sub.sum() - len(members)) / max(1, len(members) * (len(members) - 1)))
        info = describe_cluster([feats[m] for m in members])
        agencies = Counter(docs[feats[m].id].agency for m in members if docs[feats[m].id].agency)
        decades = {feats[m].year // 10 for m in members if feats[m].year}
        yrs = [feats[m].year for m in members if feats[m].year]
        releases = {docs[feats[m].id].release_id for m in members}
        clusters.append({
            "members": [feats[m].id for m in members],
            "size": len(members), "series": len(series), "cohesion": round(cohesion, 3),
            "agency_count": len(agencies), "decade_count": len(decades), "release_count": len(releases),
            "span_years": (max(yrs) - min(yrs)) if yrs else 0,
            "centroid": [round(float(pos[members, 0].mean()), 4), round(float(pos[members, 1].mean()), 4)],
            "agencies": [a for a, _ in agencies.most_common(3)],
            **info,
        })
    # clusters that bridge agencies or decades first: those are the connections
    clusters.sort(key=lambda c: (-(c["agency_count"] > 1 or c["span_years"] >= 15), -c["size"]))
    for n, c in enumerate(clusters, start=1):
        c["id"] = n
    member_cluster = {m: c["id"] for c in clusters for m in c["members"]}

    # discovered links: strong similarity between records of different series
    # that the publisher did not already pair
    links = []
    n = len(feats)
    for a in range(n):
        for b in range(a + 1, n):
            s = float(sim[a, b])
            if s < LINK_THRESHOLD:
                continue
            fa, fb = feats[a], feats[b]
            if fa.series == fb.series or fa.record_id in fb.related or fb.record_id in fa.related:
                continue
            da, db_ = docs[fa.id], docs[fb.id]
            cross_agency = bool(da.agency and db_.agency and da.agency != db_.agency)
            gap = abs(fa.year - fb.year) if fa.year and fb.year else 0
            # the interesting links connect different agencies or distant years
            if not cross_agency and gap < 15:
                continue
            links.append({"a": fa.id, "b": fb.id, "score": round(s, 3), "shared": shared_details(fa, fb),
                          "cross_agency": cross_agency, "years_apart": gap})
    links.sort(key=lambda l: (-l["cross_agency"], -l["score"]))
    per_record: Counter = Counter()
    kept = []
    for l in links:  # keep the list varied: at most 3 links per record
        if per_record[l["a"]] >= 3 or per_record[l["b"]] >= 3:
            continue
        per_record[l["a"]] += 1
        per_record[l["b"]] += 1
        kept.append(l)
    links = kept[:60]

    similar = {}
    for a in range(n):
        order = np.argsort(-sim[a])
        top = []
        for b in order[1:]:
            if len(top) >= 6 or sim[a, b] < 0.25:
                break
            if feats[b].series == feats[a].series:
                continue
            top.append([feats[b].id, round(float(sim[a, b]), 3), shared_details(feats[a], feats[b])[:6]])
        similar[str(feats[a].id)] = top

    points = [{"id": f.id, "x": round(float(pos[i, 0]), 4), "y": round(float(pos[i, 1]), 4),
               "cluster": member_cluster.get(f.id), "media": docs[f.id].media_type, "year": f.year}
              for i, f in enumerate(feats)]
    return {"clusters": clusters, "map": points, "links": links, "similar": similar}
