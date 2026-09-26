"""FastAPI web app: an overview of what has been released and what it contains."""
from __future__ import annotations

import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from urllib.parse import urlencode

from .. import __version__
from ..classify.taxonomy import FACET_LABELS, FACETS, label
from ..config import get_settings
from ..db import init_db, session_scope
from . import patterns as P
from . import queries as Q

log = logging.getLogger(__name__)
# uvicorn only configures its own loggers; surface the pipeline's progress in
# the service logs (e.g. Railway's log view)
if not logging.getLogger("ufo").handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("ufo").addHandler(_h)
    logging.getLogger("ufo").setLevel(logging.INFO)
HERE = Path(__file__).resolve().parent
SEED = HERE.parent.parent / "data" / "seed" / "ufo-seed.json.gz"

templates = Jinja2Templates(directory=str(HERE / "templates"))
_stop = threading.Event()


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

def _last_finished_run() -> datetime | None:
    """When the last run that got to the end started; runs cut short by a
    redeploy don't count, so they are picked up again."""
    from sqlalchemy import func, select

    from ..db import PipelineRun

    with session_scope() as db:
        return db.scalar(select(func.max(PipelineRun.started_at)).where(PipelineRun.finished_at.is_not(None)))


def _refresh_outdated_analysis() -> None:
    """Recompute patterns after an upgrade changed what the analysis stores."""
    from ..analyze import analysis_outdated, run_analysis

    try:
        with session_scope() as db:
            outdated = analysis_outdated(db)
        if outdated:
            log.info("analysis results are from an older version; recomputing")
            run_analysis()
    except Exception:
        log.exception("analysis refresh failed")


def _scheduler_loop() -> None:
    """Run the pipeline once every PIPELINE_INTERVAL_HOURS, counted from the
    last finished run, so restarts and redeploys don't cause extra runs."""
    from datetime import timedelta

    from ..db import utcnow
    from ..pipeline import run_pipeline

    s = get_settings()
    interval = timedelta(hours=max(0.25, s.pipeline_interval_hours))
    first = True
    _refresh_outdated_analysis()
    while not _stop.is_set():
        try:
            last = _last_finished_run()
        except Exception:
            log.exception("could not read the last pipeline run")
            last = None
        due = last is None or utcnow() - last >= interval
        if not due and first:
            try:  # an LLM key was just added: re-classify now rather than tomorrow
                from ..pipeline import llm_upgrade_pending

                due = llm_upgrade_pending()
            except Exception:
                log.exception("could not check for records to re-classify")
        if due and (s.run_on_startup or not first):
            try:
                run_pipeline(trigger="startup" if first else "schedule")
            except Exception:  # never let the thread die
                log.exception("scheduled pipeline run failed")
        elif first and last is not None:
            log.info("next pipeline run due at %s UTC", (last + interval).isoformat(timespec="minutes"))
        first = False
        _stop.wait(600)  # check every 10 minutes whether a run is due


def start_background_jobs() -> None:
    s = get_settings()
    init_db()
    try:
        from ..seed import import_seed

        n = import_seed(SEED) if s.seed_on_startup else 0
        if n:
            log.info("seeded database with %d records", n)
    except Exception:
        log.exception("seed import failed")
    if s.scheduler_enabled:
        threading.Thread(target=_scheduler_loop, name="pipeline-scheduler", daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Import the seed + start the scheduler off the request path so the
    # healthcheck answers immediately.
    threading.Thread(target=start_background_jobs, name="bootstrap", daemon=True).start()
    yield
    _stop.set()


app = FastAPI(title="UFO Files", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")


def _asset_version() -> str:
    """Content hash of the static assets: a new deploy with changed CSS gets a
    new URL, so browsers (phones especially) never keep a stale stylesheet."""
    import hashlib

    h = hashlib.sha256()
    for f in sorted((HERE / "static").glob("*")):
        if f.is_file():
            h.update(f.read_bytes())
    return h.hexdigest()[:10]


ASSET_VERSION = _asset_version()


@app.middleware("http")
async def _cache_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        # versioned URLs can be cached long; unversioned ones must revalidate
        versioned = request.query_params.get("v") == ASSET_VERSION
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable" if versioned else "no-cache"
    elif "text/html" in response.headers.get("content-type", ""):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------

def _fmt_int(n) -> str:
    return f"{int(n or 0):,}"


def _ago(dt: datetime | None) -> str:
    if not dt:
        return "never"
    secs = (datetime.utcnow() - dt).total_seconds()
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if secs >= size:
            k = int(secs // size)
            return f"{k} {unit}{'s' if k != 1 else ''} ago"
    return "just now"


def _date(d: date | None, fmt: str = "%b %-d, %Y") -> str:
    return d.strftime(fmt) if d else "—"


def _highlight(text: str, q: str | None, width: int = 220) -> Markup:
    """A snippet of ``text`` around the first match of ``q``, with the match marked."""
    if not text:
        return Markup("")
    if not q:
        return Markup(escape(text[:width] + ("…" if len(text) > width else "")))
    i = text.lower().find(q.lower())
    if i < 0:
        return Markup(escape(text[:width] + ("…" if len(text) > width else "")))
    start = max(0, i - width // 2)
    snippet = text[start:start + width]
    j = i - start
    out = ("…" if start else "") + str(escape(snippet[:j])) + "<mark>" + str(escape(snippet[j:j + len(q)])) + "</mark>" + str(escape(snippet[j + len(q):]))
    return Markup(out + ("…" if start + width < len(text) else ""))


def _url(path: str, **params) -> str:
    clean = {k: v for k, v in params.items() if v not in (None, "", [])}
    return path + ("?" + urlencode(clean, doseq=True) if clean else "")


templates.env.filters["n"] = _fmt_int
templates.env.filters["ago"] = _ago
templates.env.filters["d"] = _date
templates.env.globals.update(
    label=label, FACET_LABELS=FACET_LABELS, FACETS=FACETS, MEDIA_LABELS=Q.MEDIA_LABELS,
    MEDIA_ORDER=Q.MEDIA_ORDER, highlight=_highlight, url=_url, version=__version__, asset_version=ASSET_VERSION,
)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, ctx)


def _parse_tags(values: list[str]) -> list[tuple[str, str]]:
    out = []
    for v in values:
        if ":" in v:
            facet, value = v.split(":", 1)
            if facet in FACETS or facet == "agency":
                out.append((facet, value))
    return out


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with session_scope() as db:
        stats = Q.overview(db)
        latest = Q.latest_release(db)
        ctx = dict(
            stats=stats,
            releases=Q.releases(db),
            latest=latest,
            latest_highlights=Q.highlights(db, latest.id if latest else None, limit=6) if latest else [],
            topics=Q.facet_counts(db, "topic"),
            kinds=Q.facet_counts(db, "kind"),
            assessments=Q.facet_counts(db, "assessment"),
            shapes=Q.facet_counts(db, "shape"),
            witnesses=Q.facet_counts(db, "witness"),
            sensors=Q.facet_counts(db, "sensor"),
            regions=Q.facet_counts(db, "region"),
            programs=Q.facet_counts(db, "program"),
            eras=Q.era_counts(db),
            agencies=[{"value": a, "label": a, "n": n} for a, n in Q.agencies(db)],
            locations=Q.top_locations(db),
            visuals=P.releases_visuals(db),
            patterns=P.patterns_context(db),
        )
        return render(request, "index.html", **ctx)


@app.get("/documents", response_class=HTMLResponse)
def documents(
    request: Request,
    q: str | None = None,
    release: int | None = None,
    agency: str | None = None,
    media: str | None = None,
    tag: list[str] = Query(default=[]),
    sort: str = "release",
    page: int = Query(1, ge=1),
):
    tags = _parse_tags(tag)
    with session_scope() as db:
        res = Q.search(db, q=q, release=release, agency=agency, media=media, tags=tags, sort=sort, page=page)
        return render(
            request, "documents.html", res=res, q=q or "", release=release, agency=agency, media=media,
            tags=tags, tag_values=tag, sort=sort, releases=Q.releases(db), agencies=Q.agencies(db),
        )


@app.get("/documents/{doc_id}", response_class=HTMLResponse)
def document(request: Request, doc_id: int):
    with session_scope() as db:
        doc = Q.document(db, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        return render(request, "document.html", doc=doc, groups=Q.grouped_tags(doc), related=Q.related(db, doc),
                      patterns=P.document_patterns(db, doc))


@app.get("/documents/{doc_id}/text.txt", response_class=PlainTextResponse)
def document_text(doc_id: int):
    with session_scope() as db:
        doc = Q.document(db, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        head = f"{doc.title}\nSource: {doc.file_url or '-'}\n\n"
        return PlainTextResponse(head + (doc.text or ""), headers={
            "Content-Disposition": f'inline; filename="{doc.record_id.replace(chr(34), "")}.txt"'})


@app.get("/releases", response_class=HTMLResponse)
def releases_page(request: Request):
    with session_scope() as db:
        rels = Q.releases(db)
        detail = []
        for r in reversed(rels):
            detail.append({
                **r,
                "topics": Q.facet_counts(db, "topic", r["id"], limit=6),
                "kinds": Q.facet_counts(db, "kind", r["id"], limit=5),
                "agencies": Q.facet_counts(db, "agency", r["id"], limit=6),
                "highlights": Q.highlights(db, r["id"], limit=5),
            })
        return render(request, "releases.html", releases=detail, visuals=P.releases_visuals(db))


@app.get("/patterns", response_class=HTMLResponse)
def patterns_page(request: Request):
    with session_scope() as db:
        return render(request, "patterns.html", p=P.patterns_context(db))


@app.get("/patterns/evidence", response_class=HTMLResponse)
def patterns_evidence(request: Request, feature: str | None = None, year: int | None = None,
                      place: str | None = None):
    if not (feature or year or place):
        raise HTTPException(400, "choose a feature, year or place")
    with session_scope() as db:
        return render(request, "evidence.html", ev=P.evidence(db, feature, year, place))


@app.get("/patterns/links", response_class=HTMLResponse)
def patterns_links(request: Request):
    with session_scope() as db:
        return render(request, "links.html", lp=P.links_page(db))


@app.get("/patterns/types/{type_id}", response_class=HTMLResponse)
def patterns_type(request: Request, type_id: int):
    with session_scope() as db:
        t = P.sighting_type(db, type_id)
        if not t:
            raise HTTPException(404, "sighting type not found")
        return render(request, "type.html", t=t)


@app.get("/patterns/compare", response_class=HTMLResponse)
def patterns_compare(request: Request, a: int, b: int):
    with session_scope() as db:
        c = P.compare(db, a, b)
        if not c:
            raise HTTPException(404, "records not found")
        return render(request, "compare.html", c=c)


@app.get("/api/patterns")
def api_patterns():
    from ..analyze import load_results

    with session_scope() as db:
        r = load_results(db)
        r.pop("similar", None)
        r.pop("tag_weights", None)
        if "computed_at" in r:
            r["computed_at"] = r["computed_at"].isoformat()
        return r


@app.get("/pipeline", response_class=HTMLResponse)
def pipeline_page(request: Request):
    s = get_settings()
    from ..db import AnalysisResult

    with session_scope() as db:
        computed = db.get(AnalysisResult, "overview")
        return render(
            request, "pipeline.html", runs=Q.runs(db), statuses=Q.status_counts(db), stats=Q.overview(db),
            settings=s, admin_enabled=bool(s.admin_token),
            analysis_at=computed.computed_at if computed else None,
            classifier=(f'{ {"anthropic": "Claude", "openai": "OpenAI"}[s.llm_provider] } ({s.llm_model})'
                        if s.llm_available else "keyword rules"),
        )


# ---------------------------------------------------------------------------
# JSON API
# ---------------------------------------------------------------------------

def _doc_json(d, full: bool = False) -> dict:
    out = {
        "id": d.id, "record_id": d.record_id, "title": d.title, "media_type": d.media_type,
        "agency": d.agency, "release": d.release.label if d.release else None,
        "release_date": d.release.release_date.isoformat() if d.release else None,
        "incident_date": d.incident_date_raw, "incident_year": d.incident_year,
        "incident_location": d.incident_location, "file_url": d.file_url, "status": d.status,
        "page_count": d.page_count, "ocr_page_count": d.ocr_page_count, "summary": d.summary,
        "document_kind": d.document_kind, "assessment": d.assessment, "significance": d.significance,
        "tags": {},
    }
    for t in d.tags:
        out["tags"].setdefault(t.facet, []).append(t.value)
    if full:
        out.update(description=d.description, key_points=d.key_points, classification=d.classification,
                   classifier=d.classifier, related_ids=d.related_ids,
                   pages=[{"page": p.page_no, "method": p.method, "confidence": p.confidence, "text": p.text} for p in d.pages])
    return out


@app.get("/api/stats")
def api_stats():
    with session_scope() as db:
        o = Q.overview(db)
        return {
            "records": o["records"], "documents": o["documents"], "pages": o["pages"], "ocr_pages": o["ocr_pages"],
            "releases": Q.releases(db), "last_run": o["last_run"].started_at.isoformat() if o["last_run"] else None,
            "facets": {f: Q.facet_counts(db, f) for f in list(FACETS) + ["agency"]},
        }


@app.get("/api/documents")
def api_documents(
    q: str | None = None, release: int | None = None, agency: str | None = None, media: str | None = None,
    tag: list[str] = Query(default=[]), sort: str = "release", page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
):
    with session_scope() as db:
        res = Q.search(db, q=q, release=release, agency=agency, media=media, tags=_parse_tags(tag),
                       sort=sort, page=page, per_page=per_page)
        return {"total": res.total, "page": res.page, "pages": res.pages, "items": [_doc_json(d) for d in res.items]}


@app.get("/api/documents/{doc_id}")
def api_document(doc_id: int):
    with session_scope() as db:
        doc = Q.document(db, doc_id)
        if not doc:
            raise HTTPException(404, "document not found")
        return _doc_json(doc, full=True)


def _require_admin(authorization: str | None) -> None:
    token = get_settings().admin_token
    if not token or authorization != f"Bearer {token}":
        raise HTTPException(401, "set ADMIN_TOKEN and send 'Authorization: Bearer <token>'")


@app.post("/api/analysis/run")
def api_analyze(authorization: str | None = Header(default=None)):
    """Recompute the patterns (and LLM tagging of new passages) without
    checking sources. Refused while a pipeline run is in progress."""
    _require_admin(authorization)
    from ..analyze import run_analysis
    from ..pipeline import _local_lock

    if not _local_lock.acquire(blocking=False):
        raise HTTPException(409, "a pipeline run is in progress; it ends with the analysis")

    def work():
        try:
            run_analysis()
        except Exception:
            log.exception("analysis run failed")
        finally:
            _local_lock.release()

    threading.Thread(target=work, name="analysis", daemon=True).start()
    return JSONResponse({"started": True}, status_code=202)


@app.post("/api/pipeline/run")
def api_run(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    from ..pipeline import run_pipeline

    threading.Thread(target=run_pipeline, kwargs={"trigger": "api"}, daemon=True).start()
    return JSONResponse({"started": True}, status_code=202)


@app.get("/healthz")
def healthz():
    return {"ok": True, "time": time.time()}
