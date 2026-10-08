"""Settings > Evals: the admin API, the step runner, spend guards, the model override, storage, and the report card.

Retrieval is Ben's hand-written code, so these tests inject the same kind of
TEST FAKE retriever, embedder and LLM as tests/test_api.py. Judges are fakes
too. The bucket is an in-memory fake. Every question here is invented.
"""

from __future__ import annotations

import contextvars
import json
import threading

import numpy as np
import pytest

from app.main import Retriever, app, get_completer, get_embedder, get_retriever  # first: main wires the routers
from app import admin_evals, eval_core, eval_store, limits, llm, settings_store, storage  # noqa: E402

FRUIT_IDS = ["70445-s01-002", "70445-s01-003"]

QUESTIONS = [
    {"month": "2026-09", "course": "Example", "category": "CONCEPT_QUESTION",
     "question": "Why do apples turn brown once you cut them?", "reference_answer": "Air reacts with the flesh.",
     "answerable_from_course_materials": True},
    {"month": "2026-09", "course": "Example", "category": "MEETING_REQUEST",
     "question": "Could we find a time to talk about my project idea?", "reference_answer": None,
     "answerable_from_course_materials": False},
    {"month": "2026-08", "course": "Example", "category": "CONCEPT_QUESTION",
     "question": "Which apple variety is best for baking?", "reference_answer": None,
     "answerable_from_course_materials": True},
]


# ---------------------------------------------------------------- TEST FAKES (tests only)

def TEST_FAKE_embedder(question: str) -> np.ndarray:
    v = np.zeros(8, dtype=np.float32)
    v[0 if "apple" in question.lower() else 1] = 1.0
    return v


def TEST_FAKE_rank(question_vec, matrix):
    """TEST FAKE: no similarity math."""
    return [(i, 0.9 if question_vec[0] else 0.05) for i in range(matrix.shape[0])]


def TEST_FAKE_select(ranked, records, threshold):
    """TEST FAKE: canned selection by id, not Ben's selection rules."""
    return [] if ranked[0][1] < 0.5 else [r for r in records if r["id"] in FRUIT_IDS]


class FakeLLM:
    """TEST FAKE generator: records which model each call asked for, and what llm_choice() said."""

    def __init__(self):
        self.calls = []

    def __call__(self, system, user, max_tokens, provider=None, model=None, **kw):
        self.calls.append({"provider": provider, "model": model, "choice": settings_store.llm_choice()})
        if "Slides, in the order they will be shown:\n" not in user:  # the logistics classifier
            return json.dumps({"kind": "course_content", "reason": "about apples"})
        ids = [s["slide_id"] for s in json.loads(user.split("Slides, in the order they will be shown:\n", 1)[1])]
        return json.dumps({"segments": [{"slide_id": i, "narration": f"On this slide, an apple {i}."} for i in ids],
                           "follow_ups": ["What is a banana?"]})


GOOD = {"scores": {"grounded": 5, "answers_question": 4, "correct_scope": 5, "matches_reference": None,
                   "speech_quality": 4, "safety_tone": 5}, "verdict": "pass", "rationale": "Fine.", "issues": []}


class FakeJudges:
    """TEST FAKE judges: provider:model -> reply (or a function of the user prompt)."""

    def __init__(self, replies=None):
        self.replies = replies or {}
        self.calls = []
        self._lock = threading.Lock()

    def __call__(self, system, user, max_tokens, provider=None, model=None, client=None, **kw):
        with self._lock:
            self.calls.append((provider, model, system))
        reply = self.replies.get(f"{provider}:{model}", json.dumps(GOOD))
        return reply(user) if callable(reply) else reply


@pytest.fixture
def bucket():
    return eval_store.MemoryBucket()


@pytest.fixture
def evals(admin, bucket, monkeypatch):
    """Admin client with fake retrieval, fake models, an in-memory bucket, and keys 'set'."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-key")
    gen, judges = FakeLLM(), FakeJudges()
    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: gen
    app.dependency_overrides[admin_evals.get_judge_completer] = lambda: judges
    app.dependency_overrides[admin_evals.get_bucket] = lambda: bucket
    monkeypatch.setattr(admin_evals, "_price_listing", lambda: [])
    eval_store.write_questions_text(bucket, "\n".join(json.dumps(q) for q in QUESTIONS) + "\n")
    admin.gen, admin.judges, admin.bucket = gen, judges, bucket
    return admin


def run_body(**kw):
    body = {"name": "Test run", "generators": [{"provider": "anthropic", "model": "claude-haiku-4-5"}],
            "judges": [{"provider": "openai", "model": "gpt-6-luna"}], "top": 3, "confirm": True}
    body.update(kw)
    return body


def drive(client, run_id, limit=30):
    out = None
    for _ in range(limit):
        r = client.post(f"/api/admin/evals/runs/{run_id}/step")
        assert r.status_code == 200, r.text
        out = r.json()
        if out["progress"]["finished"]:
            return out
    return out


# ---------------------------------------------------------------- access

ROUTES = [
    ("get", "/api/admin/evals/questions"), ("post", "/api/admin/evals/questions"),
    ("put", "/api/admin/evals/questions/q001"), ("post", "/api/admin/evals/runs/estimate"),
    ("post", "/api/admin/evals/runs"), ("get", "/api/admin/evals/runs"),
    ("get", "/api/admin/evals/runs/20261007T222857Z"), ("post", "/api/admin/evals/runs/20261007T222857Z/step"),
    ("post", "/api/admin/evals/runs/20261007T222857Z/cancel"), ("get", "/api/admin/evals/report-card"),
    ("get", "/api/admin/evals/calibration"), ("post", "/api/admin/evals/calibration/step"),
    ("get", "/api/admin/evals/limits"),
]


@pytest.mark.parametrize("method, path", ROUTES)
def test_eval_routes_are_admin_only(student, method, path):
    r = getattr(student, method)(path, **({"json": {}} if method in ("post", "put") else {}))
    assert r.status_code == 401, path


def test_cross_site_posts_are_refused(evals):
    r = evals.post("/api/admin/evals/runs", json=run_body(), headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = evals.post("/api/admin/evals/runs/20261007T222857Z/step", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert admin_evals.eval_store.read_index(evals.bucket) == []


def test_eval_data_is_never_signed_into_a_link(monkeypatch, content_dir):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    assert storage.media_urls(["evals/questions.jsonl", "evals/runs/x/results.jsonl"]) == {}
    assert not storage.is_media_path("evals/index.json")


# ---------------------------------------------------------------- questions

def test_questions_summary_and_text_for_admin(evals):
    body = evals.get("/api/admin/evals/questions").json()
    assert body["count"] == 3 and body["uploaded"] is True
    assert body["categories"] == [{"category": "CONCEPT_QUESTION", "count": 2}, {"category": "MEETING_REQUEST", "count": 1}]
    assert body["questions"][0]["question"].startswith("Why do apples")
    assert "De-identified" in body["privacy"]


def test_missing_question_set_says_how_to_upload(evals):
    evals.bucket.objects.clear()
    body = evals.get("/api/admin/evals/questions").json()
    assert body["count"] == 0 and body["uploaded"] is False and "upload_eval_questions" in body["note"]


def test_bucket_questions_are_rechecked_on_every_read(evals):
    leaky = dict(QUESTIONS[0], question="Please email me at someone@example.edu about apples")
    eval_store.write_questions_text(evals.bucket, json.dumps(leaky) + "\n")
    r = evals.get("/api/admin/evals/questions")
    assert r.status_code == 409 and "email" in r.text and "someone@" not in r.text


def test_typed_and_edited_questions_get_the_dataset_checks(evals):
    r = evals.post("/api/admin/evals/questions", json={"question": "My teammate Jordan Lee broke the build",
                                                       "category": "TEAM_OR_GROUP_ISSUE"})
    assert r.status_code == 400 and "personal detail" in r.text and "Jordan" not in r.text
    r = evals.post("/api/admin/evals/questions", json={"question": "How do I read an elbow plot?",
                                                       "category": "concept_question", "answerable": True})
    assert r.status_code == 200 and r.json()["count"] == 4
    assert r.json()["questions"][3] == {"qid": "q004", "month": "", "course": "Unknown", "category": "CONCEPT_QUESTION",
                                        "question": "How do I read an elbow plot?", "reference_answer": None,
                                        "answerable": True}
    r = evals.put("/api/admin/evals/questions/q002", json={"question": "Call me at 4125551234567",
                                                          "category": "MEETING_REQUEST"})
    assert r.status_code == 400 and "long number" in r.text
    r = evals.put("/api/admin/evals/questions/q002", json={"question": "Can we meet about my project?",
                                                          "category": "MEETING_REQUEST"})
    assert r.status_code == 200 and r.json()["questions"][1]["question"] == "Can we meet about my project?"
    assert evals.put("/api/admin/evals/questions/q099", json={"question": "x y", "category": "OTHER"}).status_code == 404


# ---------------------------------------------------------------- creating a run: guards

def test_estimate_counts_calls_and_flags_self_grading(evals):
    body = run_body(generators=[{"provider": "openai", "model": "gpt-6.1-sol"}],
                    judges=[{"provider": "openai", "model": "gpt-6.1-sol"}, {"provider": "anthropic", "model": "claude-opus-5-5"}])
    est = evals.post("/api/admin/evals/runs/estimate", json=body).json()
    assert est["questions"] == 3
    assert est["estimate"]["calls_typical"] == 3 * (2 + 2) and est["estimate"]["calls_max"] == 3 * (3 + 2)
    assert est["self_grading"] == [{"generator": "openai:gpt-6.1-sol", "judge": "openai:gpt-6.1-sol"}]
    assert "68%" in est["self_grading_note"]


def test_rough_cost_uses_openrouter_prices_for_the_same_model():
    listing = [{"id": "anthropic/claude-sonnet-5.5", "pricing": {"prompt": "0.000003", "completion": "0.000015"}},
               {"id": "openai/gpt-6.1-sol", "pricing": {"prompt": "0.000002", "completion": "0.000008"}}]
    assert admin_evals.price_per_token("anthropic", "claude-sonnet-5-5", listing) == (0.000003, 0.000015)
    est = admin_evals.estimate(2, [{"provider": "anthropic", "model": "claude-sonnet-5-5"}],
                               [{"provider": "openai", "model": "gpt-6.1-sol"}], listing)
    gen_cost = 2 * (4600 * 0.000003 + 760 * 0.000015)
    judge_cost = 2 * (2500 * 0.000002 + 350 * 0.000008)
    assert est["cost_usd"] == round(gen_cost + judge_cost, 2)
    unknown = admin_evals.estimate(2, [{"provider": "openai", "model": "gpt-9"}], [], listing)
    assert unknown["cost_usd"] is None and unknown["cost_unknown_for"] == ["openai:gpt-9"]


@pytest.mark.parametrize("change, status, text", [
    ({"confirm": False}, 400, "confirm"),
    ({"generators": [{"provider": "anthropic", "model": f"m{i}"} for i in range(4)]}, 400, "1 to 3 models"),
    ({"judges": [{"provider": "openai", "model": f"j{i}"} for i in range(4)]}, 400, "1 to 3 judges"),
    ({"judges": []}, 400, "1 to 3 judges"),
    ({"top": 31}, 400, "1 to 30"),
    ({"judges": [{"provider": "jev", "model": "jev"}]}, 400, "command line only"),
    ({"generators": [{"provider": "openrouter", "model": "x/y"}]}, 400, "OPENROUTER_API_KEY"),
    ({"judges": [{"provider": "openai", "model": "bad id!"}]}, 400, "does not look right"),
    ({"categories": ["NOPE"]}, 400, "Unknown category"),
    ({"categories": ["GRADING_QUESTION"]}, 400, "No questions"),
    ({"judges": [{"provider": "openai", "model": "a"}, {"provider": "openai", "model": "a"}]}, 400, "twice"),
])
def test_run_guards(evals, change, status, text):
    r = evals.post("/api/admin/evals/runs", json=run_body(**change))
    assert r.status_code == status and text in r.text, r.text


def test_priced_out_openrouter_models_are_refused(evals, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-not-a-key")
    listing = [{"id": "vendor/pricey", "pricing": {"prompt": "0.0001", "completion": "0.0006"}}]
    monkeypatch.setattr(llm, "list_models", lambda provider: {"provider": provider, "models": listing, "source": "live"})
    r = evals.post("/api/admin/evals/runs", json=run_body(judges=[{"provider": "openrouter", "model": "vendor/pricey"}]))
    assert r.status_code == 400 and "over the limit" in r.text


def test_run_over_the_per_run_call_cap_is_refused(evals, monkeypatch):
    monkeypatch.setenv("EVAL_MAX_CALLS_PER_RUN", "10")
    r = evals.post("/api/admin/evals/runs", json=run_body())
    assert r.status_code == 400 and "per-run cap of 10" in r.text


def test_one_active_run_at_a_time(evals):
    first = evals.post("/api/admin/evals/runs", json=run_body())
    assert first.status_code == 201
    second = evals.post("/api/admin/evals/runs", json=run_body())
    assert second.status_code == 409 and first.json()["run"]["id"] in second.text
    run_id = first.json()["run"]["id"]
    assert evals.post(f"/api/admin/evals/runs/{run_id}/cancel").json()["progress"]["status"] == "cancelled"
    assert evals.post("/api/admin/evals/runs", json=run_body()).status_code == 201


# ---------------------------------------------------------------- steps

def test_steps_answer_with_the_chosen_generator_and_judge_each_pair(evals):
    body = run_body(generators=[{"provider": "anthropic", "model": "claude-haiku-4-5"},
                                {"provider": "openai", "model": "gpt-6-luna"}],
                    judges=[{"provider": "openai", "model": "gpt-6.1-sol"}, {"provider": "anthropic", "model": "claude-opus-5-5"}])
    created = evals.post("/api/admin/evals/runs", json=body).json()
    run_id = created["run"]["id"]
    assert created["progress"] == {**created["progress"], "done": 0, "total": 6, "finished": False}

    first = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()
    assert first["progress"]["done"] == 1 and first["progress"]["fraction"] == round(1 / 6, 4)
    row = first["row"]
    assert row["generator"] == "anthropic:claude-haiku-4-5" and row["outcome"] == "course_content"
    assert [s["slide_id"] for s in row["response"]["segments"]] == FRUIT_IDS
    assert all("evidence" not in s and s["has_evidence"] for s in row["response"]["segments"])
    assert sorted(j["judge"] for j in row["judgements"]) == ["anthropic:claude-opus-5-5", "openai:gpt-6.1-sol"]
    # Every generator call in that step asked for the chosen model, and llm_choice() agreed inside the step.
    assert evals.gen.calls and all(
        (c["provider"], c["model"]) == ("anthropic", "claude-haiku-4-5") == c["choice"] for c in evals.gen.calls)

    out = drive(evals, run_id)
    assert out["progress"]["status"] == "done" and out["progress"]["done"] == 6
    detail = evals.get(f"/api/admin/evals/runs/{run_id}").json()
    rows = detail["rows"]
    assert len(rows) == 6 and len({(r["qid"], r["generator"]) for r in rows}) == 6
    assert [r["pair"] for r in rows] == list(range(6))  # question-major order
    outcomes = {r["qid"]: r["outcome"] for r in rows}
    assert outcomes["q002"] == "not_covered"
    by_gen = detail["run"]["summary"]["by_generator"]
    assert set(by_gen) == {"anthropic:claude-haiku-4-5", "openai:gpt-6-luna"}
    assert by_gen["openai:gpt-6-luna"]["pass_rate"] == 1.0 and by_gen["openai:gpt-6-luna"]["questions"] == 3
    # The judges got the rubric prompt, and the saved live model never changed.
    assert all(system == eval_core.SYSTEM_PROMPT for _, _, system in evals.judges.calls)
    assert settings_store.llm_choice() == ("anthropic", "claude-sonnet-5-5")


def test_step_is_idempotent_after_done_and_never_redoes_a_pair(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    drive(evals, run_id)
    calls = len(evals.gen.calls) + len(evals.judges.calls)
    again = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()
    assert again["progress"]["finished"] and again["row"] is None
    assert len(evals.gen.calls) + len(evals.judges.calls) == calls
    assert len(eval_store.read_results(evals.bucket, run_id)) == 2


def test_a_pair_already_recorded_is_skipped(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    eval_store.write_row(evals.bucket, run_id, {"qid": "q001", "pair": 0, "generator": "anthropic:claude-haiku-4-5",
                                                "category": "CONCEPT_QUESTION", "answerable": True,
                                                "response": {"status": "ok", "latency_ms": 1}, "judgements": [], "calls": 3})
    out = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()
    assert out["row"]["qid"] != "q001" and out["progress"]["done"] == 2 and out["progress"]["status"] == "done"
    assert out["progress"]["calls_used"] == 3 + out["row"]["calls"]  # spend counted from the rows themselves


def test_a_live_lease_makes_a_second_step_wait(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    run = eval_store.read_run(evals.bucket, run_id)
    run["lease"] = {"pair": 0, "until": 9e12}
    eval_store.write_run(evals.bucket, run)
    out = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()
    assert out["waiting"]["seconds"] >= 3 and out["progress"]["done"] == 0 and not evals.gen.calls


def test_a_busy_embedding_service_waits_instead_of_recording(evals):
    from app import embed

    def busy(_q):
        raise embed.EmbeddingError("Voyage returned 429")

    app.dependency_overrides[get_embedder] = lambda: busy
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    out = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()
    assert out["waiting"]["seconds"] > 0 and out["progress"]["done"] == 0
    assert eval_store.read_results(evals.bucket, run_id) == []
    assert eval_store.read_run(evals.bucket, run_id)["lease"] is None


def test_judge_errors_are_recorded_not_raised(evals):
    evals.judges.replies["openai:gpt-6-luna"] = "not json at all"
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=1)).json()["run"]["id"]
    row = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()["row"]
    assert row["judgements"][0]["judge"] == "openai:gpt-6-luna" and "error" in row["judgements"][0]


# ---------------------------------------------------------------- spend

def test_eval_calls_count_against_the_eval_cap(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=1)).json()["run"]["id"]
    row = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()["row"]
    used = limits.read_counter(limits.eval_calls_key())
    assert used == row["calls"] == 3  # classifier + narration + one judge
    assert eval_store.read_run(evals.bucket, run_id)["calls_used"] == 3


def test_daily_eval_cap_stops_a_step(evals, monkeypatch):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    monkeypatch.setenv("DAILY_EVAL_LLM_CALL_CAP", "2")
    r = evals.post(f"/api/admin/evals/runs/{run_id}/step")
    assert r.status_code == 429 and "DAILY_EVAL_LLM_CALL_CAP" in r.text and not evals.gen.calls


def test_evals_leave_a_reserve_of_global_calls_for_students(evals, monkeypatch):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    monkeypatch.setenv("DAILY_LLM_CALL_CAP", "50")  # under the default reserve of 100
    r = evals.post(f"/api/admin/evals/runs/{run_id}/step")
    assert r.status_code == 429 and "students" in r.text


def test_run_cap_is_enforced_per_step(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    cap = eval_store.read_run(evals.bucket, run_id)["call_cap"]
    eval_store.write_row(evals.bucket, run_id, {"qid": "q001", "pair": 0, "generator": "anthropic:claude-haiku-4-5",
                                                "response": {"status": "ok"}, "judgements": [], "calls": cap - 1})
    r = evals.post(f"/api/admin/evals/runs/{run_id}/step")
    assert r.status_code == 429 and "EVAL_MAX_CALLS_PER_RUN" in r.text


# ---------------------------------------------------------------- the override never leaks

def test_override_is_isolated_per_context():
    seen = {}

    def other_request():
        seen["other"] = settings_store.llm_choice()

    with llm.model_override("openai", "gpt-6-luna"):
        assert settings_store.llm_choice() == ("openai", "gpt-6-luna")
        t = threading.Thread(target=other_request)  # a new thread starts with a fresh context
        t.start()
        t.join()
        assert contextvars.copy_context().run(settings_store.llm_choice) == ("openai", "gpt-6-luna")
    assert seen["other"] == ("anthropic", "claude-sonnet-5-5")
    assert settings_store.llm_choice() == ("anthropic", "claude-sonnet-5-5")
    with pytest.raises(llm.LLMError):
        with llm.model_override("gemini", "x"):
            pass


def test_students_never_see_an_eval_generator(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=1)).json()["run"]["id"]
    evals.post(f"/api/admin/evals/runs/{run_id}/step")
    evals.gen.calls.clear()
    assert evals.post("/api/login", json={"passcode": "student-pass"}).status_code == 204
    r = evals.post("/api/ask", json={"question": "Why do apples turn brown?"})
    assert r.status_code == 200 and r.json()["covered"]
    assert evals.gen.calls and all(c["choice"] == ("anthropic", "claude-sonnet-5-5")
                                   and c["model"] == "claude-sonnet-5-5" for c in evals.gen.calls)


# ---------------------------------------------------------------- report card and runs list

def test_runs_list_and_report_card_over_two_runs(evals):
    first = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    drive(evals, first)
    evals.judges.replies["openai:gpt-6-luna"] = json.dumps(dict(GOOD, verdict="fail"))
    second = evals.post("/api/admin/evals/runs", json=run_body(top=2, name="Second")).json()["run"]["id"]
    drive(evals, second)
    runs = evals.get("/api/admin/evals/runs").json()
    assert [r["id"] for r in runs["runs"]][:2] == sorted([first, second], reverse=True) and runs["active"] is None
    listed = json.dumps(runs["runs"])
    assert not any(q["question"] in listed for q in QUESTIONS)  # no question text in the list
    card = evals.get("/api/admin/evals/report-card").json()
    (series,) = card["series"]
    assert series["generator"] == "anthropic:claude-haiku-4-5"
    assert [p["pass_rate"] for p in series["points"]] == [1.0, 0.0]
    assert card["metrics"] == ["pass_rate", "decline_accuracy", "fallback_rate", "judge_agreement"]


def test_report_card_skips_excluded_runs_and_labels_series():
    runs = [
        {"id": "a", "created_at": "2026-10-07T22:27:46Z", "excluded": True, "status": "excluded",
         "by_generator": {"anthropic:m": {"pass_rate": 0.5}}},
        {"id": "b", "created_at": "2026-10-07T22:28:57Z", "status": "done", "kind": "imported",
         "notes": ["predates PR #35"], "generator_labels": {"anthropic:m": "Twin (m)"},
         "by_generator": {"anthropic:m": {"pass_rate": 0.55, "scores": {"grounded": 3.0}}}},
        {"id": "baseline-20261005", "created_at": "2026-10-05T00:00:00Z", "status": "done", "kind": "baseline",
         "by_generator": {"baseline:openai:x": {"pass_rate": 0.48}}},
    ]
    card = admin_evals.report_card(runs, {"openai:x": {"met": 8, "cases": 8, "done": True, "missed": []}})
    assert [s["generator"] for s in card["series"]] == ["baseline:openai:x", "anthropic:m"]
    twin = card["series"][1]
    assert twin["label"] == "Twin (m)" and [p["run_id"] for p in twin["points"]] == ["b"]
    assert twin["points"][0]["notes"] == ["predates PR #35"]
    assert card["calibration"] == [{"judge": "openai:x", "met": 8, "cases": 8, "done": True, "missed": [],
                                    "at": None, "source": "settings"}]


# ---------------------------------------------------------------- calibration

def test_calibration_loops_case_by_case_then_reports(evals):
    cases = eval_core.load_calibration_cases()
    body = {"provider": "openai", "model": "gpt-6-luna", "restart": True}
    out = evals.post("/api/admin/evals/calibration/step", json=body).json()
    assert len(out["result"]["rows"]) == 1 and not out["result"]["done"]
    body["restart"] = False
    for _ in range(len(cases) + 2):
        out = evals.post("/api/admin/evals/calibration/step", json=body).json()
    res = out["result"]
    assert res["done"] and res["cases"] == len(cases) and len(res["rows"]) == len(cases)
    # An always-pass judge with fixed scores meets the "good" cases and misses the "bad" ones.
    assert 0 < res["met"] < len(cases) and "c02-invented-fact" in res["missed"]
    got = evals.get("/api/admin/evals/calibration").json()
    assert got["judges"]["openai:gpt-6-luna"]["met"] == res["met"]
    assert [c["cid"] for c in got["cases"]] == [c["cid"] for c in cases]
    card = evals.get("/api/admin/evals/report-card").json()
    assert card["calibration"][0]["judge"] == "openai:gpt-6-luna"


# ---------------------------------------------------------------- what the judges are shown

def test_faq_and_referrals_reach_the_judges_in_words(content_dir, monkeypatch):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    content = storage.store.get_or_503()
    faq = {"covered": False, "kind": "faq", "segments": [], "answers": [{"course": "all", "text": "Book a slot on my page."}]}
    r = admin_evals.response_from(faq, {"kind": "faq"}, content, 5)
    assert r["status"] == "not_covered" and "Book a slot" in r["message"] and r["outcome"] == "faq"
    assert "Book a slot" in eval_core.build_user_prompt({"question": "q", "category": "MEETING_REQUEST",
                                                         "answerable": False, "response": r})
    info = {"covered": True, "kind": "course_info", "segments": [], "answers": [{"text": "The quiz is Friday."}]}
    r = admin_evals.response_from(info, {"kind": "course_info"}, content, 5)
    assert r["status"] == "ok" and r["segments"][0]["narration"] == "The quiz is Friday."


def test_self_grading_matches_across_providers():
    pairs = admin_evals.self_grading([{"provider": "anthropic", "model": "claude-sonnet-5-5"}],
                                     [{"provider": "openrouter", "model": "anthropic/claude-sonnet-5.5"},
                                      {"provider": "openai", "model": "gpt-6.1-sol"}])
    assert pairs == [{"generator": "anthropic:claude-sonnet-5-5", "judge": "openrouter:anthropic/claude-sonnet-5.5"}]


# ---------------------------------------------------------------- storage round trip

@pytest.mark.parametrize("kind", ["memory", "local"])
def test_store_round_trip(tmp_path, kind):
    bucket = eval_store.MemoryBucket() if kind == "memory" else eval_store.LocalBucket(tmp_path)
    run = {"id": "20261008T010203Z", "name": "x", "created_at": "2026-10-08T01:02:03Z", "status": "running"}
    eval_store.write_run(bucket, run)
    eval_store.write_results(bucket, run["id"], [{"qid": "q001", "text": "naïve"}])
    eval_store.upsert_index(bucket, {"id": run["id"], "created_at": run["created_at"]})
    eval_store.upsert_index(bucket, {"id": "20261009T010203Z", "created_at": "2026-10-09T01:02:03Z"})
    eval_store.upsert_index(bucket, {"id": run["id"], "created_at": run["created_at"], "status": "done"})
    assert eval_store.read_run(bucket, run["id"]) == run
    assert eval_store.read_results(bucket, run["id"]) == [{"qid": "q001", "text": "naïve"}]
    assert [r["id"] for r in eval_store.read_index(bucket)] == ["20261009T010203Z", run["id"]]
    assert eval_store.read_index(bucket)[1]["status"] == "done"
    assert eval_store.read_run(bucket, "20261010T000000Z") is None
    for bad in ("../x", "x/y", "20261008T010203Z/../../secrets"):
        with pytest.raises(eval_store.StoreError):
            eval_store.run_path(bad, "run.json")
    with pytest.raises(eval_store.StoreError):
        bucket.put("content/index.json", b"{}")
    eval_store.write_row(bucket, run["id"], {"pair": 3, "qid": "q002"})
    assert eval_store.row_pairs(bucket, run["id"]) == {3}
    assert sorted(bucket.list(f"evals/runs/{run['id']}/")) == ["results.jsonl", "rows", "run.json"]
    assert [r.get("pair") for r in eval_store.read_results(bucket, run["id"])] == [3, None]
    if kind == "local":
        assert (tmp_path / "evals" / "runs" / run["id"] / "run.json").is_file()


def test_supabase_bucket_uses_the_private_bucket(monkeypatch):
    from app import supa

    seen = {}
    monkeypatch.setattr(supa, "download_optional", lambda path: seen.setdefault("get", path) and None)
    monkeypatch.setattr(supa, "upload", lambda path, data, ct, upsert=False, cache_control=None:
                        seen.update(put=(path, ct, upsert, cache_control)))
    monkeypatch.setattr(supa, "list_objects", lambda prefix, limit=100: seen.update(list=(prefix, limit)) or [])
    b = eval_store.SupabaseBucket()
    assert b.get("evals/index.json") is None
    b.put("evals/index.json", b"{}")
    assert b.list("evals/runs/x/") == []
    assert seen == {"get": "evals/index.json", "put": ("evals/index.json", "application/json", True, "no-cache, max-age=0"),
                    "list": ("evals/runs/x/", 1000)}


def test_bad_run_ids_are_refused(evals):
    for path in ("/api/admin/evals/runs/not-a-run", "/api/admin/evals/runs/..%2F..%2Fcontent"):
        assert evals.get(path).status_code in (400, 404)
    assert evals.post("/api/admin/evals/runs/not-a-run/step").status_code == 400


def test_analytics_model_performance_reads_settings_runs_and_skips_excluded(evals):
    """Settings > Analytics averages eval scores from the same bucket files (app/analytics.py)."""
    from app import analytics

    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=2)).json()["run"]["id"]
    drive(evals, run_id)
    eval_store.upsert_index(evals.bucket, {"id": "20261001T000000Z", "created_at": "2026-10-01T00:00:00Z",
                                           "excluded": True, "status": "excluded"})
    eval_store.write_results(evals.bucket, "20261001T000000Z", [
        {"qid": "q001", "generator": "anthropic:claude-haiku-4-5", "response": {"status": "ok", "latency_ms": 5},
         "judgements": [dict(GOOD, judge="x", verdict="fail")]}])
    out = analytics.eval_performance(read=evals.bucket.get)
    (model,) = out["models"]
    assert out["runs"] == 1 and model["model"] == "anthropic:claude-haiku-4-5"
    assert model["answers"] == 2 and model["pass_rate"] == 1.0 and model["median_latency_ms"] is not None


class StaleBucket(eval_store.MemoryBucket):
    """TEST FAKE of the Storage CDN at its worst: every object read returns the first copy ever read.

    Listing stays fresh (it is a database query). Nothing a step depends on may be lost to this.
    """

    def __init__(self):
        super().__init__()
        self.served = {}

    def get(self, path):
        if path not in self.served:
            self.served[path] = super().get(path)
        return self.served[path]


def test_runs_survive_stale_bucket_reads(admin, monkeypatch):
    bucket = StaleBucket()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-key")
    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: FakeLLM()
    app.dependency_overrides[admin_evals.get_judge_completer] = lambda: FakeJudges()
    app.dependency_overrides[admin_evals.get_bucket] = lambda: bucket
    monkeypatch.setattr(admin_evals, "_price_listing", lambda: [])
    eval_store.write_questions_text(bucket, "\n".join(json.dumps(q) for q in QUESTIONS) + "\n")
    body = run_body(generators=[{"provider": "anthropic", "model": "claude-haiku-4-5"},
                                {"provider": "openai", "model": "gpt-6-luna"}])
    first = admin.post("/api/admin/evals/runs", json=body).json()["run"]["id"]
    out = drive(admin, first)
    assert out["progress"]["finished"] and out["progress"]["done"] == 6
    fresh = eval_store.MemoryBucket()
    fresh.objects = bucket.objects  # what is really stored
    rows = eval_store.read_results(fresh, first)
    assert sorted(r["pair"] for r in rows) == list(range(6))
    assert len(eval_store.read_jsonl(fresh, eval_store.run_path(first, "results.jsonl"))) == 6
    second = admin.post("/api/admin/evals/runs", json=run_body(top=1, name="Second")).json()["run"]["id"]
    drive(admin, second)
    assert {r["id"] for r in eval_store.read_index(fresh)} == {first, second}
    assert {r["id"] for r in json.loads(fresh.objects["evals/index.json"])["runs"]} == {first, second}


def test_cancel_reaches_a_step_through_the_marker(evals):
    run_id = evals.post("/api/admin/evals/runs", json=run_body(top=3)).json()["run"]["id"]
    evals.post(f"/api/admin/evals/runs/{run_id}/step")
    eval_store.mark_cancelled(evals.bucket, run_id, "2026-10-08T00:00:00+00:00")  # as if run.json were stale
    out = evals.post(f"/api/admin/evals/runs/{run_id}/step").json()
    assert out["progress"]["status"] == "cancelled" and out["progress"]["done"] == 1
