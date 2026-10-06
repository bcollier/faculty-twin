"""Load, check, and choose the student questions an eval run uses.

A question file is JSON Lines, one question per line:

    {"month": "2026-09", "course": "70-445 AI Methods", "category": "CODE_HELP",
     "question": "...", "reference_answer": "..." | null,
     "answerable_from_course_materials": true}

Real questions come from Ben's email, rewritten by an AI so the student cannot
be recognised by name, details, or writing style, and kept PG. They live only
in `evals/private/` (git-ignored) and never enter git. `evals/questions.example.jsonl`
holds invented examples in the same shape for tests and dry runs.

Every question is checked again here before anything is sent to a model: the
roster-free scrub from `app/privacy.py` runs over the text, and a record that
still looks like it carries an email, a long id, a URL, or a key is refused.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from app.privacy import scrub_question

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


def leak_reasons(text: str | None) -> list[str]:
    """Why `text` is not safe to send to a judge model (empty list means it is)."""
    if not text:
        return []
    found = [name for name, pat in _LEAKS.items() if pat.search(text)]
    if scrub_question(text) != text and "scrub token" not in found:
        found.append("personal detail")
    return found


def _record(raw: dict[str, Any], n: int) -> Question:
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


def load(path: str | Path) -> list[Question]:
    """Read a question file. Lines are kept in file order (most recent first by convention)."""
    out: list[Question] = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(f"line {n}: not JSON ({exc.msg})") from exc
        out.append(_record(raw, n))
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
