"""Dates mentioned in (often OCR'd) report text."""
from __future__ import annotations

import re
from datetime import date

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MON = r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"

PATTERNS = [
    # July 7, 1947 / July 7th 1947 / JUL 7 47
    re.compile(rf"(?<![A-Za-z]){_MON}\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<year>(?:19|20)\d\d|'?\d\d)(?!\d)", re.I),
    # 7 July 1947 / 07JUL47 / 7 JUL 47
    re.compile(rf"(?<!\d)(?P<day>\d{{1,2}})\s*{_MON}\s*,?\s*(?P<year>(?:19|20)\d\d|\d\d)(?!\d)", re.I),
    # July 1947
    re.compile(rf"(?<![A-Za-z]){_MON},?\s+(?P<year>(?:19|20)\d\d)(?!\d)", re.I),
    # 7/7/47 or 7/7/1947 (US order)
    re.compile(r"(?<![\d/])(?P<month>\d{1,2})/(?P<day>\d{1,2})/(?P<year>(?:19|20)\d\d|\d\d)(?![\d/])"),
    # 1947-07-07
    re.compile(r"(?<!\d)(?P<year>(?:19|20)\d\d)-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)"),
]


def _year(raw: str, max_year: int) -> int | None:
    raw = raw.lstrip("'")
    y = int(raw)
    if y < 100:
        y += 2000 if 2000 + y <= max_year else 1900
    return y


def find_dates(text: str, min_year: int = 1940, max_date: date | None = None) -> list[tuple[date, str]]:
    """Return (date, precision) pairs; precision is 'day' or 'month'.

    Month-only mentions are dated to the 1st. Dates outside
    [min_year, max_date] are dropped (OCR noise, document numbers)."""
    max_date = max_date or date.today()
    out: dict[tuple[int, int, int], str] = {}
    spans: list[tuple[int, int]] = []
    for rx in PATTERNS:
        for m in rx.finditer(text or ""):
            if any(a <= m.start() < b for a, b in spans):
                continue  # already covered by a more specific pattern
            g = m.groupdict()
            try:
                year = _year(g["year"], max_date.year)
                month = MONTHS[g["mon"].lower()[:3]] if g.get("mon") else int(g["month"])
                day = int(g["day"]) if g.get("day") else None
                d = date(year, month, day or 1)
            except (KeyError, ValueError, TypeError):
                continue
            if d.year < min_year or d > max_date:
                continue
            key = (d.year, d.month, d.day)
            prec = "day" if day else "month"
            if out.get(key) != "day":
                out[key] = prec
            spans.append((m.start(), m.end()))
    return [(date(*k), p) for k, p in sorted(out.items())]
