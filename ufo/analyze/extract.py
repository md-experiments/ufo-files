"""Per-page extraction of observables, dates and places into the database."""
from __future__ import annotations

from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..db import Document, Mention, Observation
from .dates import find_dates
from .features import find_observables, is_sighting_text
from .places import find_places, location_places

# research papers and contracts: their pages are analysed only when they read
# like sighting accounts, and their own dates/places are not sighting dates
NON_SIGHTING_KINDS = {"scientific_study", "administrative"}


def units(doc: Document) -> list[tuple[int, str]]:
    """The texts to analyse: each page, plus the publisher description (page 0)."""
    out = [(p.page_no, p.text or "") for p in doc.pages]
    if doc.description and doc.document_kind not in NON_SIGHTING_KINDS:
        out.append((0, doc.description))
    return out


def extract_document(db: Session, doc: Document) -> tuple[int, int]:
    """Replace ``doc``'s observations and mentions. Returns (sighting units, observations)."""
    db.execute(delete(Observation).where(Observation.document_id == doc.id))
    db.execute(delete(Mention).where(Mention.document_id == doc.id))
    max_date = doc.release.release_date if doc.release else date.today()
    sighting_units = n_obs = 0
    for page_no, text in units(doc):
        # the publisher's own description is always about the sighting
        if page_no != 0 and not is_sighting_text(text):
            continue
        sighting_units += 1
        for hit in find_observables(text):
            db.add(Observation(document_id=doc.id, page_no=page_no, feature=hit.key, snippet=hit.snippet))
            n_obs += 1
        if page_no != 0:
            for d, precision in find_dates(text, max_date=max_date):
                db.add(Mention(document_id=doc.id, page_no=page_no, kind="date", value=d.isoformat(), precision=precision))
            for place in find_places(text):
                db.add(Mention(document_id=doc.id, page_no=page_no, kind="place", value=place))
    # the record's own metadata, when the publisher gives it
    if doc.document_kind in NON_SIGHTING_KINDS:
        return sighting_units, n_obs
    if doc.incident_date:
        db.add(Mention(document_id=doc.id, page_no=0, kind="date", value=doc.incident_date.isoformat(), precision="day"))
    elif doc.incident_year:
        db.add(Mention(document_id=doc.id, page_no=0, kind="date", value=f"{doc.incident_year}-01-01", precision="year"))
    for place in location_places(doc.incident_location):
        db.add(Mention(document_id=doc.id, page_no=0, kind="place", value=place))
    return sighting_units, n_obs


def extract_all(db: Session) -> dict:
    docs = db.scalars(
        select(Document).where(Document.status == "classified")
        .options(selectinload(Document.pages), selectinload(Document.release))
    ).all()
    totals = {"documents": 0, "sighting_units": 0, "observations": 0}
    for doc in docs:
        u, o = extract_document(db, doc)
        totals["documents"] += 1
        totals["sighting_units"] += u
        totals["observations"] += o
    return totals
