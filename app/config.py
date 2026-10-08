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
DEFAULT_DAILY_VOICE_CHAR_CAP = 20000  # ElevenLabs characters per UTC day (costs money)
DEFAULT_DAILY_FREE_VOICE_CHAR_CAP = 200000  # free Microsoft voices per UTC day (no cost; be a good citizen)

# Spend guards that do not depend on the visitor id (a student with the
# passcode can mint new visitor ids by logging in again). See docs/SECURITY.md.
PER_ADDRESS_MINUTE_LIMIT = 20  # questions per minute from one network address (salted daily hash)
PER_ADDRESS_DAY_LIMIT = 300  # a classroom behind one NAT stays well under this
DEFAULT_DAILY_LLM_CALL_CAP = 600  # all narration calls, every visitor, per UTC day (fails closed)
DEFAULT_DAILY_EMBED_CAP = 1500  # question embeddings, every visitor, per UTC day (fails closed)
VOICE_VISITOR_SHARE = 0.25  # one visitor (or one address) may use at most this share of each daily voice cap
NARRATION_MAX_CHARS = 900  # 110 words of normal prose is about 700 characters
# Settings > Evals (admin only) has its own budget on top of the global model-call cap above.
DEFAULT_DAILY_EVAL_LLM_CALL_CAP = 300  # model calls by admin eval runs per UTC day (DAILY_EVAL_LLM_CALL_CAP)
DEFAULT_EVAL_MAX_CALLS_PER_RUN = 300  # model calls one admin eval run may make (EVAL_MAX_CALLS_PER_RUN)
DEFAULT_EVAL_STUDENT_RESERVE = 100  # an eval step waits rather than leave students fewer global calls than this
EVAL_MAX_QUESTIONS = 30
EVAL_MAX_GENERATORS = 6  # raised from 3 on Oct 8 for model comparisons (docs/SPEC.md, Block 8c)
EVAL_MAX_JUDGES = 6  # raised from 3 on Oct 8 so a run can use judges from several vendors
# OpenRouter lets the admin pick any model; refuse ones priced above this (USD per million tokens).
DEFAULT_MAX_PRICE_PER_MTOK = {"prompt": 15.0, "completion": 60.0}

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
    """An integer environment variable; `default` when it is unset or not a number."""
    raw = env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s is not an integer; using %s", name, default)
        return default


def content_dir() -> Path | None:
    """Local folder holding `content/`, `slides/`, `clips/` (dev and tests).

    Never used in production: the dev-only `/api/files` route must not be
    switchable on by setting CONTENT_DIR in Vercel.
    """
    raw = env("CONTENT_DIR")
    if not raw:
        return None
    if is_production():
        log.warning("CONTENT_DIR is ignored in production")
        return None
    return Path(raw).expanduser()


def supabase_configured() -> bool:
    return bool(env("SUPABASE_URL") and env("SUPABASE_SERVICE_ROLE_KEY"))


def bucket() -> str:
    return env("SUPABASE_BUCKET", DEFAULT_BUCKET) or DEFAULT_BUCKET


def is_production() -> bool:
    """True on Vercel (cookies get the Secure flag, CONTENT_DIR is ignored).

    VERCEL=1 only appears when the project exposes System Environment
    Variables, so the other Vercel runtime markers count too, and FT_ENV can
    force it either way.
    """
    forced = (env("FT_ENV") or "").lower()
    if forced in ("production", "prod"):
        return True
    if forced in ("development", "dev", "test"):
        return False
    return any(env(name) for name in ("VERCEL", "VERCEL_ENV", "VERCEL_REGION", "VERCEL_URL"))


MIN_SECRET_CHARS = 32


def _secret(name: str, dev_value: str) -> bytes:
    """A signing key from the environment.

    There is no silent fallback: a missing key in a deployment would mean
    signing with a value printed in this public repo, so anyone could forge
    audio links (making the voice say anything) or cookies. A fixed dev key is
    used only when FT_LOCAL_DEV=1 is set explicitly, and never in production.
    """
    value = env(name)
    if value:
        if is_production() and len(value) < MIN_SECRET_CHARS:
            raise RuntimeError(f"{name} is too short; use at least {MIN_SECRET_CHARS} random characters")
        return value.encode()
    if env("FT_LOCAL_DEV") == "1" and not is_production():
        log.warning("%s not set; using an insecure local-dev key (FT_LOCAL_DEV=1)", name)
        return dev_value.encode()
    raise RuntimeError(f"{name} is not set")


def session_secret() -> bytes:
    """Key for signing cookies."""
    return _secret("SESSION_SECRET", "local-dev-session-secret")


def audio_secret() -> bytes:
    """Key for signing audio links."""
    return _secret("AUDIO_SIGNING_SECRET", "local-dev-audio-secret")
