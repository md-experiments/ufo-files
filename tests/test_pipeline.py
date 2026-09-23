from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from conftest import make_pdf
from ufo import pipeline
from ufo.fetch import FetchError, FetchResult
from ufo.sources.base import RecordInfo, Source


class FakeSource(Source):
    name = "pursue"
    records: list[RecordInfo] = []

    def list_records(self):
        return list(self.records)


def rec(rid, release, media="pdf", url=None, desc="A pilot observed an orb over the ocean.", **kw):
    return RecordInfo(record_id=rid, title=f"{rid}, Report", media_type=media, release_date=release,
                      agency="Department of War", description=desc,
                      file_url=url or f"https://example.gov/{rid}.pdf", **kw)


@pytest.fixture()
def fake(env, monkeypatch, tmp_path):
    files = {}

    def fake_download(url, dest, expect=None):
        if url not in files:
            raise FetchError(f"could not fetch {url} (direct: HTTP 403; wayback: HTTP 404)")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(Path(files[url]).read_bytes())
        return FetchResult(url=url, via="wayback", content=b"", content_type="application/pdf")

    monkeypatch.setattr(pipeline, "download", fake_download)
    monkeypatch.setattr(pipeline, "get_sources", lambda names=None: [FakeSource()])
    FakeSource.records = []
    return files, tmp_path


def _count(model, **where):
    from ufo.db import session_scope

    with session_scope() as db:
        q = select(func.count()).select_from(model)
        for k, v in where.items():
            q = q.where(getattr(model, k) == v)
        return db.scalar(q)


def test_full_run_is_incremental(fake):
    from ufo.db import Document, PipelineRun, Release, Tag, session_scope

    files, tmp = fake
    files["https://example.gov/D1.pdf"] = make_pdf(tmp / "d1.pdf", ["Pilot saw a metallic disc. It was assessed to be a balloon."])
    FakeSource.records = [
        rec("D1", date(2026, 5, 8)),
        rec("V1", date(2026, 5, 8), media="video", url="https://www.dvidshub.net/video/1"),
        rec("D2", date(2026, 5, 22)),  # file not reachable yet
    ]
    run_id = pipeline.run_pipeline()
    assert run_id
    with session_scope() as db:
        docs = {d.record_id: d for d in db.scalars(select(Document))}
        assert docs["D1"].status == "classified" and docs["D1"].page_count == 1
        assert "metallic disc" in docs["D1"].text
        assert docs["V1"].status == "classified"  # classified from metadata only
        assert docs["D2"].status == "unavailable"
        rels = db.scalars(select(Release).order_by(Release.release_date)).all()
        assert [r.number for r in rels] == [1, 2]
        run = db.get(PipelineRun, run_id)
        assert run.status == "ok" and run.new_records == 3 and run.processed == 2 and run.failed == 1
        assert _count(Tag, facet="shape", value="orb") >= 1

    # second run: nothing new, the unavailable file is retried and now succeeds
    files["https://example.gov/D2.pdf"] = make_pdf(tmp / "d2.pdf", ["Radar tracked an object."])
    run2 = pipeline.run_pipeline()
    with session_scope() as db:
        run = db.get(PipelineRun, run2)
        assert run.new_records == 0 and run.updated_records == 0 and run.processed == 1
        assert db.scalar(select(Document).where(Document.record_id == "D2")).status == "classified"

    # a new release appears, and a published description changes
    FakeSource.records = [
        rec("D1", date(2026, 5, 8), desc="Updated: the object was a seabird."),
        rec("V1", date(2026, 5, 8), media="video", url="https://www.dvidshub.net/video/1"),
        rec("D2", date(2026, 5, 22)),
        rec("V2", date(2026, 6, 12), media="video", url="https://www.dvidshub.net/video/2"),
    ]
    run3 = pipeline.run_pipeline()
    with session_scope() as db:
        run = db.get(PipelineRun, run3)
        assert run.new_records == 1 and run.updated_records == 1 and run.processed == 2
        d1 = db.scalar(select(Document).where(Document.record_id == "D1"))
        assert d1.page_count == 1 and d1.summary.startswith("Updated")  # re-classified, not re-extracted
        assert _count(Release) == 3


def test_web_pages_render(fake):
    from fastapi.testclient import TestClient

    files, tmp = fake
    files["https://example.gov/D1.pdf"] = make_pdf(tmp / "d1.pdf", ["An orb was filmed by an MQ-9 crew."])
    FakeSource.records = [rec("D1", date(2026, 5, 8)), rec("V1", date(2026, 5, 8), media="video", url=None)]
    pipeline.run_pipeline()

    from ufo.web.app import app

    with TestClient(app) as client:
        for path in ["/", "/documents", "/documents?q=orb", "/documents?tag=shape:orb&sort=significance",
                     "/documents/1", "/releases", "/pipeline", "/api/stats", "/api/documents", "/api/documents/1",
                     "/healthz"]:
            r = client.get(path)
            assert r.status_code == 200, (path, r.text[-500:])
        assert "MQ-9" in client.get("/documents/1").text
        assert client.get("/api/documents?q=orb").json()["total"] >= 1
        assert client.get("/documents/999").status_code == 404
        assert client.post("/api/pipeline/run").status_code == 401


def test_seed_roundtrip(fake, tmp_path):
    from ufo import db as dbmod
    from ufo.seed import export_seed, import_seed

    files, tmp = fake
    files["https://example.gov/D1.pdf"] = make_pdf(tmp / "d1.pdf", ["Page one text."])
    FakeSource.records = [rec("D1", date(2026, 5, 8))]
    pipeline.run_pipeline()
    seed = tmp_path / "seed.json.gz"
    assert export_seed(seed) == 1

    # fresh database
    dbmod.reset_engine()
    from ufo import config
    import os
    os.environ["DATA_DIR"] = str(tmp_path / "fresh")
    config.reset_settings()
    dbmod.init_db()
    assert import_seed(seed) == 1
    assert import_seed(seed) == 0  # only into an empty DB
    with dbmod.session_scope() as db:
        d = db.scalar(select(dbmod.Document))
        assert d.status == "classified" and "Page one" in d.text and d.tags and d.release.number == 1
    # the next run sees no changes
    run = pipeline.run_pipeline()
    with dbmod.session_scope() as db:
        r = db.get(dbmod.PipelineRun, run)
        assert r.new_records == 0 and r.updated_records == 0
