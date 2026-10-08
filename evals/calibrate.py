"""Check the judges before trusting them: score invented answers whose right verdict is known.

`evals/calibration.jsonl` holds hand-written, clearly synthetic cases (no student
data). Each has an `expect` block: an optional verdict, plus `min` / `max`
bounds on dimension scores. A judge that misses these cannot be trusted to rate
the twin's real answers.

    uv run --no-project --with-requirements requirements.txt python -m evals.calibrate \\
        --judge openai:gpt-6.1-sol --judge openrouter:<model id>

Writes `evals/private/calibration/<UTC time>.json` and prints a table. Exit code
3 if any judge is not ready (missing key).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.eval_core import check  # noqa: F401  (shared with Settings > Evals)

from .judges import Judge, JudgeError, make_judge
from .run import PRIVATE, load_dotenv

CASES = Path(__file__).resolve().parent / "calibration.jsonl"


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def calibrate(cases: list[dict[str, Any]], judges: list[Judge]) -> dict[str, Any]:
    rows = []
    for case in cases:
        for judge in judges:
            j = judge.judge(case)
            rows.append({"cid": case["cid"], "judge": judge.name, "misses": check(case, j), "judgement": j})
    per_judge = {}
    for judge in judges:
        mine = [r for r in rows if r["judge"] == judge.name]
        per_judge[judge.name] = {
            "cases": len(mine),
            "met": sum(1 for r in mine if not r["misses"]),
            "missed": [r["cid"] for r in mine if r["misses"]],
        }
    return {"per_judge": per_judge, "rows": rows}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--judge", action="append", default=[], metavar="PROVIDER:MODEL", required=True)
    p.add_argument("--cases", type=Path, default=CASES)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    load_dotenv()
    try:
        judges = [make_judge(s) for s in args.judge]
    except JudgeError as exc:
        print(exc, file=sys.stderr)
        return 2
    missing = [f"{j.name}: {why}" for j in judges if (why := j.ready())]
    if missing:
        print("Judges not ready:\n  " + "\n  ".join(missing), file=sys.stderr)
        return 3
    result = calibrate(load_cases(args.cases), judges)
    for name, s in result["per_judge"].items():
        print(f"{name}: met {s['met']} of {s['cases']}" + (f"; missed {', '.join(s['missed'])}" if s["missed"] else ""))
    for r in result["rows"]:
        if r["misses"]:
            print(f"  {r['judge']} {r['cid']}: " + "; ".join(r["misses"]))
    out = args.out or PRIVATE / "calibration" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
