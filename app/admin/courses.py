"""The course catalog: courses, their sessions, and what material each session has.

Serves Settings > Courses and source material (the course and session tables, adding a
course or session, hiding a session from students). The catalog lives in the Supabase
`courses` and `sessions` tables; the counts of indexed slides and clips come from the
loaded index. Every route needs the admin cookie.
"""

from __future__ import annotations

import re
from datetime import date as date_cls
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from .. import auth, config, playlist, storage, supa
from .common import (
    COURSE_RE,
    UPLOAD_KINDS,
    CourseBody,
    SessionBody,
    SessionPatch,
    _check_course,
    _db_error,
    _need_supabase,
    session_id,
)

router = APIRouter(prefix="/api/admin")


def _check_date(value: str | None) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(400, "date must look like 2026-10-05.") from exc


def _check_title(value: str | None, what: str) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if len(value) > 200:
        raise HTTPException(400, f"{what} must be under 200 characters.")
    return value


# ---------------------------------------------------------------- the catalog view

@router.get("/courses")
def list_courses(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Courses -> sessions with what exists for each: uploaded sources, indexed slides, clips."""
    try:
        loaded = storage.store.get()
    except storage.ContentUnavailable:
        loaded = None
    course_rows, session_rows, source_rows = _catalog_rows()
    courses = _CourseTree({c["code"]: {"course": c["code"], "title": c.get("title"), "term": c.get("term"),
                                       "sessions": {}} for c in course_rows})
    for s in session_rows:
        row = courses.session(s["course"], int(s["session"]))
        row.update({"date": s.get("date"), "title": s.get("title"), "visible": s.get("visible", True)})
    for src in source_rows:  # newest first: keep the latest per kind
        row = courses.session(src["course"], int(src["session"]))
        if row["sources"].get(src["kind"]) is None:
            row["sources"][src["kind"]] = {
                "id": src["id"],
                "status": src["status"],
                "message": src.get("message"),
                "path": src["path"],
                "updated_at": src.get("updated_at"),
            }
    if loaded is not None:
        courses.add_index(loaded.records)
    for c in courses.by_code.values():
        for row in c["sessions"].values():
            _summarize_session(row)
    return {
        "courses": [
            {**c, "sessions": [c["sessions"][k] for k in sorted(c["sessions"])]}
            for _, c in sorted(courses.by_code.items())
        ]
    }


def _catalog_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(courses, sessions, sources) rows from Postgres; all empty without Supabase."""
    if not config.supabase_configured():
        return [], [], []
    try:
        return (supa.select("courses", {"select": "*", "order": "code"}),
                supa.select("sessions", {"select": "*", "order": "course,session"}),
                supa.select("sources", {"select": "*", "order": "updated_at.desc"}))
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc


class _CourseTree:
    """Course code -> course entry with its sessions by number, created on first mention."""

    def __init__(self, by_code: dict[str, dict[str, Any]]) -> None:
        self.by_code = by_code

    def session(self, course: str, number: int) -> dict[str, Any]:
        """The entry for one session, made empty (no sources, nothing indexed) the first time it is named."""
        entry = self.by_code.setdefault(course, {"course": course, "title": None, "term": None, "sessions": {}})
        return entry["sessions"].setdefault(
            number,
            {
                "id": session_id(course, number),
                "session": number,
                "date": None,
                "title": None,
                "visible": True,
                "sources": {k: None for k in UPLOAD_KINDS},
                "slides_indexed": 0,
                "clips": 0,
            },
        )

    def add_index(self, records: list[dict[str, Any]]) -> None:
        """Count indexed slides and clips per session, and fill missing dates and titles from the index."""
        for r in records:
            if r.get("kind", "slide") != "slide" or not r.get("course"):
                continue
            row = self.session(str(r["course"]), int(r.get("session") or 0))
            row["slides_indexed"] += 1
            row["clips"] += 1 if r.get("clip") else 0
            row["_transcript"] = row.get("_transcript") or bool(str(r.get("transcript") or "").strip())
            row["date"] = row["date"] or r.get("date")
            row["title"] = row["title"] or r.get("session_title")
            course = self.by_code[str(r["course"])]
            course["title"] = course["title"] or r.get("course_title")


def _summarize_session(row: dict[str, Any]) -> None:
    """Add `has`: which material a session has, from an upload or from the index."""
    src = row["sources"]
    row["has"] = {
        "slides": bool(src["slides"]) or row["slides_indexed"] > 0,
        "transcript": bool(src["transcript"]) or row.pop("_transcript", False),
        "video": bool(src["video"]) or row["clips"] > 0,
        "clips": row["clips"] > 0,
        "indexed": row["slides_indexed"] > 0,
    }
    row.pop("_transcript", None)


# ---------------------------------------------------------------- adding and editing

@router.post("/courses")
def add_course(body: CourseBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Add a course to the catalog."""
    _need_supabase()
    code = (body.course or body.code or "").strip()
    if not COURSE_RE.match(code):
        raise HTTPException(400, "The course code is five digits, like 70445.")
    title = _check_title(body.title, "The title")
    if not title:
        raise HTTPException(400, "Please give the course a title.")
    row = {"code": code, "title": title, "term": _check_title(body.term, "The term")}
    try:
        saved = supa.insert("courses", row, upsert_on="code")[0]
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    return {"course": saved["code"], "title": saved.get("title"), "term": saved.get("term"), "sessions": []}


def _ensure_session(course: str, number: int, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create or update a session row (the course must exist); uploads call it too, before minting a link."""
    if not 1 <= number <= 99:
        raise HTTPException(400, "The session number must be between 1 and 99.")
    if not supa.select("courses", {"select": "code", "code": f"eq.{course}"}):
        raise HTTPException(400, "Add the course first.")
    row = {"id": session_id(course, number), "course": course, "session": number, **(extra or {})}
    return supa.insert("sessions", row, upsert_on="id")[0]


@router.post("/sessions")
def add_session(body: SessionBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Add a session to a course."""
    _need_supabase()
    extra = {
        "date": _check_date(body.date),
        "title": _check_title(body.title, "The title"),
        "visible": True if body.visible is None else body.visible,
    }
    try:
        row = _ensure_session(_check_course(body.course), body.session, extra)
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    playlist.clear_hidden_cache()
    return row


@router.patch("/sessions/{sid}")
def patch_session(sid: str, body: SessionPatch, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """`sid` is "<course>-s<NN>", e.g. 70445-s06. Creates the row if only the index knew the session."""
    _need_supabase()
    m = re.match(r"^([0-9]{5})-s([0-9]{2})$", sid)
    if not m:
        raise HTTPException(400, "Session ids look like 70445-s06.")
    values: dict[str, Any] = {}
    if body.visible is not None:
        values["visible"] = body.visible
    if body.title is not None:
        values["title"] = _check_title(body.title, "The title")
    if body.date is not None:
        values["date"] = _check_date(body.date)
    if not values:
        raise HTTPException(400, "Nothing to change.")
    try:
        row = _ensure_session(m.group(1), int(m.group(2)), values)
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    playlist.clear_hidden_cache()
    return row
