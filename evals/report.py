"""Turn a run's results into numbers: per judge, per dimension, per category, and agreement.

Two outputs, on purpose:
- `summary(...)` has no question text, narration, or rationale: categories,
  counts, and scores only. It is the one file safe to paste into a PR, the
  prompt log, or the README.
- `full_markdown(...)` is the private report Ben reads question by question.
  It stays in `evals/private/`.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations
from statistics import mean
from typing import Any

from .rubric import DIMENSIONS


def _avg(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _ok_judgements(results: list[dict[str, Any]]):
    for r in results:
        for j in r.get("judgements", []):
            if "error" not in j:
                yield r, j


def summary(results: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    statuses: dict[str, int] = defaultdict(int)
    for r in results:
        statuses[r["response"]["status"]] += 1

    judges = sorted({j["judge"] for r in results for j in r.get("judgements", [])})
    per_judge: dict[str, Any] = {}
    for name in judges:
        js = [j for _, j in _ok_judgements(results) if j["judge"] == name]
        errors = sum(1 for r in results for j in r.get("judgements", []) if j["judge"] == name and "error" in j)
        per_judge[name] = {
            "judged": len(js),
            "errors": errors,
            "pass_rate": _avg([1.0 if j["verdict"] == "pass" else 0.0 for j in js]),
            "scores": {d: _avg([j["scores"][d] for j in js if j["scores"].get(d) is not None]) for d in DIMENSIONS},
        }

    by_cat: dict[str, dict[str, Any]] = {}
    cats = sorted({r["category"] for r in results})
    for cat in cats:
        rows = [r for r in results if r["category"] == cat]
        js = [j for r, j in _ok_judgements(rows)]
        by_cat[cat] = {
            "questions": len(rows),
            "answered": sum(1 for r in rows if r["response"]["status"] == "ok"),
            "pass_rate": _avg([1.0 if j["verdict"] == "pass" else 0.0 for j in js]),
            "correct_scope": _avg([j["scores"]["correct_scope"] for j in js if j["scores"].get("correct_scope")]),
        }

    # Scope behavior without any judge: did the twin answer what it should and decline the rest?
    expected = [r for r in results if r["response"]["status"] in ("ok", "not_covered")]
    right_call = [
        r for r in expected if (r["response"]["status"] == "ok") == bool(r["answerable"])
    ]
    answerable = [r for r in expected if r["answerable"]]
    declined_answerable = [r for r in answerable if r["response"]["status"] == "not_covered"]
    ok = [r for r in results if r["response"]["status"] == "ok"]
    fallback = [r for r in ok if r["response"].get("narration_source") == "fallback"]

    agreement: dict[str, Any] = {}
    for a, b in combinations(judges, 2):
        diffs, same = [], []
        for r in results:
            ja = next((j for j in r.get("judgements", []) if j["judge"] == a and "error" not in j), None)
            jb = next((j for j in r.get("judgements", []) if j["judge"] == b and "error" not in j), None)
            if not ja or not jb:
                continue
            same.append(1.0 if ja["verdict"] == jb["verdict"] else 0.0)
            for d in DIMENSIONS:
                if ja["scores"].get(d) is not None and jb["scores"].get(d) is not None:
                    diffs.append(abs(ja["scores"][d] - jb["scores"][d]))
        agreement[f"{a} vs {b}"] = {"verdict_agreement": _avg(same), "mean_abs_score_gap": _avg(diffs)}

    return {
        "meta": meta or {},
        "questions": len(results),
        "status_counts": dict(sorted(statuses.items())),
        "scope_right_call_rate": _avg([1.0] * len(right_call) + [0.0] * (len(expected) - len(right_call))),
        "answerable_declined": len(declined_answerable),
        "narration_fallback_rate": _avg([1.0] * len(fallback) + [0.0] * (len(ok) - len(fallback))),
        "median_latency_ms": sorted(r["response"]["latency_ms"] for r in results)[len(results) // 2] if results else None,
        "judges": per_judge,
        "judge_agreement": agreement,
        "by_category": by_cat,
    }


def summary_markdown(s: dict[str, Any]) -> str:
    lines = ["# Faculty Twin eval summary", ""]
    meta = s.get("meta") or {}
    if meta:
        lines.append(" · ".join(f"{k}: {v}" for k, v in meta.items()))
        lines.append("")
    lines.append(f"Questions: {s['questions']}. Outcomes: "
                 + ", ".join(f"{k} {v}" for k, v in s["status_counts"].items()) + ".")
    lines.append(f"Right call on answer versus decline: {s['scope_right_call_rate']}. "
                 f"Course questions the twin declined: {s['answerable_declined']}. "
                 f"Answers that fell back to speaker notes: {s['narration_fallback_rate']}.")
    lines.append("")
    if s["judges"]:
        dims = list(DIMENSIONS)
        lines.append("| Judge | Judged | Errors | Pass rate | " + " | ".join(dims) + " |")
        lines.append("| --- " * (4 + len(dims)) + "|")
        for name, j in s["judges"].items():
            cells = [str(j["scores"][d]) if j["scores"][d] is not None else "N/A" for d in dims]
            lines.append(f"| {name} | {j['judged']} | {j['errors']} | {j['pass_rate']} | " + " | ".join(cells) + " |")
        lines.append("")
    if s["judge_agreement"]:
        lines.append("| Judge pair | Same verdict | Mean score gap |")
        lines.append("| --- | --- | --- |")
        for pair, a in s["judge_agreement"].items():
            lines.append(f"| {pair} | {a['verdict_agreement']} | {a['mean_abs_score_gap']} |")
        lines.append("")
    lines.append("| Category | Questions | Answered | Pass rate | Scope score |")
    lines.append("| --- | --- | --- | --- | --- |")
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['questions']} | {c['answered']} | {c['pass_rate']} | {c['correct_scope']} |")
    return "\n".join(lines) + "\n"


def full_markdown(results: list[dict[str, Any]]) -> str:
    """Question-by-question report. PRIVATE: contains question text and narration."""
    lines = ["# Faculty Twin eval: full results (private, do not commit)", ""]
    for r in results:
        resp = r["response"]
        lines.append(f"## {r['qid']} · {r['category']} · expected: {'answer' if r['answerable'] else 'decline'}")
        lines.append("")
        lines.append(f"**Question.** {r['question']}")
        if r.get("reference_answer"):
            lines.append(f"**Reference.** {r['reference_answer']}")
        lines.append(f"**Twin.** {resp['status']}"
                     + (f" ({resp['message']})" if resp.get("message") else "")
                     + (f", narration {resp['narration_source']}" if resp.get("narration_source") else "")
                     + (f", top score {resp['top_score']:.3f}" if isinstance(resp.get("top_score"), float) else ""))
        for seg in resp.get("segments", []):
            lines.append(f"- `{seg['slide_id']}`: {seg['narration']}")
        for j in r.get("judgements", []):
            if "error" in j:
                lines.append(f"- *{j['judge']}*: error, {j['error']}")
                continue
            sc = ", ".join(f"{k} {v if v is not None else 'N/A'}" for k, v in j["scores"].items())
            lines.append(f"- *{j['judge']}*: **{j['verdict']}** ({sc}). {j['rationale']}")
            for issue in j.get("issues", []):
                lines.append(f"  - {issue}")
        lines.append("")
    return "\n".join(lines)
