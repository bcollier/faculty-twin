"""Run an eval: pick the top questions, ask the twin, have several LLMs judge each answer.

    # On the Mac mini (index and keys there), in-process:
    uv run --no-project --with-requirements requirements.txt python -m evals.run \\
        --questions evals/private/questions.jsonl --top 25 \\
        --judge anthropic:claude-opus-5-5 --judge openai:gpt-6.1-sol

    # Against a running site:
    FT_EVAL_PASSCODE=... python -m evals.run --target http --base-url https://<site> ...

    # Baseline: a generic chatbot with no course material answers the same questions:
    python -m evals.run --target baseline --baseline-model openai:gpt-6.1-sol --judge ...

    # Dry run with invented questions and no keys (checks wiring only):
    python -m evals.run --questions evals/questions.example.jsonl --target none

Writes `evals/private/runs/<UTC time>/`: `results.jsonl` and `report.md`
(private: question text and narration) and `summary.json` and `summary.md`
(aggregates only, safe to share). Nothing here writes outside `evals/private/`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import dataset, report
from .judges import Judge, JudgeError
from .targets import BaselineTarget, HttpTarget, InProcessTarget, _result

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "evals" / "private"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from the git-ignored .env without overriding the environment."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.removeprefix("export ").strip()
        os.environ.setdefault(key, value.strip().strip("'\""))


class NoTarget:
    """Skips the twin entirely: every question comes back retrieval_not_ready. For wiring checks."""

    name = "none"

    def ask(self, question: str) -> dict[str, Any]:
        return _result("retrieval_not_ready", "no target (dry run)")


def evaluate(questions: list[dataset.Question], target, judges: list[Judge],
             log=lambda msg: None) -> list[dict[str, Any]]:
    results = []
    for i, q in enumerate(questions, start=1):
        response = target.ask(q.question)
        item = {
            "qid": q.qid,
            "category": q.category,
            "course": q.course,
            "answerable": q.answerable,
            "question": q.question,
            "reference_answer": q.reference_answer,
            "response": response,
            "judgements": [],
        }
        if response["status"] in ("ok", "not_covered"):
            item["judgements"] = [j.judge(item) for j in judges]
        log(f"[{i}/{len(questions)}] {q.qid} {q.category}: {response['status']}"
            + "".join(f" | {j['judge']} {j.get('verdict', 'error')}" for j in item["judgements"]))
        results.append(item)
    return results


def write_run(results: list[dict[str, Any]], meta: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "report.md").write_text(report.full_markdown(results), encoding="utf-8")
    s = report.summary(results, meta)
    (out_dir / "summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    (out_dir / "summary.md").write_text(report.summary_markdown(s), encoding="utf-8")
    return out_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--questions", default=str(PRIVATE / "questions.jsonl"))
    p.add_argument("--top", type=int, default=25)
    p.add_argument("--target", choices=("in-process", "http", "baseline", "none"), default="in-process")
    p.add_argument("--baseline-model", default="openai:gpt-6.1-sol", metavar="PROVIDER:MODEL",
                   help="model for --target baseline (a generic chatbot with no course material)")
    p.add_argument("--base-url", help="site for --target http")
    p.add_argument("--judge", action="append", default=[], metavar="PROVIDER:MODEL")
    p.add_argument("--out", help="run folder (default evals/private/runs/<UTC time>)")
    args = p.parse_args(argv)

    load_dotenv()
    try:
        questions = dataset.load(args.questions)
    except (OSError, dataset.DatasetError) as exc:
        print(f"Cannot use {args.questions}: {exc}", file=sys.stderr)
        return 2
    chosen = dataset.select_top(questions, args.top)
    print(f"{len(questions)} questions loaded; evaluating {len(chosen)}.")
    for cat, n in dataset.category_counts(questions):
        print(f"  {cat}: {n}")

    try:
        judges = [Judge.parse_spec(s) for s in args.judge]
    except JudgeError as exc:
        print(exc, file=sys.stderr)
        return 2
    missing = [f"{j.name}: {why}" for j in judges if (why := j.ready())]
    if missing:
        print("Judges not ready:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 3

    if args.target == "http":
        if not args.base_url:
            print("--target http needs --base-url", file=sys.stderr)
            return 2
        target = HttpTarget(args.base_url)
    elif args.target == "baseline":
        target = BaselineTarget(args.baseline_model)
        if why := target.ready():
            print(f"Baseline model not ready: {why}", file=sys.stderr)
            return 3
    elif args.target == "none":
        target = NoTarget()
    else:
        target = InProcessTarget()

    out = Path(args.out) if args.out else PRIVATE / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    started = time.monotonic()
    results = evaluate(chosen, target, judges, log=print)
    meta = {
        "target": target.name,
        "judges": ", ".join(j.name for j in judges) or "none",
        "questions_file": Path(args.questions).name,
        "minutes": round((time.monotonic() - started) / 60, 1),
    }
    write_run(results, meta, out)
    not_ready = sum(1 for r in results if r["response"]["status"] == "retrieval_not_ready")
    if not_ready == len(results) and results:
        print("Every question came back 'retrieval not implemented yet': the twin cannot answer until "
              "app/retrieval.py is written. Judges did not run.")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
