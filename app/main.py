"""Faculty Twin backend: one FastAPI app, deployed on Vercel as a single function.

Every route except /api/health and /api/login needs the student cookie (an
admin cookie also works, so Ben can preview). Settings routes live in
app/admin.py and need the admin cookie.
"""

from __future__ import annotations

import contextlib
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import (
    auth,
    config,
    course_info,
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
    thresholds,
    usage,
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
    if usage.pending_count():
        # Usage counters are written off the request path; finish them after the response is sent,
        # so the function is not frozen with writes still pending.
        previous = response.background

        async def finish() -> None:
            if previous is not None:
                await previous()
            await run_in_threadpool(usage.flush, 3.0)

        response.background = BackgroundTask(finish)
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
    # The threshold is read per request (Settings override, else Ben's value in retrieval.py).
    return Retriever(retrieval.rank, retrieval.select_segments, thresholds.slide_threshold())


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
    source: Optional[str] = Field(None, max_length=20)  # chip | typed | follow_up; anything else is ignored


class EventBody(BaseModel):
    name: str = Field(..., max_length=40)


def question_source(request: Request, sent: Optional[str]) -> Optional[str]:
    """Where a question came from, for the log. The X-FT-Source header may only claim a test source
    (smoke, eval, prompt_test): it is a tag for analytics and changes nothing else."""
    header = (request.headers.get("x-ft-source") or "").strip().lower()
    if header in usage.HEADER_SOURCES:
        return header
    return sent if sent in usage.QUESTION_SOURCES else None


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
LOG_KINDS = (logistics.COURSE_CONTENT, STORED_TOPIC, faq.KIND, course_info.KIND, logistics.LOGISTICS, NOT_COVERED)


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
    info_records, info_matrix = course_info.searchable(content, course)
    _check_retrieval_ready(retriever, matrix.shape[1])
    if not records and not info_records:
        info["kind"] = NOT_COVERED
        return playlist.not_covered(question), info
    try:
        qvec = embedder(question)  # once: the same vector scores the slides and the course-info chunks
    except embed.EmbeddingCapReached as exc:
        config.log.warning("embedding cap reached")
        raise HTTPException(
            503, "The twin has answered as many new questions as it can today. Please try again tomorrow."
        ) from exc
    except embed.EmbeddingError as exc:
        config.log.warning("embedding failed: %s", exc)
        raise HTTPException(503, "The search service is not available right now. Please try again shortly.") from exc
    if records and qvec.shape[-1] != matrix.shape[1]:
        config.log.error("question vector dim %s != index dim %s", qvec.shape, matrix.shape)
        raise HTTPException(503, "The search index does not match the embedding model. Ben needs to rebuild it.")
    if info_matrix is not None and qvec.shape[-1] != info_matrix.shape[1]:
        config.log.error("question vector dim %s != course-info index dim %s", qvec.shape, info_matrix.shape)
        info_records, info_matrix = [], None  # a stale info index never blocks slide answers
    try:
        ranked = retriever.rank(qvec, matrix) if records else []
        info_ranked = retriever.rank(qvec, info_matrix) if info_records else []
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc
    best_slide = float(ranked[0][1]) if ranked else None
    info["top_score"] = best_slide
    info["top_slide_id"] = records[ranked[0][0]].get("id") if ranked else None  # Analytics topics

    # Course info from Canvas (spec step 6a): wins when it clears the info threshold and beats every slide.
    hits = course_info.top_hits(info_ranked, info_records)
    if hits and hits[0].score >= course_info.threshold() and (best_slide is None or hits[0].score > best_slide):
        used_model()
        with usage.purpose("course_info"):
            result = course_info.answer(
                question, course, hits, _suggested_questions(content, course), completer, provider=provider, model=model
            )
        info["kind"] = course_info.KIND
        info["top_slide_id"] = None  # answered from Canvas, not a slide
        info["top_score"] = hits[0].score
        info["narration"] = result.source
        info["errors"] = result.errors
        return result.reply, info

    if not records:
        info["kind"] = NOT_COVERED
        return playlist.not_covered(question), info
    try:
        chosen = retriever.select_segments(ranked, records, retriever.threshold)
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc
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
    source = question_source(request, body.source)
    test_purpose = usage.TEST_PURPOSES.get(source or "")
    started = time.monotonic()
    with usage.tally() as spent, (
        usage.purpose(test_purpose, sticky=True) if test_purpose else contextlib.nullcontext()
    ):
        try:
            result, info = answer(question, course, content, retriever, embedder, completer)
        except RetrievalNotReady:
            raise HTTPException(503, "retrieval not implemented yet")
    latency = int((time.monotonic() - started) * 1000)
    limits.log_question(
        question, info["top_score"], result["covered"], info["provider"], info["model"], latency, course,
        kind=info.get("kind"), source=source, tokens_in=spent.tokens_in, tokens_out=spent.tokens_out,
        **_log_extras(content, result, info),
    )
    if info.get("faq_id") and not test_purpose:
        usage.record_faq(info["faq_id"])
    return result


def _log_extras(content: Content, result: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    """Analytics columns for the question log: the top slide, its session, and the voice characters signed."""
    segments = result.get("segments") or []
    top = info.get("top_slide_id") or next((s.get("slide_id") for s in segments if s.get("slide_id")), None)
    rec = content.record(top) if top else None
    return {
        "top_slide_id": top,
        "session": (rec or {}).get("session"),
        "session_title": (rec or {}).get("session_title"),
        # Live voice only (/api/audio links); stored answers play pre-made mp3s.
        "voice_chars": sum(len(s.get("narration") or "") for s in segments
                           if str(s.get("audio") or "").startswith("/api/audio")),
    }


EVENT_PER_MINUTE = 60
EVENT_PER_DAY = 2000


@app.post("/api/event", status_code=204)
def event(body: EventBody, session: auth.Session = Depends(auth.require_student)) -> None:
    """One student-page event for Settings > Analytics: an allowlisted name, nothing else (no text, no ids)."""
    if body.name not in usage.EVENT_NAMES:
        raise HTTPException(400, "Unknown event.")
    visitor = auth.visitor_key(session)
    t = time.gmtime()
    for key, cap, ttl in (
        (f"rl:event-min:{visitor}:{time.strftime('%Y%m%d%H%M', t)}", EVENT_PER_MINUTE, 300),
        (f"rl:event-day:{visitor}:{time.strftime('%Y%m%d', t)}", EVENT_PER_DAY, 172800),
    ):
        ok, _ = limits.increment(key, 1, cap=cap, ttl_seconds=ttl)
        if not ok:
            raise HTTPException(429, "Too many events.")
    usage.record_event(body.name)


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
    usage.record_tts(voice.kind or "unverified", len(text))  # characters sent, by voice tier (Analytics)
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

from .analytics import router as analytics_router  # noqa: E402

app.include_router(admin_router)
app.include_router(thresholds.router)
app.include_router(analytics_router)

from .admin_evals import router as admin_evals_router  # noqa: E402  (Settings > Evals)

app.include_router(admin_evals_router)
