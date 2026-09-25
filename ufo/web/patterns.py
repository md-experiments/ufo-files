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
        "timeline_svg": charts.timeline(tl["years"], tl["waves"]),
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
        "case_map": charts.case_map(r["map"], clusters, {i: f'{t["record_id"]}: {t["title"]}' for i, t in titles.items()}),
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
    return {"strip": charts.strip_plot(strip), "stacks": stacks, "agency_legend": legend, "topic_heat": heat}
