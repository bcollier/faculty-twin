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

With `--run <folder> --generator provider:model` it instead uploads any `evals.run` folder (for
example a run judged by Jev) as a finished run; add `--note` for anything the report card should say.

With `--compare <run folder>` it instead uploads one model comparison run from `evals/compare.py`
(docs/SPEC.md, Block 8c): run 1 of every question x answering model, as a finished run with up to six
answering models, so it appears on the Settings report card next to admin runs. Repeated answers
(test-retest) stay in the run folder's report; the run's notes carry the headline reliability numbers.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app import eval_runs, eval_store
from app.eval_core import read_jsonl
from app.eval_runs import model_ref
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
    return datetime.strptime(run_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def convert_run(run_dir: Path, run_id: str, generator: dict[str, str], excluded: bool,
                name: str | None = None, label: str | None = None,
                notes: list[str] | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A CLI run folder -> (run.json, result rows) in the Settings format.

    `name`, `label` and `notes` default to the October 7 run's; `import_run` passes its own.
    """
    results = read_jsonl(run_dir / "results.jsonl")
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    meta = summary.get("meta") or {}
    judges = [j.strip() for j in str(meta.get("judges") or "").split(",") if j.strip() and j.strip() != "none"]
    key = eval_runs.model_key(generator)
    rows = [_cli_row(r, pair, key) for pair, r in enumerate(results)]
    started = _stamp(run_id)
    minutes = float(meta.get("minutes") or 0)
    run = {
        "id": run_id,
        "name": name
        or ("October 7 twin run (command line)" if not excluded else "October 7 first attempt (command line)"),
        "kind": "imported",
        "source": f"evals/private/runs/{run_id}",
        "created_at": started.isoformat(timespec="seconds"),
        "updated_at": started.isoformat(timespec="seconds"),
        "finished_at": (started + timedelta(minutes=minutes)).isoformat(timespec="seconds"),
        "status": "excluded" if excluded else "done",
        "status_note": None,
        "excluded": excluded,
        "generators": [generator],
        "generator_labels": {key: label or TWIN_LABEL},
        "judges": [model_ref(j) for j in judges],
        "top": len(results),
        "categories": None,
        "questions": [{k: r[k] for k in ("qid", "category", "course", "answerable", "question", "reference_answer")}
                      for r in results],
        "pairs_total": len(results),
        "pairs_done": len(results),
        "calls_used": None,
        "call_cap": None,
        "self_grading": eval_runs.self_grading([generator], [model_ref(j) for j in judges]),
        "notes": list(notes) if notes is not None else [eval_runs.IMPORTED_NOTE],
        "lease": None,
    }
    if excluded:
        run["status_note"] = _errored_note(results)
        run["notes"] = [run["status_note"]]
    run["summary"] = eval_runs.summarize(run, rows)
    return run, rows


def _cli_row(r: dict[str, Any], pair: int, generator: str) -> dict[str, Any]:
    """One command-line result in the Settings row format, with the outcome the CLI did not record."""
    resp = dict(r["response"])
    # At the time every decline came from the not-covered threshold (no FAQ, no logistics routing yet).
    resp["outcome"] = {"ok": "course_content", "not_covered": "not_covered"}.get(resp.get("status"))
    return {
        "qid": r["qid"], "pair": pair, "category": r["category"], "course": r.get("course"),
        "answerable": r["answerable"], "question": r["question"], "reference_answer": r.get("reference_answer"),
        "generator": generator, "outcome": resp["outcome"], "response": resp, "judgements": r.get("judgements", []),
        "calls": None, "at": None,
    }


def _errored_note(results: list[dict[str, Any]]) -> str:
    """Why the October 7 first attempt is listed but never plotted."""
    errors = sum(1 for r in results if r["response"]["status"] == "error")
    return (f"{errors} of {len(results)} questions errored (Voyage's free tier allows 3 embedding "
            f"requests a minute). Excluded from the report card; the rerun is {VALID_RUN}.")


def baseline_entry() -> dict[str, Any]:
    """The October 5 generic-chatbot baseline as a report-card entry (aggregates only)."""
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
    judges = [model_ref(j) for j in BASELINE_PER_JUDGE]
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


def run(private: Path, bucket: eval_store.Bucket, out: Callable[[str], None] = print) -> int:
    """Import the October 7 runs, the October 5 baseline and the README calibration results."""
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
            eval_store.write_calibration_entry(bucket, name, {
                "judge": name, "attempt": None, "met": res["met"], "cases": res["cases"], "done": True, "missed": [],
                "rows": [], "started_at": "2026-10-05T12:00:00+00:00", "finished_at": "2026-10-05T12:00:00+00:00",
                "source": "evals/README.md (October 5, command line)"})
            added.append(name)
    out(f"Calibration: added {', '.join(added) or 'nothing (Settings results kept)'}.")
    return 0


QUESTION_FIELDS = ("qid", "category", "course", "answerable", "question", "reference_answer", "type",
                   "expected_kind", "expected_slides")
ROW_EXTRA_FIELDS = ("type", "expected_kind", "expected_slides", "must_include", "must_not")


def _read_json_or(path: Path, default: Any) -> Any:
    """Parsed JSON, or `default` when the file is missing or not JSON (optional run files)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _compare_row(r: dict[str, Any], qid: str, pair: int, generator: str) -> dict[str, Any]:
    """One comparison result in the Settings row format (judge token usage dropped)."""
    return {
        "qid": qid, "pair": pair, "category": r["category"], "course": r.get("course"),
        "answerable": r["answerable"], "question": r["question"], "reference_answer": r.get("reference_answer"),
        **{k: r[k] for k in ROW_EXTRA_FIELDS if r.get(k)},
        "web_path": r.get("web_path", True), "generator": generator, "outcome": r["response"].get("outcome"),
        "response": r["response"],
        "judgements": [{k: v for k, v in j.items() if k != "usage"} for j in r.get("judgements", [])],
        "calls": (r["response"].get("usage") or {}).get("calls"), "at": None,
    }


def _compare_notes(run_dir: Path, meta: dict[str, Any]) -> list[str]:
    """The run's notes: what was compared, the spend, and the test-retest ICC when the report has it."""
    notes = [f"Model comparison from the command line (evals/compare.py): "
             f"{meta.get('questions_file', 'question set')}, run 1 of each question x model. "
             f"Spend for the whole comparison: ${meta.get('spend_usd', 0):.2f}."]
    if meta.get("web_path") is False:
        notes.append("The \"beyond the slides\" web path was not in the app yet: "
                     "web questions were expected to be declined.")
    _missing = object()
    summary = _read_json_or(run_dir / "compare_summary.json", _missing)
    if summary is not _missing:
        models = (summary.get("generator_retest") or {}).get("models", {})
        icc = {k.split(":", 1)[-1]: v.get("icc") for k, v in models.items()}
        if any(v is not None for v in icc.values()):
            notes.append("Test-retest ICC (run 1 vs run 2): " + ", ".join(f"{k} {v}" for k, v in icc.items()) + ".")
    return notes


def convert_compare(run_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A comparison run folder (evals/compare.py) -> (run.json, rows) in the Settings format. Run 1 only."""
    rows_in = read_jsonl(run_dir / "results.jsonl")
    meta = _read_json_or(run_dir / "meta.json", {})
    run_id = eval_store.check_run_id(run_dir.name)
    rep1 = [r for r in rows_in if r.get("rep", 1) == 1]
    gens_keys = meta.get("generators") or sorted({r["generator"] for r in rep1})
    generators = [model_ref(g) for g in gens_keys]
    judge_keys = meta.get("judges") or sorted({j["judge"] for r in rep1 for j in r["judgements"]})
    judges = [model_ref(j) for j in judge_keys]
    qids = sorted({r["qid"] for r in rep1})
    questions, rows = _compare_questions_and_rows(rep1, qids, gens_keys)
    finished = meta.get("finished_at") or _stamp(run_id.split("-")[0]).isoformat(timespec="seconds")
    notes = _compare_notes(run_dir, meta)
    run = {
        "id": run_id,
        "name": meta.get("label") or f"Model comparison {run_id}",
        "kind": "imported",
        "source": f"evals/private/runs/{run_id}",
        "created_at": _stamp(run_id.split("-")[0]).isoformat(timespec="seconds"),
        "updated_at": finished,
        "finished_at": finished,
        "status": "done",
        "status_note": None,
        "excluded": False,
        "generators": generators,
        "generator_labels": {},
        "judges": judges,
        "top": len(qids),
        "categories": None,
        "questions": questions,
        "pairs_total": len(rows),
        "pairs_done": len(rows),
        "calls_used": None,
        "call_cap": None,
        "self_grading": eval_runs.self_grading(generators, judges),
        "notes": notes,
        "lease": None,
    }
    if meta.get("only_types"):
        _exclude_subset(run, meta)
    run["summary"] = eval_runs.summarize(run, rows)
    return run, rows


def _compare_questions_and_rows(rep1: list[dict[str, Any]], qids: list[str],
                                gens_keys: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(questions, rows) for run 1: one question entry per qid, one row per question x answering model.

    A row's `pair` is its slot in the full question x model grid, so a missing answer leaves a gap.
    """
    by = {(r["qid"], r["generator"]): r for r in rep1}
    questions, rows = [], []
    for qi, qid in enumerate(qids):
        first = next(r for r in rep1 if r["qid"] == qid)
        questions.append({k: first.get(k) for k in QUESTION_FIELDS if first.get(k) is not None})
        for gi, g in enumerate(gens_keys):
            r = by.get((qid, g))
            if r is not None:
                rows.append(_compare_row(r, qid, qi * len(gens_keys) + gi, g))
    return questions, rows


def _exclude_subset(run: dict[str, Any], meta: dict[str, Any]) -> None:
    """Mark a run over only some question types as excluded, with a note saying why."""
    # A subset (for example only the web questions) is not comparable with full runs: listed, never plotted.
    run["status"] = "excluded"
    run["excluded"] = True
    run["status_note"] = (f"Only the {', '.join(meta['only_types'])} questions of {meta.get('questions_file')}: "
                          "listed for its results, left out of the report card's lines.")
    run["notes"].insert(0, run["status_note"])


def import_compare(run_dir: Path, bucket: eval_store.Bucket, out: Callable[[str], None] = print) -> int:
    """Upload one model comparison run (run 1 of each question x model) as a finished run."""
    run_json, rows = convert_compare(run_dir)
    eval_store.write_run(bucket, run_json)
    for row in rows:
        eval_store.write_row(bucket, run_json["id"], row)
    eval_store.write_results(bucket, run_json["id"], rows)
    eval_store.mark_finished(bucket, run_json["id"], run_json["status"], run_json["finished_at"])
    eval_store.upsert_index(bucket, eval_runs.index_entry(run_json))
    out(f"{run_json['id']}: {len(rows)} rows, {len(run_json['generators'])} answering models, "
        f"{len(run_json['judges'])} judges.")
    return 0


CLI_RUN_NOTE = ("Imported from a command-line run (python -m evals.run). The command line records every decline as "
                "not covered: FAQ, logistics and not-covered replies are not told apart.")


def parse_generator(spec: str) -> dict[str, str]:
    """"provider:model" -> {"provider", "model"}; the narration model the run used (the CLI does not record it)."""
    provider, sep, model = (spec or "").partition(":")
    if not sep or not provider or not model:
        raise ValueError("--generator must look like provider:model, for example anthropic:claude-sonnet-5-5")
    return {"provider": provider, "model": model}


def import_run(run_dir: Path, bucket: eval_store.Bucket, generator: dict[str, str], notes: list[str] | None = None,
               out: Callable[[str], None] = print) -> int:
    """Upload any `evals.run` folder (results.jsonl + summary.json) as a finished run on the report card.

    Use it for runs the October 7 import does not cover, such as a Jev-judged run. The run id is the
    folder name. The generator is given by the caller: in-process runs do not record the narration model.
    """
    run_id = eval_store.check_run_id(run_dir.name)
    if not (run_dir / "results.jsonl").is_file() or not (run_dir / "summary.json").is_file():
        out(f"{run_id}: no results.jsonl and summary.json here, skipped.")
        return 2
    key = eval_runs.model_key(generator)
    meta = json.loads((run_dir / "summary.json").read_text(encoding="utf-8")).get("meta") or {}
    judges = str(meta.get("judges") or "none")
    run_json, rows = convert_run(
        run_dir, run_id, generator, excluded=False,
        name=f"Command-line run {run_id} (judges: {judges})",
        label=f"Twin with {key} (narration model given at import)",
        notes=[CLI_RUN_NOTE, *(notes or [])],
    )
    eval_store.write_run(bucket, run_json)
    for row in rows:
        eval_store.write_row(bucket, run_id, row)
    eval_store.write_results(bucket, run_id, rows)
    eval_store.mark_finished(bucket, run_id, run_json["status"], run_json["finished_at"])
    eval_store.upsert_index(bucket, eval_runs.index_entry(run_json))
    with_p = sum(1 for r in rows for j in r["judgements"] if isinstance(j, dict) and "p_pass" in j)
    out(f"{run_id}: {len(rows)} rows, judges {judges}, {with_p} judgements with P(pass).")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Command line: upload the October runs, or the --run / --compare folders, to the private bucket.

    Needs the Supabase settings; prints counts only, never question text.
    """
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--private", type=Path, default=PRIVATE, help="the evals/private folder that holds runs/")
    p.add_argument("--env-file", type=Path, default=ROOT / ".env")
    p.add_argument("--compare", type=Path, action="append", default=[],
                   help="a model comparison run folder (evals/compare.py) to upload instead")
    p.add_argument("--run", type=Path, action="append", default=[],
                   help="an evals.run folder to upload instead (needs --generator)")
    p.add_argument("--generator", help="provider:model the --run folders' twin narrated with")
    p.add_argument("--note", action="append", default=[], help="a note shown with the --run (repeatable)")
    args = p.parse_args(argv)
    if args.run and not args.generator:
        print("--run needs --generator provider:model (the CLI does not record the narration model).",
              file=sys.stderr)
        return 2
    load_dotenv(args.env_file)
    from app import config

    if not config.supabase_configured():
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (environment or .env).", file=sys.stderr)
        return 3
    try:
        if args.run:
            bucket = eval_store.SupabaseBucket()
            gen = parse_generator(args.generator)
            return max(import_run(folder, bucket, gen, args.note) for folder in args.run)
        if args.compare:
            bucket = eval_store.SupabaseBucket()
            return max(import_compare(folder, bucket) for folder in args.compare)
        return run(args.private, eval_store.SupabaseBucket())
    except eval_store.StoreError as exc:
        print(f"Bucket write failed: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
