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
