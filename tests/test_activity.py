"""Settings > Activity: honest log rows (kind, no model when none was called), the docs link, and the test scripts.

Retrieval, embeddings and models here are TEST FAKES (from test_api); no network.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import numpy as np
import pytest

from app.main import LOG_KINDS, Retriever, app, get_completer, get_embedder, get_retriever  # before app.admin
from app import admin, limits, logistics, storage, supa  # noqa: E402,I001

from test_api import TEST_FAKE_embedder, TEST_FAKE_llm, TEST_FAKE_rank, TEST_FAKE_select

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "TESTING_AND_SCORES.md"
DOC_URL = "https://github.com/bcollier/faculty-twin/blob/main/docs/TESTING_AND_SCORES.md"
NO_ENV_FILE = "/nonexistent/faculty-twin-test.env"  # keeps the scripts from reading a real .env in tests


def _use(kind: str, calls: list[str]):
    """TEST FAKE pipeline: fake retriever and embedder; the fake model answers the logistics check with `kind`."""

    def fake(system, user, max_tokens, provider=None, model=None):
        if system == logistics.SYSTEM_PROMPT:
            calls.append("classify")
            return json.dumps({"kind": kind, "reason": "fake"})
        calls.append("narrate")
        return TEST_FAKE_llm(system, user, max_tokens, provider, model)

    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: fake


def _ask(student, question: str) -> dict:
    r = student.post("/api/ask", json={"question": question})
    assert r.status_code == 200, r.text
    return limits._mem_log[-1]


# ---------------------------------------------------------------- what /api/ask records

def test_course_content_row_records_the_model(student):
    calls: list[str] = []
    _use("course_content", calls)
    row = _ask(student, "Tell me about fruit")
    assert row["kind"] == "course_content" and row["covered"] is True
    assert row["provider"] == "anthropic" and row["model"]
    assert row["top_score"] == pytest.approx(0.9)
    assert calls == ["classify", "narrate"]


def test_stored_topic_row_has_no_model_and_no_score(student):
    calls: list[str] = []
    _use("course_content", calls)
    row = _ask(student, "Show me the banana slide")
    assert row["kind"] == "stored_topic" and row["covered"] is True
    assert row["provider"] is None and row["model"] is None and row["top_score"] is None
    assert calls == []


def test_faq_row_has_no_model_and_no_score(student):
    calls: list[str] = []
    _use("course_content", calls)
    row = _ask(student, "When are your office hours?")
    assert row["kind"] == "faq" and row["covered"] is False
    assert row["provider"] is None and row["model"] is None and row["top_score"] is None
    assert row["latency_ms"] is not None
    assert calls == []


def test_not_covered_row_has_a_score_but_no_model(student):
    calls: list[str] = []
    _use("course_content", calls)
    row = _ask(student, "who won the Stanley Cup")
    assert row["kind"] == "not_covered" and row["covered"] is False
    assert row["provider"] is None and row["model"] is None
    assert row["top_score"] == pytest.approx(0.05)
    assert calls == []


def test_keyword_logistics_row_has_no_model(student):
    calls: list[str] = []
    _use("course_content", calls)
    row = _ask(student, "Can I get a regrade on my fruit quiz?")
    assert row["kind"] == "logistics" and row["covered"] is False
    assert row["provider"] is None and row["model"] is None
    assert row["top_score"] == pytest.approx(0.9)
    assert calls == []


def test_model_logistics_row_records_the_model(student):
    calls: list[str] = []
    _use("logistics", calls)
    row = _ask(student, "Where is the fruit recording from last week?")
    assert row["kind"] == "logistics" and row["provider"] == "anthropic" and row["model"]
    assert calls == ["classify"]


def test_empty_index_for_the_filter_is_not_covered(student, monkeypatch):
    calls: list[str] = []
    _use("course_content", calls)
    from app import playlist

    monkeypatch.setattr(playlist, "hidden_sessions", lambda: {("70445", 1), ("45884", 2)})
    row = _ask(student, "Tell me about fruit")
    assert row["kind"] == "not_covered" and row["top_score"] is None and row["provider"] is None


def test_every_logged_kind_is_known(student):
    calls: list[str] = []
    _use("course_content", calls)
    for q in ["Tell me about fruit", "Show me the banana slide", "When are your office hours?",
              "who won the Stanley Cup", "Can I get a regrade on my fruit quiz?"]:
        _ask(student, q)
    # course_info needs the optional info index, which this fixture leaves out: tests/test_course_info.py logs it.
    # alert (a student reporting a broken quiz or submission) is logged in tests/test_alerts.py.
    # web needs a course-adjacent question and a search: tests/test_web_answer.py logs it.
    assert {r["kind"] for r in limits._mem_log} == set(LOG_KINDS) - {"course_info", "alert", "web"}


# ---------------------------------------------------------------- the Activity rows

@pytest.mark.parametrize(
    "row, kind",
    [
        ({"covered": True, "top_score": 0.61}, "course_content"),
        ({"covered": True, "top_score": None}, "stored_topic"),
        ({"covered": False, "top_score": None}, "faq"),
        ({"covered": False, "top_score": 0.545}, "logistics"),
        ({"covered": False, "top_score": 0.52}, "logistics"),
        ({"covered": False, "top_score": 0.41}, "not_covered"),
    ],
)
def test_infer_kind_for_rows_without_one(row, kind):
    assert admin.infer_kind(row) == kind
    out = admin.activity_row({**row, "provider": "anthropic", "model": "m"})
    assert out["kind"] == kind and out["kind_inferred"] is True


def test_activity_row_keeps_a_recorded_kind():
    out = admin.activity_row({"covered": False, "top_score": 0.55, "kind": "logistics", "provider": "openai", "model": "m"})
    assert out["kind"] == "logistics" and out["kind_inferred"] is False
    assert out["provider"] == "openai" and out["model"] == "m"  # logistics may have called the model


@pytest.mark.parametrize("kind", ["faq", "not_covered", "stored_topic"])
def test_activity_row_shows_no_model_for_kinds_that_never_call_one(kind):
    out = admin.activity_row({"covered": kind == "stored_topic", "top_score": None, "kind": kind,
                              "provider": "anthropic", "model": "claude"})
    assert out["provider"] is None and out["model"] is None


def test_activity_row_fixes_old_stored_rows_logged_as_course_content():
    out = admin.activity_row({"covered": True, "top_score": None, "kind": "course_content",
                              "provider": "anthropic", "model": "m"})
    assert out["kind"] == "stored_topic" and out["kind_inferred"] is True and out["model"] is None


def test_admin_log_returns_kinds(admin_client_with_rows):
    rows = admin_client_with_rows.get("/api/admin/log").json()["rows"]
    kinds = [r["kind"] for r in rows]
    assert kinds == ["logistics", "not_covered", "faq", "stored_topic", "course_content"]
    assert all(r["kind_inferred"] is False for r in rows)
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["faq"]["model"] is None and by_kind["course_content"]["provider"] == "anthropic"


@pytest.fixture
def admin_client_with_rows(admin):
    calls: list[str] = []
    _use("course_content", calls)
    for q in ["Tell me about fruit", "Show me the banana slide", "When are your office hours?",
              "who won the Stanley Cup", "Can I get a regrade on my fruit quiz?"]:
        assert admin.post("/api/ask", json={"question": q}).status_code == 200
    return admin


# ---------------------------------------------------------------- before the kind column exists

def _supabase_on(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")


def test_recent_questions_reads_without_kind_when_the_column_is_missing(monkeypatch):
    _supabase_on(monkeypatch)
    seen: list[str] = []

    def fake_select(table, params=None):
        seen.append(params["select"])
        if "kind" in params["select"]:
            raise supa.SupabaseError(
                'select question_log failed (400): {"code":"42703","message":"column question_log.kind does not exist"}')
        return [{"at": "2026-10-07T12:00:00Z", "question": "q", "covered": False, "top_score": None}]

    monkeypatch.setattr(supa, "select", fake_select)
    rows = limits.recent_questions(5)
    assert len(rows) == 1 and "kind" not in rows[0]
    # With source (analytics migration), then with kind, then without either.
    assert seen[0].endswith(",kind,source") and seen[1].endswith(",kind") and "kind" not in seen[2]


def test_recent_questions_requests_kind_when_the_column_exists(monkeypatch):
    _supabase_on(monkeypatch)
    seen: list[dict] = []

    def fake_select(table, params=None):
        seen.append(params)
        return [{"question": "q", "kind": "faq"}]

    monkeypatch.setattr(supa, "select", fake_select)
    assert limits.recent_questions(5)[0]["kind"] == "faq"
    assert len(seen) == 1 and "kind" in seen[0]["select"] and seen[0]["limit"] == "5"


def test_recent_questions_raises_other_database_errors(monkeypatch):
    _supabase_on(monkeypatch)

    def fake_select(table, params=None):
        raise supa.SupabaseError("select question_log failed (500): boom")

    monkeypatch.setattr(supa, "select", fake_select)
    with pytest.raises(supa.SupabaseError):
        limits.recent_questions(5)


def test_log_question_writes_without_kind_when_the_column_is_missing(monkeypatch):
    _supabase_on(monkeypatch)
    monkeypatch.setattr(limits, "increment", lambda *a, **k: (True, 1))
    inserted: list[dict] = []

    def fake_insert(table, row, upsert_on=None):
        if "kind" in row:
            raise supa.SupabaseError(
                "insert question_log failed (400): {\"code\":\"PGRST204\",\"message\":\"Could not find the 'kind' column\"}")
        inserted.append(row)
        return [row]

    monkeypatch.setattr(supa, "insert", fake_insert)
    limits.log_question("what is an apple", None, False, None, None, 1, None, kind="faq")
    assert inserted and "kind" not in inserted[0] and inserted[0]["provider"] is None


@pytest.mark.parametrize(
    "message, missing",
    [
        ('{"code":"42703","message":"column question_log.kind does not exist"}', True),
        ("{\"code\":\"PGRST204\",\"message\":\"Could not find the 'kind' column of 'question_log'\"}", True),
        ("select question_log failed (500): timeout", False),
        ('{"code":"42703","message":"column question_log.other does not exist"}', False),
    ],
)
def test_missing_kind_column_detection(message, missing):
    assert limits.missing_kind_column(supa.SupabaseError(message)) is missing


# ---------------------------------------------------------------- the page and the doc

def test_activity_section_links_the_doc():
    html = (ROOT / "public" / "admin.html").read_text(encoding="utf-8")
    section = html[html.index('id="sec-activity"'):html.index("</section>", html.index('id="sec-activity"'))]
    assert f'href="{DOC_URL}"' in section and "How these numbers work" in section
    assert "Run a new test: see" in section and "Testing and scores" in section
    for tag in section.split("<a ")[1:]:
        assert 'target="_blank"' in tag and 'rel="noopener"' in tag
    assert "—" not in section


def test_activity_script_shows_badges_and_none():
    js = (ROOT / "public" / "admin.js").read_text(encoding="utf-8")
    for text in ["'Covered'", "'Stored answer'", "'FAQ'", "'Referred to Ben'", "'Not covered'",
                 "'No search ran'", "'none'"]:
        assert text in js, text
    for kind in LOG_KINDS:
        assert f"{kind}:" in js, kind
    assert "—" not in js


def test_doc_explains_the_columns_and_commands():
    doc = DOC.read_text(encoding="utf-8")
    assert "—" not in doc
    for heading in ["### When", "### Covered", "### Top score", "### Latency", "### Model", "## Run a new test"]:
        assert heading in doc, heading
    assert "0.52" in doc and "0.543" in doc and "0.448" in doc
    assert "python -m pytest -q" in doc and "python -m evals.run --top 25 --target in-process" in doc
    assert "python -m scripts.threshold_table" in doc and "python -m scripts.live_smoke" in doc
    assert "evals/private/" in doc and "summary.md" in doc
    assert "mac mini" not in doc.lower() and "laptop" not in doc.lower()
    for part in ["Vercel", "Supabase", "Voyage", "ElevenLabs", "edge-tts", "local build machine"]:
        assert part in doc, part


def test_spec_describes_the_kinds():
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert "TESTING_AND_SCORES.md" in spec
    for kind in LOG_KINDS:
        assert f"`{kind}`" in spec, kind


# ---------------------------------------------------------------- scripts/threshold_table.py

from scripts import live_smoke, threshold_table  # noqa: E402


def _fake_embed_many(texts):
    """TEST FAKE: one fake vector per question, no network."""
    return [TEST_FAKE_embedder(t) for t in texts]


def test_threshold_defaults_are_five_on_and_five_off():
    qs = threshold_table.load_questions(None)
    assert [q["label"] for q in qs].count("on") == 5 and [q["label"] for q in qs].count("off") == 5


def test_threshold_questions_file(tmp_path):
    f = tmp_path / "q.jsonl"
    f.write_text('{"question": "apple?", "label": "on"}\n\n{"question": "hockey?", "label": "OFF"}\n')
    assert threshold_table.load_questions(str(f)) == [
        {"question": "apple?", "label": "on"}, {"question": "hockey?", "label": "off"}]
    f.write_text('{"question": "apple?", "label": "maybe"}\n')
    with pytest.raises(SystemExit):
        threshold_table.load_questions(str(f))


def test_threshold_score_and_summary(content_dir):
    content = storage.load_local(content_dir)
    from app import playlist

    records, matrix = playlist.searchable(content, None)
    questions = [{"question": "What is an apple?", "label": "on"}, {"question": "Who won the cup?", "label": "off"}]
    rows = threshold_table.score(questions, records, matrix, _fake_embed_many, rank=TEST_FAKE_rank)
    assert [r.top_score for r in rows] == [pytest.approx(0.9), pytest.approx(0.05)]
    s = threshold_table.summarize(rows, 0.52)
    assert s["separates"] and s["gap"] == pytest.approx(0.85)
    assert not threshold_table.summarize(rows, 0.95)["separates"]
    assert threshold_table.summarize(rows, 0.95)["wrong_declines"] == ["What is an apple?"]
    text = threshold_table.render(rows, s)
    assert "| on-topic | 0.900 | yes |" in text and "separates every question" in text


def test_threshold_uses_bens_rank_unchanged(content_dir):
    from app import playlist, retrieval

    records, matrix = playlist.searchable(storage.load_local(content_dir), None)
    rows = threshold_table.score([{"question": "apple", "label": "on"}], records, matrix,
                                 lambda texts: [np.asarray(matrix[1], dtype=np.float32)])
    assert threshold_table.score.__defaults__[0] is retrieval.rank
    assert rows[0].top_score == pytest.approx(1.0, abs=1e-5) and rows[0].slide_id == records[1]["id"]


def test_threshold_main(content_dir, monkeypatch, capsys):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    code = threshold_table.main(["--env-file", NO_ENV_FILE], embed_many=_fake_embed_many)
    out = capsys.readouterr().out
    assert "Threshold: 0.520" in out and "On-topic:" in out
    assert code in (0, 1)


def test_threshold_main_needs_content_dir(monkeypatch):
    with pytest.raises(SystemExit):
        threshold_table.main(["--env-file", NO_ENV_FILE], embed_many=_fake_embed_many)


# ---------------------------------------------------------------- scripts/live_smoke.py

PASS = "smoke-secret-passcode"


def _fake_site(passcode: str = PASS, broken_faq: bool = False):
    """TEST FAKE deployed site for the smoke check."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/health":
            return httpx.Response(200, json={"ok": True})
        if path == "/api/login":
            if json.loads(request.content)["passcode"] != passcode:
                return httpx.Response(401, json={"detail": "That passcode is not right."})
            return httpx.Response(204, headers={"set-cookie": "ft_session=abc; Path=/; Secure; HttpOnly"})
        if "ft_session=abc" not in request.headers.get("cookie", ""):
            return httpx.Response(401, json={"detail": "Not signed in"})
        if path == "/api/topics":
            return httpx.Response(200, json=[{"question": "What is k-means?", "course": "70445"}])
        if path == "/api/ask":
            q = json.loads(request.content)["question"]
            if q == live_smoke.COVERED_QUESTION:
                return httpx.Response(200, json={"covered": True, "segments": [{"n": 1}]})
            if q == live_smoke.FAQ_QUESTION and not broken_faq:
                return httpx.Response(200, json={"covered": False, "kind": "faq", "segments": []})
            return httpx.Response(200, json={"covered": False, "segments": []})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_live_smoke_passes_and_never_prints_the_passcode(monkeypatch, capsys):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    code = live_smoke.main(["--base-url", "https://site.test", "--env-file", NO_ENV_FILE], transport=_fake_site())
    out = capsys.readouterr().out
    assert code == 0 and "All steps passed." in out
    for name in ["health", "login", "topics", "ask covered", "ask off-topic", "ask FAQ"]:
        assert name in out
    assert "kind=faq" in out and "covered=True" in out
    assert PASS not in out


def test_live_smoke_reports_a_wrong_passcode(monkeypatch, capsys):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    code = live_smoke.main(["--base-url", "https://site.test", "--env-file", NO_ENV_FILE],
                           transport=_fake_site(passcode="other"))
    out = capsys.readouterr().out
    assert code == 1 and "FAIL login" in out and "401" in out and PASS not in out


def test_live_smoke_flags_a_wrong_answer(monkeypatch, capsys):
    monkeypatch.setenv("STUDENT_PASSCODE", PASS)
    code = live_smoke.main(["--base-url", "https://site.test", "--env-file", NO_ENV_FILE],
                           transport=_fake_site(broken_faq=True))
    out = capsys.readouterr().out
    assert code == 1 and "FAIL ask FAQ" in out


def test_live_smoke_without_a_passcode(monkeypatch, capsys):
    monkeypatch.delenv("STUDENT_PASSCODE", raising=False)
    code = live_smoke.main(["--base-url", "https://site.test", "--env-file", NO_ENV_FILE], transport=_fake_site())
    out = capsys.readouterr().out
    assert code == 1 and "STUDENT_PASSCODE is not set" in out
