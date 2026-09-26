"""Deterministic keyword classifier. Always available and needs no API key.

It classifies from the publisher's title and description (high weight) and from
the extracted text (lower weight, since OCR text is noisy and long files
mention many things in passing).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .taxonomy import era_for_year

# facet -> value -> regex (case-insensitive, matched on word boundaries)
RULES: dict[str, dict[str, str]] = {
    "topic": {
        # Branch names alone don't make an encounter: every Blue Book file mentions
        # the Air Force, since it ran the program. Require a unit, base, aircrew
        # or an operational report.
        "military_encounter": r"military (?:pilots?|aircrews?|aviators?|personnel|service ?members?|witness(?:es)?|sources?|observers?|operators?|platforms?|systems?|sensors?|bases?|installations?|compounds?|aircraft|jets?|radar|units?)|air force (?:pilots?|jets?|aircraft|radar|crews?|personnel|interceptors?)|navy (?:pilots?|ships?|vessels?|aircraft|carriers?|aviators?|radar|personnel|crews?)|(?:army|marine corps) (?:personnel|bases?|posts?|units?|soldiers?)|(?:u\.s\.|united states) (?:navy|air force|army|marine corps) (?:unidentified|uap|ufo)|fighter (?:pilots?|jets?)|interceptors?|scrambled|combatant command|centcom|indopacom|northcom|eucom|africom|southcom|misrep|mission report|range fouler|isr|warship|carrier strike",
        "aviation": r"pilots?|aircrew|cockpit|fighter|f-?\d{2}|mq-9|reaper|aircraft|airliner|faa",
        "nuclear": r"nuclear|atomic|los alamos|sandia|oak ridge|hanford|icbm|missile (?:silo|site|facility)|minuteman|weapons storage|department of energy|nnsa",
        "space": r"astronauts?|apollo|gemini|mercury|skylab|space shuttle|iss\b|orbit(?:al)?|lunar|moon|nasa",
        "maritime": r"underwater|undersea|submerged|usos?|transmedium|ocean|sea surface|submarine|ship|vessel|arabian gulf|yellow sea|east china sea",
        "law_enforcement": r"police|sheriff|law enforcement|trooper|officers? reported",
        "public_sighting": r"civilian|citizen|resident|member of the public|farmer|witness(?:es)? reported|usper",
        "drones": r"drones?|uas\b|unmanned|swarm",
        "crash_retrieval": r"crash(?:ed)?|debris|wreckage|recovered (?:object|material)|retrieval|fragment|metallic sample|material sample",
        "contact_claims": r"abduct|occupants?|humanoid|alien beings?|extraterrestrial (?:beings?|visitors?)|contactee|little men",
        "advanced_tech": r"propulsion|anti-?gravity|warp|metamaterial|metallic glass|spintronic|breakthrough|dird|advanced aerospace|exotic",
        "government_program": r"project blue book|project sign|project grudge|aawsap|aatip|uap task force|robertson panel|condon committee|scientific advisory panel",
        "foreign_activity": r"soviet|ussr|russia|chinese|china|prc|iran|adversar|foreign (?:aircraft|technology|intelligence)",
        "radar_tracking": r"radar|tracked|track(?:ing)? data|sensor|flir|infrared",
        "biological": r"injur|burns?|radiation (?:sickness|exposure)|nausea|health effects|medical",
    },
    "shape": {
        "orb": r"orbs?|spheres?|spherical|ball(?:s)? of light|round object|globe",
        "disc": r"discs?|disks?|saucers?|flying discs?|flying saucers?|disc-shaped",
        "cigar": r"cigar(?:-shaped)?|cylind(?:er|rical)|tube-shaped|rod-shaped",
        "triangle": r"triang(?:le|ular)|boomerang|chevron|v-shaped",
        "tic_tac": r"tic[- ]?tac|capsule-shaped|pill-shaped",
        "light": r"lights? in the sky|bright lights?|points? of light|glowing|luminous|flashing lights?",
        "cube": r"cubes?|box-shaped|cube within a sphere",
        "fireball": r"fireballs?|green fireball|meteor-like",
        "formation": r"formation|cluster of|multiple objects|\d+ objects|swarm|orbs launching",
        "irregular": r"irregular|jellyfish|star-shaped|diamond-shaped|egg-shaped|oval",
    },
    "domain": {
        "air": r"airspace|altitude|in the sky|aerial|flying|feet msl|ft agl|flight level",
        "space": r"orbit|lunar|moon|outer space|spacecraft|astronaut",
        "sea": r"sea surface|over the (?:ocean|water|sea)|ship|vessel|maritime",
        "undersea": r"underwater|undersea|submerged|entered the water|transmedium",
        "land": r"landed|landing site|on the ground|ground trace|hovering over (?:a )?(?:field|road)",
    },
    "witness": {
        "military_pilot": r"pilots?|aircrew|aviators?|wso|weapons systems officer|mq-9 (?:crew|operator)",
        "military": r"airmen|soldiers?|sailors?|servicemembers?|military personnel|sentry|guards?|crew members?",
        "law_enforcement": r"police|sheriff|trooper|deputy|law enforcement",
        "civilian": r"civilian|citizen|resident|farmer|motorist|usper|member of the public",
        "astronaut": r"astronauts?|aldrin|armstrong|lovell|cernan|schmitt|cooper|glenn",
        "scientist": r"scientists?|physicist|astronomer|engineers?|dr\.",
        "official": r"senior (?:usic )?official|government official|ambassador|embassy",
        "intelligence": r"cia|intelligence (?:source|officer|report)|informant|agent reported",
    },
    "sensor": {
        "visual": r"visually|observed|eyewitness|saw|naked eye|sighted",
        "radar": r"radar",
        # not a bare "IR" (OCR noise in old scans) or "thermal" (air currents)
        "infrared": r"infrared|flir|eo/ir|forward[- ]looking infrared|ir (?:sensors?|cameras?|pods?|imag(?:e|es|ery|ing)|video|footage|signatures?|targeting)|thermal (?:imag(?:e|es|ery|ing)|cameras?|sensors?|signatures?|video|footage)",
        "video": r"video|fmv|full motion|footage|film",
        "photo": r"photo(?:graph)?s?|images?|camera|still frame",
        "satellite": r"satellite|space-based",
        "sonar": r"sonar|acoustic",
        "radio": r"radio|sigint|signals? intelligence|transmission",
    },
    "program": {
        "blue_book": r"blue book",
        "sign_grudge": r"project sign|project grudge",
        "robertson_panel": r"robertson panel|scientific advisory panel on unidentified",
        "condon": r"condon",
        "aawsap": r"aawsap|aatip|bigelow|baass|dird",
        "uaptf": r"uap task force|uaptf",
        "apollo": r"apollo|gemini|mercury|skylab",
    },
}

ASSESSMENT_RULES = [
    ("hoax", r"\bhoax(?:es|ed)?\b|characteri[sz]\w* [^.]{0,60}\bas (?:a |an )?(?:fabrication|forgery|hoax)|(?:report|photo(?:graph)?|image|video|story|claim|sighting)s? (?:was|were) (?:a |an )?(?:fabrication|forgery|fabricated|faked|staged)"),
    ("resolved_balloon", r"(?:assessed|determined|identified|resolved|consistent with|likely)[^.]{0,80}balloons?"),
    ("resolved_bird", r"(?:assessed|determined|identified|resolved|consistent with|likely|were)[^.]{0,80}(?:birds?|seabirds?|seagulls?)"),
    ("resolved_satellite", r"(?:assessed|determined|identified|resolved|consistent with|likely)[^.]{0,80}(?:satellites?|starlink|space debris|rocket body)"),
    ("resolved_aircraft", r"(?:assessed|determined|identified|resolved|consistent with|likely)[^.]{0,80}(?:aircraft|airplane|drones?|uas)\b"),
    ("resolved_astronomical", r"(?:assessed|determined|identified|resolved|consistent with|likely)[^.]{0,80}(?:venus|jupiter|planet|stars?|meteors?|moon)\b"),
    ("resolved_artifact", r"(?:assessed|determined|identified|resolved|consistent with|likely)[^.]{0,80}(?:artifact|lens flare|camera defect|film defect|reflection|parallax|micrometeoroid)"),
    ("unresolved", r"unresolved|unidentified|could not (?:be )?(?:determine|identif)|remains? unexplained|insufficient data|no (?:definitive|conclusive) determination"),
]

KIND_RULES = [
    # contract paperwork first: a "Statement of Objectives" is not a witness statement
    ("administrative", r"statement of (?:objectives|work)|solicitation|contract (?:award|modification)|purchase order|budget"),
    ("mission_report", r"mission report|misrep|range fouler|debrief|unresolved uap report"),
    ("diplomatic_cable", r"cable|department of state|embassy|telegram"),
    ("transcript", r"transcript|technical crew debriefing|air-to-ground|onboard voice"),
    ("scientific_study", r"dird|study|research|scientific|analysis of|technical report|metallic|spintronic"),
    ("investigation_file", r"project blue book|case file|investigation|incident summar|file on|flying disc|numeric(?:al)? files?|vol[_ ]\d"),
    ("witness_statement", r"statement|narrative|interview|testimony|usper"),
    ("intelligence_report", r"intelligence|information report|cia|dia\b"),
    ("analysis", r"analysis|assessment|case resolution|update"),
    ("administrative", r"contract|solicitation|order|budget"),
    ("correspondence", r"memo(?:randum)?|letter|correspondence"),
]

# order matters: "Washington, D.C." must match before the state of Washington
US_REGIONS = {
    "us_east": "maine|new hampshire|vermont|massachusetts|rhode island|connecticut|new york|new jersey|pennsylvania|delaware|maryland|virginia|washington, d\\.?c\\.?|boston|northeastern united states|eastern united states",
    "us_west": "washington|oregon|california|nevada|idaho|montana|wyoming|utah|colorado|arizona|new mexico|alaska|hawaii|las vegas|los angeles|san diego|seattle|denver|phoenix|albuquerque|roswell|area 51|western united states|westen united states",
    "us_south": "texas|oklahoma|arkansas|louisiana|mississippi|alabama|georgia|florida|south carolina|north carolina|tennessee|kentucky|west virginia|cape kennedy|cape canaveral|southeastern united states|gulf of mexico",
    "us_midwest": "ohio|indiana|illinois|michigan|wisconsin|minnesota|iowa|missouri|kansas|nebraska|south dakota|north dakota|midwest",
}
WORLD_REGIONS = {
    "middle_east": "centcom|middle east|iraq|iran|syria|jordan|saudi|kuwait|qatar|bahrain|uae|united arab emirates|oman|yemen|arabian gulf|persian gulf|red sea|afghanistan|israel|egypt|gulf of aden|strait of hormuz",
    "asia_pacific": "indopacom|japan|korea|china|taiwan|philippines|yellow sea|east china sea|south china sea|australia|guam|okinawa|pacific|india|vietnam|thailand|indonesia",
    "europe": "eucom|greece|germany|france|united kingdom|england|italy|spain|poland|hungary|budapest|norway|sweden|belgium|netherlands|azerbaijan|turkey|ukraine|mediterranean|baltic",
    "russia": "ussr|soviet|russia|siberia|moscow",
    "latin_america": "southcom|brazil|bahia|mexico|argentina|chile|peru|colombia|venezuela|puerto rico|caribbean|cuba",
    "africa": "africom|africa|somalia|libya|niger|djibouti|kenya|nigeria",
    "oceans": "atlantic ocean|north atlantic|pacific ocean|indian ocean|open ocean",
    "space": "moon|lunar|low earth orbit|low-earth orbit|orbit|space",
}


# Phrases that only count in the publisher's title and description: in the
# body text they are letterheads and return addresses ("Wright-Patterson Air
# Force Base", "4602d Air Intelligence Service Squadron") on every Blue Book
# file, whatever it is about.
META_ONLY_RULES: dict[tuple[str, str], str] = {
    ("topic", "military_encounter"): r"air force bases?|squadrons?",
}

# A sensor the body text mentions in passing (in a later analysis, say)
# cannot have recorded an incident that predates the technology. Applies to
# text matches only; the publisher's own title or description always counts.
SENSOR_MIN_YEAR = {"infrared": 1965, "satellite": 1957}


def _rx(pattern: str) -> re.Pattern:
    return re.compile(rf"(?<![A-Za-z0-9])(?:{pattern})(?![A-Za-z0-9])", re.IGNORECASE)


_COMPILED = {facet: {v: _rx(p) for v, p in vals.items()} for facet, vals in RULES.items()}
_META_ONLY = {k: _rx(p) for k, p in META_ONLY_RULES.items()}
_ASSESS = [(v, re.compile(p, re.IGNORECASE)) for v, p in ASSESSMENT_RULES]
_KIND = [(v, _rx(p)) for v, p in KIND_RULES]
_US = {k: _rx(p) for k, p in US_REGIONS.items()}
_WORLD = {k: _rx(p) for k, p in WORLD_REGIONS.items()}
_US_GENERIC = _rx(r"united states|u\.s\.|usa|conus|northcom|norad")


@dataclass
class RuleResult:
    tags: dict[str, list[str]] = field(default_factory=dict)
    assessment: str = "not_assessed"
    kind: str = "other"


def kind_from_title(title: str, media_type: str = "pdf") -> str:
    """A record's document kind from its published title alone: enough to
    label a record whose file has not been fetched yet."""
    return classify_rules(title, None, None, media_type).kind


def _anachronistic(facet: str, value: str, incident_year: int | None) -> bool:
    if facet != "sensor" or not incident_year:
        return False
    return incident_year < SENSOR_MIN_YEAR.get(value, 0)


def region_for_location(location: str | None) -> str | None:
    if not location:
        return None
    for key, rx in _US.items():
        if rx.search(location):
            return key
    for key, rx in _WORLD.items():
        if rx.search(location):
            return key
    if _US_GENERIC.search(location):
        return "us_other"
    if re.search(r"various|multiple|worldwide", location, re.I):
        return "other"
    return None


def classify_rules(
    title: str,
    description: str | None,
    text: str | None,
    media_type: str,
    location: str | None = None,
    incident_year: int | None = None,
    text_min_hits: int = 3,
    description_shared: bool = False,
) -> RuleResult:
    """``description_shared``: the publisher reuses this description for a
    whole collection (e.g. every section of one FBI file), so it says little
    about this particular record and only counts as body text."""
    # AARO annotated nearly every recent description ("AARO Comment: ...",
    # "AARO assessed ..."): that says who reviewed the record, not its subject
    desc = re.sub(r"\bAARO\b( Comment:?)?", "", description or "")
    meta = title if description_shared else f"{title}\n{desc}"
    body = (text or "")[:400_000]
    if description_shared:
        body = f"{desc}\n{body}"
    res = RuleResult()
    # long files mention many things in passing: require more hits the longer they are
    min_hits = max(text_min_hits, len(body) // 20_000)

    for facet, rules in _COMPILED.items():
        values = []
        for value, rx in rules.items():
            meta_rx = _META_ONLY.get((facet, value))
            if rx.search(meta) or (meta_rx and meta_rx.search(meta)):
                values.append(value)
            elif body and len(rx.findall(body)) >= min_hits and not _anachronistic(facet, value, incident_year):
                values.append(value)
        if values:
            res.tags[facet] = values

    # document kind: media type decides for non-documents
    if media_type in ("video", "audio"):
        res.kind = media_type
    elif media_type == "image":
        res.kind = "photo"
    else:
        for value, rx in _KIND:
            if rx.search(title):
                res.kind = value
                break
        else:
            for value, rx in _KIND:
                if rx.search(meta):
                    res.kind = value
                    break

    # assessment: prefer the publisher's own description (AARO comments live there)
    for source in (description or "", body[:50_000]):
        for value, rx in _ASSESS:
            if rx.search(source):
                res.assessment = value
                break
        if res.assessment != "not_assessed":
            break

    region = region_for_location(location) or region_for_location(title)
    if region:
        res.tags["region"] = [region]
    era = era_for_year(incident_year)
    if era:
        res.tags["era"] = [era]
    return res
