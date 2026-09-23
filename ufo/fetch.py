"""HTTP fetching with an Internet Archive fallback.

Government sites (war.gov, aaro.mil, ...) sit behind Akamai and often refuse
requests from cloud/datacenter IP ranges. When a direct request fails we fall
back to the Wayback Machine's raw ("id_") snapshot of the same URL, which
serves byte-identical copies of what was archived.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .config import get_settings

log = logging.getLogger(__name__)

WAYBACK = "https://web.archive.org/web/{ts}id_/{url}"


class FetchError(RuntimeError):
    pass


@dataclass
class FetchResult:
    url: str
    via: str  # "direct" | "wayback"
    content: bytes
    content_type: str


def _client() -> httpx.Client:
    s = get_settings()
    return httpx.Client(
        timeout=httpx.Timeout(s.http_timeout, connect=30),
        follow_redirects=True,
        headers={
            "User-Agent": s.user_agent,
            "Accept": "text/html,application/pdf,text/csv,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def _looks_blocked(resp: httpx.Response) -> bool:
    if resp.status_code >= 400:
        return True
    ctype = resp.headers.get("content-type", "")
    # Akamai sometimes returns an HTML "Access Denied" page with 200
    return "text/html" in ctype and b"Access Denied" in resp.content[:2000]


def _get(client: httpx.Client, url: str, retries: int = 3) -> httpx.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 ** (attempt + 1))
                continue
            return resp
        except httpx.HTTPError as exc:  # network error: retry with backoff
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise FetchError(f"GET {url} failed: {last}")


def wayback_url(url: str, when: datetime | None = None) -> str:
    ts = (when or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M%S")
    return WAYBACK.format(ts=ts, url=url)


def fetch(url: str, mode: str | None = None, expect: str | None = None) -> FetchResult:
    """Fetch ``url`` directly, falling back to the Wayback Machine.

    ``expect`` is an optional content sniff: "pdf" requires a %PDF header, so an
    HTML error page served with status 200 is not mistaken for the document.
    """
    mode = (mode or get_settings().fetch_mode).lower()
    attempts: list[tuple[str, str]] = []
    if mode in ("auto", "direct"):
        attempts.append(("direct", url))
    if mode in ("auto", "wayback"):
        attempts.append(("wayback", wayback_url(url)))

    errors = []
    with _client() as client:
        for via, target in attempts:
            try:
                resp = _get(client, target)
            except FetchError as exc:
                errors.append(f"{via}: {exc}")
                continue
            if _looks_blocked(resp):
                errors.append(f"{via}: HTTP {resp.status_code}")
                continue
            if expect == "pdf" and not resp.content.lstrip()[:5].startswith(b"%PDF"):
                errors.append(f"{via}: response is not a PDF")
                continue
            return FetchResult(url=url, via=via, content=resp.content,
                               content_type=resp.headers.get("content-type", ""))
    raise FetchError(f"could not fetch {url} ({'; '.join(errors)})")


def request_archive(url: str) -> bool:
    """Ask the Wayback Machine to capture ``url`` ("Save Page Now").

    The archive fetches from its own network, which government CDNs usually
    allow, so a file we cannot reach today becomes available via the fallback
    on a later run. Best-effort; returns True if the request was accepted.
    """
    try:
        with _client() as client:
            resp = client.get(f"https://web.archive.org/save/{url}", timeout=60)
        return resp.status_code < 400
    except httpx.HTTPError:
        return False


def download(url: str, dest: Path, expect: str | None = None) -> FetchResult:
    result = fetch(url, expect=expect)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(result.content)
    tmp.replace(dest)
    return result
