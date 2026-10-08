"""The format of an eval run, and the numbers built from it (docs/SPEC.md, "Evals").

Pure functions, no web framework: `app/admin_evals.py` (the Settings routes)
and the local scripts (`scripts/import_eval_history.py`) both use them.
"""

from __future__ import annotations

from typing import Any

from . import eval_core

ACTIVE = "running"
FINISHED = ("done", "cancelled", "stopped", "excluded")


# Labels on runs imported from the command line (scripts/import_eval_history.py).
IMPORTED_NOTE = (
    "Imported from the command-line run of October 7. It predates PR #35 (quiz access codes kept out of "
    "slides, transcripts and clips) and PR #37 (logistics questions routed to Ben before narration)."
)


def model_key(m: dict[str, str]) -> str:
    return f"{m['provider']}:{m['model']}"


def _base_model(model: str) -> str:
    """claude-sonnet-5-5, anthropic/claude-sonnet-5.5 and claude-sonnet-5.5 all compare equal."""
    return model.rsplit("/", 1)[-1].lower().replace(".", "-")


def self_grading(generators: list[dict[str, str]], judges: list[dict[str, str]]) -> list[dict[str, str]]:
    """Judge and generator pairs that are the same model: that judge is grading its own answers."""
    out = []
    for g in generators:
        for j in judges:
            if _base_model(g["model"]) == _base_model(j["model"]):
                out.append({"generator": model_key(g), "judge": model_key(j)})
    return out


SELF_GRADING_NOTE = (
    "A judge grading its own model's answers is lenient: in the October 5 baseline, gpt-6.1-sol passed 68% "
    "of its own answers while gpt-6-luna passed 27% of the same answers."
)


def index_entry(run: dict[str, Any]) -> dict[str, Any]:
    """What the runs list and the report card need: no question text."""
    return {
        "id": run["id"],
        "name": run.get("name"),
        "kind": run.get("kind", "admin"),
        "created_at": run.get("created_at"),
        "finished_at": run.get("finished_at"),
        "status": run.get("status"),
        "status_note": run.get("status_note"),
        "excluded": run.get("status") == "excluded" or bool(run.get("excluded")),
        "generators": [model_key(g) for g in run.get("generators", [])],
        "generator_labels": run.get("generator_labels") or {},
        "judges": [model_key(j) for j in run.get("judges", [])],
        "questions": len(run.get("questions", [])) or run.get("question_count"),
        "pairs_total": run.get("pairs_total"),
        "pairs_done": run.get("pairs_done"),
        "calls_used": run.get("calls_used"),
        "notes": run.get("notes", []),
        "self_grading": run.get("self_grading", []),
        "by_generator": (run.get("summary") or {}).get("by_generator", {}),
        "source": run.get("source"),
        "prompt_versions": run.get("prompt_versions") or {},
    }


def progress(run: dict[str, Any]) -> dict[str, Any]:
    total = int(run.get("pairs_total") or 0)
    done = int(run.get("pairs_done") or 0)
    return {
        "run_id": run["id"],
        "status": run.get("status"),
        "status_note": run.get("status_note"),
        "done": done,
        "total": total,
        "fraction": round(done / total, 4) if total else 1.0,
        "calls_used": run.get("calls_used", 0),
        "call_cap": run.get("call_cap"),
        "finished": run.get("status") in FINISHED,
    }


def pairs_of(run: dict[str, Any]) -> list[tuple[int, dict[str, Any], dict[str, str]]]:
    """Every (pair index, question, generator), question-major so one question's embedding is reused."""
    out = []
    i = 0
    for q in run["questions"]:
        for g in run["generators"]:
            out.append((i, q, g))
            i += 1
    return out


def summarize(run: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_gen = {}
    for g in run.get("generators", []):
        key = model_key(g)
        mine = [r for r in rows if r.get("generator") == key]
        if mine:
            by_gen[key] = eval_core.generator_metrics(mine)
    return {"by_generator": by_gen, "overall": eval_core.summary(rows, {"run": run["id"]}) if rows else None}


REPORT_METRICS = ("pass_rate", "decline_accuracy", "fallback_rate", "judge_agreement")
# Added Oct 8 (docs/SPEC.md, Block 8c): measured without a judge, plus the pass rate without same-family judges.
EXTRA_METRICS = ("pass_rate_excluding_same_family", "route_accuracy", "retrieval_hit_rate", "slide_precision",
                 "course_purity", "cost_per_answer", "mean_latency_ms", "route_n", "hit_n", "cost_n", "latency_n",
                 "judgements_excluding_same_family", "group_means")


def report_card(runs: list[dict[str, Any]], calibration: dict[str, Any]) -> dict[str, Any]:
    """Metrics over time per generator model, oldest first. Excluded and unfinished-empty runs are left out."""
    series: dict[str, dict[str, Any]] = {}
    for r in sorted(runs, key=lambda r: (str(r.get("created_at") or ""), str(r.get("id")))):
        if r.get("excluded") or r.get("status") == "excluded":
            continue
        for key, m in (r.get("by_generator") or {}).items():
            if not m:
                continue
            label = (r.get("generator_labels") or {}).get(key) or key
            s = series.setdefault(key, {"generator": key, "label": label, "points": []})
            s["points"].append({
                "run_id": r["id"],
                "run_name": r.get("name"),
                "kind": r.get("kind"),
                "at": r.get("finished_at") or r.get("created_at"),
                "status": r.get("status"),
                "judges": r.get("judges"),
                "notes": r.get("notes", []),
                "self_grading": [x for x in r.get("self_grading", []) if x.get("generator") == key],
                "questions": m.get("questions"),
                "answered": m.get("answered"),
                "judgements": m.get("judgements"),
                "score_n": m.get("score_n"),
                "pass_rate": m.get("pass_rate"),
                "decline_accuracy": m.get("decline_accuracy"),
                "fallback_rate": m.get("fallback_rate"),
                "judge_agreement": m.get("judge_agreement"),
                **{k: m.get(k) for k in EXTRA_METRICS},
                "scores": m.get("scores") or {},
                "per_judge": m.get("per_judge") or {},
            })
    judges = []
    for name, c in sorted(calibration.items()):
        if not isinstance(c, dict):
            continue
        judges.append({"judge": name, "met": c.get("met"), "cases": c.get("cases"), "done": c.get("done"),
                       "missed": c.get("missed", []), "at": c.get("finished_at") or c.get("started_at"),
                       "source": c.get("source", "settings")})
    return {"series": list(series.values()), "dimensions": list(eval_core.DIMENSIONS),
            "dimension_groups": {g: list(d) for g, d in eval_core.DIMENSION_GROUPS.items()},
            "metrics": list(REPORT_METRICS) + list(EXTRA_METRICS[:7]),
            "calibration": judges, "legend": legend()}


def legend() -> dict[str, Any]:
    """What every eval table and chart on the Settings page explains under it (app/eval_core.py)."""
    return {
        "dimensions": [{"key": d, "label": eval_core.DIMENSION_LABELS[d], "header": eval_core.dimension_header(d),
                        "description": text,
                        "group": next(g for g, ds in eval_core.DIMENSION_GROUPS.items() if d in ds)}
                       for d, text in eval_core.DIMENSIONS.items()],
        "groups": dict(eval_core.GROUP_LABELS),
        "scale": eval_core.SCALE_NOTE,
        "mean": eval_core.MEAN_NOTE,
        "na": eval_core.NA_NOTE,
        "verdict": eval_core.VERDICT_NOTE,
        "self_grading": SELF_GRADING_NOTE,
    }

