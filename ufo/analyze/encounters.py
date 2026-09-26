"""Close encounters, in J. Allen Hynek's three kinds.

Hynek's 1972 scale classes a report by how close it came:

* **first kind**: the object was seen within about 150 metres (500 feet):
  landed, on the ground, directly overhead, at treetop height;
* **second kind**: it left physical effects: engines or radios failing,
  instruments disturbed, ground traces, heat, burns or other effects on
  witnesses, animals reacting;
* **third kind**: occupants or beings were seen with it.

Each sighting account is tested against the three (an account can be in
several). The kinds are then compared: how many accounts and records, which
decades and agencies, and which reported details are more common in each
kind than across all accounts. A kind with too few accounts is listed
rather than compared.
"""
from __future__ import annotations

import re
from collections import Counter

from .compare import MIN_GROUP, decade_of, lift_rows, top_rows
from .events import BY_TAG, dim_of, tag_label

KINDS = {
    "ce1": ("Close encounter of the first kind", "seen within about 150 metres: landed, on the ground, directly overhead"),
    "ce2": ("Close encounter of the second kind", "physical effects: engines, instruments, ground traces, heat, animals, witnesses"),
    "ce3": ("Close encounter of the third kind", "occupants or beings seen with the object"),
}
CLOSE_FEET = 500

_NUM = r"(?:\d{1,3}(?:,\d{3})*|\d+|a few|few|several|ten|fifteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|(?:one |two |three |four |five )?hundred)"
_WORDS = {"a few": 3, "few": 3, "several": 5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40,
          "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "one hundred": 100,
          "two hundred": 200, "three hundred": 300, "four hundred": 400, "five hundred": 500}
_UNIT_FEET = {"feet": 1, "foot": 1, "ft": 1, "yards": 3, "yard": 3, "yds": 3, "meters": 3.28, "metres": 3.28, "m": 3.28}
_DISTANCE = re.compile(
    rf"(?<![\d,.])\b({_NUM})\s*(feet|foot|ft|yards|yard|yds|meters|metres)\.?\s*"
    r"(?:away|from|above|over|off|distant|in front|overhead|of the ground|of the witness|of (?:him|her|them|us|me))\b",
    re.I)
_WITHIN = re.compile(rf"\bwithin\s+(?:a\s+)?({_NUM})\s*(feet|foot|ft|yards|yard|yds|meters|metres)\b", re.I)
_OBJ = r"(?:object|objects|craft|saucer|disc|disk|sphere|ball|light|lights|thing|it|ufo|ship|vehicle)"
_CLOSE = re.compile(
    rf"\b{_OBJ}\s+(?:\w+\s+){{0,3}}(?:landed|touched down|came to rest|sat|resting|settled) (?:on|in|near|at|by)\b|"
    r"\blanded (?:in|on|near|beside|next to) (?:a|the|his|her|their) (?:field|ground|road|pasture|farm|yard|hill|"
    r"clearing|meadow|lawn|highway|beach|lot|lake|water)\b|"
    r"\bon the ground\b|\bat ground level\b|\btree ?top (?:level|height)\b|\bjust above the (?:ground|trees|treetops|"
    r"house|car|road|field|water|roof|rooftops)\b|\bdirectly (?:over|above|overhead)\b|"
    r"\bhover(?:ed|ing)? (?:over|above) (?:the|a|his|her|their|our) (?:car|house|truck|vehicle|road|field|barn|farm|"
    r"yard|home|witness|witnesses|him|them|us)\b|\bat close range\b|\bclose enough to (?:see|touch|make out)\b|"
    r"\barm'?s length\b|\bnearly (?:hit|struck) (?:the|his|her|their) (?:car|truck|vehicle|windshield)\b",
    re.I)
_NEG = re.compile(r"\b(?:no|not|never|without|neither|nor)\b[^.;:\n]{0,20}$", re.I)

_OCCUPANTS = re.compile(
    r"\boccupants? of (?:(?:the|a|its|these|those|one|each|another|several|two|three|flying) )?(?:craft|saucers?|"
    r"dis[ck]s?|objects?|ships?|vehicles?|ufos?|machine)\b|"
    r"\b(?:little|small|tiny|strange|odd) (?:men|man|people|beings|creatures|humanoids?|figures)\b|"
    r"\bhumanoids?\b|\bufonauts?\b|\bcrew of the (?:craft|saucer|ship|object)\b|"
    r"\b(?:beings|creatures|entities|figures|men|occupants)\b(?: \w+){0,3} (?:emerged|came out|stepped out|got out|"
    r"climbed out|left the (?:craft|saucer|ship|object)|walk(?:ed|ing) (?:away|around|toward|towards|from)|standing "
    r"(?:next to|beside|near|by|under|around)|entered the (?:craft|saucer|ship|object))\b|"
    r"\bbeings? (?:whose height|about \d|of (?:small|short|human) (?:stature|size|form))\b",
    re.I)
_HUMAN_CONTEXT = re.compile(r"\b(?:car|sedan|truck|automobile|vehicle|plane|aircraft|boat|house|building|planet|earth)\W+"
                            r"(?:\w+\W+){0,2}occupants?\b|\boccupants? of (?:another|other) planets?\b", re.I)


def _feet(number: str, unit: str) -> float:
    n = _WORDS.get(number.lower())
    if n is None:
        n = float(number.replace(",", ""))
    return n * _UNIT_FEET[unit.lower().rstrip(".")]


def _negated(text: str, start: int) -> bool:
    return bool(_NEG.search(text[max(0, start - 30):start]))


def is_close(text: str) -> bool:
    """The object came within about 150 m of the witness."""
    for m in list(_DISTANCE.finditer(text)) + list(_WITHIN.finditer(text)):
        if _feet(m.group(1), m.group(2)) <= CLOSE_FEET and not _negated(text, m.start()):
            return True
    return any(not _negated(text, m.start()) for m in _CLOSE.finditer(text))


def has_occupants(text: str) -> bool:
    """Occupants or beings were seen with the object (not the occupants of a car)."""
    for m in _OCCUPANTS.finditer(text):
        window = text[max(0, m.start() - 60):m.end()]
        if not _HUMAN_CONTEXT.search(window):
            return True
    return False


def encounter_kinds(text: str, tags: list[str]) -> set[str]:
    """Hynek kinds an account belongs to, from its text and event tags."""
    text = text or ""
    out = set()
    if "descend" in tags or is_close(text):
        out.add("ce1")
    if any(dim_of(t) == "effect" for t in tags):
        out.add("ce2")
    if has_occupants(text):
        out.add("ce3")
    return out


# ---------------------------------------------------------------------------

def encounter_summary(accounts: list[dict], docs: dict) -> dict:
    """``accounts``: dicts with id, doc, page, tags, text, year, places.
    ``docs``: id -> record dict with agency, title, tags."""
    for a in accounts:
        a["kinds"] = encounter_kinds(a["text"], a["tags"])
    kinds = []
    for key, (name, desc) in KINDS.items():
        members = [a for a in accounts if key in a["kinds"]]
        records = Counter(a["doc"] for a in members)
        decades = Counter(d for d in (decade_of(a["year"]) for a in members) if d)
        agencies = Counter(docs[a["doc"]]["agency"] for a in members if docs.get(a["doc"], {}).get("agency"))
        places = Counter(p for a in members for p in a.get("places", ()))
        # details the kind is defined by are not "distinctive" of it
        exclude = {"descend"} if key == "ce1" else ({t for t in BY_TAG if dim_of(t) == "effect"} if key == "ce2" else set())
        member_tags = [set(a["tags"]) - exclude for a in members]
        other_tags = [set(a["tags"]) - exclude for a in accounts if key not in a["kinds"]]
        enough = len(members) >= MIN_GROUP and len(records) >= 3
        # examples from different records, spread across agencies
        seen_docs, seen_ag, examples = set(), set(), []
        for rnd in (0, 1):
            for a in sorted(members, key=lambda x: (-len(x["tags"]), x["id"])):
                ag = docs.get(a["doc"], {}).get("agency")
                if a["doc"] in seen_docs or (rnd == 0 and ag in seen_ag) or len(examples) >= 4:
                    continue
                seen_docs.add(a["doc"])
                seen_ag.add(ag)
                examples.append(a["id"])
        kinds.append({
            "key": key, "name": name, "description": desc,
            "accounts": [a["id"] for a in members], "size": len(members), "record_count": len(records),
            "years": [min(a["year"] for a in members if a["year"]), max(a["year"] for a in members if a["year"])]
            if any(a["year"] for a in members) else None,
            "decades": [{"decade": d, "count": n} for d, n in sorted(decades.items())],
            "agencies": [{"agency": ag, "count": n} for ag, n in agencies.most_common(4)],
            "places": [{"key": p, "count": n} for p, n in places.most_common(5)],
            "enough": enough,
            "distinctive": lift_rows(member_tags, other_tags, tag_label) if enough else [],
            "common": top_rows(member_tags, tag_label) if enough else [],
            "examples": examples,
            "top_records": [{"id": d, "n": n} for d, n in records.most_common(6)],
        })
    overlap = {f"{a}+{b}": sum(1 for x in accounts if {a, b} <= x["kinds"])
               for a in KINDS for b in KINDS if a < b}
    return {"kinds": kinds, "accounts": len(accounts), "overlap": overlap, "min_group": MIN_GROUP,
            "any": sum(1 for a in accounts if a["kinds"])}
