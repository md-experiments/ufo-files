"""Tidying of published metadata: titles, locations, dates (#14, #15, #16)."""
from ufo.sources.clean import clean_title, date_note, normalize_location, reconcile_year


def test_filename_titles_become_readable():
    assert clean_title("18_6369445_General_1948_Vol_1") == "General 1948 Vol 1"
    assert clean_title("65_HS1-834228961_62-HQ-83894_Section_009") == "62-HQ-83894 Section 009"
    assert clean_title("255_413270_UFO's_and_Defense_What_Should_we_Prepare_For") == "UFO's and Defense What Should we Prepare For"
    assert clean_title("331_120752_Numeric_Files_1944–1945_37153_German_Armament_Equipment_Documents") == \
        "Numeric Files 1944–1945 37153 German Armament Equipment Documents"
    assert clean_title("38_143685_box7_Incident_Summaries_1-100") == "Box 7 Incident Summaries 1-100"
    assert clean_title("18_100754_ General 1946-7_Vol_2") == "General 1946-7 Vol 2"
    # ordinary titles are untouched
    assert clean_title("DOW-UAP-D051, Email Correspondence, March 2023") == "DOW-UAP-D051, Email Correspondence, March 2023"
    assert clean_title("2 Objects over Dayton") == "2 Objects over Dayton"


def test_locations_are_normalised():
    assert normalize_location("Westen United States") == "Western United States"
    assert normalize_location("Low-Earth Orbit") == "Low Earth Orbit"
    assert normalize_location("Indo-PACOM") == "INDOPACOM"
    assert normalize_location("Colorado Springs, Colorado, U.S.") == "Colorado Springs, Colorado"
    assert normalize_location("CENTCOM") == normalize_location("Middle East") == "Middle East (CENTCOM)"
    assert normalize_location("Washington, D.C.") == "Washington, D.C."
    assert normalize_location("Roswell, New Mexico.") == "Roswell, New Mexico"
    assert normalize_location("  Tremonton,  Utah ") == "Tremonton, Utah"
    assert normalize_location("N/A") is None and normalize_location("") is None and normalize_location(None) is None


def test_ambiguous_dates_defer_to_the_title():
    # 3/23/26 on an email from March 2023: the title's year wins, the full date is dropped
    assert reconcile_year("3/23/26", 2026, "DOW-UAP-D051, Email Correspondence, Pacific Time Zone, March 2023", 2026) == (2023, False)
    assert reconcile_year("8/31/20", 2020, "DOW-UAP-D042, Range Fouler Debrief, Japan, 2023", 2026) == (2023, False)
    # a written-out date is not ambiguous: it stands, and the record page notes the disagreement
    assert reconcile_year("October, 2023", 2023, "FBI-UAP-D022, Western United States Event, 2026", 2026) == (2023, True)
    assert date_note("FBI-UAP-D022, Western United States Event, 2026", 2023) == "the title says 2026"
    assert date_note("DOW-UAP-D102, Tremonton Film, Utah, 1952", 1952) is None
    # no date, and the title names only the release year: that is the paperwork's year, not an incident's
    assert reconcile_year("N/A", None, "DOW-UAP-PR049, Unresolved UAP Report, Department of the Army, 2026", 2026) == (None, True)
    assert reconcile_year("N/A", None, "Something, 2025", 2026) == (2025, True)
    assert reconcile_year("7/2/52", 1952, "Blue Book File, 1952", 2026) == (1952, True)
    assert reconcile_year("", None, "Numeric Files 1944–1945", 2026) == (1944, True)
    assert reconcile_year("", None, "No year here", 2026) == (None, True)
