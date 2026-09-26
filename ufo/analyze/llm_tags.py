"""Event tags from an LLM (Claude or OpenAI), when one is configured.

The keyword rules pick out candidate passages (at least one detail of the
phenomenon). The model then reads each passage and returns the details it
actually reports, with the words each came from, and says whether the
passage describes an observation at all (rather than a blank form,
instructions, a rebuttal or general discussion). It uses the same tag
vocabulary as the rules, so everything downstream is unchanged.

Results are cached by text and model: a passage is sent once, and a new
release only costs its own new passages. If a call fails, those passages
keep their rule-based tags and are retried on the next run.
"""
from __future__ import annotations

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..config import get_settings
from ..db import TagCache, session_scope
from .events import DIMENSIONS, TAGS, Account

log = logging.getLogger(__name__)

TAGGER_VERSION = 1
BATCH = 8

TagKey = Literal[tuple(t.key for t in TAGS)]  # type: ignore[valid-type]


class Detail(BaseModel):
    tag: TagKey
    quote: str = Field(description="The exact words in the passage this detail comes from (1-12 words, copied verbatim, OCR errors included).")


class PassageTags(BaseModel):
    id: int
    describes_observation: bool = Field(description="True if the passage reports something a witness or sensor actually observed; false for blank forms, instructions, questions, lists of options, or general discussion that reports no specific observation.")
    details: list[Detail]


class Batch(BaseModel):
    passages: list[PassageTags]


def _vocabulary() -> str:
    lines = []
    for dim, (label, _) in DIMENSIONS.items():
        tags = ", ".join(f"{t.key} ({t.label})" for t in TAGS if t.dim == dim)
        lines.append(f"- {label}: {tags}")
    return "\n".join(lines)


SYSTEM = f"""You tag passages from declassified U.S. government UFO / UAP records with what was observed.

Each passage is a paragraph or report form from a scanned document; the text is often OCR'd and noisy. For each passage, list the details it reports about the observed phenomenon, choosing tags only from this vocabulary:

{_vocabulary()}

Rules:
- Tag only what the passage reports as observed in a specific sighting: what the object looked like, how its light behaved, what it sounded like, how it moved, visible structure, effects on people, machines, animals or the ground, how many objects, how long it lasted, the time of day, and how it was observed.
- Do not tag printed form labels or empty fields ("Sound: none", "b. Roar, whistle, whoosh."), questions, instructions, lists of options, names (a person called Sparks), or details mentioned only to explain the sighting away ("probably a meteor").
- A detail the witness explicitly denies is not present, except the negative tags: use "silent" for "no sound" and "no_wings" for "no wings / no exhaust / no protrusions".
- brief = under a minute, minutes = 1 to 30 minutes, long = half an hour or more.
- For each tag, quote the exact words it comes from.
- If the passage reports no specific observation, set describes_observation to false and return no details.
Return one entry per passage, with its id."""


def tagger_id() -> str:
    """Identifies who tags accounts, so a change of tagger triggers a re-analysis."""
    s = get_settings()
    if s.llm_available and s.llm_tagging:
        return f"{s.llm_provider}:{s.llm_model}:v{TAGGER_VERSION}"
    return "rules"


def _key(text: str, model: str) -> str:
    return hashlib.sha256(f"v{TAGGER_VERSION}\n{model}\n{text}".encode()).hexdigest()


def _span(text: str, quote: str) -> tuple[int, int] | None:
    words = re.findall(r"\S+", quote)
    for n in (len(words), min(len(words), 4), min(len(words), 2)):
        if not n:
            break
        rx = r"\s+".join(re.escape(w) for w in words[:n])
        m = re.search(rx, text, re.IGNORECASE)
        if m:
            return m.start(), m.end()
    return None


def _tag_batch(texts: list[str]) -> list[tuple[bool, list[str], list[list]]] | None:
    from .. import llm

    prompt = "\n\n".join(f'<passage id="{i}">\n{t}\n</passage>' for i, t in enumerate(texts))
    prompt += "\n\nTag each passage."
    out = llm.parse(SYSTEM, prompt, Batch, max_tokens=8000)
    if out is None:
        return None
    by_id = {p.id: p for p in out.passages}
    results = []
    for i, text in enumerate(texts):
        p = by_id.get(i)
        if p is None:
            return None  # an incomplete answer: keep the rules for the whole batch
        tags, spans = [], []
        for d in p.details if p.describes_observation else []:
            if d.tag in tags:
                continue
            tags.append(d.tag)
            sp = _span(text, d.quote)
            if sp:
                spans.append([d.tag, *sp])
        results.append((p.describes_observation, tags, spans))
    return results


def refine(accounts: list[Account], progress=None) -> dict:
    """Replace the rule-based tags of ``accounts`` with the LLM's, in place.
    Each batch's tags are saved as soon as they arrive, so an interrupted run
    (a redeploy) resumes where it stopped. Returns counts: cached, tagged now,
    failed (kept rules)."""
    progress = progress or (lambda *a: None)
    s = get_settings()
    stats = {"cached": 0, "tagged": 0, "failed": 0}
    if not accounts or not (s.llm_available and s.llm_tagging):
        return stats
    model = s.llm_model
    keys = [_key(a.text, model) for a in accounts]
    cached: dict[str, tuple[bool, list, list]] = {}
    uniq = list(dict.fromkeys(keys))
    with session_scope() as db:
        for i in range(0, len(uniq), 500):
            for row in db.scalars(select(TagCache).where(TagCache.key.in_(uniq[i:i + 500]))):
                cached[row.key] = (row.observation, list(row.tags), [list(x) for x in row.spans])
    todo: dict[str, str] = {}
    for a, k in zip(accounts, keys):
        if k not in cached:
            todo.setdefault(k, a.text)
    if not todo:
        progress("LLM tags: all %d passages already tagged by %s (cached)", len(uniq), model)
    if todo:
        items = list(todo.items())
        batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
        progress("LLM tagging %d passages in %d requests with %s (%d already done)",
                 len(items), len(batches), model, len(uniq) - len(items))

        def run(batch):
            try:
                return batch, _tag_batch([t for _, t in batch])
            except Exception as exc:  # network/API problems: keep the rules for these
                log.warning("LLM tagging failed for a batch: %s", exc)
                return batch, exc

        done = errors = 0
        with ThreadPoolExecutor(max_workers=max(1, s.llm_workers)) as pool:
            for batch, res in pool.map(run, batches):
                done += 1
                if res is None or isinstance(res, Exception):
                    stats["failed"] += len(batch)
                    errors += 1
                    if errors <= 3:
                        progress("LLM tagging batch failed (kept rules): %s", str(res or "declined")[:200])
                else:
                    with session_scope() as db:
                        for (k, _), (obs, tags, spans) in zip(batch, res):
                            db.merge(TagCache(key=k, model=model, observation=obs, tags=tags, spans=spans))
                            cached[k] = (obs, tags, spans)
                            stats["tagged"] += 1
                if done % 25 == 0 or done == len(batches):
                    progress("LLM tagging: %d/%d requests done, %d failed", done, len(batches), errors)
    for a, k in zip(accounts, keys):
        if k not in cached:
            continue  # failed this time: rule tags stay
        if k not in todo:
            stats["cached"] += 1
        obs, tags, spans = cached[k]
        a.tags = list(tags) if obs else []
        a.spans = [list(x) for x in spans] if obs else []
    return stats
