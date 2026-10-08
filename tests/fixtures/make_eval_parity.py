"""Regenerate tests/fixtures/eval_parity.json: invented eval results (no student data) and what the CLI computes from them.

The fixture was first written by the CLI code as it stood before the shared
eval core moved into app/eval_core.py (October 7, 2026), so
tests/test_eval_core.py checks the move changed nothing. (summary.md's wording
changed on purpose afterwards, to state every scale, so it is tested on its
own.) Only rerun this on purpose, when the expected output is meant to change:

    uv run --no-project --with-requirements requirements.txt python tests/fixtures/make_eval_parity.py > tests/fixtures/eval_parity.json
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals import calibrate, dataset, report, rubric  # noqa: E402

PARSE_INPUTS = [
    '```json\n{"scores": {"grounded": 4.6, "answers_question": 5, "correct_scope": 5, "matches_reference": null, '
    '"speech_quality": 3, "safety_tone": 5}, "verdict": "PASS", "rationale": "ok", "issues": "one"}\n```',
    'Here: {"scores": {"answers_question": 2, "correct_scope": 1, "safety_tone": 4}, "verdict": "fail", '
    '"rationale": "' + "x" * 700 + '", "issues": []} trailing',
]


def invented_results() -> list[dict]:
    random.seed(7)
    cats = ["CONCEPT_QUESTION", "MEETING_REQUEST", "EXTENSION_REQUEST", "CODE_HELP", "MISSED_CLASS"]
    judges = ["anthropic:judge-a", "openai:judge-b", "openrouter:vendor/judge-c"]
    rows = []
    for i in range(14):
        cat = cats[i % len(cats)]
        answerable = cat in ("CONCEPT_QUESTION", "CODE_HELP")
        status = random.choice(["ok", "ok", "not_covered", "not_covered", "error"]) if i else "ok"
        resp = {"status": status, "message": None, "segments": [], "follow_ups": [], "narration_source": None,
                "top_score": round(random.uniform(0.3, 0.7), 3), "latency_ms": random.randint(500, 9000)}
        if status == "ok":
            resp["narration_source"] = random.choice(["llm", "llm", "fallback"])
            resp["segments"] = [{"n": 1, "slide_id": f"70445-s01-00{i % 9 + 1}", "narration": f"Invented narration {i}.",
                                 "evidence": {"title": f"Invented slide {i}", "slide_text": "Invented text."}}]
            resp["follow_ups"] = ["An invented follow-up?"]
        js = []
        if status in ("ok", "not_covered"):
            for name in judges:
                if random.random() < 0.08:
                    js.append({"judge": name, "error": "invented timeout"})
                    continue
                sc = {d: (None if random.random() < 0.15 else random.randint(1, 5)) for d in rubric.DIMENSIONS}
                sc["correct_scope"] = random.randint(1, 5)
                js.append({"judge": name, "scores": sc, "verdict": random.choice(["pass", "fail"]),
                           "rationale": f"Invented rationale {i}.", "issues": []})
        rows.append({"qid": f"q{i + 1:03d}", "category": cat, "course": "Example", "answerable": answerable,
                     "question": f"Invented question number {i}?",
                     "reference_answer": None if i % 3 else "Invented reference.",
                     "response": resp, "judgements": js})
    return rows


def main() -> None:
    rows = invented_results()
    s = report.summary(rows, {"target": "fixture"})
    out = {
        "results": rows,
        "summary": s,
        "full_markdown": report.full_markdown(rows),
        "prompts": [rubric.build_user_prompt(r) for r in rows],
        "system_prompt": rubric.SYSTEM_PROMPT,
        "parse_inputs": PARSE_INPUTS,
        "parse": [rubric.parse(x) for x in PARSE_INPUTS],
        "select_top": [q.qid for q in dataset.select_top(dataset.load(ROOT / "evals" / "questions.example.jsonl"), 4)],
        "calibration_checks": [
            calibrate.check(c, {"judge": "x", "verdict": "pass", "scores": {d: 3 for d in rubric.DIMENSIONS}})
            for c in calibrate.load_cases()
        ],
    }
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
