"""The HTTP client that talks to the government site and the Wayback Machine."""
from __future__ import annotations

import httpx
import pytest

from ufo import fetch as fetch_mod
from ufo.fetch import FetchError, fetch


def test_client_sends_browser_fetch_metadata():
    """Akamai in front of war.gov answers 403 unless the request carries the
    Sec-Fetch-* headers a browser sends, together with Accept-Encoding."""
    with fetch_mod._client() as client:
        req = client.build_request("GET", "https://www.war.gov/UFO/")
    assert req.headers["Sec-Fetch-Dest"] == "document"
    assert req.headers["Sec-Fetch-Mode"] == "navigate"
    assert req.headers["Sec-Fetch-Site"] == "none"
    assert "gzip" in req.headers["Accept-Encoding"]
    assert req.headers["User-Agent"]


def test_fetch_falls_back_to_wayback_when_blocked(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), dict(request.headers)))
        if request.url.host == "www.war.gov":
            return httpx.Response(403, text="<HTML><TITLE>Access Denied</TITLE></HTML>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, content=b"Title,Type\n", headers={"content-type": "text/csv"})

    real_client = fetch_mod._client

    def patched_client() -> httpx.Client:
        c = real_client()
        return httpx.Client(transport=httpx.MockTransport(handler), headers=c.headers, follow_redirects=True)

    monkeypatch.setattr(fetch_mod, "_client", patched_client)
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda *_: None)
    res = fetch("https://www.war.gov/Portals/1/Interactive/2026/UFO/uap-data.csv", mode="auto")
    assert res.via == "wayback" and res.content == b"Title,Type\n"
    assert [u.split("//")[1].split("/")[0] for u, _ in seen] == ["www.war.gov", "web.archive.org"]
    # every attempt, direct or archived, carries the browser headers
    assert all(h.get("sec-fetch-mode") == "navigate" for _, h in seen)


def test_fetch_reports_every_failed_attempt(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="denied")

    monkeypatch.setattr(fetch_mod, "_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(fetch_mod.time, "sleep", lambda *_: None)
    with pytest.raises(FetchError) as exc:
        fetch("https://www.war.gov/x.pdf", mode="auto")
    assert "direct: HTTP 403" in str(exc.value) and "wayback: HTTP 403" in str(exc.value)
