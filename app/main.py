"""Faculty Twin backend: one FastAPI app, deployed on Vercel as a single function.

Every route except /api/health and /api/login needs the student cookie (an
admin cookie also works, so Ben can preview). Settings routes live in
app/admin.py and need the admin cookie.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import auth, config, embed, limits, llm, narration, playlist, retrieval, settings_store, speech, storage
from .storage import Content

app = FastAPI(title="Faculty Twin", docs_url=None, redoc_url=None, openapi_url=None)


# ---------------------------------------------------------------- readable errors

@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", []) if p != "body") or "request"
    if first.get("type") == "json_invalid" or field == "request":
        msg = "The request body must be JSON."
    else:
        msg = f"{field}: {first.get('msg', 'is not valid')}"
    return JSONResponse({"detail": msg}, status_code=400)


# ---------------------------------------------------------------- injectable pieces

@dataclass
class Retriever:
    """The hand-written retrieval functions. Tests swap in a TEST FAKE via dependency override."""

    rank: Callable[[np.ndarray, np.ndarray], list[tuple[int, float]]]
    select_segments: Callable[[list[tuple[int, float]], list[dict[str, Any]], Optional[float]], list[dict[str, Any]]]
    threshold: Optional[float]


def get_retriever() -> Retriever:
    return Retriever(retrieval.rank, retrieval.select_segments, retrieval.NOT_COVERED_THRESHOLD)


def get_embedder() -> Callable[[str], np.ndarray]:
    return embed.embed_question


def get_completer() -> Callable[..., str]:
    return llm.complete_json


def get_content() -> Content:
    return storage.store.get_or_503()


# ---------------------------------------------------------------- request bodies

class PasscodeBody(BaseModel):
    passcode: str


class AskBody(BaseModel):
    question: str
    course: Optional[str] = None


def clean_question(raw: str) -> str:
    question = re.sub(r"\s+", " ", raw or "").strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    if len(question) > config.QUESTION_MAX_CHARS:
        raise HTTPException(400, f"Please keep questions under {config.QUESTION_MAX_CHARS} characters.")
    return question


def clean_course(raw: Optional[str]) -> Optional[str]:
    if raw in (None, "", "all"):
        return None
    if raw not in config.COURSE_CODES:
        raise HTTPException(400, "course must be 70445, 45884, or null.")
    return raw


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


# ---------------------------------------------------------------- the ask pipeline

class RetrievalNotReady(Exception):
    pass


def answer(
    question: str,
    course: Optional[str],
    content: Content,
    retriever: Retriever,
    embedder: Callable[[str], np.ndarray],
    completer: Callable[..., str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run retrieval + narration. Returns (playlist, info for logging)."""
    if provider is None or model is None:
        provider, model = settings_store.llm_choice()
    voice = settings_store.voice_id()
    info: dict[str, Any] = {"provider": provider, "model": model, "top_score": None, "narration": None}

    stored = _stored_topic(content, question, course)
    if stored is not None:
        info["narration"] = "stored"
        return _replay_topic(content, question, stored, voice), info

    records, matrix = playlist.searchable(content, course)
    if not records:
        return playlist.not_covered(question), info
    try:
        qvec = embedder(question)
    except embed.EmbeddingError as exc:
        config.log.warning("embedding failed: %s", exc)
        raise HTTPException(503, "The search service is not available right now. Please try again shortly.") from exc
    if qvec.shape[-1] != matrix.shape[1]:
        config.log.error("question vector dim %s != index dim %s", qvec.shape, matrix.shape)
        raise HTTPException(503, "The search index does not match the embedding model. Ben needs to rebuild it.")
    try:
        ranked = retriever.rank(qvec, matrix)
        chosen = retriever.select_segments(ranked, records, retriever.threshold)
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc
    info["top_score"] = float(ranked[0][1]) if ranked else None
    if not chosen:
        return playlist.not_covered(question), info

    codes = {r["id"]: (playlist.related_code(content, r) or {}).get("source") for r in chosen}
    result = narration.narrate(question, chosen, codes, provider=provider, model=model, complete=completer)
    info["narration"] = result.source
    info["errors"] = result.errors
    return playlist.build_playlist(content, question, chosen, result.narrations, result.follow_ups, voice), info


def _stored_topic(content: Content, question: str, course: Optional[str]) -> Optional[dict[str, Any]]:
    key = _norm(question)
    for topic in content.topics:
        if not isinstance(topic, dict) or not topic.get("playlist"):
            continue
        if _norm(str(topic.get("question", ""))) == key and (course is None or topic.get("course") in (None, course)):
            return topic
    return None


def _replay_topic(content: Content, question: str, topic: dict[str, Any], voice: Optional[str]) -> dict[str, Any]:
    """Rebuild a pre-generated playlist with fresh signed links."""
    stored = topic["playlist"]
    segs = [s for s in stored.get("segments", []) if content.record(s.get("slide_id", ""))]
    chosen = [content.record(s["slide_id"]) for s in segs]
    narrations = {s["slide_id"]: s.get("narration", "") for s in segs}
    result = playlist.build_playlist(content, question, chosen, narrations, stored.get("follow_ups", []), voice)
    if voice is None:
        return result  # captions only
    # Stored mp3s live at audio/<voice_id>/<hash>.mp3; only use one made with the current voice.
    paths = {s["slide_id"]: str(s.get("audio_path") or "").lstrip("/") for s in segs}
    usable = {k: p for k, p in paths.items() if p.startswith(f"audio/{voice}/")}
    audio = storage.media_urls(list(usable.values()))
    for seg in result["segments"]:
        path = usable.get(seg["slide_id"])
        if path and audio.get(path):
            seg["audio"] = audio[path]
    return result


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/login", status_code=204)
def login(body: PasscodeBody, request: Request, response: Response) -> None:
    limits.check_login_rate(request, "student")
    code = (body.passcode or "").strip()
    if not code or len(code) > 200 or not auth.check_student_passcode(code):
        raise HTTPException(401, "That passcode is not right. Please check it and try again.")
    auth.issue_student(response)


@app.post("/api/logout")
def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(auth.STUDENT_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/courses")
def courses(_: auth.Session = Depends(auth.require_student), content: Content = Depends(get_content)) -> list:
    return playlist.courses(content)


@app.get("/api/topics")
def topics(_: auth.Session = Depends(auth.require_student), content: Content = Depends(get_content)) -> list:
    out = []
    for t in content.topics:
        if isinstance(t, dict) and t.get("question"):
            out.append({"question": t["question"], "course": t.get("course")})
    return out


@app.post("/api/ask")
def ask(
    body: AskBody,
    session: auth.Session = Depends(auth.require_student),
    retriever: Retriever = Depends(get_retriever),
    embedder: Callable[[str], np.ndarray] = Depends(get_embedder),
    completer: Callable[..., str] = Depends(get_completer),
) -> dict[str, Any]:
    question = clean_question(body.question)
    course = clean_course(body.course)
    limits.check_ask_rate(auth.visitor_key(session))
    content = storage.store.get_or_503()
    started = time.monotonic()
    try:
        result, info = answer(question, course, content, retriever, embedder, completer)
    except RetrievalNotReady:
        raise HTTPException(503, "retrieval not implemented yet")
    latency = int((time.monotonic() - started) * 1000)
    limits.log_question(
        question, info["top_score"], result["covered"], info["provider"], info["model"], latency, course
    )
    return result


@app.get("/api/audio")
async def audio(
    t: str = Query(..., max_length=4000),
    s: str = Query(..., max_length=100),
    v: str = Query("", max_length=40),
    _: auth.Session = Depends(auth.require_student),
):
    text = speech.verify(t, v, s)
    if text is None:
        raise HTTPException(403, "This audio link is not valid.")
    voice = settings_store.voice_id()
    if voice is None:
        raise HTTPException(404, "The voice is turned off. Captions only.")
    if speech.voice_tag(voice) != v:
        raise HTTPException(403, "This audio link is from an older voice setting. Please ask again.")
    if not limits.take_voice_chars(len(text), settings_store.daily_voice_char_cap()):
        raise HTTPException(429, "The voice has reached today's limit. Captions only for now.")
    try:
        client, resp = await speech.open_stream(text, voice)
    except speech.VoiceError as exc:
        config.log.warning("voice failed: %s", exc)
        raise HTTPException(502, "The voice service is not available right now. Captions only.") from exc
    return StreamingResponse(
        speech.stream_bytes(client, resp),
        media_type="audio/mpeg",
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/api/files/{path:path}")
def dev_file(
    path: str,
    exp: int = Query(...),
    sig: str = Query(..., max_length=64),
    _: auth.Session = Depends(auth.require_student),
):
    """Local dev only (CONTENT_DIR set): serve slide images and clips behind the cookie."""
    if config.content_dir() is None:
        raise HTTPException(404, "Not found.")
    if not storage.verify_dev_link(path, exp, sig):
        raise HTTPException(403, "This link has expired. Please ask again.")
    target = storage.local_file(path)
    if target is None:
        raise HTTPException(404, "Not found.")
    return FileResponse(target, headers={"Cache-Control": "private, max-age=3600"})


from .admin import router as admin_router  # noqa: E402  (admin imports answer() from here)

app.include_router(admin_router)
