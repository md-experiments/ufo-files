"""Episodes read by an LLM (Claude or OpenAI), for records without a curated entry.

The model gets a record's sighting passages (the publisher's description and
every page that reads like a report) with the same guide a person follows,
and returns one episode per distinct event. Results are cached by text,
model and taxonomy version, so a new release only costs its own records.
A failed call leaves the record to the rules for this run; it is retried on
the next one.
"""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..db import EpisodeCache, session_scope
from . import episodes as E

log = logging.getLogger(__name__)

EventKey = Literal[tuple(E.EVENTS)]  # type: ignore[valid-type]
OutcomeKey = Literal[tuple(E.OUTCOMES)]  # type: ignore[valid-type]
DetailKey = Literal[tuple(E.all_details())]  # type: ignore[valid-type]


class EpisodeOut(BaseModel):
    pages: list[int] = Field(description="Page numbers the episode is read from; 0 is the publisher's description.")
    summary: str = Field(description="One or two sentences, at most 60 words, saying what happened.")
    event: EventKey
    details: list[DetailKey]
    year: int | None = None
    place: str | None = None
    outcome: OutcomeKey = "not_stated"
    explanation: str | None = Field(default=None, description="A few words when identified, e.g. 'weather balloon'.")


class Episodes(BaseModel):
    episodes: list[EpisodeOut]


def system_prompt() -> str:
    return ("You read declassified U.S. government UFO / UAP records and list what happens in each: one episode per "
            "distinct event, each summarised in one or two plain sentences and classed with a fixed vocabulary. "
            "The text is often OCR'd and noisy.\n\n" + E.guide_text() + "\n\n" + E.RULES +
            "\nReturn the episodes in page order.")


def episodes_enabled() -> bool:
    s = get_settings()
    return bool(s.llm_available and s.llm_episodes)


def reader_id() -> str:
    """Who reads records without a curated entry (part of the analysis identity)."""
    s = get_settings()
    if episodes_enabled():
        return f"{s.llm_provider}:{s.llm_model}:t{E.TAXONOMY_VERSION}"
    return "rules"


def _key(text: str, model: str) -> str:
    return hashlib.sha256(f"t{E.TAXONOMY_VERSION}\n{model}\n{text}".encode()).hexdigest()


def _prompt(inp: dict, max_chars: int) -> str:
    head = {k: v for k, v in inp.items() if k not in ("pages", "text_hash")}
    parts = [f"<record>\n{json.dumps(head, ensure_ascii=False, indent=1)}\n</record>"]
    budget = max_chars
    pages = inp["pages"]
    kept, skipped = [], 0
    for p in pages:
        block = f'<page no="{p["page"]}" hints="{",".join(p["hints"])}">\n{p["text"]}\n</page>'
        if budget - len(block) < 0 and kept:
            skipped += 1
            continue
        budget -= len(block)
        kept.append(block)
    parts += kept
    if skipped:
        parts.append(f"<note>{skipped} further pages were left out for length.</note>")
    parts.append("List the episodes.")
    return "\n\n".join(parts)


def _read(inp: dict) -> list[dict] | None:
    from .. import llm

    s = get_settings()
    out = llm.parse(system_prompt(), _prompt(inp, s.llm_max_chars), Episodes, max_tokens=12000)
    if out is None:
        return None
    return [e.model_dump() for e in out.episodes]


def episodes_for_records(items: list, progress=None) -> dict[int, list]:
    """``items``: (Document, sighting units). Returns doc id -> episodes
    (``Ep``) for the records the model read (now or from the cache)."""
    progress = progress or (lambda *a: None)
    s = get_settings()
    model = s.llm_model
    inputs = {doc.id: E.reading_input(doc, us) for doc, us in items}
    keys = {doc.id: _key(json.dumps(inputs[doc.id]["pages"], ensure_ascii=False), model) for doc, _ in items}
    cached: dict[str, list] = {}
    uniq = list(dict.fromkeys(keys.values()))
    with session_scope() as db:
        for i in range(0, len(uniq), 500):
            for row in db.scalars(select(EpisodeCache).where(EpisodeCache.key.in_(uniq[i:i + 500]))):
                cached[row.key] = list(row.episodes)
    todo = [(doc, keys[doc.id]) for doc, _ in items if keys[doc.id] not in cached]
    todo = list({k: (d, k) for d, k in todo}.values())
    if todo:
        progress("LLM reading %d records for episodes with %s (%d already read)", len(todo), model, len(items) - len(todo))

        def run(pair):
            doc, key = pair
            try:
                return doc, key, _read(inputs[doc.id])
            except Exception as exc:  # network/API problems: the rules take this record
                log.warning("LLM episodes failed for %s: %s", doc.record_id, exc)
                return doc, key, exc

        done = errors = 0
        with ThreadPoolExecutor(max_workers=max(1, s.llm_workers)) as pool:
            for doc, key, res in pool.map(run, todo):
                done += 1
                if res is None or isinstance(res, Exception):
                    errors += 1
                    if errors <= 3:
                        progress("LLM episodes failed for %s (rules used): %s", doc.record_id, str(res or "declined")[:160])
                else:
                    with session_scope() as db:
                        db.merge(EpisodeCache(key=key, model=model, record_id=doc.record_id, episodes=res))
                    cached[key] = res
                if done % 25 == 0 or done == len(todo):
                    progress("LLM episodes: %d/%d records read, %d failed", done, len(todo), errors)
    out: dict[int, list] = {}
    for doc, us in items:
        raw = cached.get(keys[doc.id])
        if raw is None:
            continue
        valid = {p for p, _ in us} | {p.page_no for p in doc.pages}
        eps = [e for e in (E.clean_episode(r, valid) for r in raw) if e]
        if eps:
            out[doc.id] = eps
    return out
