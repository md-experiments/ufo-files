"""Database models (SQLAlchemy 2.0). Works with SQLite locally and Postgres on Railway."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterator

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Release(Base):
    """A tranche of records published on a given date by a source."""

    __tablename__ = "releases"
    __table_args__ = (UniqueConstraint("source", "release_date", name="uq_release"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    release_date: Mapped[date] = mapped_column(Date, index=True)
    number: Mapped[int | None] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(128))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    documents: Mapped[list["Document"]] = relationship(back_populates="release")


class Document(Base):
    """One released record (PDF, image, video or audio) and everything we derive from it."""

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("source", "record_id", name="uq_document"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    record_id: Mapped[str] = mapped_column(String(512), index=True)
    release_id: Mapped[int | None] = mapped_column(ForeignKey("releases.id"), index=True)

    # --- metadata from the publisher -----------------------------------
    title: Mapped[str] = mapped_column(Text)
    media_type: Mapped[str] = mapped_column(String(16), index=True)  # pdf | image | video | audio
    agency: Mapped[str | None] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text)
    incident_date: Mapped[date | None] = mapped_column(Date)
    incident_date_raw: Mapped[str | None] = mapped_column(String(255))
    incident_year: Mapped[int | None] = mapped_column(Integer, index=True)
    incident_location: Mapped[str | None] = mapped_column(Text)
    file_url: Mapped[str | None] = mapped_column(Text)
    thumb_url: Mapped[str | None] = mapped_column(Text)
    video_id: Mapped[str | None] = mapped_column(String(64))
    related_ids: Mapped[list | None] = mapped_column(JSON)
    redacted: Mapped[bool | None] = mapped_column(Boolean)
    featured: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict | None] = mapped_column(JSON)
    row_hash: Mapped[str] = mapped_column(String(64))

    # --- processing state ------------------------------------------------
    # new -> downloaded -> extracted -> classified ; or failed / unavailable / skipped
    status: Mapped[str] = mapped_column(String(32), default="new", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    fetched_via: Mapped[str | None] = mapped_column(String(32))
    file_path: Mapped[str | None] = mapped_column(Text)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(Integer)

    # --- extraction ------------------------------------------------------
    page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    char_count: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)

    # --- classification --------------------------------------------------
    classifier: Mapped[str | None] = mapped_column(String(64))  # "rules" or the LLM model id
    summary: Mapped[str | None] = mapped_column(Text)
    key_points: Mapped[list | None] = mapped_column(JSON)
    document_kind: Mapped[str | None] = mapped_column(String(64), index=True)
    assessment: Mapped[str | None] = mapped_column(String(64), index=True)
    significance: Mapped[int | None] = mapped_column(Integer)
    classification: Mapped[dict | None] = mapped_column(JSON)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime)

    release: Mapped[Release | None] = relationship(back_populates="documents")
    pages: Mapped[list["Page"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="Page.page_no"
    )
    tags: Mapped[list["Tag"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(16))  # text | ocr | empty
    confidence: Mapped[float | None] = mapped_column(Float)
    text: Mapped[str] = mapped_column(Text, default="")

    document: Mapped[Document] = relationship(back_populates="pages")


class Tag(Base):
    """A (facet, value) classification label on a document, e.g. ("shape", "orb")."""

    __tablename__ = "tags"
    __table_args__ = (UniqueConstraint("document_id", "facet", "value", name="uq_tag"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    facet: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[str] = mapped_column(String(255), index=True)

    document: Mapped[Document] = relationship(back_populates="tags")


class Observation(Base):
    """A recurring observable detail (halo, hum, smell...) found on a page.

    ``page_no`` 0 means the publisher's description of the record."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    feature: Mapped[str] = mapped_column(String(64), index=True)
    snippet: Mapped[str] = mapped_column(Text)


class Account(Base):
    """A sighting account: a paragraph (or report form) describing one
    observation, tagged with what was seen and how it behaved.

    ``tags`` are event tag keys (see ``ufo.analyze.events``); ``spans`` holds
    ``[tag, start, end]`` for the words each tag was read from."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON)
    spans: Mapped[list] = mapped_column(JSON)


class TagCache(Base):
    """LLM event tags for an account's text, so each text is sent once per
    model and tagger version. ``key`` is a SHA-256 of version, model and text."""

    __tablename__ = "tag_cache"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    observation: Mapped[bool] = mapped_column(Boolean, default=True)
    tags: Mapped[list] = mapped_column(JSON)
    spans: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Mention(Base):
    """A date or place mentioned on a sighting page (or the record's own
    incident date/location, with ``page_no`` 0)."""

    __tablename__ = "mentions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), index=True)
    page_no: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # date | place
    value: Mapped[str] = mapped_column(String(64), index=True)  # ISO date, or place key
    precision: Mapped[str | None] = mapped_column(String(16))  # day | month | year


class AnalysisResult(Base):
    """Computed cross-record analysis (clusters, waves, co-occurrence...), as JSON."""

    __tablename__ = "analysis_results"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    # refreshed every minute while the run is alive; a stale heartbeat means the
    # process died (e.g. a redeploy) and the run no longer blocks new ones
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | error
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    records_seen: Mapped[int] = mapped_column(Integer, default=0)
    new_records: Mapped[int] = mapped_column(Integer, default=0)
    updated_records: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[str | None] = mapped_column(Text)


_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        url = get_settings().database_url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 60}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


def _add_missing_columns(engine: Engine) -> None:
    """Minimal forward migration: add columns introduced after a database was
    created (all new columns are nullable), so a persistent Railway volume or
    Postgres keeps working across deploys."""
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have:
                    ddl = col.type.compile(dialect=engine.dialect)
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
