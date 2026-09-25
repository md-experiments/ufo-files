"""Places mentioned in report text: U.S. states and countries/regions."""
from __future__ import annotations

import re

# key -> (name, extra regex alternatives, tile-grid row, col)
# The tile grid places each state in one square roughly where it sits on a map.
STATES: dict[str, tuple[str, str, int, int]] = {
    "AK": ("Alaska", "", 0, 0), "ME": ("Maine", "", 0, 10),
    "VT": ("Vermont", "", 1, 9), "NH": ("New Hampshire", r"N\. ?H\.", 1, 10),
    "WA": ("Washington State", r"Wash\.? State", 2, 0), "ID": ("Idaho", "", 2, 1),
    "MT": ("Montana", r"Mont\.", 2, 2), "ND": ("North Dakota", r"N\. ?Dak\.?", 2, 3),
    "MN": ("Minnesota", r"Minn\.", 2, 4), "IL": ("Illinois", r"Ill\.", 2, 5),
    "WI": ("Wisconsin", r"Wisc?\.", 2, 6), "MI": ("Michigan", r"Mich\.", 2, 7),
    "NY": ("New York", r"N\. ?Y\.", 2, 8), "RI": ("Rhode Island", "", 2, 9),
    "MA": ("Massachusetts", r"Mass\.", 2, 10),
    "OR": ("Oregon", r"Ore\.", 3, 0), "NV": ("Nevada", r"Nev\.", 3, 1), "WY": ("Wyoming", r"Wyo\.", 3, 2),
    "SD": ("South Dakota", r"S\. ?Dak\.?", 3, 3), "IA": ("Iowa", "", 3, 4), "IN": ("Indiana", r"Ind\.", 3, 5),
    "OH": ("Ohio", "", 3, 6), "PA": ("Pennsylvania", r"Penna?\.", 3, 7), "NJ": ("New Jersey", r"N\. ?J\.", 3, 8),
    "CT": ("Connecticut", r"Conn\.", 3, 9),
    "CA": ("California", r"Calif\.?", 4, 0), "UT": ("Utah", "", 4, 1), "CO": ("Colorado", r"Colo\.", 4, 2),
    "NE": ("Nebraska", r"Nebr?\.", 4, 3), "MO": ("Missouri", "", 4, 4), "KY": ("Kentucky", r"Ky\.", 4, 5),
    "WV": ("West Virginia", r"W\. ?Va\.", 4, 6), "VA": ("Virginia", r"(?<!West )Va\.", 4, 7),
    "MD": ("Maryland", r"Md\.", 4, 8), "DE": ("Delaware", r"Del\.", 4, 9),
    "AZ": ("Arizona", r"Ariz\.", 5, 1), "NM": ("New Mexico", r"N\. ?Mex\.?|N\. ?M\.", 5, 2),
    "KS": ("Kansas", r"Kans\.", 5, 3), "AR": ("Arkansas", r"Ark\.", 5, 4), "TN": ("Tennessee", r"Tenn\.", 5, 5),
    "NC": ("North Carolina", r"N\. ?C\.", 5, 6), "SC": ("South Carolina", r"S\. ?C\.", 5, 7),
    "DC": ("Washington, D.C.", "", 5, 8),
    "OK": ("Oklahoma", r"Okla\.", 6, 3), "LA": ("Louisiana", r"(?<![A-Za-z] )La\.", 6, 4),
    "MS": ("Mississippi", r"Miss\.", 6, 5), "AL": ("Alabama", r"Ala\.", 6, 6), "GA": ("Georgia", r"(?<!Republic of )Ga\.", 6, 7),
    "HI": ("Hawaii", r"Hawaiian Islands|Oahu|Honolulu", 7, 0), "TX": ("Texas", r"Tex\.", 7, 3), "FL": ("Florida", r"Fla\.", 7, 8),
}

# Well-known places that identify a state without naming it
CITIES: dict[str, str] = {
    'AK': r'Anchorage|Fairbanks',
    'WA': r'Seattle|Tacoma|Spokane|Yakima|Richland|Hanford|Mount Rainier|Mt\.? Rainier|Maury Island',
    'ID': r'Boise',
    'OR': r'Portland, Ore|Portland, Oregon',
    'NV': r'Las Vegas|Nellis|Area 51|Reno|Groom Lake',
    'CA': r'Los Angeles|San Diego|San Francisco|Edwards Air Force Base|Edwards AFB|Sacramento|Oakland',
    'UT': r'Salt Lake|Tremonton',
    'CO': r'Denver|Colorado Springs|Lowry|Buckley',
    'NM': r'Roswell|Albuquerque|Los Alamos|Alamogordo|White Sands|Sandia|Kirtland|Socorro|Holloman|Walker (?:Air Force Base|AFB)',
    'AZ': r'Phoenix|Tucson|Flagstaff',
    'TX': r'Lubbock|Dallas|Houston|San Antonio|Fort Worth|El Paso|Levelland|Amarillo',
    'OK': r'Tulsa|Oklahoma City|Tinker',
    'OH': r'Wright-Patterson|Wright[- ]Patterson|Wright Field|Dayton|Cincinnati|Cleveland|Columbus, Ohio',
    'NJ': r'Fort Monmouth|Ft\.? Monmouth|Newark',
    'KY': r'Godman|Fort Knox|Louisville',
    'IL': r'Chicago',
    'MI': r'Detroit|Selfridge',
    'MA': r'Boston',
    'NY': r'New York City|Manhattan|Brooklyn|Buffalo, N',
    'FL': r'Miami|Tampa|Cape Canaveral|Cape Kennedy|Patrick (?:Air Force Base|AFB)',
    'GA': r'Atlanta|Savannah',
    'LA': r'New Orleans|Barksdale',
    'MN': r'Minneapolis',
    'WI': r'Milwaukee|Madison, Wis',
    'MO': r'St\.? Louis',
    'PA': r'Philadelphia|Pittsburgh|Harrisburg',
    'TN': r'Oak Ridge|Knoxville|Memphis|Nashville',
    'SC': r'Charleston, S',
    'MD': r'Baltimore|Aberdeen Proving',
    'VA': r'Norfolk|Langley|Quantico',
    'HI': r'Honolulu|Pearl Harbor|Hickam',
    'MT': r'Great Falls|Malmstrom',
    'ND': r'Minot|Grand Forks',
    'SD': r'Rapid City|Ellsworth',
    'WY': r'Cheyenne|Warren (?:Air Force Base|AFB)',
    'NE': r'Omaha|Offutt',
}

# "Washington" alone is almost always the capital (FBI letterheads, "Washington
# 25, D.C."), so D.C. only counts in sighting context (the 1952 radar flap).
DC_SIGHTING = r"Washington National|National Airport|Andrews (?:Air Force Base|AFB|Field)|Bolling|over (?:the )?(?:capital|Washington)|White House|the Capitol"

# Outside the U.S. (a few regional names map to the same key)
COUNTRIES: dict[str, str] = {
    "Canada": r"Canada|Canadian|Ontario|Quebec|British Columbia|Newfoundland|Labrador",
    "Mexico": r"(?<!New )Mexico|Mexican",
    "United Kingdom": r"England|United Kingdom|Britain|British Isles|Scotland|London",
    "France": r"France|French",
    "Germany": r"Germany|German",
    "Italy": r"Italy|Italian",
    "Spain": r"Spain|Spanish",
    "Greece": r"Greece|Greek",
    "Scandinavia": r"Sweden|Swedish|Norway|Norwegian|Denmark|Danish|Finland|Finnish|Scandinavia",
    "USSR / Russia": r"U\.?S\.?S\.?R\.?|Soviet|Russia|Russian",
    "Japan": r"Japan|Japanese|Okinawa",
    "Korea": r"Korea|Korean|Yellow Sea",
    "China": r"China|Chinese|East China Sea|South China Sea",
    "Iraq": r"Iraq",
    "Syria": r"Syria",
    "Iran": r"Iran(?!ian Embassy)",
    "Arabian Gulf": r"Arabian Gulf|Persian Gulf|Strait of Hormuz|Gulf of Oman",
    "Afghanistan": r"Afghanistan",
    "Brazil": r"Brazil",
    "Argentina": r"Argentina",
    "Chile": r"Chile",
    "Peru": r"Peru",
    "Australia": r"Australia",
    "Antarctica": r"Antarctic",
    "Moon / space": r"lunar surface|on the moon|low[- ]earth orbit|in orbit",
}

def _state_rx(key: str, name: str, alt: str) -> list[re.Pattern]:
    if key == "DC":
        return [re.compile(rf"(?<![A-Za-z])(?:{DC_SIGHTING})(?![A-Za-z])", re.IGNORECASE)]
    rxs = [re.compile(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", re.IGNORECASE)]
    if key in CITIES:
        rxs.append(re.compile(rf"(?<![A-Za-z])(?:{CITIES[key]})(?![A-Za-z])", re.IGNORECASE))
    if alt:  # abbreviations are case-sensitive ("Ill." is Illinois, "ill." is not)
        rxs.append(re.compile(rf"(?<![A-Za-z])(?:{alt})"))
    return rxs


_STATE_RX = {k: _state_rx(k, name, alt) for k, (name, alt, _, _) in STATES.items()}
_COUNTRY_RX = {k: re.compile(rf"(?<![A-Za-z])(?:{p})(?![A-Za-z])") for k, p in COUNTRIES.items()}


def find_places(text: str) -> list[str]:
    """Place keys mentioned in ``text``: 'US-NM', 'Iraq', ..."""
    if not text:
        return []
    found = []
    for key, rxs in _STATE_RX.items():
        if any(rx.search(text) for rx in rxs):
            found.append(f"US-{key}")
    for key, rx in _COUNTRY_RX.items():
        if rx.search(text):
            found.append(key)
    return found


def place_label(key: str) -> str:
    if key.startswith("US-"):
        return STATES[key[3:]][0]
    return key


_COMMANDS = {
    "CENTCOM": "Middle East (CENTCOM)", "INDOPACOM": "Indo-Pacific (INDOPACOM)",
    "EUCOM": "Europe (EUCOM)", "AFRICOM": "Africa (AFRICOM)", "SOUTHCOM": "Latin America (SOUTHCOM)",
}


def location_places(location: str | None) -> list[str]:
    """Place keys for a record's published incident location."""
    if not location:
        return []
    found = find_places(location)
    for cmd, label in _COMMANDS.items():
        if cmd.lower() in location.lower():
            found.append(label)
    low = location.lower()
    if "middle east" in low and not found:
        found.append("Middle East (CENTCOM)")
    if re.search(r"\bmoon\b|orbit|space", low) and "Moon / space" not in found:
        found.append("Moon / space")
    if re.search(r"(?:atlantic|pacific|indian) ocean", low):
        found.append("Open ocean")
    return found
