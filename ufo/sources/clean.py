"""Tidying of published metadata before it is stored.

The publisher's index is mostly clean, but some rows carry a raw archive
filename as the title, spelling variants of one place, and dates that
disagree with the title. Everything here is deterministic and keeps the
record id untouched (it is the key everything else hangs on).
"""
from __future__ import annotations

import re

# "18_100754_", "65_HS1-834228961_", "331_120752_": archive box/serial prefixes
_FILENAME_PREFIX = re.compile(r"^(?:\d{1,9}_\s*)+(?:[A-Z]{1,3}\d*-\d+_)?")
_RAW_TITLE = re.compile(r"^\d+_")


def clean_title(title: str) -> str:
    """A readable title for a record whose title is an archive filename.

    ``18_6369445_General_1948_Vol_1`` -> ``General 1948 Vol 1``;
    ``65_HS1-834228961_62-HQ-83894_Section_009`` -> ``62-HQ-83894 Section 009``.
    Other titles are returned as they are."""
    title = (title or "").strip()
    if not _RAW_TITLE.match(title):
        return title
    s = _FILENAME_PREFIX.sub("", title).replace("_", " ")
    s = re.sub(r"\s+", " ", s).strip(" -")
    s = re.sub(r"\b(box|vol|volume|part|section|serial)(\d+)\b", r"\1 \2", s, flags=re.IGNORECASE)
    if not s:
        return title
    return s[0].upper() + s[1:]


# spelling variants of one place, keyed by the lower-cased published value
LOCATION_CANON = {
    "westen united states": "Western United States",
    "western u.s.": "Western United States",
    "low-earth orbit": "Low Earth Orbit",
    "leo": "Low Earth Orbit",
    "indo-pacom": "INDOPACOM",
    "indopacom aor": "INDOPACOM",
    "centcom aor": "Middle East (CENTCOM)",
    "centcom": "Middle East (CENTCOM)",
    "middle east": "Middle East (CENTCOM)",
    "u.s. central command": "Middle East (CENTCOM)",
    "washington dc": "Washington, D.C.",
    "washington, dc": "Washington, D.C.",
    "washington d.c.": "Washington, D.C.",
}
_COUNTRY_SUFFIX = re.compile(r",\s*(?:U\.?S\.?A?\.?|United States(?: of America)?)\s*$", re.IGNORECASE)


def normalize_location(location: str | None) -> str | None:
    """One spelling per place: whitespace and trailing punctuation trimmed,
    a redundant ", U.S." dropped, known typos and variants mapped."""
    if not location:
        return None
    s = re.sub(r"\s+", " ", location).strip().rstrip(",;")
    s = re.sub(r"(?<![A-Z])\.$", "", s)  # a stray full stop, not the one in "D.C."
    if s.upper() in ("", "N/A", "NA", "NONE", "UNKNOWN"):
        return None
    if "," in s:  # "Colorado Springs, Colorado, U.S." (but not "Washington, D.C.")
        s = _COUNTRY_SUFFIX.sub("", s)
    return LOCATION_CANON.get(s.lower(), s)


_YEAR = re.compile(r"\b(1[89]\d\d|20\d\d)\b")
_TWO_DIGIT_YEAR = re.compile(r"^\s*\d{1,2}/\d{1,2}/\d{2}\s*$")


def title_years(title: str | None) -> list[int]:
    return [int(y) for y in _YEAR.findall(title or "")]


def reconcile_year(raw_date: str | None, parsed_year: int | None, title: str | None,
                   release_year: int | None) -> tuple[int | None, bool]:
    """The incident year to store, and whether the published date was kept.

    * A date with a two-digit year is ambiguous (``3/23/26`` on a record
      titled "March 2023"): when the title names a year and the parsed one is
      not among them, the title's year wins and the date is dropped.
    * A record with no date whose title names only the release year ("...
      Report, 2026") is dated by its paperwork, not an incident: no year."""
    years = title_years(title)
    if parsed_year and years and parsed_year not in years and _TWO_DIGIT_YEAR.match(raw_date or ""):
        return years[-1] if len(years) == 1 else years[0], False
    if parsed_year:
        return parsed_year, True
    if not years:
        return None, True
    year = years[-1] if len(years) == 1 else years[0]
    no_date = not raw_date or raw_date.strip().upper() in ("", "N/A")
    if no_date and release_year and year == release_year and len(set(years)) == 1:
        return None, True
    return year, True


def date_note(title: str | None, incident_year: int | None) -> str | None:
    """A caution when the title names a year the stored date does not."""
    years = title_years(title)
    if incident_year and years and incident_year not in years:
        return f"the title says {years[0]}"
    return None
