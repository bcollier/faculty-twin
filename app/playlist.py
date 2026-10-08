"""Everything around retrieval that is not ranking or selection.

- which records are searchable (slides only, chosen course, visible sessions)
- attaching related code to a chosen slide
- turning chosen records + narration into the playlist JSON the frontend plays
- the course/session catalog for /api/courses
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from . import config, speech, storage, supa
from .storage import Content

if TYPE_CHECKING:
    from .voices import Plan

_hidden_lock = threading.Lock()
_hidden: tuple[float, set[tuple[str, int]]] = (0.0, set())
_hidden_generation = 0  # bumped when Settings hides or shows a session; an older read is not cached
HIDDEN_CACHE_SECONDS = 60.0


def hidden_sessions() -> set[tuple[str, int]]:
    """(course, session) pairs hidden from students in Settings (sessions.visible = false)."""
    global _hidden
    now = time.monotonic()
    with _hidden_lock:
        if _hidden[0] and now - _hidden[0] < HIDDEN_CACHE_SECONDS:
            return _hidden[1]
        generation = _hidden_generation
    if not config.supabase_configured():
        return set()
    try:
        rows = supa.select("sessions", {"select": "course,session", "visible": "eq.false"})
        hidden = {(str(r["course"]), int(r["session"])) for r in rows}
    except (supa.SupabaseError, KeyError, ValueError) as exc:
        config.log.warning("sessions read failed: %s", exc)
        hidden = _hidden[1]
    with _hidden_lock:
        if generation == _hidden_generation:  # not if a session was hidden while this read was in flight
            _hidden = (now, hidden)
    return hidden


def clear_hidden_cache() -> None:
    global _hidden, _hidden_generation
    with _hidden_lock:
        _hidden = (0.0, set())
        _hidden_generation += 1


def searchable(content: Content, course: str | None) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Slide records (and their matrix rows) a student may be shown for this question."""
    hidden = hidden_sessions()
    rows = [
        i
        for i, r in enumerate(content.records)
        if r.get("kind", "slide") == "slide"
        and (course is None or str(r.get("course")) == course)
        and (str(r.get("course")), int(r.get("session") or 0)) not in hidden
    ]
    return [content.records[i] for i in rows], content.matrix[rows]


def related_code(content: Content, rec: dict[str, Any]) -> dict[str, Any] | None:
    """The first related code cell of a slide, as `{source, mark_lines}`."""
    for code_id in rec.get("related_code") or []:
        code = content.record(code_id)
        if code is None:
            continue
        source = code.get("source") or code.get("text") or ""
        if source.strip():
            return {"source": source, "mark_lines": list(code.get("mark_lines") or [])}
    return None


LINKS_MAX_SLIDES = 10


def media_links(content: Content, recs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`{slide_id: {image, clip}}`: signed slide-image links and `{url, start, end}` clips (or None), one batch."""
    clips = {r["id"]: str(r.get("clip")).lstrip("/") for r in recs if r.get("clip")}
    urls = storage.media_urls([r.get("image") for r in recs] + list(clips.values()))
    out: dict[str, dict[str, Any]] = {}
    for rec in recs:
        clip = None
        if urls.get(clips.get(rec["id"], "")):
            start, end = content.clip_windows.get(rec["id"], (None, None))
            clip = {"url": urls[clips[rec["id"]]], "start": start, "end": end}
        out[rec["id"]] = {"image": urls.get(str(rec.get("image") or "").lstrip("/")), "clip": clip}
    return out


def fresh_links(content: Content, slide_ids: list[str]) -> dict[str, dict[str, Any]]:
    """New signed links for slides already on a student's screen (`POST /api/links`).

    Added Oct 8 (code review): replaces re-asking the whole question when an hour-old link expires.
    Only slides in the index and in visible sessions; anything else is left out.
    """
    hidden = hidden_sessions()
    recs = []
    for sid in dict.fromkeys(str(s) for s in slide_ids):
        rec = content.record(sid)
        if rec is None or rec.get("kind", "slide") != "slide":
            continue
        if (str(rec.get("course")), int(rec.get("session") or 0)) in hidden:
            continue
        recs.append(rec)
    return media_links(content, recs)


def build_playlist(
    content: Content,
    question: str,
    chosen: list[dict[str, Any]],
    narrations: dict[str, str],
    follow_ups: list[str],
    voice: "Plan | None",
) -> dict[str, Any]:
    """`voice` is the answer's voice plan (app/voices.py); None or an empty plan means captions only.

    Each segment carries the signed link for the voice that should speak and
    that voice's label, plus, when the free fallback is on, a second signed
    link and label the page switches to if the first voice fails.
    """
    primary = voice.primary if voice is not None else None
    fallback = voice.fallback if voice is not None else None
    media = media_links(content, chosen)
    segments = []
    sources = []
    for n, rec in enumerate(chosen, start=1):
        image, clip = media[rec["id"]]["image"], media[rec["id"]]["clip"]
        narration = narrations.get(rec["id"], "")
        audio = speech.audio_link(narration, primary.tag_key) if primary else None
        audio_fallback = speech.audio_link(narration, fallback.tag_key) if (audio and fallback) else None
        segments.append(
            {
                "n": n,
                "slide_id": rec["id"],
                "course": rec.get("course"),
                "course_title": rec.get("course_title"),
                "session": rec.get("session"),
                "session_title": rec.get("session_title"),
                "date": rec.get("date"),
                "slide_number": rec.get("slide_number"),
                "image": image,
                "narration": narration,
                "audio": audio,
                "voice": primary.public() if (audio and primary) else None,
                "audio_fallback": audio_fallback,
                "voice_fallback": fallback.public() if (audio_fallback and fallback) else None,
                "code": related_code(content, rec),
                "clip": clip,
            }
        )
        sources.append(
            {
                "slide_id": rec["id"],
                "course": rec.get("course"),
                "session": rec.get("session"),
                "date": rec.get("date"),
                "slide_number": rec.get("slide_number"),
                "image": image,
            }
        )
    return {
        "question": question,
        "covered": bool(segments),
        "segments": segments,
        "sources": sources,
        "follow_ups": follow_ups,
    }


def not_covered(question: str) -> dict[str, Any]:
    return {"question": question, "covered": False, "segments": [], "sources": [], "follow_ups": []}


def courses(content: Content) -> list[dict[str, Any]]:
    """`[{course, title, sessions: [{session, date, title}]}]` from the index, hidden sessions removed."""
    hidden = hidden_sessions()
    by_course: dict[str, dict[str, Any]] = {}
    for r in content.records:
        course = str(r.get("course") or "")
        if not course:
            continue
        session = int(r.get("session") or 0)
        if (course, session) in hidden:
            continue
        entry = by_course.setdefault(course, {"course": course, "title": r.get("course_title"), "sessions": {}})
        if session in entry["sessions"]:
            continue
        entry["sessions"][session] = {"session": session, "date": r.get("date"), "title": r.get("session_title")}
    return [
        {**c, "sessions": [c["sessions"][k] for k in sorted(c["sessions"])]}
        for _, c in sorted(by_course.items())
    ]
