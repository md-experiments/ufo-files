"""Episodes: the taxonomy, the rules fallback, the curated and LLM tiers, the hierarchy and its map."""
import json
from datetime import date

import pytest
from sqlalchemy import select

from ufo.analyze import episodes as E
from ufo.analyze.hierarchy import hierarchy_summary, pack_circles, pack_layout


def test_taxonomy_is_consistent():
    for event, subs in E.SUBCATS.items():
        assert event in E.EVENTS
        keys = [s.key for s in subs]
        assert len(keys) == len(set(keys))
        assert keys[-1] == "other" and not subs[-1].any  # every class has a catch-all last
        for s in subs:
            assert s.any <= E.VALID_DETAILS, (event, s.key, s.any - E.VALID_DETAILS)
    assert set(E.CONTEXT).isdisjoint({t for t in E.VALID_DETAILS if t not in E.CONTEXT})
    guide = E.guide_text()
    for d in E.all_details():
        assert d in guide
    for k in E.EVENTS:
        assert f"- {k}:" in guide


def test_subcategory_priority():
    # radar outranks the shape; effects put an episode under close encounters
    assert E.subcategory_for("sighting", ["disc", "radar", "night"]) == "radar_visual"
    assert E.subcategory_for("sighting", ["disc", "night"]) == "structured"
    assert E.subcategory_for("sighting", ["night", "starlike"]) == "night_lights"
    assert E.subcategory_for("sighting", ["over_water", "pilot"]) == "water"
    assert E.subcategory_for("sighting", []) == "other"
    assert E.subcategory_for("encounter", ["landed", "em_effects"]) == "interference"
    assert E.subcategory_for("encounter", ["landed"]) == "landed"
    assert E.subcategory_for("traces", ["traces"]) == "markings"
    assert E.subcategory_for("none", ["policy", "study"]) == "policy"
    assert E.subcategory_for("nope", ["policy"]) == "other"


def test_clean_episode_validates_codes_and_pages():
    e = E.clean_episode({"pages": [3, "4", 99, -1, 3], "summary": "  A disc   hovered. ", "event": "sighting",
                         "details": ["disc", "bogus", "hover", "disc"], "year": "1952", "place": "Roswell",
                         "outcome": "maybe", "explanation": ""}, valid_pages={0, 3, 4})
    assert e.pages == [3, 4] and e.summary == "A disc hovered." and e.details == ["disc", "hover"]
    assert e.year == 1952 and e.place == "Roswell" and e.outcome == "not_stated" and e.explanation is None
    assert E.clean_episode({"summary": "x", "event": "spaceship"}) is None
    assert E.clean_episode({"summary": " ", "event": "sighting"}) is None
    far = E.clean_episode({"summary": "x", "event": "sighting", "year": date.today().year + 50})
    assert far.year is None
    long = E.clean_episode({"summary": "word " * 200, "event": "none"})
    assert len(long.summary) <= E.MAX_SUMMARY + 1 and long.summary.endswith("…")


def test_rules_summary_reads_like_a_sentence():
    s = E.rules_summary(["disc", "metallic", "hover", "accelerate", "silent", "daylight", "pilot", "radar"])
    assert s.startswith("A pilot saw a silvery disc-shaped object in daylight, tracked on radar.")
    assert "hovered" in s and "made no sound" in s
    s = E.rules_summary(["formation", "red_orange", "flashing", "night", "em_effects", "minutes"])
    assert "several red-orange lights at night" in s and "They were flashing" in s and "Engines or electrics failed." in s
    assert E.rules_summary([]) == "Witnesses saw an object."


def test_rules_event_class():
    assert E.rules_event(["disc", "hover"]) == "sighting"
    assert E.rules_event(["disc", "em_effects"]) == "encounter"
    assert E.rules_event(["traces", "disc"]) == "traces"
    assert E.rules_event(["sphere", "infrared"], media="video", page_no=0) == "sensor"
    assert E.rules_event(["sphere", "pilot"], media="video", page_no=0) == "sighting"


class _Acc:
    def __init__(self, page_no, tags):
        self.page_no, self.tags = page_no, tags


class _Doc:
    def __init__(self, **kw):
        self.title = "T"
        self.description = None
        self.summary = None
        self.document_kind = None
        self.media_type = "pdf"
        self.__dict__.update(kw)


def test_rules_episodes_fall_back_to_the_description():
    eps = E.rules_episodes(_Doc(), [_Acc(2, ["disc", "hover"]), _Acc(5, ["sphere", "em_effects"]), _Acc(6, [])])
    assert [(e.pages, e.event) for e in eps] == [([2], "sighting"), ([5], "encounter")]
    doc = _Doc(description="A pilot observed a bright orb over the ocean. It climbed vertically and vanished. More text.",
               media_type="video")
    eps = E.rules_episodes(doc, [])
    assert len(eps) == 1 and eps[0].pages == [0] and eps[0].event == "sighting"
    assert eps[0].summary == "A pilot observed a bright orb over the ocean. It climbed vertically and vanished."
    study = E.rules_episodes(_Doc(description="This paper reviews propulsion.", document_kind="scientific_study"), [])
    assert study[0].event == "none" and study[0].details == ["study"]
    assert E.rules_episodes(_Doc(), [])[0].event == "none"


def test_circle_packing_has_no_overlaps_and_fits():
    radii = [5, 3, 3, 2, 1, 1, 4]
    centres = pack_circles(radii, pad=0.5)
    for i in range(len(radii)):
        for j in range(i + 1, len(radii)):
            (xa, ya), (xb, yb) = centres[i], centres[j]
            assert ((xa - xb) ** 2 + (ya - yb) ** 2) ** 0.5 >= radii[i] + radii[j] + 0.5 - 1e-6
    assert pack_circles([]) == [] and pack_circles([2]) == [(0.0, 0.0)]
    eps = [{"id": i, "doc": i % 7, "event": ev, "sub": sub, "details": [], "year": None, "outcome": "not_stated", "source": "rules"}
           for i, (ev, sub) in enumerate([("sighting", "structured")] * 30 + [("sighting", "water")] * 5
                                          + [("encounter", "landed")] * 4 + [("none", "policy")] * 2)]
    m = pack_layout(eps)
    assert len(m["points"]) == len(eps) and {c["key"] for c in m["classes"]} == {"sighting", "encounter", "none"}
    assert all(0 <= p["x"] <= 1 and 0 <= p["y"] <= m["aspect"] + 1e-6 for p in m["points"])
    assert 0 < m["point"] < 0.1
    assert pack_layout([])["points"] == []


def test_hierarchy_summary_counts_nodes():
    docs = {1: {"agency": "FBI", "title": "a", "record_id": "A"}, 2: {"agency": "NASA", "title": "b", "record_id": "B"}}
    eps = [
        {"id": 1, "doc": 1, "event": "sighting", "sub": "structured", "details": ["disc", "night"], "year": 1952, "outcome": "unexplained", "source": "curated"},
        {"id": 2, "doc": 1, "event": "sighting", "sub": "water", "details": ["over_water"], "year": 1953, "outcome": "not_stated", "source": "curated"},
        {"id": 3, "doc": 2, "event": "space", "sub": "astronaut", "details": ["astronaut"], "year": 1965, "outcome": "identified", "source": "rules"},
    ]
    h = hierarchy_summary(eps, docs)
    assert h["total"] == 3 and h["records"] == 2 and h["sources"] == {"curated": 2, "rules": 1}
    sighting = next(c for c in h["classes"] if c["key"] == "sighting")
    assert sighting["count"] == 2 and [s["key"] for s in sighting["subs"]] == ["water", "structured"]
    assert sighting["subs"][1]["top_details"][0]["key"] == "night"  # the defining detail is left out
    assert sighting["examples"] == [1]  # one example per record, hand-read first
    assert h["decades"] == ["1950s", "1960s"] and len(h["map"]["points"]) == 3
    assert next(c for c in h["classes"] if c["key"] == "none")["count"] == 0


def _record(db, rid, pages, description="An orb hovered silently over the base at night and shot away.", kind=None):
    from ufo.db import Document, Page

    doc = Document(source="pursue", record_id=rid, title=rid, media_type="pdf", description=description, row_hash="h",
                   status="classified", classifier="rules", document_kind=kind, agency="FBI", incident_year=1952)
    doc.pages = [Page(page_no=i + 1, method="text", text=t) for i, t in enumerate(pages)]
    db.add(doc)
    db.flush()
    return doc.id


def test_curated_episodes_win_and_stale_text_is_counted(env, monkeypatch, tmp_path):
    from ufo.analyze.episodes import build_episodes, save_curated, reset_curated
    from ufo.analyze.extract import extract_all
    from ufo.db import Episode, session_scope

    page = ("On July 7, 1952 a farmer observed a silver disc hovering silently over his field at night. "
            "The object then accelerated rapidly and disappeared.")
    with session_scope() as db:
        a = _record(db, "CUR-1", [page])
        b = _record(db, "RULE-1", [page])
    curated_path = tmp_path / "curated.json.gz"
    monkeypatch.setattr(E, "CURATED_PATH", curated_path)
    save_curated({"CUR-1": {"text_hash": "0000000000000000", "episodes": [
        {"pages": [1, 7], "summary": "A farmer watched a silver disc hover, then race off.", "event": "sighting",
         "details": ["disc", "metallic", "hover", "accelerate", "silent", "night", "bogus"], "year": 1952,
         "place": "Kansas", "outcome": "unexplained"},
        {"pages": [1], "summary": "", "event": "sighting"},  # dropped: no summary
    ]}}, curated_path)
    reset_curated()
    with session_scope() as db:
        extract_all(db)  # the sighting accounts and date mentions the rules tier reads
    with session_scope() as db:
        stats = build_episodes(db)
        assert stats["curated"] == 1 and stats["rules"] == 1 and stats["stale"] == 1 and stats["episodes"] == 3
        row = db.scalar(select(Episode).where(Episode.document_id == a))
        assert row.source == "curated" and row.pages == [1] and row.sub == "structured" and row.place == "Kansas"
        assert "bogus" not in row.details and row.year == 1952
        # one rules episode per tagged account: the page, and the description
        rules = db.scalars(select(Episode).where(Episode.document_id == b).order_by(Episode.page_no)).all()
        assert [r.pages for r in rules] == [[0], [1]] and all(r.source == "rules" for r in rules)
        rule = rules[1]
        assert rule.summary.startswith("Witnesses saw a silvery disc-shaped object at night")
        assert rule.year == 1952 and rule.account_ids  # linked to the tagged account on that page
    reset_curated()


def test_llm_episodes_are_used_and_cached(env, monkeypatch):
    from ufo import config, llm
    from ufo.analyze import llm_episodes as L
    from ufo.analyze.episodes import build_episodes
    from ufo.db import Episode, EpisodeCache, session_scope

    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    config.reset_settings()
    assert L.episodes_enabled() and L.reader_id().startswith("openai:")
    calls = []

    def fake_parse(system, prompt, schema, max_tokens=0):
        calls.append(prompt)
        assert "EVENT CLASSES" in system and '<page no="1"' in prompt
        return schema(episodes=[L.EpisodeOut(pages=[1], summary="A crew tracked a disc on radar over the sea.",
                                             event="sighting", details=["disc", "radar", "over_water"], year=1951,
                                             place="off Korea", outcome="unexplained")])

    monkeypatch.setattr(llm, "parse", fake_parse)
    with session_scope() as db:
        _record(db, "LLM-1", ["On July 7, 1951 the crew observed a silver disc over the sea and tracked the object on radar. "
                              "The object hovered at high altitude, then sped off and disappeared from the scope."])
    with session_scope() as db:
        stats = build_episodes(db)
        assert stats["llm"] == 1 and stats["rules"] == 0
        e = db.scalar(select(Episode))
        assert e.source == "llm" and e.sub == "radar_visual" and e.place == "off Korea"
        assert db.scalar(select(EpisodeCache.record_id)) == "LLM-1"
    with session_scope() as db:
        build_episodes(db)
    assert len(calls) == 1  # the second run came from the cache

    def failing(system, prompt, schema, max_tokens=0):
        raise RuntimeError("boom")

    monkeypatch.setattr(llm, "parse", failing)
    with session_scope() as db:
        _record(db, "LLM-2", ["Witnesses saw a glowing sphere hovering at night."])
    with session_scope() as db:
        stats = build_episodes(db)
        assert stats["llm"] == 1 and stats["rules"] == 1  # the failed record keeps the rules this run
    config.reset_settings()


def test_curate_validate_and_import(env, tmp_path):
    from ufo.analyze import curate
    from ufo.db import session_scope

    with session_scope() as db:
        _record(db, "R-1", ["On July 7, 1952 a farmer observed a silver disc hovering silently over his field at night. "
                            "The object then accelerated rapidly and disappeared at high altitude."])
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "b1.json").write_text(json.dumps({
        "R-1": {"text_hash": "x", "episodes": [
            {"pages": [1, 9], "summary": "A disc hovered then shot away.", "event": "sighting", "details": ["disc", "zzz"]},
            {"pages": [1], "summary": "", "event": "sighting"}]},
        "R-9": {"episodes": []},
    }))
    (out_dir / "bad.json").write_text("{not json")
    target = tmp_path / "curated.json.gz"
    report = curate.import_output(out_dir, target)
    assert "1 records, 1 episodes" in report
    assert "unknown details ['zzz']" in report and "pages not in the record [9]" in report
    assert "unknown record id" in report and "invalid JSON" in report and "dropped" in report
    E.reset_curated()
    loaded = E.load_curated(target)
    assert loaded["R-1"]["episodes"][0]["pages"] == [1]
    E.reset_curated()
    n, b = curate.export_input(tmp_path / "inp", batch_chars=10)
    assert n == 1 and b == 1 and (tmp_path / "inp" / "GUIDE.md").exists()
    batch = json.loads((tmp_path / "inp" / "batch-001.json").read_text())
    assert batch[0]["record_id"] == "R-1" and batch[0]["pages"][0]["page"] == 0 and "disc" in batch[0]["pages"][1]["hints"]
    assert "R-1" in curate.check()
