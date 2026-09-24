"""The Claude classifier, exercised against a mocked HTTP transport."""
import json

import httpx
import httpx2
import pytest

from ufo.classify import classify_document


def _payload():
    return {
        "summary": "An MQ-9 crew recorded two orbs off Japan.",
        "key_points": ["Two orbs", "Infrared video"],
        "document_kind": "mission_report",
        "topics": ["military_encounter", "radar_tracking"],
        "shapes": ["orb"],
        "domains": ["air"],
        "witnesses": ["military_pilot"],
        "sensors": ["infrared", "video"],
        "assessment": "unresolved",
        "programs": ["uaptf"],
        "locations": ["East China Sea"],
        "people_and_organizations": ["INDOPACOM"],
        "incident_years": [2024],
        "significance": 4,
        "significance_reason": "Multi-sensor military observation.",
    }


def test_llm_classification(env, monkeypatch):
    import anthropic

    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen["body"], seen["headers"] = body, request.headers
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
            "content": [{"type": "text", "text": json.dumps(_payload())}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10},
        })

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: real(
        api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), **kw))

    c = classify_document(record_id="DOW-UAP-PR046", title="Unresolved UAP Report, INDOPACOM, 2024",
                          description="desc", text="[Page 1]\nMISREP text", media_type="pdf",
                          agency="Department of War", location=None, incident_year=None, use_llm=True)
    assert c.classifier == "claude-opus-5"
    assert c.summary.startswith("An MQ-9")
    assert c.tags["shape"] == ["orb"]
    assert c.tags["region"] == ["asia_pacific"]  # from the model's locations when metadata has none
    assert c.tags["era"] == ["2020s"]
    assert c.significance == 4

    body = seen["body"]
    assert body["model"] == "claude-opus-5"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "MISREP text" in body["messages"][0]["content"]
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]


def test_llm_failure_falls_back_to_rules(env, monkeypatch):
    import anthropic

    def boom(**kw):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr("ufo.classify.llm.classify_llm", lambda *a, **k: boom())
    c = classify_document(record_id="X", title="Report of an orb", description="An orb.", text=None,
                          media_type="pdf", agency=None, location=None, incident_year=None, use_llm=True)
    assert c.classifier == "rules" and "llm_error" in c.details
