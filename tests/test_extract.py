import pytest

from conftest import make_pdf
from ufo.extract import extract_pdf, tesseract_available, text_quality


def test_text_pdf(env):
    pdf = make_pdf(env / "t.pdf", ["The pilot observed a spherical object over the ocean at dawn."])
    res = extract_pdf(pdf)
    assert res.page_count == 1
    assert res.pages[0].method == "text"
    assert "spherical object" in res.text


@pytest.mark.skipif(not tesseract_available(), reason="tesseract not installed")
def test_scanned_page_is_ocrd(env):
    pdf = make_pdf(env / "s.pdf", ["Typed cover page with enough words to be a real text layer here."],
                   scanned_pages=["FLYING DISC OBSERVED NEAR ROSWELL"])
    res = extract_pdf(pdf)
    assert [p.method for p in res.pages] == ["text", "ocr"]
    assert "ROSWELL" in res.pages[1].text.upper()
    assert res.pages[1].confidence and res.pages[1].confidence > 50
    assert len(res.ocr_pages) == 1


def test_ocr_disabled_leaves_scans_empty(env):
    pdf = make_pdf(env / "s.pdf", [], scanned_pages=["NOTHING TO SEE"])
    res = extract_pdf(pdf, ocr=False)
    assert res.pages[0].method == "empty"


def test_text_quality():
    assert text_quality("The object was observed by the crew.") > 0.9
    assert text_quality("~l1 [u;J0 ,,.. 1.4a ~~ #") < 0.3
