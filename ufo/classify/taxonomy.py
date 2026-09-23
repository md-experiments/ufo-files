"""The classification taxonomy shared by the rules and LLM classifiers.

Each facet maps a stable machine value to a human label. The web app groups and
filters on these facets.
"""
from __future__ import annotations

FACETS: dict[str, dict[str, str]] = {
    "kind": {
        "mission_report": "Military mission report",
        "investigation_file": "Investigation / case file",
        "intelligence_report": "Intelligence report",
        "diplomatic_cable": "Diplomatic cable",
        "scientific_study": "Scientific study / research",
        "transcript": "Transcript / debrief",
        "witness_statement": "Witness statement",
        "correspondence": "Memo / correspondence",
        "analysis": "Official analysis / assessment",
        "administrative": "Contract / administrative",
        "photo": "Photograph / image",
        "video": "Video",
        "audio": "Audio recording",
        "other": "Other",
    },
    "topic": {
        "military_encounter": "Military encounter",
        "aviation": "Pilot / aviation sighting",
        "nuclear": "Nuclear facilities & weapons",
        "space": "Space & astronauts",
        "maritime": "Maritime / undersea (USO)",
        "law_enforcement": "Law enforcement report",
        "public_sighting": "Civilian sighting",
        "drones": "Drones / swarms",
        "crash_retrieval": "Crash / debris / retrieval",
        "contact_claims": "Contact & abduction claims",
        "advanced_tech": "Advanced propulsion & physics",
        "government_program": "Government UFO program",
        "foreign_activity": "Foreign / adversary activity",
        "radar_tracking": "Radar & sensor tracking",
        "biological": "Health effects on witnesses",
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
        "military_pilot": "Military pilot / aircrew",
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
        "video": "Video / full-motion video",
        "photo": "Photograph",
        "satellite": "Satellite / space-based",
        "sonar": "Sonar",
        "radio": "Radio / signals",
    },
    "assessment": {
        "unresolved": "Unresolved",
        "resolved_balloon": "Explained: balloon",
        "resolved_aircraft": "Explained: aircraft / drone",
        "resolved_bird": "Explained: birds",
        "resolved_satellite": "Explained: satellite / debris",
        "resolved_astronomical": "Explained: astronomical",
        "resolved_artifact": "Explained: sensor / camera artifact",
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
        "aaro": "AARO",
        "apollo": "Apollo / Gemini / Mercury",
        "fbi_vault": "FBI flying-disc files",
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
        "latin_america": "Latin America & Caribbean",
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
    "program": "Program",
    "region": "Region",
    "era": "Incident era",
}


def label(facet: str, value: str) -> str:
    return FACETS.get(facet, {}).get(value, value.replace("_", " ").title())


def era_for_year(year: int | None) -> str | None:
    if not year or year < 1940:
        return None
    return f"{min(year, 2029) // 10 * 10}s"
