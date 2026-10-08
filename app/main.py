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
from urllib.parse import urlparse

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from . import (
    auth,
    config,
    edge_voice,
    embed,
    faq,
    limits,
    llm,
    logistics,
    narration,
    playlist,
    retrieval,
    settings_store,
    speech,
    storage,
    voices,
)
from .storage import Content

app = FastAPI(title="Faculty Twin", docs_url=None, redoc_url=None, openapi_url=None)

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _allowed_hosts(request: Request) -> set[str]:
    hosts = {request.headers.get("host", "")}
    site = config.env("PUBLIC_SITE_URL")
    if site:
        hosts.add(urlparse(site).netloc)
    return {h.lower() for h in hosts if h}


def cross_site(request: Request) -> bool:
    """True when a browser says this state-changing request came from another site.

    SameSite=Lax already keeps the cookies off cross-site POSTs; this also
    covers sibling subdomains (same-site but cross-origin) and old browsers.
    Requests with neither header (curl, tests, server-to-server) carry no
    ambient browser cookies an attacker could ride on, so they pass.
    """
    origin = request.headers.get("origin")
    if origin is not None:
        return origin == "null" or urlparse(origin).netloc.lower() not in _allowed_hosts(request)
    fetch_site = request.headers.get("sec-fetch-site")
    return fetch_site is not None and fetch_site not in ("same-origin", "none")


@app.middleware("http")
async def _guard(request: Request, call_next):
    if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/") and cross_site(request):
        return JSONResponse({"detail": "Cross-site requests are not allowed."}, status_code=403)
    response = await call_next(request)
    for name, value in API_SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    # Playlists carry short-lived signed links; nothing JSON should be cached.
    response.headers.setdefault("Cache-Control", "no-store")
    return response


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


# What answered a question, as written to question_log.kind (docs/SPEC.md, Data formats).
STORED_TOPIC = "stored_topic"
NOT_COVERED = "not_covered"
LOG_KINDS = (logistics.COURSE_CONTENT, STORED_TOPIC, faq.KIND, logistics.LOGISTICS, NOT_COVERED)


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
    """Run retrieval + narration. Returns (playlist, info for logging).

    `info["kind"]` says which path answered (one of LOG_KINDS). `info["provider"]`
    and `info["model"]` stay None unless a model was called for this question
    (the logistics classifier or narration), so the question log is honest.
    """
    if provider is None or model is None:
        provider, model = settings_store.llm_choice()
    voice = voices.for_answer()
    info: dict[str, Any] = {"provider": None, "model": None, "top_score": None, "narration": None, "kind": None}

    def used_model() -> None:
        info["provider"], info["model"] = provider, model

    stored = _stored_topic(content, question, course)
    if stored is not None:
        info["narration"] = "stored"
        info["kind"] = STORED_TOPIC
        return _replay_topic(content, question, stored, voice), info

    # Course FAQ (spec step 3a): Ben's own written answers, before any embedding or model call.
    hit = faq.match(question, course)
    if hit is not None:
        info["kind"] = faq.KIND
        info["faq_id"] = hit.entry.id
        return faq.reply(question, course, hit, _suggested_questions(content, course)), info

    records, matrix = playlist.searchable(content, course)
    _check_retrieval_ready(retriever, matrix.shape[1])
    if not records:
        info["kind"] = NOT_COVERED
        return playlist.not_covered(question), info
    try:
        qvec = embedder(question)
    except embed.EmbeddingCapReached as exc:
        config.log.warning("embedding cap reached")
        raise HTTPException(
            503, "The twin has answered as many new questions as it can today. Please try again tomorrow."
        ) from exc
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
        info["kind"] = NOT_COVERED
        return playlist.not_covered(question), info

    # Logistics check (spec step 7a): meetings, absences, grades, deadlines and Canvas go to Ben.
    kind = logistics.classify(question, completer, provider=provider, model=model)
    info["kind"] = kind.kind
    info["kind_source"] = kind.source
    if kind.source != "keyword":  # "llm", or "error" after a call was tried
        used_model()
    if kind.kind == logistics.LOGISTICS:
        referral = logistics.referral(question, _suggested_questions(content, course))
        referral["links"] = [dict(faq.CALENDLY)]
        referral["contacts"] = faq.ta_contacts(course)
        return referral, info

    codes = {r["id"]: (playlist.related_code(content, r) or {}).get("source") for r in chosen}
    used_model()
    result = narration.narrate(question, chosen, codes, provider=provider, model=model, complete=completer)
    info["narration"] = result.source
    info["errors"] = result.errors
    return playlist.build_playlist(content, question, chosen, result.narrations, result.follow_ups, voice), info


def _check_retrieval_ready(retriever: Retriever, dim: int) -> None:
    """Fail fast with "retrieval not implemented yet" before spending an embedding call.

    Runs the ranking function on an empty matrix: no math happens, but the stub raises.
    """
    try:
        retriever.rank(np.ones(dim, dtype=np.float32), np.zeros((0, dim), dtype=np.float32))
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc
    except Exception:  # an empty matrix is an odd input; the real call will tell
        pass


def _suggested_questions(content: Content, course: Optional[str]) -> list[str]:
    """Suggested course questions to offer after a logistics referral."""
    out = []
    for topic in content.topics:
        if isinstance(topic, dict) and topic.get("question") and (course is None or topic.get("course") in (None, course)):
            out.append(str(topic["question"]))
    return out[: logistics.MAX_FOLLOW_UPS]


def _stored_topic(content: Content, question: str, course: Optional[str]) -> Optional[dict[str, Any]]:
    key = _norm(question)
    for topic in content.topics:
        if not isinstance(topic, dict) or not topic.get("playlist"):
            continue
        if _norm(str(topic.get("question", ""))) == key and (course is None or topic.get("course") in (None, course)):
            return topic
    return None


def _replay_topic(content: Content, question: str, topic: dict[str, Any], voice: voices.Plan) -> dict[str, Any]:
    """Rebuild a pre-generated playlist with fresh signed links."""
    stored = topic["playlist"]
    segs = [s for s in stored.get("segments", []) if content.record(s.get("slide_id", ""))]
    chosen = [content.record(s["slide_id"]) for s in segs]
    narrations = {s["slide_id"]: s.get("narration", "") for s in segs}
    result = playlist.build_playlist(content, question, chosen, narrations, stored.get("follow_ups", []), voice)
    if voice.primary is None:
        return result  # captions only
    # Stored mp3s live at audio/<voice tag>/<hash>.mp3 (older ElevenLabs ones at audio/<voice id>/);
    # only use one made with the voice that is speaking, so the label stays true.
    prefixes = voice.primary.audio_prefixes()
    paths = {s["slide_id"]: str(s.get("audio_path") or "").lstrip("/") for s in segs}
    usable = {k: p for k, p in paths.items() if p.startswith(prefixes)}
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


@app.get("/api/voice")
def voice_info(_: auth.Session = Depends(auth.require_student)) -> dict[str, Any]:
    """What students are told about the voice: `{kind, label, fallback: {kind, label} | null}`.

    kind is "clone", "stock", "free", "unverified", or "none" (captions only, label null).
    """
    return voices.for_answer().public()


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
    request: Request,
    session: auth.Session = Depends(auth.require_student),
    retriever: Retriever = Depends(get_retriever),
    embedder: Callable[[str], np.ndarray] = Depends(get_embedder),
    completer: Callable[..., str] = Depends(get_completer),
) -> dict[str, Any]:
    question = clean_question(body.question)
    course = clean_course(body.course)
    limits.check_ask_rate(auth.visitor_key(session), limits.client_hash(request))
    content = storage.store.get_or_503()
    started = time.monotonic()
    try:
        result, info = answer(question, course, content, retriever, embedder, completer)
    except RetrievalNotReady:
        raise HTTPException(503, "retrieval not implemented yet")
    latency = int((time.monotonic() - started) * 1000)
    limits.log_question(
        question, info["top_score"], result["covered"], info["provider"], info["model"], latency, course,
        kind=info.get("kind"),
    )
    return result


@app.get("/api/audio")
async def audio(
    request: Request,
    t: str = Query(..., max_length=4000),
    s: str = Query(..., max_length=100),
    v: str = Query("", max_length=40),
    session: auth.Session = Depends(auth.require_student),
):
    text = speech.verify(t, v, s)  # the signature covers the text and the voice tag
    if text is None:
        raise HTTPException(403, "This audio link is not valid.")
    plan = voices.current(resolve_kind=False)
    if plan.primary is None:
        raise HTTPException(404, "The voice is turned off. Captions only.")
    voice = plan.match(v)
    if voice is None:
        raise HTTPException(403, "This audio link is from an older voice setting. Please ask again.")
    # Each tier has its own daily cap (ElevenLabs costs money; the free voices are capped higher).
    if not limits.take_voice_chars(
        len(text), voices.daily_cap(voice), auth.visitor_key(session), limits.client_hash(request), pool=voice.pool
    ):
        raise HTTPException(429, "The voice has reached today's limit. Captions only for now.")
    headers = {"Cache-Control": "private, max-age=86400"}
    if voice.provider == voices.EDGE:
        try:
            stream = await edge_voice.open_stream(text, voice.voice_id)
        except edge_voice.FreeVoiceError as exc:
            config.log.warning("free voice failed: %s", exc)
            raise HTTPException(502, "The voice service is not available right now. Captions only.") from exc
        return StreamingResponse(stream, media_type="audio/mpeg", headers=headers)
    try:
        client, resp = await speech.open_stream(text, voice.voice_id)
    except speech.VoiceError as exc:
        config.log.warning("voice failed: %s", exc)
        raise HTTPException(502, "The voice service is not available right now. Captions only.") from exc
    return StreamingResponse(speech.stream_bytes(client, resp), media_type="audio/mpeg", headers=headers)


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
