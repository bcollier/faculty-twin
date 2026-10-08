"""Give the Settings > Evals report card its history: import earlier command-line results into the bucket.

    uv run --no-project --with-requirements requirements.txt python -m scripts.import_eval_history

Runs on the local build machine (it reads `evals/private/runs/`, git-ignored).
What it writes to the private bucket, in the format `app/eval_store.py`
describes (safe to run again; it replaces its own entries):

- the October 7 twin run `20261007T222857Z` (22 questions, judged by
  claude-opus-5-5 and gpt-6.1-sol) as a finished run, labelled as predating
  PR #35 (access-code filter) and PR #37 (logistics routing). The run did not
  record its narration model; the local `.env` set anthropic / claude-sonnet-5-5,
  and the label says so.
- the errored first attempt `20261007T222746Z` (20 of 22 questions failed on
  Voyage's free-tier rate limit) as an excluded run: listed, never plotted.
- the October 5 generic-chatbot baseline from evals/README.md as a baseline
  entry (aggregates only; there are no per-question rows for it here).
- the October 5 judge calibration results from evals/README.md, for judges
  Settings has not calibrated itself.

It prints counts only, never question text.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app import eval_runs, eval_store
from evals.run import PRIVATE, ROOT, load_dotenv

VALID_RUN = "20261007T222857Z"
ERRORED_RUN = "20261007T222746Z"
TWIN_GENERATOR = {"provider": "anthropic", "model": "claude-sonnet-5-5"}
TWIN_LABEL = "Twin with claude-sonnet-5-5 (narration model from the local .env; the CLI run did not record it)"

BASELINE_ID = "baseline-20261005"
BASELINE_KEY = "baseline:openai:gpt-6.1-sol"
BASELINE_LABEL = "Generic chatbot, no course material (gpt-6.1-sol)"
# evals/README.md, "Baseline results, October 5, 2026": per-judge numbers over the same 22 questions.
BASELINE_PER_JUDGE = {
    "openai:gpt-6.1-sol": {"pass_rate": 0.68, "answers_question": 3.77, "correct_scope": 3.68,
                           "matches_reference": 2.00, "speech_quality": 3.67, "safety_tone": 4.45},
    "openai:gpt-6-luna": {"pass_rate": 0.27, "answers_question": 3.00, "correct_scope": 2.59,
                          "matches_reference": 1.08, "speech_quality": 3.11, "safety_tone": 3.14},
}
BASELINE_AGREEMENT = 0.59

# evals/README.md, "Calibration results, October 5, 2026".
README_CALIBRATION = {
    "openai:gpt-6.1-sol": {"met": 8, "cases": 8},
    "openai:gpt-6-luna": {"met": 8, "cases": 8},
}


def _stamp(run_id: str) -> datetime:
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)


def _judge_ref(name: str) -> dict[str, str]:
    provider, _, model = name.partition(":")
    return {"provider": provider, "model": model}


def convert_run(run_dir: Path, run_id: str, generator: dict[str, str], excluded: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A CLI run folder -> (run.json, result rows) in the Settings format."""
    results = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta = summary.get("meta") or {}
    judges = [j.strip() for j in str(meta.get("judges") or "").split(",") if j.strip() and j.strip() != "none"]
    key = eval_runs.model_key(generator)
    rows = []
    for pair, r in enumerate(results):
        resp = dict(r["response"])
        # At the time every decline came from the not-covered threshold (no FAQ, no logistics routing yet).
        resp["outcome"] = {"ok": "course_content", "not_covered": "not_covered"}.get(resp.get("status"))
        rows.append({
            "qid": r["qid"], "pair": pair, "category": r["category"], "course": r.get("course"),
            "answerable": r["answerable"], "question": r["question"], "reference_answer": r.get("reference_answer"),
            "generator": key, "outcome": resp["outcome"], "response": resp, "judgements": r.get("judgements", []),
            "calls": None, "at": None,
        })
    started = _stamp(run_id)
    minutes = float(meta.get("minutes") or 0)
    errors = sum(1 for r in results if r["response"]["status"] == "error")
    run = {
        "id": run_id,
        "name": "October 7 twin run (command line)" if not excluded else "October 7 first attempt (command line)",
        "kind": "imported",
        "source": f"evals/private/runs/{run_id}",
        "created_at": started.isoformat(timespec="seconds"),
        "updated_at": started.isoformat(timespec="seconds"),
        "finished_at": (started + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
        "status": "excluded" if excluded else "done",
        "status_note": None,
        "excluded": excluded,
        "generators": [generator],
        "generator_labels": {key: TWIN_LABEL},
        "judges": [_judge_ref(j) for j in judges],
        "top": len(results),
        "categories": None,
        "questions": [{k: r[k] for k in ("qid", "category", "course", "answerable", "question", "reference_answer")}
                      for r in results],
        "pairs_total": len(results),
        "pairs_done": len(results),
        "calls_used": None,
        "call_cap": None,
        "self_grading": eval_runs.self_grading([generator], [_judge_ref(j) for j in judges]),
        "notes": [eval_runs.IMPORTED_NOTE],
        "lease": None,
    }
    if excluded:
        run["status_note"] = (f"{errors} of {len(results)} questions errored (Voyage's free tier allows 3 embedding "
                              f"requests a minute). Excluded from the report card; the rerun is {VALID_RUN}.")
        run["notes"] = [run["status_note"]]
    run["summary"] = eval_runs.summarize(run, rows)
    return run, rows


def baseline_entry() -> dict[str, Any]:
    def mean2(field: str) -> float:
        return round(sum(j[field] for j in BASELINE_PER_JUDGE.values()) / len(BASELINE_PER_JUDGE), 2)

    dims = ("answers_question", "correct_scope", "matches_reference", "speech_quality", "safety_tone")
    metrics = {
        "questions": 22,
        "answered": 22,
        "errors": 0,
        "judgements": 44,
        "score_n": None,  # the README published means only, not how many answers each covers
        "pass_rate": mean2("pass_rate"),
        "scores": {"grounded": None, **{d: mean2(d) for d in dims}},
        "decline_accuracy": None,
        "answerable_declined": None,
        "fallback_rate": None,
        "judge_agreement": BASELINE_AGREEMENT,
        "median_latency_ms": None,
        "per_judge": {name: {"pass_rate": j["pass_rate"], "judged": 22} for name, j in BASELINE_PER_JUDGE.items()},
    }
    judges = [_judge_ref(j) for j in BASELINE_PER_JUDGE]
    return {
        "id": BASELINE_ID,
        "name": "October 5 baseline: generic chatbot (command line)",
        "kind": "baseline",
        "source": "evals/README.md, Baseline results, October 5, 2026",
        "created_at": "2026-10-05T12:00:00+00:00",
        "updated_at": "2026-10-05T12:00:00+00:00",
        "finished_at": "2026-10-05T12:00:00+00:00",
        "status": "done",
        "status_note": None,
        "excluded": False,
        "generators": [{"provider": "baseline", "model": "openai:gpt-6.1-sol"}],
        "generator_labels": {BASELINE_KEY: BASELINE_LABEL},
        "judges": judges,
        "questions": [],
        "question_count": 22,
        "pairs_total": 22,
        "pairs_done": 22,
        "self_grading": [{"generator": BASELINE_KEY, "judge": "openai:gpt-6.1-sol"}],
        "notes": [
            "The bar the twin has to clear: a generic chatbot with no course material answered the same 22 questions.",
            "Aggregates only, from evals/README.md: each number is the mean of the two judges' published means. "
            "No per-question rows. gpt-6.1-sol was grading its own answers.",
        ],
        "summary": {"by_generator": {BASELINE_KEY: metrics}},
    }


def run(private: Path, bucket: eval_store.Bucket, out=print) -> int:
    runs_dir = private / "runs"
    for run_id, excluded in ((VALID_RUN, False), (ERRORED_RUN, True)):
        folder = runs_dir / run_id
        if not (folder / "results.jsonl").is_file():
            out(f"{run_id}: not found under {runs_dir}, skipped.")
            continue
        run_json, rows = convert_run(folder, run_id, TWIN_GENERATOR, excluded)
        eval_store.write_run(bucket, run_json)
        eval_store.write_results(bucket, run_id, rows)
        eval_store.upsert_index(bucket, eval_runs.index_entry(run_json))
        m = run_json["summary"]["by_generator"].get(eval_runs.model_key(TWIN_GENERATOR), {})
        out(f"{run_id}: {len(rows)} rows, {'excluded' if excluded else 'done'}, pass rate {m.get('pass_rate')}, "
            f"right call {m.get('decline_accuracy')}, judge agreement {m.get('judge_agreement')}.")
    base = baseline_entry()
    eval_store.write_run(bucket, base)
    eval_store.upsert_index(bucket, eval_runs.index_entry(base))
    out(f"{BASELINE_ID}: baseline entry, pass rate {base['summary']['by_generator'][BASELINE_KEY]['pass_rate']}.")
    cal = eval_store.read_calibration(bucket)
    added = []
    for name, res in README_CALIBRATION.items():
        if name not in cal:
            cal[name] = {"judge": name, "met": res["met"], "cases": res["cases"], "done": True, "missed": [],
                         "rows": [], "started_at": "2026-10-05T12:00:00+00:00",
                         "finished_at": "2026-10-05T12:00:00+00:00",
                         "source": "evals/README.md (October 5, command line)"}
            added.append(name)
    eval_store.write_calibration(bucket, cal)
    out(f"Calibration: added {', '.join(added) or 'nothing (Settings results kept)'}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--private", type=Path, default=PRIVATE, help="the evals/private folder that holds runs/")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    args = p.parse_args(argv)
    load_dotenv(args.env_file)
    from app import config

    if not config.supabase_configured():
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (environment or .env).", file=sys.stderr)
        return 3
    try:
        return run(args.private, eval_store.SupabaseBucket())
    except eval_store.StoreError as exc:
        print(f"Bucket write failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
