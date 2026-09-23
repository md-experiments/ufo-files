"""Pluggable sources of released UAP records.

Each source knows how to list the records it currently publishes. The pipeline
diffs that listing against the database to find new or changed records, so a
source only has to describe "what is out there right now".
"""
from __future__ import annotations

from .base import RecordInfo, Source
from .pursue import PursueSource

SOURCES: dict[str, type[Source]] = {PursueSource.name: PursueSource}


def get_sources(names: list[str] | None = None) -> list[Source]:
    names = names or list(SOURCES)
    return [SOURCES[n]() for n in names]


__all__ = ["RecordInfo", "Source", "SOURCES", "get_sources"]
