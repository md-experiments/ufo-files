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


def test_seed_roundtrip(fake, tmp_path, monkeypatch):
    from ufo import db as dbmod
    from ufo.seed import export_seed, import_seed

    files, tmp = fake
    files["https://example.gov/D1.pdf"] = make_pdf(tmp / "d1.pdf", ["Page one text."])
    FakeSource.records = [rec("D1", date(2026, 5, 8))]
    pipeline.run_pipeline()
    seed = tmp_path / "seed.json.gz"
    assert export_seed(seed) == 1

    # fresh database (new SQLite file, or emptied Postgres)
    from ufo import config

    dbmod.reset_engine()
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "fresh"))
    config.reset_settings()
    dbmod.Base.metadata.drop_all(dbmod.get_engine())
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


def test_dead_run_does_not_block(fake):
    from datetime import timedelta

    from ufo.db import PipelineRun, session_scope, utcnow

    with session_scope() as db:
        db.add(PipelineRun(status="running", started_at=utcnow() - timedelta(hours=1),
                           heartbeat_at=utcnow() - timedelta(minutes=30)))
    assert pipeline.run_pipeline() is not None
    with session_scope() as db:
        assert db.get(PipelineRun, 1).status == "error"

    with session_scope() as db:  # a live run (fresh heartbeat) does block
        db.add(PipelineRun(status="running", heartbeat_at=utcnow()))
    assert pipeline.run_pipeline() is None


def test_missing_files_recovered_from_release_bundle(fake, monkeypatch):
    import zipfile

    from ufo.db import Document, session_scope

    files, tmp = fake
    pdf = make_pdf(tmp / "inner.pdf", ["Recovered from the bundle: a cigar-shaped object."])
    bundle = tmp / "release_02_documents.zip"
    with zipfile.ZipFile(bundle, "w") as zf:
        zf.write(pdf, "release_02/documents/DOW-UAP-D017_Sandia.pdf")
    bundle_url = "https://www.war.gov/medialink/ufo/052226/release_02/release_02_document_bundle.zip"
    files[bundle_url] = bundle  # only the bundle is reachable, not the file itself
    FakeSource.bundle_urls = [bundle_url]
    FakeSource.records = [rec("D17", date(2026, 5, 22),
                              url="https://www.war.gov/medialink/ufo/052226/release_02/documents/DOW-UAP-D017_Sandia.pdf")]
    try:
        pipeline.run_pipeline()
    finally:
        FakeSource.bundle_urls = []
    with session_scope() as db:
        d = db.scalar(select(Document))
        assert d.status == "classified" and d.fetched_via == "bundle"
        assert "cigar-shaped" in d.text


def test_analysis_stage_and_patterns_pages(fake):
    from fastapi.testclient import TestClient

    from ufo.analyze import load_results
    from ufo.db import Observation, session_scope

    files, tmp = fake
    recs = []
    wave = ("On July {d}, 1947 witnesses observed a silver disc hovering silently over Roswell, New Mexico. "
            "The object made no sound, had no wings, and the observers saw it disappear at tremendous speed. "
            "Another object appeared in the sky at high altitude.")
    for i in range(12):
        url = f"https://example.gov/W{i}.pdf"
        import textwrap

        files[url] = make_pdf(tmp / f"w{i}.pdf", ["\n".join(textwrap.wrap(wave.format(d=i + 1), 70))])
        recs.append(rec(f"W{i}", date(2026, 5, 8), url=url, desc=wave.format(d=i + 1)))
    for i in range(3):
        recs.append(rec(f"M{i}", date(2026, 5, 22), media="video", url=None,
                        desc=f"An infrared sensor recorded an orb over the Arabian Gulf in 2023 (clip {i})."))
    FakeSource.records = recs
    pipeline.run_pipeline()

    with session_scope() as db:
        r = load_results(db)
        assert r["overview"]["observations"] > 0
        assert _count(Observation, feature="silent") >= 1
        years = {y["year"]: y["count"] for y in r["timeline"]["years"]}
        assert years.get(1947, 0) > 0
        assert r["map"] and "similar" in r

    from ufo.web.app import app

    with TestClient(app) as client:
        for path in ["/patterns", "/patterns/evidence?feature=silent", "/patterns/evidence?year=1947",
                     "/patterns/evidence?place=US-NM", "/api/patterns", "/releases", "/", "/documents/1"]:
            resp = client.get(path)
            assert resp.status_code == 200, (path, resp.text[-800:])
        assert "Patterns in the UFO files" in client.get("/patterns").text
        assert client.get("/patterns/evidence").status_code == 400
        cmp = client.get("/patterns/compare?a=1&b=2")
        assert cmp.status_code == 200, cmp.text[-800:]
        assert "Matching accounts" in cmp.text
        assert "Sighting accounts" in client.get("/documents/1").text  # the tagged paragraphs
        r = client.get("/api/patterns").json()
        for t in r["clusters"]:
            assert client.get(f"/patterns/types/{t['id']}").status_code == 200
        assert client.get("/patterns/types/999").status_code == 404
        assert client.get("/patterns/links").status_code == 200
        assert client.get("/patterns/compare?a=1&b=1").status_code == 404


def test_scheduler_runs_once_per_interval(fake, monkeypatch):
    from datetime import timedelta

    from ufo.db import PipelineRun, session_scope, utcnow
    from ufo.web import app as webapp

    calls = []
    monkeypatch.setattr(pipeline, "run_pipeline", lambda trigger: calls.append(trigger))
    monkeypatch.setattr(webapp._stop, "wait", lambda _t: webapp._stop.set())
    with session_scope() as db:  # finished 2 hours ago: not due with a daily interval
        db.add(PipelineRun(status="ok", started_at=utcnow() - timedelta(hours=2), finished_at=utcnow()))
    webapp._stop.clear()
    webapp._scheduler_loop()
    assert calls == []
    with session_scope() as db:  # a run cut short by a redeploy doesn't count
        db.query(PipelineRun).delete()
        db.add(PipelineRun(status="error", started_at=utcnow() - timedelta(hours=2)))
    webapp._stop.clear()
    webapp._scheduler_loop()
    assert calls == ["startup"]
    webapp._stop.clear()


def test_admin_endpoints_need_token(fake, monkeypatch):
    from fastapi.testclient import TestClient

    from ufo import config
    from ufo.web.app import app

    monkeypatch.setattr(pipeline, "run_analysis_only", lambda trigger: None)
    with TestClient(app) as client:
        assert client.post("/api/analysis/run").status_code == 401
        monkeypatch.setenv("ADMIN_TOKEN", "t0k")
        config.reset_settings()
        assert client.post("/api/analysis/run", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert client.post("/api/analysis/run", headers={"Authorization": "Bearer t0k"}).status_code == 202


def test_llm_classification_runs_in_parallel(fake, monkeypatch):
    import time

    from ufo import config

    for k, v in {"OPENAI_API_KEY": "x", "LLM_ENABLED": "true", "LLM_WORKERS": "4", "LLM_TAGGING": "false"}.items():
        monkeypatch.setenv(k, v)
    config.reset_settings()
    calls = []

    def slow(meta, text):
        calls.append(meta["record_id"])
        time.sleep(0.3)
        return None

    monkeypatch.setattr("ufo.classify.llm.classify_llm", slow)
    FakeSource.records = [rec(f"V{i}", date(2026, 5, 8), media="video", url=None, desc=f"An orb {i}.")
                          for i in range(12)]
    t = time.time()
    assert pipeline.run_pipeline()
    assert len(calls) == 12
    assert time.time() - t < 12 * 0.3  # four at a time, not one after another


def test_records_waiting_for_reclassification_stay_visible(fake):
    from sqlalchemy import select

    from ufo.db import Document, has_classification, session_scope

    FakeSource.records = [rec("V1", date(2026, 5, 8), media="video", url=None, desc="An orb over the sea.")]
    pipeline.run_pipeline()
    with session_scope() as db:
        db.scalar(select(Document)).status = "extracted"  # queued for re-classification, run interrupted
    with session_scope() as db:
        assert db.scalar(select(Document).where(has_classification())) is not None
    assert pipeline.llm_upgrade_pending()  # the scheduler picks it up
