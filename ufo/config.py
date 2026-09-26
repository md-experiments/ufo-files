"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None or val == "":
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    val = os.environ.get(name)
    return int(val) if val not in (None, "") else default


def _database_url(data_dir: Path) -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return f"sqlite:///{data_dir / 'ufo.db'}"
    # Railway / Heroku style URLs -> SQLAlchemy psycopg3 driver
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("DATA_DIR", "data")).resolve())
    database_url: str = ""

    # Fetching
    user_agent: str = field(default_factory=lambda: os.environ.get(
        "USER_AGENT",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36 ufo-files-pipeline",
    ))
    # direct = official site only, wayback = Internet Archive only, auto = direct then wayback
    fetch_mode: str = field(default_factory=lambda: os.environ.get("FETCH_MODE", "auto"))
    http_timeout: int = field(default_factory=lambda: _int("HTTP_TIMEOUT", 120))
    download_workers: int = field(default_factory=lambda: _int("DOWNLOAD_WORKERS", 3))
    # ask the Internet Archive to capture files we could not fetch from anywhere
    request_archive: bool = field(default_factory=lambda: _bool("REQUEST_ARCHIVE", True))
    keep_files: bool = field(default_factory=lambda: _bool("KEEP_FILES", True))

    # Extraction / OCR
    ocr_enabled: bool = field(default_factory=lambda: _bool("OCR_ENABLED", True))
    # auto = OCR pages without usable embedded text; always = OCR every page and
    # keep whichever reads better
    ocr_mode: str = field(default_factory=lambda: os.environ.get("OCR_MODE", "auto").lower())
    ocr_dpi: int = field(default_factory=lambda: _int("OCR_DPI", 300))
    ocr_lang: str = field(default_factory=lambda: os.environ.get("OCR_LANG", "eng"))
    ocr_page_timeout: int = field(default_factory=lambda: _int("OCR_PAGE_TIMEOUT", 120))
    ocr_workers: int = field(default_factory=lambda: _int("OCR_WORKERS", 2))
    # A page with fewer embedded-text characters than this is OCR'd
    ocr_min_chars: int = field(default_factory=lambda: _int("OCR_MIN_CHARS", 80))
    max_pages: int = field(default_factory=lambda: _int("MAX_PAGES_PER_DOC", 2000))

    # Classification
    # An LLM (Claude or OpenAI) refines classification and tags sighting
    # accounts when a key is set. LLM_PROVIDER=auto uses whichever key is
    # present (Anthropic first when both are); "anthropic" / "openai" force one.
    anthropic_api_key: str = field(default_factory=lambda: os.environ.get("ANTHROPIC_API_KEY", ""))
    openai_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    llm_provider_pref: str = field(default_factory=lambda: os.environ.get("LLM_PROVIDER", "auto").strip().lower())
    llm_enabled: bool = field(default_factory=lambda: _bool("LLM_ENABLED", True))
    llm_model_override: str = field(default_factory=lambda: os.environ.get("LLM_MODEL", "").strip())
    llm_tagging: bool = field(default_factory=lambda: _bool("LLM_TAGGING", True))
    llm_workers: int = field(default_factory=lambda: _int("LLM_WORKERS", 4))
    # seconds to wait for one LLM reply before giving up (the record keeps its rules result)
    llm_timeout: int = field(default_factory=lambda: _int("LLM_TIMEOUT", 180))
    # OpenAI reasoning effort; gpt-6-luna accepts none/low/medium/high/xhigh/max
    openai_reasoning_effort: str = field(default_factory=lambda: os.environ.get("OPENAI_REASONING_EFFORT", "none").strip().lower())
    llm_max_chars: int = field(default_factory=lambda: _int("LLM_MAX_CHARS", 120_000))

    # Scheduling (web process)
    scheduler_enabled: bool = field(default_factory=lambda: _bool("SCHEDULER_ENABLED", True))
    pipeline_interval_hours: float = field(default_factory=lambda: float(os.environ.get("PIPELINE_INTERVAL_HOURS", "24")))
    seed_on_startup: bool = field(default_factory=lambda: _bool("SEED_ON_STARTUP", True))
    run_on_startup: bool = field(default_factory=lambda: _bool("RUN_PIPELINE_ON_STARTUP", True))
    admin_token: str = field(default_factory=lambda: os.environ.get("ADMIN_TOKEN", ""))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "files").mkdir(exist_ok=True)
        if not self.database_url:
            self.database_url = _database_url(self.data_dir)

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def llm_provider(self) -> str | None:
        """"anthropic", "openai", or None when no LLM is configured."""
        if not self.llm_enabled:
            return None
        has = {"anthropic": bool(self.anthropic_api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
               "openai": bool(self.openai_api_key)}
        if self.llm_provider_pref in has:
            return self.llm_provider_pref if has[self.llm_provider_pref] else None
        return next((p for p in ("anthropic", "openai") if has[p]), None)

    @property
    def llm_available(self) -> bool:
        return self.llm_provider is not None

    @property
    def llm_model(self) -> str:
        if self.llm_model_override:
            return self.llm_model_override
        return DEFAULT_MODELS.get(self.llm_provider or "anthropic", DEFAULT_MODELS["anthropic"])


DEFAULT_MODELS = {"anthropic": "claude-opus-5", "openai": "gpt-6-luna"}

_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Used by tests after changing the environment."""
    global _settings
    _settings = None
