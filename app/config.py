"""Environment configuration.

Every value is read from the environment at call time (not import time) so a
cold start picks up Vercel's variables and tests can monkeypatch them. Nothing
here ever logs or returns a secret value; callers that report configuration
(the admin status route) only report booleans.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger("faculty_twin")

# Courses this deployment knows about. The index decides which sessions exist.
COURSE_CODES = ("70445", "45884")

QUESTION_MAX_CHARS = 300
NARRATION_MAX_WORDS = 110
PER_MINUTE_LIMIT = 5
PER_DAY_LIMIT = 30
DEFAULT_DAILY_VOICE_CHAR_CAP = 20000

DEFAULT_LLM_PROVIDER = "anthropic"
DEFAULT_LLM_MODELS = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-6.1-sol",
    "openrouter": "anthropic/claude-sonnet-5.5",
}
DEFAULT_VOYAGE_MODEL = "voyage-3.5"
DEFAULT_BUCKET = "twin-content"
DEFAULT_ELEVENLABS_MODEL = "eleven_multilingual_v2"


def env(name: str, default: str | None = None) -> str | None:
    """Return a stripped env var, or `default` when unset or blank."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def env_int(name: str, default: int) -> int:
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s is not an integer; using %s", name, default)
        return default


def content_dir() -> Path | None:
    """Local folder holding `content/`, `slides/`, `clips/` (dev and tests)."""
    raw = env("CONTENT_DIR")
    return Path(raw).expanduser() if raw else None


def supabase_configured() -> bool:
    return bool(env("SUPABASE_URL") and env("SUPABASE_SERVICE_ROLE_KEY"))


def bucket() -> str:
    return env("SUPABASE_BUCKET", DEFAULT_BUCKET) or DEFAULT_BUCKET


def is_production() -> bool:
    """True on Vercel (cookies get the Secure flag)."""
    return bool(env("VERCEL"))


def session_secret() -> bytes:
    """Key for signing cookies. Missing in local dev -> a fixed dev key, with a warning."""
    value = env("SESSION_SECRET")
    if not value:
        if is_production():
            raise RuntimeError("SESSION_SECRET is not set")
        log.warning("SESSION_SECRET not set; using an insecure local-dev key")
        value = "local-dev-session-secret"
    return value.encode()


def audio_secret() -> bytes:
    value = env("AUDIO_SIGNING_SECRET")
    if not value:
        if is_production():
            raise RuntimeError("AUDIO_SIGNING_SECRET is not set")
        log.warning("AUDIO_SIGNING_SECRET not set; using an insecure local-dev key")
        value = "local-dev-audio-secret"
    return value.encode()
