"""Settings > Evals, read-only views over finished runs: which questions fail most, and how judges compare.

Two views (docs/SPEC.md, "Evals"), both built from the same private result rows
`admin_evals` writes (one row per question x answering model, each with every
judge's verdict and scores). Nothing here calls a model or writes anything.

- `questions(...)`: every question across the chosen runs, hardest first (lowest
  share of judgements that passed), with per-model pass rates and how often the
  judges agreed. `question_detail(...)` returns one question's answers, each with
  every judge's verdict, scores, and rationale, and the agreement between them.
- `compare(...)`: one run as matrices: pass rate per answering model x judge, each
  judge's leniency, verdict agreement and mean score gap for every judge pair, and
  mean score per dimension per answering model.

Question text is private (de-identified student email): these functions are only
reached through admin routes behind the ft_admin cookie.
"""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations
from statistics import mean
from typing import Any

from . import eval_core

Run = dict[str, Any]
Row = dict[str, Any]


def _ok(judgements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        j for j in judgements or [] if isinstance(j, dict) and "error" not in j and j.get("verdict") in ("pass", "fail")
    ]


def _errors(judgements: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Judge calls that failed ({judge, error}, no verdict): skipped by every rate here, but counted."""
    return [j for j in judgements or [] if isinstance(j, dict) and "error" in j]


def _avg(values: list[float], digits: int = 3) -> float | None:
    return round(mean(values), digits) if values else None


def agreement(judgements: list[dict[str, Any]]) -> dict[str, Any]:
    """How much one answer's judges agree.

    `verdict` is the share of judges that gave the majority verdict (1.0 = unanimous; 0.5 = a split
    between two judges). `score_spread` is, per dimension, the gap between the highest and lowest
    score (0 = the same score from every judge); `mean_spread` averages it over the dimensions scored.
    """
    ok = _ok(judgements)
    if not ok:
        return {
            "judges": 0,
            "verdict": None,
            "unanimous": None,
            "majority": None,
            "score_spread": {},
            "mean_spread": None,
        }
    passes = sum(1 for j in ok if j["verdict"] == "pass")
    majority = "pass" if passes * 2 > len(ok) else "fail" if passes * 2 < len(ok) else "split"
    share = max(passes, len(ok) - passes) / len(ok)
    spread: dict[str, float] = {}
    for dim in eval_core.DIMENSIONS:
        vals = [
            float(j["scores"][dim])
            for j in ok
            if isinstance(j.get("scores"), dict) and j["scores"].get(dim) is not None
        ]
        if len(vals) >= 2:
            spread[dim] = round(max(vals) - min(vals), 2)
    return {
        "judges": len(ok),
        "verdict": round(share, 3),
        "unanimous": share == 1.0 and len(ok) > 1,
        "majority": majority,
        "score_spread": spread,
        "mean_spread": _avg(list(spread.values()), 2),
    }


def _answer_text(row: Row, limit: int = 1200) -> str:
    resp = row.get("response") or {}
    parts = [str(s.get("narration") or "") for s in resp.get("segments") or []]
    text = " ".join(p for p in parts if p) or str(resp.get("message") or "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _qkey(row: Row) -> str:
    return str(row.get("qid") or "")


def _usable(runs_rows: list[tuple[Run, list[Row]]], run_id: str | None) -> list[tuple[Run, list[Row]]]:
    out = []
    for run, rows in runs_rows:
        if run.get("excluded") or run.get("status") == "excluded":
            continue
        if run_id and run.get("id") != run_id:
            continue
        out.append((run, rows))
    return out


def questions(
    runs_rows: list[tuple[Run, list[Row]]], generator: str | None = None, run_id: str | None = None
) -> dict[str, Any]:
    """Every question, hardest first, across the usable runs (optionally one run and one answering model).

    This is the Evals "Questions" table: it shows Ben which questions fail most, so he knows where the
    material or the prompt needs work.
    """
    usable = _usable(runs_rows, run_id)
    generators = sorted({str(r.get("generator")) for _, rows in usable for r in rows if r.get("generator")})
    judges = sorted({str(j.get("judge")) for _, rows in usable for r in rows for j in _ok(r.get("judgements"))})
    by_q: dict[str, dict[str, Any]] = {}
    for run, rows in usable:
        for row in rows:
            if generator and row.get("generator") != generator:
                continue
            _tally_answer(by_q.setdefault(_qkey(row), _new_tally(row)), run, row)
    out = sorted((_question_summary(q) for q in by_q.values()), key=_hardest_first)
    return {
        "runs": [_run_heading(r) for r, _ in usable],
        "generators": generators,
        "judges": judges,
        "filter": {"generator": generator, "run_id": run_id},
        "questions": out,
    }


def _new_tally(row: Row) -> dict[str, Any]:
    """A question's empty running totals, headed by the fields of its first answer row."""
    return {
        "qid": row.get("qid"),
        "question": row.get("question"),
        "category": row.get("category"),
        "course": row.get("course"),
        "answerable": row.get("answerable"),
        "answers": 0,
        "judgements": 0,
        "passes": 0,
        "errors": 0,
        "agreements": [],
        "unanimous": 0,
        "by_generator": {},
        "outcomes": {},
        "runs": set(),
    }


def _tally_answer(q: dict[str, Any], run: Run, row: Row) -> None:
    """Add one answer row (one answering model in one run) to its question's totals."""
    ok = _ok(row.get("judgements"))
    passes = sum(1 for j in ok if j["verdict"] == "pass")
    q["answers"] += 1
    q["errors"] += len(_errors(row.get("judgements")))
    q["runs"].add(run.get("id"))
    q["judgements"] += len(ok)
    q["passes"] += passes
    outcome = str(row.get("outcome") or "unknown")
    q["outcomes"][outcome] = q["outcomes"].get(outcome, 0) + 1
    agr = agreement(row.get("judgements"))
    if agr["verdict"] is not None and agr["judges"] > 1:  # one judge cannot disagree with itself
        q["agreements"].append(agr["verdict"])
        q["unanimous"] += 1 if agr["unanimous"] else 0
    g = q["by_generator"].setdefault(str(row.get("generator")), {"answers": 0, "judgements": 0, "passes": 0})
    g["answers"] += 1
    g["judgements"] += len(ok)
    g["passes"] += passes


def _rate(passes: int, total: int) -> float | None:
    return round(passes / total, 3) if total else None


def _question_summary(q: dict[str, Any]) -> dict[str, Any]:
    """One row of the Questions table from a question's totals."""
    return {
        "qid": q["qid"],
        "question": q["question"],
        "category": q["category"],
        "course": q["course"],
        "answerable": q["answerable"],
        "answers": q["answers"],
        "judgements": q["judgements"],
        "runs": len(q["runs"]),
        "pass_rate": _rate(q["passes"], q["judgements"]),
        "fails": q["judgements"] - q["passes"],
        "errors_skipped": q["errors"],
        "agreement": _avg(q["agreements"]),
        "unanimous_share": _rate(q["unanimous"], len(q["agreements"])),
        "outcomes": q["outcomes"],
        "by_generator": {
            k: {
                "answers": v["answers"],
                "judgements": v["judgements"],
                "pass_rate": _rate(v["passes"], v["judgements"]),
            }
            for k, v in sorted(q["by_generator"].items())
        },
    }


def _hardest_first(q: dict[str, Any]) -> tuple[float, int, str]:
    """Sort key: lowest pass rate first (unjudged last), then the most judged, then by id."""
    return (q["pass_rate"] if q["pass_rate"] is not None else 2.0, -(q["judgements"]), str(q["qid"]))


def _run_heading(run: Run) -> dict[str, Any]:
    return {
        "id": run.get("id"),
        "name": run.get("name"),
        "at": run.get("finished_at") or run.get("created_at"),
        "judges": run.get("judges"),
    }


def question_detail(
    runs_rows: list[tuple[Run, list[Row]]], qid: str, generator: str | None = None, run_id: str | None = None
) -> dict[str, Any]:
    """One question: every answer (per run and answering model) with each judge's verdict, scores and reason."""
    answers = []
    head: dict[str, Any] | None = None
    for run, rows in _usable(runs_rows, run_id):
        for row in rows:
            if _qkey(row) != qid or (generator and row.get("generator") != generator):
                continue
            head = head or {
                k: row.get(k) for k in ("qid", "question", "category", "course", "answerable", "reference_answer")
            }
            answers.append(
                {
                    "run_id": run.get("id"),
                    "run_name": run.get("name"),
                    "at": row.get("at") or run.get("finished_at") or run.get("created_at"),
                    "generator": row.get("generator"),
                    "outcome": row.get("outcome"),
                    "answer": _answer_text(row),
                    "judgements": [
                        {
                            "judge": j.get("judge"),
                            "verdict": j.get("verdict"),
                            "scores": j.get("scores") or {},
                            "rationale": j.get("rationale"),
                            "issues": j.get("issues") or [],
                            "error": j.get("error"),
                            "p_pass": j.get("p_pass"),
                        }
                        for j in row.get("judgements") or []
                        if isinstance(j, dict)
                    ],
                    "agreement": agreement(row.get("judgements")),
                }
            )
    answers.sort(key=lambda a: (str(a["generator"]), str(a["at"] or "")))
    return {"question": head, "answers": answers, "dimensions": list(eval_core.DIMENSIONS)}


def compare(run: Run, rows: list[Row]) -> dict[str, Any]:
    """One run as matrices: model x judge pass rates, judge leniency, judge-pair agreement, scores per dimension.

    This is the Evals "Compare" view: it shows whether a model is weak or a judge is just strict.
    """
    generators = sorted({str(r.get("generator")) for r in rows if r.get("generator")})
    judges = sorted({str(j.get("judge")) for r in rows for j in _ok(r.get("judgements"))})
    cells, leniency, dim_scores, errors = _tally_verdicts(rows, generators, judges)
    return {
        "run": {
            "id": run.get("id"),
            "name": run.get("name"),
            "at": run.get("finished_at") or run.get("created_at"),
            "status": run.get("status"),
        },
        "generators": generators,
        "judges": judges,
        "matrix": {
            g: {j: {"n": c["n"], "pass_rate": round(c["passes"] / c["n"], 3)} for j, c in cells[g].items()}
            for g in generators
        },
        "leniency": {j: {"n": len(v), "pass_rate": _avg(v)} for j, v in leniency.items()},
        "errors_skipped": dict(sorted(errors.items())),
        "pairs": [_judge_pair(rows, a, b) for a, b in combinations(judges, 2)],
        "dimensions": list(eval_core.DIMENSIONS),
        "scores": {
            g: {d: {"n": len(v), "mean": _avg(v, 2)} for d, v in dims.items()} for g, dims in dim_scores.items()
        },
    }


def _tally_verdicts(rows: list[Row], generators: list[str], judges: list[str]) -> tuple[
    dict[str, dict[str, dict[str, Any]]], dict[str, list[float]], dict[str, dict[str, list[float]]], dict[str, int]
]:
    """Count every judgement once: passes per model x judge, each judge's verdicts, scores, and failed calls."""
    cells: dict[str, dict[str, dict[str, Any]]] = {g: {} for g in generators}
    leniency: dict[str, list[float]] = {j: [] for j in judges}
    dim_scores: dict[str, dict[str, list[float]]] = {g: {} for g in generators}
    errors: dict[str, int] = {}
    for r in rows:
        g = str(r.get("generator"))
        for j in _errors(r.get("judgements")):
            errors[str(j.get("judge"))] = errors.get(str(j.get("judge")), 0) + 1
        for j in _ok(r.get("judgements")):
            name = str(j["judge"])
            c = cells[g].setdefault(name, {"n": 0, "passes": 0})
            c["n"] += 1
            c["passes"] += 1 if j["verdict"] == "pass" else 0
            leniency[name].append(1.0 if j["verdict"] == "pass" else 0.0)
            for dim, val in (j.get("scores") or {}).items():
                if val is not None and dim in eval_core.DIMENSIONS:
                    dim_scores[g].setdefault(dim, []).append(float(val))
    return cells, leniency, dim_scores, errors


def _judge_pair(rows: list[Row], a: str, b: str) -> dict[str, Any]:
    """How often judges `a` and `b` agree on the answers both judged, and their mean score gap."""
    same, gaps, n = [], [], 0
    for r in rows:
        ja = next((j for j in _ok(r.get("judgements")) if j["judge"] == a), None)
        jb = next((j for j in _ok(r.get("judgements")) if j["judge"] == b), None)
        if not ja or not jb:
            continue
        n += 1
        same.append(1.0 if ja["verdict"] == jb["verdict"] else 0.0)
        for dim in eval_core.DIMENSIONS:
            va, vb = (ja.get("scores") or {}).get(dim), (jb.get("scores") or {}).get(dim)
            if va is not None and vb is not None:
                gaps.append(abs(float(va) - float(vb)))
    return {"a": a, "b": b, "n": n, "verdict_agreement": _avg(same), "mean_score_gap": _avg(gaps, 2)}
