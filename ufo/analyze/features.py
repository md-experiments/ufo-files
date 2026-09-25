"""Observable details ("signatures") reported in sightings.

Each observable is a recurring, concrete detail a witness or sensor can report
(a halo, a humming sound, a sulfur smell, an abrupt right-angle turn, engine
failure...). They are searched page by page, only on pages that read like a
sighting report, and every hit keeps the sentence it came from so people can
check it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# group -> label used in the UI
GROUPS = {
    "light": "Light & appearance",
    "sound": "Sound",
    "smell": "Smell",
    "motion": "Movement",
    "form": "Form & structure",
    "effect": "Physical effects",
    "sensor": "Detection",
}


@dataclass(frozen=True)
class Observable:
    key: str
    label: str
    group: str
    pattern: str
    # pattern that, when found just before a match, cancels it ("no sound")
    negation: str | None = None
    description: str = ""


def _o(key, label, group, pattern, negation=None, description=""):
    return Observable(key, label, group, pattern, negation, description)


OBSERVABLES: list[Observable] = [
    # light & appearance
    _o("halo", "Halo / glow", "light",
       r"halos?|haloes|ring of (?:light|fire)|corona|aura|glow(?:ed|ing|s)?|self[- ]luminous|luminous|incandescent|phosphorescent",
       description="A glow, halo, corona or self-luminous surface around the object."),
    _o("flashing", "Flashing / pulsing light", "light",
       r"flash(?:ing|ed|es)|puls(?:ing|ating|ated)|blink(?:ing|ed)|strob(?:e|ing)|flicker(?:ing|ed)|twinkl(?:ing|ed)",
       description="Light that flashes, pulses, blinks or flickers."),
    _o("red_orange", "Red / orange light", "light",
       r"(?:red|reddish|orange|amber|red-orange|orange-red)(?:[- ](?:colou?red|glow|glowing))?\s+(?:lights?|glow|fire|ball|objects?|orbs?|sphere|disc|disk|color|colou?red)|(?:fiery|flaming) (?:red|orange)|ball of (?:fire|flame)",
       description="A red, orange or fire-coloured light or object."),
    _o("green", "Green light", "light",
       r"green(?:ish)?(?:[- ](?:colou?red|glowing))?\s+(?:lights?|glow|fireballs?|ball|objects?|flash|flare)|green fireballs?",
       description="Green lights or green fireballs (a well-known 1948–51 New Mexico cluster)."),
    _o("blue_white", "Blue-white light", "light",
       r"blue[- ]white|bluish(?:[- ]white)?|blue(?:[- ](?:colou?red|glowing))?\s+(?:lights?|glow|flame|objects?|haze)",
       description="Blue, bluish or blue-white light."),
    _o("metallic", "Metallic / silver surface", "light",
       r"metallic|silver(?:y|ed)?|chrome|polished|aluminum|aluminium|shiny|glint(?:ed|ing)?|reflect(?:ed|ing) (?:the )?sun(?:light)?",
       description="A metallic, silvery or sun-glinting surface."),
    # sound
    _o("hum", "Humming / buzzing", "sound",
       r"humm?(?:ing|ed)?(?: sound| noise)?|buzz(?:ing|ed)?|whirr?(?:ing)?|whin(?:e|ing)|droning|whistl(?:e|ing)|swish(?:ing)?|throbbing|rumbl(?:e|ing)|roar(?:ing)?",
       negation=r"\b(?:no|not|without|never|nor|any)\b[^.;]{0,25}$",
       description="A hum, buzz, whirr, whistle or roar (excluding 'no sound' statements)."),
    _o("silent", "Silent", "sound",
       r"no (?:audible |associated |apparent )?(?:sound|noise)s?|silent(?:ly)?|noiseless(?:ly)?|without (?:any |a )?(?:sound|noise)|made no (?:sound|noise)|not (?:make|making|made) (?:any )?(?:sound|noise)|soundless(?:ly)?|could not hear|no audible",
       description="Explicitly reported as making no sound."),
    # smell
    _o("smell", "Odour / smell", "smell",
       r"(?:strong|pungent|acrid|peculiar|strange|unusual|burning|sulph?ur(?:ous)?|foul|chemical|metallic|sweet|rotten|electrical) (?:odou?r|smell)s?|odou?r ?(?:of|like|similar to|resembling) ?\w+|smell(?:ed|s)? (?:of|like|something)|(?:sulph?ur|ozone|brimstone)(?:[- ]like)? (?:smell|odou?r|fumes)|stench|smelled",
       negation=r"\b(?:no|not|without|nor)\b[^.;]{0,25}$",
       description="A smell reported by witnesses: sulfur, ozone, burning, or unidentified odours."),
    # motion
    _o("hover", "Hovering / stationary", "motion",
       r"hover(?:ed|ing|s)?|stationary|motionless|stood still|remained still|hung (?:in the sky|motionless)|suspended in",
       description="Hovering or holding a fixed position."),
    _o("high_speed", "Extreme speed", "motion",
       r"(?:tremendous|terrific|incredible|fantastic|enormous|extreme(?:ly)? high|very high|great|high|terrible) (?:rate of )?speeds?|(?:very|extremely) fast|faster than (?:any|a jet|conventional)|streaked (?:across|away|off)|shot (?:off|away|straight up)|disappeared (?:at|with) (?:great|high|tremendous) speed",
       description="Speed described as tremendous, beyond aircraft, or streaking away."),
    _o("maneuver", "Abrupt manoeuvres", "motion",
       r"right[- ]angle turns?|90[- ]degree turns?|zig[- ]?zag(?:ged|ging|s)?|abrupt(?:ly)? (?:turn|change|stop|reversal|accelerat)|sudden(?:ly)? (?:accelerat|reversed|changed direction|stopped)|instant(?:ly|aneous(?:ly)?) (?:accelerat|stop|reverse|chang)|erratic(?:ally)? (?:movement|motion|manner|flight|path)|reversed (?:its )?(?:direction|course)",
       description="Right-angle turns, zig-zags, instant stops or accelerations."),
    _o("split_merge", "Splitting / launching objects", "motion",
       r"split (?:into|in two|apart)|separated into|broke (?:up )?into (?:two|three|several)|merged|joined (?:together|by another)|launch(?:ed|ing) (?:other|smaller|additional) (?:objects?|orbs?)|smaller objects? (?:emerged|came out|left)|(?:orbs?|objects?) (?:emerged|came) (?:out )?from",
       description="One object splitting, merging, or releasing smaller objects."),
    _o("transmedium", "Enters / leaves water", "motion",
       r"entered the (?:water|sea|ocean)|(?:went|dove|dived|plunged|disappeared) into the (?:water|sea|ocean)|emerged from the (?:water|sea|ocean)|submerged|beneath the (?:surface|waves)|transmedium|trans-medium",
       description="Transmedium behaviour: going into or coming out of water."),
    # form & structure
    _o("no_wings", "No wings / exhaust", "form",
       r"no (?:visible )?(?:wings?|tail|exhaust|contrails?|vapou?r trails?|propellers?|control surfaces|means of (?:propulsion|lift)|rotors?|engines? (?:visible|seen))|without (?:wings|a tail|any visible means)|no (?:apparent|discernible) (?:means of )?propulsion|wingless",
       description="No wings, tail, exhaust or visible propulsion noted."),
    _o("trail", "Trail / vapour", "form",
       r"(?:vapou?r|exhaust|smoke|fiery|luminous|glowing|light) trails?|left a trail|trail(?:ing)? (?:of|behind)|contrails?",
       negation=r"\b(?:no|not|without|nor)\b[^.;]{0,25}$",
       description="A visible trail, vapour or exhaust (excluding 'no trail')."),
    _o("formation", "Formation / multiple objects", "form",
       r"(?:in|flying in|a) (?:tight |loose |v[- ]?|diamond |triangular )?formation|(?:two|three|four|five|six|seven|eight|nine|ten|several|multiple|\d{1,2}) (?:similar |identical |bright |round |white )?(?:objects|discs|disks|lights|orbs|craft|saucers)",
       description="Several objects together or flying in formation."),
    _o("large", "Very large object", "form",
       r"(?:size|large|big) (?:of|as) (?:a )?(?:football field|house|aircraft carrier|b-?29|airliner|dc-?\d|barn)|(?:enormous|huge|gigantic|massive) (?:object|craft|disc|disk|ship|light)|several hundred feet (?:long|in diameter|across)",
       description="Size compared to a house, airliner or football field, or described as huge."),
    # effects
    _o("em_effects", "Electrical / engine interference", "effect",
       r"(?:car|truck|vehicle|automobile|motor|tractor|bus)(?:'s)? (?:engine |motor )?(?:stalled|stopped|died|failed|quit|cut out|went dead)|(?:engine|motor|ignition) of (?:his|her|their|the) (?:car|truck|vehicle|automobile) (?:stalled|stopped|died|failed|quit)|stall(?:ed|ing) (?:the |his |her )?(?:car|engine|motor|vehicle)|(?:radio|electrical|electronic) (?:interference|failure|static|malfunction|went dead)|(?:head)?lights? (?:went out|failed|dimmed)|power (?:failure|outage|went out)|electromagnetic (?:effects?|interference)|\bEME\b",
       negation=r"\b(?:no|not|without|never|nor)\b[^.;]{0,25}$",
       description="Engines stalling, radios failing, lights dimming or power outages."),
    _o("instruments", "Instrument / compass anomalies", "effect",
       r"compass(?:es)? (?:needle )?(?:spun|swung|deviat|went wild|fluctuat|anomal)|magnetic (?:disturbance|anomal(?:y|ies))|instruments? (?:malfunction|failed|went (?:wild|haywire)|anomal)|geiger (?:counter )?(?:reading|registered|showed)|radiation (?:readings?|levels?) (?:were|was|increased)",
       description="Compass deviation, magnetic disturbances, instrument malfunctions or radiation readings."),
    _o("physiological", "Effects on witnesses", "effect",
       r"(?:witness|he|she|they|pilot|observer)s? (?:felt|experienced|suffered|became) (?:\w+ ){0,2}(?:ill|sick|nause|dizz|paraly|numb|burn|warm|heat|tingl)|(?:burns|blisters|rash|sunburn|redness) (?:on|to) (?:his|her|their|the) (?:face|skin|arms?|hands?|eyes?)|radiation sickness|temporar(?:y|ily) paraly[sz]|conjunctivitis|eye (?:irritation|damage)|nausea",
       description="Burns, nausea, paralysis or other effects on witnesses."),
    _o("traces", "Ground traces / landing", "effect",
       r"ground traces?|landing (?:site|marks?|traces?|gear marks)|(?:scorch|burn)(?:ed)? (?:marks?|area|grass|ground|circle)|(?:circular|crushed|flattened|burned) (?:area|imprints?|depressions?|vegetation|grass)|imprints? in the (?:ground|soil)|landed (?:in|on) (?:a|the) (?:field|ground|road|pasture)",
       description="Landing marks, burned or flattened vegetation, imprints in the ground."),
    # detection
    _o("radar", "Radar contact", "sensor",
       r"radar (?:contacts?|returns?|tracks?|tracked|lock(?:ed)?|confirmed|picked up|showed|painted|targets?|sighting|echo(?:es)?|blips?)|(?:tracked|picked up|detected|painted|observed) (?:\w+ )?(?:by|on) (?:ground |airborne |ship'?s? )?radar|radar[- ]visual",
       description="Detected or tracked on radar."),
    _o("infrared", "Infrared / FLIR", "sensor",
       r"infra-?red|\bflir\b|\bir\b (?:sensor|camera|imagery|video|signature)|thermal (?:imag(?:e|ing|er|ery)|sensor|camera|signature)|mid-?wave|\bmwir\b|\blwir\b",
       description="Seen on infrared / thermal sensors (typical of modern military reports)."),
]

BY_KEY = {o.key: o for o in OBSERVABLES}

_COMPILED = [
    (o, re.compile(rf"(?<![A-Za-z])(?:{o.pattern})(?![A-Za-z])", re.IGNORECASE),
     re.compile(o.negation, re.IGNORECASE) if o.negation else None)
    for o in OBSERVABLES
]

# A page "reads like a sighting" when it uses enough of this vocabulary.
_SIGHTING = re.compile(
    r"(?<![A-Za-z])(?:sight(?:ed|ing|ings)|observ(?:ed|ation|er|ers)|witness(?:es|ed)?|saw|seen|appeared|disappeared|"
    r"object|objects|lights?|in the sky|flying (?:disc|disk|saucer)s?|saucers?|ufos?|uaps?|unidentified|craft|"
    r"altitude|hovering|heading|approximately \d+ (?:feet|miles|seconds|minutes)|reported)(?![A-Za-z])",
    re.IGNORECASE,
)
SIGHTING_MIN_HITS = 4

# Document kinds that are not sighting accounts (research papers, contracts).
NON_SIGHTING_KINDS = {"scientific_study", "administrative"}


def is_sighting_text(text: str) -> bool:
    return len(_SIGHTING.findall(text or "")) >= SIGHTING_MIN_HITS


def _snippet(text: str, start: int, end: int, width: int = 110) -> str:
    a = max(0, start - width)
    b = min(len(text), end + width)
    # widen to word boundaries
    while a > 0 and text[a - 1].isalnum():
        a -= 1
    while b < len(text) and text[b].isalnum():
        b += 1
    s = re.sub(r"\s+", " ", text[a:b]).strip()
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


@dataclass
class Hit:
    key: str
    snippet: str
    match: str


# Report forms (Project Blue Book "Incident Summaries", Air Force questionnaires)
# print field labels such as "Odor detected: None" or "Exhaust trails n/s".
# A match followed by an empty answer is a form label, not an observation.
_EMPTY_ANSWER = re.compile(
    r"^[\s:.,;|\-]*(?:\(?if any\)?[\s:.,\-]*)?(?:(?:detected|dotected|noted|observed|heard|seen|visible)[\s:.,\-]*)?"
    r"(?:none|nono|n\s?/\s?[sa]|n\.s\.|no\b|nil|negative|unknown|not (?:observed|noted|detected|stated)|u\s?/\s?s|b\s?/\s?s|[mw]\s?/\s?s)",
    re.IGNORECASE,
)


def find_observables(text: str) -> list[Hit]:
    """All observables present in ``text`` (first genuine occurrence of each)."""
    hits: list[Hit] = []
    if not text:
        return hits
    for obs, rx, neg in _COMPILED:
        for m in rx.finditer(text):
            if neg is not None and neg.search(text[max(0, m.start() - 40):m.start()]):
                continue
            if obs.key != "silent" and _EMPTY_ANSWER.match(text[m.end():m.end() + 40]):
                continue
            hits.append(Hit(obs.key, _snippet(text, m.start(), m.end()), m.group(0)))
            break
    return hits
