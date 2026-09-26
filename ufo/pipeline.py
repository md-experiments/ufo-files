"""The end-to-end pipeline: discover -> download -> extract (OCR) -> classify.

Every run is incremental and idempotent. Records already processed are left
alone unless their published metadata changed, so running it on a schedule only
does work when the government publishes something new.
"""
from __future__ import annotations

import hashlib
import logging
import re
import shutil
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timedelta
from pathlib import Path
from urllib.parse import unquote

from sqlalchemy import delete, func, select

from .classify import classify_document
from .config import get_settings
from .db import Document, Page, PipelineRun, Release, Tag, init_db, session_scope, utcnow
from .extract import extract_pdf
from .fetch import FetchError, download, request_archive
from .sources import RecordInfo, Source, get_sources

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 5
STALE_RUN = timedelta(minutes=10)  # no heartbeat for this long = dead run
HEARTBEAT_SECONDS = 60
_local_lock = threading.Lock()

# a publisher description reused by this many records describes a collection,
# not the individual record
SHARED_DESCRIPTION_MIN = 4

PENDING = ("new", "downloaded", "extracted", "failed", "unavailable")


class RunLog:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, msg: str, *args) -> None:
        text = msg % args if args else msg
        log.info(text)
        self.lines.append(f"{utcnow():%H:%M:%S} {text}")

    def text(self) -> str:
        return "\n".join(self.lines[-500:])


def _safe_name(record_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", record_id)[:150]


def _file_path(doc: Document) -> Path:
    ext = Path(doc.file_url or "").suffix.lower() or ".bin"
    return get_settings().files_dir / doc.source / f"{_safe_name(doc.record_id)}{ext}"


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def sync_source(source: Source, rlog: RunLog) -> tuple[int, int, int]:
    """Upsert the source's current listing. Returns (seen, new, updated)."""
    records = source.list_records()
    new = updated = 0
    with session_scope() as db:
        releases = {r.release_date: r for r in db.scalars(select(Release).where(Release.source == source.name))}
        for d in sorted({r.release_date for r in records} - set(releases)):
            rel = Release(source=source.name, release_date=d, label="")
            db.add(rel)
            releases[d] = rel
            rlog("new release detected: %s %s", source.name, d.isoformat())
        for i, d in enumerate(sorted(releases), start=1):
            releases[d].number = i
            releases[d].label = source.release_label(i, d)
        db.flush()

        existing = {d.record_id: d for d in db.scalars(select(Document).where(Document.source == source.name))}
        for rec in records:
            doc = existing.get(rec.record_id)
            h = rec.row_hash
            if doc is None:
                doc = Document(source=source.name, record_id=rec.record_id, status="new", row_hash=h)
                _apply_record(doc, rec)
                doc.release = releases[rec.release_date]
                db.add(doc)
                new += 1
            elif doc.row_hash != h:
                file_changed = doc.file_url != rec.file_url
                _apply_record(doc, rec)
                doc.row_hash = h
                doc.release = releases[rec.release_date]
                # metadata edits only need re-classification; a new file needs everything
                doc.status = "new" if file_changed else ("extracted" if doc.status == "classified" else doc.status)
                doc.attempts = 0
                updated += 1
    rlog("%s: %d records listed, %d new, %d updated", source.name, len(records), new, updated)
    return len(records), new, updated


def _apply_record(doc: Document, rec: RecordInfo) -> None:
    doc.title = rec.title
    doc.media_type = rec.media_type
    doc.agency = rec.agency
    doc.description = rec.description
    doc.incident_date = rec.incident_date
    doc.incident_date_raw = rec.incident_date_raw
    doc.incident_year = rec.incident_year
    doc.incident_location = rec.incident_location
    doc.file_url = rec.file_url
    doc.thumb_url = rec.thumb_url
    doc.video_id = rec.video_id
    doc.related_ids = rec.related_ids
    doc.redacted = rec.redacted
    doc.featured = rec.featured
    doc.raw = rec.raw


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------

def _download(doc_id: int) -> tuple[int, str | None]:
    """Download one document's file. Returns (doc_id, error)."""
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        url, path = doc.file_url, _file_path(doc)
        if path.exists() and doc.file_sha256:
            return doc_id, None
    try:
        res = download(url, path, expect="pdf" if path.suffix == ".pdf" else None)
    except FetchError as exc:
        err = str(exc)
        if get_settings().request_archive and "wayback: HTTP 404" in err:
            if request_archive(url):
                err += " (requested an Internet Archive capture; will retry next run)"
        with session_scope() as db:
            doc = db.get(Document, doc_id)
            doc.status, doc.error = "unavailable", err[:2000]
            doc.attempts += 1
        return doc_id, err
    _mark_downloaded(doc_id, path, res.via)
    return doc_id, None


def _mark_downloaded(doc_id: int, path: Path, via: str) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        doc.status = "downloaded"
        doc.error = None
        doc.fetched_via = via
        doc.file_path = str(path)
        doc.file_size = path.stat().st_size
        doc.file_sha256 = digest.hexdigest()


def _extract(doc_id: int) -> None:
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        path = Path(doc.file_path or _file_path(doc))
    result = extract_pdf(path)
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        db.execute(delete(Page).where(Page.document_id == doc_id))
        for p in result.pages:
            db.add(Page(document_id=doc_id, page_no=p.page_no, method=p.method, confidence=p.confidence, text=p.text))
        doc.page_count = result.page_count
        doc.ocr_page_count = len(result.ocr_pages)
        doc.ocr_confidence = result.ocr_confidence
        doc.text = result.text
        doc.char_count = len(result.text)
        doc.status = "extracted"
    if not get_settings().keep_files:
        path.unlink(missing_ok=True)


def _classify(doc_id: int):
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        args = dict(
            record_id=doc.record_id,
            title=doc.title,
            description=doc.description,
            text=doc.text,
            media_type=doc.media_type,
            agency=doc.agency,
            location=doc.incident_location,
            incident_year=doc.incident_year,
            incident_date_raw=doc.incident_date_raw,
            description_shared=bool(doc.description) and (db.scalar(
                select(func.count(Document.id)).where(
                    Document.source == doc.source, Document.description == doc.description)
            ) or 0) >= SHARED_DESCRIPTION_MIN,
        )
    c = classify_document(**args)
    with session_scope() as db:
        doc = db.get(Document, doc_id)
        db.execute(delete(Tag).where(Tag.document_id == doc_id))
        tags = dict(c.tags)
        tags["kind"] = [c.kind]
        tags["assessment"] = [c.assessment]
        if doc.agency:
            tags["agency"] = [doc.agency]
        for facet, values in tags.items():
            for v in dict.fromkeys(values):
                db.add(Tag(document_id=doc_id, facet=facet, value=v))
        doc.classifier = c.classifier
        doc.summary = c.summary
        doc.key_points = c.key_points
        doc.document_kind = c.kind
        doc.assessment = c.assessment
        doc.significance = c.significance
        doc.classification = {"tags": c.tags, **c.details}
        doc.status = "classified"
        doc.error = None
        doc.processed_at = utcnow()
    return c


def process_pending(
    rlog: RunLog,
    limit: int | None = None,
    record_ids: list[str] | None = None,
    bundles: list[str] | None = None,
) -> tuple[int, int]:
    """Download, extract and classify every record that needs it.

    ``bundles`` are release zip archives to fall back on for files that could
    not be downloaded individually."""
    s = get_settings()
    with session_scope() as db:
        q = select(Document.id, Document.media_type, Document.status, Document.file_url).where(
            Document.status.in_(PENDING)
        )
        if record_ids:
            q = q.where(Document.record_id.in_(record_ids))
        rows = [r for r in db.execute(q.order_by(Document.id)).all()]
        # stop hammering records that keep failing; "unavailable" keeps being retried
        # because the file may appear later (e.g. once archived)
        attempts = dict(db.execute(select(Document.id, Document.attempts)).all())
    rows = [r for r in rows if r.status != "failed" or attempts.get(r.id, 0) < MAX_ATTEMPTS]
    if limit:
        rows = rows[:limit]
    if not rows:
        rlog("nothing to process")
        return 0, 0

    rlog("processing %d records", len(rows))
    failed: set[int] = set()
    ok = 0
    count_lock = threading.Lock()
    llm_failures: list[str] = []
    extract_lock = threading.Lock()  # OCR has its own process pool; one document at a time

    def finish(doc_id: int) -> None:
        """Extract (if needed) and classify one record, isolating failures."""
        nonlocal ok
        try:
            with session_scope() as db:
                doc = db.get(Document, doc_id)
                status, media = doc.status, doc.media_type
            if media == "pdf" and status == "downloaded":
                with extract_lock:
                    _extract(doc_id)
            elif media == "pdf" and status != "extracted":
                return  # no file (e.g. no link published)
            c = _classify(doc_id)
            err = c.details.get("llm_error") if c else None
            with count_lock:
                ok += 1
                if err:
                    llm_failures.append(err)
                    if len(llm_failures) <= 5:
                        rlog("LLM classification failed for doc %d (kept rules result): %s", doc_id, err[:200])
                if ok % 10 == 0:
                    rlog("processed %d/%d", ok, len(rows))
        except Exception as exc:
            log.exception("processing doc %d failed", doc_id)
            with count_lock:
                failed.add(doc_id)
            with session_scope() as db:
                doc = db.get(Document, doc_id)
                doc.status, doc.error = "failed", f"{type(exc).__name__}: {exc}"[:2000]
                doc.attempts += 1

    needs_file = {r.id for r in rows if r.media_type == "pdf" and r.status in ("new", "failed", "unavailable") and r.file_url}
    # Downloads run in the background from the start. Meanwhile, records that
    # need no download (videos, images, already-downloaded PDFs) are processed,
    # then each PDF is extracted and classified as soon as its file lands, so
    # the site fills up early on a fresh install. With an LLM, several records
    # are classified at once (LLM_WORKERS), since each call waits on the API.
    workers = max(1, s.llm_workers) if s.llm_available else 1
    with ThreadPoolExecutor(max_workers=max(1, s.download_workers)) as pool, \
            ThreadPoolExecutor(max_workers=workers) as work:
        if needs_file:
            rlog("downloading %d files", len(needs_file))
        if workers > 1:
            rlog("classifying with %d parallel workers", workers)
        futures = [pool.submit(_download, i) for i in sorted(needs_file)]
        jobs = [work.submit(finish, r.id) for r in rows if r.id not in needs_file]
        for fut in as_completed(futures):
            doc_id, err = fut.result()
            if err:
                with count_lock:
                    failed.add(doc_id)
                rlog("download failed for doc %d: %s", doc_id, err[:300])
            else:
                jobs.append(work.submit(finish, doc_id))
        for j in jobs:
            j.result()
    missing = [i for i in failed if i in needs_file]
    if missing and bundles:
        for doc_id in _from_bundles(missing, bundles, rlog):
            failed.discard(doc_id)
            finish(doc_id)
    if llm_failures:
        rlog("LLM classification failed for %d records; they keep the rules result", len(llm_failures))
    rlog("processed %d, failed %d", ok, len(failed))
    return ok, len(failed)


_RELEASE_NO = re.compile(r"release[_-]?0*(\d+)", re.IGNORECASE)


def _release_no(url: str) -> int | None:
    m = _RELEASE_NO.search(url or "")
    return int(m.group(1)) if m else None


def _member_key(name: str) -> str:
    return unquote(name.rsplit("/", 1)[-1]).lower()


def _from_bundles(doc_ids: list[int], bundles: list[str], rlog: RunLog) -> list[int]:
    """Extract files we could not download individually from their release's
    zip bundle. Returns the ids that were recovered."""
    with session_scope() as db:
        docs = {d.id: (d.file_url, _file_path(d)) for d in db.scalars(select(Document).where(Document.id.in_(doc_ids)))}
    by_release: dict[int, list[int]] = {}
    for doc_id, (url, _) in docs.items():
        n = _release_no(url)
        if n is not None:
            by_release.setdefault(n, []).append(doc_id)
    recovered: list[int] = []
    bundle_dir = get_settings().data_dir / "bundles"
    for n, ids in sorted(by_release.items()):
        for bundle_url in (b for b in bundles if _release_no(b) == n):
            zpath = bundle_dir / Path(unquote(bundle_url)).name
            try:
                if not zpath.exists():
                    rlog("fetching release %d bundle for %d missing files: %s", n, len(ids), bundle_url)
                    download(bundle_url, zpath)
                with zipfile.ZipFile(zpath) as zf:
                    members = {_member_key(m): m for m in zf.namelist() if not m.endswith("/")}
                    for doc_id in list(ids):
                        url, dest = docs[doc_id]
                        member = members.get(_member_key(url))
                        if not member:
                            continue
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(member) as src, dest.open("wb") as out:
                            shutil.copyfileobj(src, out, 1 << 20)
                        _mark_downloaded(doc_id, dest, "bundle")
                        recovered.append(doc_id)
                        ids.remove(doc_id)
            except (FetchError, zipfile.BadZipFile, OSError) as exc:
                rlog("bundle %s unusable: %s", bundle_url, str(exc)[:300])
                zpath.unlink(missing_ok=True)
                continue
            if not ids:
                break
    if recovered:
        rlog("recovered %d files from release bundles", len(recovered))
    if bundle_dir.exists():  # bundles are large; keep only what's needed per run
        shutil.rmtree(bundle_dir, ignore_errors=True)
    return recovered


def reset_for_reprocess(stage: str, record_ids: list[str] | None = None) -> int:
    """Mark documents to be redone from a stage: 'extract' or 'classify'."""
    target = {"extract": "downloaded", "classify": "extracted"}[stage]
    n = 0
    with session_scope() as db:
        q = select(Document).where(Document.status.in_(("classified", "extracted", "failed")))
        if record_ids:
            q = q.where(Document.record_id.in_(record_ids))
        for doc in db.scalars(q):
            if doc.media_type != "pdf":
                doc.status = "extracted"
            elif stage == "extract" and not (doc.file_path and Path(doc.file_path).exists()):
                doc.status = "new"  # file was not kept: fetch again
            else:
                doc.status = target
            doc.attempts = 0
            n += 1
    return n


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _analysis_exists() -> bool:
    from .db import AnalysisResult

    with session_scope() as db:
        return db.scalar(select(func.count()).select_from(AnalysisResult)) > 0


def _analysis_outdated() -> bool:
    from .analyze import analysis_outdated

    with session_scope() as db:
        return analysis_outdated(db)


def _needs_llm_classification(db) -> list[int]:
    """Records classified by the rules or another model while an LLM is now
    configured. Records the current model already failed on are left alone
    (reset them with ``python -m ufo reprocess classify`` to retry)."""
    s = get_settings()
    if not s.llm_available:
        return []
    out = []
    for doc_id, classifier, details in db.execute(
            select(Document.id, Document.classifier, Document.classification).where(Document.status == "classified")):
        if classifier == s.llm_model:
            continue
        if (details or {}).get("llm_error_model") == s.llm_model:
            continue
        out.append(doc_id)
    return out


def llm_upgrade_pending() -> bool:
    """Records wait for (re-)classification: newly queued ones, or ones an
    interrupted run left extracted but not yet classified."""
    with session_scope() as db:
        if db.scalar(select(func.count(Document.id)).where(Document.status.in_(("extracted", "downloaded")))):
            return True
        return bool(_needs_llm_classification(db))


def _queue_llm_classification(rlog: RunLog) -> None:
    with session_scope() as db:
        ids = _needs_llm_classification(db)
        if not ids:
            return
        for doc in db.scalars(select(Document).where(Document.id.in_(ids))):
            doc.status = "extracted"  # text stays; only classification reruns
    rlog("re-classifying %d records with %s", len(ids), get_settings().llm_model)


def run_analysis_step(rlog: RunLog) -> None:
    """Look for connections across all records (waves, recurring details,
    sighting types, links). Failures here never fail the run."""
    from .analyze import run_analysis

    try:
        s = run_analysis(progress=rlog)
        rlog("analysis: %d sighting pages, %d observations, %d waves, %d sighting types, %d connections",
             s["sighting_units"], s["observations"], s["waves"], s["clusters"], s["links"])
    except Exception as exc:
        log.exception("analysis failed")
        rlog("analysis failed: %s", exc)


def _begin_run(trigger: str) -> int | None:
    """Record a new run, unless one is alive. A run whose heartbeat stopped
    (e.g. killed by a redeploy) is closed as interrupted."""
    with session_scope() as db:
        running = db.scalar(
            select(PipelineRun).where(PipelineRun.status == "running").order_by(PipelineRun.started_at.desc())
        )
        if running and utcnow() - (running.heartbeat_at or running.started_at) < STALE_RUN:
            log.info("run %d still in progress; skipping", running.id)
            return None
        if running:
            running.status, running.finished_at = "error", utcnow()
            running.log = (running.log or "") + "\ninterrupted (no heartbeat; the service restarted)"
        run = PipelineRun(trigger=trigger)
        db.add(run)
        db.flush()
        return run.id


def _start_heartbeat(run_id: int, rlog: RunLog) -> threading.Event:
    done = threading.Event()

    def beat() -> None:
        while not done.wait(HEARTBEAT_SECONDS):
            try:
                with session_scope() as db:
                    run = db.get(PipelineRun, run_id)
                    run.heartbeat_at = utcnow()
                    run.log = rlog.text()
            except Exception:  # pragma: no cover - best effort
                log.exception("heartbeat failed")

    threading.Thread(target=beat, name="pipeline-heartbeat", daemon=True).start()
    return done


def close_stale_runs() -> None:
    """Mark runs left "running" by a previous process as interrupted."""
    with session_scope() as db:
        for run in db.scalars(select(PipelineRun).where(PipelineRun.status == "running")):
            if utcnow() - (run.heartbeat_at or run.started_at) >= STALE_RUN:
                run.status, run.finished_at = "error", utcnow()
                run.log = (run.log or "") + "\ninterrupted (no heartbeat; the service restarted)"


def run_analysis_only(trigger: str = "analysis") -> int | None:
    """Recompute the patterns as a tracked run (shown on the pipeline page with
    its progress). Returns the run id, or None if a run is in progress."""
    init_db()
    if not _local_lock.acquire(blocking=False):
        return None
    try:
        run_id = _begin_run(trigger)
        if run_id is None:
            return None
        rlog = RunLog()
        rlog("recomputing patterns")
        done = _start_heartbeat(run_id, rlog)
        status = "ok"
        try:
            run_analysis_step(rlog)
            if any("analysis failed" in line for line in rlog.lines):
                status = "error"
        finally:
            done.set()
            with session_scope() as db:
                run = db.get(PipelineRun, run_id)
                run.status, run.finished_at, run.log = status, utcnow(), rlog.text()
        return run_id
    finally:
        _local_lock.release()


def run_pipeline(trigger: str = "manual", sources: list[str] | None = None, limit: int | None = None) -> int | None:
    """Run discovery + processing. Returns the PipelineRun id, or None if a run
    is already in progress."""
    init_db()
    if not _local_lock.acquire(blocking=False):
        log.info("pipeline already running in this process")
        return None
    try:
        run_id = _begin_run(trigger)
        if run_id is None:
            return None
        rlog = RunLog()
        done = _start_heartbeat(run_id, rlog)
        seen = new = updated = 0
        status = "ok"
        processed = failed = 0
        try:
            bundles: list[str] = []
            for source in get_sources(sources):
                try:
                    a, b, c = sync_source(source, rlog)
                    bundles += getattr(source, "bundle_urls", [])
                    seen, new, updated = seen + a, new + b, updated + c
                except Exception as exc:
                    status = "error"
                    rlog("source %s failed: %s", source.name, exc)
                    log.exception("source %s failed", source.name)
            _queue_llm_classification(rlog)
            processed, failed = process_pending(rlog, limit=limit, bundles=bundles)
            if processed or new or updated or not _analysis_exists() or _analysis_outdated():
                run_analysis_step(rlog)
        except Exception as exc:
            status = "error"
            rlog("pipeline error: %s", exc)
            log.exception("pipeline error")
        finally:
            done.set()
            with session_scope() as db:
                run = db.get(PipelineRun, run_id)
                run.status = status
                run.finished_at = utcnow()
                run.records_seen, run.new_records, run.updated_records = seen, new, updated
                run.processed, run.failed = processed, failed
                run.log = rlog.text()
        return run_id
    finally:
        _local_lock.release()
