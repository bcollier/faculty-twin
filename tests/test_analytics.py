"""Settings > Analytics: usage metering, pricing, client events, the question log, aggregation, labeling.

Models, embeddings and retrieval here are TEST FAKES (from test_api) or httpx mock transports; no network.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from test_api import TEST_FAKE_embedder, TEST_FAKE_llm, TEST_FAKE_rank, TEST_FAKE_select

# app.main first: it wires the routers that the admin modules import from.
from app.main import Retriever, app, get_completer, get_embedder, get_retriever

# isort: split
from app import admin, analytics, embed, limits, llm, logistics, pricing, supa, usage  # noqa: E402,I001
from page_source import page_source  # tests/fixtures is on sys.path (tests/conftest.py)

ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime.now(UTC).strftime("%Y-%m-%d")


@pytest.fixture(autouse=True)
def _reset_analytics():
    analytics.reset_memory()
    yield
    analytics.reset_memory()
    app.dependency_overrides.clear()


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def use_fakes(kind: str = "course_content") -> None:
    """TEST FAKE pipeline: fake retriever, embedder, and model (no network)."""

    def fake(system, user, max_tokens, provider=None, model=None):
        if system == logistics.SYSTEM_PROMPT:
            return json.dumps({"kind": kind, "reason": "fake"})
        return TEST_FAKE_llm(system, user, max_tokens, provider, model)

    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: fake


def _supabase_on(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")


# ---------------------------------------------------------------- provider usage fields

def test_anthropic_usage_counts_cache_tokens_as_input():
    data = {"usage": {"input_tokens": 100, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 5,
                      "output_tokens": 40}}
    assert usage.parse_llm_usage("anthropic", data) == (125, 40)


@pytest.mark.parametrize("provider", ["openai", "openrouter"])
def test_chat_providers_use_prompt_and_completion_tokens(provider):
    data = {"usage": {"prompt_tokens": 321, "completion_tokens": 54, "total_tokens": 375}}
    assert usage.parse_llm_usage(provider, data) == (321, 54)


@pytest.mark.parametrize("data", [{}, {"usage": None}, {"usage": {"prompt_tokens": "x"}}, None, "text"])
def test_missing_or_odd_usage_counts_zero(data):
    assert usage.parse_llm_usage("openai", data) == (0, 0)


def test_voyage_usage_and_fallback_estimate():
    assert usage.parse_embed_usage({"usage": {"total_tokens": 9}}) == 9
    assert usage.parse_embed_usage({}, ["x" * 40]) == 10


def test_complete_json_records_tokens_under_the_purpose(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def handler(request):
        return httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}], "stop_reason": "end_turn",
                                         "usage": {"input_tokens": 1200, "output_tokens": 300}})

    with usage.purpose("narration"), usage.tally() as t:
        llm.complete_json("S", "U", 100, provider="anthropic", model="claude-sonnet-5-5", client=mock_client(handler))
    base = f"usage:{TODAY}:narration:anthropic:claude-sonnet-5-5"
    assert limits.read_counter(f"{base}:in") == 1200
    assert limits.read_counter(f"{base}:out") == 300
    assert limits.read_counter(f"{base}:calls") == 1
    assert (t.tokens_in, t.tokens_out, t.calls) == (1200, 300, 1)


def test_complete_json_records_openrouter_usage_with_colon_model_ids(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 50, "completion_tokens": 7}})

    llm.complete_json("S", "U", 100, provider="openrouter", model="meta/llama-x:free", client=mock_client(handler))
    key = f"usage:{TODAY}:other:openrouter:meta/llama-x:free:in"
    assert limits.read_counter(key) == 50
    parsed = usage.parse_key(key)
    assert parsed["model"] == "meta/llama-x:free" and parsed["metric"] == "in" and parsed["purpose"] == "other"


def test_embedding_records_voyage_tokens(monkeypatch):
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")

    def handler(request):
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2], "index": 0}], "usage": {"total_tokens": 8}})

    embed.embed_question("what is k-means", client=mock_client(handler))
    assert limits.read_counter(f"embed:{TODAY}:voyage:voyage-3.5:tokens") == 8
    assert limits.read_counter(f"embed:{TODAY}:voyage:voyage-3.5:calls") == 1


def test_eval_judges_record_their_tokens(monkeypatch):
    from evals.judges import Judge

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 900, "completion_tokens": 80}})

    Judge("openai", "gpt-6-luna", client=mock_client(handler))._send("S", "U")
    assert limits.read_counter(f"usage:{TODAY}:eval_judge:openai:gpt-6-luna:in") == 900
    assert limits.read_counter(f"usage:{TODAY}:eval_judge:openai:gpt-6-luna:out") == 80


def test_sticky_purpose_wins_over_inner_tags():
    with usage.purpose("prompt_test", sticky=True), usage.purpose("narration"):
        assert usage.current_purpose() == "prompt_test"
    with usage.purpose("narration"):
        assert usage.current_purpose() == "narration"
    assert usage.current_purpose() == "other"
    with usage.purpose("not-a-purpose"):
        assert usage.current_purpose() == "other"


def test_recording_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("database down")

    monkeypatch.setattr(limits, "increment", boom)
    usage.record_llm("anthropic", "m", {"usage": {"input_tokens": 1}})  # no exception
    usage.record_tts("clone", 10)
    usage.record_event("chip_tap")


def test_records_go_to_a_thread_when_supabase_is_on(monkeypatch):
    _supabase_on(monkeypatch)
    seen: list[str] = []
    monkeypatch.setattr(limits, "increment", lambda key, amount, **k: seen.append(key) or (True, amount))
    usage.record_tts("free", 120)
    usage.flush(2.0)
    assert sorted(seen) == [f"tts:{TODAY}:free:calls", f"tts:{TODAY}:free:chars"]
    assert usage.pending_count() == 0


@pytest.mark.parametrize(
    "key, expected",
    [
        ("usage:2026-10-07:narration:anthropic:claude-sonnet-5-5:out",
         {"kind": "usage", "purpose": "narration", "provider": "anthropic", "model": "claude-sonnet-5-5", "metric": "out"}),
        ("embed:2026-10-07:voyage:voyage-3.5:tokens", {"kind": "embed", "model": "voyage-3.5", "metric": "tokens"}),
        ("tts:2026-10-07:clone:chars", {"kind": "tts", "tier": "clone", "metric": "chars"}),
        ("event:2026-10-07:chip_tap", {"kind": "event", "name": "chip_tap"}),
        ("faq:2026-10-07:meeting", {"kind": "faq", "name": "meeting"}),
    ],
)
def test_counter_keys_parse(key, expected):
    parsed = usage.parse_key(key)
    assert parsed["day"] == "2026-10-07"
    assert {k: parsed[k] for k in expected} == expected


def test_unrelated_keys_do_not_parse():
    assert usage.parse_key("questions:2026-10-07") is None
    assert usage.parse_key("rl:min:abc:202610071200") is None


# ---------------------------------------------------------------- pricing

def test_pricing_defaults_carry_sources_and_verify():
    table = pricing.defaults()
    assert table["llm"] and all(r["source"].startswith("https://") and r["verify"] and r["checked"] for r in table["llm"])
    assert all(r["source"].startswith("https://") for r in table["embed"])
    assert table["tts"]["edge_per_1k_chars"] == 0.0


def test_pricing_math():
    table = pricing.defaults()
    # claude-sonnet-5-5 at $2 in / $10 out per 1M tokens
    assert pricing.llm_cost(table, "anthropic", "claude-sonnet-5-5", 1_000_000, 500_000) == pytest.approx(7.0)
    assert pricing.llm_cost(table, "anthropic", "unknown-model", 10, 10) is None
    assert pricing.embed_cost(table, "voyage-3.5", 2_000_000) == pytest.approx(0.12)
    assert pricing.tts_cost(table, "clone", 10_000) == pytest.approx(0.80)
    assert pricing.tts_cost(table, "free", 10_000) == 0.0


def test_openrouter_live_price_fills_gaps():
    live = {"x/cheap": {"pricing": {"prompt": "0.0000001", "completion": "0.0000004"}}}
    assert pricing.llm_cost(pricing.defaults(), "openrouter", "x/cheap", 1_000_000, 1_000_000, live) == pytest.approx(0.5)
    live_router = {"x/auto": {"pricing": {"prompt": "-1", "completion": "-1"}}}
    assert pricing.llm_cost(pricing.defaults(), "openrouter", "x/auto", 10, 10, live_router) is None


def test_pricing_validation_and_merge():
    table = pricing.defaults()
    table["llm"] = [{"provider": "anthropic", "model": "claude-sonnet-5-5", "in": 3, "out": 15}]
    clean = pricing.validate(table)
    merged = pricing.current(clean)
    assert merged["saved"] is True
    sonnet = next(r for r in merged["llm"] if r["model"] == "claude-sonnet-5-5")
    assert sonnet["in"] == 3.0
    assert any(r["model"] == "claude-haiku-4-5" for r in merged["llm"])  # defaults fill the rest
    for bad in ({"llm": [{"provider": "nope", "model": "m", "in": 1, "out": 1}]},
                {"llm": [{"provider": "openai", "model": "m", "in": -1, "out": 1}]},
                {"llm": [{"provider": "openai", "model": "bad id with spaces", "in": 1, "out": 1}]},
                {"tts": {"elevenlabs_plan": "platinum"}}, "nope"):
        with pytest.raises(pricing.BadPricing):
            pricing.validate(bad)


def test_pricing_put_round_trip(admin):
    r = admin.get("/api/admin/analytics/pricing")
    assert r.status_code == 200 and r.json()["pricing"]["saved"] is False
    table = r.json()["pricing"]
    table["llm"].append({"provider": "openrouter", "model": "x/new-model", "in": 0.5, "out": 1.5})
    r = admin.put("/api/admin/analytics/pricing", json={"pricing": table})
    assert r.status_code == 200, r.text
    assert any(x["model"] == "x/new-model" for x in r.json()["pricing"]["llm"])
    r = admin.put("/api/admin/analytics/pricing", json={"pricing": {"llm": [{"provider": "x"}]}})
    assert r.status_code == 400
    r = admin.put("/api/admin/analytics/pricing", json={"reset": True})
    assert r.status_code == 200 and r.json()["pricing"]["saved"] is False


# ---------------------------------------------------------------- client events

def test_event_requires_the_student_cookie(client):
    assert client.post("/api/event", json={"name": "chip_tap"}).status_code == 401


def test_event_allowlist(student):
    assert student.post("/api/event", json={"name": "chip_tap"}).status_code == 204
    assert limits.read_counter(f"event:{TODAY}:chip_tap") == 1
    assert student.post("/api/event", json={"name": "my name is Maria"}).status_code == 400
    assert student.post("/api/event", json={"name": "x" * 100}).status_code == 400
    assert student.post("/api/event", json={}).status_code == 400
    assert not [k for k in limits._mem if k.startswith("event:") and "chip_tap" not in k]


def test_event_extra_fields_are_dropped(student):
    r = student.post("/api/event", json={"name": "clip_played", "text": "free text", "visitor": "abc"})
    assert r.status_code == 204
    assert not [k for k in limits._mem if "free text" in k or "abc" in k.split(":")]


def test_event_rate_limit(student, monkeypatch):
    import time as _time

    # Pin the clock inside one minute (as test_api's links test does): 61 requests can cross a minute
    # boundary, which opens a fresh per-minute window and the 429 never comes (seen once, Oct 8).
    fixed = _time.gmtime(1_800_000_000)
    monkeypatch.setattr(_time, "gmtime", lambda *args: fixed)
    for _ in range(main_event_cap()):
        assert student.post("/api/event", json={"name": "segment_played"}).status_code == 204
    assert student.post("/api/event", json={"name": "segment_played"}).status_code == 429


def main_event_cap() -> int:
    from app import main

    return main.EVENT_PER_MINUTE


def test_event_cross_site_is_refused(student):
    r = student.post("/api/event", json={"name": "chip_tap"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_every_frontend_event_is_allowlisted():
    js = page_source("app.js")
    sent = set(re.findall(r"track\('([a-z_]+)'\)", js))
    assert sent and sent <= set(usage.EVENT_NAMES)
    listed = set(re.search(r"const EVENTS = new Set\(\[(.*?)\]\)", js, re.S).group(1).replace("'", "").replace(" ", "")
                 .replace("\n", "").split(","))
    assert listed == set(usage.EVENT_NAMES)


# ---------------------------------------------------------------- the question log

def test_ask_logs_analytics_columns(student):
    use_fakes()
    r = student.post("/api/ask", json={"question": "Tell me about fruit", "source": "chip"})
    assert r.status_code == 200
    row = limits._mem_log[-1]
    assert row["source"] == "chip"
    assert re.match(r"^70445-s01-\d{3}$", row["top_slide_id"])
    assert row["session"] == 1 and row["session_title"] == "Fruit basics"
    assert row["tokens_in"] == 0 and row["tokens_out"] == 0  # the fake model reports no usage
    assert isinstance(row["voice_chars"], int)


def test_ask_source_header_tags_test_traffic_only(student):
    use_fakes()
    student.post("/api/ask", json={"question": "Tell me about fruit", "source": "typed"},
                 headers={"X-FT-Source": "smoke"})
    assert limits._mem_log[-1]["source"] == "smoke"
    student.post("/api/ask", json={"question": "Tell me about fruit", "source": "typed"},
                 headers={"X-FT-Source": "admin"})
    assert limits._mem_log[-1]["source"] == "typed"
    student.post("/api/ask", json={"question": "Tell me about fruit", "source": "<script>"})
    assert limits._mem_log[-1]["source"] is None


def test_faq_hits_are_counted_per_entry(student):
    use_fakes()
    student.post("/api/ask", json={"question": "When are your office hours?"})
    assert limits.read_counter(f"faq:{TODAY}:meeting") == 1
    student.post("/api/ask", json={"question": "When are your office hours?"}, headers={"X-FT-Source": "smoke"})
    assert limits.read_counter(f"faq:{TODAY}:meeting") == 1  # tests are not student FAQ hits


def test_log_falls_back_before_the_migration(monkeypatch):
    _supabase_on(monkeypatch)
    monkeypatch.setattr(limits, "increment", lambda *a, **k: (True, 1))
    attempts: list[dict] = []

    def fake_insert(table, row, upsert_on=None):
        attempts.append(row)
        if "source" in row:
            raise supa.SupabaseError(
                "insert question_log failed (400): {\"code\":\"PGRST204\",\"message\":\"Could not find the 'source' column\"}")
        return [row]

    monkeypatch.setattr(supa, "insert", fake_insert)
    limits.log_question("what is an apple", 0.9, True, "anthropic", "m", 10, "70445", kind="course_content",
                        source="typed", top_slide_id="70445-s01-002", tokens_in=5, tokens_out=6)
    assert len(attempts) == 2
    assert attempts[-1]["kind"] == "course_content"
    assert not set(limits.ANALYTICS_COLUMNS) & set(attempts[-1])


def test_fallback_reason_is_dropped_alone_before_its_migration(monkeypatch):
    _supabase_on(monkeypatch)
    monkeypatch.setattr(limits, "increment", lambda *a, **k: (True, 1))
    attempts: list[dict] = []

    def fake_insert(table, row, upsert_on=None):
        attempts.append(row)
        if "fallback_reason" in row:
            raise supa.SupabaseError("insert question_log failed (400): {\"code\":\"PGRST204\"}")
        return [row]

    monkeypatch.setattr(supa, "insert", fake_insert)
    limits.log_question("q", 0.7, True, "anthropic", "m", 10, None, kind="course_info", source="typed",
                        fallback_reason="provider_credits")
    assert len(attempts) == 2 and attempts[0]["fallback_reason"] == "provider_credits"
    assert "fallback_reason" not in attempts[1] and attempts[1]["source"] == "typed"  # analytics kept
    attempts.clear()
    limits.log_question("q", 0.7, True, "anthropic", "m", 10, None, kind="course_info", fallback_reason=None)
    assert len(attempts) == 1 and "fallback_reason" not in attempts[0]  # rows that did not fall back never need it


def test_log_rows_falls_back_before_the_migration(monkeypatch):
    _supabase_on(monkeypatch)
    seen: list[str] = []

    def fake_select(table, params=None):
        seen.append(params["select"])
        if "source" in params["select"]:
            raise supa.SupabaseError('select failed (400): {"code":"42703","message":"column question_log.source does not exist"}')
        return [{"at": "2026-10-07T12:00:00Z", "question": "q", "covered": False, "kind": "not_covered"}]

    monkeypatch.setattr(supa, "select", fake_select)
    rows, full = limits.log_rows("2026-10-01")
    assert len(rows) == 1 and full is False and len(seen) == 2


def test_counters_since_reads_only_analytics_keys():
    limits.increment(f"usage:{TODAY}:narration:anthropic:m:in", 5)
    limits.increment(f"event:{TODAY}:chip_tap", 1)
    limits.increment(f"questions:{TODAY}", 1)
    limits.increment("usage:2001-01-01:narration:anthropic:m:in", 5)
    out = limits.counters_since(usage.PREFIXES, (datetime.now(UTC) - timedelta(days=6)).date().isoformat())
    assert set(out) == {f"usage:{TODAY}:narration:anthropic:m:in", f"event:{TODAY}:chip_tap"}


# ---------------------------------------------------------------- test traffic

@pytest.mark.parametrize(
    "row, expected",
    [
        ({"source": "smoke", "question": "x"}, (True, False)),
        ({"source": "eval", "question": "x"}, (True, False)),
        ({"source": "typed", "question": usage.SMOKE_QUESTIONS[0]}, (False, False)),
        ({"question": usage.SMOKE_QUESTIONS[1]}, (True, True)),
        ({"question": "What is k-means?"}, (False, True)),
    ],
)
def test_test_traffic_detection(row, expected):
    assert usage.is_test_traffic(row) == expected


def test_smoke_questions_match_the_smoke_script():
    from scripts import live_smoke as mod

    assert set(usage.SMOKE_QUESTIONS) == {mod.COVERED_QUESTION, mod.OFF_TOPIC_QUESTION, mod.FAQ_QUESTION}
    assert '"X-FT-Source": "smoke"' in (ROOT / "scripts" / "live_smoke.py").read_text()


def test_activity_rows_carry_the_test_badge():
    row = admin.activity_row({"question": usage.SMOKE_QUESTIONS[2], "covered": False, "kind": "faq"})
    assert row["test"] is True and row["test_inferred"] is True
    row = admin.activity_row({"question": "hi", "covered": False, "kind": "faq", "source": "chip"})
    assert row["test"] is False and row["test_inferred"] is False


# ---------------------------------------------------------------- aggregation

def _row(at: str, kind: str, **kw) -> dict:
    covered = kind in ("course_content", "stored_topic", "course_info")
    return {"at": at, "kind": kind, "covered": covered, "question": kw.pop("question", "q"), "top_score": 0.6,
            "latency_ms": 1000, **kw}


def test_aggregate_on_fake_rows():
    now = datetime(2026, 10, 7, 18, 0, tzinfo=UTC)
    rows = [
        _row("2026-10-07T15:00:00+00:00", "course_content", top_slide_id="70445-s06-014", session_title="Neural nets",
             source="chip"),
        _row("2026-10-07T14:00:00+00:00", "course_content", top_slide_id="70445-s06-014", source="typed"),
        _row("2026-10-06T14:00:00+00:00", "stored_topic", top_slide_id="45884-s02-003", source="follow_up"),
        _row("2026-10-06T13:00:00+00:00", "not_covered", question="who won the cup"),
        _row("2026-10-05T13:00:00+00:00", "not_covered", question="Who won the cup?", top_score=0.4),
        _row("2026-10-05T12:00:00+00:00", "faq"),
        _row("2026-10-05T11:00:00+00:00", "logistics", source="smoke"),  # test traffic
        _row("2026-09-01T11:00:00+00:00", "faq"),  # outside the 7-day range
    ]
    counters = {
        "usage:2026-10-07:narration:anthropic:claude-sonnet-5-5:in": 1_000_000,
        "usage:2026-10-07:narration:anthropic:claude-sonnet-5-5:out": 100_000,
        "usage:2026-10-07:narration:anthropic:claude-sonnet-5-5:calls": 3,
        "usage:2026-10-06:smoke_test:anthropic:claude-sonnet-5-5:in": 1000,
        "usage:2026-10-06:logistics:anthropic:mystery-model:in": 500,
        "embed:2026-10-07:voyage:voyage-3.5:tokens": 1_000_000,
        "tts:2026-10-07:clone:chars": 1000,
        "tts:2026-10-07:free:chars": 5000,
        "event:2026-10-07:segment_played": 2,
        "event:2026-10-07:walkthrough_completed": 1,
        "faq:2026-10-05:meeting": 1,
        "usage:2026-09-01:narration:anthropic:claude-sonnet-5-5:in": 999,  # outside the range
    }
    titles = {"70445-s06-014": {"title": "Backprop", "course_title": "AI for Business Leaders"}}
    out = analytics.aggregate(rows, counters, pricing.defaults(), 7, now, titles.get, faq_titles={"meeting": "Meeting with me"})
    k = out["kpis"]
    assert k["questions"] == 6 and out["test_traffic"] == {"rows": 1, "included": False}
    assert k["covered"] == 3 and k["covered_pct"] == 50.0
    assert k["by_kind"]["not_covered"] == 2 and "logistics" not in k["by_kind"]
    # $2 + $1 (sonnet) + $0.06 (voyage) + $0.08 (clone voice) + smoke 1000 tokens ($0.002)
    assert k["est_spend_usd"] == pytest.approx(2.0 + 1.0 + 0.002 + 0.06 + 0.08, abs=1e-6)
    assert k["unpriced"] == ["anthropic/mystery-model"]
    assert {p["purpose"] for p in out["purposes"]} == {"narration", "smoke_test", "logistics"}
    assert len(out["questions_by_day"]) == 7 and out["questions_by_day"][-1]["questions"] == 2
    assert sum(out["questions_by_hour"]) == 6
    top = out["topics"]["top"][0]
    assert top["slide_id"] == "70445-s06-014" and top["questions"] == 2 and top["title"] == "Backprop"
    course = next(c for c in out["topics"]["courses"] if c["course"] == "70445")
    assert course["sessions"][0]["session_title"] == "Neural nets" and course["title"] == "AI for Business Leaders"
    assert out["topics"]["gaps"][0]["count"] == 2  # the two wordings of the cup question group together
    assert out["topics"]["faq"] == [{"id": "meeting", "title": "Meeting with me", "count": 1}]
    assert out["topics"]["sources"] == {"chip": 1, "typed": 1, "follow_up": 1, "unknown": 3}
    assert out["funnel"] == {"questions": 6, "walkthroughs": 3, "first_segment_played": 2, "walkthrough_completed": 1}
    tiers = {t["tier"]: t for t in out["tts"]}
    assert tiers["free"]["cost"] == 0 and tiers["clone"]["cost"] == pytest.approx(0.08)
    for day in out["spend"]["days"]:
        assert all(v >= 0 for v in day["values"].values())

    with_tests = analytics.aggregate(rows, counters, pricing.defaults(), 7, now, include_tests=True)
    assert with_tests["kpis"]["questions"] == 7 and with_tests["kpis"]["by_kind"]["logistics"] == 1


def test_spend_series_fold_into_other():
    now = datetime(2026, 10, 7, tzinfo=UTC)
    counters = {f"usage:2026-10-07:narration:openai:gpt-6-luna-{i}:in": 1_000_000 * (i + 1) for i in range(10)}
    table = pricing.defaults()
    table["llm"] += [{"provider": "openai", "model": f"gpt-6-luna-{i}", "in": 1.0, "out": 1.0} for i in range(10)]
    out = analytics.aggregate([], counters, table, 7, now)
    assert len(out["spend"]["series"]) == analytics.MAX_SERIES + 1 and out["spend"]["series"][-1] == "Other"
    assert out["kpis"]["questions"] == 0 and out["kpis"]["covered_pct"] is None


def test_analytics_route_is_admin_only(student):
    assert student.get("/api/admin/analytics?days=7").status_code == 401
    assert student.get("/api/admin/analytics/export.csv").status_code == 401
    assert student.post("/api/admin/analytics/topics", json={"limit": 50}).status_code == 401
    assert student.put("/api/admin/analytics/pricing", json={"reset": True}).status_code == 401


def test_analytics_route_end_to_end(admin):
    use_fakes()
    admin.post("/api/ask", json={"question": "Tell me about fruit", "source": "typed"})
    admin.post("/api/ask", json={"question": usage.SMOKE_QUESTIONS[1]}, headers={"X-FT-Source": "smoke"})
    admin.post("/api/event", json={"name": "segment_played"})
    r = admin.get("/api/admin/analytics?days=7")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kpis"]["questions"] == 1 and d["test_traffic"]["rows"] == 1
    assert d["events"]["segment_played"] == 1
    assert d["models"]["available"] is False
    body = json.dumps(d)
    assert "visitor" not in body and "rl:" not in body
    assert admin.get("/api/admin/analytics?days=12").status_code == 400
    assert admin.get("/api/admin/analytics?days=7&include_tests=true").json()["kpis"]["questions"] == 2


def test_csv_export_escapes_formulas(admin):
    limits._mem_log.append({"at": datetime.now(UTC).isoformat(), "question": "=HYPERLINK(\"x\")",
                            "covered": False, "kind": "not_covered"})
    r = admin.get("/api/admin/analytics/export.csv?days=7")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    lines = r.text.splitlines()
    assert lines[0].startswith("at,course,kind") and "'=HYPERLINK" in r.text


# ---------------------------------------------------------------- eval scores

def test_eval_performance_averages_across_runs():
    files = {
        "evals/index.json": json.dumps({"runs": [{"id": "r1"}, {"id": "r2"}, {"id": "../bad"}]}).encode(),
        "evals/runs/r1/results.jsonl": "\n".join(json.dumps(x) for x in [
            {"generator": {"provider": "anthropic", "model": "a"}, "judges": [
                {"verdict": "pass", "scores": {"grounded": 5, "safety_tone": 4}},
                {"verdict": "fail", "scores": {"grounded": 3}}]},
            {"generator": {"provider": "openai", "model": "b"}, "judges": [{"error": "timeout"}]},
        ]).encode(),
        "evals/runs/r2/results.jsonl": json.dumps(
            {"generator": "anthropic/a", "judgements": [{"verdict": "pass", "scores": {"grounded": 4}}]}).encode(),
    }
    out = analytics.eval_performance(files.get)
    assert out["available"] and out["runs"] == 2
    a = next(m for m in out["models"] if m["model"] == "anthropic/a")
    assert a["runs"] == 2 and a["answers"] == 2 and a["pass_rate"] == pytest.approx(2 / 3, abs=1e-3)
    assert a["scores"]["grounded"] == 4.0 and a["scores"]["safety_tone"] == 4.0 and a["scores"]["correct_scope"] is None
    b = next(m for m in out["models"] if m["model"] == "openai/b")
    assert b["pass_rate"] is None


def test_eval_performance_without_data():
    assert analytics.eval_performance(lambda p: None)["available"] is False
    assert analytics.eval_performance(lambda p: b"not json")["available"] is False


# ---------------------------------------------------------------- topic labeling

def _seed_questions(n: int, smoke: int = 0) -> None:
    now = datetime.now(UTC)
    for i in range(n):
        limits._mem_log.append({"at": (now - timedelta(minutes=n - i)).isoformat(),
                                "question": f"What is topic {i}? email me at a{i}@b.com", "covered": True,
                                "kind": "course_content", "source": "typed"})
    for _ in range(smoke):
        limits._mem_log.append({"at": now.isoformat(), "question": "SMOKE ONLY QUESTION", "covered": False,
                                "kind": "faq", "source": "smoke"})


def _labeler(calls: list):
    def fake(system, user, max_tokens, provider=None, model=None):
        calls.append({"system": system, "user": user, "max_tokens": max_tokens, "provider": provider, "model": model,
                      "purpose": usage.current_purpose()})
        return json.dumps({"themes": [
            {"label": "Topics, my name is Maria", "count": 9, "examples": [1, 2, 999, "invented question"]},
            {"label": "Other", "count": 3, "examples": [3]},
        ]})

    return fake


def test_label_topics_sends_scrubbed_text_and_caps(admin, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    _seed_questions(20, smoke=3)
    calls: list = []
    app.dependency_overrides[analytics.get_label_completer] = lambda: _labeler(calls)
    r = admin.post("/api/admin/analytics/topics", json={"limit": 10})
    assert r.status_code == 200, r.text
    run = r.json()
    sent = calls[0]
    assert sent["model"] == "claude-haiku-4-5" and sent["max_tokens"] == analytics.LABEL_MAX_TOKENS
    assert sent["purpose"] == "topic_label"
    assert "@b.com" not in sent["user"] and "[email]" in sent["user"]
    assert "SMOKE ONLY" not in sent["user"]  # test traffic is not labeled
    assert len(json.loads(sent["user"].split("\n", 1)[1])) == 10
    assert run["themes"][0]["label"] == "Topics, my name is [name]"
    assert len(run["themes"][0]["examples"]) == 2  # only real question numbers
    assert all("@" not in q for q in run["themes"][0]["examples"])
    history = admin.get("/api/admin/analytics/topics").json()["runs"]
    assert history and history[0]["id"] == run["id"]


def test_label_topics_limits(admin, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    calls: list = []
    app.dependency_overrides[analytics.get_label_completer] = lambda: _labeler(calls)
    assert admin.post("/api/admin/analytics/topics", json={"limit": 301}).status_code == 400
    assert admin.post("/api/admin/analytics/topics", json={"limit": 50}).status_code == 400  # nothing to group yet
    _seed_questions(30)
    for _ in range(analytics.LABEL_DAILY_CAP):
        assert admin.post("/api/admin/analytics/topics", json={"limit": 20}).status_code == 200
    r = admin.post("/api/admin/analytics/topics", json={"limit": 20})
    assert r.status_code == 429
    assert len(calls) == analytics.LABEL_DAILY_CAP


def test_parse_labels_rejects_garbage():
    with pytest.raises(ValueError):
        analytics.parse_labels("no json here", ["q"])
    with pytest.raises(ValueError):
        analytics.parse_labels('{"themes": []}', ["q"])


# ---------------------------------------------------------------- the page

def test_admin_page_has_the_analytics_section():
    html = (ROOT / "public" / "admin.html").read_text()
    assert 'href="#sec-analytics"' in html and 'id="sec-analytics"' in html
    assert 'src="admin-analytics.js"' in html and 'href="analytics.css"' in html
    assert "Show test traffic" in html
    assert "—" not in (ROOT / "public" / "admin-analytics.js").read_text()


def test_content_gaps_with_equal_counts_show_the_most_recent_first():
    # Oct 8 code review: ties were sorted oldest first, so with more than TOP_N one-off gaps the
    # table kept the oldest and dropped the newest, the ones Ben most needs to see.
    now = datetime(2026, 10, 8, 12, tzinfo=UTC)
    rows = [{"question": f"unanswered question number {i}", "kind": "not_covered", "covered": False,
             "at": (now - timedelta(hours=i)).isoformat()} for i in range(analytics.TOP_N + 5)]
    out = analytics.aggregate(rows, {}, pricing.defaults(), 7, now)
    gaps = out["topics"]["gaps"]
    assert len(gaps) == analytics.TOP_N
    assert gaps[0]["question"] == "unanswered question number 0"
    assert "unanswered question number 24" not in {g["question"] for g in gaps}


def test_schema_has_the_migration_block():
    sql = (ROOT / "supabase" / "schema.sql").read_text()
    for col in limits.ANALYTICS_COLUMNS + limits.REASON_COLUMNS:
        assert re.search(rf"alter table question_log add column if not exists {col}\s", sql), col
