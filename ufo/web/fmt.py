"""Display formatting shared by the templates and the pattern pages."""
from __future__ import annotations

import re
from datetime import date

from ..classify.rules import kind_from_title
from ..sources.pursue import parse_us_date

DATE_FMT = "%-d %b %Y"  # 2 Jul 1952: unambiguous for readers anywhere

# Records whose date is the paperwork's, not an incident's
DOCUMENT_DATED_KINDS = {"administrative", "correspondence", "scientific_study"}

_US_RANGE = re.compile(r"(\d{1,2}/\d{1,2}/\d{2,4})\s*[-–]\s*(\d{1,2}/\d{1,2}/\d{2,4})")


def fmt_date(d: date | None, fmt: str = DATE_FMT) -> str:
    return d.strftime(fmt) if d else "—"


def incident_date_text(raw: str | None, parsed: date | None = None, year: int | None = None) -> str:
    """The published date in one unambiguous form.

    ``7/2/52`` becomes ``2 Jul 1952``; ranges and month-only dates keep the
    publisher's wording with stray commas removed (``October, 2023`` ->
    ``October 2023``); an unparseable value is shown as published."""
    if parsed:
        return fmt_date(parsed)
    raw = re.sub(r"\s+", " ", raw or "").strip()
    if not raw or raw.upper() == "N/A":
        return str(year) if year else "—"
    m = _US_RANGE.fullmatch(raw)
    if m:
        a, b = parse_us_date(m.group(1)), parse_us_date(m.group(2))
        if a and b:
            return f"{fmt_date(a)} – {fmt_date(b)}"
    single = parse_us_date(raw)
    if single:
        return fmt_date(single)
    return re.sub(r"(?<=[A-Za-z]),\s*(?=\d{4}\b)", " ", raw)


def date_label(document_kind: str | None, title: str | None = None, media_type: str = "pdf") -> str:
    """"Document date" for paperwork (contracts, letters, studies), otherwise
    "Incident date". A record not classified yet (its file could not be
    fetched) is judged from its title."""
    if document_kind is None and title:
        document_kind = kind_from_title(title, media_type)
    return "Document date" if document_kind in DOCUMENT_DATED_KINDS else "Incident date"


def description_remainder(summary: str | None, description: str | None) -> str | None:
    """When the summary is the opening of the publisher's description (the
    rules classifier's summary always is), the part of the description that
    follows it; ``None`` when the two differ or nothing follows."""
    if not summary or not description:
        return None
    words = summary.strip().rstrip("…").split()
    if not words:
        return None
    pattern = r"\s*" + r"\s+".join(re.escape(w) for w in words)
    m = re.match(pattern, description)
    if not m:
        return None
    rest = description[m.end():].strip().lstrip(".,;:").strip()
    return rest or None
