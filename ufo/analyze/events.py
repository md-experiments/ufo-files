"""Sighting accounts and what they describe.

A sighting page is cut into *accounts*: paragraphs (or a whole report form)
that describe one observation. Each account is tagged with what the witness
or sensor reported: the object's shape and colour, how its light behaved,
what it sounded like, how it moved, visible structure, effects on people,
machines or the ground, how many objects there were, how long it lasted, the
time of day and how it was observed.

The tags describe the event, not the paperwork: agency, archive series,
region, decade and the wording of the document are deliberately left out, so
two accounts match only when they describe the same kind of thing happening.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .features import _EMPTY_ANSWER, BY_KEY as OBS, is_sighting_text

# dimension -> (label, weight in similarity). Behaviour and phenomena weigh
# most; context (number, duration, time of day, how it was seen) little.
DIMENSIONS = {
    "shape": ("Shape", 1.0),
    "colour": ("Colour", 0.7),
    "light": ("Light", 1.2),
    "sound": ("Sound", 1.3),
    "motion": ("Movement", 1.3),
    "structure": ("Structure & trail", 1.1),
    "effect": ("Effects", 1.5),
    "count": ("Number of objects", 0.5),
    "duration": ("Duration", 0.35),
    "time": ("Time of day", 0.3),
    "observer": ("Observed by / with", 0.4),
}
# dimensions that describe the phenomenon itself; a match needs at least one
BEHAVIOUR = {"light", "sound", "motion", "structure", "effect"}
CORE = BEHAVIOUR | {"shape"}

_NEG = re.compile(r"\b(?:no|not|never|without|nor|neither|none|any)\b[^.;:\n]{0,25}$", re.IGNORECASE)
_OBJ = r"(?:object|objects|craft|saucers?|dis[ck]s?|lights?|spheres?|balls?|orbs?|thing|it|ufos?|uaps?|body)"
_COLOUR_NOUN = (r"(?:lights?|glow|glowing|objects?|colou?r(?:ed)?|orbs?|balls?|spheres?|dis[ck]s?|flames?|fireballs?|"
                r"flash(?:es)?|haze|halo|trail|beams?|saucers?|craft|body|cent(?:er|re)|rim|tail|luminosity|brilliance|"
                r"streak|spot|star)")


def _colour(words: str) -> str:
    """A colour word used for the object: followed by a noun it describes, or
    after a verb ("it was bright red"). Place names like White Sands are not."""
    c = rf"(?:{words})(?:ish)?"
    return (rf"{c}(?:[- ](?:colou?red|glowing|hued|tinted))?(?:\s*(?:and|or|to|-|/)\s*\w+)?\s+{_COLOUR_NOUN}"
            rf"|(?:was|were|appeared|looked|seemed|glowed|glowing|turned|changed to|colou?r(?:ed)?:?|colou?r was)\s+"
            rf"(?:a\s+)?(?:bright|brilliant|dull|pale|deep|light|dark|vivid|very|intense)?\s*{c}(?![- ]?(?:sands|house|river|mountains?))")


@dataclass(frozen=True)
class Tag:
    key: str
    label: str
    dim: str
    pattern: str
    negatable: bool = True


def _t(key, label, dim, pattern, negatable=True):
    return Tag(key, label, dim, pattern, negatable)


TAGS: list[Tag] = [
    # shape
    _t("disc", "Disc / saucer", "shape", r"dis[ck]s?|dis[ck]-shaped|saucers?|pie[- ]?pans?|plate[- ]shaped|like (?:a|two) (?:plates?|dinner plates?)|coin[- ]shaped"),
    _t("sphere", "Sphere / ball / orb", "shape", r"spheres?|spherical|balls?(?!oon)(?! of (?:fire|flame))|orbs?|globes?|globular|round (?:object|light|ball|shape)"),
    _t("cigar", "Cigar / cylinder", "shape", r"cigars?|cigar[- ]shaped|cylind(?:er|ers|rical)|tub(?:e|es|ular)(?:[- ]shaped)?|pencil[- ]shaped|rocket[- ]shaped|torpedo[- ]shaped|capsule"),
    _t("oval", "Oval / egg / tic-tac", "shape", r"ovals?|oval[- ]shaped|egg[- ]shaped|elliptical|ellipse|football[- ]shaped|tic[- ]?tac"),
    _t("triangle", "Triangle", "shape", r"triang(?:le|les|ular)|delta[- ]shaped|arrowhead"),
    _t("chevron", "Boomerang / V / crescent", "shape", r"boomerang|chevron|v[- ]shaped|crescent|half[- ]moon|wing[- ]shaped"),
    _t("diamond", "Diamond / box", "shape", r"diamond[- ]shaped|cube|cubical|box[- ]shaped|rectangular (?:object|shape)|square (?:object|shape)"),
    _t("fireball", "Fireball", "shape", r"fire ?balls?|balls? of (?:fire|flame)|flaming (?:ball|object)"),
    _t("starlike", "Star-like point", "shape", r"star[- ]like|like (?:a|an) (?:bright |large )?star|resembl\w* (?:a )?star|point of light|pin ?point of light"),
    # colour
    _t("metallic", "Metallic / silver", "colour", OBS["metallic"].pattern),
    _t("white", "White", "colour", _colour("white|whitish")),
    _t("red_orange", "Red / orange", "colour", OBS["red_orange"].pattern + "|" + _colour("red|reddish|orange|amber|red-orange|orange-red")),
    _t("yellow", "Yellow / gold", "colour", _colour("yellow|yellowish|gold|golden")),
    _t("green", "Green", "colour", OBS["green"].pattern + "|" + _colour("green|greenish|green-yellow|greenish-yellow|blue-green")),
    _t("blue_white", "Blue / blue-white", "colour", OBS["blue_white"].pattern + "|" + _colour("blue|bluish")),
    _t("dark", "Dark / black", "colour", _colour("black|dark gr[ae]y|dark|grey|gray")),
    _t("colour_change", "Changing colours", "colour", r"chang(?:ed|ing|es) colou?rs?|multi-?colou?red|(?:various|several|different) colou?rs|colou?rs? chang(?:ed|ing)|rainbow"),
    # light
    _t("glow", "Glowing / self-luminous", "light", r"glow(?:ed|ing|s)?|self[- ]luminous|luminous|luminescen\w*|incandescent|phosphorescent|lit up|illuminated"),
    _t("halo", "Halo / corona / haze", "light", r"halos?|haloes|ring of (?:light|fire)|corona|aura|fuzzy (?:edges?|outline)|haze around"),
    _t("flashing", "Flashing / pulsing", "light", OBS["flashing"].pattern),
    _t("bright", "Very bright", "light", r"(?:very|extremely|intensely|unusually|exceptionally) bright|brilliant(?:ly)?|blinding|dazzling|bright as (?:the )?(?:sun|moon|a star|venus)|brighter than (?:any |a |the )?(?:star|planet|venus|moon)"),
    _t("beam", "Beam of light", "light", r"beams? of light|light beams?|searchlight|spotlight|shaft of light|rays? of light|shone (?:a )?(?:light|beam)"),
    # sound
    _t("silent", "Silent", "sound", OBS["silent"].pattern, negatable=False),
    _t("hum", "Hum / buzz / drone", "sound", r"humm?(?:ing|ed)?(?: sound| noise)?|buzz(?:ing|ed)?|whirr?(?:ing)?|droning|throbbing"),
    _t("whoosh", "Whoosh / rushing air", "sound", r"whoosh\w*|swish(?:ing)?|rushing (?:sound|noise|of air|air|wind)|like (?:the )?(?:rushing )?wind"),
    _t("roar", "Roar / rumble", "sound", r"roar(?:ing|ed)?|rumbl(?:e|ing|ed)|like thunder|thunderous"),
    _t("whistle", "Whistle / whine", "sound", r"whistl(?:e|ing|ed)|whin(?:e|ing)|high[- ]pitched"),
    _t("bang", "Bang / explosion", "sound", r"explo(?:sion|ded|sive sound)|loud (?:report|bang|boom|noise)|sonic boom|\bbang\b"),
    # motion
    _t("hover", "Hovering / stationary", "motion", OBS["hover"].pattern),
    _t("accelerate", "Sudden acceleration", "motion", r"(?:sudden|rapid|terrific|tremendous|instant\w*|fantastic) accelerat\w+|accelerat\w+ (?:rapidly|suddenly|away|at)|shot (?:off|away|straight up|upward|up)|took off at|sped (?:away|off)|zoom(?:ed|ing) (?:away|off|up)|streaked (?:away|off|across|out)"),
    _t("high_speed", "Extreme speed", "motion", OBS["high_speed"].pattern),
    _t("maneuver", "Abrupt turns", "motion", OBS["maneuver"].pattern + r"|sharp turns?|made a (?:sharp |sudden |quick )?turn|turned (?:sharply|abruptly|suddenly)"),
    _t("erratic", "Erratic / darting", "motion", r"erratic(?:ally)?|dart(?:ed|ing)|jerky|irregular (?:path|motion|movements?|course|flight)|bounc(?:ed|ing) around|zig[- ]?zag\w*"),
    _t("oscillate", "Wobbling / fluttering", "motion", r"oscillat\w+|wobbl\w+|flutter\w*|rock(?:ed|ing) (?:back and forth|motion|from side to side)|sway(?:ed|ing)|bobb(?:ed|ing)|(?:like a |falling )leaf|pendulum|flipp(?:ed|ing) over"),
    _t("rotate", "Spinning / rotating", "motion", r"rotat(?:ed|ing|ion)|spinn(?:ing|er)|\bspun\b|revolv(?:ed|ing)|turning on its axis|whirl(?:ed|ing)"),
    _t("climb", "Vertical climb", "motion", r"climb(?:ed|ing)? (?:vertically|straight up|steeply|rapidly|sharply)|rose (?:straight up|vertically|rapidly|swiftly)|ascen(?:t|ded|ding)|straight up(?:ward)?|vertical(?:ly)? (?:climb|ascent|rise)|went up (?:rapidly|straight)|steep(?:ly)? climb\w*"),
    _t("descend", "Descending / landing", "motion", rf"{_OBJ}\s+(?:\w+\s+){{0,3}}(?:landed|descended|came down|dropped|settled|touched down|lowered)|landed (?:in|on) (?:a|the) (?:field|ground|road|pasture)"),
    _t("pacing", "Followed / paced a vehicle", "motion", r"(?:followed|paced|trailed|chased|shadowed|kept pace with|flew alongside|flying alongside|stayed with|pursued) (?:the|our|his|her|their|my|a) (?:aircraft|plane|airplane|ship|car|vehicle|jet|fighter|bomber|b-?\d+|f-?\d+|train|automobile|truck)|was being followed|(?:circled|circling) (?:the|our|his|their) (?:aircraft|plane|airplane|ship|car|vehicle|jet)"),
    _t("circling", "Circling / orbiting", "motion", r"circl(?:ed|ing) (?:over|around|above|the (?:area|field|base|city|town))|orbit(?:ed|ing) (?:over|around|the)|in circles"),
    _t("vanish", "Vanished abruptly", "motion", r"(?:disappeared|vanished|blinked out|went out|winked out|faded out) (?:instantly|suddenly|abruptly|immediately|in an instant|in a flash|like a light)|(?:suddenly|abruptly|instantly|just) (?:disappeared|vanished|went out|blinked out)|dimmed (?:and|then) (?:disappeared|went out)"),
    _t("drift", "Slow drifting", "motion", r"drift(?:ed|ing)|(?:moved|moving|travel(?:l)?ed|flew) (?:very )?slowly|slow(?:ly)? moving|float(?:ed|ing)"),
    _t("split_merge", "Splitting / merging", "motion", OBS["split_merge"].pattern),
    _t("transmedium", "Into / out of water", "motion", OBS["transmedium"].pattern),
    # structure
    _t("no_wings", "No wings / exhaust", "structure", OBS["no_wings"].pattern + r"|no (?:visible )?protrusions", negatable=False),
    _t("trail", "Trail / vapour", "structure", OBS["trail"].pattern),
    _t("sparks", "Sparks / flames", "structure", r"(?:showers? of|emitt\w+|throwing|shooting|trailing|gave off|giving off) sparks|sparks? (?:flew|flying|coming|shooting|trailing|fell|falling|from)|flames? (?:coming|shooting|trailing|issuing) (?:from|out)|fire (?:from|out of) (?:the |its )?(?:rear|tail|bottom|back)|exhaust flame"),
    _t("dome", "Dome / cupola", "structure", r"dome[ds]?|dome[- ]like|cupola|turret|hat[- ]shaped|bubble on top"),
    _t("windows", "Windows / row of lights", "structure", r"port ?holes?|windows? (?:around|along|in) (?:the|it)|lighted windows|rows? of (?:lights|windows)|lights? (?:around|along) (?:the|its) (?:rim|edge|perimeter|bottom)"),
    _t("rim", "Rim / ring / band", "structure", r"\brim\b|flange|ring around (?:it|the)|band (?:around|of lights)"),
    _t("large", "Very large", "structure", OBS["large"].pattern),
    # effects
    _t("em_effects", "Engine / electrical failure", "effect", OBS["em_effects"].pattern),
    _t("instruments", "Instrument anomalies", "effect", OBS["instruments"].pattern),
    _t("physiological", "Effects on witnesses", "effect", OBS["physiological"].pattern),
    _t("traces", "Ground traces", "effect", r"ground traces?|landing (?:site|marks?|traces?|gear marks)|(?:scorch|burn)(?:ed)? (?:marks?|area|grass|ground|circle)|(?:circular|crushed|flattened|burned|withered|dried) (?:area|imprints?|depressions?|vegetation|grass|plants)|imprints? in the (?:ground|soil)|(?:holes|marks|depressions) (?:in|on) the (?:ground|soil)"),
    _t("smell", "Odour", "effect", OBS["smell"].pattern),
    _t("heat", "Heat felt", "effect", r"(?:felt|intense|radiat\w+|wave of|blast of) (?:heat|warmth)|heat (?:was felt|from the object)"),
    _t("animals", "Animals reacted", "effect", r"(?:dogs?|cattle|cows?|horses?|animals|birds|livestock|sheep)\s+(?:\w+\s+){0,2}(?:barked|howled|were (?:frightened|restless|disturbed|upset|excited)|became (?:restless|excited|frightened|agitated)|stampeded|panicked|bolted)"),
    # number
    _t("pair", "Two objects", "count", r"(?:two|2|pair of|a couple of) (?:\w+ )?(?:objects|dis[ck]s|lights|orbs|spheres|balls|craft|saucers|things)"),
    _t("formation", "Several / formation", "count", r"(?:in|flying in|a) (?:tight |loose |v[- ]?|diamond |triangular |echelon |straight )?formation|(?:three|four|five|six|seven|eight|nine|ten|several|many|multiple|numerous|a number of|group of|\d{1,2}) (?:\w+ )?(?:objects|dis[ck]s|lights|orbs|spheres|balls|craft|saucers)"),
    # duration (the number is read in tag_account)
    _t("brief", "A few seconds", "duration", r"__duration_brief__"),
    _t("minutes", "Minutes", "duration", r"__duration_minutes__"),
    _t("long", "Half an hour or more", "duration", r"__duration_long__"),
    # time of day
    _t("night", "At night", "time", r"night(?:time)?|after dark|midnight|(?:late|in the) evening"),
    _t("twilight", "Dusk / dawn", "time", r"dusk|twilight|sunset|dawn|sunrise|daybreak"),
    _t("daylight", "Daylight", "time", r"daylight|daytime|broad day|afternoon|midday|noon"),
    # how it was observed
    _t("pilot", "Pilot / aircrew", "observer", r"(?:the |our |a |airline |fighter |jet )pilots?|co-?pilot|aircrew|crew (?:of|aboard) the (?:aircraft|plane|airliner)|while (?:in )?flying|in flight|cockpit"),
    _t("radar", "Radar", "observer", OBS["radar"].pattern),
    _t("infrared", "Infrared / FLIR", "observer", OBS["infrared"].pattern),
    _t("instrument_obs", "Theodolite / telescope / binoculars", "observer", r"theodolites?|cine-?theodolites?|kine-?theodolites?|telescopes?|binoculars|field glasses|tracking (?:instrument|camera|telescope)"),
    _t("photo", "Photographed / filmed", "observer", r"photograph(?:ed|s)?|took (?:a )?(?:pictures?|photos?|movies?)|filmed|motion pictures?|movie camera"),
    _t("at_sea", "From a ship", "observer", r"(?:from|aboard|on board|on) (?:the |a )?(?:ship|vessel|uss|u\.s\.s\.|destroyer|carrier|freighter|tanker|boat)|at sea"),
    _t("driving", "From a vehicle", "observer", r"while driving|(?:in|from) (?:his|her|their|the) (?:car|truck|automobile|vehicle)|on the highway|patrol car"),
    _t("many_witnesses", "Many witnesses", "observer", r"(?:several|many|numerous|hundreds of|dozens of|a number of|\d{2,}) (?:other )?(?:witnesses|people|persons|observers|residents|citizens|spectators)"),
]
BY_TAG = {t.key: t for t in TAGS}

_DURATION = re.compile(
    r"(?:for|lasted|lasting|duration(?: of)?|visible for|in sight|period of|approximately|approx\.?|about|some)\s+"
    r"(?:about |approximately |some |around |nearly |almost )?(\d{1,3}|one|two|three|four|five|six|ten|fifteen|twenty|thirty|forty|a few|several)"
    r"\s*(?:(?:to|or|-)\s*\d{1,3}\s*)?(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_WORDNUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "ten": 10, "fifteen": 15, "twenty": 20,
            "thirty": 30, "forty": 40, "a few": 3, "several": 5}
_COMPILED = [(t, re.compile(rf"(?<![A-Za-z])(?:{t.pattern})(?![A-Za-z])", re.IGNORECASE))
             for t in TAGS if not t.pattern.startswith("__")]
_SENT_END = re.compile(r"[.!?]")
# instructions and blank questionnaires ("sketch if possible", "(Name city, country)")
_TEMPLATE = re.compile(r"\bif (?:any|possible|known|applicable|so)\b|\bshould be\b|\bdescribe\b|\bindicate\b|"
                       r"\bstate whether\b|\bname (?:city|of)\b|\bcheck (?:one|appropriate)\b|\bplease\b|\bwill be\b|"
                       r"\bshall\b|\bmust\b|\binclude\b|\bsuch as\b|\bcode\s+\d+|\be\.\s?g\.", re.IGNORECASE)
_EXPLAIN = re.compile(r"\b(?:explained as|identified as|attributed to|probably (?:was|were|a|an)|most likely|possibl[ey] (?:a|an|was))\b",
                      re.IGNORECASE)


@dataclass
class Account:
    page_no: int
    seq: int
    text: str
    tags: list[str] = field(default_factory=list)
    spans: list[list] = field(default_factory=list)  # [tag, start, end]


_ENUM = re.compile(r"^\s*(?:\(?\d{1,2}\)|\d{1,2}\s?\.|[a-hA-H]\s?\.|\([a-h]\))\s")


def _is_label(text: str, start: int, end: int) -> bool:
    """The match sits in a printed form label ("b. Roar, whistle, whoosh.",
    "Shape of object:") rather than in an answer or a narrative."""
    a = text.rfind("\n", 0, start) + 1
    b = text.find("\n", end)
    line = text[a:b if b >= 0 else len(text)].strip()
    return len(line) <= 48 and (line.endswith(":") or bool(_ENUM.match(line)))


def _in_option_list(text: str, start: int, end: int) -> bool:
    """Printed choices: "Vertical ascent or descent, oscillating, fluttering, erratic, etc." """
    a = max(text.rfind(".", 0, start - 1), text.rfind("\n\n", 0, start)) + 1
    b = text.find(".", end)
    sent = text[a:b + 1 if b >= 0 else len(text)]
    return bool(re.search(r"\betc\b", sent)) and sent.count(",") >= 3


def _is_question(text: str, end: int) -> bool:
    m = _SENT_END.search(text, end, min(len(text), end + 200))
    return bool(m and m.group(0) == "?")


def _duration(text: str) -> tuple[str, int, int] | None:
    for m in _DURATION.finditer(text):
        n = m.group(1).lower()
        n = _WORDNUM.get(n, None) if not n.isdigit() else int(n)
        if not n:
            continue
        unit = m.group(2).lower()
        secs = n * (1 if unit.startswith("s") else 60 if unit.startswith("m") else 3600)
        key = "brief" if secs < 60 else "minutes" if secs < 1800 else "long"
        return key, m.start(), m.end()
    return None


def tag_account(text: str) -> tuple[list[str], list[list]]:
    """The event tags present in ``text`` and where each first occurs."""
    found: dict[str, tuple[int, int]] = {}
    taken: list[tuple[int, int, str]] = []
    for tag, rx in _COMPILED:
        for m in rx.finditer(text):
            if tag.negatable and _NEG.search(text[max(0, m.start() - 40):m.start()]):
                continue
            if tag.key != "silent" and _EMPTY_ANSWER.match(text[m.end():m.end() + 40]):
                continue  # a blank form field: "Sound: none"
            if _is_label(text, m.start(), m.end()):
                continue
            if _in_option_list(text, m.start(), m.end()):
                continue
            if _is_question(text, m.end()):
                continue  # a printed question: "Did the object hover?"
            if tag.dim == "shape" and _EXPLAIN.search(text[max(0, m.start() - 60):m.start()]):
                continue  # an explanation, not an observation
            if any(dim == tag.dim and m.start() < e and s < m.end() for s, e, dim in taken):
                continue  # the same words already count for this dimension ("shot straight up")
            found[tag.key] = (m.start(), m.end())
            taken.append((m.start(), m.end(), tag.dim))
            break
    d = _duration(text)
    if d:
        found[d[0]] = (d[1], d[2])
    # pre-printed check lists ("disc / sphere / cigar / oval ...") are not answers
    for dim, limit in (("shape", 4), ("colour", 5), ("motion", 7), ("sound", 4)):
        keys = [k for k in found if BY_TAG[k].dim == dim]
        if len(keys) >= limit:
            for k in keys:
                del found[k]
    order = sorted(found, key=lambda k: found[k][0])
    return order, [[k, *found[k]] for k in order]


# ---------------------------------------------------------------------------
# cutting pages into accounts

_PARA = re.compile(r"\n\s*\n")
_WS = re.compile(r"[ \t]+")
_SENT = re.compile(r"(?<=[.!?])\s+")
MAX_ACCOUNT = 1400
MIN_TAGS = 2  # accounts with fewer event tags say too little to compare


def _clean(s: str) -> str:
    return "\n".join(_WS.sub(" ", line).strip() for line in s.strip().splitlines() if line.strip())


def segment(text: str) -> list[str]:
    """Paragraphs packed into accounts of up to ~1400 characters. Short
    paragraphs (form fields, headings) join their neighbours; long ones are
    cut at sentence breaks."""
    out: list[str] = []
    buf = ""
    for para in (_clean(p) for p in _PARA.split(text or "")):
        if not para:
            continue
        if len(para) > MAX_ACCOUNT:
            if buf:
                out.append(buf)
                buf = ""
            chunk = ""
            for s in _SENT.split(para):
                if chunk and len(chunk) + len(s) > MAX_ACCOUNT - 200:
                    out.append(chunk)
                    chunk = ""
                chunk = f"{chunk} {s}".strip()
            if chunk:
                out.append(chunk)
            continue
        if buf and len(buf) + len(para) > MAX_ACCOUNT:
            out.append(buf)
            buf = ""
        buf = f"{buf}\n\n{para}".strip()
    if buf:
        out.append(buf)
    return out


def candidates_for(units: list[tuple[int, str]]) -> list[Account]:
    """Passages worth tagging: paragraphs of sighting pages (page 0 is the
    description) where the rules find at least one detail of the phenomenon."""
    out: list[Account] = []
    for page_no, text in units:
        if page_no != 0 and not is_sighting_text(text):
            continue
        for seq, chunk in enumerate(segment(text)):
            if len(_TEMPLATE.findall(chunk)) >= 3:
                continue
            tags, spans = tag_account(chunk)
            if any(BY_TAG[t].dim in CORE for t in tags):
                out.append(Account(page_no, seq, chunk, tags, spans))
    return out


def keep(account: Account) -> bool:
    """An account needs at least two details of the phenomenon to be compared."""
    return sum(1 for t in account.tags if t in BY_TAG and BY_TAG[t].dim in CORE) >= MIN_TAGS


def accounts_for(units: list[tuple[int, str]]) -> list[Account]:
    """Tagged accounts from a record's pages, using the keyword rules."""
    return [a for a in candidates_for(units) if keep(a)]


def tag_label(key: str) -> str:
    return BY_TAG[key].label if key in BY_TAG else key


def dim_of(key: str) -> str:
    return BY_TAG[key].dim if key in BY_TAG else ""
