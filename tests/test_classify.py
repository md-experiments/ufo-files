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
