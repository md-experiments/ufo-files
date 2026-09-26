"""Cross-record analysis: the pipeline stage that looks for connections.

After new records are processed, this stage
1. extracts recurring observable details (halo, hum, smell, right-angle turns,
   engine failure...), dates and places from every page that reads like a
   sighting report, keeping the evidence sentence for each;
2. finds *waves* — periods when many more reports were made than usual — and
   what distinguished them;
3. measures which details tend to be reported together;
4. cuts sighting pages into accounts (paragraphs), tags each with what was
   observed (shape, light, sound, movement, effects...), groups accounts into
   recurring sighting types, and links records whose accounts describe the
   same kind of event (see ``events`` and ``signatures``). Wording and
   archive metadata are not used for this.

Everything is recomputed from the database, so it stays in step with new data.
"""
from __future__ import annotations

import logging
import statistics
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..db import Account, AnalysisResult, Document, Mention, Observation, session_scope, utcnow
from .extract import extract_all
from .features import BY_KEY, GROUPS, OBSERVABLES
from .links import series_key
from .places import STATES, place_label

log = logging.getLogger(__name__)

MAP_MAX = 3000  # accounts drawn on the map of sighting accounts
ANALYSIS_VERSION = 3  # bump when stored results change shape; triggers a rebuild on startup
MIN_PAIR_COUNT = 4  # co-occurrence pairs seen fewer times are noise


def run_analysis() -> dict:
    """Extract, analyse and store all results. Returns a short summary."""
    with session_scope() as db:
        totals = extract_all(db)
    if totals["tagging"]["tagged"] or totals["tagging"]["failed"]:
        log.info("LLM tagging: %(tagged)d tagged, %(cached)d cached, %(failed)d failed (kept rules)", totals["tagging"])
    with session_scope() as db:
        results = compute(db)
        # a partial run (some LLM calls failed) is retried on the next pipeline run
        results["tagger"] = totals["tagger"] + (":partial" if totals["tagging"]["failed"] else "")
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


def analysis_outdated(db: Session) -> bool:
    """True when results exist but were computed by an older version, or by a
    different tagger (an LLM key was added, removed or changed)."""
    from .llm_tags import tagger_id

    if not db.scalar(select(func.count()).select_from(AnalysisResult)):
        return False
    version, tagger = db.get(AnalysisResult, "version"), db.get(AnalysisResult, "tagger")
    return (version is None or version.data != ANALYSIS_VERSION
            or tagger is None or tagger.data != tagger_id())


# ---------------------------------------------------------------------------

def compute(db: Session) -> dict:
    docs = {d.id: d for d in db.scalars(select(Document).where(Document.status == "classified"))}
    obs = db.execute(select(Observation.document_id, Observation.page_no, Observation.feature, Observation.snippet)).all()
    dates = db.execute(select(Mention.document_id, Mention.page_no, Mention.value, Mention.precision)
                       .where(Mention.kind == "date")).all()
    places = db.execute(select(Mention.document_id, Mention.page_no, Mention.value).where(Mention.kind == "place")).all()

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
    results.update(connections(db, docs, unit_year))
    results["version"] = ANALYSIS_VERSION
    results["overview"] = {
        "sighting_units": len(all_units),
        "observations": len(obs),
        "records_with_observations": len({o[0] for o in obs}),
        "date_mentions": len(dates),
        "waves": len(results["timeline"]["waves"]),
        "clusters": len(results["clusters"]),
        "links": len(results["links"]),
        "accounts": results["accounts"],
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


def connections(db: Session, docs, unit_year) -> dict:
    """Sighting types, the map of accounts, similar records and cross-links,
    all from the event tags of sighting accounts (see ``signatures``)."""
    from .events import dim_of
    from .signatures import (LINK_MIN_DETAILS, LINK_SCORE, SIGNATURE_DIMS, Acc, layout, pair_detail,
                             record_similarity, same_report, sighting_types, tag_weights, type_of_account)

    empty = {"clusters": [], "map": [], "links": [], "similar": {}, "tag_weights": {}, "accounts": 0}
    rows = db.execute(select(Account.id, Account.document_id, Account.page_no, Account.tags)
                      .where(Account.document_id.in_(list(docs)) if docs else Account.id < 0)
                      .order_by(Account.id)).all()
    accs = [Acc(r.id, r.document_id, r.page_no, list(r.tags or []), unit_year((r.document_id, r.page_no)))
            for r in rows]
    if len(accs) < 2:
        return {**empty, "accounts": len(accs)}
    by_id = {a.id: a for a in accs}
    w = tag_weights(accs)
    desc_counts = Counter(docs[i].description for i in docs if docs[i].description)
    shared_desc = {d for d, c in desc_counts.items() if c >= 4}
    series = {i: series_key(d.record_id, d.description, shared_desc) for i, d in docs.items()}

    # sighting types, and where each account sits on the map
    types = sighting_types(accs, w)
    account_type = type_of_account(types)
    mapped = accs
    if len(accs) > MAP_MAX:  # keep every typed account, sample the rest
        import random

        rest = [a for a in accs if a.id not in account_type]
        random.Random(5).shuffle(rest)
        typed = [a for a in accs if a.id in account_type]
        mapped = typed + rest[: max(0, MAP_MAX - len(typed))]
    pos = layout(mapped, w)
    points = [{"id": a.id, "doc": a.doc, "page": a.page, "x": round(float(pos[i, 0]), 4),
               "y": round(float(pos[i, 1]), 4), "cluster": account_type.get(a.id), "tags": a.tags[:6]}
              for i, a in enumerate(mapped)]
    where = {p["id"]: (p["x"], p["y"]) for p in points}
    for t in types:
        members = [by_id[a] for a in t["accounts"]]
        agencies = Counter(docs[a.doc].agency for a in members if docs[a.doc].agency)
        years = [a.year for a in members if a.year]
        xy = [where[a.id] for a in members if a.id in where]
        t.update({
            "size": len(members), "record_count": len(t["records"]),
            "agencies": [a for a, _ in agencies.most_common(4)], "agency_count": len(agencies),
            "span_years": (max(years) - min(years)) if years else 0,
            "centroid": [round(sum(x for x, _ in xy) / len(xy), 4), round(sum(y for _, y in xy) / len(xy), 4)] if xy else [0.5, 0.5],
        })

    # records linked through their best-matching accounts
    best = record_similarity(accs, w, series)
    similar: dict[str, list] = defaultdict(list)
    for (da, dbb), v in best.items():
        shared = pair_detail(by_id[v["acc_a"]].tags, by_id[v["acc_b"]].tags, w)["shared"]
        if sum(1 for t in shared if dim_of(t) in SIGNATURE_DIMS) < LINK_MIN_DETAILS:
            continue
        similar[str(da)].append([dbb, v["score"], shared[:6], v["acc_a"], v["acc_b"]])
        similar[str(dbb)].append([da, v["score"], shared[:6], v["acc_b"], v["acc_a"]])
    similar = {k: sorted(v, key=lambda s: -s[1])[:6] for k, v in similar.items()}

    links = []
    for (da, dbb), v in sorted(best.items(), key=lambda kv: -kv[1]["score"]):
        if v["score"] < LINK_SCORE:
            break
        a, b = docs[da], docs[dbb]
        if a.record_id in (b.related_ids or []) or b.record_id in (a.related_ids or []):
            continue
        ya, yb = by_id[v["acc_a"]].year or a.incident_year, by_id[v["acc_b"]].year or b.incident_year
        gap = abs(ya - yb) if ya and yb else 0
        cross_agency = bool(a.agency and b.agency and a.agency != b.agency)
        if not cross_agency and gap < 15:
            continue  # the interesting links connect different agencies or distant years
        detail = pair_detail(by_id[v["acc_a"]].tags, by_id[v["acc_b"]].tags, w)
        links.append({"a": da, "b": dbb, "score": v["score"], "pairs": v["pairs"], "acc_a": v["acc_a"],
                      "acc_b": v["acc_b"], "shared": detail["shared"], "cross_agency": cross_agency,
                      "years_apart": gap,
                      "details": sum(1 for t in detail["shared"] if dim_of(t) in SIGNATURE_DIMS)})
    texts = dict(db.execute(select(Account.id, Account.text).where(
        Account.id.in_([x for link in links for x in (link["acc_a"], link["acc_b"])]))).all()) if links else {}
    per_record: Counter = Counter()
    kept = []
    for link in links:
        link["same_report"] = same_report(texts.get(link["acc_a"], ""), texts.get(link["acc_b"], ""))
        # two shared details can be chance; three, or a copy of the same report, is a lead
        if not link["same_report"] and link["details"] < LINK_MIN_DETAILS:
            continue
        if per_record[link["a"]] >= 3 or per_record[link["b"]] >= 3:
            continue  # keep the list varied: at most 3 links per record
        per_record[link["a"]] += 1
        per_record[link["b"]] += 1
        kept.append(link)
    links = kept[:60]
    return {"clusters": types, "map": points, "links": links, "similar": similar, "tag_weights": w,
            "accounts": len(accs)}
