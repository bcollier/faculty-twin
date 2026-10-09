"""Everything around retrieval that is not ranking or selection.

- which records are searchable (slides only, chosen course, visible sessions)
- attaching related code to a chosen slide
- turning chosen records + narration into the playlist JSON the frontend plays
- the course/session catalog for /api/courses
"""

from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING, Any

import numpy as np

from . import config, speech, storage, supa
from .storage import Content

if TYPE_CHECKING:
    from .voices import Plan, Voice

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
    """Drop the cached hidden-session list, and make any read in flight discard what it fetched."""
    global _hidden, _hidden_generation
    with _hidden_lock:
        _hidden = (0.0, set())
        _hidden_generation += 1


SEARCHABLE_VIEWS = 8  # (course, hidden sessions) views kept per loaded index


def searchable(content: Content, course: str | None) -> tuple[list[dict[str, Any]], np.ndarray]:
    """Slide records (and their matrix rows) a student may be shown for this question.

    Changed Oct 8 (retrieval rebuild): the rows for one course filter and one set of hidden sessions are
    copied out of the index once and kept on the loaded content as a read-only array, so every question
    reuses it and `retrieval.rank` computes its row lengths once. A reload brings a new Content (and new
    views); hiding or showing a session changes the key.
    """
    hidden = hidden_sessions()
    views = getattr(content, "views", None)
    key = (course, frozenset(hidden))
    if views is not None and key in views:
        records, matrix = views[key]
        return list(records), matrix
    rows = [
        i
        for i, r in enumerate(content.records)
        if r.get("kind", "slide") == "slide"
        and (course is None or str(r.get("course")) == course)
        and (str(r.get("course")), int(r.get("session") or 0)) not in hidden
    ]
    records, matrix = [content.records[i] for i in rows], content.matrix[rows]
    matrix.flags.writeable = False
    if views is not None:
        if len(views) >= SEARCHABLE_VIEWS:
            views.clear()
        views[key] = (records, matrix)
    return list(records), matrix


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


def boxes_path(rec: dict[str, Any]) -> str | None:
    """The slide's word-box sidecar (read-along): `<slide image without .webp>.boxes.json`.

    The indexer writes one next to each slide image it could read words from;
    signing a path with no object behind it just leaves that slide without boxes.
    """
    image = str(rec.get("image") or "").lstrip("/")
    if not image.startswith("slides/") or not image.endswith(".webp") or image.endswith("-thumb.webp"):
        return None
    return image[: -len(".webp")] + ".boxes.json"


def media_links(content: Content, recs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """`{slide_id: {image, clip, boxes}}`: signed slide-image links, `{url, start, end}` clips (or None),
    and the slide's word boxes (or None), in one batch."""
    clips = {r["id"]: str(r.get("clip")).lstrip("/") for r in recs if r.get("clip")}
    boxes = {r["id"]: b for r in recs if (b := boxes_path(r))}
    urls = storage.media_urls([r.get("image") for r in recs] + list(clips.values()) + list(boxes.values()))
    out: dict[str, dict[str, Any]] = {}
    for rec in recs:
        clip = None
        if urls.get(clips.get(rec["id"], "")):
            start, end = content.clip_windows.get(rec["id"], (None, None))
            clip = {"url": urls[clips[rec["id"]]], "start": start, "end": end}
        out[rec["id"]] = {
            "image": urls.get(str(rec.get("image") or "").lstrip("/")),
            "clip": clip,
            "boxes": urls.get(boxes.get(rec["id"], "")),
        }
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
    voice: Plan | None,
) -> dict[str, Any]:
    """`voice` is the answer's voice plan (app/voices.py); None or an empty plan means captions only.

    Each segment carries the signed link for the voice that should speak and
    that voice's label, plus, when the free fallback is on, a second signed
    link and label the page switches to if the first voice fails.
    """
    primary = voice.primary if voice is not None else None
    fallback = voice.fallback if voice is not None else None
    media = media_links(content, chosen)
    segments = [_segment(content, n, rec, media[rec["id"]], narrations.get(rec["id"], ""), primary, fallback)
                for n, rec in enumerate(chosen, start=1)]
    sources = [_source(rec, media[rec["id"]]["image"]) for rec in chosen]
    return {
        "question": question,
        "covered": bool(segments),
        "segments": segments,
        "sources": sources,
        "follow_ups": follow_ups,
    }


def _segment(content: Content, n: int, rec: dict[str, Any], media: dict[str, Any], narration: str,
             primary: Voice | None, fallback: Voice | None) -> dict[str, Any]:
    """One walkthrough step: the slide, its narration, signed audio links (and fallback), code, clip, read-along."""
    audio = speech.audio_link(narration, primary.tag_key) if primary else None
    audio_fallback = speech.audio_link(narration, fallback.tag_key) if (audio and fallback) else None
    return {
        "n": n,
        "slide_id": rec["id"],
        "course": rec.get("course"),
        "course_title": rec.get("course_title"),
        "session": rec.get("session"),
        "session_title": rec.get("session_title"),
        "date": rec.get("date"),
        "slide_number": rec.get("slide_number"),
        "image": media["image"],
        "narration": narration,
        "audio": audio,
        "voice": primary.public() if (audio and primary) else None,
        "audio_fallback": audio_fallback,
        "voice_fallback": fallback.public() if (audio_fallback and fallback) else None,
        "code": related_code(content, rec),
        "clip": media["clip"],
        # Read-along (added Oct 8): word timings for each audio link, and the slide's word boxes.
        "timings": speech.timings_link(audio),
        "timings_fallback": speech.timings_link(audio_fallback),
        "boxes": media["boxes"],
    }


def _source(rec: dict[str, Any], image: str | None) -> dict[str, Any]:
    """The source card for one slide."""
    return {
        "slide_id": rec["id"],
        "course": rec.get("course"),
        "session": rec.get("session"),
        "date": rec.get("date"),
        "slide_number": rec.get("slide_number"),
        "image": image,
    }


def not_covered(question: str) -> dict[str, Any]:
    return {"question": question, "covered": False, "segments": [], "sources": [], "follow_ups": []}


# ---------------------------------------------------------------- cross-course fallback (spec step 7c)

CROSS_COURSE = "cross_course"


def other_course_ranked(ranked: list[tuple[int, float]], records: list[dict[str, Any]],
                        course: str) -> list[tuple[int, float]]:
    """`ranked` (over every course's slides) without the pairs for `course`: indexes still point into `records`.

    Added Oct 8 (the frames question filtered to 45-884): select_segments() (app/retrieval.py) runs on these
    pairs with the unfiltered records list, so it can only pick the other course's slides (its gap fills come
    from the same session as two picked slides).
    """
    return [(i, s) for i, s in ranked if 0 <= i < len(records) and str(records[i].get("course")) != course]


def _course_label(code: str) -> str:
    """The course number as students write it: "70-445" for "70445"; anything else as given."""
    return f"{code[:2]}-{code[2:]}" if len(code) == 5 and code.isdigit() else code


def cross_course_intro(asked: str, chosen: list[dict[str, Any]]) -> str:
    """What the student reads first when the slides come from another course than the filter.

    "My 45-884 slides don't cover that, but I taught it in 70-445 (AI for Business Leaders). Here are 3 slides
    from session 3 (Rules Search and Expert Systems). I'll walk you through them."
    Plain words only: no em dash, no web address, nothing the voice checks would stop (tests/test_cross_course.py).
    """
    first = chosen[0]
    taught = _course_label(str(first.get("course") or ""))
    title = str(first.get("course_title") or "").strip()
    n = len(chosen)
    sessions = list(dict.fromkeys(int(r.get("session") or 0) for r in chosen))
    slides = "Here is 1 slide" if n == 1 else f"Here are {n} slides"
    if len(sessions) == 1:
        session_title = str(first.get("session_title") or "").strip()
        where = f"from session {sessions[0]}" + (f" ({session_title})" if session_title else "")
        walk = "I'll walk you through it." if n == 1 else "I'll walk you through them."
    else:
        where, walk = f"from {len(sessions)} sessions", "I'll walk you through them in order."
    text = (f"My {_course_label(asked)} slides don't cover that, but I taught it in {taught}"
            + (f" ({title})" if title else "") + f". {slides} {where}. {walk}")
    return re.sub(r"\s*[—–]\s*", ", ", text)


def mark_cross_course(reply: dict[str, Any], asked: str, chosen: list[dict[str, Any]]) -> dict[str, Any]:
    """The playlist from build_playlist(), labeled as an answer from another course than the filter."""
    reply["kind"] = CROSS_COURSE
    reply["asked_course"] = asked
    reply["message"] = cross_course_intro(asked, chosen)
    return reply


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
