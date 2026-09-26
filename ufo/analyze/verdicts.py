"""Verdicts stated in the text: was the object identified, or not?

The publisher's assessment covers only part of the archive, so the same
verdict is also read from the files themselves, the way the close-encounter
kinds are. A sighting page or account is *explained* when it says what the
object was ("evaluated as a weather balloon", "turned out to be Venus",
"the object was a plastic balloon"), and *unresolved* when it says it never
was ("listed as unidentified", "could not be identified", "remains
unexplained", "EVALUATION: UNKNOWN"). Questions, negations ("not a balloon",
"didn't think it was a drone", "ruled out"), speculation ("may have been
caused by", "the possibility that"), witness impressions ("appeared to be a
plastic balloon", "they thought it was a plane") and hypotheticals do not
count.
"Unidentified flying object" is a name, not a verdict. "Insufficient data",
Blue Book's third category, is neither and is left out.

Where a passage carries both, a definite verdict beats a hedged one
("probable aircraft"), and the later one wins.

Blue Book record cards state their conclusion as a ticked box, which OCR
does not preserve, so most historical verdicts are out of reach here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# explanation categories, in the taxonomy's terms
CATEGORIES = [
    ("resolved_balloon", r"(?:weather |research |skyhook |pilot |radiosonde |hot[- ]air |plastic |toy )?balloons?|skyhook|radiosonde"),
    ("resolved_aircraft", r"aircraft|air ?planes?|airliners?|jet(?:s| aircraft| fighters?| planes?|-type aircraft)?(?! ?(?:nozzles?|engines?|exhausts?|streams?|noises?|sounds?|trails?))|helicopters?|"
                          r"bombers?|fighters?|(?:conventional |commercial |military |light |propeller[- ]driven )?planes?(?! crash)|"
                          r"missiles?|rockets?|drones?|uas|refuel(?:ing|ling) (?:aircraft|tanker|operation)|blimp|airship|dirigible"),
    ("resolved_astronomical", r"stars?|planets?|venus|jupiter|mars|saturn|sirius|capella|arcturus|meteors?|meteorites?|fireballs?|"
                              r"bolides?|comets?|the moon|moon|astronomical (?:bod(?:y|ies)|objects?|phenomen(?:on|a))|"
                              r"celestial (?:bod(?:y|ies)|objects?)|astral bod(?:y|ies)"),
    ("resolved_satellite", r"satellites?|echo (?:i|ii|1|2)|sputnik|re-?entry|space (?:debris|junk)|rocket bod(?:y|ies)|decaying (?:satellite|booster)"),
    ("resolved_bird", r"birds?|geese|(?:sea ?)?gulls?|ducks?|flocks? of (?:birds|geese|ducks)|insects?|moths?"),
    ("resolved_other", r"(?:lenticular )?clouds?|(?:ball )?lightning|(?:temperature )?inversion|mirage|refraction|"
                       r"reflections?(?: of (?:the )?(?:sun|moon|lights?|headlights))?|search ?lights?|sun ?dogs?|parhelia|"
                       r"natural phenomen(?:on|a)|weather phenomen(?:on|a)|marsh gas|swamp gas|st\.? elmo'?s fire|ice crystals|"
                       r"contrails?|vapou?r trails?|kites?|fireworks|(?:parachute |signal |aircraft )?flares?|parachutes?|"
                       r"optical (?:illusion|effect|phenomen(?:on|a))|autokinesis|autokinetic|hallucinations?|"
                       r"psychological (?:in origin|effect|phenomen(?:on|a))"),
    ("resolved_artifact", r"lens flares?|film defects?|processing defects?|emulsion defects?|(?:camera |photographic |sensor |"
                          r"video |compression |digital |image )artefacts?|(?:camera |photographic |sensor |video |compression |"
                          r"digital |image )artifacts?|double exposures?|scratch(?:es)? on the (?:film|negative)|"
                          r"(?:internal )?reflections? (?:in|off|from) the (?:lens|window|canopy|windshield)|bokeh|lens glare"),
    ("hoax", r"hoax(?:es)?|pranks?|fabrications?|fakes?|forger(?:y|ies)|practical jokes?"),
]
_CAT_ALT = "|".join(f"(?P<{k}>{p})" for k, p in CATEGORIES)
_ART = r"(?:(?:a|an|the|some|two|three|several|probable|possible|probably|possibly|likely|a probable|a possible) )?"
_HEDGE = r"(?:probably|possibly|likely|most likely|apparently|evidently|undoubtedly|almost certainly|clearly|obviously)"
_SUBJECT = (r"(?:objects?|lights?|sightings?|phenomen\w+|reports?|cases?|incidents?|ufos?|uaps?|glow|streaks?|images?|"
            r"targets?|contacts?|it|they|this|these|that|those|he saw|she saw|they saw|what (?:he|she|they|the witness) saw)")
# words allowed between the subject and "was": not a hedge ("they thought it was a plane")
_GAP = (r"(?!(?:thought|think|thinks|believed?|believes|felt|feel|assumed?|guess(?:ed)?|suspect(?:ed)?|imagined?|"
        r"supposed?|wondered|claim(?:ed|s)?|said|says|stated|reported|might|may|could|would|should|if|whether)\b)\w+")

# statements that the object was X ("probable" / "possible" X count, as Blue Book's own categories, but as hedges)
_EXPLAINED = re.compile(
    rf"\b(?:"
    rf"(?:identified|evaluated|explained|classified|listed|carried|written off|dismissed|accounted for|assessed|resolved|"
    rf"categori[sz]ed|logged|recorded|labell?ed) as {_ART}|"
    rf"(?:was|were|is|are|has been|have been|had been) (?:determined|found|shown|proved|proven|confirmed|established|judged|"
    rf"considered|believed|thought|concluded|assessed) to (?:be|have been) {_ART}|"
    rf"(?:turned out|proved|proven) to be {_ART}|"
    rf"(?:conclusions?|evaluation|final evaluation|final conclusion|assessment|explanation|cause|identification|solution)"
    rf"\s*[:\-–]\s*{_ART}|"
    rf"{_SUBJECT}\W+(?:{_GAP}\W+){{0,5}}(?:was|were|is|are|has been|have been) (?:{_HEDGE} )?"
    rf"(?:(?:attributed|attributable|traced|due) to|caused by|(?:the|a) (?:result|product) of) {_ART}|"
    rf"{_SUBJECT}\W+(?:{_GAP}\W+){{0,3}}(?:was|were|is|are) (?:{_HEDGE} )?(?:a|an|the planet|the star) "
    rf")(?:{_CAT_ALT})\b", re.I)
_UNRESOLVED = re.compile(
    r"\b(?:"
    r"(?:remain(?:s|ed|ing)?|still|listed|carried|classified|evaluated|categori[sz]ed|labell?ed|considered|regarded|rated|"
    r"logged|recorded)(?: (?:the|this|that|it|them|these|those)(?: \w+){0,2})? (?:as )?(?:an? )?(?:unidentified|unknown|"
    r"unexplained|unresolved)(?! (?:flying|aerial|anomalous|"
    r"submerged|aerospace|object|objects|craft|light|lights|target|targets|phenomen|ufo|uap|sighting|source|type|origin))|"
    r"(?:conclusions?|evaluation|final evaluation|assessment|explanation|identification|status)\s*[:\-–]\s*(?:unknown|"
    r"unidentified|unexplained|unresolved|none|no explanation)|"
    r"(?:object|objects|light|lights|sighting|phenomenon|phenomena|it|they|its (?:nature|identity|origin|source)|"
    r"the (?:nature|identity|origin|source) of the \w+) (?:\w+ ){0,2}(?:could|can|cannot|could not|can't|couldn't)(?: not)? be "
    r"(?:identified|explained|determined|accounted for|resolved|attributed)|"
    r"(?:no|without) (?:explanation|identification|conclusion|determination|solution) (?:was|has been|could be|can be|is) "
    r"(?:found|made|offered|reached|given|possible|available|determined)|"
    r"(?:unable|failed|fails) to (?:identify|explain) (?:the|its|their|this|these|what)\b|"
    r"(?:no|not any) (?:known|conventional|natural|plausible|satisfactory|logical|rational) explanation|"
    r"defie[sd] (?:explanation|identification|analysis)|"
    r"(?:remains?|remained|still) (?:a mystery|unexplained|unidentified|unresolved|unaccounted for)|"
    r"(?:has|have|had|was|were) (?:never|not) (?:been )?(?:identified|explained|resolved|accounted for)|"
    r"unresolved uap"
    r")\b", re.I)
_UNRESOLVED_NEG = re.compile(r"\b(?:not|never|no longer)\s+(?:remain|listed|carried|classified|considered)", re.I)
_NEG_BEFORE = re.compile(
    r"(?:\b(?:not|never|neither|nor|no|without|unlike|rather than|instead of|ruled out|rule out|rules out|eliminat\w+|"
    r"disprov\w+|if|whether|unless|hardly|doubtful|doubt(?:s|ed)?|deny|denied|impossible|excluded?|discount\w*|other than|"
    r"except|may|might|could|would|should|possibility|possibilities|chance|speculat\w+|suggest\w*|suspect\w*|"
    r"assum\w+|think|thinks|thought|believe[sd]?|supposed?|imagine[sd]?|wonder\w*|guess\w*|impressions?|(?:un)?likely that|theory|"
    r"theories|hypothes\w+|claim\w*|allege\w*|rumou?r\w*|mistaken(?:ly)? for|mistook|resembl\w+|(?:looked|look|looks) like|"
    r"similar to|as if|like)\b|n'?t\b)[^.;]{0,35}$", re.I)
_HYPOTHETICAL_AFTER = re.compile(r"^\W*(?:because|since|if|unless|would|could|might|hypothesis|theory|or\b)", re.I)


@dataclass
class Verdict:
    group: str        # "explained" | "unresolved"
    category: str     # taxonomy assessment value ("resolved_balloon"...), or "unresolved"
    start: int
    end: int
    strong: bool      # official / definite wording, not a hedge
    sentence: str


def _sentence(text: str, start: int, end: int) -> str:
    lo = max(text.rfind(".", 0, start), text.rfind("\n\n", 0, start), text.rfind("!", 0, start), text.rfind("?", 0, start)) + 1
    hi_candidates = [i for i in (text.find(".", end), text.find("\n\n", end), text.find("?", end)) if i != -1]
    hi = min(hi_candidates) + 1 if hi_candidates else len(text)
    return " ".join(text[lo:hi].split())[:400]


def _clean(text: str, m: re.Match) -> bool:
    """No question, negation, speculation or hypothetical around the match."""
    # OCR'd text breaks lines mid-sentence, so only a full stop or a blank line ends the context
    sent_start = max(text.rfind(".", 0, m.start()), text.rfind("\n\n", 0, m.start())) + 1
    before = text[max(sent_start, m.start() - 60):m.start()].replace("\n", " ")
    sent = _sentence(text, m.start(), m.end())
    if sent.endswith("?"):
        return False
    if _NEG_BEFORE.search(before):
        return False
    if _HYPOTHETICAL_AFTER.match(text[m.end():m.end() + 20]):
        return False
    return True


def find_verdicts(text: str) -> list[Verdict]:
    """Every verdict stated in ``text``, in order of appearance."""
    text = text or ""
    out: list[Verdict] = []
    for m in _EXPLAINED.finditer(text):
        if not _clean(text, m):
            continue
        cat = next(k for k, _ in CATEGORIES if m.group(k))
        strong = not re.match(r"[^.]*\b(?:probabl|possibl|likely)", m.group(0), re.I)
        out.append(Verdict("explained", cat, m.start(), m.end(), strong, _sentence(text, m.start(), m.end())))
    for m in _UNRESOLVED.finditer(text):
        if not _clean(text, m):
            continue
        if _UNRESOLVED_NEG.search(text[max(0, m.start() - 12):m.end()]):
            continue
        strong = not re.match(r"(?:no|not any) \w+ explanation|defie|(?:has|have|had|was|were) (?:never|not)", m.group(0), re.I)
        out.append(Verdict("unresolved", "unresolved", m.start(), m.end(), strong, _sentence(text, m.start(), m.end())))
    # one verdict per span
    out.sort(key=lambda v: (v.start, -v.end))
    kept: list[Verdict] = []
    for v in out:
        if kept and v.start < kept[-1].end:
            continue
        kept.append(v)
    return kept


def verdict_of(text: str) -> Verdict | None:
    """The verdict a passage settles on: official wording beats a hedge, and
    among equals the last one stated wins."""
    vs = find_verdicts(text)
    if not vs:
        return None
    return max(vs, key=lambda v: (v.strong, v.start))


def verdict_label(category: str) -> str:
    from ..classify.taxonomy import label

    return label("assessment", category)
