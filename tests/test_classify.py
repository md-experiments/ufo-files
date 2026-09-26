from ufo.classify import classify_document
from ufo.classify.rules import classify_rules, region_for_location


def test_rules_from_metadata():
    r = classify_rules(
        "DOW-UAP-PR046, Unresolved UAP Report, INDOPACOM, 2024",
        "An MQ-9 crew observed two orbs over the East China Sea on infrared sensors. The case remains unresolved.",
        None, "video", location="East China Sea", incident_year=2024,
    )
    assert r.kind == "video"
    assert r.assessment == "unresolved"
    assert "orb" in r.tags["shape"]
    assert "infrared" in r.tags["sensor"]
    assert "military_encounter" in r.tags["topic"]
    assert r.tags["region"] == ["asia_pacific"]
    assert r.tags["era"] == ["2020s"]


def test_rules_resolution_and_kind():
    r = classify_rules(
        "DOW-UAP-D102, Project Blue Book File on Tremonton Film, Utah, 1952",
        "Later assessments became more confident that the objects were seabirds reflecting sunlight. AARO Comment: film format.",
        None, "pdf", location="Tremonton, Utah", incident_year=1952,
    )
    assert r.assessment == "resolved_bird"
    assert r.kind == "investigation_file"
    assert "blue_book" in r.tags["program"]
    assert "aaro" not in r.tags["program"]  # "AARO Comment" is attribution, not subject
    assert r.tags["region"] == ["us_west"]


def test_long_text_needs_more_hits():
    body = ("routine contract language " * 4000) + " submarine submarine submarine"
    r = classify_rules("Contract", None, body, "pdf")
    assert "maritime" not in r.tags.get("topic", [])


def test_region_lookup():
    assert region_for_location("Las Vegas, Nevada") == "us_west"
    assert region_for_location("Washington, D.C.") == "us_east"
    assert region_for_location("Arabian Gulf") == "middle_east"
    assert region_for_location("Low Earth Orbit") == "space"
    assert region_for_location("USSR") == "russia"
    assert region_for_location("Atlantis") is None


def test_classify_without_llm_uses_description_as_summary(env):
    c = classify_document(record_id="X", title="Report", description="A long description. " * 50, text=None,
                          media_type="pdf", agency="FBI", location=None, incident_year=None)
    assert c.classifier == "rules"
    assert c.summary and len(c.summary) <= 610


def test_shared_collection_description_is_weak_evidence():
    desc = "FBI file on flying discs, including reports near Oak Ridge and speculation about propulsion."
    own = classify_rules("62-HQ-83894 Section 3", desc, "A farmer saw lights.", "pdf")
    shared = classify_rules("62-HQ-83894 Section 3", desc, "A farmer saw lights.", "pdf", description_shared=True)
    assert "nuclear" in own.tags["topic"]
    assert "nuclear" not in shared.tags.get("topic", [])


def test_aaro_attribution_and_entity_do_not_tag():
    r = classify_rules("LLE-UAP-PR001, Unresolved UAP Report, Colorado, 2023",
                       "A law enforcement entity reported lights. AARO assessed the case as unresolved.", None, "video")
    assert "contact_claims" not in r.tags.get("topic", [])
    assert "government_program" not in r.tags.get("topic", [])
    assert "aaro" not in r.tags.get("program", [])


def test_hoax_needs_an_actual_finding():
    assert classify_rules("DIRD", "Methods to fabricate metallic glasses.", None, "pdf").assessment != "hoax"
    assert classify_rules("Study", "Reports consistent with real objects rather than fabrications.", None, "pdf").assessment != "hoax"
    assert classify_rules("Cable", "The embassy characterized the reports as a fabrication originating in Rio.", None, "pdf").assessment == "hoax"
    assert classify_rules("Memo", "The photo was a hoax.", None, "pdf").assessment == "hoax"


def test_sensor_tags_need_real_evidence():
    # OCR noise ("ir" fragments) and "thermal air currents" are not infrared sensors
    body = ("The objects were sea gulls soaring on a thermal air current. Cy of ltr ir file. " * 40)
    r = classify_rules("Project Blue Book File on Tremonton Film, Utah, 1952", None, body, "pdf", incident_year=1952)
    assert "infrared" not in r.tags.get("sensor", [])
    # a later analysis mentioning FLIR cannot have recorded a 1952 film
    body = "The FLIR pod recorded a thermal signature. " * 40
    assert "infrared" not in classify_rules("Film, 1952", None, body, "pdf", incident_year=1952).tags.get("sensor", [])
    assert "infrared" in classify_rules("Report, 2021", None, body, "pdf", incident_year=2021).tags.get("sensor", [])
    # the publisher's own description always counts
    assert "infrared" in classify_rules("Film", "Recorded on an infrared camera.", None, "pdf", incident_year=1952).tags["sensor"]


def test_military_encounter_needs_more_than_a_branch_name():
    # an Air Force investigation of a civilian's film is not a military encounter
    desc = ("Project Blue Book was a U.S. Air Force program. The witness, a U.S. Navy Warrant Officer on leave, "
            "filmed the objects. The Air Force's assessment favored seabirds.")
    letterhead = "Wright-Patterson Air Force Base, Ohio. 4602d Air Intelligence Service Squadron. " * 40
    r = classify_rules("Project Blue Book File on Tremonton Film", desc, letterhead, "pdf")
    assert "military_encounter" not in r.tags.get("topic", [])
    for desc in ("A military platform operating in the region observed an orb.",
                 "Two military pilots reported a fast-moving object.",
                 "United States Navy Unidentified Anomalous Phenomena footage."):
        assert "military_encounter" in classify_rules("Report", desc, None, "video").tags["topic"], desc


def test_contract_paperwork_is_administrative():
    assert classify_rules("DOW-UAP-D110, AAWSAP Statement of Objectives, July 2008", None, None, "pdf").kind == "administrative"
    assert classify_rules("DOW-UAP-D112, AAWSAP Contract Modification P00001", None, None, "pdf").kind == "administrative"
    assert classify_rules("USPER Statement", "A witness narrative.", None, "pdf").kind == "witness_statement"
