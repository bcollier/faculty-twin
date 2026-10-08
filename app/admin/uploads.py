"""Uploading source material and following it through processing.

Serves the upload buttons and the status column in Settings > Courses and source material.

Uploads never pass through this function (Vercel caps request bodies at
4.5 MB). `POST /api/admin/uploads` records a `sources` row and returns a signed
Supabase upload URL; the browser PUTs the file straight to the private bucket
under `inbox/<course>/s<NN>/<kind>/<filename>`. The local processing worker
(indexer/worker.py, owned by the pipeline, running on the machine that holds
the private archive) picks up rows with status `uploaded`, processes them, and
bumps `settings.index_version` when the index is rebuilt.

Source status: pending (upload URL minted, file not seen yet) -> uploaded ->
processing -> ready | error. A pending row becomes uploaded when the browser
calls `/complete`, or on the next `GET /api/admin/sources` once the object is
in storage.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from .. import auth, supa
from .common import UPLOAD_KINDS, UploadBody, _check_course, _db_error, _need_supabase
from .courses import _ensure_session

router = APIRouter(prefix="/api/admin")

CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".vtt": "text/vtt",
    ".mp4": "video/mp4",
    ".ipynb": "application/x-ipynb+json",
}
UPLOAD_URL_SECONDS = 7200  # Supabase signed upload URLs last two hours


def safe_filename(name: str) -> str:
    """A file name safe in a bucket path: no folders, only letters, digits, dot, dash and underscore."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return base[:120]


@router.post("/uploads")
def create_upload(body: UploadBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """A sources row and a signed upload URL into the bucket's inbox; the local worker picks the file up."""
    _check_course(body.course)
    _need_supabase()
    if body.kind not in UPLOAD_KINDS:
        raise HTTPException(400, "kind must be slides, transcript, video, or notebook.")
    allowed, max_size = UPLOAD_KINDS[body.kind]
    filename = safe_filename(body.filename)
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    if not filename or ext not in allowed:
        raise HTTPException(400, f"{body.kind} uploads must be {' or '.join(sorted(allowed))} files.")
    if body.size <= 0 or body.size > max_size:
        raise HTTPException(400, f"That file is too large for {body.kind} (limit {max_size // 1024**2} MB).")
    course = _check_course(body.course)
    if not 1 <= body.session <= 99:
        raise HTTPException(400, "The session number must be between 1 and 99.")
    path = f"inbox/{course}/s{body.session:02d}/{body.kind}/{filename}"
    try:
        _ensure_session(course, body.session)
        row = supa.insert(
            "sources",
            {
                "course": course,
                "session": body.session,
                "kind": body.kind,
                "path": path,
                "status": "pending_upload",
                "message": None,
                "size_bytes": body.size,
                "updated_at": datetime.now(UTC).isoformat(),
            },
            upsert_on="path",
        )[0]
        upload_url = supa.create_upload_url(path, upsert=True)
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    return {
        "source_id": row["id"],
        "path": path,
        "upload_url": upload_url,
        "method": "PUT",
        "headers": {"Content-Type": CONTENT_TYPES[ext], "x-upsert": "true"},
        "expires_in": UPLOAD_URL_SECONDS,
    }


def _set_status(source_id: int, status: str, message: str | None = None) -> dict[str, Any]:
    rows = supa.update(
        "sources",
        {"id": f"eq.{source_id}"},
        {"status": status, "message": message, "updated_at": datetime.now(UTC).isoformat()},
    )
    if not rows:
        raise HTTPException(404, "No such source.")
    return rows[0]


@router.get("/sources")
def list_sources(
    course: str | None = Query(None, max_length=5),
    session: int | None = Query(None, ge=1, le=99),
    _: auth.Session = Depends(auth.require_admin),
) -> dict[str, Any]:
    """Uploads with their processing status, newest first, optionally for one course or session."""
    _need_supabase()
    params = {"select": "*", "order": "updated_at.desc", "limit": "500"}
    if course:
        params["course"] = f"eq.{course}"
    if session:
        params["session"] = f"eq.{session}"
    try:
        rows = supa.select("sources", params)
        checked = 0
        for row in rows:  # promote finished uploads the browser did not report
            if row["status"] == "pending_upload" and checked < 10:
                checked += 1
                if supa.object_exists(row["path"]):
                    row.update(_set_status(row["id"], "uploaded"))
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    return {"sources": rows}


@router.post("/sources/{source_id}/complete")
def complete_source(source_id: int, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """The browser calls this after its PUT finishes; marks the source ready for the worker."""
    _need_supabase()
    try:
        rows = supa.select("sources", {"select": "*", "id": f"eq.{source_id}"})
        if not rows:
            raise HTTPException(404, "No such source.")
        if not supa.object_exists(rows[0]["path"]):
            raise HTTPException(409, "The file has not arrived in storage yet.")
        return _set_status(source_id, "uploaded")
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc


@router.post("/sources/{source_id}/rerun")
def rerun_source(source_id: int, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Queue an upload for processing again (status back to uploaded)."""
    _need_supabase()
    try:
        return _set_status(source_id, "uploaded")
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
