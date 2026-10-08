"""app/limits.py against a fake Supabase (supa functions monkeypatched): counters, the question log, and paging."""

from __future__ import annotations

import pytest

from app import limits, supa


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SUPABASE_URL", "https://proj.supabase.test")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
    state = {"rpc": [], "select": [], "insert": []}
    return state


def test_increment_uses_the_rpc_and_reads_list_or_dict(db, monkeypatch):
    replies = iter([[{"allowed": True, "count": 3}], {"allowed": False, "count": 9}])

    def rpc(fn, args):
        db["rpc"].append((fn, args))
        return next(replies)

    monkeypatch.setattr(supa, "rpc", rpc)
    assert limits.increment("rl:x", 1, cap=10, ttl_seconds=60) == (True, 3)
    assert limits.increment("rl:x", 1, cap=10) == (False, 9)
    assert db["rpc"][0] == ("ft_increment", {"p_key": "rl:x", "p_amount": 1, "p_cap": 10, "p_ttl_seconds": 60})


@pytest.mark.parametrize("failure", [supa.SupabaseError("down"), [], [{"count": 1}], None])
def test_increment_db_trouble_fails_open_for_rate_limits_and_closed_for_caps(db, monkeypatch, failure):
    def rpc(fn, args):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(supa, "rpc", rpc)
    assert limits.increment("rl:y", 1, cap=1, fail_open=True) == (True, 1)  # memory fallback
    assert limits.increment("rl:y", 1, cap=1, fail_open=True)[0] is False  # ...which still limits
    assert limits.increment("cap:z", 1, cap=100, fail_open=False) == (False, 0)


def test_read_counter(db, monkeypatch):
    monkeypatch.setattr(supa, "select", lambda table, params: [{"count": "7"}])
    assert limits.read_counter("voice_chars:2026-10-08") == 7
    monkeypatch.setattr(supa, "select", lambda table, params: [])
    assert limits.read_counter("x") == 0

    def down(table, params):
        raise supa.SupabaseError("down")

    monkeypatch.setattr(supa, "select", down)
    assert limits.read_counter("x") == 0


def test_take_caps_with_zero_cap(monkeypatch):
    monkeypatch.setenv("DAILY_LLM_CALL_CAP", "0")
    monkeypatch.setenv("DAILY_EMBED_CAP", "-1")
    monkeypatch.setenv("DAILY_EVAL_LLM_CALL_CAP", "0")
    assert limits.take_llm_call() is False
    assert limits.take_embedding() is False
    assert limits.take_eval_call() is False


def test_eval_cap_counts(monkeypatch):
    monkeypatch.setenv("DAILY_EVAL_LLM_CALL_CAP", "2")
    assert limits.take_eval_call() and limits.take_eval_call()
    assert limits.take_eval_call() is False
    assert limits.read_counter(limits.eval_calls_key()) == 2


# ---------------------------------------------------------------- question log in Postgres

def test_log_question_drops_columns_until_the_insert_works(db, monkeypatch):
    monkeypatch.setattr(supa, "rpc", lambda fn, args: [{"allowed": True, "count": 1}])
    attempts = []

    def insert(table, row, upsert_on=None):
        attempts.append(dict(row))
        if "kind" in row:
            raise supa.SupabaseError("PGRST204 Could not find the 'kind' column")
        return [row]

    monkeypatch.setattr(supa, "insert", insert)
    limits.log_question("what is k-means", 0.712345, True, "anthropic", "m", 900, "70445", kind="course_content",
                        top_slide_id="70445-s01-002", source="typed", visitor="never-logged")
    assert len(attempts) == 3
    assert attempts[0]["top_slide_id"] == "70445-s01-002" and attempts[0]["top_score"] == 0.7123
    assert "visitor" not in attempts[0]
    assert "top_slide_id" not in attempts[1] and "kind" in attempts[1]
    assert "kind" not in attempts[2]


def test_log_question_skips_duplicate_attempts(db, monkeypatch):
    monkeypatch.setattr(supa, "rpc", lambda fn, args: [{"allowed": True, "count": 1}])
    attempts = []

    def insert(table, row, upsert_on=None):
        attempts.append(dict(row))
        raise supa.SupabaseError("down")

    monkeypatch.setattr(supa, "insert", insert)
    limits.log_question("q", None, False, None, None)  # no extras: attempt 2 equals attempt 1
    assert len(attempts) == 2  # never raises


def test_recent_questions_falls_back_through_columns(db, monkeypatch):
    calls = []

    def select(table, params):
        calls.append(params["select"])
        if "source" in params["select"]:
            raise supa.SupabaseError("42703 column question_log.source does not exist")
        if params["select"].endswith(",kind"):
            raise supa.SupabaseError("42703 column question_log.kind does not exist")
        return [{"question": "q"}]

    monkeypatch.setattr(supa, "select", select)
    assert limits.recent_questions(5) == [{"question": "q"}]
    assert calls == [limits.LOG_COLUMNS + ",kind,source,fallback_reason", limits.LOG_COLUMNS + ",kind,source",
                     limits.LOG_COLUMNS + ",kind", limits.LOG_COLUMNS]


def test_recent_questions_other_errors_raise(db, monkeypatch):
    def select(table, params):
        raise supa.SupabaseError("500 internal")

    monkeypatch.setattr(supa, "select", select)
    with pytest.raises(supa.SupabaseError):
        limits.recent_questions()


def test_log_rows_pages_and_reports_full_columns(db, monkeypatch):
    monkeypatch.setattr(limits, "PAGE_ROWS", 2)
    rows = [{"at": f"2026-10-0{i}"} for i in range(5)]
    calls = []

    def select(table, params):
        calls.append(params)
        off, lim = int(params["offset"]), int(params["limit"])
        return rows[off: off + lim]

    monkeypatch.setattr(supa, "select", select)
    out, full = limits.log_rows("2026-10-01")
    assert out == rows and full is True
    assert [c["offset"] for c in calls] == ["0", "2", "4"]
    assert calls[0]["at"] == "gte.2026-10-01"


def test_log_rows_without_analytics_columns(db, monkeypatch):
    def select(table, params):
        if "top_slide_id" in params["select"]:
            raise supa.SupabaseError("PGRST204 missing column")
        return [{"at": "x"}]

    monkeypatch.setattr(supa, "select", select)
    assert limits.log_rows("2026-10-01") == ([{"at": "x"}], False)


def test_log_rows_all_shapes_missing_and_other_errors(db, monkeypatch):
    def missing(table, params):
        raise supa.SupabaseError("42703")

    monkeypatch.setattr(supa, "select", missing)
    assert limits.log_rows("2026-10-01") == ([], False)

    def broken(table, params):
        raise supa.SupabaseError("500 internal")

    monkeypatch.setattr(supa, "select", broken)
    with pytest.raises(supa.SupabaseError):
        limits.log_rows("2026-10-01")


def test_log_rows_in_memory_filters_by_time():
    limits.log_question("old question", None, False, None, None)
    rows, full = limits.log_rows("2000-01-01")
    assert full is True and rows[0]["question"] == "old question"
    assert limits.log_rows("2999-01-01") == ([], True)


def test_counters_since_pages_and_filters_by_key_day(db, monkeypatch):
    monkeypatch.setattr(limits, "PAGE_ROWS", 2)
    pages = [
        [{"key": "voice_chars:2026-10-07", "count": 5}, {"key": "voice_chars:2026-10-01", "count": 9}],
        [{"key": "llm_calls:2026-10-08", "count": None}, {"key": "other:2026-10-08", "count": 1}],
        [{"key": "llm_calls", "count": 3}],
    ]
    calls = []

    def select(table, params):
        calls.append(params)
        return pages[len(calls) - 1]

    monkeypatch.setattr(supa, "select", select)
    out = limits.counters_since(("voice_chars", "llm_calls"), "2026-10-05")
    assert out == {"voice_chars:2026-10-07": 5, "llm_calls:2026-10-08": 0}
    assert calls[0]["day"] == "gte.2026-10-04"  # a day of slack
    assert calls[0]["or"] == '(key.like."voice_chars*",key.like."llm_calls*")'
    assert len(calls) == 3


def test_counters_since_in_memory():
    limits.increment("voice_chars:2026-10-07", 4)
    limits.increment("voice_chars:2026-09-01", 4)
    limits.increment("embeds:2026-10-07", 1)
    assert limits.counters_since(("voice_chars",), "2026-10-01") == {"voice_chars:2026-10-07": 4}


@pytest.mark.parametrize("text,expected", [
    ("42703 column question_log.kind does not exist", True),
    ("PGRST204 Could not find the 'kind' column", True),
    ("500 internal", False),
    ("42703 column question_log.source does not exist", False),
])
def test_missing_kind_column(text, expected):
    assert limits.missing_kind_column(Exception(text)) is expected
