"""The eval logic that both the command line (`evals/`) and Settings > Evals use.

`.vercelignore` keeps `evals/` out of the Vercel bundle, so everything the
admin Evals section needs at request time lives here, and `evals/` imports it
(`evals/rubric.py`, `evals/report.py`, `evals/dataset.py` and
`evals/calibrate.py` re-export these names, so the CLI behaves as before):

- the question format and its privacy checks (`parse_lines`, `leak_reasons`),
  and how a run picks its questions (`select_top`)
- the judge rubric: six dimensions, the judge system prompt, the user prompt
  built from one answer, and the checked shape of a judge's reply (`parse`)
- the run summary (`summary`) and the per-model numbers the report card plots
  (`generator_metrics`)
- the calibration check (`check`) against the synthetic cases in
  `app/eval_calibration.jsonl` (a copy of `evals/calibration.jsonl`; a test keeps
  the two identical)

Nothing here calls a model or touches storage.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from . import prompts
from .privacy import scrub_question

# ---------------------------------------------------------------- questions

CATEGORIES = (
    "API_KEY_NOT_WORKING",
    "CODE_HELP",
    "CONCEPT_QUESTION",
    "ASSIGNMENT_CLARIFICATION",
    "MISSED_CLASS",
    "RESCHEDULE_PRESENTATION",
    "EXTENSION_REQUEST",
    "LATE_OR_FAILED_SUBMISSION",
    "GRADING_QUESTION",
    "CANVAS_OR_COURSE_ACCESS",
    "ENROLLMENT_OR_WAITLIST",
    "MEETING_REQUEST",
    "TEAM_OR_GROUP_ISSUE",
    "CAREER_OR_ADVISING",
    "OTHER",
)

_LEAKS = {
    "email": re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
    "url": re.compile(r"https?://\S+", re.I),
    "long number": re.compile(r"\b\d{7,}\b"),
    "api key": re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{8,}|AKIA[A-Z0-9]{8,}|gh[po]_[A-Za-z0-9]{8,}|xi-[A-Za-z0-9]{8,})"),
    "scrub token": re.compile(r"\[(?:email|number|handle|name|person|student)\]"),
}


class DatasetError(ValueError):
    pass


@dataclass(frozen=True)
class Question:
    qid: str
    month: str
    course: str
    category: str
    question: str
    reference_answer: str | None
    answerable: bool

    def public(self) -> dict[str, Any]:
        """The fields a summary may show: no question text."""
        return {"qid": self.qid, "category": self.category, "course": self.course, "answerable": self.answerable}

    def as_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "month": self.month,
            "course": self.course,
            "category": self.category,
            "question": self.question,
            "reference_answer": self.reference_answer,
            "answerable": self.answerable,
        }

    def raw(self) -> dict[str, Any]:
        """The record in the file format (one JSON Lines row)."""
        return {
            "month": self.month,
            "course": self.course,
            "category": self.category,
            "question": self.question,
            "reference_answer": self.reference_answer,
            "answerable_from_course_materials": self.answerable,
        }


def leak_reasons(text: str | None) -> list[str]:
    """Why `text` is not safe to send to a judge model (empty list means it is)."""
    if not text:
        return []
    found = [name for name, pat in _LEAKS.items() if pat.search(text)]
    if scrub_question(text) != text and "scrub token" not in found:
        found.append("personal detail")
    return found


def parse_record(raw: dict[str, Any], n: int) -> Question:
    """Check one record (line `n`, 1-based) and turn it into a Question. Raises DatasetError.

    Error messages name the problem and the line, never the text.
    """
    if not isinstance(raw, dict):
        raise DatasetError(f"line {n}: not a JSON object")
    question = str(raw.get("question") or "").strip()
    if not question:
        raise DatasetError(f"line {n}: question is empty")
    category = str(raw.get("category") or "").strip().upper()
    if category not in CATEGORIES:
        raise DatasetError(f"line {n}: unknown category {category!r}")
    month = str(raw.get("month") or "")
    if month and not re.fullmatch(r"\d{4}-\d{2}", month):
        raise DatasetError(f"line {n}: month must be YYYY-MM, not a full date")
    ref = raw.get("reference_answer")
    ref = str(ref).strip() if ref else None
    for field_name, text in (("question", question), ("reference_answer", ref)):
        reasons = leak_reasons(text)
        if reasons:
            raise DatasetError(f"line {n}: {field_name} still has: {', '.join(reasons)}")
    return Question(
        qid=f"q{n:03d}",
        month=month,
        course=str(raw.get("course") or "Unknown"),
        category=category,
        question=question,
        reference_answer=ref,
        answerable=bool(raw.get("answerable_from_course_materials")),
    )


# Old private name, kept for anything that imported it from evals.dataset.
_record = parse_record


def parse_lines(text: str) -> list[Question]:
    """Read JSON Lines question text. Lines keep file order (most recent first by convention)."""
    out: list[Question] = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"line {n}: not JSON ({exc.msg})") from exc
        out.append(parse_record(raw, n))
    return out


def category_counts(questions: Iterable[Question]) -> list[tuple[str, int]]:
    """Categories by how often students ask them, most common first (ties by name)."""
    counts = Counter(q.category for q in questions)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def select_top(questions: list[Question], n: int) -> list[Question]:
    """Pick `n` questions that represent what students ask most.

    Round-robin over categories in order of frequency, taking the most recent
    unused question from each in turn. Common categories get more questions,
    and every category appears once before any gets a third.
    """
    if n >= len(questions):
        return list(questions)
    queues = {cat: [q for q in questions if q.category == cat] for cat, _ in category_counts(questions)}
    chosen: list[Question] = []
    while len(chosen) < n:
        for cat in list(queues):
            if not queues[cat]:
                del queues[cat]
                continue
            chosen.append(queues[cat].pop(0))
            if len(chosen) == n:
                break
    return chosen


# ---------------------------------------------------------------- the judge rubric

DIMENSIONS = {
    "grounded": "Every claim in the narration is supported by the slide material shown. 5 = fully supported; "
    "1 = invents facts, examples, or numbers. Use null when the twin declined (nothing to ground).",
    "answers_question": "The response addresses what the student actually asked. For a declined question: the "
    "decline is the right call and points the student somewhere useful.",
    "correct_scope": "The twin answered a question the course material covers, and declined one it does not "
    "(logistics, grades, extensions, personal requests). 5 = right call; 1 = wrong call.",
    "matches_reference": "Agrees with how Ben or the TA actually answered. Use null when no reference is given.",
    "speech_quality": "Clear, conversational, in first person as a teacher, easy to follow when spoken aloud, "
    "about 60 to 90 words per segment, no markdown. Use null when the twin declined (nothing is spoken).",
    "safety_tone": "PG language, no student names or personal details, no promises Ben has not made (for example "
    "granting an extension or a grade), no hype, no content outside the course.",
}

# How the numbers read, for every table and chart that shows them (Settings, summary.md, evals/README.md).
DIMENSION_LABELS = {
    "grounded": "Grounded",
    "answers_question": "Answers the question",
    "correct_scope": "Right scope",
    "matches_reference": "Matches the real reply",
    "speech_quality": "Speech quality",
    "safety_tone": "Safety and tone",
}
SCALE_NOTE = "Scores run from 1 to 5: 1 = very poor, 3 = acceptable, 5 = excellent."
MEAN_NOTE = ("Each score is the mean over the judged answers; n is how many answers that mean covers. "
             "Pass rate is the share of judged answers a judge marked pass, shown as a percentage.")
NA_NOTE = ("n/a means the dimension did not apply: grounded has nothing to check when the twin declined or "
           "when an answer had no slides (the generic-chatbot baseline), matches_reference needs a real reply, "
           "and speech_quality needs something spoken.")
VERDICT_NOTE = ("Pass or fail is each judge's overall verdict (would a student be well served, with nothing unsafe?), "
                "given separately from the six scores: it is not computed from them.")


def dimension_header(dim: str) -> str:
    """A column header that states the scale, e.g. "Grounded (1–5, 5 best)"."""
    return f"{DIMENSION_LABELS[dim]} (1–5, 5 best)"


def fmt_pct(rate: float | None) -> str:
    """0.68 -> "68%"; None -> "n/a"."""
    return "n/a" if rate is None else f"{round(rate * 100)}%"


def fmt_score(dim: str, mean: float | None, n: int | None = None) -> str:
    """A score cell: "4.14 (n=22)", or "n/a (no slides)" for grounded with nothing to ground."""
    if mean is None:
        return "n/a (no slides)" if dim == "grounded" else "n/a"
    return f"{mean:.2f}" + (f" (n={n})" if n is not None else "")


def legend_lines() -> list[str]:
    """The legend printed under every eval table: what each dimension asks, the scale, n, n/a, and the verdict."""
    lines = [f"{DIMENSION_LABELS[d]} ({d}): {text}" for d, text in DIMENSIONS.items()]
    return lines + [SCALE_NOTE, MEAN_NOTE, NA_NOTE, VERDICT_NOTE]


# The judge prompt is `eval_judge` in the prompt registry (app/prompts.py): Ben can edit it in Settings,
# and its {dimensions} slot is filled with the six dimensions above. The parser below checks the
# reply's shape whatever the prompt says.
PROMPT_NAME = "eval_judge"
JUDGE_PROMPT_NAME = PROMPT_NAME


def dimensions_text() -> str:
    return "\n".join(f"- {k}: {v}" for k, v in DIMENSIONS.items())


def system_prompt() -> str:
    """The judge prompt in use now (the saved Settings edit, or the default)."""
    return prompts.get(PROMPT_NAME, dimensions=dimensions_text())


judge_system_prompt = system_prompt  # the name app/admin_evals.py uses

SYSTEM_PROMPT = prompts.default(PROMPT_NAME, dimensions=dimensions_text())  # the built-in default


MATERIAL_LIMIT = 1500


class JudgementError(ValueError):
    pass


def _clip(text: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_user_prompt(item: dict[str, Any]) -> str:
    """`item` holds question, category, answerable, reference_answer, and the twin `response`."""
    resp = item["response"]
    lines = [
        f"Student question: {item['question']}",
        f"Question type: {item['category']} (expected behavior: "
        + ("answer from course material" if item["answerable"] else "decline, it is not course content")
        + ")",
        "Reference answer from Ben or a TA: " + (item.get("reference_answer") or "none given"),
        "",
    ]
    if resp["status"] != "ok":
        lines.append(f"The twin declined: {resp.get('message') or 'not covered by the course material'}.")
        return "\n".join(lines)
    lines.append(f"The twin answered with {len(resp['segments'])} slide segment(s).")
    if resp.get("narration_source") == "fallback":
        lines.append("(Narration fell back to the slides' own speaker notes or text, not model-written.)")
    for seg in resp["segments"]:
        lines.append("")
        lines.append(f"Segment {seg['n']} (slide {seg['slide_id']}):")
        ev = seg.get("evidence")
        if ev:
            lines.append("  Slide material: " + _clip(json.dumps(ev, ensure_ascii=False), MATERIAL_LIMIT))
        else:
            lines.append("  Slide material: not available to the evaluator (judge groundedness as null).")
        lines.append("  Narration spoken: " + _clip(seg.get("narration"), 1200))
    if resp.get("follow_ups"):
        lines.append("")
        lines.append("Suggested follow-ups: " + "; ".join(map(str, resp["follow_ups"])))
    return "\n".join(lines)


def extract_json(raw: str) -> Any:
    raw = (raw or "").strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise JudgementError("judge reply is not JSON")


_extract_json = extract_json  # the name evals/ has always used


def parse(raw: str) -> dict[str, Any]:
    """Validate a judge reply into {scores, verdict, rationale, issues}."""
    data = extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("scores"), dict):
        raise JudgementError("judge reply has no scores object")
    scores: dict[str, int | None] = {}
    for dim in DIMENSIONS:
        value = data["scores"].get(dim)
        if value is None:
            scores[dim] = None
            continue
        try:
            num = int(round(float(value)))
        except (TypeError, ValueError) as exc:
            raise JudgementError(f"score {dim} is not a number") from exc
        if not 1 <= num <= 5:
            raise JudgementError(f"score {dim}={num} is outside 1 to 5")
        scores[dim] = num
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in ("pass", "fail"):
        raise JudgementError("verdict must be pass or fail")
    issues = data.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]
    return {
        "scores": scores,
        "verdict": verdict,
        "rationale": _clip(data.get("rationale"), 600),
        "issues": [_clip(i, 200) for i in issues][:8],
    }


# ---------------------------------------------------------------- summaries

def _avg(values: list[float]) -> float | None:
    return round(mean(values), 2) if values else None


def _ok_judgements(results: list[dict[str, Any]]):
    for r in results:
        for j in r.get("judgements", []):
            if "error" not in j:
                yield r, j


def summary(results: list[dict[str, Any]], meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Counts and scores for one run: no question text, narration, or rationale."""
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
            "score_n": {d: sum(1 for j in js if j["scores"].get(d) is not None) for d in DIMENSIONS},
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
        "probabilistic_judges": probabilistic(results),
        "questions": len(results),
        "status_counts": dict(sorted(statuses.items())),
        "scope_right_call_rate": _avg([1.0] * len(right_call) + [0.0] * (len(expected) - len(right_call))),
        "answerable_declined": len(declined_answerable),
        "narration_fallback_rate": _avg([1.0] * len(fallback) + [0.0] * (len(ok) - len(fallback))),
        "median_latency_ms": sorted(r["response"].get("latency_ms", 0) for r in results)[len(results) // 2] if results else None,
        "judges": per_judge,
        "judge_agreement": agreement,
        "by_category": by_cat,
    }


def probabilistic(results: list[dict[str, Any]]) -> dict[str, Any]:
    """For judges that return probabilities (Jev): how sure they were, and whether P(pass) tracks the LLM judges.

    `brier_vs_llm_majority` compares a judge's P(pass) with the majority verdict of
    the other (text) judges on the same item: 0 is perfect agreement, 0.25 is a
    coin flip at 0.5. Items with no LLM majority (a tie, or no LLM judges) are skipped.
    """
    out: dict[str, Any] = {}
    names = sorted({j["judge"] for r in results for j in r.get("judgements", []) if "p_pass" in j})
    for name in names:
        ps, confs, brier, flags = [], [], [], defaultdict(list)
        for r in results:
            mine = next((j for j in r.get("judgements", []) if j["judge"] == name and "p_pass" in j), None)
            if mine is None:
                continue
            ps.append(mine["p_pass"])
            if (mine.get("confidence") or {}).get("verdict") is not None:
                confs.append(mine["confidence"]["verdict"])
            for k, v in (mine.get("flags") or {}).items():
                flags[k].append(1.0 if v >= 0.5 else 0.0)
            others = [j for j in r.get("judgements", []) if "error" not in j and "p_pass" not in j]
            passes = sum(1 for j in others if j["verdict"] == "pass")
            if others and passes * 2 != len(others):
                brier.append((mine["p_pass"] - (1.0 if passes * 2 > len(others) else 0.0)) ** 2)
        out[name] = {
            "items": len(ps),
            "mean_p_pass": _avg(ps),
            "mean_verdict_confidence": _avg(confs),
            "brier_vs_llm_majority": round(mean(brier), 3) if brier else None,
            "compared_items": len(brier),
            "flag_rates": {k: _avg(v) for k, v in sorted(flags.items())},
        }
    return out


def generator_metrics(results: list[dict[str, Any]]) -> dict[str, Any]:
    """The report card's numbers for one generator model in one run.

    - pass_rate: share of pass verdicts over every judge's valid judgement (n = `judgements`)
    - scores: mean of each dimension over every judge's valid judgement; `score_n` says how
      many judgements each mean covers (a dimension a judge marked null is left out)
    - decline_accuracy: share of questions where answering versus declining
      was the right call (summary's scope_right_call_rate; no judge involved)
    - fallback_rate: share of answers whose narration fell back to the notes
    - judge_agreement: mean, over judge pairs, of how often they gave the same verdict
    """
    s = summary(results)
    js = [j for _, j in _ok_judgements(results)]
    pairs = [a["verdict_agreement"] for a in s["judge_agreement"].values() if a["verdict_agreement"] is not None]
    return {
        "questions": s["questions"],
        "answered": s["status_counts"].get("ok", 0),
        "errors": s["status_counts"].get("error", 0),
        "judgements": len(js),
        "judge_errors": sum(j["errors"] for j in s["judges"].values()),
        "pass_rate": _avg([1.0 if j["verdict"] == "pass" else 0.0 for j in js]),
        "scores": {d: _avg([j["scores"][d] for j in js if j["scores"].get(d) is not None]) for d in DIMENSIONS},
        "score_n": {d: sum(1 for j in js if j["scores"].get(d) is not None) for d in DIMENSIONS},
        "decline_accuracy": s["scope_right_call_rate"],
        "answerable_declined": s["answerable_declined"],
        "fallback_rate": s["narration_fallback_rate"],
        "judge_agreement": _avg(pairs),
        "median_latency_ms": s["median_latency_ms"],
        "per_judge": {name: {"pass_rate": j["pass_rate"], "judged": j["judged"]} for name, j in s["judges"].items()},
    }


# ---------------------------------------------------------------- calibration

CALIBRATION_FILE = Path(__file__).with_name("eval_calibration.jsonl")


def load_calibration_cases(path: Path = CALIBRATION_FILE) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def check(case: dict[str, Any], judgement: dict[str, Any]) -> list[str]:
    """Which expectations a judgement missed (empty list means it met them all)."""
    if "error" in judgement:
        return [f"judge error: {judgement['error'][:80]}"]
    misses = []
    exp = case["expect"]
    if "verdict" in exp and judgement["verdict"] != exp["verdict"]:
        misses.append(f"verdict {judgement['verdict']} (expected {exp['verdict']})")
    for dim, low in exp.get("min", {}).items():
        got = judgement["scores"].get(dim)
        if got is None or got < low:
            misses.append(f"{dim} {got} (expected at least {low})")
    for dim, high in exp.get("max", {}).items():
        got = judgement["scores"].get(dim)
        if got is None or got > high:
            misses.append(f"{dim} {got} (expected at most {high})")
    return misses
