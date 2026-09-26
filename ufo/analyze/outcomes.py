"""Explained versus unresolved: does anything set the cases apart that the
government could not explain?

Each classified record carries an official assessment: *unresolved* (the
publisher or the file says the object was never identified), *explained*
(balloon, aircraft, birds, astronomical, a camera artifact, a hoax...) or
*no assessment given*. That verdict comes from the publisher's description
(and from the file text when an LLM classifies). A second, **derived**
verdict is read from the pages themselves (see ``verdicts``): what the file
says the object was, or that it never was identified. By default a record
with no stated verdict takes its derived one, and the two are counted
together; a stated-only view leaves the derived verdicts out. Every derived
verdict is listed with the sentence it came from, and a record whose text
disagrees with its stated verdict is flagged.

The summary shows how the three groups split across decades, agencies,
regions, kinds of record, shapes, sensors and witnesses, and which reported
details are more common among the unresolved cases than among the rest.
Explained cases get the same treatment once there are enough of them; until
then they are listed one by one. Sighting accounts that state their own
verdict are compared in the same way.
"""
from __future__ import annotations

from collections import Counter

from ..classify.taxonomy import label as facet_label
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


def derive_record(verdicts: list[dict]) -> dict | None:
    """A record's verdict from the ones its pages state: dicts with group,
    category, strong, page and sentence. Official wording outweighs hedges;
    explained wins when it outnumbers unresolved, unresolved when stated at
    all otherwise; an even split is no verdict."""
    if not verdicts:
        return None
    strong = [v for v in verdicts if v.get("strong")] or verdicts
    explained = [v for v in strong if v["group"] == "explained"]
    unresolved = [v for v in strong if v["group"] == "unresolved"]
    if len(explained) > len(unresolved):
        cat = Counter(v["category"] for v in explained).most_common(1)[0][0]
        pick = [v for v in explained if v["category"] == cat][-1]
        return {"group": "explained", "assessment": cat, "page": pick["page"], "sentence": pick["sentence"]}
    if unresolved and len(unresolved) > len(explained):
        pick = unresolved[-1]
        return {"group": "unresolved", "assessment": "unresolved", "page": pick["page"], "sentence": pick["sentence"]}
    return None


def outcome_summary(records: list[dict], accounts: list[dict], use_derived: bool = True) -> dict:
    """``records`` as for ``redaction_summary``, plus ``assessment`` and
    ``verdicts`` (what its pages state). ``accounts``: dicts with id, doc,
    tags and ``verdict`` (a dict with group, category, sentence, source) or
    None. With ``use_derived`` a record with no stated verdict takes its
    derived one."""
    recs = []
    for r in records:
        stated = outcome_group(r.get("assessment"))
        if not stated:
            continue  # unclassified
        derived = derive_record(r.get("verdicts") or [])
        group, assessment, source = stated, r["assessment"], "stated"
        if use_derived and derived and stated == "not_assessed":
            group, assessment, source = derived["group"], derived["assessment"], "derived"
        recs.append(dict(r, stated=stated, derived=derived, group=group, effective=assessment, source=source))
    groups = list(GROUPS)
    counts = Counter(r["group"] for r in recs)
    explanations = Counter(r["effective"] for r in recs if r["group"] == "explained")

    by = []
    for facet, flabel in SPLIT_FACETS:
        if facet == "assessment":
            continue
        rows = split_by(((r["group"], v) for r in recs for f, v in record_facets(r) if f == facet), groups)
        rows = [row for row in rows if row["total"] >= 3]
        derived_by_value = Counter(v for r in recs if r["source"] == "derived" for f, v in record_facets(r) if f == facet)
        for row in rows:
            row["label"] = facet_value_label(facet, row["value"])
            row["link"] = None if facet in ("agency", "decade", "media") else f"{facet}:{row['value']}"
            row["derived"] = derived_by_value[row["value"]]
        if len(rows) >= 2:
            by.append({"facet": facet, "label": flabel, "rows": rows[:12]})

    with_details = [r for r in recs if r["items"]]
    profiles = {}
    for g in ("unresolved", "explained"):
        members = [r for r in with_details if r["group"] == g]
        others = [r for r in with_details if r["group"] != g]
        profiles[g] = {
            "n": len(members), "records": counts[g], "of": len(with_details),
            "derived": sum(1 for r in members if r["source"] == "derived"),
            "distinctive": lift_rows((r["items"] for r in members), (r["items"] for r in others), tag_label),
            "common": top_rows((r["items"] for r in members), tag_label) if len(members) >= MIN_GROUP else [],
        }

    def case(r):
        return {"id": r["id"], "record_id": r["record_id"], "title": r["title"], "agency": r["agency"], "year": r["year"],
                "assessment": facet_label("assessment", r["effective"]), "source": r["source"],
                "details": [tag_label(k) for k in sorted(r["items"])][:8]}

    explained_cases = sorted((r for r in recs if r["group"] == "explained"), key=lambda r: (r["year"] or 9999))
    # every verdict read from the text, used or not, so nothing derived is hidden
    derived_cases = []
    for r in sorted((r for r in recs if r["derived"]), key=lambda r: (r["year"] or 9999)):
        d = r["derived"]
        derived_cases.append({
            "id": r["id"], "record_id": r["record_id"], "title": r["title"], "agency": r["agency"], "year": r["year"],
            "group": d["group"], "assessment": facet_label("assessment", d["assessment"]), "page": d["page"],
            "sentence": d["sentence"], "stated": r["stated"], "stated_label": facet_label("assessment", r["assessment"]),
            "used": r["source"] == "derived",
            "agrees": r["stated"] == "not_assessed" or r["stated"] == d["group"],
        })

    # accounts that state their own verdict
    acc_groups = {g: [a for a in accounts if a.get("verdict") and a["verdict"]["group"] == g] for g in ("explained", "unresolved")}
    if not use_derived:
        acc_groups = {g: [] for g in acc_groups}
    acc = {"n": sum(len(v) for v in acc_groups.values()), "of": len(accounts)}
    for g, members in acc_groups.items():
        others = [a for a in accounts if a not in members]
        acc[g] = {
            "n": len(members), "records": len({a["doc"] for a in members}), "accounts": [a["id"] for a in members],
            "distinctive": lift_rows((a["tags"] for a in members), (a["tags"] for a in others), tag_label),
            "common": top_rows((a["tags"] for a in members), tag_label) if len(members) >= MIN_GROUP else [],
        }
    return {
        "overview": {**{g: counts[g] for g in groups}, "classified": len(recs), "with_details": len(with_details),
                     "derived": Counter(r["group"] for r in recs if r["source"] == "derived"),
                     "derivable": len(derived_cases), "disagree": sum(1 for c in derived_cases if not c["agrees"]),
                     "use_derived": use_derived},
        "explanations": [{"key": k, "label": facet_label("assessment", k), "count": n}
                         for k, n in sorted(explanations.items(), key=lambda kv: -kv[1])],
        "by": by,
        "profiles": profiles,
        "explained_cases": [case(r) for r in explained_cases[:24]],
        "derived_cases": derived_cases[:60],
        "accounts": acc,
        "min_group": MIN_GROUP,
    }
