from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class RecordInfo:
    record_id: str
    title: str
    media_type: str  # pdf | image | video | audio
    release_date: date
    agency: str | None = None
    description: str | None = None
    incident_date: date | None = None
    incident_date_raw: str | None = None
    incident_year: int | None = None
    incident_location: str | None = None
    file_url: str | None = None  # the file we download and extract (PDFs only are extracted)
    thumb_url: str | None = None
    video_id: str | None = None
    related_ids: list[str] = field(default_factory=list)
    redacted: bool | None = None
    featured: bool = False
    raw: dict = field(default_factory=dict)

    @property
    def row_hash(self) -> str:
        payload = asdict(self)
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class Source(ABC):
    name: str = "base"
    label: str = "Base source"
    homepage: str = ""

    @abstractmethod
    def list_records(self) -> list[RecordInfo]:
        """Return every record the source currently publishes."""

    def release_label(self, number: int | None, release_date: date) -> str:
        prefix = f"Release {number:02d}" if number else "Release"
        return f"{prefix} · {release_date:%-d %b %Y}"
