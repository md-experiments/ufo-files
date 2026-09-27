"""Tools for reading records by hand into episodes (the curated tier).

``export_input`` writes each classified record's sighting passages, with the
keyword hints, into JSON batches sized for one reader (a person, or Claude
in an editing session). The reader writes one output file per batch:
``{"<record id>": {"text_hash": "...", "episodes": [...]}, ...}``.
``import_output`` validates those files against the taxonomy and merges them
into ``data/episodes/curated.json.gz``, which the pipeline loads on every
analysis. ``check`` reports which records in the database have no curated
entry or were read from different text.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..db import Document, has_classification, session_scope
from . import episodes as E


def reader_instructions() -> str:
    return (
        "Read each record's passages and list the episodes: what happens, one or two sentences each, "
        "with the class, details, year, place and outcome from the vocabulary below.\n\n"
        + E.RULES + "\n\n" + E.guide_text()
        + "\n\nOutput: a JSON object keyed by record_id; each value is {\"text_hash\": <copied from the input>, "
          "\"episodes\": [{\"pages\": [..], \"summary\": \"..\", \"event\": \"..\", \"details\": [..], "
          "\"year\": 1952, \"place\": \"..\", \"outcome\": \"..\", \"explanation\": null}, ...]}.")


def export_input(out_dir: Path, batch_chars: int = 140_000, only_new: bool = False) -> tuple[int, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    curated = E.load_curated() if only_new else {}
    items = []
    with session_scope() as db:
        docs = db.scalars(select(Document).where(has_classification())
                          .options(selectinload(Document.pages), selectinload(Document.release))).all()
        for doc in docs:
            if only_new and doc.record_id in curated:
                continue
            us = E.sighting_units(doc)
            inp = E.reading_input(doc, us)
            items.append((sum(len(p["text"]) for p in inp["pages"]) + len(json.dumps(inp["description"] or "")), inp))
    items.sort(key=lambda x: x[1]["record_id"])
    batches: list[list[dict]] = []
    size = 0
    for n, inp in items:
        if batches and size + n > batch_chars and size > 0:
            batches.append([])
            size = 0
        if not batches:
            batches.append([])
        batches[-1].append(inp)
        size += n
    for i, batch in enumerate(batches, start=1):
        (out_dir / f"batch-{i:03d}.json").write_text(json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "GUIDE.md").write_text(reader_instructions(), encoding="utf-8")
    return len(items), len(batches)


def validate_output(data: dict, known_pages: dict[str, set[int]] | None = None) -> tuple[dict, list[str]]:
    """Clean a reader's output. Returns (records, problems)."""
    records, problems = {}, []
    if not isinstance(data, dict):
        return records, ["output is not a JSON object keyed by record id"]
    for rid, entry in data.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("episodes"), list):
            problems.append(f"{rid}: entry needs an 'episodes' list")
            continue
        valid = known_pages.get(rid) if known_pages else None
        if known_pages is not None and valid is None:
            problems.append(f"{rid}: unknown record id")
            continue
        eps = []
        for i, raw in enumerate(entry["episodes"]):
            if not isinstance(raw, dict):
                problems.append(f"{rid}[{i}]: not an object")
                continue
            if raw.get("event") not in E.EVENTS:
                problems.append(f"{rid}[{i}]: unknown event class {raw.get('event')!r}")
            bad = [d for d in raw.get("details") or [] if d not in E.VALID_DETAILS]
            if bad:
                problems.append(f"{rid}[{i}]: unknown details {bad}")
            if valid is not None:
                missing = [p for p in raw.get("pages") or [] if p not in valid]
                if missing:
                    problems.append(f"{rid}[{i}]: pages not in the record {missing}")
            e = E.clean_episode(raw, valid)
            if e is None:
                problems.append(f"{rid}[{i}]: dropped (no class or summary)")
                continue
            if len(e.summary.split()) > 90:
                problems.append(f"{rid}[{i}]: summary is long ({len(e.summary.split())} words)")
            eps.append(e.to_dict())
        if not eps:
            problems.append(f"{rid}: no valid episodes")
            continue
        records[rid] = {"text_hash": entry.get("text_hash"), "episodes": eps}
    return records, problems


def _known_pages() -> dict[str, set[int]]:
    out = {}
    with session_scope() as db:
        for doc in db.scalars(select(Document).where(has_classification()).options(selectinload(Document.pages))):
            out[doc.record_id] = {0} | {p.page_no for p in doc.pages}
    return out


def import_output(in_dir: Path, out_path: Path | None = None) -> str:
    known = _known_pages()
    merged = dict(E.load_curated(out_path or E.CURATED_PATH)) if (out_path or E.CURATED_PATH).exists() else {}
    all_problems: list[str] = []
    n_files = 0
    for f in sorted(in_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            all_problems.append(f"{f.name}: invalid JSON ({exc})")
            continue
        records, problems = validate_output(data, known)
        all_problems += [f"{f.name}: {p}" for p in problems]
        merged.update(records)
        n_files += 1
    E.save_curated(merged, out_path or E.CURATED_PATH)
    lines = [f"merged {n_files} files: {len(merged)} records, "
             f"{sum(len(r['episodes']) for r in merged.values())} episodes -> {out_path or E.CURATED_PATH}"]
    lines += [f"  ! {p}" for p in all_problems[:200]]
    if len(all_problems) > 200:
        lines.append(f"  ... and {len(all_problems) - 200} more problems")
    return "\n".join(lines)


def check() -> str:
    curated = E.load_curated()
    missing, stale, classes = [], [], Counter()
    with session_scope() as db:
        docs = db.scalars(select(Document).where(has_classification()).options(selectinload(Document.pages))).all()
        for doc in docs:
            entry = curated.get(doc.record_id)
            if not entry:
                missing.append(doc.record_id)
                continue
            if entry.get("text_hash") and entry["text_hash"] != E.text_hash(E.sighting_units(doc)):
                stale.append(doc.record_id)
            for e in entry["episodes"]:
                classes[(e["event"], E.subcategory_for(e["event"], e["details"]))] += 1
    lines = [f"{len(docs)} records in the database, {len(curated)} with curated episodes, "
             f"{len(missing)} without, {len(stale)} read from different text"]
    for (ev, sub), n in sorted(classes.items(), key=lambda kv: (-kv[1])):
        lines.append(f"  {n:5d}  {ev}/{sub}")
    if missing:
        lines.append("missing: " + ", ".join(missing[:40]) + (" ..." if len(missing) > 40 else ""))
    if stale:
        lines.append("different text: " + ", ".join(stale[:40]) + (" ..." if len(stale) > 40 else ""))
    return "\n".join(lines)
