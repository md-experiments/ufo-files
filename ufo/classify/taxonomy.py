"""The classification taxonomy shared by the rules and LLM classifiers.

Each facet maps a stable machine value to a human label. The web app groups and
filters on these facets.
"""
from __future__ import annotations

FACETS: dict[str, dict[str, str]] = {
    "kind": {
        "mission_report": "Mission report",
        "investigation_file": "Case file",
        "intelligence_report": "Intelligence report",
        "diplomatic_cable": "Diplomatic cable",
        "scientific_study": "Scientific study",
        "transcript": "Transcript / debrief",
        "witness_statement": "Witness statement",
        "correspondence": "Correspondence",
        "analysis": "Official analysis",
        "administrative": "Administrative",
        "photo": "Photograph / image",
        "video": "Video",
        "audio": "Audio recording",
        "other": "Other",
    },
    "topic": {
        "military_encounter": "Military encounter",
        "aviation": "Pilot / aviation sighting",
        "nuclear": "Nuclear sites & weapons",
        "space": "Space & astronauts",
        "maritime": "Maritime / undersea",
        "law_enforcement": "Law enforcement",
        "public_sighting": "Civilian sighting",
        "drones": "Drones / swarms",
        "crash_retrieval": "Crash & debris",
        "contact_claims": "Contact claims",
        "advanced_tech": "Advanced physics",
        "government_program": "Government UFO programs",
        "foreign_activity": "Foreign activity",
        "radar_tracking": "Radar & sensors",
        "biological": "Health effects",
    },
    "shape": {
        "orb": "Orb / sphere",
        "disc": "Disc / saucer",
        "cigar": "Cigar / cylinder",
        "triangle": "Triangle",
        "tic_tac": "Tic Tac / capsule",
        "light": "Light(s) only",
        "cube": "Cube / box",
        "fireball": "Fireball",
        "formation": "Formation / cluster",
        "irregular": "Irregular / other shape",
    },
    "domain": {
        "air": "Air",
        "space": "Space",
        "sea": "Sea surface",
        "undersea": "Undersea",
        "land": "Land / ground",
    },
    "witness": {
        "military_pilot": "Military aircrew",
        "military": "Military personnel",
        "law_enforcement": "Law enforcement",
        "civilian": "Civilian",
        "astronaut": "Astronaut",
        "scientist": "Scientist / engineer",
        "official": "Government official",
        "intelligence": "Intelligence source",
    },
    "sensor": {
        "visual": "Visual (eyewitness)",
        "radar": "Radar",
        "infrared": "Infrared / FLIR",
        "video": "Video",
        "photo": "Photograph",
        "satellite": "Satellite",
        "sonar": "Sonar",
        "radio": "Radio / signals",
    },
    "assessment": {
        "unresolved": "Unresolved",
        "resolved_balloon": "Explained: balloon",
        "resolved_aircraft": "Explained: aircraft",
        "resolved_bird": "Explained: birds",
        "resolved_satellite": "Explained: satellite",
        "resolved_astronomical": "Explained: astronomical",
        "resolved_artifact": "Explained: camera artifact",
        "resolved_other": "Explained: other",
        "hoax": "Hoax / fabrication",
        "not_assessed": "No assessment given",
    },
    "program": {
        "blue_book": "Project Blue Book",
        "sign_grudge": "Project Sign / Grudge",
        "robertson_panel": "Robertson Panel",
        "condon": "Condon Committee",
        "aawsap": "AAWSAP / AATIP",
        "uaptf": "UAP Task Force",
        "apollo": "Apollo / Gemini / Mercury",
    },
    "region": {
        "us_west": "U.S. — West",
        "us_east": "U.S. — East",
        "us_south": "U.S. — South",
        "us_midwest": "U.S. — Midwest",
        "us_other": "U.S. — unspecified",
        "middle_east": "Middle East",
        "asia_pacific": "Asia-Pacific",
        "europe": "Europe",
        "russia": "Russia / USSR",
        "latin_america": "Latin America",
        "africa": "Africa",
        "oceans": "Open ocean",
        "space": "Space / Moon",
        "other": "Other / multiple",
    },
    "era": {
        "1940s": "1940s", "1950s": "1950s", "1960s": "1960s", "1970s": "1970s",
        "1980s": "1980s", "1990s": "1990s", "2000s": "2000s", "2010s": "2010s", "2020s": "2020s",
    },
}

FACET_LABELS = {
    "kind": "Document type",
    "topic": "Topic",
    "shape": "Reported shape",
    "domain": "Domain",
    "witness": "Witness",
    "sensor": "Evidence / sensor",
    "assessment": "Official assessment",
    "program": "Program",  # AARO is left out: it reviewed nearly every record
    "region": "Region",
    "era": "Incident era",
}


def label(facet: str, value: str) -> str:
    known = FACETS.get(facet, {}).get(value)
    if known:
        return known
    # free-text values (agency names) are shown as published
    return value.replace("_", " ").title() if "_" in value else value


def era_for_year(year: int | None) -> str | None:
    if not year or year < 1940:
        return None
    return f"{min(year, 2029) // 10 * 10}s"
