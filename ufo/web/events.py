"""Queries and view-models for the Events page: what happens in the files,
one episode at a time, in a hierarchy of event classes and subcategories."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analyze import load_results
from ..analyze.episodes import EVENTS, OUTCOMES, SUBCATS, SUB_BY_KEY, detail_dim, detail_label, event_label, sub_label
from ..analyze.events import DIMENSIONS
from ..db import Document, Episode
from . import charts
from .patterns import doc_titles

SOURCE_LABELS = {"curated": "read by hand", "llm": "read by the LLM", "rules": "keyword rules"}
OUTCOME_CLS = {"unexplained": "s-2", "identified": "s-1", "hoax": "s-4", "not_stated": "s-none"}
CLASS_CLS = {k: f"ev-{k}" for k in EVENTS}
DIM_LABELS = {**{k: v[0] for k, v in DIMENSIONS.items()}, "context": "Context"}


def detail_chips(keys) -> list[dict]:
    order = list(DIMENSIONS) + ["context"]
    ks = sorted(keys, key=lambda k: (order.index(detail_dim(k)) if detail_dim(k) in order else 99))
    return [{"key": k, "label": detail_label(k), "dim": detail_dim(k), "dim_label": DIM_LABELS.get(detail_dim(k), "")}
            for k in ks]


def episode_view(e: Episode, titles: dict[int, dict]) -> dict:
    page = e.pages[0] if e.pages else e.page_no
    return {
        "id": e.id, "summary": e.summary, "event": e.event, "sub": e.sub,
        "event_label": event_label(e.event), "sub_label": sub_label(e.event, e.sub),
        "chips": detail_chips(e.details or []), "year": e.year, "place": e.place,
        "outcome": e.outcome, "outcome_label": OUTCOMES.get(e.outcome, e.outcome), "outcome_cls": OUTCOME_CLS.get(e.outcome, "s-none"),
        "explanation": e.explanation, "source": e.source, "source_label": SOURCE_LABELS.get(e.source, e.source),
        "doc": titles.get(e.document_id), "page": page, "pages": list(e.pages or []),
        "href": f"/documents/{e.document_id}" + (f"#p{page}" if page else ""),
    }


def _episodes(db: Session, ids) -> dict[int, Episode]:
    ids = list({int(i) for i in ids})
    out = {}
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        for e in db.scalars(select(Episode).where(Episode.id.in_(chunk))):
            out[e.id] = e
    return out


def _views(db: Session, ids: list[int]) -> list[dict]:
    eps = _episodes(db, ids)
    titles = doc_titles(db, {e.document_id for e in eps.values()})
    return [episode_view(eps[i], titles) for i in ids if i in eps and eps[i].document_id in titles]


def events_context(db: Session) -> dict:
    r = load_results(db)
    h = r.get("hierarchy")
    if not h or not h.get("total"):
        return {"ready": False}
    ex_ids = [i for c in h["classes"] for i in c["examples"][:1]] + \
             [i for c in h["classes"] for s in c["subs"] for i in s["examples"][:1]]
    views = {v["id"]: v for v in _views(db, ex_ids)}
    classes = []
    for c in h["classes"]:
        if not c["count"]:
            continue
        subs = [{**s, "example": next((views[i] for i in s["examples"] if i in views), None),
                 "outcome_bar": _outcome_bar(s["outcomes"], s["count"])} for s in c["subs"]]
        classes.append({**c, "cls": CLASS_CLS[c["key"]], "subs": subs,
                        "example": next((views[i] for i in c["examples"] if i in views), None),
                        "outcome_bar": _outcome_bar(c["outcomes"], c["count"])})
    # the map: one dot per episode, titled with its summary
    m = h["map"]
    eps = _episodes(db, [p["id"] for p in m["points"]])
    titles = doc_titles(db, {e.document_id for e in eps.values()})
    tips, hrefs = {}, {}
    for p in m["points"]:
        e = eps.get(p["id"])
        if not e:
            continue
        d = titles.get(e.document_id) or {}
        summary = e.summary if len(e.summary) <= 160 else e.summary[:160].rsplit(" ", 1)[0] + "…"
        tips[p["id"]] = f'{summary} — {d.get("record_id", "")}' + (f" ({e.year})" if e.year else "")
        page = e.pages[0] if e.pages else 0
        hrefs[p["id"]] = f"/documents/{e.document_id}" + (f"#p{page}" if page else "")
    labels = {c["key"]: c["label"] for c in classes}
    sub_labels = {(c["key"], s["key"]): s["label"] for c in classes for s in c["subs"]}
    counts = {(c["key"], s["key"]): s["count"] for c in classes for s in c["subs"]}
    heat = None
    if h["decades"]:
        heat = charts.heatmap(
            [(row["key"], row["label"]) for row in h["decade_matrix"]], h["decades"],
            [[c["share"] for c in row["cells"]] for row in h["decade_matrix"]],
            [[f'{row["label"]}, {c["decade"]}: {c["count"]} of {tot} episodes ({100 * c["share"]:.0f}%)'
              for c, tot in zip(row["cells"], h["decade_totals"])] for row in h["decade_matrix"]],
            per_row=False, row_link="/events/{key}")
    sources = h.get("sources", {})
    return {
        "ready": True,
        "computed_at": r.get("computed_at"),
        "total": h["total"], "records": h["records"],
        "n_classes": len(classes), "n_subs": sum(len(c["subs"]) for c in classes),
        "outcomes": h["outcomes"],
        "outcome_legend": [(OUTCOMES[k], OUTCOME_CLS[k]) for k in OUTCOMES],
        "sources": [(SOURCE_LABELS[k], sources.get(k, 0)) for k in ("curated", "llm", "rules") if sources.get(k)],
        "classes": classes,
        "map": charts.circle_map(m, tips, hrefs, labels, sub_labels, counts),
        "decade_heat": heat,
        "legend": [(c["label"], c["cls"]) for c in classes],
    }


def _outcome_bar(outcomes: dict, total: int):
    segs = [{"label": OUTCOMES[k], "n": outcomes.get(k, 0), "cls": OUTCOME_CLS[k]} for k in OUTCOMES]
    return charts.stacked_bar(segs, max(1, total))


def _node(h: dict, event: str, sub: str | None) -> dict | None:
    c = next((c for c in h.get("classes", []) if c["key"] == event), None)
    if not c:
        return None
    if sub is None:
        return c
    return next((s for s in c["subs"] if s["key"] == sub), None)


def event_group(db: Session, event: str, sub: str | None = None, limit: int = 400) -> dict | None:
    """Every episode of one class (or one subcategory), grouped by record,
    oldest first. A class page shows each subcategory with a few examples
    and a link to its full list."""
    if event not in EVENTS or (sub is not None and (event, sub) not in SUB_BY_KEY):
        return None
    r = load_results(db)
    h = r.get("hierarchy") or {}
    node = _node(h, event, sub)
    q = select(Episode).where(Episode.event == event)
    if sub is not None:
        q = q.where(Episode.sub == sub)
    eps = db.scalars(q.order_by(Episode.year.nulls_last(), Episode.document_id, Episode.seq)).all()
    titles = doc_titles(db, {e.document_id for e in eps})
    views = [episode_view(e, titles) for e in eps if e.document_id in titles]
    out = {"event": event, "sub": sub, "event_label": event_label(event), "sub_label": sub_label(event, sub) if sub else None,
           "description": (SUB_BY_KEY[(event, sub)].description if sub else EVENTS[event][1]),
           "cls": CLASS_CLS[event], "node": node, "total": len(views),
           "outcome_bar": _outcome_bar(node["outcomes"], node["count"]) if node else None,
           "outcome_legend": [(OUTCOMES[k], OUTCOME_CLS[k]) for k in OUTCOMES]}
    if sub is None:
        by_sub: dict[str, list[dict]] = defaultdict(list)
        for v in views:
            by_sub[v["sub"]].append(v)
        out["subs"] = [{"key": s.key, "label": s.label, "description": s.description, "count": len(by_sub[s.key]),
                        "examples": _spread(by_sub[s.key], 6)}
                       for s in SUBCATS[event] if by_sub.get(s.key)]
        return out
    by_doc: dict[int, list[dict]] = defaultdict(list)
    for v in views[:limit]:
        by_doc[v["doc"]["id"]].append(v)
    out["records"] = [{"doc": titles[d], "episodes": v} for d, v in by_doc.items()]
    out["shown"] = min(len(views), limit)
    return out


def _spread(views: list[dict], k: int) -> list[dict]:
    """A few episodes from different records, spread over the years."""
    seen, picks = set(), []
    step = max(1, len(views) // k)
    for i in range(0, len(views), step):
        v = views[i]
        if v["doc"]["id"] in seen:
            continue
        seen.add(v["doc"]["id"])
        picks.append(v)
        if len(picks) >= k:
            break
    return picks


def document_episodes(db: Session, doc: Document) -> list[dict]:
    eps = db.scalars(select(Episode).where(Episode.document_id == doc.id).order_by(Episode.seq)).all()
    titles = {doc.id: {"id": doc.id, "record_id": doc.record_id, "title": doc.title, "year": doc.incident_year,
                       "agency": doc.agency, "media": doc.media_type}}
    return [episode_view(e, titles) for e in eps]


def api_events(db: Session) -> dict:
    r = load_results(db)
    h = dict(r.get("hierarchy") or {})
    h.pop("map", None)
    eps = db.scalars(select(Episode).order_by(Episode.document_id, Episode.seq)).all()
    docs = {d.id: d.record_id for d in db.execute(select(Document.id, Document.record_id))}
    return {
        "computed_at": r.get("computed_at").isoformat() + "Z" if r.get("computed_at") else None,
        "classes": {k: {"label": v[0], "description": v[1],
                        "subcategories": {s.key: {"label": s.label, "description": s.description} for s in SUBCATS[k]}}
                    for k, v in EVENTS.items()},
        "hierarchy": h,
        "episodes": [{"id": e.id, "record_id": docs.get(e.document_id), "document_id": e.document_id, "pages": e.pages,
                      "summary": e.summary, "event": e.event, "subcategory": e.sub, "details": e.details,
                      "year": e.year, "place": e.place, "outcome": e.outcome, "explanation": e.explanation,
                      "source": e.source} for e in eps],
    }
