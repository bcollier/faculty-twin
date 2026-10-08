"""Faculty Twin backend: one FastAPI app, deployed on Vercel as a single function.

Every route except /api/health and /api/login needs the student cookie (an
admin cookie also works, so Ben can preview). Settings routes live in
app/admin/ and need the admin cookie.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import numpy as np
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from . import (
    alerts,
    auth,
    config,
    course_info,
    edge_voice,
    embed,
    faq,
    helper_slide,
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
    timings,
    usage,
    voices,
    web_answer,
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
    select_segments: Callable[[list[tuple[int, float]], list[dict[str, Any]], float | None], list[dict[str, Any]]]
    threshold: float | None


def get_retriever() -> Retriever:
    # The threshold is read per request (Settings override, else Ben's value in retrieval.py).
    return Retriever(retrieval.rank, retrieval.select_segments, thresholds.slide_threshold())


def get_embedder() -> Callable[[str], np.ndarray]:
    return embed.embed_question


def get_completer() -> Callable[..., str]:
    return llm.complete_json


def get_searcher() -> Callable[..., web_answer.WebReply]:
    """The web search call for beyond-the-slides answers. Tests swap in a TEST FAKE."""
    return web_answer.search


def get_content() -> Content:
    return storage.store.get_or_503()


# ---------------------------------------------------------------- request bodies

class PasscodeBody(BaseModel):
    passcode: str


class AskBody(BaseModel):
    """POST /api/ask: the question, an optional course filter, and where the question came from."""

    question: str
    course: str | None = None
    source: str | None = Field(None, max_length=20)  # chip | typed | follow_up; anything else is ignored


class EventBody(BaseModel):
    name: str = Field(..., max_length=40)


class LinksBody(BaseModel):
    slide_ids: list[str] = Field(..., max_length=playlist.LINKS_MAX_SLIDES)


def question_source(request: Request, sent: str | None) -> str | None:
    """Where a question came from, for the log. The X-FT-Source header may only claim a test source
    (smoke, eval, prompt_test): it is a tag for analytics and changes nothing else."""
    header = (request.headers.get("x-ft-source") or "").strip().lower()
    if header in usage.HEADER_SOURCES:
        return header
    return sent if sent in usage.QUESTION_SOURCES else None


def clean_question(raw: str) -> str:
    """The question with whitespace collapsed; 400 when it is empty or over QUESTION_MAX_CHARS."""
    question = re.sub(r"\s+", " ", raw or "").strip()
    if not question:
        raise HTTPException(400, "Please type a question.")
    if len(question) > config.QUESTION_MAX_CHARS:
        raise HTTPException(400, f"Please keep questions under {config.QUESTION_MAX_CHARS} characters.")
    return question


def clean_course(raw: str | None) -> str | None:
    """A course code or None for both; 400 for anything else."""
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
CROSS_COURSE = playlist.CROSS_COURSE  # the other course's slides answered (spec step 7c)
LOG_KINDS = (logistics.COURSE_CONTENT, STORED_TOPIC, faq.KIND, course_info.KIND, logistics.LOGISTICS,
             web_answer.KIND, CROSS_COURSE, NOT_COVERED, alerts.KIND)


def answer(
    question: str,
    course: str | None,
    content: Content,
    retriever: Retriever,
    embedder: Callable[[str], np.ndarray],
    completer: Callable[..., str],
    provider: str | None = None,
    model: str | None = None,
    visitor: str | None = None,
    searcher: Callable[..., Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run retrieval + narration. Returns (playlist, info for logging).

    `info["kind"]` says which path answered (one of LOG_KINDS). `info["provider"]`
    and `info["model"]` stay None unless a model was called for this question
    (the logistics classifier, the web scope check, a web answer, or narration),
    so the question log is honest. `searcher` is the web search call for step 7b
    (default `web_answer.search`).

    The routes run cheapest first, and the first one that answers wins (docs/ARCHITECTURE.md,
    "How a question is routed"). The first three need no embedding; the question is embedded
    once, then the rest share that one vector.
    """
    if provider is None or model is None:
        provider, model = settings_store.llm_choice()
    ask = _Ask(question, course, content, retriever, embedder, completer, provider, model, visitor, searcher,
               voice=voices.for_answer())
    for route in (_instructor_alert, _stored_topic_route, _course_faq, _search, _personal_request, _canvas_answer):
        reply = route(ask)
        if reply is not None:
            return reply, ask.info
    return _slides_answer(ask), ask.info


@dataclass
class _Ask:
    """One question on its way through answer(): what came in, and what the search found."""

    question: str
    course: str | None
    content: Content
    retriever: Retriever
    embedder: Callable[[str], np.ndarray]
    completer: Callable[..., str]
    provider: str
    model: str
    visitor: str | None
    searcher: Callable[..., Any] | None
    voice: voices.Plan
    info: dict[str, Any] = field(default_factory=lambda: {
        "provider": None, "model": None, "top_score": None, "narration": None, "kind": None})
    # Filled by _search: the visible slides and Canvas chunks, and how the question ranks against each.
    records: list[dict[str, Any]] = field(default_factory=list)
    info_records: list[dict[str, Any]] = field(default_factory=list)
    ranked: list[tuple[int, float]] = field(default_factory=list)
    info_ranked: list[tuple[int, float]] = field(default_factory=list)
    best_slide: float | None = None
    qvec: np.ndarray | None = None
    # Filled by _other_course when a course filter found nothing: every course's visible slides, ranked.
    all_records: list[dict[str, Any]] = field(default_factory=list)
    all_ranked: list[tuple[int, float]] = field(default_factory=list)

    def used_model(self) -> None:
        """Record the model in the log row: only routes that called one do this."""
        self.info["provider"], self.info["model"] = self.provider, self.model

    def suggested(self) -> list[str]:
        return _suggested_questions(self.content, self.course)


def _instructor_alert(ask: _Ask) -> dict[str, Any] | None:
    """Wins when a student reports a broken quiz, submission or API key: it texts Ben, then stops.

    Spec "Instructor alerts". Only a student request (a visitor) can text; tests and evals run it as a dry run.
    """
    incident = alerts.check(ask.question, ask.course, ask.content, ask.completer, ask.provider, ask.model,
                            visitor=ask.visitor)
    if incident is None:
        return None
    ask.info.update(incident.info)
    return incident.reply


def _stored_topic_route(ask: _Ask) -> dict[str, Any] | None:
    """Wins when the question is a suggested question: its stored walkthrough replays (no search, no model)."""
    stored = _stored_topic(ask.content, ask.question, ask.course)
    if stored is None:
        return None
    ask.info["narration"] = "stored"
    ask.info["kind"] = STORED_TOPIC
    return _replay_topic(ask.content, ask.question, stored, ask.voice)


def _course_faq(ask: _Ask) -> dict[str, Any] | None:
    """Wins when Ben's course FAQ matches (spec step 3a): his own written answer, before any embedding."""
    hit = faq.match(ask.question, ask.course)
    if hit is None:
        return None
    ask.info["kind"] = faq.KIND
    ask.info["faq_id"] = hit.entry.id
    return faq.reply(ask.question, ask.course, hit, ask.suggested())


def _search(ask: _Ask) -> dict[str, Any] | None:
    """Embed the question once and rank the slides and the Canvas chunks with that one vector.

    Answers (not covered) only when there is nothing to search at all; otherwise it fills in the
    rankings for the routes after it and returns None.
    """
    records, matrix = playlist.searchable(ask.content, ask.course)
    info_records, info_matrix = course_info.searchable(ask.content, ask.course)
    _check_retrieval_ready(ask.retriever, matrix.shape[1])
    if not records and not info_records:
        ask.info["kind"] = NOT_COVERED
        return playlist.not_covered(ask.question)
    qvec = _embed(ask)
    if records and qvec.shape[-1] != matrix.shape[1]:
        config.log.error("question vector dim %s != index dim %s", qvec.shape, matrix.shape)
        raise HTTPException(503, "The search index does not match the embedding model. Ben needs to rebuild it.")
    if info_matrix is not None and qvec.shape[-1] != info_matrix.shape[1]:
        config.log.error("question vector dim %s != course-info index dim %s", qvec.shape, info_matrix.shape)
        info_records, info_matrix = [], None  # a stale info index never blocks slide answers
    try:
        ranked = ask.retriever.rank(qvec, matrix) if records else []
        info_ranked = ask.retriever.rank(qvec, info_matrix) if info_records else []
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc
    ask.records, ask.info_records, ask.ranked, ask.info_ranked = records, info_records, ranked, info_ranked
    ask.qvec = qvec
    ask.best_slide = float(ranked[0][1]) if ranked else None
    ask.info["top_score"] = ask.best_slide
    ask.info["top_slide_id"] = records[ranked[0][0]].get("id") if ranked else None  # Analytics topics
    return None


def _embed(ask: _Ask) -> np.ndarray:
    """The question vector. A spent daily cap or a Voyage failure is a 503 the page can explain."""
    try:
        return ask.embedder(ask.question)
    except embed.EmbeddingCapReached as exc:
        config.log.warning("embedding cap reached")
        raise HTTPException(
            503, "The twin has answered as many new questions as it can today. Please try again tomorrow."
        ) from exc
    except embed.EmbeddingError as exc:
        config.log.warning("embedding failed: %s", exc)
        raise HTTPException(503, "The search service is not available right now. Please try again shortly.") from exc


def _personal_request(ask: _Ask) -> dict[str, Any] | None:
    """Wins for a request only Ben can act on (a regrade, an extension, an absence): never Canvas or slides.

    Spec steps 6a and 7a. Keyword check only: no model call.
    """
    if not logistics.personal_request(ask.question):
        return None
    ask.info["kind"], ask.info["kind_source"] = logistics.LOGISTICS, "keyword"
    return _referral(ask.question, ask.course, ask.content)


def _canvas_answer(ask: _Ask) -> dict[str, Any] | None:
    """Wins when the best Canvas chunk clears the info threshold and beats the best slide by the info margin.

    Spec step 6a. A question about where something is on Canvas only needs the threshold
    (course_info.canvas_request). Otherwise the slides answer, or, when no slide clears its
    threshold, the web path (step 7b) gets its turn even though a weaker Canvas chunk exists.
    """
    hits = course_info.top_hits(ask.info_ranked, ask.info_records, question=ask.question)
    best_info = float(ask.info_ranked[0][1]) if ask.info_ranked else None
    canvas = course_info.canvas_request(ask.question)
    if not (hits and course_info.wins(best_info, ask.best_slide, canvas=canvas)):
        return None
    ask.used_model()
    with usage.purpose("course_info"):
        result = course_info.answer(ask.question, ask.course, hits, ask.suggested(), ask.completer,
                                    provider=ask.provider, model=ask.model)
    info = ask.info
    info["kind"] = course_info.KIND
    info["top_slide_id"] = None  # answered from Canvas, not a slide
    info["top_score"] = best_info
    info["narration"] = result.source
    info["errors"] = result.errors
    info["fallback_reason"] = result.reason  # shown in Settings > Activity when it fell back
    return result.reply


def _slides_answer(ask: _Ask) -> dict[str, Any]:
    """The last route: Ben's select_segments() picks the slides; else the other course, the web path, or not covered.

    With a course filter and nothing picked in that course, the other course's slides get a turn first
    (spec step 7c). With slides chosen, the logistics check (spec step 7a) may still send the question to Ben;
    otherwise one narration call explains the slides, with the helper-slide check beside it.
    """
    info = ask.info
    chosen = _select(ask, ask.ranked, ask.records) if ask.records else []
    cross = False
    if not chosen:
        chosen = _other_course(ask)
        cross = bool(chosen)
    if not chosen:
        info["kind"] = NOT_COVERED
        if not ask.records:
            return playlist.not_covered(ask.question)
        # Beyond the slides (spec step 7b): a course-adjacent question may get a web answer. Its closest slides
        # come from every course (they are links to look at): with a filter, _other_course ranked them all.
        records, ranked = (ask.all_records, ask.all_ranked) if ask.all_records else (ask.records, ask.ranked)
        reply, _ = _beyond_the_slides(ask.question, ask.course, ask.content, records, ranked, ask.completer,
                                      ask.searcher or get_searcher(), ask.provider, ask.model, info, ask.used_model)
        return reply

    # Logistics check (spec step 7a): meetings, absences, grades, deadlines and Canvas go to Ben.
    kind = logistics.classify(ask.question, ask.completer, provider=ask.provider, model=ask.model)
    info["kind"] = kind.kind
    info["kind_source"] = kind.source
    if kind.source != "keyword":  # "llm", or "error" after a call was tried
        ask.used_model()
    if kind.kind == logistics.LOGISTICS:
        return _referral(ask.question, ask.course, ask.content)
    if cross:
        info["kind"] = CROSS_COURSE

    codes = {r["id"]: (playlist.related_code(ask.content, r) or {}).get("source") for r in chosen}
    ask.used_model()
    # The helper-slide self-check runs beside narration, so it adds no wait (spec "AI-drawn helper slides").
    helper = _start_helper(ask.question, "check", helper_slide.slide_material(chosen), ask.completer, ask.provider,
                           ask.model)
    result = narration.narrate(ask.question, chosen, codes, provider=ask.provider, model=ask.model,
                               complete=ask.completer)
    info["narration"] = result.source
    info["errors"] = result.errors
    reply = playlist.build_playlist(ask.content, ask.question, chosen, result.narrations, result.follow_ups, ask.voice)
    if cross:
        playlist.mark_cross_course(reply, str(ask.course), chosen)
    _attach_helper(reply, helper, info)
    return reply


def _select(ask: _Ask, ranked: list[tuple[int, float]], records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ben's select_segments() with the threshold in use (Settings override, else his value)."""
    try:
        return ask.retriever.select_segments(ranked, records, ask.retriever.threshold)
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc


def _rank_all_courses(ask: _Ask) -> None:
    """Rank every course's visible slides with the question vector already made (no second embedding)."""
    records, matrix = playlist.searchable(ask.content, None)
    if not records or ask.qvec is None or ask.qvec.shape[-1] != matrix.shape[1]:
        return
    try:
        ask.all_ranked = ask.retriever.rank(ask.qvec, matrix)
    except NotImplementedError as exc:
        raise RetrievalNotReady() from exc
    ask.all_records = records


def _other_course(ask: _Ask) -> list[dict[str, Any]]:
    """Spec step 7c: a course filter found nothing over the threshold, so try the other course's slides.

    Added Oct 8 (live bug): with the filter on 45-884, the frames and semantic networks question (taught in 70-445,
    best slide 0.594) got a web answer, because its best 45-884 slide scored 0.412. Same vector, same rank(), same
    threshold and select_segments() over the unfiltered records, with the filtered course's pairs left out.
    """
    if ask.course is None:
        return []
    _rank_all_courses(ask)
    others = playlist.other_course_ranked(ask.all_ranked, ask.all_records, ask.course)
    if not others:
        return []
    chosen = [r for r in _select(ask, others, ask.all_records) if str(r.get("course")) != ask.course]
    if chosen:
        best, score = ask.all_records[others[0][0]], float(others[0][1])
        config.log.info("cross-course answer: %s best %.3f, %s best %s %.3f", ask.course,
                        ask.best_slide if ask.best_slide is not None else float("nan"), best.get("course"),
                        best.get("id"), score)
        # The log row shows the slides that answered, so a covered row never carries the filtered course's low score.
        ask.info["top_score"], ask.info["top_slide_id"] = score, best.get("id")
    return chosen


_helper_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ft-helper")


def _start_helper(question: str, mode: str, material: list[dict[str, Any]], completer: Callable[..., str],
                  provider: str, model: str):
    """Start the helper-slide call in this request's context (usage purpose and tally carry over).

    Eval runs skip it: they grade the answer, and the extra call would only spend the eval budget.
    """
    if not helper_slide.enabled() or usage.current_purpose() == "eval_generate":
        return None
    ctx = contextvars.copy_context()
    return _helper_pool.submit(ctx.run, helper_slide.for_answer, question, mode, material, completer, provider, model)


def _attach_helper(reply: dict[str, Any], future, info: dict[str, Any], timeout: float = 25.0) -> None:
    """Add `generated_slide` when the helper call produced one. A slow or failed call adds nothing."""
    if future is None:
        return
    try:
        slide = future.result(timeout=timeout)
    except Exception as exc:  # timeout or anything else: the answer goes out without a helper slide
        config.log.info("helper slide not attached: %s", type(exc).__name__)
        return
    if slide:
        reply["generated_slide"] = slide
        info["helper_slide"] = slide.get("origin")


def _referral(question: str, course: str | None, content: Content) -> dict[str, Any]:
    """The logistics referral (spec step 7a): Ben's words, the Calendly button, the TA cards."""
    referral = logistics.referral(question, _suggested_questions(content, course))
    referral["links"] = [dict(faq.CALENDLY)]
    referral["contacts"] = faq.ta_contacts(course)
    return referral


def _beyond_the_slides(
    question: str,
    course: str | None,
    content: Content,
    records: list[dict[str, Any]],
    ranked: list[tuple[int, float]],
    completer: Callable[..., str],
    searcher: Callable[..., Any],
    provider: str,
    model: str,
    info: dict[str, Any],
    used_model: Callable[[], None],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Spec step 7b: no slide covers the question. Off: the usual decline. On: scope check, then maybe the web."""
    declined = playlist.not_covered(question)
    if not web_answer.enabled():
        return declined, info
    scope = web_answer.classify(question, completer, provider=provider, model=model)
    info["web_scope"], info["web_scope_source"] = scope.scope, scope.source
    if scope.source != "keyword":
        used_model()
    if scope.scope == web_answer.LOGISTICS:
        info["kind"] = logistics.LOGISTICS
        return _referral(question, course, content), info
    if scope.scope != web_answer.COURSE_ADJACENT:
        return declined, info
    if not web_answer.take_budget():  # today's web answers are used up (or the cap is 0)
        info["web_capped"] = True
        return declined, info
    used_model()
    related = web_answer.related_slides(content, ranked, records)
    with usage.purpose("web_answer"):
        result = web_answer.answer(question, related, _suggested_questions(content, course), searcher,
                                   provider=provider, model=model)
    if result.reason:
        info["fallback_reason"] = result.reason  # shown in Settings > Activity (e.g. provider_credits)
    if result.reply is None:  # nothing at all to point to
        return declined, info
    if result.source == "llm":  # a web answer can come with one AI-drawn slide, drawn from that answer only
        material = [{"web_answer": result.reply.get("message"),
                     "sources": [link["url"] for link in result.reply.get("links") or []]}]
        _attach_helper(result.reply, _start_helper(question, "draft", material, completer, provider, model), info)
    info["kind"] = web_answer.KIND
    info["narration"] = result.source
    info["errors"] = result.errors
    return result.reply, info


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


def _suggested_questions(content: Content, course: str | None) -> list[str]:
    """Suggested course questions to offer after a logistics referral."""
    out = []
    for topic in content.topics:
        if isinstance(topic, dict) and topic.get("question") and _for_course(topic, course):
            out.append(str(topic["question"]))
    return out[: logistics.MAX_FOLLOW_UPS]


def _for_course(topic: dict[str, Any], course: str | None) -> bool:
    """A suggested question shows for every course when either side has none."""
    return course is None or topic.get("course") in (None, course)


def _stored_topic(content: Content, question: str, course: str | None) -> dict[str, Any] | None:
    """The suggested question with the same words (ignoring case and punctuation), if it can still replay."""
    key = _norm(question)
    for topic in content.topics:
        if not isinstance(topic, dict) or not topic.get("playlist"):
            continue
        if _norm(str(topic.get("question", ""))) == key and _for_course(topic, course):
            # Only when a stored slide can still be shown; otherwise the question is answered live.
            return topic if _replayable(content, topic) else None
    return None


def _replayable(content: Content, topic: dict[str, Any]) -> list[dict[str, Any]]:
    """The stored segments whose slide is still in the index and not in a session hidden in Settings.

    Added Oct 8 (code review): replays skipped the hidden-session filter that live answers use.
    """
    hidden = playlist.hidden_sessions()
    out = []
    for seg in (topic.get("playlist") or {}).get("segments", []):
        rec = content.record(str(seg.get("slide_id") or ""))
        if rec is not None and (str(rec.get("course")), int(rec.get("session") or 0)) not in hidden:
            out.append(seg)
    return out


def _replay_topic(content: Content, question: str, topic: dict[str, Any], voice: voices.Plan) -> dict[str, Any]:
    """Rebuild a pre-generated playlist with fresh signed links."""
    stored = topic["playlist"]
    segs = _replayable(content, topic)
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
    # Read-along: each stored mp3 may have its word timings next to it (`<hash>.words.json`).
    sidecars = {p: w for p in usable.values() if (w := words_path(p))}
    audio = storage.media_urls(list(usable.values()) + list(sidecars.values()))
    for seg in result["segments"]:
        path = usable.get(seg["slide_id"])
        if path and audio.get(path):
            seg["audio"] = audio[path]
            seg["timings"] = audio.get(sidecars.get(path, ""))
    return result


def words_path(mp3_path: str) -> str | None:
    """`audio/<tag>/<hash>.mp3` -> `audio/<tag>/<hash>.words.json` (the stored clip's word timings)."""
    return mp3_path[: -len(".mp3")] + ".words.json" if mp3_path.endswith(".mp3") else None


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.post("/api/login", status_code=204)
def login(body: PasscodeBody, request: Request, response: Response) -> None:
    """Check the student passcode (rate limited per address) and set the 7-day cookie."""
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
    """The suggested questions (text and course only)."""
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
    searcher: Callable[..., Any] = Depends(get_searcher),
) -> dict[str, Any]:
    """Answer one question: rate limits, answer(), then one question-log row. 503 until retrieval exists."""
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
            result, info = answer(question, course, content, retriever, embedder, completer,
                                  visitor=None if test_purpose else auth.visitor_key(session),
                                  searcher=searcher)
        except RetrievalNotReady:
            raise HTTPException(503, "retrieval not implemented yet") from None
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
        "fallback_reason": info.get("fallback_reason"),  # course info and web answers
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


LINKS_PER_MINUTE = 10


@app.post("/api/links")
def links(body: LinksBody, session: auth.Session = Depends(auth.require_student)) -> dict[str, Any]:
    """Fresh signed image and clip links for slides on screen, after the old ones expired (1 hour).

    No model call, no question-log row: the page swaps the links into the answer it is playing.
    """
    minute = time.strftime("%Y%m%d%H%M", time.gmtime())
    ok, _ = limits.increment(f"rl:links-min:{auth.visitor_key(session)}:{minute}", 1, cap=LINKS_PER_MINUTE,
                             ttl_seconds=300)
    if not ok:
        raise HTTPException(429, "Too many link refreshes. Please wait a minute.")
    return {"links": playlist.fresh_links(storage.store.get_or_503(), body.slide_ids)}


@app.get("/api/audio")
async def audio(
    request: Request,
    t: str = Query(..., max_length=4000),
    s: str = Query(..., max_length=100),
    v: str = Query("", max_length=40),
    session: auth.Session = Depends(auth.require_student),
):
    """Stream one signed narration in the voice its link names (the student page's audio player).

    Only text the backend signed is ever spoken, in a voice the current setting still offers, and
    the characters count against that voice tier's daily cap once per visitor per day.
    """
    text = speech.verify(t, v, s)  # the signature covers the text and the voice tag
    if text is None:
        raise HTTPException(403, "This audio link is not valid.")
    plan = voices.current(resolve_kind=False)
    web_voice = web_answer.voice()  # a free voice for web answers only (never the clone), or None
    voice = plan.match(v) if plan.primary is not None else None
    if voice is None and web_voice is not None and web_voice.tag == v:
        voice = web_voice
    if voice is None:
        if plan.primary is None and web_voice is None:
            raise HTTPException(404, "The voice is turned off. Captions only.")
        raise HTTPException(403, "This audio link is from an older voice setting. Please ask again.")
    # Each tier has its own daily cap (ElevenLabs costs money; the free voices are capped higher).
    visitor, address = auth.visitor_key(session), limits.client_hash(request)
    # A signed link is charged once per visitor per day (Safari asks for one link in Range pieces).
    charged = limits.audio_play_is_charged(s, visitor)
    if charged and not limits.take_voice_chars(len(text), voices.daily_cap(voice), visitor, address,
                                               pool=voice.pool):
        raise HTTPException(429, "The voice has reached today's limit. Captions only for now.")
    headers = {"Cache-Control": "private, max-age=86400"}
    collector = timings.Collector("edge" if voice.provider == voices.EDGE else "elevenlabs")
    try:
        if voice.provider == voices.EDGE:
            stream = await edge_voice.open_stream(text, voice.voice_id, collector)
        else:
            client, resp = await speech.open_stream(text, voice.voice_id)
            stream = speech.stream_bytes(client, resp, collector)
    except (edge_voice.FreeVoiceError, speech.VoiceError) as exc:
        # Nothing was spoken: give the characters back, so an outage does not use up today's cap.
        if charged:
            limits.give_back_voice_chars(len(text), visitor, address, pool=voice.pool)
        config.log.warning("%s failed: %s", "free voice" if voice.provider == voices.EDGE else "voice", exc)
        raise HTTPException(502, "The voice service is not available right now. Captions only.") from exc
    usage.record_tts(voice.kind or "unverified", len(text))  # characters sent, by voice tier (Analytics)
    return StreamingResponse(_keep_timings(stream, collector, v, text), media_type="audio/mpeg", headers=headers)


async def _keep_timings(stream, collector: timings.Collector, tag: str, text: str):
    """Pass the audio through; once all of it was sent, save its word timings for the read-along."""
    async for chunk in stream:
        yield chunk
    if collector.failed:
        return
    words = collector.result(text)
    if words:
        await asyncio.to_thread(timings.save, tag, text, words, collector.source)


TIMINGS_PER_MINUTE = 120


@app.get("/api/audio/timings")
def audio_timings(
    t: str = Query(..., max_length=4000),
    s: str = Query(..., max_length=100),
    v: str = Query("", max_length=40),
    session: auth.Session = Depends(auth.require_student),
) -> JSONResponse:
    """Word timings for a signed narration (read-along). Same signature as /api/audio; never calls a voice."""
    text = speech.verify(t, v, s)
    if text is None:
        raise HTTPException(403, "This audio link is not valid.")
    minute = time.strftime("%Y%m%d%H%M", time.gmtime())
    ok, _ = limits.increment(f"rl:timings-min:{auth.visitor_key(session)}:{minute}", 1, cap=TIMINGS_PER_MINUTE,
                             ttl_seconds=300)
    if not ok:
        raise HTTPException(429, "Too many requests. Please wait a minute.")
    entry = timings.load(v, text)
    if not entry:
        return JSONResponse({"words": None, "source": "pending"}, headers={"Cache-Control": "no-store"})
    return JSONResponse({"words": entry.get("words"), "source": entry.get("source")},
                        headers={"Cache-Control": timings.CACHE_CONTROL})


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

app.include_router(helper_slide.router)  # Settings > Draft slides

app.include_router(admin_evals_router)

from .admin_alerts import router as admin_alerts_router  # noqa: E402  (Settings > Student alerts)

app.include_router(admin_alerts_router)
