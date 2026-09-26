"""Redaction: what the release withheld, and from which kinds of record.

Two measures, because they cover different parts of the archive:

* the publisher's own **redaction flag** (a column of the PURSUE index),
  which mostly marks the modern Department of War mission reports and videos;
* **markers left in the text**: FOIA exemption codes stamped where text was
  removed ("(b)(1)" national security, "(b)(6)" personal privacy, "(b)(7)(C)"
  law-enforcement privacy...) and blacked-out blocks that OCR reads as runs of
  block characters. These catch the historical FBI and Air Force files, which
  the flag leaves out.

The summary shows how the two rates vary by agency, decade, kind of record,
topic, shape and so on, which exemptions each agency cites, and what the
heavily redacted records have in common.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict

from ..classify.taxonomy import label as facet_label
from .compare import MIN_GROUP, decade_of, lift_rows, split_by
from .events import tag_label

EXEMPTIONS = {
    "b1": "National security · (b)(1)",
    "b2": "Internal agency rules · (b)(2)",
    "b3": "Withheld by statute · (b)(3)",
    "b4": "Trade secrets · (b)(4)",
    "b5": "Deliberative process · (b)(5)",
    "b6": "Personal privacy · (b)(6)",
    "b7": "Law enforcement · (b)(7)",
    "block": "Blacked-out block",
}
# how many markers per page make a record "heavily redacted"
HEAVY_PER_PAGE = 2.0

# "(b)(7)(C)" — but not "paragraph (b)(1) of this clause" in a contract
_FULL = re.compile(r"\(\s?b\s?\)\s?\(\s?([1-9])\s?\)(?:\s?\(\s?[A-Fa-f]\s?\))?", re.I)
_CITATION_BEFORE = re.compile(r"(?:paragraphs?|sections?|subsections?|clauses?|subparagraphs?|subdivisions?|"
                              r"items?|parts?)\s*[\w.\-]*\s*(?:\(\w+\)\s*)*$|[A-Za-z0-9]$", re.I)
_CITATION_AFTER = re.compile(r"^\s*(?:of|in|under|to)\s+(?:this|the|such|these|those|section|paragraph)\b", re.I)
# the short stamps FBI files use in the margin: "b6", "b7C"
_SHORT = re.compile(r"(?<![A-Za-z0-9])b([1-7])(?:\s?([A-Fa-f]))?(?![A-Za-z0-9])")
_BLOCK = re.compile(r"[█▇▆■▪]{2,}|X{5,}|\[(?:redacted|deleted|withheld|excised)\]|\((?:redacted|deleted)\)", re.I)


def find_redactions(text: str) -> list[str]:
    """Exemption codes and blacked-out blocks on one page, one entry per hit
    (``b1``…``b7`` or ``block``)."""
    text = text or ""
    hits: list[str] = []
    for m in _FULL.finditer(text):
        before = text[max(0, m.start() - 40):m.start()]
        after = text[m.end():m.end() + 30]
        if _CITATION_BEFORE.search(before) or _CITATION_AFTER.match(after):
            continue
        hits.append("b" + m.group(1))
    # bare "b6" stamps are only trusted where a page has several of them, or
    # a full-form code as well: a lone one is more likely a form field
    short = ["b" + m.group(1) for m in _SHORT.finditer(text)]
    if short and (hits or len(short) >= 2):
        hits += short
    hits += ["block"] * len(_BLOCK.findall(text))
    return hits


def exemption_label(code: str) -> str:
    return EXEMPTIONS.get(code, code)


# ---------------------------------------------------------------------------

FACETS = [
    ("agency", "Agency"), ("decade", "Incident decade"), ("kind", "Document type"), ("media", "Format"),
    ("topic", "Topic"), ("shape", "Reported shape"), ("sensor", "Evidence / sensor"), ("witness", "Witness"),
    ("domain", "Domain"), ("region", "Region"), ("assessment", "Official assessment"),
]


def record_facets(rec: dict) -> list[tuple[str, str]]:
    """(facet, value) pairs a record falls under, for the split tables and
    the common-threads comparison. Free-text facets (agency) keep their value."""
    out = []
    if rec.get("agency"):
        out.append(("agency", rec["agency"]))
    dec = decade_of(rec.get("year"))
    if dec:
        out.append(("decade", dec))
    if rec.get("media"):
        out.append(("media", rec["media"]))
    for tag in rec.get("tags", ()):
        facet, _, value = tag.partition(":")
        if facet in ("kind", "topic", "shape", "sensor", "witness", "domain", "region", "assessment"):
            out.append((facet, value))
    return out


def facet_value_label(facet: str, value: str) -> str:
    if facet in ("agency", "decade"):
        return value
    if facet == "media":
        return {"pdf": "PDF", "video": "Video", "image": "Image", "audio": "Audio"}.get(value, value)
    return facet_label(facet, value)


def redaction_summary(records: list[dict]) -> dict:
    """``records``: one dict per classified record with keys id, record_id,
    title, agency, media, year, redacted (publisher flag), pages (text pages),
    tags (set of "facet:value"), items (set of detail keys reported) and
    markers (Counter of exemption codes found in its text)."""
    for r in records:
        n = sum(r["markers"].values())
        r["marker_count"] = n
        r["density"] = round(n / r["pages"], 2) if r["pages"] else 0.0
        r["marked"] = n > 0
        r["heavy"] = bool(r["pages"]) and r["density"] >= HEAVY_PER_PAGE
        r["any"] = bool(r["redacted"]) or r["marked"]
    with_text = [r for r in records if r["pages"]]
    codes = Counter()
    code_records = Counter()
    for r in records:
        codes.update(r["markers"])
        code_records.update(set(r["markers"]))

    groups = ["heavy", "marked_light", "flag_only", "none"]

    def tier(r):
        if r["heavy"]:
            return "heavy"
        if r["marked"]:
            return "marked_light"
        if r["redacted"]:
            return "flag_only"
        return "none"

    facets_of = {r["id"]: record_facets(r) for r in records}
    by = []
    for facet, flabel in FACETS:
        members = [(r, v) for r in records for f, v in facets_of[r["id"]] if f == facet]
        rows = split_by(((tier(r), v) for r, v in members), groups)
        rows = [row for row in rows if row["total"] >= 5]  # a share of 2 records says nothing
        flagged_by_value = Counter(v for r, v in members if r["redacted"])
        for row in rows:
            row["label"] = facet_value_label(facet, row["value"])
            n_any = row["total"] - row["counts"]["none"]
            row["any"] = n_any
            row["any_share"] = round(n_any / row["total"], 3)
            row["flag_share"] = round(flagged_by_value[row["value"]] / row["total"], 3)
            row["link"] = None if facet in ("agency", "decade", "media") else f"{facet}:{row['value']}"
        rows.sort(key=lambda r: (-r["any_share"], -r["total"]))
        if len(rows) >= 2:
            by.append({"facet": facet, "label": flabel, "rows": rows[:12]})

    # which exemptions each agency cites (why text was withheld)
    agency_codes: dict[str, Counter] = defaultdict(Counter)
    for r in records:
        if r["agency"] and r["markers"]:
            agency_codes[r["agency"]].update(r["markers"])
    exemptions_by_agency = [
        {"agency": a, "total": sum(c.values()),
         "codes": [{"key": k, "label": exemption_label(k), "count": n} for k, n in c.most_common()]}
        for a, c in sorted(agency_codes.items(), key=lambda kv: -sum(kv[1].values()))
    ]

    # common threads: what the redacted records are about, compared with everything
    def facet_items(r):
        return {f"{f}:{v}" for f, v in facets_of[r["id"]] if f != "assessment"}

    def item_label(k):
        f, _, v = k.partition(":")
        return f"{facet_value_label(f, v)}" + ("" if f in ("agency", "decade", "media") else f" ({dict(FACETS)[f].lower()})")

    heavy = [r for r in records if r["heavy"]]
    not_heavy = [r for r in with_text if not r["heavy"]]
    flagged = [r for r in records if r["redacted"]]
    not_flagged = [r for r in records if not r["redacted"]]
    threads = {
        "heavy": {
            "n": len(heavy), "of": len(with_text),
            "facets": lift_rows((facet_items(r) for r in heavy), (facet_items(r) for r in not_heavy), item_label),
            "details": lift_rows((r["items"] for r in heavy if r["items"]), (r["items"] for r in not_heavy if r["items"]),
                                 tag_label),
        },
        "flagged": {
            "n": len(flagged), "of": len(records),
            "facets": lift_rows((facet_items(r) for r in flagged), (facet_items(r) for r in not_flagged), item_label),
            "details": lift_rows((r["items"] for r in flagged if r["items"]), (r["items"] for r in not_flagged if r["items"]),
                                 tag_label),
        },
    }
    top = sorted(heavy, key=lambda r: (-r["density"], -r["marker_count"]))[:12]
    return {
        "overview": {
            "records": len(records), "flagged": len(flagged), "with_text": len(with_text),
            "marked": sum(1 for r in with_text if r["marked"]), "heavy": len(heavy),
            "flagged_without_markers": sum(1 for r in with_text if r["redacted"] and not r["marked"]),
            "marked_without_flag": sum(1 for r in with_text if r["marked"] and not r["redacted"]),
            "markers": sum(codes.values()),
        },
        "codes": [{"key": k, "label": exemption_label(k), "count": codes[k], "records": code_records[k]}
                  for k in EXEMPTIONS if codes[k]],
        "by": by,
        "exemptions_by_agency": exemptions_by_agency,
        "threads": threads,
        "top": [{"id": r["id"], "record_id": r["record_id"], "title": r["title"], "agency": r["agency"],
                 "year": r["year"], "pages": r["pages"], "markers": r["marker_count"], "density": r["density"],
                 "codes": [{"key": k, "label": exemption_label(k), "count": n} for k, n in r["markers"].most_common(3)]}
                for r in top],
        "min_group": MIN_GROUP,
    }
