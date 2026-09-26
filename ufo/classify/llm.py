"""Optional LLM classifier producing plain-language summaries and structured
labels. Enabled when ANTHROPIC_API_KEY or OPENAI_API_KEY is set (see ``ufo.llm``)."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..config import get_settings
from .taxonomy import FACETS

log = logging.getLogger(__name__)


def _lit(facet: str):
    return Literal[tuple(FACETS[facet])]  # type: ignore[valid-type]


Kind = _lit("kind")
Topic = _lit("topic")
Shape = _lit("shape")
Domain = _lit("domain")
Witness = _lit("witness")
Sensor = _lit("sensor")
Assessment = _lit("assessment")
Program = _lit("program")


class LLMClassification(BaseModel):
    summary: str = Field(description="2-4 plain-language sentences a member of the public can understand: what this record is and what it describes.")
    key_points: list[str] = Field(description="Up to 5 short, concrete facts from the record (who, what, when, where, what was observed, what was concluded).")
    document_kind: Kind
    topics: list[Topic]
    shapes: list[Shape] = Field(description="Shapes of the reported object(s); empty if none described.")
    domains: list[Domain]
    witnesses: list[Witness]
    sensors: list[Sensor] = Field(description="How the phenomenon was observed or recorded.")
    assessment: Assessment = Field(description="The official conclusion stated in the record, not your own opinion.")
    programs: list[Program]
    locations: list[str] = Field(description="Specific places named for the incident(s), most specific first.")
    people_and_organizations: list[str] = Field(description="Named people, units or organizations central to the record (no redacted names).")
    incident_years: list[int]
    significance: int = Field(ge=1, le=5, description="How notable this record is for someone following the UFO releases: 1 = routine/administrative, 5 = striking, well-documented event.")
    significance_reason: str


SYSTEM = """You catalogue declassified U.S. government records about UFOs / unidentified anomalous phenomena (UAP) so the public can quickly see what has been released.

Work only from the record provided. Text may come from OCR of old or redacted scans: ignore OCR noise and redaction markers (e.g. "(b)(6)", "1.4a"), and never guess redacted content. Report what the document says, including the official assessment, without adding your own conclusions about whether UAP are extraterrestrial. Use empty lists when a field does not apply."""


def _build_prompt(meta: dict, text: str | None, max_chars: int) -> tuple[str, bool]:
    lines = [f"{k}: {v}" for k, v in meta.items() if v]
    truncated = False
    body = text or ""
    if len(body) > max_chars:
        # Keep the beginning and end, which usually carry the summary/conclusions,
        # and record that the middle was not seen.
        head = body[: int(max_chars * 0.8)]
        tail = body[-int(max_chars * 0.2):]
        body = f"{head}\n\n[... {len(text) - max_chars:,} characters omitted ...]\n\n{tail}"
        truncated = True
    prompt = "<metadata>\n" + "\n".join(lines) + "\n</metadata>\n"
    if body:
        prompt += f"<extracted_text>\n{body}\n</extracted_text>\n"
    else:
        prompt += "(No document text: this record is a photo/video/audio item; classify from the metadata.)\n"
    prompt += "\nClassify this record."
    return prompt, truncated


def classify_llm(meta: dict, text: str | None) -> tuple[LLMClassification, bool] | None:
    """Returns (classification, input_truncated) or None when the model declined."""
    from .. import llm

    s = get_settings()
    prompt, truncated = _build_prompt(meta, text, s.llm_max_chars)
    out = llm.parse(SYSTEM, prompt, LLMClassification)
    if out is None:
        log.warning("LLM declined to classify %s", meta.get("record_id"))
        return None
    return out, truncated
