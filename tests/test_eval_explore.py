"""Settings > Evals read-only views: questions hardest first, one question's judges, and model x judge comparisons.

Rows are invented (no real eval questions). Routes are checked for admin-only access and filter validation.
"""

from __future__ import annotations

import pytest

# app.main first: it wires the routers that the admin modules import from.
from app.main import app  # noqa: F401

# isort: split
from app import admin_evals, eval_explore  # noqa: E402

S = {
    "grounded": 4,
    "answers_question": 4,
    "correct_scope": 5,
    "matches_reference": None,
    "speech_quality": 4,
    "safety_tone": 5,
}


def j(judge, verdict, **scores):
    return {"judge": judge, "verdict": verdict, "scores": {**S, **scores}, "rationale": f"{judge} says {verdict}"}


def row(qid, gen, judgements, **extra):
    return {
        "qid": qid,
        "question": f"Invented question {qid}",
        "category": extra.pop("category", "CONCEPT_QUESTION"),
        "course": None,
        "answerable": True,
        "generator": gen,
        "outcome": "answered",
        "response": {"segments": [{"narration": f"Answer to {qid} from {gen}"}]},
        "judgements": judgements,
        **extra,
    }


RUN_A = {"id": "20261008T010000Z", "name": "A", "status": "finished", "created_at": "2026-10-08T01:00:00Z"}
RUN_B = {"id": "20261008T020000Z", "name": "B", "status": "finished", "created_at": "2026-10-08T02:00:00Z"}
RUN_X = {"id": "20261008T030000Z", "name": "excluded", "status": "excluded", "excluded": True}

ROWS_A = [
    row("q1", "anthropic:sonnet", [j("openai:sol", "fail", grounded=2), j("anthropic:opus", "fail", grounded=1)]),
    row("q2", "anthropic:sonnet", [j("openai:sol", "pass"), j("anthropic:opus", "fail", grounded=2)]),
    row("q1", "openai:sol", [j("openai:sol", "pass"), j("anthropic:opus", "fail")]),
    row(
        "q2",
        "openai:sol",
        [j("openai:sol", "pass"), j("anthropic:opus", "pass"), {"judge": "openrouter:gemini", "error": "timeout"}],
    ),
]
ROWS_B = [row("q1", "anthropic:sonnet", [j("openai:sol", "fail"), j("anthropic:opus", "fail")])]
ROWS_X = [row("q3", "anthropic:sonnet", [j("openai:sol", "pass")])]
DATA = [(RUN_A, ROWS_A), (RUN_B, ROWS_B), (RUN_X, ROWS_X)]


def test_agreement_counts_the_majority_and_score_spread():
    a = eval_explore.agreement([j("x", "pass", grounded=5), j("y", "pass", grounded=2), j("z", "fail", grounded=4)])
    assert a["judges"] == 3 and a["verdict"] == pytest.approx(2 / 3, abs=1e-3) and a["majority"] == "pass"
    assert a["score_spread"]["grounded"] == 3 and a["unanimous"] is False
    assert eval_explore.agreement([j("x", "pass"), j("y", "fail")])["majority"] == "split"
    assert eval_explore.agreement([{"judge": "x", "error": "boom"}])["verdict"] is None


def test_questions_are_hardest_first_and_skip_excluded_runs():
    out = eval_explore.questions(DATA)
    qs = out["questions"]
    assert [q["qid"] for q in qs] == ["q1", "q2"]  # q1: 1 pass of 6; q2: 4 of 5; q3 only in an excluded run
    q1 = qs[0]
    assert q1["judgements"] == 6 and q1["fails"] == 5 and q1["runs"] == 2
    assert q1["by_generator"]["openai:sol"]["pass_rate"] == 0.5
    assert out["judges"] == ["anthropic:opus", "openai:sol"]  # errored judgements are not counted as a judge's vote
    assert {r["id"] for r in out["runs"]} == {RUN_A["id"], RUN_B["id"]}


def test_questions_filter_by_model_and_run():
    only = eval_explore.questions(DATA, generator="openai:sol")["questions"]
    assert {q["qid"] for q in only} == {"q1", "q2"} and all(set(q["by_generator"]) == {"openai:sol"} for q in only)
    one_run = eval_explore.questions(DATA, run_id=RUN_B["id"])["questions"]
    assert [q["qid"] for q in one_run] == ["q1"] and one_run[0]["judgements"] == 2


def test_question_detail_has_every_judge_and_agreement():
    d = eval_explore.question_detail(DATA, "q2")
    assert d["question"]["qid"] == "q2" and len(d["answers"]) == 2
    sol = next(a for a in d["answers"] if a["generator"] == "openai:sol")
    assert [x["judge"] for x in sol["judgements"]] == ["openai:sol", "anthropic:opus", "openrouter:gemini"]
    assert sol["judgements"][2]["error"] == "timeout" and sol["agreement"]["unanimous"] is True
    assert "Answer to q2" in sol["answer"]
    assert eval_explore.question_detail(DATA, "q3")["question"] is None  # excluded run


def test_compare_matrices():
    c = eval_explore.compare(RUN_A, ROWS_A)
    assert c["generators"] == ["anthropic:sonnet", "openai:sol"] and c["judges"] == ["anthropic:opus", "openai:sol"]
    assert c["matrix"]["anthropic:sonnet"]["openai:sol"] == {"n": 2, "pass_rate": 0.5}
    assert c["leniency"]["anthropic:opus"] == {"n": 4, "pass_rate": 0.25}
    pair = c["pairs"][0]
    assert pair["n"] == 4 and pair["verdict_agreement"] == 0.5 and pair["mean_score_gap"] is not None
    assert c["scores"]["anthropic:sonnet"]["grounded"]["n"] == 4


@pytest.fixture
def admin_client(admin, monkeypatch):
    monkeypatch.setattr(
        admin_evals,
        "_runs_rows",
        lambda bucket, run_id=None: [(r, rows) for r, rows in DATA if not run_id or r["id"] == run_id],
    )
    return admin


def test_routes_answer_for_admin(admin_client):
    r = admin_client.get("/api/admin/evals/explore")
    assert r.status_code == 200 and [q["qid"] for q in r.json()["questions"]] == ["q1", "q2"]
    r = admin_client.get("/api/admin/evals/explore/q1", params={"generator": "openai:sol"})
    assert r.status_code == 200 and {a["generator"] for a in r.json()["answers"]} == {"openai:sol"}
    assert admin_client.get("/api/admin/evals/explore/q9").status_code == 404
    r = admin_client.get("/api/admin/evals/compare", params={"run_id": "all"})
    assert r.status_code == 200 and r.json()["runs"] == 3
    assert (
        admin_client.get("/api/admin/evals/compare", params={"run_id": RUN_A["id"]}).json()["run"]["id"] == RUN_A["id"]
    )


def test_routes_reject_bad_filters(admin_client):
    assert admin_client.get("/api/admin/evals/explore", params={"generator": "<script>"}).status_code == 400
    assert admin_client.get("/api/admin/evals/compare", params={"run_id": "../../etc"}).status_code == 400


def test_routes_are_admin_only(student):
    for path in ("/api/admin/evals/explore", "/api/admin/evals/explore/q1", "/api/admin/evals/compare"):
        assert student.get(path).status_code in (401, 403)


def test_failed_judge_calls_are_skipped_and_counted():
    qs = {q["qid"]: q for q in eval_explore.questions(DATA)["questions"]}
    assert qs["q2"]["errors_skipped"] == 1 and qs["q2"]["judgements"] == 4  # the error is not a fail
    assert qs["q1"]["errors_skipped"] == 0
    c = eval_explore.compare(RUN_A, ROWS_A)
    assert c["errors_skipped"] == {"openrouter:gemini": 1}
    assert "openrouter:gemini" not in c["judges"]  # a judge with only errors gets no column of fake fails


def test_every_answer_kind_has_a_label_in_both_eval_views():
    """A new answer kind (like cross_course on Oct 8) must get a name in Settings > Evals, not a raw code."""
    import re
    from pathlib import Path

    from app import eval_core

    root = Path(__file__).resolve().parents[1]
    for name, table in (("admin-evals.js", "OUTCOMES"), ("admin-evals-explore.js", "OUTCOME_NAMES")):
        js = (root / "public" / name).read_text()
        block = js[js.index(f"const {table} = {{") :]
        block = block[: block.index("};")]
        keys = set(re.findall(r"\b([a-z_]+):", block))
        missing = set(eval_core.ROUTE_OF_KIND) - keys
        assert not missing, f"{name} has no label for {sorted(missing)}"
