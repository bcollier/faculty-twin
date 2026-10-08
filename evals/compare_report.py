"""Numbers and reports for a model comparison run (`evals/compare.py`; docs/SPEC.md, Block 8c).

`analyze(rows, meta)` turns result rows into one dict of numbers (no question text unless asked for).
`write_all` writes, into the run folder:

- `compare.md`: every table, then question-by-question detail (private: it has question text)
- `compare_summary.md`, `compare_summary.json`: the same tables and numbers with no question text
- `report.html`: the visual report (`evals/report_html.py`), with question text only for a committable set

and, for the committable course set, copies the shareable files to `evals/reports/<run>/`.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from statistics import median
from typing import Any

from app import eval_core
from app.eval_core import DIMENSION_GROUPS, DIMENSION_LABELS, fmt_num, fmt_pct
from app.eval_runs import _base_model

from . import reliability as rel

SCORED = DIMENSION_GROUPS["core"] + DIMENSION_GROUPS["teaching"]
TYPE_LABELS = {"concept": "Concept", "beyond": "Beyond the slides", "off_topic": "Off-topic", "logistics": "Logistics",
               None: "Real email (no type)"}


# ---------------------------------------------------------------- helpers

def ok_judgements(row: dict[str, Any], field: str = "judgements") -> list[dict[str, Any]]:
    """The judgements of one answer that did not error."""
    return [j for j in row.get(field) or [] if "error" not in j]


def answer_composite(row: dict[str, Any], judges: list[str] | None = None,
                     dims: tuple[str, ...] = SCORED) -> float | None:
    """One number per answer: the mean over judges of each judge's mean over the scored dimensions it gave."""
    per_judge = []
    for j in ok_judgements(row):
        if judges is not None and j["judge"] not in judges:
            continue
        vals = [j["scores"].get(d) for d in dims if j["scores"].get(d) is not None]
        if vals:
            per_judge.append(sum(vals) / len(vals))
    return sum(per_judge) / len(per_judge) if per_judge else None


def answer_dim(row: dict[str, Any], dim: str, exclude_family: bool = False) -> float | None:
    """One answer's mean score on one dimension over the judges (optionally without same-family judges)."""
    fam = eval_core.model_family(row.get("generator"))
    vals = [j["scores"].get(dim) for j in ok_judgements(row)
            if j["scores"].get(dim) is not None and not (exclude_family and eval_core.model_family(j["judge"]) == fam)]
    return sum(vals) / len(vals) if vals else None


def answer_pass(row: dict[str, Any], exclude_family: bool = False) -> float | None:
    """One answer's share of pass verdicts (optionally without same-family judges)."""
    fam = eval_core.model_family(row.get("generator"))
    vals = [1.0 if j["verdict"] == "pass" else 0.0 for j in ok_judgements(row)
            if not (exclude_family and eval_core.model_family(j["judge"]) == fam)]
    return sum(vals) / len(vals) if vals else None


def self_grading_pairs(generators: list[str], judges: list[str]) -> list[dict[str, str]]:
    """Judge and answering-model pairs that are the same model ("self") or the same family."""
    out = []
    for g in generators:
        for j in judges:
            exact = _base_model(g.split(":", 1)[-1]) == _base_model(j.split(":", 1)[-1])
            family = eval_core.model_family(g) == eval_core.model_family(j)
            if exact or family:
                out.append({"generator": g, "judge": j, "kind": "self" if exact else "same family"})
    return out


# ---------------------------------------------------------------- analysis

def without_provider_errors(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rows minus answers and judgements a provider outage spoiled (they say nothing about a model), and counts."""
    kept, answers, judgements = [], 0, 0
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        if eval_core.is_provider_error(r):
            answers += 1
            by[r.get("generator", "?")] += 1
            continue
        r2 = dict(r)
        for field in ("judgements", "judgements_retest"):
            if r.get(field):
                bad = [j for j in r[field] if eval_core.is_provider_error(j)]
                judgements += len(bad)
                for j in bad:
                    by[j.get("judge", "?")] += 1
                r2[field] = [j for j in r[field] if not eval_core.is_provider_error(j)]
        kept.append(r2)
    return kept, {"answers": answers, "judgements": judgements, "by_model": dict(sorted(by.items()))}


def analyze(rows: list[dict[str, Any]], meta: dict[str, Any] | None = None,
            include_text: bool = False) -> dict[str, Any]:
    """Every number the reports show, from the result rows (run 1 for the headline, all runs for retest)."""
    meta = dict(meta or {})
    rows, provider_errors = without_provider_errors(rows)
    gens = meta.get("generators") or sorted({r["generator"] for r in rows})
    judges = meta.get("judges") or sorted({j["judge"] for r in rows for j in r.get("judgements", [])})
    web_path = not any(r.get("web_path") is False for r in rows)
    rep1 = [r for r in rows if r.get("rep", 1) == 1]
    reps = max((r.get("rep", 1) for r in rows), default=1)

    models: dict[str, Any] = {}
    for g in gens:
        mine = [r for r in rep1 if r["generator"] == g]
        if mine:
            models[g] = _model_numbers(g, mine, [r for r in rows if r["generator"] == g], judges, web_path)

    return {
        "meta": meta,
        "web_path": web_path,
        "generators": [g for g in gens if g in models],
        "judges": judges,
        "reps": reps,
        "questions": len({r["qid"] for r in rows}),
        "answers": len(rows),
        "types": sorted({str(r.get("type")) for r in rep1}),
        "models": models,
        "self_grading": self_grading_pairs(gens, judges),
        "generator_retest": generator_retest(rows, gens, judges),
        "judge_retest": judge_retest(rows, judges),
        "inter_judge": inter_judge(rep1, judges),
        "questions_detail": question_detail(rows, gens, include_text) if include_text is not None else [],
        "plain": rel.PLAIN,
        "provider_errors": provider_errors,
        "routed": _routed(rows),
    }


def _model_numbers(g: str, mine: list[dict[str, Any]], every_run: list[dict[str, Any]], judges: list[str],
                   web_path: bool) -> dict[str, Any]:
    """One answering model: run-1 scores with confidence intervals, by question type, time and cost.

    `mine` is its run-1 rows; `every_run` all its rows (latency and cost count every answer).
    """
    lat = sorted(r["response"].get("latency_ms") or 0 for r in every_run
                 if r["response"].get("status") in ("ok", "not_covered"))
    # Answers a model actually wrote (or a route that called one): what time and cost per answer describe.
    model_lat = [x for x in (eval_core.answer_metrics(r, web_path) for r in every_run)
                 if x["route"] in ("slides", "course_info", "web") or (x["tokens_out"] or 0) > 0]
    timed = [x["latency_ms"] for x in model_lat if x["latency_ms"] is not None]
    return {
        **eval_core.generator_metrics(mine),
        "pass_ci": rel.mean_ci((answer_pass(r) for r in mine), 0, 1),
        "pass_ci_excluding_same_family": rel.mean_ci((answer_pass(r, True) for r in mine), 0, 1),
        "dim_ci": {d: rel.mean_ci((answer_dim(r, d) for r in mine), 1, 5) for d in SCORED},
        "dim_ci_excluding_same_family": {d: rel.mean_ci((answer_dim(r, d, True) for r in mine), 1, 5)
                                         for d in SCORED},
        "teaching_ci": rel.mean_ci((answer_composite(r, dims=DIMENSION_GROUPS["teaching"]) for r in mine), 1, 5),
        "core_ci": rel.mean_ci((answer_composite(r, dims=DIMENSION_GROUPS["core"]) for r in mine), 1, 5),
        "by_type": _by_type(mine, web_path),
        "latencies_ms": lat,
        "latencies_model_ms": sorted(timed),
        "median_latency_model_ms": median(timed) if model_lat else None,
        "cost_per_model_answer": (round(sum(x["cost_usd"] or 0 for x in model_lat) / len(model_lat), 5)
                                  if model_lat else None),
        "model_answers": len(model_lat),
        "per_judge_pass": {j: _pass(x for r in mine for x in ok_judgements(r) if x["judge"] == j) for j in judges},
    }


def _by_type(mine: list[dict[str, Any]], web_path: bool) -> dict[str, Any]:
    """Run-1 numbers per question type (concept, beyond, off_topic, logistics, or none)."""
    by_type = {}
    for t in sorted({r.get("type") for r in mine}, key=lambda x: str(x)):
        rs = [r for r in mine if r.get("type") == t]
        det = eval_core.deterministic_metrics(rs, web_path)
        by_type[str(t)] = {
            "questions": len(rs),
            "route_accuracy": det["route_accuracy"], "route_n": det["route_n"],
            "retrieval_hit_rate": det["retrieval_hit_rate"], "hit_n": det["hit_n"],
            "pass_rate": rel.mean_ci(answer_pass(r) for r in rs)["mean"],
            "pass_rate_excluding_same_family": rel.mean_ci(answer_pass(r, True) for r in rs)["mean"],
            "teaching_mean": rel.mean_ci(answer_composite(r, dims=DIMENSION_GROUPS["teaching"]) for r in rs)["mean"],
            "routes": det["routes"],
        }
    return by_type


def _routed(rows: list[dict[str, Any]]) -> dict[str, int]:
    """How many answers and judgements went through OpenRouter instead of the direct API."""
    every_judgement = [j for r in rows for j in (r.get("judgements") or []) + (r.get("judgements_retest") or [])]
    return {
        "answers": sum(1 for r in rows if (r.get("response") or {}).get("via")),
        "judgements": sum(1 for j in every_judgement if j.get("via")),
        "judgements_total": len(every_judgement),
    }


def _pass(js: Iterable[dict[str, Any]]) -> dict[str, Any]:
    vals = [1.0 if j["verdict"] == "pass" else 0.0 for j in js]
    return {"rate": round(sum(vals) / len(vals), 3) if vals else None, "n": len(vals)}


def generator_retest(rows: list[dict[str, Any]], gens: list[str], judges: list[str]) -> dict[str, Any]:
    """Same question x model answered more than once: do the judged scores and verdicts repeat?"""
    out: dict[str, Any] = {"models": {}, "dimensions": {}}
    by_key = {(r["generator"], r["qid"], r.get("rep", 1)): r for r in rows}
    reps = sorted({r.get("rep", 1) for r in rows})
    if len(reps) < 2:
        return out
    for g in gens:
        out["models"][g] = _model_retest(g, by_key)
    for d in SCORED:
        pairs = _repeat_pairs(by_key, d)
        out["dimensions"][d] = {"icc": rel.icc_2_1([list(p) for p in pairs]), "n": len(pairs),
                                "exact": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None}
    pairs = _repeat_pairs(by_key, "verdict")
    out["verdict_kappa"] = rel.cohen_kappa([p[0] for p in pairs], [p[1] for p in pairs])
    out["verdict_pairs"] = len(pairs)
    return out


def _model_retest(g: str, by_key: dict[tuple[str, str, int], dict[str, Any]]) -> dict[str, Any]:
    """Run 1 against run 2 for one model: score agreement (ICC, Spearman), verdict flips, same route."""
    qids = sorted({q for (gg, q, rep) in by_key if gg == g and rep == 2 and (gg, q, 1) in by_key})
    pts, flips, route_same = [], [], []
    for q in qids:
        a, b = by_key[(g, q, 1)], by_key[(g, q, 2)]
        ca, cb = answer_composite(a), answer_composite(b)
        if ca is not None and cb is not None:
            pts.append({"qid": q, "type": a.get("type"), "run1": round(ca, 3), "run2": round(cb, 3)})
        va = {j["judge"]: j["verdict"] for j in ok_judgements(a)}
        vb = {j["judge"]: j["verdict"] for j in ok_judgements(b)}
        flips += [va[j] != vb[j] for j in va if j in vb]
        route_same.append(eval_core.route_of(a["response"]) == eval_core.route_of(b["response"]))
    taught = [[answer_composite(by_key[(g, q, r)], dims=DIMENSION_GROUPS["teaching"]) for r in (1, 2)] for q in qids]
    three = [q for q in qids if (g, q, 3) in by_key]
    m3 = [[answer_composite(by_key[(g, q, r)]) for r in (1, 2, 3)] for q in three]
    return {
        "n": len(pts),
        "icc": rel.icc_2_1([[p["run1"], p["run2"]] for p in pts]),
        "spearman": rel.spearman([p["run1"] for p in pts], [p["run2"] for p in pts]),
        "pearson": rel.pearson([p["run1"] for p in pts], [p["run2"] for p in pts]),
        "verdict_flip_rate": round(sum(flips) / len(flips), 3) if flips else None,
        "verdict_pairs": len(flips),
        "route_consistency": round(sum(route_same) / len(route_same), 3) if route_same else None,
        # Teaching scores only (answers that taught something): no help from easy declines scoring 5.
        "icc_teaching": rel.icc_2_1(taught),
        "n_teaching": sum(1 for t in taught if None not in t),
        "icc_three_runs": rel.icc_2_1(m3) if len(three) >= 2 else None,
        "n_three_runs": len(three),
        "points": pts,
    }


def _repeat_pairs(by_key: dict[tuple[str, str, int], dict[str, Any]], d: str) -> list[tuple[Any, Any]]:
    """(run 1, run 2) values of one dimension ("verdict" for pass/fail) from the same judge, pooled over models."""
    pairs = []
    for (g, q, rep), a in by_key.items():
        if rep != 1 or (g, q, 2) not in by_key:
            continue
        b = by_key[(g, q, 2)]
        for ja in ok_judgements(a):
            jb = next((x for x in ok_judgements(b) if x["judge"] == ja["judge"]), None)
            if jb is None:
                continue
            if d == "verdict":
                pairs.append((ja["verdict"], jb["verdict"]))
            elif ja["scores"].get(d) is not None and jb["scores"].get(d) is not None:
                pairs.append((ja["scores"][d], jb["scores"][d]))
    return pairs


def judge_retest(rows: list[dict[str, Any]], judges: list[str]) -> dict[str, Any]:
    """The same judge scoring the same answer twice."""
    out = {}
    for name in judges:
        pairs, verdicts, answers = [], [], 0
        per_dim: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for r in rows:
            a = next((j for j in ok_judgements(r) if j["judge"] == name), None)
            b = next((j for j in ok_judgements(r, "judgements_retest") if j["judge"] == name), None)
            if a is None or b is None:
                continue
            answers += 1
            verdicts.append((a["verdict"], b["verdict"]))
            for d in SCORED:
                if a["scores"].get(d) is not None and b["scores"].get(d) is not None:
                    pairs.append((a["scores"][d], b["scores"][d]))
                    per_dim[d].append((a["scores"][d], b["scores"][d]))
        out[name] = {
            "answers": answers,
            "score_pairs": len(pairs),
            "icc": rel.icc_2_1([list(p) for p in pairs]),
            "exact": round(sum(a == b for a, b in pairs) / len(pairs), 3) if pairs else None,
            "within_one": round(sum(abs(a - b) <= 1 for a, b in pairs) / len(pairs), 3) if pairs else None,
            "kappa": rel.cohen_kappa([v[0] for v in verdicts], [v[1] for v in verdicts]),
            "verdict_same": round(sum(a == b for a, b in verdicts) / len(verdicts), 3) if verdicts else None,
            "teaching_icc": rel.icc_2_1([list(p) for d in DIMENSION_GROUPS["teaching"] for p in per_dim[d]]),
        }
    return out


def inter_judge(rep1: list[dict[str, Any]], judges: list[str]) -> dict[str, Any]:
    """Do the judges agree? Krippendorff's alpha (ordinal) per dimension, exact and within-one shares, and a
    judge x judge matrix of verdict agreement and kappa."""
    dims = {}
    for d in SCORED:
        units = []
        for r in rep1:
            by = {j["judge"]: j["scores"].get(d) for j in ok_judgements(r)}
            units.append([by.get(name) for name in judges])
        agree = rel.pairwise_agreement(units)
        dims[d] = {"alpha": rel.krippendorff_alpha_ordinal(units), "exact": agree["exact"],
                   "within_one": agree["within_one"], "pairs": agree["pairs"],
                   "answers": sum(1 for u in units if sum(v is not None for v in u) >= 2)}
    verdict_value = {"pass": 1.0, "fail": 0.0}
    verdict_units = [[verdict_value.get(next((j["verdict"] for j in ok_judgements(r) if j["judge"] == n), ""))
                      for n in judges] for r in rep1]
    matrix = {}
    for a in judges:
        for b in judges:
            if a == b:
                continue
            va, vb = [], []
            gaps = []
            for r in rep1:
                ja = next((j for j in ok_judgements(r) if j["judge"] == a), None)
                jb = next((j for j in ok_judgements(r) if j["judge"] == b), None)
                if ja is None or jb is None:
                    continue
                va.append(ja["verdict"])
                vb.append(jb["verdict"])
                gaps += [abs(ja["scores"][d] - jb["scores"][d]) for d in SCORED
                         if ja["scores"].get(d) is not None and jb["scores"].get(d) is not None]
            matrix[f"{a}|{b}"] = {
                "same_verdict": round(sum(x == y for x, y in zip(va, vb)) / len(va), 3) if va else None,
                "kappa": rel.cohen_kappa(va, vb), "n": len(va),
                "mean_gap": round(sum(gaps) / len(gaps), 2) if gaps else None,
            }
    return {"dimensions": dims, "verdict_alpha": rel.krippendorff_alpha_ordinal(verdict_units),
            "verdict_agreement": rel.pairwise_agreement(verdict_units), "matrix": matrix}


def question_detail(rows: list[dict[str, Any]], gens: list[str], include_text: bool) -> list[dict[str, Any]]:
    """Run 1, question by question: route, hit, passes and slides per model (text only if asked)."""
    out = []
    for qid in sorted({r["qid"] for r in rows}):
        rs = [r for r in rows if r["qid"] == qid and r.get("rep", 1) == 1]
        if not rs:
            continue
        first = rs[0]
        item = {"qid": qid, "type": first.get("type"), "category": first.get("category"),
                "expected": eval_core.expected_routes(first, first.get("web_path", True)),
                "expected_slides": first.get("expected_slides") or [], "models": {}}
        if include_text:
            item["question"] = first.get("question")
        for r in rs:
            m = eval_core.answer_metrics(r, r.get("web_path", True))
            item["models"][r["generator"]] = {
                "route": m["route"], "route_ok": m["route_ok"], "hit": m["hit"],
                "passes": sum(1 for j in ok_judgements(r) if j["verdict"] == "pass"),
                "judged": len(ok_judgements(r)),
                "slides": eval_core.response_slides(r["response"]),
                "composite": None if answer_composite(r) is None else round(answer_composite(r), 2),
            }
        out.append(item)
    return out


# ---------------------------------------------------------------- markdown

def _ci(c: dict[str, Any], rate: bool = False) -> str:
    """A mean with its 95% interval and n, as "4.21 [3.98–4.40] (n=20)" or "68% [52–81] (n=20)"."""
    if c.get("mean") is None:
        return "n/a"
    if rate:
        return f"{round(c['mean'] * 100)}% [{round(c['low'] * 100)}–{round(c['high'] * 100)}] (n={c['n']})"
    return f"{c['mean']:.2f} [{c['low']:.2f}–{c['high']:.2f}] (n={c['n']})"


def seconds(ms: float | None) -> float | None:
    """Milliseconds as seconds; None stays None."""
    return None if ms is None else (ms or 0) / 1000


def short(model_key: str) -> str:
    """A model's short label: "openrouter:google/gemini-3.8-flash" -> "gemini-3.8-flash"."""
    return model_key.split(":", 1)[-1].split("/")[-1]


def markdown(a: dict[str, Any], detail: bool) -> str:
    """The comparison report as Markdown. `detail` adds the question-by-question table."""
    lines = _md_intro(a) + _md_headline(a) + _md_rubric(a) + _md_measured(a) + _md_by_type(a) + _md_per_judge(a)
    lines += reliability_markdown(a)
    if detail and a["questions_detail"]:
        lines += _md_questions(a)
    return "\n".join(lines) + "\n"


def _md_intro(a: dict[str, Any]) -> list[str]:
    """Title, what was compared, and the notes a reader needs before the numbers."""
    meta = a["meta"]
    gens, judges = a["generators"], a["judges"]
    L: list[str] = []
    L.append(f"# Model comparison: {meta.get('label') or meta.get('run_id') or 'eval run'}")
    L.append("")
    L.append(f"Question set: `{meta.get('questions_file', '?')}` ({a['questions']} questions"
             + (", invented, committable" if meta.get("private") is False else ", private real questions") + "). "
             f"Answering models: {', '.join(f'`{g}`' for g in gens)}. Judges: {', '.join(f'`{j}`' for j in judges)}. "
             f"Answers: {a['answers']} ({a['reps']} run{'s' if a['reps'] > 1 else ''} of each question x model "
             f"where repeated). Spend: ${meta.get('spend_usd', 0):.2f}.")
    L.append("")
    if not a["web_path"]:
        L.append("- **The \"beyond the slides\" web path was not in the app for this run.** Web questions were "
                 "expected to be declined (route accuracy counts a decline as right); judges were told the expected "
                 "behavior is a web answer, so their scores show what students would lose without it.")
    pe = a.get("provider_errors") or {}
    if pe.get("answers") or pe.get("judgements"):
        L.append(f"- **Provider errors (no credit or quota):** {pe['answers']} answers and {pe['judgements']} "
                 "judgements were refused by their provider and are left out of every number here ("
                 + ", ".join(f"`{k}` {v}" for k, v in pe["by_model"].items())
                 + "). Running the same command on this folder asks exactly those again.")
    rt = a.get("routed") or {}
    if rt.get("answers") or rt.get("judgements"):
        L.append(f"- **Routing:** {rt['answers']} of {a['answers']} answers and {rt['judgements']} of "
                 f"{rt['judgements_total']} judgements by Claude models went through OpenRouter (the same models) "
                 "after the direct Anthropic API key ran out of credit mid-run; their latency includes "
                 "OpenRouter's hop.")
    L.append("- Every score is 1 to 5 (1 = very poor, 3 = acceptable, 5 = excellent): the mean over answers, each "
             "answer's value the mean of the judges that scored it, with a 95% confidence interval in brackets and n "
             "= answers. Pass rate is the share of judge verdicts that were pass.")
    L.append("- **Self-grading:** " + "; ".join(f"`{s['judge']}` judging `{s['generator']}` ({s['kind']})"
                                                for s in a["self_grading"]) + ". The column \"without same-family "
             "judges\" drops a Claude judge on Claude answers and a GPT judge on GPT answers.")
    L.append("")
    return L


def _md_headline(a: dict[str, Any]) -> list[str]:
    L = ["## Headline", ""]
    L.append("| Model | Pass rate, all judges (%, 95% CI, n answers) | Pass rate without same-family judges | "
             "Core rubric mean (1–5) | Teaching mean (1–5) | Retrieval hit (% of concept questions) | "
             "Right route (% of answers) | Median time to answer, model answers (s) | Cost per answer (USD, n) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for g in a["generators"]:
        m = a["models"][g]
        L.append(f"| `{short(g)}` | {_ci(m['pass_ci'], True)} | {_ci(m['pass_ci_excluding_same_family'], True)} | "
                 f"{_ci(m['core_ci'])} | {_ci(m['teaching_ci'])} | {fmt_pct(m['retrieval_hit_rate'])} "
                 f"(n={m['hit_n']}) | {fmt_pct(m['route_accuracy'])} (n={m['route_n']}) | "
                 f"{fmt_num(seconds(m['median_latency_model_ms']), 1)} "
                 f"(n={m['model_answers']}) | ${fmt_num(m['cost_per_answer'], 4)} (n={m['cost_n']}) |")
    L.append("")
    return L


def _md_rubric(a: dict[str, Any]) -> list[str]:
    """Per-dimension scores for the core and teaching groups, with and without same-family judges."""
    L: list[str] = []
    for group, title in (("core", "Core rubric (1–5, 5 best)"), ("teaching", "Teaching quality (1–5, 5 best)")):
        dims = DIMENSION_GROUPS[group]
        header = ["| Model | " + " | ".join(DIMENSION_LABELS[d] for d in dims) + " |", "| --- " * (len(dims) + 1) + "|"]
        L += [f"## {title}", ""] + header
        for g in a["generators"]:
            m = a["models"][g]
            L.append(f"| `{short(g)}` | " + " | ".join(_ci(m["dim_ci"][d]) for d in dims) + " |")
        L += ["", "Without same-family judges:", ""] + header
        for g in a["generators"]:
            m = a["models"][g]
            L.append(f"| `{short(g)}` | " + " | ".join(_ci(m["dim_ci_excluding_same_family"][d]) for d in dims) + " |")
        L.append("")
    L += [f"- {DIMENSION_LABELS[d]} ({d}): {eval_core.DIMENSIONS[d]}" for d in SCORED]
    L.append("")
    return L


def _md_measured(a: dict[str, Any]) -> list[str]:
    """Numbers measured from the answers themselves, with no judge."""
    L = ["## Measured without a judge", ""]
    L.append("| Model | Right route (%) | Retrieval hit (%) | Slide precision (%) | One course only (%) | "
             "Fell back to speaker notes (%) | Mean time to answer (s, all answers) | Total cost (USD) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for g in a["generators"]:
        m = a["models"][g]
        L.append(f"| `{short(g)}` | {fmt_pct(m['route_accuracy'])} (n={m['route_n']}) | "
                 f"{fmt_pct(m['retrieval_hit_rate'])} (n={m['hit_n']}) | {fmt_pct(m['slide_precision'])} "
                 f"(n={m['precision_n']}) | {fmt_pct(m['course_purity'])} (n={m['purity_n']}) | "
                 f"{fmt_pct(m['fallback_rate'])} | {fmt_num(seconds(m['mean_latency_ms']), 1)} (n={m['latency_n']}) | "
                 f"${fmt_num(m['total_cost'], 3)} |")
    L.append("")
    L.append("- Right route: the answer came from the path the question expects (slides, Canvas, FAQ, referral to Ben, "
             "web, or a decline). Retrieval hit: at least one expected slide was among the answer's slides "
             "(questions with expected slides that should be answered from slides). Slide precision: share of the "
             "answer's slides that were expected. One course only: answers whose slides all came from one course.")
    L.append("")
    return L


def _md_by_type(a: dict[str, Any]) -> list[str]:
    L = ["## By question type (run 1)", ""]
    L.append("| Model | Type | Questions (n) | Right route (%) | Retrieval hit (%) | Pass rate (%) | "
             "Pass rate without same-family judges (%) | Teaching mean (1–5) | Routes taken |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for g in a["generators"]:
        for t, b in a["models"][g]["by_type"].items():
            L.append(f"| `{short(g)}` | {TYPE_LABELS.get(None if t == 'None' else t, t)} | {b['questions']} | "
                     f"{fmt_pct(b['route_accuracy'])} | {fmt_pct(b['retrieval_hit_rate'])} | "
                     f"{fmt_pct(b['pass_rate'])} | "
                     f"{fmt_pct(b['pass_rate_excluding_same_family'])} | {fmt_num(b['teaching_mean'])} | "
                     + ", ".join(f"{k} {v}" for k, v in b["routes"].items()) + " |")
    L.append("")
    return L


def _md_per_judge(a: dict[str, Any]) -> list[str]:
    """Pass rate by judge, with self-grading pairs marked."""
    judges = a["judges"]
    L = ["## Pass rate by judge (%, n verdicts)", ""]
    L.append("| Model | " + " | ".join(f"`{short(j)}`" for j in judges) + " |")
    L.append("| --- " * (len(judges) + 1) + "|")
    flags = {(s["generator"], s["judge"]): s["kind"] for s in a["self_grading"]}
    for g in a["generators"]:
        cells = []
        for j in judges:
            p = a["models"][g]["per_judge_pass"][j]
            mark = f" ({flags[(g, j)]})" if (g, j) in flags else ""
            cells.append(f"{fmt_pct(p['rate'])} (n={p['n']}){mark}")
        L.append(f"| `{short(g)}` | " + " | ".join(cells) + " |")
    L.append("")
    return L


def _md_questions(a: dict[str, Any]) -> list[str]:
    """Question by question: the route each model took and how many judges passed it (run 1)."""
    gens = a["generators"]
    L = ["## Question by question (run 1)", ""]
    L.append("| Question | Type | Expected | " + " | ".join(f"`{short(g)}`" for g in gens) + " |")
    L.append("| --- " * (len(gens) + 3) + "|")
    for q in a["questions_detail"]:
        text = q.get("question")
        label = f"{q['qid']}: {text}" if text else q["qid"]
        cells = []
        for g in gens:
            x = q["models"].get(g)
            if not x:
                cells.append("n/a")
                continue
            mark = "" if x["route_ok"] else " (wrong route)"
            hit = "" if x["hit"] is None else (", hit" if x["hit"] else ", miss")
            cells.append(f"{x['route']}{mark}{hit}, {x['passes']}/{x['judged']} pass")
        L.append(f"| {label.replace('|', '/')} | {TYPE_LABELS.get(q['type'], q['type'])} | "
                 f"{' or '.join(q['expected'])} | " + " | ".join(cells) + " |")
    L.append("")
    return L


def reliability_markdown(a: dict[str, Any]) -> list[str]:
    """Test-retest (generators and judges) and inter-judge agreement, as Markdown lines."""
    return (["## Test-retest reliability", ""] + _md_generator_retest(a) + _md_judge_retest(a)
            + _md_inter_judge(a))


def _md_generator_retest(a: dict[str, Any]) -> list[str]:
    P = a["plain"]
    gr = a["generator_retest"]
    if not gr.get("models"):
        return ["No repeated answers in this run.", ""]
    L = ["**Generator stability:** the same question answered again by the same model, judged again by the "
         "same judges. Each answer's score is the mean of every judge's mean over the eleven scored dimensions.", ""]
    L.append("| Model | ICC(2,1), run 1 vs run 2 | Spearman | ICC, teaching scores only (n answers) | "
             "Verdict flip rate (% of judge verdicts) | Same route both times (%) | Questions (n) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- |")
    for g, m in gr["models"].items():
        L.append(f"| `{short(g)}` | {fmt_num(m['icc'])} | {fmt_num(m['spearman'])} | {fmt_num(m.get('icc_teaching'))} "
                 f"(n={m.get('n_teaching')}) | {fmt_pct(m['verdict_flip_rate'])} (n={m['verdict_pairs']}) | "
                 f"{fmt_pct(m['route_consistency'])} | {m['n']} |")
    L.append("")
    L.append("The all-dimension ICC is high partly because questions differ a lot (a clean decline scores near 5 "
             "on every dimension it has); the teaching-only ICC compares answers that actually taught something.")
    L.append("")
    L.append("| Dimension (pooled over models and judges) | ICC(2,1), run 1 vs run 2 | Same score (%) | Pairs (n) |")
    L.append("| --- | --- | --- | --- |")
    for d, m in gr["dimensions"].items():
        L.append(f"| {DIMENSION_LABELS[d]} | {fmt_num(m['icc'])} | {fmt_pct(m['exact'])} | {m['n']} |")
    L.append(f"| Verdict (kappa) | {fmt_num(gr.get('verdict_kappa'))} |  | {gr.get('verdict_pairs')} |")
    L.append("")
    L += [f"- {P['icc']}", f"- {P['spearman']}", f"- {P['flip']}", ""]
    return L


def _md_judge_retest(a: dict[str, Any]) -> list[str]:
    P = a["plain"]
    jr = {k: v for k, v in a["judge_retest"].items() if v["answers"]}
    if not jr:
        return []
    L = ["**Judge stability:** the same judge scoring the same answer a second time.", ""]
    L.append("| Judge | ICC(2,1) of scores | Teaching ICC | Same score (%) | Within one point (%) | "
             "Kappa on pass/fail | Same verdict (%) | Answers (n) | Score pairs (n) |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for name, m in jr.items():
        L.append(f"| `{short(name)}` | {fmt_num(m['icc'])} | {fmt_num(m['teaching_icc'])} | {fmt_pct(m['exact'])} | "
                 f"{fmt_pct(m['within_one'])} | {fmt_num(m['kappa'])} | {fmt_pct(m['verdict_same'])} | "
                 f"{m['answers']} | {m['score_pairs']} |")
    L.append("")
    L += [f"- {P['icc']}", f"- {P['kappa']}", ""]
    return L


def _md_inter_judge(a: dict[str, Any]) -> list[str]:
    P = a["plain"]
    ij = a["inter_judge"]
    L = ["## Inter-judge agreement (run 1)", ""]
    L.append("| Dimension | Krippendorff's alpha (ordinal) | Same score (% of judge pairs) | Within one point (%) | "
             "Answers with 2+ judges (n) |")
    L.append("| --- | --- | --- | --- | --- |")
    for d, m in ij["dimensions"].items():
        L.append(f"| {DIMENSION_LABELS[d]} | {fmt_num(m['alpha'])} | {fmt_pct(m['exact'])} | "
                 f"{fmt_pct(m['within_one'])} | {m['answers']} |")
    L.append(f"| Verdict (pass/fail) | {fmt_num(ij['verdict_alpha'])} | "
             f"{fmt_pct(ij['verdict_agreement']['exact'])} | | |")
    L.append("")
    L += [f"- {P['alpha']}", f"- {P['exact']}", f"- {P['within_one']}", ""]
    L.append("| Judge pair | Same verdict (%) | Cohen's kappa | Mean score gap (points, 0–4) | Answers (n) |")
    L.append("| --- | --- | --- | --- | --- |")
    seen = set()
    for k, m in ij["matrix"].items():
        a_, b_ = k.split("|")
        if (b_, a_) in seen:
            continue
        seen.add((a_, b_))
        L.append(f"| `{short(a_)}` vs `{short(b_)}` | {fmt_pct(m['same_verdict'])} | {fmt_num(m['kappa'])} | "
                 f"{fmt_num(m['mean_gap'])} | {m['n']} |")
    L.append("")
    return L


# ---------------------------------------------------------------- write

def _strip_text(a: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(a))
    for q in out.get("questions_detail", []):
        q.pop("question", None)
    return out


def write_all(rows: list[dict[str, Any]], meta: dict[str, Any], out_dir: Path, public_dir: Path | None) -> list[Path]:
    """Write every report into the run folder (and, for a committable set, into evals/reports/)."""
    from . import report_html

    include_text = meta.get("private") is False
    a = analyze(rows, meta, include_text=True)
    shareable = a if include_text else _strip_text(a)
    written = []
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "compare.md").write_text(markdown(a, detail=True), encoding="utf-8")
    (out_dir / "compare_summary.md").write_text(markdown(_strip_text(a), detail=False), encoding="utf-8")
    (out_dir / "compare_summary.json").write_text(json.dumps(_strip_text(a), indent=1), encoding="utf-8")
    (out_dir / "report.html").write_text(report_html.render(shareable, include_text=include_text), encoding="utf-8")
    written += [out_dir / n for n in ("compare.md", "compare_summary.md", "compare_summary.json", "report.html")]
    if public_dir is not None and include_text:
        public_dir.mkdir(parents=True, exist_ok=True)
        (public_dir / "compare.md").write_text(markdown(a, detail=True), encoding="utf-8")
        (public_dir / "compare_summary.json").write_text(json.dumps(a, indent=1), encoding="utf-8")
        (public_dir / "report.html").write_text(report_html.render(a, include_text=True), encoding="utf-8")
        written += [public_dir / n for n in ("compare.md", "compare_summary.json", "report.html")]
    return written
