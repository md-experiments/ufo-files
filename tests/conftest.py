import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated data dir + SQLite DB per test, no LLM, no scheduler."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # set TEST_DATABASE_URL (e.g. postgresql://...) to run against Postgres
    if os.environ.get("TEST_DATABASE_URL"):
        monkeypatch.setenv("DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    else:
        monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("REQUEST_ARCHIVE", "false")
    from ufo import config, db

    config.reset_settings()
    db.reset_engine()
    db.Base.metadata.drop_all(db.get_engine())
    db.init_db()
    yield tmp_path
    db.reset_engine()
    config.reset_settings()


def make_pdf(path: Path, text_pages: list[str], scanned_pages: list[str] = ()) -> Path:
    """A PDF with real text pages followed by image-only ("scanned") pages."""
    import pymupdf

    doc = pymupdf.open()
    for t in text_pages:
        page = doc.new_page()
        page.insert_text((72, 72), t, fontsize=12)
    for t in scanned_pages:
        tmp = pymupdf.open()
        p = tmp.new_page()
        p.insert_text((72, 100), t, fontsize=22)
        pix = p.get_pixmap(dpi=200)
        page = doc.new_page()
        page.insert_image(page.rect, pixmap=pix)
    doc.save(str(path))
    return path
