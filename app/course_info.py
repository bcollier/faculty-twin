"""Course-info answers: syllabus policies, AI use, O'Reilly access, assignments and due dates, from Canvas.

The real-question eval (evals/README.md, Oct 7) showed most student email is
about running the course, and much of that is already written down on Canvas.
`/api/ask` (docs/SPEC.md, "Inside /api/ask", step 6a) checks a second private
index of Canvas material after the course FAQ and before slide narration:

1. The question is embedded once; the same vector scores the slides and the
   info chunks, both with Ben's `rank()` (app/retrieval.py, unchanged).
2. When the best info chunk scores at least the info threshold (Settings
   override, else env `INFO_THRESHOLD`, else 0.55; app/thresholds.py) and beats
   the best slide by at least the info margin (Settings override, else env
   `INFO_MARGIN`, else 0.05; added Oct 8), and the question is not a personal
   request only Ben can act on (`logistics.personal_request`), the top 3 chunks
   go to one model call through app/llm.py (the active provider and model) with
   a strict grounding prompt: Ben's first-person voice, at most 120 words, only
   from the chunks.
3. The reply is validated (word cap, no web addresses, no `[student]`, no
   access-code-like tokens, grounded in the chunks' words, no long echo of the
   question). A reply that is not JSON is asked for once more (added Oct 8);
   any other failure falls back to the first sentences of the top chunk.
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

from . import config, faq, llm, narration, prompts, thresholds

KIND = "course_info"
DEFAULT_THRESHOLD = thresholds.DEFAULT_INFO  # 0.55
TOP_CHUNKS = 3
MAX_WORDS = 120
MAX_TOKENS = 600
FALLBACK_WORDS = 80
# A reply that is not JSON at all (prose, a refusal preamble) is asked for once more; every other
# failure falls back at once (Oct 8 code review).
NOT_JSON_RETRIES = 1
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
# A student's or TA's CMU address. Added Oct 8: the Canvas text no longer masks "andrew.cmu.edu" (it is the
# university's account domain, indexer/roster.py), so the address before it is checked here instead.
_ANDREW_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@andrew\.cmu\.edu\b", re.I)


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
    reason: Optional[str] = None  # why it fell back (FALLBACK_REASONS), for the Activity log


def threshold() -> float:
    """The Settings override (`info_threshold`), else env `INFO_THRESHOLD`, else 0.55. Read per question."""
    return thresholds.info_threshold()


def margin() -> float:
    """The Settings override (`info_margin`), else env `INFO_MARGIN`, else 0.05. Read per question."""
    return thresholds.info_margin()


def wins(best_info: Optional[float], best_slide: Optional[float]) -> bool:
    """True when the best Canvas chunk should answer instead of the slides (docs/SPEC.md step 6a).

    It must clear the info threshold and beat the best slide by the margin. Added Oct 8: the course-set
    eval had Canvas class summaries answering concept questions they led the slides by 0.003 to 0.011.
    """
    if best_info is None or best_info < threshold():
        return False
    # Rounded so a lead of exactly the margin counts (0.68 - 0.63 is 0.04999... in floating point).
    return best_slide is None or round(best_info - best_slide, 6) >= margin()


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


def one_course(hits: list[Hit], course: Optional[str]) -> list[Hit]:
    """With "All courses", only the top chunk's course (and chunks with no course) go into one answer.

    Added Oct 8 (code review): the top 3 chunks could come from both courses, so a single answer,
    labelled with one course, mixed the other course's policies, due dates, and links.
    """
    if course is not None or not hits:
        return hits
    top = str(hits[0].record.get("course") or "")
    if top in ("", "all"):
        return hits
    return [h for h in hits if str(h.record.get("course") or "") in ("", "all", top)]


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
    if _ANDREW_EMAIL.search(text):
        return "it contains an andrew.cmu.edu email address"
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
    # A few words over the cap keeps its whole sentences that fit (Oct 8: Opus 5.5 wrote 122-126 words).
    trimmed = narration.trim_to_sentences(text, MAX_WORDS)
    if trimmed is None:
        raise ValidationError(f"answer is {narration.word_count(text)} words")
    text = trimmed
    bad = problem(text)
    if bad:
        raise ValidationError(bad)
    ungrounded = ground.problem(text)
    if ungrounded:
        raise ValidationError(f"answer is not grounded: {ungrounded}")
    return text


# Lines indexer/canvas_import.py puts above an item's text (and the plain-text stand-ins for files and links).
_HEADER_LINE = re.compile(r"^(?:Module|Class|Due|Points|Closes|Posted|Link|File posted on Canvas)\s*:", re.I)


def _body_lines(rec: dict[str, Any]) -> list[str]:
    """The chunk's own text: without the item title line and the Module/Class/Due/... header lines above it."""
    lines = [line.strip() for line in str(rec.get("text") or "").splitlines()]
    title = narration.clean_speech(str(rec.get("title") or ""))
    i = 1 if lines and title and narration.clean_speech(lines[0]) == title else 0
    while i < len(lines) and (not lines[i] or _HEADER_LINE.match(lines[i])):
        i += 1
    return [line for line in lines[i:] if line]


def fallback_text(rec: dict[str, Any]) -> str:
    """The top chunk's first sentences that are safe to show, up to FALLBACK_WORDS words.

    Changed Oct 8 (live O'Reilly bug): it starts at the first real sentence, not the title and
    "Module: Course Overview" header, says the due date as a sentence, and ends each heading or
    paragraph line with a period so "Step 1: ..." does not run into the next sentence.
    """
    parts = []
    due = due_text(rec.get("due_at"))
    if due:
        parts.append(f"It is due {due}.")
    for line in _body_lines(rec):
        line = narration.clean_speech(line)
        if line and line[-1] not in ".!?:;":
            line += "."
        parts.append(line)
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(parts))
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


FALLBACK_REASONS = {
    "provider_credits": "The model provider account is out of credits or quota",
    "provider_auth": "The model provider rejected the API key",
    "provider_rate_limit": "The model provider rate-limited the call",
    "provider_unreachable": "The model provider could not be reached (network or timeout)",
    "provider_refused": "The model declined the request",
    "provider_error": "The model provider returned an error",
    "daily_cap": "Today's model-call cap (DAILY_LLM_CALL_CAP) is used up",
    "not_json": "The model's reply was not the JSON asked for",
    "no_answer": "The model's reply had no answer",
    "too_long": "The answer was over the word cap with no sentence to cut at",
    "not_grounded": "The answer used words that are not in the Canvas pages",
    "unsafe_text": "The answer failed a safety check (web address, access code, name, PG)",
    "error": "Unexpected error",
}


def fallback_reason(exc: Exception) -> str:
    """A short code for why a course-info answer fell back (Added Oct 8, for Settings > Activity)."""
    text = str(exc).lower()
    if isinstance(exc, (ValidationError, narration.ValidationError)):
        if "not json" in text:
            return "not_json"
        if "no answer" in text or "empty answer" in text:
            return "no_answer"
        if re.search(r"answer is \d+ words", text):
            return "too_long"
        if "not grounded" in text:
            return "not_grounded"
        return "unsafe_text"
    if isinstance(exc, llm.LLMError):
        if "model-call cap" in text:
            return "daily_cap"
        if "credit balance" in text or "quota" in text or "billing" in text:
            return "provider_credits"
        status = re.search(r"returned (\d{3})", text)
        if status:
            code = int(status.group(1))
            return {401: "provider_auth", 403: "provider_auth", 429: "provider_rate_limit"}.get(code, "provider_error")
        if "request failed" in text:
            return "provider_unreachable"
        if "declined" in text:
            return "provider_refused"
        return "provider_error"
    return "error"


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
    hits = one_course(hits, course)
    top = hits[0].record
    errors: list[str] = []
    source, reason = "llm", None
    system, user, ground = system_prompt(), build_user_prompt(question, hits), grounding(question, hits)
    text = None
    for attempt in range(1 + NOT_JSON_RETRIES):
        try:
            raw = complete(system, user, MAX_TOKENS, provider=provider, model=model)
            text = validate(raw, ground)
            break
        except Exception as exc:  # model trouble or a reply that fails the checks: never put it on screen
            errors.append(str(exc)[:200])
            reason = fallback_reason(exc)
            if reason == "not_json" and attempt < NOT_JSON_RETRIES:
                config.log.info("course-info reply was not JSON; asking once more")
                continue
            config.log.warning("course-info answer fell back (%s): %s", reason, str(exc)[:200])
            break
    if text is None:
        text, source = fallback_text(top), "fallback"
    else:
        reason = None  # the retry answered: nothing fell back
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
    return Result(reply, source, errors, reason)
