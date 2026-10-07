"""The Jev judge, with a TEST FAKE System One model: no key, no network.

Skipped unless deepeval is installed (it is an evals-only dependency):

    uv run --no-project --with-requirements requirements.txt --with-requirements evals/requirements.txt \\
        --with pytest python -m pytest -q tests/test_jev_judge.py
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("deepeval")

from deepeval.models.base_model import DeepEvalBaseSystemOneModel  # noqa: E402
from deepeval.models.system_one.schema import (  # noqa: E402
    NoulAnswer,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneAnswers,
)

from evals import calibrate, rubric  # noqa: E402
from evals.jev_judge import FLAGS, JevJudge, build_test_case_fields, to_rubric  # noqa: E402
from evals.judges import make_judge  # noqa: E402


class FakeJev(DeepEvalBaseSystemOneModel):
    """TEST FAKE: scores high unless the answer looks bad; flags by keyword. Records each state it saw."""

    def __init__(self):
        self.states = []
        super().__init__("fake-jev")

    def load_model(self, *args, **kwargs):
        return None

    def get_model_name(self, *args, **kwargs):
        return "fake-jev"

    def _answers(self, state, questions):
        self.states.append(state)
        text = json.dumps(state).lower()
        bad = any(w in text for w in ("sure, take two extra days", "exam is cancelled", "priya", "## ", "stanford study"))
        declined = "(the twin declined" in text
        answers = SystemOneAnswers()
        for key, q in questions.items():
            if isinstance(q, ScoreQuestion):
                top = len(q.levels) - 1
                level = 0 if bad else top
                answers.scores[key] = ScoreAnswer(score=level, probabilities={i: float(i == level) for i in range(top + 1)},
                                                  confidence=0.9)
            elif isinstance(q, NoulQuestion):
                s = str(q.instructions).lower()
                if s.startswith("a student would be well served"):
                    p = 0.1 if bad else 0.9
                elif "promises" in s:
                    p = 0.95 if "extra days" in text else 0.05
                elif "names or describes a student" in s:
                    p = 0.9 if "priya" in text else 0.05
                elif "follows an instruction" in s:
                    p = 0.9 if "exam is cancelled" in text else 0.05
                elif "markdown" in s:
                    p = 0.9 if "## " in text else 0.05
                else:
                    p = 0.5
                answers.nouls[key] = NoulAnswer(probability=p)
        assert declined or answers.nouls  # every request carries at least the verdict
        return answers, 0.0001

    def decide(self, state, questions):
        return self._answers(state, questions)

    async def a_decide(self, state, questions):
        return self._answers(state, questions)


def cases():
    return {c["cid"]: c for c in calibrate.load_cases()}


def test_spec_and_readiness(monkeypatch):
    assert make_judge("jev").name == "jev:jev-latest"
    assert make_judge("jev:jev-2").name == "jev:jev-2"
    assert make_judge("openai:gpt-6.1-sol").name == "openai:gpt-6.1-sol"
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert "TYPESAFE_API_KEY" in JevJudge().ready()
    assert JevJudge(system_one_model=FakeJev()).ready() is None


def test_fields_include_reference_and_evidence_only_when_present():
    c = cases()
    good = build_test_case_fields(c["c01-grounded-good"])
    assert good["expected_output"] and good["retrieval_context"] and "Segment 1" in good["actual_output"]
    decline = build_test_case_fields(c["c04-correct-decline"])
    assert "retrieval_context" not in decline and "expected_output" not in decline
    assert decline["actual_output"].startswith("(The twin declined")


def test_good_answer_scores_high_with_probabilities():
    out = JevJudge(system_one_model=FakeJev()).judge(cases()["c01-grounded-good"])
    assert out["verdict"] == "pass" and out["p_pass"] == 0.9
    assert out["scores"]["grounded"] == 5.0 and out["scores"]["matches_reference"] == 5.0
    assert set(out["scores"]) == set(rubric.DIMENSIONS)
    assert out["issues"] == [] and set(out["flags"]) == set(FLAGS)
    assert out["confidence"]["verdict"] == pytest.approx(0.8)  # |2p - 1| for a Noul


def test_bad_answers_fail_and_raise_the_right_flag():
    c = cases()
    judge = JevJudge(system_one_model=FakeJev())
    promise = judge.judge(c["c03-promised-extension"])
    assert promise["verdict"] == "fail" and promise["scores"]["safety_tone"] == 1.0
    assert promise["flags"]["promises_on_behalf"] >= 0.5 and FLAGS["promises_on_behalf"] in promise["issues"]
    assert judge.judge(c["c06-injection-echo"])["flags"]["obeys_injected_instruction"] >= 0.5
    assert judge.judge(c["c07-student-named"])["flags"]["names_a_student"] >= 0.5
    assert judge.judge(c["c08-markdown-speech"])["flags"]["markdown_not_speech"] >= 0.5


def test_decline_asks_no_grounding_speech_or_flag_questions():
    fake = FakeJev()
    out = JevJudge(system_one_model=fake).judge(cases()["c04-correct-decline"])
    assert out["scores"]["grounded"] is None and out["scores"]["speech_quality"] is None
    assert out["flags"] == {} and out["verdict"] == "pass"


def test_calibration_runs_with_the_jev_judge():
    result = calibrate.calibrate(calibrate.load_cases(), [JevJudge(system_one_model=FakeJev())])
    stats = result["per_judge"]["jev:jev-latest"]
    assert stats["cases"] == 8 and stats["met"] >= 6  # the keyword fake is not meant to be perfect


def test_failure_is_reported_not_raised():
    class Broken(FakeJev):
        def decide(self, state, questions):
            raise RuntimeError("network down")

    out = JevJudge(system_one_model=Broken()).judge(cases()["c01-grounded-good"])
    assert out["judge"] == "jev:jev-latest" and "network down" in out["error"]


def test_to_rubric():
    assert to_rubric(0.0) == 1.0 and to_rubric(1.0) == 5.0 and to_rubric(0.5) == 3.0 and to_rubric(None) is None


def test_summary_reports_jev_probabilities_against_llm_majority(tmp_path):
    from evals import report
    from evals.run import write_run

    c = cases()
    items = []
    for cid in ("c01-grounded-good", "c03-promised-extension"):
        item = dict(c[cid], qid=cid, course="x")
        llm_verdict = "pass" if cid.startswith("c01") else "fail"
        item["judgements"] = [
            {"judge": "openai:a", "scores": {d: 3 for d in rubric.DIMENSIONS}, "verdict": llm_verdict, "rationale": "", "issues": []},
            JevJudge(system_one_model=FakeJev()).judge(item),
        ]
        items.append(item)
    s = report.summary(items)
    p = s["probabilistic_judges"]["jev:jev-latest"]
    assert p["items"] == 2 and p["compared_items"] == 2
    assert p["brier_vs_llm_majority"] == pytest.approx(0.01)  # (0.9-1)^2 and (0.1-0)^2
    assert p["flag_rates"]["promises_on_behalf"] == 0.5  # flagged on the promise case only
    out = write_run(items, {}, tmp_path / "run")
    assert "Brier vs LLM majority" in (out / "summary.md").read_text()
