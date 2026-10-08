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


# Where an answer came from (docs/SPEC.md, Block 8c): answer()'s kind, grouped into routes.
ROUTES = ("slides", "course_info", "faq", "logistics", "web", "declined")
ROUTE_OF_KIND = {
    "course_content": "slides",
    "stored_topic": "slides",
    "course_info": "course_info",
    "faq": "faq",
    "logistics": "logistics",
    "web": "web",
    "not_covered": "declined",
}
QUESTION_TYPES = ("concept", "beyond", "off_topic", "logistics")
SLIDE_ID_RE = re.compile(r"^\d{5}-s\d{2}-\d{3}$")


@dataclass(frozen=True)
class Question:
    qid: str
    month: str
    course: str
    category: str
    question: str
    reference_answer: str | None
    answerable: bool
    # Optional (Block 8c): what a good answer looks like, for route and retrieval metrics.
    qtype: str | None = None
    expected_kind: tuple[str, ...] = ()
    expected_slides: tuple[str, ...] = ()
    must_include: tuple[str, ...] = ()
    must_not: tuple[str, ...] = ()

    def public(self) -> dict[str, Any]:
        """The fields a summary may show: no question text."""
        return {"qid": self.qid, "category": self.category, "course": self.course, "answerable": self.answerable,
                "type": self.qtype}

    def expectations(self) -> dict[str, Any]:
        """The optional fields, only those that are set."""
        out: dict[str, Any] = {}
        if self.qtype:
            out["type"] = self.qtype
        if self.expected_kind:
            out["expected_kind"] = list(self.expected_kind)
        if self.expected_slides:
            out["expected_slides"] = list(self.expected_slides)
        if self.must_include:
            out["must_include"] = list(self.must_include)
        if self.must_not:
            out["must_not"] = list(self.must_not)
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "qid": self.qid,
            "month": self.month,
            "course": self.course,
            "category": self.category,
            "question": self.question,
            "reference_answer": self.reference_answer,
            "answerable": self.answerable,
            **self.expectations(),
        }

    def raw(self) -> dict[str, Any]:
        """The record in the file format (one JSON Lines row)."""
        out = {
            "month": self.month,
            "course": self.course,
            "category": self.category,
            "question": self.question,
            "reference_answer": self.reference_answer,
            "answerable_from_course_materials": self.answerable,
        }
        exp = self.expectations()
        if "type" in exp:
            out["type"] = exp.pop("type")
        if "expected_kind" in exp and len(exp["expected_kind"]) == 1:
            exp["expected_kind"] = exp["expected_kind"][0]
        return {**out, **exp}


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
    qtype = raw.get("type")
    if qtype is not None and qtype not in QUESTION_TYPES:
        raise DatasetError(f"line {n}: type must be one of {', '.join(QUESTION_TYPES)}")
    kinds = raw.get("expected_kind")
    kinds = [kinds] if isinstance(kinds, str) else list(kinds or [])
    bad = [k for k in kinds if k not in ROUTES]
    if bad:
        raise DatasetError(f"line {n}: expected_kind must be from {', '.join(ROUTES)}")
    slides = list(raw.get("expected_slides") or [])
    if any(not isinstance(s, str) or not SLIDE_ID_RE.match(s) for s in slides):
        raise DatasetError(f"line {n}: expected_slides must be slide ids like 70445-s06-014")
    lists = {}
    for field_name in ("must_include", "must_not"):
        items = raw.get(field_name) or []
        if not isinstance(items, list) or any(not isinstance(i, str) for i in items):
            raise DatasetError(f"line {n}: {field_name} must be a list of strings")
        for item in items:
            if leak_reasons(item):
                raise DatasetError(f"line {n}: {field_name} still has: {', '.join(leak_reasons(item))}")
        lists[field_name] = tuple(i.strip() for i in items if i.strip())
    return Question(
        qid=f"q{n:03d}",
        month=month,
        course=str(raw.get("course") or "Unknown"),
        category=category,
        question=question,
        reference_answer=ref,
        answerable=bool(raw.get("answerable_from_course_materials")),
        qtype=qtype,
        expected_kind=tuple(kinds),
        expected_slides=tuple(slides),
        must_include=lists["must_include"],
        must_not=lists["must_not"],
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
    # Teaching quality (Block 8c). Null when the twin declined or referred the student (nothing was taught).
    "good_teaching": "This was good teaching: it builds understanding, not just facts. 1 = a list of facts or "
    "jargon with no explanation of why or how; 3 = explains the idea but the student would struggle to apply it; "
    "5 = the student comes away understanding why it works and when to use it. Null when nothing was taught.",
    "explains_concept_effectively": "A strong and effective way to communicate the concept: clear intuition, a "
    "concrete example or analogy, and it goes from simple to complex. 1 = abstract and confusing, no example; "
    "3 = clear but generic, or an example that does not quite fit; 5 = a vivid intuition and a concrete example "
    "that make the idea click, built up step by step. Null when nothing was taught.",
    "accurate": "Technically correct. 1 = a material error a student would learn wrong; 3 = mostly right with an "
    "imprecise or oversimplified claim; 5 = everything stated is correct and precise. Null when nothing was taught.",
    "engaging_voice": "Sounds like a professor talking to a student, not a textbook. 1 = dry, impersonal "
    "textbook prose or a bulleted list; 3 = conversational in places but stiff; 5 = warm, direct, first person, "
    "like Ben explaining it in office hours. Null when nothing was taught.",
    "appropriate_depth": "The right level for an MBA or business-analytics student. 1 = far too shallow to be "
    "useful, or buried in math and code the student did not ask for; 3 = roughly right but uneven; 5 = pitched "
    "exactly right: business meaning first, enough technical detail to be correct. Null when nothing was taught.",
    # Web answers only (the "beyond the slides" path). Null for every other answer.
    "cites_sources": "Web answers only: cites 2 to 4 relevant sources the student can open. 1 = no sources or "
    "irrelevant ones; 3 = one source, or sources that only loosely support it; 5 = 2 to 4 relevant sources. "
    "Null unless the twin answered from the web.",
    "labeled_beyond_slides": "Web answers only: clearly labeled as beyond Ben's slides, not presented as course "
    "material or spoken in Ben's cloned voice. 1 = presented as course material; 5 = clearly labeled. "
    "Null unless the twin answered from the web.",
}

# The dimensions in groups, for reports: the six core ones first, teaching quality as its own group.
DIMENSION_GROUPS = {
    "core": ("grounded", "answers_question", "correct_scope", "matches_reference", "speech_quality", "safety_tone"),
    "teaching": ("good_teaching", "explains_concept_effectively", "accurate", "engaging_voice", "appropriate_depth"),
    "web": ("cites_sources", "labeled_beyond_slides"),
}
GROUP_LABELS = {"core": "Core rubric", "teaching": "Teaching quality", "web": "Web answers"}
CORE_DIMENSIONS = DIMENSION_GROUPS["core"]
TEACHING_DIMENSIONS = DIMENSION_GROUPS["teaching"]
WEB_DIMENSIONS = DIMENSION_GROUPS["web"]

# How the numbers read, for every table and chart that shows them (Settings, summary.md, evals/README.md).
DIMENSION_LABELS = {
    "grounded": "Grounded",
    "answers_question": "Answers the question",
    "correct_scope": "Right scope",
    "matches_reference": "Matches the real reply",
    "speech_quality": "Speech quality",
    "safety_tone": "Safety and tone",
    "good_teaching": "Good teaching",
    "explains_concept_effectively": "Explains the concept effectively",
    "accurate": "Accurate",
    "engaging_voice": "Engaging voice",
    "appropriate_depth": "Appropriate depth",
    "cites_sources": "Cites sources",
    "labeled_beyond_slides": "Labeled beyond the slides",
}
SCALE_NOTE = "Scores run from 1 to 5: 1 = very poor, 3 = acceptable, 5 = excellent."
MEAN_NOTE = ("Each score is the mean over the judged answers; n is how many answers that mean covers. "
             "Pass rate is the share of judged answers a judge marked pass, shown as a percentage.")
NA_NOTE = ("n/a means the dimension did not apply: grounded has nothing to check when the twin declined or "
           "when an answer had no slides (the generic-chatbot baseline), matches_reference needs a real reply, "
           "speech_quality needs something spoken, the five teaching dimensions need something taught, and "
           "cites_sources and labeled_beyond_slides apply to web answers only.")
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
    """Every scored dimension, in its group, with what 1, 3 and 5 mean where the rubric anchors them."""
    lines = []
    for group, dims in DIMENSION_GROUPS.items():
        lines.append(f"{GROUP_LABELS[group]}:")
        lines += [f"- {k}: {DIMENSIONS[k]}" for k in dims]
    return "\n".join(lines)


def system_prompt() -> str:
    """The judge prompt in use now (the saved Settings edit, or the default)."""
    return prompts.get(PROMPT_NAME, dimensions=dimensions_text())


judge_system_prompt = system_prompt  # the name app/admin_evals.py uses

SYSTEM_PROMPT = prompts.default(PROMPT_NAME, dimensions=dimensions_text())  # the built-in default


MATERIAL_LIMIT = 1500

# What the judge is told a good answer does, per expected route (Block 8c).
EXPECTED_BEHAVIOR = {
    "slides": "answer from the course slides",
    "course_info": "answer from the course information on Canvas",
    "faq": "give Ben's written course FAQ answer",
    "logistics": "refer the student to Ben or the TA, promising nothing",
    "web": "a short answer from the web, labeled as beyond the slides, citing 2 to 4 sources",
    "declined": "decline, it is not course content",
}


class JudgementError(ValueError):
    pass


def _clip(text: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_user_prompt(item: dict[str, Any]) -> str:
    """`item` holds question, category, answerable, reference_answer, and the twin `response`."""
    resp = item["response"]
    kinds = [k for k in (item.get("expected_kind") or []) if k in EXPECTED_BEHAVIOR]
    if kinds:
        expected = " or ".join(EXPECTED_BEHAVIOR[k] for k in kinds)
    else:
        expected = "answer from course material" if item["answerable"] else "decline, it is not course content"
    lines = [
        f"Student question: {item['question']}",
        f"Question type: {item['category']} (expected behavior: {expected})",
        "Reference answer from Ben or a TA: " + (item.get("reference_answer") or "none given"),
    ]
    if item.get("must_include"):
        lines.append("A good answer must include: " + "; ".join(map(str, item["must_include"])))
    if item.get("must_not"):
        lines.append("A good answer must not: " + "; ".join(map(str, item["must_not"])))
    lines.append("")
    if resp.get("outcome") == "web" and resp["status"] == "ok":
        lines.append("The twin answered from the web, beyond its slides (text only, not in Ben's voice).")
        for seg in resp.get("segments") or []:
            lines.append("  Answer shown: " + _clip(seg.get("narration"), 1500))
        links = resp.get("links") or []
        lines.append("  Sources listed: " + ("; ".join(_clip(f"{l.get('title') or ''} {l.get('url') or ''}", 200)
                                                  for l in links if isinstance(l, dict)) or "none"))
        if resp.get("label"):
            lines.append("  Label shown: " + _clip(resp["label"], 200))
        return "\n".join(lines)
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
        mine_err = [j for r in results for j in r.get("judgements", []) if j["judge"] == name and "error" in j]
        errors = sum(1 for j in mine_err if not j.get(PROVIDER_ERROR))  # a refused call is not a judge error
        per_judge[name] = {
            "judged": len(js),
            "errors": errors,
            "provider_errors": len(mine_err) - errors,
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


# ---------------------------------------------------------------- provider outages

# A provider refusing calls for billing reasons (no credit, quota used up). Seen on Oct 8: Anthropic returned
# 400 "Your credit balance is too low to access the Anthropic API". OpenAI sends 429 "insufficient_quota";
# OpenRouter sends 402. Such an answer or judgement says nothing about the model, so it is marked
# `provider_error`, left out of every score, and asked again later.
PROVIDER_ERROR = "provider_error"
_BILLING = re.compile(
    r"credit balance is too low|insufficient[_ ]quota|exceeded your current quota|insufficient credits"
    r"|requires more credits|payment required|billing (?:hard )?limit|returned 402\b|\b402 payment",
    re.I,
)


def is_billing_error(text: Any) -> bool:
    """Whether a provider error message means the account cannot pay (credit, quota or billing)."""
    return bool(text) and bool(_BILLING.search(str(text)))


def is_provider_error(row_or_judgement: dict[str, Any]) -> bool:
    if row_or_judgement.get(PROVIDER_ERROR):
        return True
    resp = row_or_judgement.get("response")
    return isinstance(resp, dict) and resp.get("status") == PROVIDER_ERROR


# ---------------------------------------------------------------- measured without a judge (Block 8c)

def model_family(name: str | None) -> str:
    """claude / gpt / gemini / other, from a "provider:model" key (OpenRouter ids included)."""
    text = str(name or "").lower()
    if "claude" in text or text.startswith("anthropic:"):
        return "claude"
    if "gpt" in text or text.startswith("openai:") or "/o1" in text or "/o3" in text:
        return "gpt"
    if "gemini" in text or "gemma" in text or "google/" in text:
        return "gemini"
    return text.split(":", 1)[0] or "other"


def response_slides(resp: dict[str, Any]) -> list[str]:
    """The course slide ids an answer showed, in order (not FAQ, Canvas or baseline segments)."""
    return [str(s.get("slide_id")) for s in resp.get("segments") or []
            if SLIDE_ID_RE.match(str(s.get("slide_id") or ""))]


def route_of(resp: dict[str, Any]) -> str | None:
    """Which route answered: from answer()'s kind when recorded, else from the response's shape."""
    outcome = resp.get("outcome")
    if outcome in ROUTE_OF_KIND:
        return ROUTE_OF_KIND[outcome]
    if resp.get("status") == "ok":
        return "slides" if response_slides(resp) else None
    if resp.get("status") == "not_covered":
        return "declined"
    return None


def expected_routes(row: dict[str, Any], web_path: bool = True) -> list[str]:
    """The routes a good answer may take. Without `expected_kind`: answerable questions expect slides or
    Canvas, the rest anything but slides. While the app has no web path, `web` questions expect a decline."""
    kinds = list(row.get("expected_kind") or [])
    if not kinds:
        kinds = ["slides", "course_info"] if row.get("answerable") else ["course_info", "faq", "logistics", "web",
                                                                         "declined"]
    if not web_path:
        kinds = ["declined" if k == "web" else k for k in kinds]
    return list(dict.fromkeys(kinds))


def answer_metrics(row: dict[str, Any], web_path: bool = True) -> dict[str, Any]:
    """Route, retrieval, latency and cost for one answer. None means "does not apply"."""
    resp = row.get("response") or {}
    route = route_of(resp)
    exp = expected_routes(row, web_path)
    slides = response_slides(resp)
    expected = set(row.get("expected_slides") or [])
    wants_slides = bool(expected) and "slides" in exp
    usage_ = resp.get("usage") or {}
    return {
        "route": route,
        "expected_routes": exp,
        "route_ok": None if route is None else route in exp,
        # Hit: any expected slide among the answer's slides (a decline of a concept question is a miss).
        "hit": (bool(expected & set(slides)) if wants_slides and route is not None else None),
        "precision": (sum(1 for s in slides if s in expected) / len(slides) if wants_slides and slides else None),
        "pure": (len({s[:5] for s in slides}) == 1 if slides else None),
        "latency_ms": resp.get("latency_ms") if resp.get("status") in ("ok", "not_covered") else None,
        "cost_usd": usage_.get("cost_usd"),
        "tokens_in": usage_.get("tokens_in"),
        "tokens_out": usage_.get("tokens_out"),
        "fallback": (resp.get("narration_source") == "fallback") if route == "slides" else None,
    }


def _rate(flags: list[Any]) -> tuple[float | None, int]:
    vals = [1.0 if f else 0.0 for f in flags if f is not None]
    return _avg(vals), len(vals)


def deterministic_metrics(results: list[dict[str, Any]], web_path: bool = True) -> dict[str, Any]:
    """Judge-free numbers over a set of answers, each with its n."""
    ms = [answer_metrics(r, web_path) for r in results]
    route_acc, route_n = _rate([m["route_ok"] for m in ms])
    hit, hit_n = _rate([m["hit"] for m in ms])
    purity, purity_n = _rate([m["pure"] for m in ms])
    precisions = [m["precision"] for m in ms if m["precision"] is not None]
    lat = sorted(m["latency_ms"] for m in ms if isinstance(m["latency_ms"], (int, float)))
    costs = [m["cost_usd"] for m in ms if isinstance(m["cost_usd"], (int, float))]
    fallback, fallback_n = _rate([m["fallback"] for m in ms])
    routes = Counter(m["route"] or "error" for m in ms)
    return {
        "route_accuracy": route_acc, "route_n": route_n,
        "retrieval_hit_rate": hit, "hit_n": hit_n,
        "slide_precision": _avg(precisions), "precision_n": len(precisions),
        "course_purity": purity, "purity_n": purity_n,
        "mean_latency_ms": round(mean(lat)) if lat else None,
        "median_latency_ms": lat[len(lat) // 2] if lat else None,
        "p90_latency_ms": lat[min(len(lat) - 1, int(0.9 * len(lat)))] if lat else None,
        "latency_n": len(lat),
        "cost_per_answer": round(mean(costs), 5) if costs else None,
        "total_cost": round(sum(costs), 4) if costs else None,
        "cost_n": len(costs),
        "narration_fallback_rate": fallback, "fallback_n": fallback_n,
        "routes": dict(sorted(routes.items())),
    }


def pass_rate_without_same_family(results: list[dict[str, Any]]) -> tuple[float | None, int]:
    """Pass rate over judgements whose judge is not from the answering model's family (needs row["generator"])."""
    verdicts = []
    for r in results:
        fam = model_family(r.get("generator"))
        for j in r.get("judgements", []):
            if "error" in j or model_family(j.get("judge")) == fam:
                continue
            verdicts.append(1.0 if j.get("verdict") == "pass" else 0.0)
    return _avg(verdicts), len(verdicts)


def group_mean(scores: dict[str, float | None], group: str) -> float | None:
    vals = [scores.get(d) for d in DIMENSION_GROUPS[group] if scores.get(d) is not None]
    return round(mean(vals), 2) if vals else None


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
    scores = {d: _avg([j["scores"][d] for j in js if j["scores"].get(d) is not None]) for d in DIMENSIONS}
    excl, excl_n = pass_rate_without_same_family(results)
    det = deterministic_metrics(results, web_path=not any(r.get("web_path") is False for r in results))
    return {
        **{k: v for k, v in det.items() if k not in ("narration_fallback_rate", "fallback_n", "median_latency_ms")},
        "pass_rate_excluding_same_family": excl,
        "judgements_excluding_same_family": excl_n,
        "group_means": {g: group_mean(scores, g) for g in DIMENSION_GROUPS},
        "questions": s["questions"],
        "answered": s["status_counts"].get("ok", 0),
        "errors": s["status_counts"].get("error", 0),
        "judgements": len(js),
        "judge_errors": sum(j["errors"] for j in s["judges"].values()),
        "pass_rate": _avg([1.0 if j["verdict"] == "pass" else 0.0 for j in js]),
        "scores": scores,
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
