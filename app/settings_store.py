"""Admin-editable settings, stored as key/value rows in the Supabase `settings` table.

Keys used (docs/SPEC.md): provider, model, voice_id, voice_kind, voice_fallback,
voice_fallback_voice, daily_voice_char_cap, daily_free_voice_char_cap,
student_passcode_hash, index_version, pricing (Analytics price table). Rows are cached in memory for 30 seconds
so a busy function does not hit Postgres on every request. When Supabase is not
configured (local dev, tests) the env-var defaults apply and writes go to an
in-memory dict.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from . import config, supa

CACHE_SECONDS = 30.0

_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_at = 0.0
_local: dict[str, Any] = {}  # used only when Supabase is not configured


def _load() -> dict[str, Any]:
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if _cache_at and now - _cache_at < CACHE_SECONDS:
            return _cache
    if not config.supabase_configured():
        return dict(_local)
    try:
        rows = supa.select("settings", {"select": "key,value"})
        values = {r["key"]: r["value"] for r in rows}
    except supa.SupabaseError as exc:
        config.log.warning("settings read failed, using defaults: %s", exc)
        values = dict(_cache)  # last good copy, possibly empty
    with _lock:
        _cache, _cache_at = values, now
    return values


def get(key: str, default: Any = None) -> Any:
    value = _load().get(key)
    return default if value is None else value


def all_values() -> dict[str, Any]:
    return dict(_load())


def put(values: dict[str, Any]) -> None:
    """Write settings and drop the cache so this instance sees them at once."""
    global _cache_at
    if config.supabase_configured():
        rows = [{"key": k, "value": v} for k, v in values.items()]
        supa.insert("settings", rows, upsert_on="key")
    else:
        _local.update(values)
    with _lock:
        _cache_at = 0.0


def clear_cache() -> None:
    global _cache_at, _cache
    with _lock:
        _cache_at = 0.0
        _cache = {}
    _local.clear()


# ---------------------------------------------------------------- typed helpers

def llm_choice() -> tuple[str, str]:
    """Active (provider, model): settings row first, then env, then built-in default."""
    if get("provider"):
        provider, model = get("provider"), get("model")
    else:
        provider, model = config.env("LLM_PROVIDER"), config.env("LLM_MODEL")
    if provider not in config.DEFAULT_LLM_MODELS:
        provider, model = config.DEFAULT_LLM_PROVIDER, None
    return provider, model or config.DEFAULT_LLM_MODELS[provider]


def voice_id() -> str | None:
    """The raw voice setting (or ELEVENLABS_VOICE_ID), or None for captions only.

    Values look like "eleven:<id>", "edge:<ShortName>", or an older bare
    ElevenLabs id; app/voices.py parses them and decides the label.
    """
    value = get("voice_id") or config.env("ELEVENLABS_VOICE_ID")
    if not value or value == "none":
        return None
    return str(value)


def daily_voice_char_cap() -> int:
    raw = get("daily_voice_char_cap")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return config.env_int("DAILY_VOICE_CHAR_CAP", config.DEFAULT_DAILY_VOICE_CHAR_CAP)


def daily_free_voice_char_cap() -> int:
    """Daily characters for the free Microsoft voices: higher than ElevenLabs (no cost), but still
    capped to be a good citizen to a free service."""
    raw = get("daily_free_voice_char_cap")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return config.env_int("DAILY_FREE_VOICE_CHAR_CAP", config.DEFAULT_DAILY_FREE_VOICE_CHAR_CAP)


def index_version() -> str | None:
    value = get("index_version")
    return None if value is None else str(value)
