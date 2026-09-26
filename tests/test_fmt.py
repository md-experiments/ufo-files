from datetime import date

from ufo.classify.taxonomy import label
from ufo.web.fmt import date_label, description_remainder, incident_date_text


def test_dates_are_unambiguous():
    assert incident_date_text("7/2/52", date(1952, 7, 2)) == "2 Jul 1952"
    assert incident_date_text("9/22/08", None) == "22 Sep 2008"  # parsed on the fly
    assert incident_date_text("1948-1950", None, 1948) == "1948-1950"
    assert incident_date_text("October, 2023", None, 2023) == "October 2023"
    assert incident_date_text("June 1-2, 1966", None, 1966) == "June 1-2, 1966"
    assert incident_date_text("Late 2025", None, 2025) == "Late 2025"
    assert incident_date_text("1/1/68-12/31/69", None, 1968) == "1 Jan 1968 – 31 Dec 1969"
    assert incident_date_text("N/A", None, 2023) == "2023"
    assert incident_date_text(None, None, None) == "—"


def test_paperwork_gets_a_document_date():
    assert date_label("administrative") == "Document date"
    assert date_label("correspondence") == "Document date"
    assert date_label("scientific_study") == "Document date"
    assert date_label("mission_report") == "Incident date"
    assert date_label(None) == "Incident date"
    # not classified yet (file unavailable): judged from the title
    assert date_label(None, "DOW-UAP-D115, AAWSAP Contract Modification P00004, May 2010") == "Document date"
    assert date_label(None, "DOW-UAP-PR034, Unresolved UAP Report, Greece, October 2023", "video") == "Incident date"


def test_description_remainder():
    desc = "First sentence here. Second sentence.\n\nA second paragraph."
    assert description_remainder("First sentence here.", desc) == "Second sentence.\n\nA second paragraph."
    assert description_remainder("First sentence here…", desc) == "Second sentence.\n\nA second paragraph."
    assert description_remainder(desc, desc) is None  # nothing follows
    assert description_remainder("An LLM wrote this.", desc) is None  # not a prefix
    assert description_remainder(None, desc) is None


def test_agency_labels_are_shown_as_published():
    assert label("agency", "FBI") == "FBI"
    assert label("agency", "Department of War") == "Department of War"
    assert label("agency", "Executive Office of the President") == "Executive Office of the President"
    assert label("topic", "nuclear") == "Nuclear sites & weapons"
    assert label("topic", "some_new_value") == "Some new value"
