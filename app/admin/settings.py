"""Reading and saving Settings, the model picker, and the one-question model test.

Serves Settings > Model (provider, model, the picker list, "Test"), the saved choice in
Settings > Voice, and Settings > Limits and access (daily caps, beyond-the-slides web answers,
the student passcode, open access). Every route needs the admin cookie.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .. import (
    auth,
    config,
    edge_voice,
    limits,
    llm,
    playlist,
    settings_store,
    speech,
    storage,
    supa,
    usage,
    voices,
    web_answer,
)
from ..main import (
    RetrievalNotReady,
    Retriever,
    answer,
    clean_course,
    clean_question,
    get_completer,
    get_embedder,
    get_retriever,
)
from ..storage import Content
from .common import MODEL_ID_RE, SettingsBody, TestBody, _db_error
from .price_guard import check_model_price, max_price_per_mtok, model_warning

router = APIRouter(prefix="/api/admin")

FIRST_SLIDES_NOTE = "Retrieval is not ready yet, so this test used the first three slides of the index."


# ---------------------------------------------------------------- the settings view

def settings_view() -> dict[str, Any]:
    """The settings Settings shows: model, voices, caps, web answers (never a key or the passcode hash)."""
    provider, model = settings_store.llm_choice()
    stored_voice = settings_store.get("voice_id")
    voice_source = voices.setting_source()
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
        "open_access_until": auth.open_access_until() or None,
        **web_view(),
        "index_version": settings_store.index_version(),
    }


def web_view() -> dict[str, Any]:
    """Beyond-the-slides settings: on or off, today's cap and count, and the voice that reads web answers."""
    spoken = web_answer.voice()
    return {
        "web_answers_enabled": web_answer.enabled(),
        "daily_web_answer_cap": web_answer.daily_cap(),
        "web_answers_today": limits.read_counter(limits.web_answers_key()),
        "web_answer_voice": spoken.setting if spoken else "none",
        "web_answer_voice_label": spoken.label if spoken else None,
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


def _voice_values(raw: str | None) -> dict[str, Any]:
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


# ---------------------------------------------------------------- reading and saving

@router.get("/settings")
def get_settings(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    return settings_view()


@router.put("/settings")
def put_settings(body: SettingsBody, _: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """Save the fields that were sent, after checking each. 400 names the first bad field."""
    values: dict[str, Any] = {}
    values.update(_model_values(body))
    values.update(_voice_setting_values(body))
    values.update(_cap_and_web_values(body))
    if body.student_passcode is not None:
        code = body.student_passcode.strip()
        if not 6 <= len(code) <= 100:
            raise HTTPException(400, "The student passcode must be 6 to 100 characters.")
        values["student_passcode_hash"] = auth.hash_passcode(code)  # rotating signs every student out
    if body.open_access_until is not None:
        values["open_access_until"] = _open_access_value(body.open_access_until)
    if not values:
        raise HTTPException(400, "Nothing to save.")
    if "model" in values:
        check_model_price(values["provider"], values["model"])
    try:
        settings_store.put(values)
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
    return settings_view()


def _open_access_value(until: float) -> int:
    """0 closes open access now; otherwise a time in the next 48 hours."""
    if until <= 0:
        return 0
    if not time.time() < until <= time.time() + auth.OPEN_MAX_SECONDS:
        raise HTTPException(400, "Open access must end within the next 48 hours.")
    return int(until)


def _model_values(body: SettingsBody) -> dict[str, Any]:
    """Provider and model. A provider alone takes its default model; a model alone keeps the provider."""
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
    return values


def _voice_setting_values(body: SettingsBody) -> dict[str, Any]:
    """The voice that reads answers, and the free fallback for when ElevenLabs fails or is capped."""
    values: dict[str, Any] = {}
    if "voice_id" in body.model_fields_set:
        values.update(_voice_values(body.voice_id))  # null: server default; "none": captions only
    if body.voice_fallback is not None:
        if body.voice_fallback not in voices.FALLBACK_MODES:
            raise HTTPException(400, "voice_fallback must be captions or free.")
        values["voice_fallback"] = body.voice_fallback
    if body.voice_fallback_voice is not None:
        values["voice_fallback_voice"] = _free_voice(
            body.voice_fallback_voice, "The fallback must be one of the free Microsoft voices (edge:...).")
    return values


def _free_voice(raw: str, not_free_message: str) -> str:
    """"edge:<voice>" for a free Microsoft voice that exists; 400 for anything else (never an ElevenLabs voice)."""
    try:
        parsed = voices.parse(raw)
    except voices.BadVoice as exc:
        raise HTTPException(400, str(exc)) from exc
    if not parsed or parsed[0] != voices.EDGE:
        raise HTTPException(400, not_free_message)
    _check_free_voice(parsed[1])
    return f"edge:{parsed[1]}"


def _cap(value: int, top: int, message: str) -> int:
    if not 0 <= value <= top:
        raise HTTPException(400, message)
    return value


def _cap_and_web_values(body: SettingsBody) -> dict[str, Any]:
    """The daily caps, and the beyond-the-slides web answers (on or off, cap, voice)."""
    values: dict[str, Any] = {}
    if body.daily_voice_char_cap is not None:
        values["daily_voice_char_cap"] = _cap(
            body.daily_voice_char_cap, 10_000_000, "The daily voice cap must be between 0 and 10,000,000 characters.")
    if body.daily_free_voice_char_cap is not None:
        values["daily_free_voice_char_cap"] = _cap(
            body.daily_free_voice_char_cap, 10_000_000,
            "The free voice cap must be between 0 and 10,000,000 characters.")
    if body.web_answers_enabled is not None:
        values["web_answers_enabled"] = bool(body.web_answers_enabled)
    if body.daily_web_answer_cap is not None:
        values["daily_web_answer_cap"] = _cap(
            body.daily_web_answer_cap, 100_000, "The daily web answer cap must be between 0 and 100,000.")
    if body.web_answer_voice is not None:
        # Web answers are never read in my clone: only "none" or a free Microsoft voice.
        raw = body.web_answer_voice.strip()
        values["web_answer_voice"] = "none" if raw in ("", "none") else _free_voice(
            raw, "Web answers can only be read by a free Microsoft voice (edge:...), never "
                 "the voice clone or another ElevenLabs voice.")
    return values


# ---------------------------------------------------------------- the model picker and test

@router.get("/models")
def models(
    provider: str = Query(..., max_length=20), _: auth.Session = Depends(auth.require_admin)
) -> dict[str, Any]:
    """The model picker's list for one provider (curated, live, or both)."""
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, "provider must be anthropic, openai, or openrouter.")
    try:
        return llm.list_models(provider)
    except llm.LLMError as exc:
        raise HTTPException(502, f"Could not load the model list: {exc}") from exc


def _model_to_test(body: TestBody) -> tuple[str, str]:
    """The provider and model to test: the ones sent, else the saved ones, else the provider's default.

    400 when the provider is unknown, the model id looks wrong, the key is missing, or the price is over the cap.
    """
    active_provider, active_model = settings_store.llm_choice()
    provider = body.provider or active_provider
    if provider not in llm.PROVIDERS:
        raise HTTPException(400, "provider must be anthropic, openai, or openrouter.")
    default = active_model if provider == active_provider else config.DEFAULT_LLM_MODELS[provider]
    model = (body.model or default).strip()
    if not MODEL_ID_RE.match(model):
        raise HTTPException(400, "That model id does not look right.")
    if not llm.key_configured(provider):
        raise HTTPException(400, f"{llm.KEY_VARS[provider]} is not set, so this provider cannot be tested.")
    check_model_price(provider, model)
    return provider, model


@router.post("/test")
def test_model(
    body: TestBody,
    request: Request,
    session: auth.Session = Depends(auth.require_admin),
    retriever: Retriever = Depends(get_retriever),
    embedder: Callable[[str], np.ndarray] = Depends(get_embedder),
    completer: Callable[..., str] = Depends(get_completer),
) -> dict[str, Any]:
    """Run one sample question through the chosen (unsaved) provider and model."""
    question = clean_question(body.question)
    course = clean_course(body.course)
    provider, model = _model_to_test(body)
    limits.check_ask_rate(auth.visitor_key(session), limits.client_hash(request))  # counts against limits; not logged
    content = storage.store.get_or_503()
    started = time.monotonic()
    note = None
    try:
        with usage.purpose("prompt_test", sticky=True):  # Analytics counts test spend apart from students
            result, info = answer(question, course, content, retriever, embedder, completer, provider, model)
    except (RetrievalNotReady, HTTPException) as exc:
        # Retrieval or embeddings are not ready: test the model on the first slides instead.
        if isinstance(exc, HTTPException) and exc.status_code != 503:
            raise
        note = FIRST_SLIDES_NOTE
        with usage.purpose("prompt_test", sticky=True):
            result, res = _first_slides_answer(content, question, completer, provider, model)
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


def _first_slides_answer(content: Content, question: str, completer: Callable[..., str], provider: str,
                         model: str) -> tuple[dict[str, Any], Any]:
    """Narrate the index's first three slides: how a model or prompt is tested before retrieval exists."""
    from .. import narration

    sample = [r for r in content.records if r.get("kind", "slide") == "slide"][:3]
    codes = {r["id"]: (playlist.related_code(content, r) or {}).get("source") for r in sample}
    res = narration.narrate(question, sample, codes, provider=provider, model=model, complete=completer)
    return playlist.build_playlist(content, question, sample, res.narrations, res.follow_ups, None), res
