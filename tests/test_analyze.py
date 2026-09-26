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
