"""The local scripts that feed Settings > Evals: question upload and history import. Fake bucket, invented data."""

from __future__ import annotations

import json
from pathlib import Path

from app import eval_runs, eval_store
from scripts import import_eval_history, upload_eval_questions

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "evals" / "questions.example.jsonl"
PARITY = json.loads((ROOT / "tests" / "fixtures" / "eval_parity.json").read_text())


def test_upload_checks_then_uploads_without_printing_text(tmp_path):
    bucket, lines = eval_store.MemoryBucket(), []
    assert upload_eval_questions.run(EXAMPLES, bucket, out=lines.append) == 0
    assert eval_store.read_questions_text(bucket) == EXAMPLES.read_text()
    printed = "\n".join(lines)
    for row in EXAMPLES.read_text().splitlines():
        assert json.loads(row)["question"] not in printed
    assert "6 questions passed" in printed


def test_upload_refuses_a_leaky_file(tmp_path):
    bad = tmp_path / "q.jsonl"
    bad.write_text(json.dumps({"category": "OTHER", "question": "Write to pat@example.edu"}) + "\n")
    bucket, lines = eval_store.MemoryBucket(), []
    assert upload_eval_questions.run(bad, bucket, out=lines.append) == 2
    assert eval_store.read_questions_text(bucket) is None and "pat@" not in "\n".join(lines)


def test_upload_keeps_questions_added_in_settings_unless_forced():
    bucket = eval_store.MemoryBucket()
    extra = json.dumps({"category": "OTHER", "question": "An invented question typed in Settings?"})
    eval_store.write_questions_text(bucket, EXAMPLES.read_text() + extra + "\n")
    lines = []
    assert upload_eval_questions.run(EXAMPLES, bucket, out=lines.append) == 4
    assert "1 question(s)" in "\n".join(lines)
    assert upload_eval_questions.run(EXAMPLES, bucket, force=True, out=lines.append) == 0
    assert eval_store.read_questions_text(bucket) == EXAMPLES.read_text()


def _fake_run(folder: Path, rows: list[dict], judges: str, minutes: float) -> None:
    folder.mkdir(parents=True)
    (folder / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / "summary.json").write_text(json.dumps({"meta": {"target": "in-process", "judges": judges, "minutes": minutes}}))


def test_import_history_writes_runs_baseline_and_calibration(tmp_path):
    rows = [r for r in PARITY["results"]]
    judges = "anthropic:judge-a, openai:judge-b, openrouter:vendor/judge-c"
    _fake_run(tmp_path / "runs" / import_eval_history.VALID_RUN, rows, judges, 7.7)
    _fake_run(tmp_path / "runs" / import_eval_history.ERRORED_RUN, rows[:3], judges, 0.7)
    bucket, lines = eval_store.MemoryBucket(), []
    assert import_eval_history.run(tmp_path, bucket, out=lines.append) == 0
    assert import_eval_history.run(tmp_path, bucket, out=lines.append) == 0  # safe to run again

    index = eval_store.read_index(bucket)
    assert [r["id"] for r in index] == [import_eval_history.VALID_RUN, import_eval_history.ERRORED_RUN,
                                        import_eval_history.BASELINE_ID]
    valid = eval_store.read_run(bucket, import_eval_history.VALID_RUN)
    assert valid["status"] == "done" and "PR #35" in valid["notes"][0] and "PR #37" in valid["notes"][0]
    assert valid["finished_at"] == "2026-10-07T22:36:39+00:00"
    results = eval_store.read_results(bucket, import_eval_history.VALID_RUN)
    assert len(results) == len(rows) and {r["generator"] for r in results} == {"anthropic:claude-sonnet-5-5"}
    # The imported numbers are the CLI's numbers for the same rows.
    m = valid["summary"]["by_generator"]["anthropic:claude-sonnet-5-5"]
    assert m["decline_accuracy"] == PARITY["summary"]["scope_right_call_rate"]
    assert m["fallback_rate"] == PARITY["summary"]["narration_fallback_rate"]
    assert index[1]["excluded"] is True

    card = eval_runs.report_card(index, eval_store.read_calibration(bucket))
    keys = [s["generator"] for s in card["series"]]
    assert keys == [import_eval_history.BASELINE_KEY, "anthropic:claude-sonnet-5-5"]
    twin = card["series"][1]
    assert [p["run_id"] for p in twin["points"]] == [import_eval_history.VALID_RUN]  # excluded run not plotted
    base = card["series"][0]["points"][0]
    assert base["pass_rate"] == round((0.68 + 0.27) / 2, 2) and base["judge_agreement"] == 0.59
    assert base["scores"]["grounded"] is None and base["self_grading"]
    assert {c["judge"] for c in card["calibration"]} == {"openai:gpt-6.1-sol", "openai:gpt-6-luna"}
    printed = "\n".join(lines)
    assert not any(r["question"] in printed for r in rows)


def test_import_run_uploads_any_cli_folder_with_jev_probabilities(tmp_path):
    rows = [dict(r) for r in PARITY["results"]]
    for r in rows:  # an invented Jev judgement with its probability on every row
        r["judgements"] = [{"judge": "jev:jev-latest", "verdict": "pass", "p_pass": 0.81,
                            "scores": {"grounded": 4.2}, "rationale": "P(pass) 0.81."}]
    folder = tmp_path / "20261008T183422Z"
    _fake_run(folder, rows, "jev:jev-latest", 12.0)
    bucket, lines = eval_store.MemoryBucket(), []
    gen = import_eval_history.parse_generator("anthropic:claude-sonnet-5-5")
    assert import_eval_history.import_run(folder, bucket, gen, ["Retrieval before PR #105."], out=lines.append) == 0
    run = eval_store.read_run(bucket, "20261008T183422Z")
    assert run["status"] == "done" and "jev:jev-latest" in run["name"]
    assert run["notes"][0] == import_eval_history.CLI_RUN_NOTE and run["notes"][1] == "Retrieval before PR #105."
    results = eval_store.read_results(bucket, "20261008T183422Z")
    assert len(results) == len(rows) and results[0]["judgements"][0]["p_pass"] == 0.81
    assert [r["id"] for r in eval_store.read_index(bucket)] == ["20261008T183422Z"]
    printed = "\n".join(lines)
    assert f"{len(rows)} judgements with P(pass)" in printed
    assert not any(r["question"] in printed for r in rows)


def test_import_run_needs_a_generator_and_a_safe_folder(tmp_path):
    import pytest

    with pytest.raises(ValueError):
        import_eval_history.parse_generator("claude-sonnet-5-5")
    with pytest.raises(eval_store.StoreError):
        import_eval_history.import_run(tmp_path / "not-a-run-id", eval_store.MemoryBucket(),
                                       {"provider": "anthropic", "model": "m"})
    assert import_eval_history.main(["--run", str(tmp_path / "20261008T183422Z")]) == 2
