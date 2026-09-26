"""Published series: records the publisher already grouped together.

Matches inside one series (sections of one FBI file, boxes of one Project
Blue Book collection, records sharing a collection-wide description) are not
discoveries, so the similarity step leaves them out.
"""
from __future__ import annotations

import re


def series_key(record_id: str, description: str | None, shared_descriptions: set[str]) -> str:
    """Records that belong to one published series (FBI file sections, Blue Book
    boxes) or share a collection-wide description."""
    if description and description in shared_descriptions:
        return "desc:" + str(hash(description))
    m = re.match(r"^(\d+_[A-Za-z0-9-]+_[A-Za-z0-9-]+)", record_id)  # NARA "65_HS1-834228961_62-HQ-83894_Section_001"
    if m:
        return "nara:" + m.group(1)
    return "rec:" + record_id
