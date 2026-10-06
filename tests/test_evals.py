"""Evals harness: dataset checks, question selection, judge parsing, the run loop, and reports.

Uses the synthetic content fixture, the same TEST FAKE retriever pattern as
tests/test_api.py (no similarity math, not Ben's selection rules), and fake
judges. No network, no keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from evals import dataset, report, rubric
from evals.judges import Judge, JudgeError
from evals.run import NoTarget, evaluate, main, write_run
from evals.targets import HttpTarget, InProcessTarget

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "evals" / "questions.example.jsonl"
FRUIT_IDS = ["70445-s01-002", "70445-s01-003"]


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def q(category: str, text: str = "How do I choose k?", answerable: bool = True, **extra) -> dict:
    return {"month": "2026-09", "course": "Example", "category": category, "question": text,
            "reference_answer": extra.get("ref"), "answerable_from_course_materials": answerable}


# ---------------------------------------------------------------- dataset

def test_example_file_loads():
    qs = dataset.load(EXAMPLES)
    assert len(qs) == 6
    assert qs[0].qid == "q001" and qs[0].category == "API_KEY_NOT_WORKING"


@pytest.mark.parametrize(
    "text, reason",
    [
        ("Email me at someone@andrew.cmu.edu please", "email"),
        ("My id is 123456789", "long number"),
        ("See https://example.com/doc/abc", "url"),
        ("my key sk-abcdefghijklmnop fails", "api key"),
        ("My teammate Jordan Lee did the code", "personal detail"),
    ],
)
def test_records_with_personal_details_are_refused(tmp_path, text, reason):
    path = write_jsonl(tmp_path / "q.jsonl", [q("CODE_HELP", text)])
    with pytest.raises(dataset.DatasetError) as err:
        dataset.load(path)
    assert reason in str(err.value)
    assert text not in str(err.value)  # the error names the problem, not the text


def test_full_dates_and_unknown_categories_are_refused(tmp_path):
    bad_month = dict(q("CODE_HELP"), month="2026-09-14")
    with pytest.raises(dataset.DatasetError, match="YYYY-MM"):
        dataset.load(write_jsonl(tmp_path / "a.jsonl", [bad_month]))
    with pytest.raises(dataset.DatasetError, match="unknown category"):
        dataset.load(write_jsonl(tmp_path / "b.jsonl", [q("HOMEWORK_PANIC")]))


def test_select_top_round_robins_by_frequency(tmp_path):
    rows = [q("MEETING_REQUEST", f"Meeting {i}?", False) for i in range(5)]
    rows += [q("CODE_HELP", f"Code {i}?") for i in range(3)]
    rows += [q("GRADING_QUESTION", "Grade?", False)]
    qs = dataset.load(write_jsonl(tmp_path / "q.jsonl", rows))
    assert dataset.category_counts(qs)[0] == ("MEETING_REQUEST", 5)
    picked = dataset.select_top(qs, 5)
    assert [p.category for p in picked] == [
        "MEETING_REQUEST", "CODE_HELP", "GRADING_QUESTION", "MEETING_REQUEST", "CODE_HELP"
    ]
    assert picked[0].question == "Meeting 0?"  # most recent first within a category
    assert dataset.select_top(qs, 50) == qs


# ---------------------------------------------------------------- rubric

GOOD = {
    "scores": {"grounded": 5, "answers_question": 4, "correct_scope": 5, "matches_reference": None,
               "speech_quality": 4, "safety_tone": 5},
    "verdict": "pass", "rationale": "Clear and grounded.", "issues": [],
}


def test_parse_accepts_fenced_json_and_rounds_scores():
    raw = "```json\n" + json.dumps(dict(GOOD, scores=dict(GOOD["scores"], grounded=4.6))) + "\n```"
    out = rubric.parse(raw)
    assert out["scores"]["grounded"] == 5 and out["scores"]["matches_reference"] is None
    assert out["verdict"] == "pass"


@pytest.mark.parametrize(
    "bad",
    ["not json", json.dumps({"verdict": "pass"}), json.dumps(dict(GOOD, verdict="maybe")),
     json.dumps(dict(GOOD, scores=dict(GOOD["scores"], safety_tone=9)))],
)
def test_parse_rejects_bad_replies(bad):
    with pytest.raises(rubric.JudgementError):
        rubric.parse(bad)


def test_prompt_marks_missing_evidence_and_expected_behavior():
    item = {"question": "Can I get an extension?", "category": "EXTENSION_REQUEST", "answerable": False,
            "reference_answer": None,
            "response": {"status": "ok", "segments": [{"n": 1, "slide_id": "x", "narration": "Hi", "evidence": None}],
                         "follow_ups": [], "narration_source": "llm"}}
    text = rubric.build_user_prompt(item)
    assert "decline, it is not course content" in text
    assert "not available to the evaluator" in text


# ---------------------------------------------------------------- judges

def fake_judge(name: str, reply) -> Judge:
    provider, model = name.split(":")
    return Judge(provider, model, call=lambda system, user: reply(user) if callable(reply) else reply)


def test_judge_spec_parsing():
    assert Judge.parse_spec("openai:gpt-6-astra").name == "openai:gpt-6-astra"
    with pytest.raises(JudgeError):
        Judge.parse_spec("acme:model")
    assert "ANTHROPIC_API_KEY" in Judge.parse_spec("anthropic:claude-opus-5-5").ready()


def test_judge_retries_then_reports_error_without_raising():
    calls = []

    def flaky(system, user):
        calls.append(1)
        return "garbage" if len(calls) < 2 else json.dumps(GOOD)

    ok = Judge("anthropic", "m", call=flaky).judge(
        {"question": "q", "category": "CODE_HELP", "answerable": True, "reference_answer": None,
         "response": {"status": "not_covered", "segments": []}}, sleep=lambda s: None)
    assert ok["verdict"] == "pass" and len(calls) == 2
    bad = fake_judge("openai:m", "garbage").judge(
        {"question": "q", "category": "CODE_HELP", "answerable": True, "reference_answer": None,
         "response": {"status": "not_covered", "segments": []}}, sleep=lambda s: None)
    assert "error" in bad and bad["judge"] == "openai:m"


# ---------------------------------------------------------------- in-process target (TEST FAKES)

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


def TEST_FAKE_llm(system, user, max_tokens, provider=None, model=None):
    ids = [s["slide_id"] for s in json.loads(user.split("Slides, in the order they will be shown:\n", 1)[1])]
    return json.dumps({"segments": [{"slide_id": i, "narration": f"On this slide, an apple {i}."} for i in ids],
                       "follow_ups": ["What is a banana?"]})


@pytest.fixture
def in_process(content_dir, monkeypatch):
    from app import main, storage

    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    return InProcessTarget(main.Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5), TEST_FAKE_embedder,
                           TEST_FAKE_llm, storage.store.get_or_503())


def test_in_process_target_returns_segments_with_evidence(in_process):
    out = in_process.ask("What is an apple?")
    assert out["status"] == "ok"
    assert [s["slide_id"] for s in out["segments"]] == FRUIT_IDS
    assert out["segments"][0]["evidence"]["title"] == "What is an apple"
    assert out["narration_source"] in ("llm", "fallback")


def test_in_process_target_reports_not_covered(in_process):
    assert in_process.ask("Will it rain tomorrow?")["status"] == "not_covered"


def test_in_process_target_reports_retrieval_not_ready(content_dir, monkeypatch):
    from app import main, retrieval, storage

    monkeypatch.setenv("CONTENT_DIR", str(content_dir))
    target = InProcessTarget(main.Retriever(retrieval.rank, retrieval.select_segments, None),
                             TEST_FAKE_embedder, TEST_FAKE_llm, storage.store.get_or_503())
    try:
        retrieval.rank(np.ones(8, dtype=np.float32), np.zeros((0, 8), dtype=np.float32))
    except NotImplementedError:
        assert target.ask("What is an apple?")["status"] == "retrieval_not_ready"
    else:
        pytest.skip("Ben's retrieval is written; nothing to check here")


# ---------------------------------------------------------------- http target

def test_http_target_logs_in_and_maps_statuses():
    import httpx

    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/login":
            return httpx.Response(204)
        body = json.loads(request.content)
        if "apple" in body["question"]:
            return httpx.Response(200, json={"covered": True, "segments": [{"n": 1, "slide_id": "a", "narration": "x"}],
                                             "follow_ups": []})
        if "rain" in body["question"]:
            return httpx.Response(200, json={"covered": False, "segments": []})
        return httpx.Response(503, json={"detail": "retrieval not implemented yet"})

    t = HttpTarget("https://site.test", passcode="p", spacing=0,
                   client=httpx.Client(transport=httpx.MockTransport(handler)), sleep=lambda s: None)
    assert t.ask("apple?")["status"] == "ok"
    assert t.ask("rain?")["status"] == "not_covered"
    assert t.ask("other")["status"] == "retrieval_not_ready"
    assert seen[0] == "/api/login" and seen.count("/api/login") == 1


# ---------------------------------------------------------------- run loop and reports

def test_evaluate_and_reports(in_process, tmp_path):
    qs = dataset.load(write_jsonl(tmp_path / "q.jsonl", [
        q("CONCEPT_QUESTION", "What is an apple?", True, ref="A fruit."),
        q("EXTENSION_REQUEST", "Can I get more time on the lab?", False),
        q("CONCEPT_QUESTION", "Explain apple varieties", True),
    ]))
    judges = [
        fake_judge("anthropic:a", json.dumps(GOOD)),
        fake_judge("openai:b", lambda user: json.dumps(dict(GOOD, verdict="fail")) if "declined" in user
                   else json.dumps(GOOD)),
    ]
    results = evaluate(qs, in_process, judges)
    assert [r["response"]["status"] for r in results] == ["ok", "not_covered", "ok"]
    assert all(len(r["judgements"]) == 2 for r in results)

    s = report.summary(results, {"target": "test"})
    assert s["scope_right_call_rate"] == 1.0
    assert s["judges"]["anthropic:a"]["pass_rate"] == 1.0
    assert s["judges"]["openai:b"]["pass_rate"] == round(2 / 3, 2)
    assert s["judge_agreement"]["anthropic:a vs openai:b"]["verdict_agreement"] == round(2 / 3, 2)

    out = write_run(results, {"target": "test"}, tmp_path / "run")
    summary_text = (out / "summary.md").read_text() + (out / "summary.json").read_text()
    for r in results:  # the shareable summary never carries question text or narration
        assert r["question"] not in summary_text
        for seg in r["response"]["segments"]:
            assert seg["narration"] not in summary_text
    assert "What is an apple?" in (out / "report.md").read_text()


def test_no_target_skips_judges(tmp_path):
    qs = dataset.load(EXAMPLES)
    results = evaluate(qs, NoTarget(), [fake_judge("anthropic:a", json.dumps(GOOD))])
    assert all(r["response"]["status"] == "retrieval_not_ready" and r["judgements"] == [] for r in results)


def test_cli_dry_run_writes_only_to_out(tmp_path, capsys):
    out = tmp_path / "run"
    assert main(["--questions", str(EXAMPLES), "--target", "none", "--top", "3", "--out", str(out)]) == 0
    assert sorted(p.name for p in out.iterdir()) == ["report.md", "results.jsonl", "summary.json", "summary.md"]
    assert "retrieval not implemented yet" in capsys.readouterr().out


def test_cli_stops_when_judge_keys_are_missing(tmp_path, monkeypatch):
    monkeypatch.setattr("evals.run.load_dotenv", lambda *a, **k: None)  # a real .env on the Mac mini has keys
    assert main(["--questions", str(EXAMPLES), "--target", "none", "--judge", "openai:gpt-6-astra",
                 "--out", str(tmp_path / "r")]) == 3


# ---------------------------------------------------------------- judge calibration

def test_calibration_cases_load_and_carry_expectations():
    from evals import calibrate

    cases = calibrate.load_cases()
    assert len(cases) >= 8 and len({c["cid"] for c in cases}) == len(cases)
    for c in cases:
        assert c["expect"] and ("verdict" in c["expect"] or c["expect"].get("min") or c["expect"].get("max"))
        rubric.build_user_prompt(c)  # every case renders into a judge prompt


def test_calibration_check_and_scoring():
    from evals import calibrate

    cases = calibrate.load_cases()
    good = next(c for c in cases if c["cid"] == "c01-grounded-good")
    bad = next(c for c in cases if c["cid"] == "c02-invented-fact")
    assert calibrate.check(good, dict(GOOD, judge="x")) == []
    misses = calibrate.check(bad, dict(GOOD, judge="x"))
    assert any("verdict" in m for m in misses) and any("grounded" in m for m in misses)
    assert calibrate.check(bad, {"judge": "x", "error": "timeout"})[0].startswith("judge error")

    always_pass = fake_judge("openai:lenient", json.dumps(GOOD))
    out = calibrate.calibrate([good, bad], [always_pass])
    assert out["per_judge"]["openai:lenient"] == {"cases": 2, "met": 1, "missed": ["c02-invented-fact"]}


def test_judge_does_not_retry_a_rejected_key():
    import httpx

    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"message": "bad key"}})

    j = Judge("openai", "m", client=httpx.Client(transport=httpx.MockTransport(handler)))
    import os
    os.environ["OPENAI_API_KEY"] = "test-key-not-real"
    try:
        out = j.judge({"question": "q", "category": "CODE_HELP", "answerable": True, "reference_answer": None,
                       "response": {"status": "not_covered", "segments": []}}, sleep=lambda s: None)
    finally:
        del os.environ["OPENAI_API_KEY"]
    assert "401" in out["error"] and len(calls) == 1
