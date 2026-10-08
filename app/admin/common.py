"""What the Settings route modules share: the course and model id checks, the database error, and the request bodies.

Every module in `app/admin/` imports from here; nothing here registers a route.
Names that start with `_` are private to the `app/admin` package, not to this file.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from pydantic import BaseModel

from .. import config, supa

# Upload kind -> (allowed extensions, size limit in bytes). Courses lists one source slot per kind.
UPLOAD_KINDS = {
    "slides": ({".pdf", ".pptx"}, 300 * 1024**2),
    "transcript": ({".vtt"}, 20 * 1024**2),
    "video": ({".mp4"}, 5 * 1024**3),
    "notebook": ({".ipynb"}, 50 * 1024**2),
}
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/~\-]{1,200}$")
COURSE_RE = re.compile(r"^[0-9]{5}$")


# ---------------------------------------------------------------- shared checks

def _need_supabase() -> None:
    if not config.supabase_configured():
        raise HTTPException(503, "Supabase is not configured, so courses and uploads are unavailable.")


def _db_error(exc: supa.SupabaseError) -> HTTPException:
    config.log.warning("admin db call failed: %s", exc)
    return HTTPException(502, "The database call failed. Check that supabase/schema.sql has been run.")


def session_id(course: str, session: int) -> str:
    return f"{course}-s{session:02d}"


def _check_course(raw: str) -> str:
    """Course codes are five digits. They become part of a storage path, so check them here."""
    code = (raw or "").strip()
    if not COURSE_RE.match(code):
        raise HTTPException(400, "The course code is five digits, like 70445.")
    return code


# ---------------------------------------------------------------- request bodies

class SettingsBody(BaseModel):
    """PUT /api/admin/settings: every field optional; only the fields sent are saved."""

    provider: str | None = None
    model: str | None = None
    voice_id: str | None = None
    voice_fallback: str | None = None
    voice_fallback_voice: str | None = None
    daily_voice_char_cap: int | None = None
    daily_free_voice_char_cap: int | None = None
    student_passcode: str | None = None
    web_answers_enabled: bool | None = None
    daily_web_answer_cap: int | None = None
    web_answer_voice: str | None = None  # "none" (text only) or "edge:<ShortName>"
    open_access_until: float | None = None  # epoch seconds; 0 closes open access now


class TestBody(BaseModel):
    """POST /api/admin/test: a question and the provider and model to try (unsaved)."""

    question: str
    provider: str | None = None
    model: str | None = None
    course: str | None = None


class PromptBody(BaseModel):
    text: str
    note: str | None = None


class PromptNoteBody(BaseModel):
    note: str | None = None


class PromptRestoreBody(BaseModel):
    version: str
    note: str | None = None


class PromptTestBody(BaseModel):
    """POST a draft prompt with a question to try it on."""

    text: str
    question: str
    course: str | None = None


class CourseBody(BaseModel):
    course: str | None = None  # the Settings page sends `course`; the spec says `code`; both work
    code: str | None = None
    title: str
    term: str | None = None


class SessionBody(BaseModel):
    course: str
    session: int
    date: str | None = None
    title: str | None = None
    visible: bool | None = True


class SessionPatch(BaseModel):
    visible: bool | None = None
    title: str | None = None
    date: str | None = None


class UploadBody(BaseModel):
    """POST /api/admin/uploads: what is being uploaded, for which session."""

    course: str
    session: int
    kind: str
    filename: str
    size: int
