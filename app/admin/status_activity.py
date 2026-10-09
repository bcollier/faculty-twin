"""What is configured and what students asked: the status summary and the recent question log.

Serves Settings > Keys (which keys are set, as booleans), the Settings page's first load
(`/status` decides between the app and the sign-in screen), and Settings > Activity (today's
counters and the last 50 questions). `activity_row` is also used by Settings > Analytics
(`app/analytics.py`). Every route needs the admin cookie.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from .. import (
    alerts,
    auth,
    config,
    faq,
    limits,
    logistics,
    settings_store,
    storage,
    supa,
    thresholds,
    usage,
    web_answer,
)
from ..main import NOT_COVERED, STORED_TOPIC
from .common import _db_error

router = APIRouter(prefix="/api/admin")

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
    *alerts.ENV_VARS,  # Settings > Student alerts (Twilio); booleans only like every key
)
# Kinds that answer without calling any model: their rows show "none" for the model.
NO_MODEL_KINDS = {STORED_TOPIC, faq.KIND, NOT_COVERED}


@router.get("/status")
def status(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """What Settings > Status shows: which keys are set, the loaded index, today's counters."""
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
            "web_answer_cap": web_answer.daily_cap(),
        },
        "limits": {
            "per_minute": config.PER_MINUTE_LIMIT,
            "per_day": config.PER_DAY_LIMIT,
            "question_max_chars": config.QUESTION_MAX_CHARS,
            "narration_max_words": config.NARRATION_MAX_WORDS,
        },
    }


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
    # Smoke checks, evals and prompt tests: a "Test" badge, and left out of student analytics.
    out["test"], out["test_inferred"] = usage.is_test_traffic(out)
    if not out["test"]:
        out["test_inferred"] = False
    if kind in NO_MODEL_KINDS:
        out["provider"] = out["model"] = None
    return out


@router.get("/log")
def question_log(_: auth.Session = Depends(auth.require_admin)) -> dict[str, Any]:
    """The last 50 question-log rows for Settings > Activity (scrubbed text and scores only)."""
    try:
        return {"rows": [activity_row(r) for r in limits.recent_questions(50)]}
    except supa.SupabaseError as exc:
        raise _db_error(exc) from exc
