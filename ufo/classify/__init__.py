"""Classification entry point: rules always run; Claude refines when configured."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from ..config import get_settings
from .rules import classify_rules, region_for_location
from .taxonomy import FACET_LABELS, FACETS, era_for_year, label

log = logging.getLogger(__name__)


@dataclass
class Classification:
    classifier: str
    summary: str | None
    key_points: list[str]
    kind: str
    assessment: str
    significance: int | None
    tags: dict[str, list[str]] = field(default_factory=dict)
    details: dict = field(default_factory=dict)


def _first_sentences(text: str, limit: int = 600) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = cut.rfind(". ")
    return cut[: end + 1] if end > 200 else cut.rstrip() + "…"


def classify_document(
    *,
    record_id: str,
    title: str,
    description: str | None,
    text: str | None,
    media_type: str,
    agency: str | None,
    location: str | None,
    incident_year: int | None,
    incident_date_raw: str | None = None,
    use_llm: bool | None = None,
) -> Classification:
    rules = classify_rules(title, description, text, media_type, location, incident_year)
    base = Classification(
        classifier="rules",
        summary=_first_sentences(description) if description else (_first_sentences(text) if text else None),
        key_points=[],
        kind=rules.kind,
        assessment=rules.assessment,
        significance=None,
        tags={k: list(v) for k, v in rules.tags.items()},
    )

    s = get_settings()
    use_llm = s.llm_available if use_llm is None else use_llm
    if not use_llm:
        return base

    from .llm import classify_llm

    meta = {
        "record_id": record_id,
        "title": title,
        "media_type": media_type,
        "agency": agency,
        "incident_date": incident_date_raw,
        "incident_location": location,
        "publisher_description": description,
    }
    try:
        out = classify_llm(meta, text)
    except Exception as exc:  # network/API problems: keep the rules result
        log.warning("LLM classification failed for %s: %s", record_id, exc)
        base.details["llm_error"] = str(exc)[:500]
        return base
    if out is None:
        base.details["llm_error"] = "declined"
        return base

    llm, truncated = out
    tags: dict[str, list[str]] = {
        "topic": list(dict.fromkeys(llm.topics)),
        "shape": list(dict.fromkeys(llm.shapes)),
        "domain": list(dict.fromkeys(llm.domains)),
        "witness": list(dict.fromkeys(llm.witnesses)),
        "sensor": list(dict.fromkeys(llm.sensors)),
        "program": list(dict.fromkeys(llm.programs)),
    }
    # Region and era are derived deterministically from the metadata; fall back
    # to the model's reading of the text when the metadata has none.
    region = rules.tags.get("region") or [r for r in (region_for_location(l) for l in llm.locations) if r][:1]
    if region:
        tags["region"] = region
    era = rules.tags.get("era") or [e for e in (era_for_year(y) for y in llm.incident_years) if e][:1]
    if era:
        tags["era"] = era
    return Classification(
        classifier=s.llm_model,
        summary=llm.summary,
        key_points=llm.key_points[:5],
        kind=llm.document_kind,
        assessment=llm.assessment,
        significance=llm.significance,
        tags={k: v for k, v in tags.items() if v},
        details={
            "locations": llm.locations,
            "people_and_organizations": llm.people_and_organizations,
            "incident_years": llm.incident_years,
            "significance_reason": llm.significance_reason,
            "llm_input_truncated": truncated,
        },
    )


__all__ = ["Classification", "classify_document", "FACETS", "FACET_LABELS", "label"]
