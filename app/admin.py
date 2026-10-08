"""Settings page API (admin passcode, `ft_admin` cookie). Never linked from the student UI.

Sections it serves: Model, Voice, Courses and source material, Limits and access, Activity, Prompts.

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
import time
from datetime import date as date_cls
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel

from . import (
    auth,
    config,
    edge_voice,
    faq,
    limits,
    llm,
    logistics,
    playlist,
    prompts,
    settings_store,
    speech,
    storage,
    supa,
    thresholds,
    voices,
)
from .main import (
    NOT_COVERED,
    STORED_TOPIC,
    PasscodeBody,
    RetrievalNotReady,
    Retriever,
    answer,
    clean_course,
    clean_question,
    get_completer,
    get_embedder,
    get_retriever,
)

router = APIRouter(prefix="/api/admin")

UPLOAD_KINDS = {
    "slides": ({".pdf", ".pptx"}, 300 * 1024**2),
    "transcript": ({".vtt"}, 20 * 1024**2),
    "video": ({".mp4"}, 5 * 1024**3),
    "notebook": ({".ipynb"}, 50 * 1024**2),
}
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".vtt": "text/vtt",
    ".mp4": "video/mp4",
    ".ipynb": "application/x-ipynb+json",
}
UPLOAD_URL_SECONDS = 7200  # Supabase signed upload URLs last two hours
STATUS_VARS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "VOYAGE_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "SUPABASE_URL",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SESSION_SECRET",
    "AUDIO_SIGNING_SECRET",
    "STUDENT_PASSCODE",
    "ADMIN_PASSCODE",
)
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:/~\-]{1,200}$")
COURSE_RE = re.compile(r"^[0-9]{5}$")


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


# ---------------------------------------------------------------- model price guard

def max_price_per_mtok() -> dict[str, float]:
    out = dict(config.DEFAULT_MAX_PRICE_PER_MTOK)
    for key, var in (("prompt", "LLM_MAX_PROMPT_PRICE_PER_MTOK"), ("completion", "LLM_MAX_COMPLETION_PRICE_PER_MTOK")):
        raw = config.env(var)
        if raw:
            try:
                out[key] = float(raw)
            except ValueError:
                config.log.warning("%s is not a number; using %s", var, out[key])
    return out


def check_model_price(provider: str, model: str) -> None:
    """Refuse OpenRouter models priced above the ceiling (o1-pro class models cost 10-60x more).

    OpenRouter publishes prices per token; the check fails closed when the
    price cannot be read. Claude and OpenAI have no price API, so ids outside
    the curated list get a warning in the settings view instead.
    """
    if provider != "openrouter":
        return
    try:
        listing = llm.list_models("openrouter")
    except llm.LLMError as exc:
        raise HTTPException(400, "Could not check this model's price on OpenRouter right now. Try again shortly.") from exc
    found = next((m for m in listing.get("models", []) if m.get("id") == model), None)
    if found is None:
        raise HTTPException(400, "OpenRouter does not list that model id.")
    pricing = found.get("pricing") or {}
    try:
        prompt = float(pricing.get("prompt")) * 1_000_000
        completion = float(pricing.get("completion")) * 1_000_000
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "OpenRouter does not publish a fixed price for that model, so it cannot be used.") from exc
    if prompt < 0 or completion < 0:
        raise HTTPException(400, "That model has a variable price (a router), so it cannot be used.")
    ceiling = max_price_per_mtok()
    if prompt > ceiling["prompt"] or completion > ceiling["completion"]:
        raise HTTPException(
            400,
            f"{model} costs ${prompt:.2f} in / ${completion:.2f} out per million tokens, over the limit of "
            f"${ceiling['prompt']:.2f} / ${ceiling['completion']:.2f}. Pick a cheaper model, or raise "
            "LLM_MAX_PROMPT_PRICE_PER_MTOK / LLM_MAX_COMPLETION_PRICE_PER_MTOK in Vercel.",
        )


def model_warning(provider: str, model: str) -> str | None:
    if provider in ("anthropic", "openai") and model not in {m["id"] for m in llm.CURATED[provider]}:
        return "This model id is not on the curated list. Check its price before students use it."
    return None


# ---------------------------------------------------------------- login

@router.post("/login", status_code=204)
def admin_login(body: PasscodeBody, request: Request, response: Response) -> None:
    limits.check_login_rate(request, "admin")
    code = (body.passcode or "").strip()
    if not code or len(code) > 200 or not auth.check_admin_passcode(code):
        raise HTTPException(401, "That admin passcode is not right.")
    auth.issue_admin(response)


@router.post("/logout")
def admin_logout(response: Response) -> dict[str, bool]:
    response.delete_cookie(auth.ADMIN_COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- settings

class SettingsBody(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    voice_id: Optional[str] = None
    voice_fallback: Optional[str] = None
    voice_fallback_voice: Optional[str] = None
    daily_voice_char_cap: Optional[int] = None
    daily_free_voice_char_cap: Optional[int] = None
    student_passcode: Optional[str] = None


def settings_view() -> dict[str, Any]:
    provider, model = settings_store.llm_choice()
    stored_voice = settings_store.get("voice_id")
    if stored_voice == "none":
        voice_source = "none"
    elif stored_voice:
        voice_source = "settings"
    elif config.env("ELEVENLABS_VOICE_ID"):
        voice_source = "env"
    else:
        voice_source = "none"
    return {
        "provider": provider,
        "model": model,
        "providers": list(llm.PROVIDERS),
        "default_models": dict(config.DEFAULT_LLM_MODELS),
        "voice_id": stored_voice or None,  # null = server default, "none" = captions only
        "voice_source": voice_source,
        "voice_default_configured": bool(config.env("ELEVENLABS_VOICE_ID")),
        **voice_view(),
        "daily_voice_char_cap": settings_store.daily_voice_char_cap(),
        "daily_free_voice_char_cap": settings_store.daily_free_voice_char_cap(),
        "per_minute_limit": config.PER_MINUTE_LIMIT,
        "per_day_limit": config.PER_DAY_LIMIT,
        "question_max_chars": config.QUESTION_MAX_CHARS,
        "model_warning": model_warning(provider, model),
        "max_price_per_mtok": max_price_per_mtok(),
        "student_passcode_source": "settings" if settings_store.get("student_passcode_hash") else "env",
        "index_version": settings_store.index_version(),
    }


def voice_view() -> dict[str, Any]:
    """What the voice setting means right now, and the label students see for it."""
    plan = voices.current()
    return {
        "voice_kind": voices.setting_kind(voices.stored_setting()),
        "voice_label": plan.primary.label if plan.primary else None,
        "voice_costs_money": bool(plan.primary and plan.primary.costs_money),
        "voice_fallback": voices.fallback_mode(),
        "voice_fallback_voice": f"edge:{voices.fallback_voice_name()}",
        "voice_fallback_label": plan.fallback.label if plan.fallback else None,
    }


def _check_free_voice(name: str) -> None:
    known = edge_voice.is_known_voice_sync(name)
    if known is None:
        raise HTTPException(502, "Could not check that voice name with Microsoft right now. Try again shortly.")
    if not known:
        raise HTTPException(
            400,
            f"Microsoft has no voice named {name}. Pick one from the list, or check the ShortName "
            "(for example en-US-AndrewMultilingualNeural).",
        )


def _voice_values(raw: Optional[str]) -> dict[str, Any]:
    """Validate a voice choice and return the settings rows to write (voice_id and voice_kind)."""
    try:
        parsed = voices.parse(raw)
    except voices.BadVoice as exc:
        raise HTTPException(400, str(exc)) from exc
    if parsed is None:
        return {"voice_id": None, "voice_kind": None}  # server default (ELEVENLABS_VOICE_ID)
    provider, voice_id = parsed
    if provider == "none":
        return {"voice_id": "none", "voice_kind": None}
    if provider == voices.EDGE:
        _check_free_voice(voice_id)
        return {"voice_id": f"edge:{voice_id}", "voice_kind": None}
    # ElevenLabs: check the voice is on the account and record whether it is my clone, so the
    # student label is right. Without a key (local dev) it is saved unverified (neutral label).
    kind = None
    if config.env("ELEVENLABS_API_KEY"):
        try:
            account = speech.list_voices()
        except speech.VoiceError as exc:
            raise HTTPException(502, f"Could not check that voice with ElevenLabs: {exc}") from exc
        meta = next((v for v in account if v.get("voice_id") == voice_id), None)
        if meta is None:
            raise HTTPException(400, "That voice is not on the ElevenLabs account.")
        kind = "clone" if speech.is_clone(meta) else "stock"
    sent = (raw or "").strip()
    stored = sent if sent == voice_id else f"eleven:{voice_id}"  # an older bare id stays as it was sent
    return {"voice_id": stored, "voice_kind": {"voice_id": voice_id, "kind": kind} if kind else None}


@router.get("/settings")
def get_settings(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    return settings_view()


@router.put("/settings")
def put_settings(body: SettingsBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if body.provider is not None:
        if body.provider not in llm.PROVIDERS:
            raise HTTPException(400, "provider must be anthropic, openai, or openrouter.")
        values["provider"] = body.provider
        values["model"] = body.model or config.DEFAULT_LLM_MODELS[body.provider]
    if body.model is not None:
        model = body.model.strip()
        if not MODEL_ID_RE.match(model):
            raise HTTPException(400, "That model id does not look right.")
        values["model"] = model
        values.setdefault("provider", settings_store.llm_choice()[0])
    if "voice_id" in body.model_fields_set:
        values.update(_voice_values(body.voice_id))  # null: server default; "none": captions only
    if body.voice_fallback is not None:
        if body.voice_fallback not in voices.FALLBACK_MODES:
            raise HTTPException(400, "voice_fallback must be captions or free.")
        values["voice_fallback"] = body.voice_fallback
    if body.voice_fallback_voice is not None:
        try:
            parsed = voices.parse(body.voice_fallback_voice)
        except voices.BadVoice as exc:
            raise HTTPException(400, str(exc)) from exc
        if not parsed or parsed[0] != voices.EDGE:
            raise HTTPException(400, "The fallback must be one of the free Microsoft voices (edge:...).")
        _check_free_voice(parsed[1])
        values["voice_fallback_voice"] = f"edge:{parsed[1]}"
    if body.daily_voice_char_cap is not None:
        if not 0 <= body.daily_voice_char_cap <= 10_000_000:
            raise HTTPException(400, "The daily voice cap must be between 0 and 10,000,000 characters.")
        values["daily_voice_char_cap"] = body.daily_voice_char_cap
    if body.daily_free_voice_char_cap is not None:
        if not 0 <= body.daily_free_voice_char_cap <= 10_000_000:
            raise HTTPException(400, "The free voice cap must be between 0 and 10,000,000 characters.")
        values["daily_free_voice_char_cap"] = body.daily_free_voice_char_cap
    if body.student_passcode is not None:
        code = body.student_passcode.strip()
        if not 6 <= len(code) <= 100:
            raise HTTPException(400, "The student passcode must be 6 to 100 characters.")
        values["student_passcode_hash"] = auth.hash_passcode(code)
    if not values:
        raise HTTPException(400, "Nothing to save.")
    if "model" in values:
        check_model_price(values["provider"], values["model"])
    try:
        settings_store.put(values)
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    return settings_view()


@router.get("/models")
def models(
    provider: str = Query(..., max_length=20), _: auth.Session = Depends(auth.require_admin)
) -> dict[str, Any]:
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, "provider must be anthropic, openai, or openrouter.")
    try:
        return llm.list_models(provider)
    except llm.LLMError as exc:
        raise HTTPException(502, f"Could not load the model list: {exc}") from exc


class TestBody(BaseModel):
    question: str
    provider: Optional[str] = None
    model: Optional[str] = None
    course: Optional[str] = None


@router.post("/test")
def test_model(
    body: TestBody,
    request: Request,
    session: auth.Session = Depends(auth.require_admin),
    retriever: Retriever = Depends(get_retriever),
    embedder=Depends(get_embedder),
    completer=Depends(get_completer),
) -> dict[str, Any]:
    """Run one sample question through the chosen (unsaved) provider and model."""
    question = clean_question(body.question)
    course = clean_course(body.course)
    active_provider, active_model = settings_store.llm_choice()
    provider = body.provider or active_provider
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, "provider must be anthropic, openai, or openrouter.")
    model = (body.model or (active_model if provider == active_provider else config.DEFAULT_LLM_MODELS[provider])).strip()
    if not MODEL_ID_RE.match(model):
        raise HTTPException(400, "That model id does not look right.")
    if not llm.key_configured(provider):
        raise HTTPException(400, f"{llm.KEY_VARS[provider]} is not set, so this provider cannot be tested.")
    check_model_price(provider, model)
    limits.check_ask_rate(auth.visitor_key(session), limits.client_hash(request))  # counts against limits; not logged
    content = storage.store.get_or_503()
    started = time.monotonic()
    note = None
    try:
        result, info = answer(question, course, content, retriever, embedder, completer, provider, model)
    except (RetrievalNotReady, HTTPException) as exc:
        # Retrieval or embeddings are not ready: test the model on the first slides instead.
        if isinstance(exc, HTTPException) and exc.status_code != 503:
            raise
        note = "Retrieval is not ready yet, so this test used the first three slides of the index."
        from . import narration

        sample = [r for r in content.records if r.get("kind", "slide") == "slide"][:3]
        codes = {r["id"]: (playlist.related_code(content, r) or {}).get("source") for r in sample}
        res = narration.narrate(question, sample, codes, provider=provider, model=model, complete=completer)
        result = playlist.build_playlist(content, question, sample, res.narrations, res.follow_ups, None)
        info = {"narration": res.source, "errors": res.errors}
    return {
        "ok": info.get("narration") in ("llm", "stored") or not result["covered"],
        "provider": provider,
        "model": model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "narration_source": info.get("narration"),
        "errors": info.get("errors") or [],
        "note": note,
        "narration": {
            "segments": [{"slide_id": s["slide_id"], "narration": s["narration"]} for s in result["segments"]],
            "follow_ups": result["follow_ups"],
        },
        "playlist": result,
    }


# ---------------------------------------------------------------- prompts

class PromptBody(BaseModel):
    text: str
    note: Optional[str] = None


class PromptNoteBody(BaseModel):
    note: Optional[str] = None


class PromptRestoreBody(BaseModel):
    version: str
    note: Optional[str] = None


class PromptTestBody(BaseModel):
    text: str
    question: str
    course: Optional[str] = None


def _prompt_name(name: str) -> str:
    if name not in prompts.REGISTRY:
        raise HTTPException(404, "There is no prompt with that name.")
    return name


def _prompt_write(fn, *args) -> dict[str, Any]:
    try:
        fn(*args)
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    except supa.SupabaseError as exc:
        config.log.warning("prompt save failed: %s", exc)
        raise HTTPException(502, "Could not save the prompt to storage. Nothing changed.") from exc
    return prompts.view(args[0])


@router.get("/prompts")
def list_prompts(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Every model-facing prompt: current text, default, and whether Ben has edited it."""
    return {"prompts": prompts.all_views(), "max_chars": prompts.MAX_CHARS}


@router.get("/prompts/{name}/history")
def prompt_history(name: str, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    try:
        return {"versions": prompts.history(_prompt_name(name))}
    except supa.SupabaseError as exc:
        config.log.warning("prompt history read failed: %s", exc)
        raise HTTPException(502, "Could not read the prompt history from storage.") from exc


@router.put("/prompts/{name}")
def save_prompt(name: str, body: PromptBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Save a new version. The text is checked (length, placeholders, reply words) before anything is written."""
    return _prompt_write(prompts.save, _prompt_name(name), body.text, body.note or "")


@router.post("/prompts/{name}/reset")
def reset_prompt(
    name: str, body: Optional[PromptNoteBody] = None, _: auth.Session = Depends(auth.require_admin)
) -> dict[str, Any]:
    note = body.note if body else None
    return _prompt_write(lambda n, t: prompts.save(n, "", t, reset=True), _prompt_name(name), note or "")


@router.post("/prompts/{name}/restore")
def restore_prompt(
    name: str, body: PromptRestoreBody, _: auth.Session = Depends(auth.require_admin)
) -> dict[str, Any]:
    return _prompt_write(prompts.restore, _prompt_name(name), body.version, body.note or "")


@router.post("/prompts/{name}/test")
def test_prompt(
    name: str,
    body: PromptTestBody,
    request: Request,
    session: auth.Session = Depends(auth.require_admin),
    retriever: Retriever = Depends(get_retriever),
    embedder=Depends(get_embedder),
    completer=Depends(get_completer),
) -> dict[str, Any]:
    """Run one question through the real path with the draft text, for this request only. Nothing is saved.

    The draft goes through the same checks as a save, and the answer through the
    same validators as any student's (grounding, caps, PG, codes, name tokens).
    """
    spec = prompts.spec(_prompt_name(name))
    if not spec.testable:
        raise HTTPException(400, "Eval prompts are tested by an eval run, not here.")
    try:
        text = prompts.check(name, body.text)
    except prompts.PromptError as exc:
        raise HTTPException(400, str(exc)) from exc
    question = clean_question(body.question)
    course = clean_course(body.course)
    provider, model = settings_store.llm_choice()
    if not llm.key_configured(provider):
        raise HTTPException(400, f"{llm.KEY_VARS[provider]} is not set, so the prompt cannot be tested.")
    limits.check_ask_rate(auth.visitor_key(session), limits.client_hash(request))  # counts against limits; not logged
    started = time.monotonic()
    with prompts.draft(name, text):
        if name == logistics.PROMPT_NAME:
            kind = logistics.classify(question, completer, provider=provider, model=model)
            output: dict[str, Any] = {"kind": kind.kind, "source": kind.source, "reason": kind.reason}
            if kind.source == "keyword":
                output["note"] = "The keyword pre-check caught this question, so the prompt was not used."
            elif kind.source == "error":
                output["note"] = "The reply was not a valid kind, so the question would be answered as course content."
            ok, errors = kind.source != "error", []
        else:
            result, info = _answer_for_test(question, course, retriever, embedder, completer, provider, model)
            output = {
                "kind": info.get("kind"),
                "narration_source": info.get("narration"),
                "covered": result.get("covered"),
                "message": result.get("message"),
                "segments": [{"slide_id": s["slide_id"], "narration": s["narration"]} for s in result["segments"]],
                "follow_ups": result.get("follow_ups") or [],
                "note": info.get("note"),
            }
            errors = info.get("errors") or []
            ok = info.get("narration") in ("llm", "stored") or not result["covered"]
    return {
        "ok": ok,
        "name": name,
        "provider": provider,
        "model": model,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "errors": errors,
        "output": output,
    }


def _answer_for_test(question, course, retriever, embedder, completer, provider, model):
    content = storage.store.get_or_503()
    try:
        return answer(question, course, content, retriever, embedder, completer, provider, model)
    except RetrievalNotReady:
        from . import narration

        sample = [r for r in content.records if r.get("kind", "slide") == "slide"][:3]
        codes = {r["id"]: (playlist.related_code(content, r) or {}).get("source") for r in sample}
        res = narration.narrate(question, sample, codes, provider=provider, model=model, complete=completer)
        result = playlist.build_playlist(content, question, sample, res.narrations, res.follow_ups, None)
        note = "Retrieval is not ready yet, so this test used the first three slides of the index."
        return result, {"narration": res.source, "errors": res.errors, "note": note, "kind": "course_content"}


# ---------------------------------------------------------------- status and activity

@router.get("/status")
def status(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    loaded = storage.store.loaded
    if loaded is None:
        try:
            loaded = storage.store.get()
        except storage.ContentUnavailable:
            loaded = None
    provider, model = settings_store.llm_choice()
    return {
        # Booleans only, keyed by env var name. Never a value.
        "keys": {name: bool(config.env(name)) for name in STATUS_VARS},
        "llm": {"provider": provider, "model": model},
        "voyage_model": config.env("VOYAGE_MODEL", config.DEFAULT_VOYAGE_MODEL),
        "content": {
            "loaded": loaded is not None,
            "source": loaded.source if loaded else None,
            "records": len(loaded.records) if loaded else 0,
            "slides": sum(1 for r in loaded.records if r.get("kind", "slide") == "slide") if loaded else 0,
            "info_chunks": len(loaded.info_records) if loaded else 0,
            "index_version": loaded.version if loaded else None,
        },
        "today": {
            **limits.today_counters(),
            "voice_char_cap": settings_store.daily_voice_char_cap(),
            "free_voice_char_cap": settings_store.daily_free_voice_char_cap(),
        },
        "limits": {
            "per_minute": config.PER_MINUTE_LIMIT,
            "per_day": config.PER_DAY_LIMIT,
            "question_max_chars": config.QUESTION_MAX_CHARS,
            "narration_max_words": config.NARRATION_MAX_WORDS,
        },
    }


# Kinds that answer without calling any model: their rows show "none" for the model.
NO_MODEL_KINDS = {STORED_TOPIC, faq.KIND, NOT_COVERED}


def infer_kind(row: dict[str, Any]) -> str:
    """Best guess at what answered an older row that has no `kind` (docs/TESTING_AND_SCORES.md).

    A stored suggested question and an FAQ hit run no search, so their top score
    is empty; a logistics referral has a score at or over the threshold but is not covered.
    """
    score = row.get("top_score")
    if row.get("covered"):
        return STORED_TOPIC if score is None else logistics.COURSE_CONTENT
    if score is None:
        return faq.KIND
    threshold = thresholds.slide_threshold()  # today's effective value (Settings override, else Ben's)
    if threshold is not None and float(score) >= threshold:
        return logistics.LOGISTICS
    return NOT_COVERED


def activity_row(row: dict[str, Any]) -> dict[str, Any]:
    """A question-log row for the Activity table: a kind on every row, no model where none was called."""
    out = dict(row)
    kind = out.get("kind")
    inferred = False
    if not kind:
        kind, inferred = infer_kind(out), True
    elif kind == logistics.COURSE_CONTENT and out.get("covered") and out.get("top_score") is None:
        # Rows logged before stored_topic existed recorded a stored playlist as course_content.
        kind, inferred = STORED_TOPIC, True
    out["kind"] = kind
    out["kind_inferred"] = inferred
    if kind in NO_MODEL_KINDS:
        out["provider"] = out["model"] = None
    return out


@router.get("/log")
def question_log(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    try:
        return {"rows": [activity_row(r) for r in limits.recent_questions(50)]}
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc


# ---------------------------------------------------------------- voice

VOICE_GROUPS = {
    "clone": {
        "label": "My voice clone",
        "cost": "ElevenLabs: costs credits per character.",
        "costs_money": True,
        "student_label": voices.CLONE_LABEL,
    },
    "elevenlabs": {
        "label": "ElevenLabs voices",
        "cost": "ElevenLabs: costs credits per character.",
        "costs_money": True,
        "student_label": voices.STOCK_LABEL,
    },
    "free": {
        "label": "Free Microsoft voices",
        "cost": "Free: no key, no cost (Microsoft neural voices through edge-tts).",
        "costs_money": False,
        "student_label": voices.STOCK_LABEL,
    },
}


def preview_path(setting: str) -> str:
    return f"/api/admin/voice-preview?voice={setting}"


@router.get("/voices")
def list_voice_options(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Every voice Settings can pick, in three groups: my clone, ElevenLabs stock, free Microsoft.

    Ids are what `PUT /api/admin/settings {voice_id}` takes. "none" (captions
    only) and null (server default) are offered by the page itself.
    """
    default_id = config.env("ELEVENLABS_VOICE_ID")
    clone: list[dict[str, Any]] = []
    stock: list[dict[str, Any]] = []
    eleven_error = None
    if not config.env("ELEVENLABS_API_KEY"):
        eleven_error = "ELEVENLABS_API_KEY is not set, so only the free voices and captions are available."
    else:
        try:
            for v in speech.list_voices():
                if not v.get("voice_id"):
                    continue
                entry = {
                    "voice_id": f"eleven:{v['voice_id']}",
                    "name": v.get("name") or v["voice_id"],
                    "category": v.get("category"),
                    "preview_url": v.get("preview_url"),
                    "is_default": v["voice_id"] == default_id,
                }
                (clone if speech.is_clone(v) else stock).append(entry)
        except speech.VoiceError as exc:
            eleven_error = f"Could not load ElevenLabs voices: {exc}"
    clone.sort(key=lambda e: (not e["is_default"], str(e["name"]).lower()))
    stock.sort(key=lambda e: str(e["name"]).lower())
    free = [
        {
            "voice_id": f"edge:{short}",
            "name": name,
            "category": "free",
            "description": desc,
            "preview_url": preview_path(f"edge:{short}"),
            "is_default": False,
        }
        for short, (name, desc) in edge_voice.FREE_VOICES.items()
    ]
    groups = [
        {"id": gid, **VOICE_GROUPS[gid], "voices": items}
        for gid, items in (("clone", clone), ("elevenlabs", stock), ("free", free))
    ]
    return {
        "groups": groups,
        "voices": clone + stock + free,
        "elevenlabs_error": eleven_error,
        "free_voice_default": f"edge:{edge_voice.DEFAULT_FREE_VOICE}",
        "preview_text": edge_voice.PREVIEW_TEXT,
    }


@router.get("/voice-preview")
async def voice_preview(
    voice: str = Query(..., max_length=120), _: auth.Session = Depends(auth.require_admin)
) -> Response:
    """One fixed sentence (server-side text, never caller-supplied) in a free voice, cached in memory.

    ElevenLabs voices use the `preview_url` from their own voice list instead,
    which costs no characters.
    """
    try:
        parsed = voices.parse(voice)
    except voices.BadVoice as exc:
        raise HTTPException(400, str(exc)) from exc
    if not parsed or parsed[0] != voices.EDGE:
        raise HTTPException(400, "Previews here are for the free Microsoft voices (edge:...).")
    name = parsed[1]
    known = await edge_voice.is_known_voice(name)
    if known is None:
        raise HTTPException(502, "Could not check that voice name with Microsoft right now.")
    if not known:
        raise HTTPException(400, f"Microsoft has no voice named {name}.")
    if edge_voice.preview_cached(name) is None and not limits.take_voice_chars(
        len(edge_voice.PREVIEW_TEXT), settings_store.daily_free_voice_char_cap(), pool="free"
    ):
        raise HTTPException(429, "The free voices have reached today's limit.")
    try:
        data = await edge_voice.preview(name)
    except edge_voice.FreeVoiceError as exc:
        config.log.warning("voice preview failed: %s", exc)
        raise HTTPException(502, "The free voice service did not respond. Try again.") from exc
    return Response(data, media_type="audio/mpeg", headers={"Cache-Control": "private, max-age=86400"})


# ---------------------------------------------------------------- courses and sessions

class CourseBody(BaseModel):
    course: Optional[str] = None  # the Settings page sends `course`; the spec says `code`; both work
    code: Optional[str] = None
    title: str
    term: Optional[str] = None


class SessionBody(BaseModel):
    course: str
    session: int
    date: Optional[str] = None
    title: Optional[str] = None
    visible: Optional[bool] = True


class SessionPatch(BaseModel):
    visible: Optional[bool] = None
    title: Optional[str] = None
    date: Optional[str] = None


def _check_date(value: Optional[str]) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        return date_cls.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise HTTPException(400, "date must look like 2026-10-05.") from exc


def _check_title(value: Optional[str], what: str) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if len(value) > 200:
        raise HTTPException(400, f"{what} must be under 200 characters.")
    return value


@router.get("/courses")
def list_courses(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Courses -> sessions with what exists for each: uploaded sources, indexed slides, clips."""
    try:
        loaded = storage.store.get()
    except storage.ContentUnavailable:
        loaded = None
    course_rows: list[dict[str, Any]] = []
    session_rows: list[dict[str, Any]] = []
    source_rows: list[dict[str, Any]] = []
    if config.supabase_configured():
        try:
            course_rows = supa.select("courses", {"select": "*", "order": "code"})
            session_rows = supa.select("sessions", {"select": "*", "order": "course,session"})
            source_rows = supa.select("sources", {"select": "*", "order": "updated_at.desc"})
        except supa.SupabaseError as exc:
            raise _db_error(exc) from exc

    courses: dict[str, dict[str, Any]] = {}
    for c in course_rows:
        courses[c["code"]] = {"course": c["code"], "title": c.get("title"), "term": c.get("term"), "sessions": {}}

    def sess(course: str, number: int) -> dict[str, Any]:
        entry = courses.setdefault(course, {"course": course, "title": None, "term": None, "sessions": {}})
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

    for s in session_rows:
        row = sess(s["course"], int(s["session"]))
        row.update({"date": s.get("date"), "title": s.get("title"), "visible": s.get("visible", True)})
    for src in source_rows:  # newest first: keep the latest per kind
        row = sess(src["course"], int(src["session"]))
        if row["sources"].get(src["kind"]) is None:
            row["sources"][src["kind"]] = {
                "id": src["id"],
                "status": src["status"],
                "message": src.get("message"),
                "path": src["path"],
                "updated_at": src.get("updated_at"),
            }
    if loaded is not None:
        for r in loaded.records:
            if r.get("kind", "slide") != "slide" or not r.get("course"):
                continue
            row = sess(str(r["course"]), int(r.get("session") or 0))
            row["slides_indexed"] += 1
            row["clips"] += 1 if r.get("clip") else 0
            row["_transcript"] = row.get("_transcript") or bool(str(r.get("transcript") or "").strip())
            row["date"] = row["date"] or r.get("date")
            row["title"] = row["title"] or r.get("session_title")
            courses[str(r["course"])]["title"] = courses[str(r["course"])]["title"] or r.get("course_title")
    for c in courses.values():
        for row in c["sessions"].values():
            src = row["sources"]
            row["has"] = {
                "slides": bool(src["slides"]) or row["slides_indexed"] > 0,
                "transcript": bool(src["transcript"]) or row.pop("_transcript", False),
                "video": bool(src["video"]) or row["clips"] > 0,
                "clips": row["clips"] > 0,
                "indexed": row["slides_indexed"] > 0,
            }
            row.pop("_transcript", None)
    return {
        "courses": [
            {**c, "sessions": [c["sessions"][k] for k in sorted(c["sessions"])]}
            for _, c in sorted(courses.items())
        ]
    }


@router.post("/courses")
def add_course(body: CourseBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
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
    if not 1 <= number <= 99:
        raise HTTPException(400, "The session number must be between 1 and 99.")
    if not supa.select("courses", {"select": "code", "code": f"eq.{course}"}):
        raise HTTPException(400, "Add the course first.")
    row = {"id": session_id(course, number), "course": course, "session": number, **(extra or {})}
    return supa.insert("sessions", row, upsert_on="id")[0]


@router.post("/sessions")
def add_session(body: SessionBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
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


# ---------------------------------------------------------------- uploads and sources

class UploadBody(BaseModel):
    course: str
    session: int
    kind: str
    filename: str
    size: int


def safe_filename(name: str) -> str:
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    return base[:120]


@router.post("/uploads")
def create_upload(body: UploadBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
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
                "updated_at": datetime.now(timezone.utc).isoformat(),
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


def _set_status(source_id: int, status: str, message: Optional[str] = None) -> dict[str, Any]:
    rows = supa.update(
        "sources",
        {"id": f"eq.{source_id}"},
        {"status": status, "message": message, "updated_at": datetime.now(timezone.utc).isoformat()},
    )
    if not rows:
        raise HTTPException(404, "No such source.")
    return rows[0]


@router.get("/sources")
def list_sources(
    course: Optional[str] = Query(None, max_length=5),
    session: Optional[int] = Query(None, ge=1, le=99),
    _: auth.Session = Depends(auth.require_admin),
) -> dict[str, Any]:
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
    _need_supabase()
    try:
        return _set_status(source_id, "uploaded")
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
