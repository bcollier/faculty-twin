"""Turn a run's results into numbers: per judge, per dimension, per category, and agreement.

Two outputs, on purpose:
- `summary(...)` has no question text, narration, or rationale: categories,
  counts, and scores only. It is the one file safe to paste into a PR, the
  prompt log, or the README.
- `full_markdown(...)` is the private report Ben reads question by question.
  It stays in `evals/private/`.
"""

from __future__ import annotations

from typing import Any

# The numbers live in app/eval_core.py so Settings > Evals computes them the same way.
from app.eval_core import _avg, _ok_judgements, probabilistic, summary  # noqa: F401  (re-exported)
from app.eval_core import (
    SCALE_NOTE,
    VERDICT_NOTE,
    dimension_header,
    fmt_pct,
    fmt_score,
    legend_lines,
)

from .rubric import DIMENSIONS


def summary_markdown(s: dict[str, Any]) -> str:
    """The shareable summary. Every header states its scale, and a legend follows every table."""
    lines = ["# Faculty Twin eval summary", ""]
    meta = s.get("meta") or {}
    if meta:
        lines.append(" · ".join(f"{k}: {v}" for k, v in meta.items()))
        lines.append("")
    lines.append(f"Questions: {s['questions']}. Outcomes (number of questions): "
                 + ", ".join(f"{k} {v}" for k, v in s["status_counts"].items()) + ".")
    lines.append(f"Right call on answer versus decline: {fmt_pct(s['scope_right_call_rate'])} of questions. "
                 f"Course questions the twin declined: {s['answerable_declined']}. "
                 f"Answers that fell back to speaker notes: {fmt_pct(s['narration_fallback_rate'])} of answers.")
    lines.append("")
    if s["judges"]:
        dims = list(DIMENSIONS)
        lines.append("| Judge | Answers judged (n) | Judge errors (n) | Pass rate (% judged pass) | "
                     + " | ".join(dimension_header(d) for d in dims) + " |")
        lines.append("| --- " * (4 + len(dims)) + "|")
        for name, j in s["judges"].items():
            score_n = j.get("score_n") or {}
            cells = [fmt_score(d, j["scores"][d], score_n.get(d)) for d in dims]
            lines.append(f"| {name} | {j['judged']} | {j['errors']} | {fmt_pct(j['pass_rate'])} | " + " | ".join(cells) + " |")
        lines.append("")
        lines += [f"- {line}" for line in legend_lines()]
        lines.append("")
    if s["judge_agreement"]:
        lines.append("| Judge pair | Same verdict (% of answers both judged) | Mean score gap (points on the 1–5 scale) |")
        lines.append("| --- | --- | --- |")
        for pair, a in s["judge_agreement"].items():
            gap = "n/a" if a["mean_abs_score_gap"] is None else f"{a['mean_abs_score_gap']:.2f}"
            lines.append(f"| {pair} | {fmt_pct(a['verdict_agreement'])} | {gap} |")
        lines.append("")
        lines.append("- Same verdict: how often both judges gave the same pass or fail on an answer they both judged.")
        lines.append("- Mean score gap: the average distance between their scores on the same dimension of the same answer "
                     "(0 = identical, 4 = opposite ends of the scale).")
        lines.append("")
    if s.get("probabilistic_judges"):
        lines.append("| Probabilistic judge | Items (n) | Mean P(pass) (0–1) | Mean confidence (0–1) | Brier vs LLM majority (0 best; n items) | Flag rates (% of items) |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for name, p in s["probabilistic_judges"].items():
            flags = ", ".join(f"{k} {fmt_pct(v)}" for k, v in p["flag_rates"].items()) or "n/a"
            lines.append(f"| {name} | {p['items']} | {p['mean_p_pass']} | {p['mean_verdict_confidence']} | "
                         f"{p['brier_vs_llm_majority']} ({p['compared_items']}) | {flags} |")
        lines.append("")
    lines.append("| Category | Questions (n) | Answered with slides (n) | Pass rate (% judged pass, all judges) | "
                 + dimension_header("correct_scope") + " |")
    lines.append("| --- | --- | --- | --- | --- |")
    for cat, c in s["by_category"].items():
        lines.append(f"| {cat} | {c['questions']} | {c['answered']} | {fmt_pct(c['pass_rate'])} | "
                     f"{fmt_score('correct_scope', c['correct_scope'])} |")
    lines.append("")
    lines.append("- Pass rate and Right scope pool every judge's judgements of the category's questions. "
                 + SCALE_NOTE + " " + VERDICT_NOTE)
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
