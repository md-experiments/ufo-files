"""Export / import a processed-data snapshot.

A snapshot lets a fresh deployment show data immediately (and keeps working
even if the official site and the Internet Archive are both unreachable from
the host). The pipeline then continues incrementally from it.
"""
from __future__ import annotations

import gzip
import json
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import func, select

from .db import Document, Page, Release, Tag, init_db, session_scope

log = logging.getLogger(__name__)

DOC_FIELDS = [
    "source", "record_id", "title", "media_type", "agency", "description", "incident_date",
    "incident_date_raw", "incident_year", "incident_location", "file_url", "thumb_url", "video_id",
    "related_ids", "redacted", "featured", "raw", "row_hash", "status", "error", "fetched_via",
    "file_sha256", "file_size", "page_count", "ocr_page_count", "ocr_confidence", "char_count", "text",
    "classifier", "summary", "key_points", "document_kind", "assessment", "significance",
    "classification", "first_seen_at", "processed_at",
]


def _ser(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def export_seed(path: Path) -> int:
    init_db()
    out = {"version": 1, "releases": [], "documents": []}
    with session_scope() as db:
        for r in db.scalars(select(Release).order_by(Release.release_date)):
            out["releases"].append({"source": r.source, "release_date": r.release_date.isoformat(),
                                    "number": r.number, "label": r.label, "first_seen_at": _ser(r.first_seen_at)})
        for d in db.scalars(select(Document).order_by(Document.id)):
            item = {f: _ser(getattr(d, f)) for f in DOC_FIELDS}
            # the stored text is rebuilt from pages on import
            item.pop("text")
            item["release_date"] = d.release.release_date.isoformat() if d.release else None
            item["pages"] = [[p.page_no, p.method, p.confidence, p.text] for p in d.pages]
            item["tags"] = [[t.facet, t.value] for t in d.tags]
            if item["status"] not in ("classified",):
                item["status"] = "new"
            out["documents"].append(item)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    return len(out["documents"])


def _date(v):
    return date.fromisoformat(v) if v else None


def _dt(v):
    return datetime.fromisoformat(v) if v else None


def import_seed(path: Path, only_if_empty: bool = True) -> int:
    init_db()
    if not path.exists():
        return 0
    with session_scope() as db:
        if only_if_empty and db.scalar(select(func.count(Document.id))):
            return 0
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    n = 0
    with session_scope() as db:
        releases = {}
        for r in data["releases"]:
            rel = Release(source=r["source"], release_date=_date(r["release_date"]), number=r["number"],
                          label=r["label"], first_seen_at=_dt(r["first_seen_at"]))
            db.add(rel)
            releases[(r["source"], r["release_date"])] = rel
        db.flush()
        for item in data["documents"]:
            pages = item.pop("pages")
            tags = item.pop("tags")
            rel_date = item.pop("release_date")
            for f in ("incident_date",):
                item[f] = _date(item[f])
            for f in ("first_seen_at", "processed_at"):
                item[f] = _dt(item[f])
            doc = Document(**item)
            doc.release = releases.get((item["source"], rel_date))
            doc.pages = [Page(page_no=p[0], method=p[1], confidence=p[2], text=p[3]) for p in pages]
            doc.text = "\n\n".join(f"[Page {p[0]}]\n{p[3]}" for p in pages if p[3].strip()) or None
            doc.tags = [Tag(facet=f, value=v) for f, v in tags]
            db.add(doc)
            n += 1
    log.info("imported %d documents from %s", n, path)
    return n
