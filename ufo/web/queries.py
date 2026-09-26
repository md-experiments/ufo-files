"""Read-side queries used by the web app and JSON API."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, case, distinct, func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..classify.taxonomy import FACET_LABELS, FACETS, label
from ..db import Document, Page, PipelineRun, Release, Tag, has_classification

MEDIA_ORDER = ["pdf", "video", "image", "audio"]
MEDIA_LABELS = {"pdf": "Documents", "video": "Videos", "image": "Images", "audio": "Audio", "other": "Other"}


def overview(db: Session) -> dict:
    totals = db.execute(
        select(
            func.count(Document.id),
            func.sum(case((Document.media_type == "pdf", 1), else_=0)),
            func.coalesce(func.sum(Document.page_count), 0),
            func.coalesce(func.sum(Document.ocr_page_count), 0),
            func.count(distinct(Document.agency)),
            func.sum(case((has_classification(), 1), else_=0)),
            func.max(Document.incident_year),
            func.min(Document.incident_year),
        )
    ).one()
    releases = db.scalar(select(func.count(Release.id))) or 0
    return {
        "records": totals[0] or 0,
        "documents": totals[1] or 0,
        "pages": int(totals[2] or 0),
        "ocr_pages": int(totals[3] or 0),
        "agencies": totals[4] or 0,
        "classified": totals[5] or 0,
        "newest_year": totals[6],
        "oldest_year": totals[7],
        "releases": releases,
        "last_run": last_run(db),
        "last_success": db.scalar(
            select(PipelineRun).where(PipelineRun.status == "ok").order_by(PipelineRun.id.desc())
        ),
    }


def last_run(db: Session) -> PipelineRun | None:
    return db.scalar(select(PipelineRun).order_by(PipelineRun.id.desc()))


def releases(db: Session) -> list[dict]:
    rels = db.scalars(select(Release).order_by(Release.release_date)).all()
    counts: dict[int, dict[str, int]] = {}
    for rid, media, n in db.execute(
        select(Document.release_id, Document.media_type, func.count()).group_by(Document.release_id, Document.media_type)
    ):
        counts.setdefault(rid, {})[media] = n
    pages = dict(db.execute(select(Document.release_id, func.sum(Document.page_count)).group_by(Document.release_id)).all())
    out = []
    for r in rels:
        c = counts.get(r.id, {})
        out.append({
            "id": r.id,
            "number": r.number,
            "label": r.label,
            "date": r.release_date,
            "source": r.source,
            "counts": c,
            "total": sum(c.values()),
            "pages": int(pages.get(r.id) or 0),
        })
    return out


def facet_counts(db: Session, facet: str, release_id: int | None = None, limit: int | None = None) -> list[dict]:
    q = select(Tag.value, func.count(distinct(Tag.document_id)).label("n")).where(Tag.facet == facet)
    if release_id:
        q = q.join(Document, Document.id == Tag.document_id).where(Document.release_id == release_id)
    q = q.group_by(Tag.value).order_by(func.count(distinct(Tag.document_id)).desc(), Tag.value)
    if limit:
        q = q.limit(limit)
    return [{"value": v, "label": label(facet, v), "n": n} for v, n in db.execute(q)]


def era_counts(db: Session) -> list[dict]:
    rows = dict(
        db.execute(select(Tag.value, func.count(distinct(Tag.document_id))).where(Tag.facet == "era").group_by(Tag.value)).all()
    )
    return [{"value": k, "label": k, "n": rows.get(k, 0)} for k in FACETS["era"]]


def top_locations(db: Session, limit: int = 12) -> list[dict]:
    q = (
        select(Document.incident_location, func.count())
        .where(Document.incident_location.is_not(None))
        .group_by(Document.incident_location)
        .order_by(func.count().desc())
        .limit(limit)
    )
    return [{"value": v, "label": v, "n": n} for v, n in db.execute(q)]


def highlights(db: Session, release_id: int | None = None, limit: int = 8) -> list[Document]:
    q = select(Document).where(has_classification())
    if release_id:
        q = q.where(Document.release_id == release_id)
    q = q.order_by(
        Document.featured.desc(),
        func.coalesce(Document.significance, 0).desc(),
        case((Document.media_type == "pdf", 0), else_=1),
        func.coalesce(Document.page_count, 0).desc(),
    ).limit(limit)
    return list(db.scalars(q))


def latest_release(db: Session) -> Release | None:
    return db.scalar(select(Release).order_by(Release.release_date.desc()))


@dataclass
class SearchResult:
    items: list[Document]
    total: int
    page: int
    per_page: int

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))


def search(
    db: Session,
    q: str | None = None,
    release: int | None = None,
    agency: str | None = None,
    media: str | None = None,
    tags: list[tuple[str, str]] | None = None,
    sort: str = "release",
    page: int = 1,
    per_page: int = 30,
) -> SearchResult:
    conds = []
    if q:
        like = f"%{q.strip()}%"
        conds.append(or_(
            Document.title.ilike(like), Document.record_id.ilike(like), Document.description.ilike(like),
            Document.summary.ilike(like), Document.incident_location.ilike(like), Document.text.ilike(like),
        ))
    if release:
        conds.append(Document.release_id == release)
    if agency:
        conds.append(Document.agency == agency)
    if media:
        conds.append(Document.media_type == media)
    for facet, value in tags or []:
        conds.append(Document.id.in_(select(Tag.document_id).where(Tag.facet == facet, Tag.value == value)))
    where = and_(*conds) if conds else None

    count_q = select(func.count(Document.id))
    list_q = select(Document).options(selectinload(Document.tags), selectinload(Document.release))
    if where is not None:
        count_q = count_q.where(where)
        list_q = list_q.where(where)
    order = {
        "release": (Document.release_id.desc(), Document.featured.desc(), Document.record_id),
        "incident": (func.coalesce(Document.incident_year, 0).desc(), Document.record_id),
        "oldest": (func.coalesce(Document.incident_year, 9999).asc(), Document.record_id),
        "significance": (func.coalesce(Document.significance, 0).desc(), Document.featured.desc(), Document.id),
        "pages": (func.coalesce(Document.page_count, 0).desc(), Document.id),
    }.get(sort, (Document.release_id.desc(), Document.record_id))
    total = db.scalar(count_q) or 0
    items = list(db.scalars(list_q.order_by(*order).offset((page - 1) * per_page).limit(per_page)))
    return SearchResult(items=items, total=total, page=page, per_page=per_page)


def agencies(db: Session) -> list[tuple[str, int]]:
    return [tuple(r) for r in db.execute(
        select(Document.agency, func.count()).where(Document.agency.is_not(None))
        .group_by(Document.agency).order_by(func.count().desc())
    )]


def agency_counts(db: Session, release_id: int | None = None, limit: int | None = None) -> list[dict]:
    """Records per agency, counted from the published listing: every record
    counts, including ones whose file could not be downloaded yet."""
    q = select(Document.agency, func.count()).where(Document.agency.is_not(None))
    if release_id:
        q = q.where(Document.release_id == release_id)
    q = q.group_by(Document.agency).order_by(func.count().desc(), Document.agency)
    if limit:
        q = q.limit(limit)
    return [{"value": a, "label": a, "n": n} for a, n in db.execute(q)]


def problem_records(db: Session) -> list[Document]:
    """Records whose file could not be fetched or processed, with the error."""
    return list(db.scalars(
        select(Document).options(selectinload(Document.release))
        .where(Document.status.in_(("unavailable", "failed"))).order_by(Document.status, Document.record_id)
    ))


def document(db: Session, doc_id: int) -> Document | None:
    return db.scalar(
        select(Document).options(selectinload(Document.tags), selectinload(Document.pages), selectinload(Document.release))
        .where(Document.id == doc_id)
    )


def related(db: Session, doc: Document) -> list[Document]:
    ids = [i for i in (doc.related_ids or []) if i]
    if not ids:
        return []
    return list(db.scalars(select(Document).where(Document.source == doc.source, Document.record_id.in_(ids))))


def status_counts(db: Session) -> dict[str, int]:
    return dict(db.execute(select(Document.status, func.count()).group_by(Document.status)).all())


def runs(db: Session, limit: int = 20) -> list[PipelineRun]:
    return list(db.scalars(select(PipelineRun).order_by(PipelineRun.id.desc()).limit(limit)))


def grouped_tags(doc: Document) -> list[tuple[str, str, list[tuple[str, str]]]]:
    by: dict[str, list[str]] = {}
    for t in doc.tags:
        by.setdefault(t.facet, []).append(t.value)
    order = ["kind", "assessment", "topic", "shape", "domain", "witness", "sensor", "program", "region", "era"]
    return [
        (f, FACET_LABELS.get(f, f.title()), [(v, label(f, v)) for v in by[f]])
        for f in order if f in by
    ]


__all__ = [
    "MEDIA_LABELS", "MEDIA_ORDER", "overview", "releases", "facet_counts", "era_counts", "top_locations",
    "highlights", "latest_release", "search", "agencies", "agency_counts", "problem_records", "document", "related",
    "status_counts", "runs", "grouped_tags", "Page",
]
