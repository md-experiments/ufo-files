"""The LLM classifier (Claude or OpenAI) and LLM event tagging, against mocked transports."""
import json

import httpx2

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


def _use(monkeypatch, **env):
    from ufo import config

    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_ENABLED", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.reset_settings()


def test_llm_classification(env, monkeypatch):
    import anthropic

    _use(monkeypatch, ANTHROPIC_API_KEY="test")

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


def test_openai_classification(env, monkeypatch):
    import openai

    _use(monkeypatch, OPENAI_API_KEY="test")
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen["body"], seen["url"] = body, str(request.url)
        return httpx2.Response(200, json={
            "id": "resp_1", "object": "response", "created_at": 0, "model": body["model"], "status": "completed",
            "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(_payload()), "annotations": []}]}],
            "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
        })

    real = openai.OpenAI
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: real(
        api_key="test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)), **kw))
    c = classify_document(record_id="DOW-UAP-PR046", title="Unresolved UAP Report, INDOPACOM, 2024",
                          description="desc", text="[Page 1]\nMISREP text", media_type="pdf",
                          agency="Department of War", location=None, incident_year=None)
    assert c.classifier == "gpt-6-luna"
    assert c.tags["shape"] == ["orb"] and c.significance == 4
    body = seen["body"]
    assert seen["url"].endswith("/responses")
    assert body["model"] == "gpt-6-luna" and body["reasoning"] == {"effort": "none"}
    assert body["text"]["format"]["type"] == "json_schema"
    assert "MISREP text" in json.dumps(body["input"])


def test_provider_follows_available_key(env, monkeypatch):
    from ufo.config import get_settings

    _use(monkeypatch)
    assert get_settings().llm_provider is None
    _use(monkeypatch, OPENAI_API_KEY="o")
    assert (get_settings().llm_provider, get_settings().llm_model) == ("openai", "gpt-6-luna")
    _use(monkeypatch, OPENAI_API_KEY="o", ANTHROPIC_API_KEY="a")
    assert get_settings().llm_provider == "anthropic"
    _use(monkeypatch, OPENAI_API_KEY="o", ANTHROPIC_API_KEY="a", LLM_PROVIDER="openai", LLM_MODEL="gpt-5")
    assert (get_settings().llm_provider, get_settings().llm_model) == ("openai", "gpt-5")


def test_llm_event_tagging_is_cached(env, monkeypatch):
    from ufo import llm
    from ufo.analyze import llm_tags
    from ufo.analyze.events import Account
    from ufo.db import session_scope

    _use(monkeypatch, OPENAI_API_KEY="o")
    calls = []

    def fake_parse(system, prompt, schema, max_tokens=0):
        calls.append(prompt)
        n = prompt.count("<passage ")
        return schema(passages=[
            {"id": 0, "describes_observation": True,
             "details": [{"tag": "disc", "quote": "silver disc"}, {"tag": "hover", "quote": "hovered"},
                         {"tag": "silent", "quote": "without a sound"}]},
            *[{"id": i, "describes_observation": False, "details": []} for i in range(1, n)],
        ])

    monkeypatch.setattr(llm, "parse", fake_parse)
    text = "A silver disc hovered over the barn without a sound, then left. Johnny Sparks saw it too."
    form = "Sound: none. Shape: disc."
    accs = [Account(1, 0, text, ["disc", "hover", "sparks"], []), Account(2, 0, form, ["disc"], [])]
    with session_scope() as db:
        stats = llm_tags.refine(db, accs)
    assert stats["tagged"] == 2 and len(calls) == 1
    assert accs[0].tags == ["disc", "hover", "silent"]  # "Sparks" the person is gone
    assert accs[0].spans[0] == ["disc", 2, 13]
    assert accs[1].tags == []  # not an observation
    again = [Account(1, 0, text, ["disc"], [])]
    with session_scope() as db:
        assert llm_tags.refine(db, again)["cached"] == 1
    assert len(calls) == 1 and again[0].tags == ["disc", "hover", "silent"]
    assert llm_tags.tagger_id().startswith("openai:gpt-6-luna")


def test_llm_failure_falls_back_to_rules(env, monkeypatch):
    import anthropic

    def boom(**kw):
        raise anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))

    monkeypatch.setattr("ufo.classify.llm.classify_llm", lambda *a, **k: boom())
    c = classify_document(record_id="X", title="Report of an orb", description="An orb.", text=None,
                          media_type="pdf", agency=None, location=None, incident_year=None, use_llm=True)
    assert c.classifier == "rules" and "llm_error" in c.details
