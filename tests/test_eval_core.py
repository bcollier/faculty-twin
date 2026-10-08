"""The shared eval core (app/eval_core.py): parity with the CLI it was moved out of, and the report card math.

tests/fixtures/eval_parity.json was written by the CLI code as it stood
before the move (tests/fixtures/make_eval_parity.py). Invented text only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import eval_core
from evals import calibrate, dataset, report, rubric

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "eval_parity.json").read_text())


NEW_DIMS = eval_core.TEACHING_DIMENSIONS + eval_core.WEB_DIMENSIONS  # added Oct 8 (Block 8c)


def _without_new_keys(s: dict) -> dict:
    """The summary minus what was added after the move (per-dimension counts, score_n, the Oct 8 dimensions)."""
    out = json.loads(json.dumps(s))
    for j in out["judges"].values():
        j.pop("score_n", None)
        j.pop("provider_errors", None)
        for d in NEW_DIMS:
            j["scores"].pop(d, None)
    return out


def _core_scores(parsed: dict) -> dict:
    out = json.loads(json.dumps(parsed))
    for d in NEW_DIMS:
        assert out["scores"].pop(d) is None  # an old-style reply leaves the new dimensions null
    return out


def test_summary_and_reports_match_the_old_cli():
    rows = FIXTURE["results"]
    assert _without_new_keys(eval_core.summary(rows, {"target": "fixture"})) == FIXTURE["summary"]
    assert _without_new_keys(report.summary(rows, {"target": "fixture"})) == FIXTURE["summary"]
    assert report.full_markdown(rows) == FIXTURE["full_markdown"]


def test_summary_counts_how_many_judgements_each_mean_covers():
    s = eval_core.summary(FIXTURE["results"])
    for name, j in s["judges"].items():
        js = [x for r in FIXTURE["results"] for x in r["judgements"] if x["judge"] == name and "error" not in x]
        for d in eval_core.DIMENSIONS:
            assert j["score_n"][d] == sum(1 for x in js if x["scores"].get(d) is not None)


def test_summary_markdown_states_every_scale_and_has_a_legend():
    rows = FIXTURE["results"]
    md = report.summary_markdown(report.summary(rows, {"target": "fixture"}))
    assert "Pass rate (% judged pass)" in md and "Grounded (1\u20135, 5 best)" in md
    assert "Answers judged (n)" in md and "(n=" in md
    assert "64%" in md and "| 0.64 |" not in md  # rates as percentages
    for line in eval_core.legend_lines():
        assert line in md
    assert "1 = very poor, 3 = acceptable, 5 = excellent" in md
    assert "not computed from them" in md  # the verdict is separate from the scores
    for r in rows:  # still no question text or narration
        assert r["question"] not in md


def test_missing_dimensions_show_as_n_a():
    assert eval_core.fmt_score("grounded", None) == "n/a (no slides)"
    assert eval_core.fmt_score("matches_reference", None) == "n/a"
    assert eval_core.fmt_score("safety_tone", 4.456, 22) == "4.46 (n=22)"
    assert eval_core.fmt_pct(0.68) == "68%" and eval_core.fmt_pct(None) == "n/a"


def test_prompts_and_parser_match_the_old_cli():
    # The judge prompt changed on purpose on Oct 8 (teaching dimensions, Block 8c); the six core lines remain.
    assert eval_core.SYSTEM_PROMPT == rubric.SYSTEM_PROMPT
    for d in eval_core.CORE_DIMENSIONS:
        assert f"- {d}: {eval_core.DIMENSIONS[d]}" in eval_core.SYSTEM_PROMPT
    assert [eval_core.build_user_prompt(r) for r in FIXTURE["results"]] == FIXTURE["prompts"]
    assert [_core_scores(eval_core.parse(x)) for x in FIXTURE["parse_inputs"]] == FIXTURE["parse"]


def test_dataset_and_calibration_match_the_old_cli():
    qs = dataset.load(ROOT / "evals" / "questions.example.jsonl")
    assert [q.qid for q in eval_core.select_top(qs, 4)] == FIXTURE["select_top"]
    assert [calibrate.check(c, {"judge": "x", "verdict": "pass", "scores": {d: 3 for d in rubric.DIMENSIONS}})
            for c in calibrate.load_cases()] == FIXTURE["calibration_checks"]


def test_calibration_cases_bundled_in_app_match_evals():
    """Vercel never ships evals/, so app/ carries a copy. They must stay identical."""
    assert eval_core.CALIBRATION_FILE.read_bytes() == calibrate.CASES.read_bytes()
    assert eval_core.load_calibration_cases() == calibrate.load_cases()


def test_parse_lines_checks_privacy_without_echoing_text():
    good = json.dumps({"category": "CODE_HELP", "question": "Why does my loop never stop?",
                       "answerable_from_course_materials": True})
    bad = json.dumps({"category": "CODE_HELP", "question": "Ask jane.doe@example.edu about it"})
    assert [q.qid for q in eval_core.parse_lines(good + "\n\n" + good)] == ["q001", "q003"]
    with pytest.raises(eval_core.DatasetError) as err:
        eval_core.parse_lines(good + "\n" + bad)
    assert "line 2" in str(err.value) and "email" in str(err.value) and "jane" not in str(err.value)


def test_judge_prompt_comes_from_the_prompt_registry():
    """Settings > Prompts can edit the judge rubric; admin runs and the CLI both read the edit."""
    from app import prompts

    assert eval_core.system_prompt() == eval_core.SYSTEM_PROMPT == rubric.SYSTEM_PROMPT
    assert eval_core.judge_system_prompt is eval_core.system_prompt
    edited = prompts.raw("eval_judge") + "\nKeep it short."
    prompts.save("eval_judge", edited, "test")
    try:
        assert eval_core.system_prompt().endswith("Keep it short.") and "- grounded:" in eval_core.system_prompt()
        assert rubric.system_prompt() == eval_core.system_prompt()
        assert prompts.default("eval_judge", dimensions=eval_core.dimensions_text()) == eval_core.SYSTEM_PROMPT
    finally:
        prompts.save("eval_judge", "", "reset", reset=True)


# ---------------------------------------------------------------- report card math

def J(name, verdict, **scores):
    full = {d: None for d in eval_core.DIMENSIONS}
    full.update(scores)
    return {"judge": name, "verdict": verdict, "scores": full, "rationale": "", "issues": []}


def row(status, answerable, judgements, source="llm"):
    return {"category": "CONCEPT_QUESTION", "answerable": answerable,
            "response": {"status": status, "narration_source": source if status == "ok" else None, "latency_ms": 10},
            "judgements": judgements}


def test_generator_metrics_by_hand():
    rows = [
        row("ok", True, [J("a", "pass", grounded=5, correct_scope=5), J("b", "pass", grounded=3, correct_scope=4)]),
        row("ok", False, [J("a", "fail", grounded=4, correct_scope=1), J("b", "pass", grounded=4, correct_scope=2)],
            source="fallback"),
        row("not_covered", False, [J("a", "pass", correct_scope=5), {"judge": "b", "error": "timeout"}]),
        row("error", True, []),
    ]
    m = eval_core.generator_metrics(rows)
    # 5 valid judgements: pass, pass, fail, pass, pass
    assert m["judgements"] == 5 and m["judge_errors"] == 1
    assert m["score_n"]["grounded"] == 4 and m["score_n"]["speech_quality"] == 0
    assert m["pass_rate"] == 0.8
    assert m["scores"]["grounded"] == 4.0  # (5 + 3 + 4 + 4) / 4
    assert m["scores"]["correct_scope"] == round((5 + 4 + 1 + 2 + 5) / 5, 2)
    assert m["scores"]["speech_quality"] is None
    # Right call: row 1 (answered, answerable) and row 3 (declined, not answerable); row 2 wrong; row 4 excluded.
    assert m["decline_accuracy"] == round(2 / 3, 2)
    assert m["fallback_rate"] == 0.5
    # a and b both judged rows 1 and 2: same verdict on row 1 only.
    assert m["judge_agreement"] == 0.5
    assert (m["questions"], m["answered"], m["errors"]) == (4, 2, 1)


def test_generator_metrics_with_one_judge_has_no_agreement():
    m = eval_core.generator_metrics([row("ok", True, [J("a", "pass", grounded=5)])])
    assert m["judge_agreement"] is None and m["pass_rate"] == 1.0
