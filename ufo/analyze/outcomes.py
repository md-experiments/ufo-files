"""Explained versus unresolved: does anything set the cases apart that the
government could not explain?

Each classified record carries an official assessment: *unresolved* (the
publisher or the file says the object was never identified), *explained*
(balloon, aircraft, birds, astronomical, a camera artifact, a hoax...) or
*no assessment given*. This summary shows how the three split across
decades, agencies, regions, kinds of record, shapes, sensors and witnesses,
and which reported details are more common among the unresolved cases than
across the archive as a whole. Explained cases get the same treatment once
there are enough of them; until then they are listed one by one.

The assessments come from the publisher's descriptions (and, when an LLM is
configured, from the file text), so the split reflects what the archive
says, not an independent evaluation.
"""
from __future__ import annotations

from collections import Counter

from ..classify.taxonomy import FACETS as TAXONOMY, label as facet_label
from .compare import MIN_GROUP, lift_rows, split_by, top_rows
from .events import tag_label
from .redaction import FACETS as SPLIT_FACETS, facet_value_label, record_facets

GROUPS = {"unresolved": "Unresolved", "explained": "Explained", "not_assessed": "No assessment given"}


def outcome_group(assessment: str | None) -> str | None:
    if not assessment:
        return None
    if assessment == "unresolved":
        return "unresolved"
    if assessment == "not_assessed":
        return "not_assessed"
    return "explained"  # resolved_* and hoax


def outcome_summary(records: list[dict]) -> dict:
    """``records`` as for ``redaction_summary``, plus ``assessment``."""
    recs = [dict(r, group=outcome_group(r.get("assessment"))) for r in records]
    recs = [r for r in recs if r["group"]]
    groups = list(GROUPS)
    counts = Counter(r["group"] for r in recs)
    explanations = Counter(r["assessment"] for r in recs if r["group"] == "explained")

    by = []
    for facet, flabel in SPLIT_FACETS:
        if facet == "assessment":
            continue
        rows = split_by(((r["group"], v) for r in recs for f, v in record_facets(r) if f == facet), groups)
        rows = [row for row in rows if row["total"] >= 3]
        for row in rows:
            row["label"] = facet_value_label(facet, row["value"])
            row["link"] = None if facet in ("agency", "decade", "media") else f"{facet}:{row['value']}"
        if len(rows) >= 2:
            by.append({"facet": facet, "label": flabel, "rows": rows[:12]})

    with_details = [r for r in recs if r["items"]]
    profiles = {}
    for g in ("unresolved", "explained"):
        members = [r for r in with_details if r["group"] == g]
        others = [r for r in with_details if r["group"] != g]
        profiles[g] = {
            "n": len(members), "records": counts[g], "of": len(with_details),
            "distinctive": lift_rows((r["items"] for r in members), (r["items"] for r in others), tag_label),
            "common": top_rows((r["items"] for r in members), tag_label) if len(members) >= MIN_GROUP else [],
        }
    explained_cases = sorted((r for r in recs if r["group"] == "explained"), key=lambda r: (r["year"] or 9999))
    return {
        "overview": {**{g: counts[g] for g in groups}, "classified": len(recs), "with_details": len(with_details)},
        "explanations": [{"key": k, "label": facet_label("assessment", k), "count": n}
                         for k, n in sorted(explanations.items(), key=lambda kv: -kv[1])],
        "by": by,
        "profiles": profiles,
        "explained_cases": [{"id": r["id"], "record_id": r["record_id"], "title": r["title"], "agency": r["agency"],
                             "year": r["year"], "assessment": facet_label("assessment", r["assessment"]),
                             "details": [tag_label(k) for k in sorted(r["items"])][:8]}
                            for r in explained_cases[:24]],
        "min_group": MIN_GROUP,
        "assessments": {k: TAXONOMY["assessment"][k] for k in TAXONOMY["assessment"]},
    }
