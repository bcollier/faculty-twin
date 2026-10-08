"""Admin-editable settings, stored as key/value rows in the Supabase `settings` table.

Keys used (docs/SPEC.md): provider, model, voice_id, voice_kind, voice_fallback,
voice_fallback_voice, daily_voice_char_cap, daily_free_voice_char_cap,
student_passcode_hash, index_version, pricing (Analytics price table). Rows are cached in memory for 30 seconds
so a busy function does not hit Postgres on every request. When Supabase is not
configured (local dev, tests) the env-var defaults apply and writes go to an
in-memory dict.

When Supabase is configured but a read fails, the last good copy is used (possibly
empty). `get_required` is for the few keys where "missing" must not mean "use the
env default" (the rotated student passcode hash): it raises `SettingsUnavailable`
when this instance has never read the table successfully (a cold start while
Supabase is down), so the caller can fail closed.
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
_generation = 0  # bumped by every write on this instance; a read that started before it is not cached
_local: dict[str, Any] = {}  # used only when Supabase is not configured
_ever_read = False  # this instance has read the settings table successfully at least once


class SettingsUnavailable(RuntimeError):
    """The settings table could not be read, and this instance has no good copy of it."""


def _load() -> dict[str, Any]:
    global _cache, _cache_at, _ever_read
    now = time.monotonic()
    with _lock:
        if _cache_at and now - _cache_at < CACHE_SECONDS:
            return _cache
        generation = _generation
    if not config.supabase_configured():
        return dict(_local)
    try:
        rows = supa.select("settings", {"select": "key,value"})
        values = {r["key"]: r["value"] for r in rows}
        _ever_read = True
    except supa.SupabaseError as exc:
        config.log.warning("settings read failed, using defaults: %s", exc)
        values = dict(_cache)  # last good copy, possibly empty
    with _lock:
        # A save on this instance while the read was in flight: the read may predate it, so do not
        # cache it for 30 s (Oct 8 code review: a saved model or passcode looked unsaved here).
        if generation == _generation:
            _cache, _cache_at = values, now
    return values


def get(key: str, default: Any = None) -> Any:
    value = _load().get(key)
    return default if value is None else value


def get_required(key: str, default: Any = None) -> Any:
    """Like `get`, but raises SettingsUnavailable on a cold instance that could not read the table.

    Added Oct 8 (code review): used where falling back to an env default would be unsafe.
    """
    values = _load()
    if config.supabase_configured() and not _ever_read:
        raise SettingsUnavailable("the settings table could not be read")
    value = values.get(key)
    return default if value is None else value


def all_values() -> dict[str, Any]:
    return dict(_load())


def put(values: dict[str, Any]) -> None:
    """Write settings and drop the cache so this instance sees them at once."""
    global _cache_at, _generation
    if config.supabase_configured():
        rows = [{"key": k, "value": v} for k, v in values.items()]
        supa.insert("settings", rows, upsert_on="key")
    else:
        _local.update(values)
    with _lock:
        _cache_at = 0.0
        _generation += 1


def clear_cache() -> None:
    global _cache_at, _cache, _generation, _ever_read
    with _lock:
        _cache_at = 0.0
        _cache = {}
        _generation += 1
        _ever_read = False
    _local.clear()


# ---------------------------------------------------------------- typed helpers

def llm_choice() -> tuple[str, str]:
    """Active (provider, model): an admin eval's per-request override first (app/llm.py
    `model_override`, never set on a student request), then the settings row, then env,
    then the built-in default."""
    from .llm import current_override

    override = current_override()
    if override is not None:
        return override
    if get("provider"):
        provider, model = get("provider"), get("model")
    else:
        provider, model = config.env("LLM_PROVIDER"), config.env("LLM_MODEL")
    if provider not in config.DEFAULT_LLM_MODELS:
        provider, model = config.DEFAULT_LLM_PROVIDER, None
    return provider, model or config.DEFAULT_LLM_MODELS[provider]


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
