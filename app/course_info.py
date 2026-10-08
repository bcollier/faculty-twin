"""Course-info answers: syllabus policies, AI use, O'Reilly access, assignments and due dates, from Canvas.

The real-question eval (evals/README.md, Oct 7) showed most student email is
about running the course, and much of that is already written down on Canvas.
`/api/ask` (docs/SPEC.md, "Inside /api/ask", step 6a) checks a second private
index of Canvas material after the course FAQ and before slide narration:

1. The question is embedded once; the same vector scores the slides and the
   info chunks, both with Ben's `rank()` (app/retrieval.py, unchanged).
2. When the best info chunk scores at least `INFO_THRESHOLD` (env, default
   0.55) and beats the best slide, the top 3 chunks go to one model call
   through app/llm.py (the active provider and model) with a strict grounding
   prompt: Ben's first-person voice, at most 120 words, only from the chunks.
3. The reply is validated (word cap, no web addresses, no `[student]`, no
   access-code-like tokens, grounded in the chunks' words, no long echo of the
   question). Any failure falls back to the first sentences of the top chunk.
4. The reply reuses the FAQ card: `answers`, Canvas link buttons, no audio.

The index (content/info_index.json + content/info_embeddings.npy) is built
privately by the indexer and loaded by app/storage.py. Missing files turn this
path off; nothing else changes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import numpy as np

from . import config, faq, narration, prompts

KIND = "course_info"
DEFAULT_THRESHOLD = 0.55
TOP_CHUNKS = 3
MAX_WORDS = 120
MAX_TOKENS = 600
FALLBACK_WORDS = 80
CHUNK_CHARS = 2500
LABEL = "From Canvas"
NOT_ANSWERED = "Here's where that is on Canvas."

PROMPT_NAME = "course_info_answer"


def system_prompt() -> str:
    """The course-info prompt in use now (Settings can edit it; app/prompts.py holds the default)."""
    return prompts.get(PROMPT_NAME, not_answered=NOT_ANSWERED, max_words=MAX_WORDS)


SYSTEM_PROMPT = prompts.default(PROMPT_NAME, not_answered=NOT_ANSWERED, max_words=MAX_WORDS)  # the built-in default

_STUDENT = re.compile(r"\[\s*student\s*\]", re.I)
# A run of 6+ letters and digits mixed together ("X7K2QP") looks like an access code.
_CODE_LIKE = re.compile(r"\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{6,}\b")
_SECRET_WORDS = re.compile(r"\b(access|enrol(?:l)?ment|join|invite|invitation)\s+code\s*(?:is|:)\s*\S", re.I)


class ValidationError(ValueError):
    pass


@dataclass
class Hit:
    record: dict[str, Any]
    score: float


@dataclass
class Result:
    reply: dict[str, Any]
    source: str  # "llm" or "fallback"
    errors: list[str] = field(default_factory=list)


def threshold() -> float:
    raw = config.env("INFO_THRESHOLD")
    if raw is None:
        return DEFAULT_THRESHOLD
    try:
        return float(raw)
    except ValueError:
        config.log.warning("INFO_THRESHOLD is not a number; using %s", DEFAULT_THRESHOLD)
        return DEFAULT_THRESHOLD


def searchable(content: Any, course: Optional[str]) -> tuple[list[dict[str, Any]], Optional[np.ndarray]]:
    """Info chunks (and their matrix rows) for this course filter. A chunk with no course fits both."""
    records = getattr(content, "info_records", None) or []
    matrix = getattr(content, "info_matrix", None)
    if not records or matrix is None:
        return [], None
    rows = [
        i
        for i, r in enumerate(records)
        if isinstance(r, dict)
        and (course is None or str(r.get("course") or "") in ("", "all", course))
    ]
    if not rows:
        return [], None
    return [records[i] for i in rows], matrix[rows]


def top_hits(ranked: list[tuple[int, float]], records: list[dict[str, Any]], n: int = TOP_CHUNKS) -> list[Hit]:
    return [Hit(records[i], float(score)) for i, score in ranked[:n]]


# ---------------------------------------------------------------- prompt

def due_text(raw: Any) -> str:
    """Canvas due_at (ISO, usually UTC) as Pittsburgh time: "Friday, October 9, 2026 at 11:59 PM ET"."""
    if not raw:
        return ""
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return str(raw)[:40]
    zone = ""
    if when.utcoffset() is not None:
        when, zone = _eastern(when), " ET"
    hour = when.strftime("%I:%M %p").lstrip("0")
    return f"{when.strftime('%A, %B')} {when.day}, {when.year} at {hour}{zone}"


def _eastern(when: datetime) -> datetime:
    """An aware time in Pittsburgh time, with or without a tz database on the server."""
    try:
        from zoneinfo import ZoneInfo

        return when.astimezone(ZoneInfo("America/New_York"))
    except Exception:  # no tz database: US rules, DST from the 2nd Sunday of March to the 1st Sunday of November
        utc = when.astimezone(timezone.utc)
        march = datetime(utc.year, 3, 8 + (6 - datetime(utc.year, 3, 8).weekday()) % 7, 7, tzinfo=timezone.utc)
        november = datetime(utc.year, 11, 1 + (6 - datetime(utc.year, 11, 1).weekday()) % 7, 6, tzinfo=timezone.utc)
        hours = -4 if march <= utc < november else -5
        return utc.astimezone(timezone(timedelta(hours=hours)))


def chunk_payload(rec: dict[str, Any]) -> dict[str, Any]:
    course = str(rec.get("course") or "")
    return {
        "course": faq.COURSE_LABELS.get(course, course),
        "title": str(rec.get("title") or ""),
        "kind": str(rec.get("kind") or ""),
        "due": due_text(rec.get("due_at")),
        "text": narration._clip(rec.get("text"), CHUNK_CHARS),
    }


def build_user_prompt(question: str, hits: list[Hit]) -> str:
    return (
        "Student question (answer it only from the Canvas material below):\n"
        + json.dumps(question)
        + "\n\nCanvas material:\n"
        + json.dumps([chunk_payload(h.record) for h in hits], ensure_ascii=False, indent=1)
    )


def grounding(question: str, hits: list[Hit]) -> narration.Grounding:
    materials = ["Canvas", NOT_ANSWERED, *faq.COURSE_LABELS.values()]
    for h in hits:
        p = chunk_payload(h.record)
        materials += [p["title"], p["kind"], p["due"], str(h.record.get("text") or "")]
    return narration.Grounding.build(question, materials)


# ---------------------------------------------------------------- validation and fallback

def problem(text: str) -> Optional[str]:
    """Why this text may not be shown, or None. Shared by the model's answer and the fallback."""
    if _STUDENT.search(text):
        return "it contains [student]"
    if narration._URLISH.search(text):
        return "it contains a web address"
    if _CODE_LIKE.search(text) or _SECRET_WORDS.search(text):
        return "it looks like it contains an access code"
    return narration.speech_problem(text)  # PG words, quiz access codes, [person] and other name tokens


def validate(raw: str, ground: narration.Grounding) -> str:
    data = narration._extract_json(raw or "")
    if not isinstance(data, dict) or not isinstance(data.get("answer"), str):
        raise ValidationError("reply has no answer")
    text = narration.clean_speech(data["answer"])
    if not text:
        raise ValidationError("empty answer")
    if narration.word_count(text) > MAX_WORDS:
        raise ValidationError(f"answer is {narration.word_count(text)} words")
    bad = problem(text)
    if bad:
        raise ValidationError(bad)
    ungrounded = ground.problem(text)
    if ungrounded:
        raise ValidationError(f"answer is not grounded: {ungrounded}")
    return text


def fallback_text(rec: dict[str, Any]) -> str:
    """The top chunk's first sentences that are safe to show, up to FALLBACK_WORDS words."""
    text = narration.clean_speech(str(rec.get("text") or ""))
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out: list[str] = []
    words = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence or problem(sentence):
            continue
        n = narration.word_count(sentence)
        if out and words + n > FALLBACK_WORDS:
            break
        if not out and n > FALLBACK_WORDS:
            sentence = " ".join(sentence.split()[:FALLBACK_WORDS]) + " ..."
            n = FALLBACK_WORDS
        out.append(sentence)
        words += n
    return " ".join(out) or NOT_ANSWERED


# ---------------------------------------------------------------- the reply

def links(hits: list[Hit]) -> list[dict[str, str]]:
    out, seen = [], set()
    for h in hits:
        url = str(h.record.get("canvas_url") or "").strip()
        if not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        label = narration.clean_speech(str(h.record.get("title") or "Open on Canvas"))[:80] or "Open on Canvas"
        out.append({"label": label, "url": url})
    return out


def answer(
    question: str,
    course: Optional[str],
    hits: list[Hit],
    follow_ups: list[str],
    complete: Callable[..., str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Result:
    """One grounded model call over the top chunks; the top chunk's first sentences if it fails."""
    top = hits[0].record
    errors: list[str] = []
    source = "llm"
    try:
        raw = complete(system_prompt(), build_user_prompt(question, hits), MAX_TOKENS, provider=provider, model=model)
        text = validate(raw, grounding(question, hits))
    except Exception as exc:  # model trouble or a reply that fails the checks: never put it on screen
        errors.append(str(exc)[:200])
        config.log.warning("course-info answer fell back: %s", str(exc)[:200])
        text, source = fallback_text(top), "fallback"
    code = str(top.get("course") or "")
    if code not in faq.COURSE_LABELS:
        code = course or ""
    reply = {
        "question": question,
        "covered": True,
        "kind": KIND,
        "label": LABEL,
        "title": narration.clean_speech(str(top.get("title") or LABEL))[:120] or LABEL,
        "message": text,
        "answers": [{"course": code, "course_label": faq.COURSE_LABELS.get(code, ""), "text": text}],
        "links": links(hits),
        "segments": [],
        "sources": [],
        "follow_ups": follow_ups[:3],
    }
    return Result(reply, source, errors)
