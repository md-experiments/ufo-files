from datetime import date

from ufo.analyze.dates import find_dates
from ufo.analyze.features import find_observables, is_sighting_text
from ufo.analyze.places import find_places, location_places


def keys(text):
    return {h.key for h in find_observables(text)}


def test_observables_and_negation():
    assert "hum" in keys("The object gave off a low humming sound as it hovered.")
    assert "hover" in keys("The object gave off a low humming sound as it hovered.")
    silent = keys("The object made no sound and had no wings or exhaust.")
    assert "silent" in silent and "hum" not in silent and "no_wings" in silent
    assert "trail" not in keys("No exhaust trail was observed.")
    assert "trail" in keys("It left a trail of white vapor behind it.")
    assert "smell" in keys("A very strong odor of sulfur filled the air.")
    assert "em_effects" not in keys("He noted no radio interference.")
    assert "em_effects" in keys("As it passed, the car engine stalled and the headlights went out.")


def test_blank_form_fields_are_not_observations():
    form = "15. Sound None 16. Size n/s 19. Odor detected None 21. Exhaust trails N/S"
    k = keys(form)
    assert "smell" not in k and "trail" not in k and "hum" not in k


def test_word_boundaries():
    assert not keys("The extreme stability of the atmosphere encompasses spintronics research.")


def test_sighting_text():
    assert is_sighting_text("Witnesses observed three bright objects in the sky; the object disappeared at high altitude.")
    assert not is_sighting_text("The contractor shall deliver the statement of work and invoices quarterly.")


def test_dates():
    got = find_dates("Seen July 7, 1947 and 8 JUL 47, report of 7/9/47; File 62-83894; September 1952.",
                     max_date=date(2026, 9, 1))
    assert [(d.isoformat(), p) for d, p in got] == [
        ("1947-07-07", "day"), ("1947-07-08", "day"), ("1947-07-09", "day"), ("1952-09-01", "month")]
    assert find_dates("dated 2031-01-01", max_date=date(2026, 9, 1)) == []


def test_places():
    text = ("FEDERAL BUREAU OF INVESTIGATION, Washington 25, D.C. Lights over ROSWELL, NEW MEXICO; "
            "radar targets over Washington National Airport; Kenneth Arnold near Mt. Rainier; he felt ill.")
    got = find_places(text)
    assert {"US-NM", "US-DC", "US-WA"} <= set(got)
    assert "US-IL" not in got  # "ill." is not Illinois
    assert find_places("Albuquerque, New Mexico") == ["US-NM"]
    assert location_places("CENTCOM") == ["Middle East (CENTCOM)"]
    assert location_places("Low Earth Orbit") == ["Moon / space"]


def test_event_tags_read_observations_not_forms():
    from ufo.analyze.events import accounts_for, tag_account

    tags, spans = tag_account("The disc was glowing red and made no sound. It hovered, then shot straight up and "
                              "vanished instantly. There was no smell.")
    assert {"disc", "red_orange", "glow", "silent", "hover", "accelerate", "vanish"} <= set(tags)
    assert "smell" not in tags  # negated
    assert all(len(s) == 3 for s in spans)
    # printed form labels, questions and option lists are not observations
    assert tag_account("19. Sound.\na. Continuous whine or buzz.\nb. Roar, whistle, whoosh.\n")[0] == []
    assert tag_account("Did the object hover? Was there a humming sound?")[0] == []
    assert tag_account("Tactics: vertical ascent or descent, oscillating, fluttering, erratic, etc.")[0] == []
    # a page is cut into accounts; ones saying too little are dropped
    page = ("Mr. Smith observed a silver disc hovering silently over the field at night. The object then "
            "accelerated rapidly and disappeared.\n\nThe weather was clear. Nothing else was reported.")
    accs = accounts_for([(3, page)])
    assert len(accs) == 1 and accs[0].page_no == 3 and "hover" in accs[0].tags


def test_account_matching_needs_shared_behaviour():
    from ufo.analyze.signatures import Acc, pair_detail, record_similarity, sighting_types, tag_weights

    accs = [Acc(i, doc, 1, tags) for i, (doc, tags) in enumerate([
        (1, ["disc", "metallic", "oscillate", "flashing", "high_speed"]),
        (2, ["disc", "metallic", "oscillate", "flashing", "night"]),
        (3, ["disc", "metallic", "night", "pilot"]),  # shares shape and colour only
        (4, ["sphere", "glow", "hover"]), (5, ["sphere", "glow", "hover"]), (6, ["sphere", "glow", "hover", "silent"]),
        (7, ["cigar", "trail"]), (8, ["triangle", "hum"]), (9, ["oval", "dome"]), (10, ["fireball", "green"]),
    ])]
    w = tag_weights(accs)
    assert pair_detail(accs[0].tags, accs[2].tags, w) is None  # no shared behaviour
    d = pair_detail(accs[0].tags, accs[1].tags, w)
    assert d and {"oscillate", "flashing"} <= set(d["shared"])
    best = record_similarity(accs, w, {}, min_score=0.1)
    assert (1, 2) in best and (1, 3) not in best
    # same-series records are never matched
    assert (1, 2) not in record_similarity(accs, w, {1: "s", 2: "s"}, min_score=0.1)
    types = sighting_types(accs, w, min_accounts=3, min_records=3)
    assert any(set(t["signature"]) >= {"sphere", "hover"} or set(t["signature"]) >= {"glow", "hover"} for t in types)


def test_redaction_markers():
    from ufo.analyze.redaction import find_redactions

    # exemption codes stamped where text was removed, in full or short form
    assert find_redactions("Pilot (b)(6) reported (b)(1) contact; see (b)(7)(C).") == ["b6", "b1", "b7"]
    assert find_redactions("b6 b7C b6").count("b6") == 2  # several margin stamps
    assert find_redactions("form b6 only") == []  # a lone stamp could be a form field
    assert find_redactions("(b)(3) 10 USC 424 b6") == ["b3", "b6"]  # a full code vouches for the short one
    # legal citations in contracts are not redactions
    assert find_redactions("as required by paragraph (b)(1) of this clause and section 316(b)(1)") == []
    assert find_redactions("excluded from the requirements of paragraph (b) (1) of this clause") == []
    # blacked-out blocks and bracketed notes
    assert find_redactions("Name: ████████ [redacted] XXXXXXXX") == ["block", "block", "block"]
    assert find_redactions("") == []


def test_redaction_summary_tiers_and_threads():
    from collections import Counter

    from ufo.analyze.redaction import redaction_summary

    def rec(i, agency, markers, pages=4, redacted=False, tags=(), items=(), year=2022):
        return {"id": i, "record_id": f"R{i}", "title": f"Record {i}", "agency": agency, "media": "pdf",
                "year": year, "redacted": redacted, "pages": pages, "tags": set(tags), "items": set(items),
                "markers": Counter(markers)}

    recs = [rec(i, "Department of War", {"b1": 20, "b6": 5}, redacted=True, tags={"topic:military", "kind:mission_report"},
                items={"infrared"}) for i in range(10)]
    recs += [rec(10 + i, "FBI", {"b7": 1}, tags={"topic:civilian", "kind:investigation_file"}, items={"disc"}, year=1952)
             for i in range(10)]
    recs += [rec(20 + i, "NASA", {}, tags={"topic:space"}, items={"orb"}, year=1969) for i in range(10)]
    s = redaction_summary(recs)
    assert s["overview"] == {"records": 30, "flagged": 10, "with_text": 30, "marked": 20, "heavy": 10,
                             "flagged_without_markers": 0, "marked_without_flag": 10, "markers": 260}
    assert [c["key"] for c in s["codes"]] == ["b1", "b6", "b7"]
    agency = next(b for b in s["by"] if b["facet"] == "agency")
    tiers = {r["value"]: r["counts"] for r in agency["rows"]}
    assert tiers["Department of War"]["heavy"] == 10 and tiers["FBI"]["marked_light"] == 10 and tiers["NASA"]["none"] == 10
    assert s["exemptions_by_agency"][0]["agency"] == "Department of War"
    assert s["exemptions_by_agency"][0]["codes"][0]["key"] == "b1"
    heavy = s["threads"]["heavy"]
    assert heavy["n"] == 10
    assert {r["key"] for r in heavy["facets"]} >= {"kind:mission_report", "topic:military"}
    assert heavy["details"][0]["key"] == "infrared"
    assert s["top"][0]["density"] == 6.25 and s["top"][0]["codes"][0]["key"] == "b1"


def test_outcome_summary_split_and_profiles():
    from collections import Counter

    from ufo.analyze.outcomes import outcome_group, outcome_summary

    assert outcome_group("resolved_balloon") == "explained" and outcome_group("hoax") == "explained"
    assert outcome_group("unresolved") == "unresolved" and outcome_group(None) is None

    def rec(i, assessment, items, agency="FBI", year=1952, tags=()):
        return {"id": i, "record_id": f"R{i}", "title": f"Record {i}", "agency": agency, "media": "pdf", "year": year,
                "assessment": assessment, "redacted": False, "pages": 2, "tags": set(tags), "items": set(items),
                "markers": Counter()}

    recs = [rec(i, "unresolved", {"disc", "hover", "silent"}, tags={"shape:disc"}) for i in range(12)]
    recs += [rec(20 + i, "resolved_balloon", {"drift", "metallic"}, agency="Department of War", year=1966,
                 tags={"shape:orb"}) for i in range(3)]
    recs += [rec(30 + i, "not_assessed", {"drift"}, agency="NASA", year=1969) for i in range(6)]
    recs.append(rec(99, None, {"disc"}))  # unclassified: left out
    s = outcome_summary(recs, [])
    assert s["overview"]["unresolved"] == 12 and s["overview"]["explained"] == 3 and s["overview"]["not_assessed"] == 6
    assert s["overview"]["classified"] == 21 and s["overview"]["derivable"] == 0
    assert s["explanations"] == [{"key": "resolved_balloon", "label": "Explained: balloon", "count": 3}]
    agency = next(b for b in s["by"] if b["facet"] == "agency")
    row = next(r for r in agency["rows"] if r["value"] == "FBI")
    assert row["counts"] == {"unresolved": 12, "explained": 0, "not_assessed": 0} and row["link"] is None
    shape = next(b for b in s["by"] if b["facet"] == "shape")
    assert shape["rows"][0]["link"] == "shape:disc"
    unresolved = s["profiles"]["unresolved"]
    assert {r["key"] for r in unresolved["distinctive"]} == {"disc", "hover", "silent"}
    assert unresolved["distinctive"][0]["others"] == 0
    # three explained records are too few to compare: they are listed instead
    assert s["profiles"]["explained"]["distinctive"] == [] and s["profiles"]["explained"]["common"] == []
    assert len(s["explained_cases"]) == 3 and s["explained_cases"][0]["assessment"] == "Explained: balloon"


def test_close_encounter_kinds():
    from ufo.analyze.encounters import encounter_kinds, has_occupants, is_close

    # first kind: within about 150 m, or landed / on the ground / overhead
    assert is_close("The object hovered about 50 feet from the car.")
    assert is_close("It came within ten feet of the helicopter.")
    assert is_close("The disc landed in a field beside the road.")
    assert is_close("A bright light passed directly overhead.")
    assert not is_close("The object was seen at 23,000 feet above the aircraft.")  # not "000 feet above"
    assert not is_close("It stayed about 3,000 feet away throughout.")
    assert not is_close("The object never came within 500 feet of the plane.")
    assert not is_close("Three objects at high altitude moved east.")
    # third kind: occupants of the object, not of a car
    assert has_occupants("Two small beings emerged from the craft and walked toward the witness.")
    assert has_occupants("He claims to have observed occupants of saucers described as of human form.")
    assert has_occupants("Little men were seen walking away from the object.")
    assert not has_occupants("Another car stopped and its occupants said they saw it too.")
    assert not has_occupants("The occupants of another planet may be curious about us.")
    assert not has_occupants("Congress heard testimony on the search for extraterrestrial intelligence.")
    # kinds from text and tags together
    assert encounter_kinds("A disc landed in the field.", ["disc"]) == {"ce1"}
    assert encounter_kinds("The object descended near the barn.", ["descend", "em_effects"]) == {"ce1", "ce2"}
    assert encounter_kinds("Two humanoids stood beside the object.", ["disc"]) == {"ce3"}
    assert encounter_kinds("Lights moved across the sky.", ["flashing"]) == set()


def test_encounter_summary_lists_small_kinds():
    from ufo.analyze.encounters import encounter_summary

    docs = {1: {"agency": "FBI", "title": "A"}, 2: {"agency": "Department of War", "title": "B"},
            3: {"agency": "NASA", "title": "C"}}
    close = "The silver disc landed in a field about 50 feet from the witness and hummed."
    far = "A bright light crossed the sky at great speed."
    accounts = []
    for i in range(12):
        accounts.append({"id": i, "doc": 1 + i % 3, "page": 1, "tags": ["disc", "metallic", "hum", "descend"],
                         "text": close, "year": 1950 + i, "places": ["US-NM"]})
    for i in range(12, 40):
        accounts.append({"id": i, "doc": 1 + i % 3, "page": 2, "tags": ["bright", "high_speed"], "text": far,
                         "year": 1960, "places": []})
    accounts.append({"id": 40, "doc": 2, "page": 3, "tags": ["disc", "silent"],
                     "text": "Two small beings emerged from the craft.", "year": 1964, "places": ["US-CA"]})
    s = encounter_summary(accounts, docs)
    ce1, ce2, ce3 = s["kinds"]
    assert ce1["size"] == 12 and ce1["record_count"] == 3 and ce1["enough"]
    assert ce1["years"] == [1950, 1961] and ce1["decades"][0] == {"decade": "1950s", "count": 10}
    assert {r["key"] for r in ce1["distinctive"]} == {"disc", "metallic", "hum"}  # "descend" defines the kind
    assert ce1["places"] == [{"key": "US-NM", "count": 12}]
    assert len(ce1["examples"]) == 3  # one per record, different agencies first
    assert ce2["size"] == 0 and ce2["examples"] == []
    assert ce3["size"] == 1 and not ce3["enough"] and ce3["distinctive"] == [] and ce3["examples"] == [40]
    assert s["any"] == 13 and s["overlap"] == {"ce1+ce2": 0, "ce1+ce3": 0, "ce2+ce3": 0}


def test_lift_rows_compare_against_the_rest():
    from ufo.analyze.compare import lift_rows, split_by

    inside = [{"a", "b"}] * 10
    others = [{"a"}] * 20
    rows = lift_rows(inside, others, str)
    assert [(r["key"], r["count"], r["others"], r["lift"]) for r in rows] == [("b", 10, 0, 20.0)]  # "a" is no more common
    assert lift_rows(inside[:3], others, str) == []  # too few to compare
    rows = split_by([("x", "FBI"), ("x", "FBI"), ("y", "FBI"), ("y", "NASA"), ("x", None)], ["x", "y"])
    assert rows[0] == {"value": "FBI", "total": 3, "counts": {"x": 2, "y": 1}, "shares": {"x": 0.667, "y": 0.333}}


def test_verdicts_in_text():
    from ufo.analyze.verdicts import find_verdicts, verdict_of

    def groups(text):
        return [(v.group, v.category) for v in find_verdicts(text)]

    assert groups("The sighting was evaluated as a weather balloon released from Holloman.") == [("explained", "resolved_balloon")]
    assert groups("It turned out to be Venus low on the horizon.") == [("explained", "resolved_astronomical")]
    assert groups("CONCLUSION: PROBABLE AIRCRAFT") == [("explained", "resolved_aircraft")]
    assert groups("The object was a plastic balloon about 40 feet across.") == [("explained", "resolved_balloon")]
    assert groups("The case remains unresolved. Evaluation: unknown.") == [("unresolved", "unresolved")] * 2
    assert groups("The object could not be identified by the Air Force.") == [("unresolved", "unresolved")]
    assert groups("The film was listed as unidentified in the Blue Book files.") == [("unresolved", "unresolved")]
    # names, questions, negations, speculation and witness impressions are not verdicts
    assert groups("He reported an unidentified flying object over the base.") == []
    assert groups("Could it have been a balloon?") == []
    assert groups("It was not a balloon, and a meteor was ruled out.") == []
    assert groups("They didn't think the object was a drone.") == []
    assert groups("The streaks may have been caused by a jet.") == []
    assert groups("There is a possibility that the incidents were caused by meteors.") == []
    assert groups("I thought the object\nwas a kite, then I realized no kite flies that high.") == []  # OCR line break
    assert groups("The object appeared to be a plastic balloon.") == []
    assert groups("They have what appear to be jet nozzles around the rim.") == []
    assert groups("Sound: could not be determined.") == []  # a form field
    # a definite verdict beats a hedged one; the later of equals wins
    v = verdict_of("It was evaluated as a possible balloon. ATIC later listed the case as unidentified.")
    assert (v.group, v.strong) == ("unresolved", True)
    v = verdict_of("First identified as a balloon. Re-evaluated: the object was a meteor.")
    assert v.category == "resolved_astronomical" and "meteor" in v.sentence


def test_derived_verdicts_fill_gaps_and_are_marked():
    from collections import Counter

    from ufo.analyze.outcomes import derive_record, outcome_summary

    ex = {"group": "explained", "category": "resolved_balloon", "strong": True, "page": 3, "sentence": "Evaluated as a balloon."}
    un = {"group": "unresolved", "category": "unresolved", "strong": True, "page": 4, "sentence": "Listed as unidentified."}
    hedged = {"group": "explained", "category": "resolved_aircraft", "strong": False, "page": 1, "sentence": "Probably an aircraft."}
    assert derive_record([]) is None
    assert derive_record([ex])["assessment"] == "resolved_balloon"
    assert derive_record([ex, un]) is None  # an even split settles nothing
    assert derive_record([un, ex, un])["group"] == "unresolved"
    assert derive_record([hedged, un])["group"] == "unresolved"  # the definite verdict outweighs the hedge
    assert derive_record([hedged])["assessment"] == "resolved_aircraft"

    def rec(i, assessment, verdicts=(), items=("disc",)):
        return {"id": i, "record_id": f"R{i}", "title": f"Record {i}", "agency": "FBI", "media": "pdf", "year": 1952,
                "assessment": assessment, "redacted": False, "pages": 2, "tags": set(), "items": set(items),
                "markers": Counter(), "verdicts": list(verdicts)}

    recs = [rec(1, "not_assessed", [ex]), rec(2, "not_assessed", [un]), rec(3, "unresolved", [ex]), rec(4, "not_assessed")]
    accounts = [{"id": 10, "doc": 1, "tags": ["disc", "hover"], "verdict": {"group": "explained", "category": "resolved_balloon",
                                                                           "sentence": "", "source": "page"}},
                {"id": 11, "doc": 4, "tags": ["disc"], "verdict": None}]
    both = outcome_summary(recs, accounts, use_derived=True)
    assert both["overview"]["explained"] == 1 and both["overview"]["unresolved"] == 2 and both["overview"]["not_assessed"] == 1
    assert both["overview"]["derived"] == {"explained": 1, "unresolved": 1}
    assert both["overview"]["derivable"] == 3 and both["overview"]["disagree"] == 1
    assert [c["source"] for c in both["explained_cases"]] == ["derived"]
    used = {c["id"]: (c["used"], c["agrees"]) for c in both["derived_cases"]}
    assert used == {1: (True, True), 2: (True, True), 3: (False, False)}  # record 3's text contradicts the publisher
    assert both["accounts"]["explained"]["accounts"] == [10] and both["accounts"]["n"] == 1
    stated = outcome_summary(recs, accounts, use_derived=False)
    assert stated["overview"]["explained"] == 0 and stated["overview"]["not_assessed"] == 3
    assert stated["overview"]["derived"] == {} and stated["overview"]["derivable"] == 3  # still listed, not used
    assert stated["accounts"]["n"] == 0
