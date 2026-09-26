"""Queries and view-models for the Patterns page and related views."""
from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from ..analyze import load_results
from ..analyze.features import BY_KEY, GROUPS, OBSERVABLES
from ..analyze.places import place_label
from ..classify.taxonomy import FACETS, label
from ..db import Document, Mention, Observation, Release, Tag
from . import charts

MEDIA_CLS = {"pdf": "s-pdf", "video": "s-video", "image": "s-image", "audio": "s-audio"}


def doc_titles(db: Session, ids) -> dict[int, dict]:
    ids = list({int(i) for i in ids})
    out = {}
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        for d in db.execute(select(Document.id, Document.record_id, Document.title, Document.incident_year,
                                   Document.agency, Document.media_type).where(Document.id.in_(chunk))):
            out[d.id] = {"id": d.id, "record_id": d.record_id, "title": d.title, "year": d.incident_year,
                         "agency": d.agency, "media": d.media_type}
    return out


def tag_label(tag: str) -> str:
    facet, _, value = tag.partition(":")
    if facet == "observable":
        return BY_KEY[value].label if value in BY_KEY else value
    return label(facet, value)


def patterns_context(db: Session) -> dict:
    r = load_results(db)
    if not r:
        return {"ready": False}
    tl = r["timeline"]
    fd = r["feature_decades"]
    features = sorted(r["features"], key=lambda f: -f["records"])
    grouped = defaultdict(list)
    for f in r["features"]:
        if f["pages"]:
            grouped[f["group"]].append(f)
    groups = [(g, GROUPS[g], sorted(grouped[g], key=lambda f: -f["pages"])) for g in GROUPS if grouped.get(g)]

    heat = charts.heatmap(
        [(row["key"], row["label"]) for row in fd["rows"]],
        fd["decades"],
        [[c["share"] for c in row["cells"]] for row in fd["rows"]],
        [[f'{row["label"]}, {c["decade"]}: {c["count"]} of {tot} sighting pages ({100 * c["share"]:.1f}%)'
          for c, tot in zip(row["cells"], fd["totals"])] for row in fd["rows"]],
        per_row=True, row_link="/patterns/evidence?feature={key}",
    )

    clusters = r["clusters"]
    member_ids = {m for c in clusters for m in c["members"]}
    link_ids = {x for l in r["links"] for x in (l["a"], l["b"])}
    titles = doc_titles(db, member_ids | link_ids | {p["id"] for p in r["map"]})
    for c in clusters:
        c["docs"] = [titles[m] for m in c["members"] if m in titles]
    links = []
    for l in r["links"]:
        if l["a"] in titles and l["b"] in titles:
            links.append({**l, "da": titles[l["a"]], "db": titles[l["b"]],
                          "shared_labels": [tag_label(t) for t in l["shared"]]})

    map_titles = {i: f'{t["record_id"]}: {t["title"]}' for i, t in titles.items()}
    cooc = r["cooccurrence"]
    top_pairs = sorted(cooc, key=lambda p: (-p["lift"]))[:14]
    max_lift = max((p["lift"] for p in top_pairs), default=1)

    for w in tl["waves"]:
        w["months_svg"] = charts.month_bars(w["month_counts"], w["peak_month"], f'{w["start"]}')
    rare = sorted((f for f in r["features"] if 0 < f["records"] <= 12), key=lambda f: f["records"])

    return {
        "ready": True,
        "computed_at": r.get("computed_at"),
        "overview": r["overview"],
        "timeline_svg": charts.responsive(charts.timeline(tl["years"], tl["waves"]),
                                          charts.timeline(tl["years"], tl["waves"], narrow=True)),
        "waves": tl["waves"],
        "features": features,
        "groups": groups,
        "decades": fd["decades"],
        "heatmap": heat,
        "sparks": {f["key"]: charts.decade_spark(f["decades"], fd["decades"]) for f in r["features"]},
        "pairs": top_pairs,
        "max_lift": max_lift,
        "tilemap": charts.us_tilemap(r["places"]["us_grid"]),
        "world": r["places"]["world"],
        "top_places": r["places"]["top"],
        "place_decades": r["places"]["decades"],
        "clusters": clusters,
        "case_map": charts.responsive(
            charts.case_map(r["map"], clusters, map_titles),
            charts.case_map(r["map"], clusters, map_titles, narrow=True)),
        "links": links,
        "rare": rare,
    }


def evidence(db: Session, feature: str | None, year: int | None, place: str | None, limit: int = 300) -> dict:
    """Sighting pages matching a detail, a year mentioned, and/or a place."""
    units = None

    def restrict(q):
        nonlocal units
        found = {(d, p) for d, p in db.execute(q)}
        units = found if units is None else units & found

    if feature:
        restrict(select(Observation.document_id, Observation.page_no).where(Observation.feature == feature))
    if year:
        restrict(select(Mention.document_id, Mention.page_no).where(
            Mention.kind == "date", Mention.value.like(f"{year}-%")))
    if place:
        restrict(select(Mention.document_id, Mention.page_no).where(Mention.kind == "place", Mention.value == place))
    units = units or set()
    total = len(units)
    chosen = sorted(units)[:limit]
    doc_ids = {d for d, _ in chosen}
    titles = doc_titles(db, doc_ids)
    obs = defaultdict(list)
    if doc_ids:
        for d, p, f, snip in db.execute(select(Observation.document_id, Observation.page_no, Observation.feature,
                                               Observation.snippet).where(Observation.document_id.in_(doc_ids))):
            obs[(d, p)].append((f, snip))
    rows = []
    for d, p in chosen:
        items = obs.get((d, p), [])
        focus = [s for f, s in items if f == feature] if feature else []
        rows.append({"doc": titles.get(d), "page": p, "snippet": (focus or [s for _, s in items] or [""])[0],
                     "features": [(f, BY_KEY[f].label) for f, _ in items if f in BY_KEY]})
    # group by record so long files don't flood the list
    by_doc = defaultdict(list)
    for row in rows:
        by_doc[row["doc"]["id"] if row["doc"] else 0].append(row)
    records = sorted(by_doc.values(), key=lambda rs: -len(rs))
    return {"total": total, "records": records, "shown": len(rows),
            "feature": BY_KEY.get(feature) if feature else None, "year": year,
            "place": place, "place_label": place_label(place) if place else None}


def document_patterns(db: Session, doc: Document) -> dict:
    """Details found in one record, and the most similar records elsewhere."""
    obs = db.execute(select(Observation.page_no, Observation.feature, Observation.snippet)
                     .where(Observation.document_id == doc.id).order_by(Observation.page_no)).all()
    per_feature: dict[str, list] = defaultdict(list)
    for p, f, snip in obs:
        per_feature[f].append((p, snip))
    details = [{"key": k, "label": BY_KEY[k].label, "group": GROUPS[BY_KEY[k].group], "pages": v}
               for k, v in sorted(per_feature.items(), key=lambda kv: -len(kv[1])) if k in BY_KEY]
    r = load_results(db)
    sim = (r.get("similar") or {}).get(str(doc.id), [])
    titles = doc_titles(db, [s[0] for s in sim])
    similar = [{"doc": titles[s[0]], "score": s[1], "shared": [tag_label(t) for t in s[2]]}
               for s in sim if s[0] in titles]
    cluster = next((c for c in r.get("clusters", []) if doc.id in c["members"]), None)
    return {"details": details, "similar": similar, "cluster": cluster}


def releases_visuals(db: Session) -> dict:
    """Charts summarising what each release contained."""
    rels = db.scalars(select(Release).order_by(Release.release_date)).all()
    docs = db.execute(select(Document.id, Document.title, Document.release_id, Document.incident_year,
                             Document.agency, Document.media_type)).all()
    by_rel = defaultdict(list)
    for d in docs:
        by_rel[d.release_id].append(d)
    strip = [{"label": r.label.split(" · ")[0] + " · " + r.release_date.strftime("%b %-d"),
              "short": f"R{r.number}" if r.number else r.release_date.strftime("%b"),
              "docs": [{"id": d.id, "title": d.title, "year": d.incident_year} for d in by_rel[r.id]]}
             for r in rels]

    agencies = Counter(d.agency or "Other" for d in docs)
    top_agencies = [a for a, _ in agencies.most_common(4)]
    agency_cls = {a: f"s-{i + 1}" for i, a in enumerate(top_agencies)}
    stacks = []
    for r in reversed(rels):
        c = Counter(d.agency or "Other" for d in by_rel[r.id])
        segs = [{"label": a, "n": c.get(a, 0), "cls": agency_cls[a]} for a in top_agencies]
        other = sum(n for a, n in c.items() if a not in agency_cls)
        segs.append({"label": "Other agencies", "n": other, "cls": "s-other"})
        stacks.append({"release": r, "total": len(by_rel[r.id]), "bar": charts.stacked_bar(segs, max(1, len(by_rel[r.id])))})

    topics = list(FACETS["topic"])
    counts = defaultdict(Counter)
    for rid, v in db.execute(select(Document.release_id, Tag.value).join(Tag, Tag.document_id == Document.id)
                             .where(Tag.facet == "topic")):
        counts[rid][v] += 1
    used = [t for t in topics if any(counts[r.id][t] for r in rels)]
    heat = charts.heatmap(
        [(t, label("topic", t)) for t in used],
        [r.label.split(" · ")[0].replace("Release ", "R") for r in rels],
        [[counts[r.id][t] / max(1, len(by_rel[r.id])) for r in rels] for t in used],
        [[f'{label("topic", t)} in {r.label}: {counts[r.id][t]} of {len(by_rel[r.id])} records' for r in rels] for t in used],
        per_row=False, row_link="/documents?tag=topic:{key}",
    )
    legend = [(a, agency_cls[a]) for a in top_agencies] + [("Other agencies", "s-other")]
    return {"strip": charts.responsive(charts.strip_plot(strip), charts.strip_plot(strip, narrow=True)), "stacks": stacks, "agency_legend": legend, "topic_heat": heat}


# ---------------------------------------------------------------------------
# Comparing two linked records
# ---------------------------------------------------------------------------

_match_cache: dict[tuple, list] = {}
_boiler_cache: dict[str, set] = {}


def _boiler(db: Session, stamp: str) -> set[str]:
    from ..analyze import boilerplate_sentences

    if stamp not in _boiler_cache:
        _boiler_cache.clear()
        _boiler_cache[stamp] = boilerplate_sentences(d or "" for d in db.scalars(select(Document.description)))
    return _boiler_cache[stamp]


def _matches(db: Session, a: Document, b: Document, stamp: str) -> list[dict]:
    from ..analyze.matching import load_passages, match

    key = (a.id, b.id, stamp)
    if key not in _match_cache:
        if len(_match_cache) > 200:
            _match_cache.clear()
        _match_cache[key] = match(load_passages(db, a), load_passages(db, b), limit=12, boiler=_boiler(db, stamp))
    return _match_cache[key]


def _pages(pages: list[int]) -> list[int]:
    return sorted(set(pages))


def compare(db: Session, a_id: int, b_id: int) -> dict | None:
    """Everything two records have in common: matching passages, shared
    details with the lines they come from, shared places and dates."""
    a, b = db.get(Document, a_id), db.get(Document, b_id)
    if not a or not b or a.id == b.id:
        return None
    r = load_results(db)
    stamp = str(r.get("computed_at") or "")
    link = next((l for l in r.get("links", []) if {l["a"], l["b"]} == {a.id, b.id}), None)
    score = link["score"] if link else None
    if score is None:
        for x, y in ((a, b), (b, a)):
            hit = next((s for s in (r.get("similar") or {}).get(str(x.id), []) if s[0] == y.id), None)
            if hit:
                score = hit[1]
                break

    # tags side by side
    tags: dict[int, dict[str, set[str]]] = {a.id: defaultdict(set), b.id: defaultdict(set)}
    for d, facet, value in db.execute(select(Tag.document_id, Tag.facet, Tag.value)
                                      .where(Tag.document_id.in_([a.id, b.id]))):
        tags[d][facet].add(value)
    facets = []
    for facet in ("kind", "topic", "shape", "domain", "sensor", "witness", "region", "era", "assessment"):
        va, vb = tags[a.id].get(facet, set()), tags[b.id].get(facet, set())
        if va or vb:
            facets.append({"label": _facet_label(facet),
                           "a": [(label(facet, v), v in vb) for v in sorted(va)],
                           "b": [(label(facet, v), v in va) for v in sorted(vb)],
                           "same": bool(va & vb)})

    def norm(s):
        return (s or "").strip().lower()

    facts = [
        {"label": "Agency", "a": a.agency or "—", "b": b.agency or "—", "same": bool(a.agency) and norm(a.agency) == norm(b.agency)},
        {"label": "Incident date", "a": a.incident_date_raw or (str(a.incident_year) if a.incident_year else "—"),
         "b": b.incident_date_raw or (str(b.incident_year) if b.incident_year else "—"),
         "same": bool(a.incident_year) and a.incident_year == b.incident_year},
        {"label": "Location", "a": a.incident_location or "—", "b": b.incident_location or "—",
         "same": bool(a.incident_location) and norm(a.incident_location) == norm(b.incident_location)},
        {"label": "Released", "a": a.release.label if a.release else "—", "b": b.release.label if b.release else "—",
         "same": bool(a.release_id) and a.release_id == b.release_id},
        {"label": "Format", "a": _fmt(a), "b": _fmt(b), "same": False},
    ]

    # observed details both records report, with the lines they come from
    obs: dict[int, dict[str, list]] = {a.id: defaultdict(list), b.id: defaultdict(list)}
    for d, p, f, snip in db.execute(select(Observation.document_id, Observation.page_no, Observation.feature,
                                           Observation.snippet).where(Observation.document_id.in_([a.id, b.id]))
                                    .order_by(Observation.page_no)):
        obs[d][f].append({"page": p, "snippet": snip})
    shared_obs = [{"key": f, "label": BY_KEY[f].label, "group": GROUPS[BY_KEY[f].group],
                   "a": obs[a.id][f][:3], "b": obs[b.id][f][:3],
                   "na": len(obs[a.id][f]), "nb": len(obs[b.id][f])}
                  for f in sorted(set(obs[a.id]) & set(obs[b.id]), key=lambda f: BY_KEY[f].label) if f in BY_KEY]
    only_a = [(f, BY_KEY[f].label) for f in sorted(set(obs[a.id]) - set(obs[b.id])) if f in BY_KEY]
    only_b = [(f, BY_KEY[f].label) for f in sorted(set(obs[b.id]) - set(obs[a.id])) if f in BY_KEY]

    # places and dates written in both
    men: dict[tuple[int, str], dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for d, p, kind, value, prec in db.execute(select(Mention.document_id, Mention.page_no, Mention.kind, Mention.value,
                                                     Mention.precision).where(Mention.document_id.in_([a.id, b.id]))):
        if kind == "date":
            if prec == "year":
                continue
            value = value[:7] if prec == "month" else value
        men[(d, kind)][value].append(p)

    def both(kind: str):
        ma, mb = men[(a.id, kind)], men[(b.id, kind)]
        return [(v, _pages(ma[v]), _pages(mb[v])) for v in sorted(set(ma) & set(mb))]

    places = [{"key": v, "label": place_label(v), "a": pa, "b": pb} for v, pa, pb in both("place")]
    dates = [{"value": v, "label": _date_label(v), "a": pa, "b": pb} for v, pa, pb in both("date")]
    matches = _matches(db, a, b, stamp)
    cluster_a = next((c for c in r.get("clusters", []) if a.id in c["members"]), None)
    cluster_b = next((c for c in r.get("clusters", []) if b.id in c["members"]), None)
    return {
        "a": a, "b": b, "score": score, "link": link,
        "cross_agency": bool(a.agency and b.agency and a.agency != b.agency),
        "years_apart": abs(a.incident_year - b.incident_year) if a.incident_year and b.incident_year else None,
        "matches": matches, "facts": facts, "facets": facets,
        "shared_obs": shared_obs, "only_a": only_a, "only_b": only_b,
        "places": places, "dates": dates,
        "same_cluster": cluster_a if cluster_a and cluster_a is cluster_b else None,
    }


def _facet_label(facet: str) -> str:
    from ..classify.taxonomy import FACET_LABELS

    return FACET_LABELS.get(facet, facet.title())


def _fmt(d: Document) -> str:
    s = {"pdf": "PDF", "video": "Video", "image": "Image", "audio": "Audio"}.get(d.media_type, d.media_type)
    return f"{s}, {d.page_count} page{'s' if d.page_count != 1 else ''}" if d.page_count else s


def _date_label(v: str) -> str:
    from datetime import date

    try:
        if len(v) == 7:
            return date.fromisoformat(v + "-01").strftime("%B %Y")
        return date.fromisoformat(v).strftime("%-d %B %Y")
    except ValueError:
        return v


def links_page(db: Session) -> dict:
    """All discovered connections, each with its strongest matching passage."""
    r = load_results(db)
    links = r.get("links", [])
    titles = doc_titles(db, {x for l in links for x in (l["a"], l["b"])})
    out = [{**l, "da": titles[l["a"]], "db": titles[l["b"]], "shared_labels": [tag_label(t) for t in l["shared"]]}
           for l in links if l["a"] in titles and l["b"] in titles]
    return {"links": out, "computed_at": r.get("computed_at"),
            "with_text": sum(1 for l in out if l.get("matches"))}
