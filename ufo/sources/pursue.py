"""PURSUE — Presidential Unsealing and Reporting System for UAP Encounters.

The Department of War publishes the UFO files at https://www.war.gov/UFO/ in
rolling tranches (first one on May 8, 2026). The page is driven by a single
CSV index, ``/Portals/1/Interactive/2026/UFO/uap-data.csv``, with one row per
released record. That CSV is the feed this source parses.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date

from ..fetch import FetchError, fetch
from .base import RecordInfo, Source

log = logging.getLogger(__name__)

BASE = "https://www.war.gov"
PAGE_URL = f"{BASE}/UFO/"
CSV_URL = f"{BASE}/Portals/1/Interactive/2026/UFO/uap-data.csv"
DVIDS_VIDEO = "https://www.dvidshub.net/video/{id}"

MEDIA_TYPES = {"PDF": "pdf", "IMG": "image", "VID": "video", "AUD": "audio"}
ID_RE = re.compile(r"^[A-Za-z]+-UAP-[A-Za-z]*\d+[A-Za-z]?$")
CSV_LINK_RE = re.compile(r"""["'](/Portals/1/Interactive/2026/UFO/uap-data\.csv[^"']*)["']""")
YEAR_RE = re.compile(r"\b(1[89]\d\d|20\d\d)\b")


def parse_us_date(value: str, today: date | None = None) -> date | None:
    """Parse m/d/yy or m/d/yyyy. Two-digit years in the future mean 19xx."""
    value = (value or "").strip()
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", value)
    if not m:
        return None
    month, day, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if year < 100:
        today = today or date.today()
        year += 2000 if 2000 + year <= today.year else 1900
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_year(*values: str | None) -> int | None:
    for v in values:
        if not v:
            continue
        years = [int(y) for y in YEAR_RE.findall(v)]
        if years:
            return years[-1] if len(years) == 1 else years[0]
    return None


def _norm_id(value: str) -> str:
    value = value.strip()
    return value.upper() if ID_RE.match(value) else value


def _split_ids(value: str) -> list[str]:
    out = []
    for part in (value or "").split("|"):
        part = part.strip()
        if not part:
            continue
        out.append(_norm_id(part.split(",")[0]))
    return out


def parse_csv(text: str, today: date | None = None) -> list[RecordInfo]:
    reader = csv.DictReader(io.StringIO(text.lstrip("﻿")))
    records: list[RecordInfo] = []
    seen: dict[str, int] = {}
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items() if k}
        title = row.get("Title", "")
        if not title:
            continue
        release_date = parse_us_date(row.get("Release Date", ""), today)
        if not release_date:
            log.warning("skipping row without release date: %s", title)
            continue
        first = title.split(",")[0].strip()
        record_id = _norm_id(first) if ID_RE.match(first) else title
        # The feed occasionally lists the same id twice (distinct files)
        seen[record_id] = seen.get(record_id, 0) + 1
        if seen[record_id] > 1:
            record_id = f"{record_id}#{seen[record_id]}"

        kind = MEDIA_TYPES.get(row.get("Type", "").upper(), "other")
        link = row.get("PDF | Image Link") or None
        video_id = row.get("DVIDS Video ID") or None
        related = _split_ids(row.get("PDF Pairing", "")) + _split_ids(row.get("Video Pairing", ""))
        file_url = link
        if kind in ("video", "audio"):
            # For A/V rows the link column points at the paired PDF, which is
            # listed as its own record; the media itself lives on DVIDS.
            file_url = DVIDS_VIDEO.format(id=video_id) if video_id else None
        raw_date = row.get("Incident Date", "")
        inc_date = parse_us_date(raw_date, today)
        year = inc_date.year if inc_date else parse_year(raw_date, title)
        if year and year > release_date.year:
            year = None
        location = row.get("Incident Location", "")
        records.append(
            RecordInfo(
                record_id=record_id,
                title=title,
                media_type=kind,
                release_date=release_date,
                agency=row.get("Agency") or None,
                description=row.get("Description Blurb") or None,
                incident_date=inc_date,
                incident_date_raw=raw_date or None,
                incident_year=year,
                incident_location=None if location.upper() in ("", "N/A") else location,
                file_url=file_url,
                thumb_url=row.get("Modal Image") or None,
                video_id=video_id,
                related_ids=related,
                redacted=(row.get("Redaction", "").upper() == "TRUE"),
                featured=(row.get("Featured", "").upper() == "YES"),
                raw=row,
            )
        )
    return records


class PursueSource(Source):
    name = "pursue"
    label = "PURSUE (war.gov/UFO)"
    homepage = PAGE_URL

    def candidate_csv_urls(self) -> list[str]:
        """The CSV is cache-busted with a query string that changes per release;
        read the current one off the landing page, then fall back to the bare URL."""
        urls: list[str] = []
        try:
            page = fetch(PAGE_URL).content.decode("utf-8", "replace")
            for path in CSV_LINK_RE.findall(page):
                url = BASE + path.replace("&amp;", "&")
                if url not in urls:
                    urls.append(url)
        except FetchError as exc:
            log.warning("could not load PURSUE landing page: %s", exc)
        urls.append(CSV_URL)
        return urls

    def list_records(self) -> list[RecordInfo]:
        best: list[RecordInfo] = []
        errors = []
        for url in self.candidate_csv_urls():
            try:
                res = fetch(url)
                records = parse_csv(res.content.decode("utf-8-sig", "replace"))
            except (FetchError, csv.Error) as exc:
                errors.append(str(exc))
                continue
            log.info("PURSUE index %s via %s: %d records", url, res.via, len(records))
            if len(records) > len(best):
                best = records
        if not best:
            raise FetchError("PURSUE index unavailable: " + "; ".join(errors))
        return best
