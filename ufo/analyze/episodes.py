"""Episodes: what happens in each record, one or two sentences at a time.

A record can describe one sighting or dozens (an FBI case-file section lists
every report that reached a field office that month). This stage cuts each
record into *episodes* — one per distinct event — and gives each a short
summary, an event class, a list of details, the year and place when the text
states them, and the outcome the record reports.

Where an episode comes from, in order of precedence:

1. **Curated** (``data/episodes/curated.json.gz``): every record in the
   bundled snapshot was read and classified by hand (well, by Claude in an
   editing session), not by keyword rules. Keyed by record id.
2. **LLM** (``llm_episodes``): when an API key is configured, records with no
   curated entry (new releases) are read by the model, with the same guide
   and vocabulary. Results are cached by text and model.
3. **Rules**: without either, each tagged sighting account becomes an episode
   with a summary written from its event tags, and a record with no accounts
   gets one episode from the publisher's description.

The hierarchy shown on the Events page is derived from the class and the
details in code (``subcategory_for``), so it can be re-tuned without
re-reading the files.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, selectinload

from ..db import Account, Document, Episode, Mention, has_classification
from .events import BY_TAG, DIMENSIONS, dim_of
from .extract import NON_SIGHTING_KINDS, units
from .features import is_sighting_text

log = logging.getLogger(__name__)

TAXONOMY_VERSION = 1
CURATED_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "episodes" / "curated.json.gz"
MAX_PAGE_CHARS = 6000  # per page, when a record is prepared for reading
MAX_SUMMARY = 600

# ---------------------------------------------------------------------------
# Taxonomy

# event class -> (label, description)
EVENTS: dict[str, tuple[str, str]] = {
    "sighting": ("Object or light seen in the sky",
                 "witnesses saw an object or light with their own eyes"),
    "sensor": ("Detected by instruments",
               "the evidence is a radar track, infrared or video footage, a photograph, sonar or another "
               "instrument, rather than an eyewitness account"),
    "encounter": ("Close encounter or physical effects",
                  "the object came close, landed, interfered with engines, electrics or instruments, affected "
                  "witnesses or animals, or occupants were seen"),
    "traces": ("Physical evidence found",
               "something was left behind or recovered: markings on the ground, debris, fragments, wreckage"),
    "space": ("Observed from space or in orbit",
              "seen by astronauts, in orbit, near a spacecraft or on the Moon"),
    "none": ("No specific event",
             "the passage is about UFOs without reporting one observation: policy, analysis, public "
             "letters, contact claims"),
}

# extra details beyond the event tags: the setting, the witnesses, what was
# found, and what a non-event passage is about
CONTEXT: dict[str, tuple[str, str]] = {
    "over_water": ("Over water", "over the sea, a lake or a river"),
    "intercept": ("Aircraft scrambled / pursued", "military aircraft were sent up after it or chased it"),
    "close_range": ("Within 150 m", "the object came within about 150 metres (500 feet) of the witness"),
    "landed": ("Landed / on the ground", "the object landed, sat on the ground or touched down"),
    "occupants": ("Occupants / beings seen", "beings or occupants were seen with the object"),
    "underwater": ("Under the water", "the object was seen or tracked below the surface"),
    "sonar": ("Sonar", "detected by sonar"),
    "astronaut": ("Seen by an astronaut", "an astronaut or cosmonaut observed it from a spacecraft"),
    "orbit": ("In orbit / near a spacecraft", "the object was in Earth orbit, near a spacecraft or satellite, "
                                             "or on the Moon"),
    "military_base": ("Near a military site", "at or near a military base, airfield, ship or exercise area"),
    "nuclear_site": ("Near a nuclear site", "near a nuclear weapons, energy or research facility"),
    "drone_like": ("Drone-like", "described as a drone, UAS or swarm"),
    "crash": ("Crash / wreckage", "the object crashed, or wreckage was claimed or recovered"),
    "debris": ("Debris / fragments", "fragments or material were recovered or analysed"),
    "military": ("Military witnesses", "the witnesses were service members on duty"),
    "police": ("Police witnesses", "the witnesses included police or other law enforcement"),
    "scientist": ("Scientist witnesses", "the witnesses included scientists, engineers or astronomers"),
    "policy": ("Policy / procedure", "official policy, reporting procedures, program administration, briefings"),
    "study": ("Analysis / study", "scientific, statistical or technical analysis of reports"),
    "inquiry": ("Public letter / request", "letters, requests or inquiries from the public or press"),
    "claims": ("Contact / conspiracy claims", "claims of contact, abduction, telepathy, secret knowledge"),
}
CONTEXT_DIM = "context"

OUTCOMES: dict[str, str] = {
    "not_stated": "No outcome given",
    "unexplained": "Unexplained / unidentified",
    "identified": "Identified / explained",
    "hoax": "Hoax or doubtful",
}


@dataclass(frozen=True)
class Sub:
    key: str
    label: str
    description: str
    any: frozenset = frozenset()
    exclude: frozenset = frozenset()


def _s(key, label, description, any_=(), exclude=()):
    return Sub(key, label, description, frozenset(any_), frozenset(exclude))


STRUCTURED = ("disc", "cigar", "oval", "triangle", "chevron", "diamond", "sphere", "dome", "windows", "rim",
              "metallic", "no_wings")

# subcategories per class, in priority order: the first whose details match wins
SUBCATS: dict[str, list[Sub]] = {
    "sighting": [
        _s("radar_visual", "Seen and tracked on radar", "an eyewitness sighting confirmed by radar", ("radar",)),
        _s("water", "Over water or from a ship", "over the sea, a lake or a river, or seen from a vessel",
           ("over_water", "at_sea", "transmedium", "underwater")),
        _s("aircrew", "Seen from an aircraft", "seen by pilots or aircrew in flight, or chased by aircraft",
           ("pilot", "intercept")),
        _s("formation", "Several objects or a formation", "two or more objects, often in formation",
           ("formation", "pair")),
        _s("fireball", "Fireball or meteor-like", "a fireball, a streak or a meteor-like body", ("fireball",)),
        _s("structured", "Structured object", "a disc, cigar, sphere or other body with a definite shape",
           STRUCTURED),
        _s("maneuvers", "Hovering, speed or manoeuvres",
           "hovering, extreme speed, sudden acceleration, sharp turns, splitting or vanishing",
           ("accelerate", "maneuver", "erratic", "split_merge", "vanish", "hover", "high_speed", "climb", "circling",
            "oscillate", "rotate")),
        _s("night_lights", "Lights at night", "lights seen at night or at dusk with no visible structure",
           ("night", "twilight", "starlike", "flashing", "glow")),
        _s("other", "Other sightings", "sightings that say little about the object"),
    ],
    "sensor": [
        _s("infrared", "Infrared or targeting-pod video", "recorded by an infrared or electro-optical sensor",
           ("infrared",)),
        _s("radar", "Radar track", "a radar return with no eyewitness account", ("radar",)),
        _s("sonar", "Sonar or underwater detection", "detected by sonar or below the surface", ("sonar", "underwater")),
        _s("photo", "Photograph or film", "an object noticed in a photograph or on film", ("photo",)),
        _s("instrument", "Telescope, theodolite or other instrument", "seen through a tracking instrument",
           ("instrument_obs",)),
        _s("other", "Other detections", "detected by some other means"),
    ],
    "encounter": [
        _s("occupants", "Occupants or beings seen", "beings or occupants were seen with the object", ("occupants",)),
        _s("interference", "Engine, electrical or instrument interference",
           "engines stalled, lights or radios failed, compasses or instruments were disturbed",
           ("em_effects", "instruments")),
        _s("physiological", "Effects on witnesses", "burns, heat, illness or other effects on people",
           ("physiological", "heat")),
        _s("animals", "Animals reacted", "dogs, cattle or other animals reacted", ("animals",)),
        _s("landed", "Landed or on the ground", "the object landed, touched down or sat on the ground",
           ("landed", "descend")),
        _s("pursuit", "Paced or chased a vehicle", "the object followed or paced a car, ship or aircraft",
           ("pacing",)),
        _s("close_range", "At close range", "within about 150 metres of the witness", ("close_range",)),
        _s("other", "Other close encounters", "close encounters of another kind"),
    ],
    "traces": [
        _s("crash", "Crash or wreckage", "a crash, or wreckage claimed or recovered", ("crash",)),
        _s("debris", "Fragments or material recovered", "fragments or material analysed", ("debris",)),
        _s("markings", "Markings or traces on the ground", "scorched, flattened or marked ground", ("traces",)),
        _s("found", "Object found on the ground", "an object that landed or was found, and what it proved to be",
           ("landed", "descend")),
        _s("other", "Other physical evidence", "physical evidence of another kind"),
    ],
    "space": [
        _s("astronaut", "Seen by an astronaut", "observed by a crew in flight", ("astronaut",)),
        _s("photo", "Anomaly in a space photograph", "an object in orbital or lunar imagery", ("photo",)),
        _s("tracked", "Tracked in orbit", "an object tracked by radar or telescope in orbit", ("radar", "orbit")),
        _s("other", "Other space observations", "space observations of another kind"),
    ],
    "none": [
        _s("policy", "Policy, procedure and programs", "how reports are handled, program administration",
           ("policy",)),
        _s("study", "Analysis and studies", "scientific, statistical or technical analysis", ("study",)),
        _s("inquiry", "Public letters and requests", "letters, requests and inquiries from the public or press",
           ("inquiry",)),
        _s("claims", "Contact and conspiracy claims", "claims of contact, abduction or secret knowledge",
           ("claims",)),
        _s("other", "Other discussion", "general discussion of the subject"),
    ],
}
SUB_BY_KEY = {(e, s.key): s for e, subs in SUBCATS.items() for s in subs}


def detail_label(key: str) -> str:
    if key in CONTEXT:
        return CONTEXT[key][0]
    return BY_TAG[key].label if key in BY_TAG else key


def detail_dim(key: str) -> str:
    return CONTEXT_DIM if key in CONTEXT else dim_of(key)


def all_details() -> list[str]:
    return [t.key for t in BY_TAG.values()] + list(CONTEXT)


VALID_DETAILS = frozenset(all_details())


def subcategory_for(event: str, details) -> str:
    """The subcategory an episode falls in: the first in its class whose
    details it has."""
    d = set(details)
    for sub in SUBCATS.get(event, ()):
        if not sub.any:
            return sub.key
        if d & sub.any and not d & sub.exclude:
            return sub.key
    return "other"


def event_label(event: str) -> str:
    return EVENTS[event][0] if event in EVENTS else event


def sub_label(event: str, sub: str) -> str:
    s = SUB_BY_KEY.get((event, sub))
    return s.label if s else sub


# ---------------------------------------------------------------------------
# The guide: the same text steers the LLM and a person (or Claude) reading a batch

def guide_text() -> str:
    lines = ["EVENT CLASSES (choose exactly one per episode):"]
    for k, (label, desc) in EVENTS.items():
        lines.append(f"- {k}: {label} — {desc}")
    lines.append("")
    lines.append("DETAILS (list every one the passage reports; codes only):")
    for dim, (label, _) in DIMENSIONS.items():
        tags = ", ".join(f"{t.key} ({t.label})" for t in BY_TAG.values() if t.dim == dim)
        lines.append(f"- {label}: {tags}")
    ctx = ", ".join(f"{k} ({v[0]})" for k, v in CONTEXT.items())
    lines.append(f"- Context: {ctx}")
    lines.append("")
    lines.append("OUTCOME (one): " + ", ".join(f"{k} ({v})" for k, v in OUTCOMES.items()))
    lines.append("")
    lines.append("How the subcategories are derived from the details (first match in each class wins):")
    for e, subs in SUBCATS.items():
        for s in subs:
            if s.any:
                lines.append(f"- {e}/{s.key} ({s.label}): any of {', '.join(sorted(s.any))}")
            else:
                lines.append(f"- {e}/{s.key} ({s.label}): none of the above")
    return "\n".join(lines)


RULES = """Rules:
- One episode per distinct event. A record that describes several sightings gets several episodes; a report that runs over several pages is one episode listing all of its pages. A record whose text reports no event at all gets one episode of class "none".
- pages: the page numbers the episode is read from (0 = the publisher's description). Use the numbers given, never invent one.
- summary: one or two plain sentences (at most 60 words) saying what happened: who saw what, where and when if stated, how it behaved, and how it ended. Write it from the text; do not copy the publisher's boilerplate. Say "reportedly" where the record is a second-hand claim.
- event: the class. "sighting" needs an eyewitness who saw the object; "sensor" when the evidence is instrument data (radar scope, infrared or video display, photograph, sonar) and any people only watched a display; "encounter" when the object came within about 150 m, landed, interfered with engines, electrics or instruments, affected people or animals, paced a vehicle, or occupants were seen; "traces" when the passage is about what was found or recovered; "space" for astronaut, orbital and lunar observations; "none" when no specific observation is reported (policy, analysis, letters from the public, contact claims, hoax confessions with no sighting).
- details: every detail the text reports, from the list. Include the context codes (over_water, pilot, radar, military, police, landed, occupants, crash, debris ...) whenever they apply: they decide the subcategory. Do not tag a detail the text denies ("no sound" is silent, not hum), a printed form label, or an explanation ("probably a balloon" is not a balloon shape).
- year: the year of the event as the text or the record metadata gives it, else null. place: a short place ("Roswell, New Mexico", "off Okinawa", "Los Alamos"), else null.
- outcome: what the record says became of the case: identified (explained as balloon, aircraft, star, meteor, satellite, hoax ruled out ...), unexplained (listed as unidentified or unresolved), hoax (admitted or judged a hoax or doubtful), else not_stated. explanation: the explanation in a few words when identified (e.g. "weather balloon"), else null.
- Skip passages that are a duplicate copy of an episode already listed for the same record (carbon copies of the same memo): add their pages to that episode instead."""


# ---------------------------------------------------------------------------
# Data model

@dataclass
class Ep:
    pages: list[int]
    summary: str
    event: str
    details: list[str] = field(default_factory=list)
    year: int | None = None
    place: str | None = None
    outcome: str = "not_stated"
    explanation: str | None = None

    def to_dict(self) -> dict:
        return {"pages": self.pages, "summary": self.summary, "event": self.event, "details": self.details,
                "year": self.year, "place": self.place, "outcome": self.outcome, "explanation": self.explanation}


def clean_episode(raw: dict, valid_pages: set[int] | None = None) -> Ep | None:
    """Validate a raw episode (from the curated file or a model): unknown
    codes are dropped, an unknown class or an empty summary rejects it."""
    event = str(raw.get("event") or "").strip()
    if event not in EVENTS:
        return None
    summary = re.sub(r"\s+", " ", str(raw.get("summary") or "")).strip()
    if not summary:
        return None
    if len(summary) > MAX_SUMMARY:
        summary = summary[:MAX_SUMMARY].rsplit(" ", 1)[0] + "…"
    pages = []
    for p in raw.get("pages") or []:
        try:
            p = int(p)
        except (TypeError, ValueError):
            continue
        if p >= 0 and (valid_pages is None or p in valid_pages) and p not in pages:
            pages.append(p)
    details = []
    for d in raw.get("details") or []:
        d = str(d).strip()
        if d in VALID_DETAILS and d not in details:
            details.append(d)
    year = raw.get("year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    if year is not None and not 1800 <= year <= date.today().year + 1:
        year = None
    place = (str(raw.get("place")).strip()[:120] or None) if raw.get("place") else None
    outcome = str(raw.get("outcome") or "not_stated")
    if outcome not in OUTCOMES:
        outcome = "not_stated"
    expl = (str(raw.get("explanation")).strip()[:80] or None) if raw.get("explanation") else None
    return Ep(sorted(pages), summary, event, details, year, place, outcome, expl)


# ---------------------------------------------------------------------------
# What a reader (curated or LLM) is given

def sighting_units(doc: Document) -> list[tuple[int, str]]:
    """The passages worth reading: the publisher's description (page 0) and
    every page that reads like a sighting report, in page order."""
    out = []
    for page_no, text in units(doc):
        if page_no == 0 or is_sighting_text(text):
            out.append((page_no, text))
    out.sort(key=lambda u: u[0])
    return out


def text_hash(us: list[tuple[int, str]]) -> str:
    h = hashlib.sha256()
    for p, t in us:
        h.update(f"{p}\n{t}\n".encode())
    return h.hexdigest()[:16]


def reading_input(doc: Document, us: list[tuple[int, str]], max_page_chars: int = MAX_PAGE_CHARS) -> dict:
    """A record as a reader sees it: metadata plus its sighting passages, each
    with the keyword hints the rules found (which can be wrong)."""
    from .events import tag_account

    pages = []
    for p, t in us:
        t = t if len(t) <= max_page_chars else t[:max_page_chars] + " […]"
        hints = tag_account(t)[0] if p else []
        pages.append({"page": p, "text": t, "hints": hints})
    return {
        "record_id": doc.record_id, "title": doc.title, "agency": doc.agency, "media": doc.media_type,
        "kind": doc.document_kind, "incident_date": doc.incident_date_raw, "incident_year": doc.incident_year,
        "incident_location": doc.incident_location, "pages_in_file": doc.page_count,
        "description": doc.description, "text_hash": text_hash(us), "pages": pages,
    }


# ---------------------------------------------------------------------------
# Curated episodes

_curated: dict | None = None


def load_curated(path: Path | None = None) -> dict[str, dict]:
    """record id -> {"text_hash", "episodes": [raw dicts]}."""
    global _curated
    path = path or CURATED_PATH
    if _curated is None:
        _curated = {}
        if path.exists():
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                data = json.load(fh)
            _curated = data.get("records", {})
            log.info("loaded curated episodes for %d records", len(_curated))
    return _curated


def reset_curated() -> None:
    global _curated
    _curated = None


def save_curated(records: dict[str, dict], path: Path | None = None) -> None:
    path = path or CURATED_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    out = {"version": 1, "taxonomy_version": TAXONOMY_VERSION, "records": dict(sorted(records.items()))}
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    reset_curated()


# ---------------------------------------------------------------------------
# Rules fallback

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z“\"(])")
_SHAPE_NOUN = {
    "disc": "disc-shaped object", "sphere": "spherical object", "cigar": "cigar-shaped object",
    "oval": "oval object", "triangle": "triangular object", "chevron": "V-shaped object",
    "diamond": "box-like object", "fireball": "fireball", "starlike": "star-like light",
}
_COLOUR_ADJ = {"metallic": "silvery", "white": "white", "red_orange": "red-orange", "yellow": "yellow",
               "green": "green", "blue_white": "bluish", "dark": "dark", "colour_change": "colour-changing"}
_MOTION = {
    "hover": "hovered", "accelerate": "accelerated away suddenly", "high_speed": "moved at extreme speed",
    "maneuver": "made abrupt turns", "erratic": "moved erratically", "oscillate": "wobbled",
    "rotate": "spun", "climb": "climbed vertically", "descend": "descended or landed",
    "pacing": "paced a vehicle", "circling": "circled", "vanish": "vanished abruptly", "drift": "drifted slowly",
    "split_merge": "split or merged", "transmedium": "entered or left the water",
}
_SOUND = {"silent": "made no sound", "hum": "hummed", "whoosh": "made a rushing sound", "roar": "roared",
          "whistle": "whistled", "bang": "was heard as a bang"}
_LIGHT = {"glow": "glowing", "halo": "haloed", "flashing": "flashing", "bright": "very bright", "beam": "beaming light"}
_STRUCT = {"no_wings": "had no wings or exhaust", "trail": "left a trail", "sparks": "threw sparks or flame",
           "dome": "had a dome", "windows": "showed windows or a row of lights", "rim": "had a rim", "large": "was very large"}
_EFFECT = {"em_effects": "engines or electrics failed", "instruments": "instruments were disturbed",
           "physiological": "witnesses felt effects", "traces": "traces were left on the ground",
           "smell": "an odour was noticed", "heat": "heat was felt", "animals": "animals reacted"}
_WHO = [("pilot", "A pilot"), ("at_sea", "A ship's crew"), ("driving", "A driver"),
        ("many_witnesses", "Several witnesses")]
_DURATION = {"brief": "for a few seconds", "minutes": "for some minutes", "long": "for half an hour or more"}


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def rules_summary(tags: list[str]) -> str:
    """A plain sentence or two written from event tags."""
    t = set(tags)
    who = next((w for k, w in _WHO if k in t), "Witnesses")
    count = "two " if "pair" in t else "several " if "formation" in t else ""
    colours = [_COLOUR_ADJ[k] for k in _COLOUR_ADJ if k in t][:2]
    shape = next((_SHAPE_NOUN[k] for k in _SHAPE_NOUN if k in t), None)
    lights = [k for k in _LIGHT if k in t]
    if shape is None:
        shape = "light" if lights or (colours and not t & set(STRUCTURED)) else "object"
    noun = shape + ("s" if count else "")
    if count == "two ":
        noun = noun.replace("ys", "ies")
    what = f"{count}{' '.join(colours + ([] if shape.startswith(('fireball', 'star')) else []))}"
    what = (what + " " + noun).strip() if colours else f"{count}{noun}"
    article = "" if count else ("an " if what[0] in "aeiou" else "a ")
    when = "at night" if "night" in t else "at dusk or dawn" if "twilight" in t else "in daylight" if "daylight" in t else ""
    how = [x for x, k in (("tracked on radar", "radar"), ("on infrared", "infrared"), ("photographed", "photo"),
                          ("through a telescope or theodolite", "instrument_obs")) if k in t]
    s1 = f"{who} saw {article}{what}"
    if when:
        s1 += f" {when}"
    if how:
        s1 += ", " + _join(how)
    s1 += "."
    did = [_LIGHT[k] for k in lights if k != "beam"]
    did = [("were " if count else "was ") + _join(did)] if did else []
    did += [_MOTION[k] for k in _MOTION if k in t][:3]
    did += [_SOUND[k] for k in _SOUND if k in t][:1]
    did += [_STRUCT[k] for k in _STRUCT if k in t][:2]
    eff = [_EFFECT[k] for k in _EFFECT if k in t][:2]
    dur = next((_DURATION[k] for k in _DURATION if k in t), "")
    s2 = ""
    if did:
        s2 = "It " + _join(did) if not count else "They " + _join(did)
        if dur:
            s2 += f", in view {dur}"
        s2 += "."
    if eff:
        s2 += (" " if s2 else "") + _join(eff).capitalize() + "."
    return (s1 + " " + s2).strip()


def rules_event(tags: list[str], media: str | None = None, page_no: int | None = None) -> str:
    t = set(tags)
    if t & {"astronaut", "orbit"}:
        return "space"
    if t & {"crash", "debris", "traces"}:
        return "traces"
    if t & {"em_effects", "instruments", "physiological", "heat", "animals", "landed", "descend", "pacing",
            "occupants", "close_range"}:
        return "encounter"
    if media in ("video", "image", "audio") and page_no == 0 and not t & {"pilot", "many_witnesses", "driving"}:
        return "sensor"
    return "sighting"


_FIRST_SENTENCES = re.compile(r"^(.*?[.!?])(\s+.*?[.!?])?(?=\s|$)", re.S)


def first_sentences(text: str, n: int = 2, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    parts = _SENT.split(text)
    out = " ".join(parts[:n]).strip()
    if len(out) > limit:
        out = out[:limit].rsplit(" ", 1)[0] + "…"
    return out


def rules_episodes(doc: Document, accounts: list) -> list[Ep]:
    """Episodes from the keyword rules: one per tagged sighting account, or
    one from the description when the record has none."""
    out: list[Ep] = []
    for a in accounts:
        tags = list(a.tags or [])
        if not tags:
            continue
        out.append(Ep([a.page_no], rules_summary(tags), rules_event(tags, doc.media_type, a.page_no), tags))
    if out:
        return out
    text = doc.description or doc.summary or ""
    if doc.document_kind in NON_SIGHTING_KINDS or doc.document_kind in ("administrative", "correspondence"):
        detail = {"scientific_study": "study", "analysis": "study", "administrative": "policy",
                  "correspondence": "inquiry"}.get(doc.document_kind or "", "")
        return [Ep([0], first_sentences(text) or f"{doc.title}: no sighting is described.", "none",
                   [detail] if detail else [])]
    if not text:
        return [Ep([], f"{doc.title}: the record's text could not be read.", "none")]
    from .events import tag_account

    tags = tag_account(text)[0]
    event = rules_event(tags, doc.media_type, 0) if tags else ("sensor" if doc.media_type in ("video", "image", "audio") else "none")
    return [Ep([0], first_sentences(text), event, tags)]


# ---------------------------------------------------------------------------
# Building the table

def _page_years(db: Session, doc_ids) -> dict[tuple[int, int], list[int]]:
    out: dict[tuple[int, int], list[int]] = defaultdict(list)
    ids = list(doc_ids)
    for chunk in (ids[i:i + 500] for i in range(0, len(ids), 500)):
        for doc_id, page_no, value in db.execute(select(Mention.document_id, Mention.page_no, Mention.value)
                                                 .where(Mention.kind == "date", Mention.document_id.in_(chunk))):
            out[(doc_id, page_no)].append(int(value[:4]))
    return out


def build_episodes(db: Session, progress=None) -> dict:
    """Rebuild the ``episodes`` table for every classified record. Returns
    counts by source."""
    from .llm_episodes import episodes_for_records, episodes_enabled

    progress = progress or (lambda *a: None)
    docs = db.scalars(select(Document).where(has_classification())
                      .options(selectinload(Document.pages), selectinload(Document.release))).all()
    accounts: dict[int, list] = defaultdict(list)
    for a in db.scalars(select(Account).where(Account.document_id.in_([d.id for d in docs]) if docs else Account.id < 0)
                        .order_by(Account.page_no, Account.seq)):
        accounts[a.document_id].append(a)
    curated = load_curated()
    years = _page_years(db, [d.id for d in docs])
    stats = {"curated": 0, "llm": 0, "rules": 0, "stale": 0}

    chosen: dict[int, tuple[str, list[Ep]]] = {}
    need_llm: list[tuple[Document, list[tuple[int, str]]]] = []
    for doc in docs:
        us = sighting_units(doc)
        valid = {p for p, _ in us} | {p.page_no for p in doc.pages}
        entry = curated.get(doc.record_id)
        if entry:
            eps = [e for e in (clean_episode(raw, valid) for raw in entry.get("episodes", [])) if e]
            if eps:
                if entry.get("text_hash") and entry["text_hash"] != text_hash(us):
                    stats["stale"] += 1
                chosen[doc.id] = ("curated", eps)
                continue
        if episodes_enabled():
            need_llm.append((doc, us))
        else:
            chosen[doc.id] = ("rules", rules_episodes(doc, accounts[doc.id]))
    if need_llm:
        got = episodes_for_records(need_llm, progress)
        for doc, _ in need_llm:
            eps = got.get(doc.id)
            chosen[doc.id] = ("llm", eps) if eps else ("rules", rules_episodes(doc, accounts[doc.id]))

    db.execute(delete(Episode))
    n = 0
    for doc in docs:
        source, eps = chosen[doc.id]
        stats[source] += 1
        for seq, e in enumerate(eps):
            pages = e.pages or [0]
            year = e.year
            if year is None:
                ys = [y for p in pages for y in years.get((doc.id, p), [])]
                year = int(statistics.median_low(ys)) if ys else doc.incident_year
            acc_ids = [a.id for a in accounts[doc.id] if a.page_no in pages]
            db.add(Episode(document_id=doc.id, page_no=pages[0], pages=pages, seq=seq, summary=e.summary,
                           event=e.event, sub=subcategory_for(e.event, e.details), details=e.details, year=year,
                           place=e.place, outcome=e.outcome, explanation=e.explanation, source=source,
                           account_ids=acc_ids))
            n += 1
    stats["episodes"] = n
    stats["records"] = len(docs)
    log.info("episodes: %d from %d records (%d curated, %d LLM, %d rules; %d curated entries from older text)",
             n, len(docs), stats["curated"], stats["llm"], stats["rules"], stats["stale"])
    return stats
