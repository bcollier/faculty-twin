"""Rate limits, the daily voice cap, and the question log.

Counters live in the Supabase `counters` table and are bumped through the
`ft_increment` RPC (see supabase/schema.sql), which adds atomically and refuses
the add when it would pass a cap. A Vercel function keeps no memory between
requests, so this has to live in Postgres.

When Supabase is not configured (local dev, tests) an in-process counter is
used instead and a warning is logged once. That keeps the limits testable and
still allows every request a normal local run would make.

Privacy: counter keys use a salted hash of the visitor id from the cookie. The
login limiter, which runs before there is a cookie, uses a salted hash of the
client address that rotates daily; the raw address is never stored. The
question log stores question text (scrubbed of emails, phone numbers, ids,
and recognisable names), scores, coverage, and model only.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from . import config, privacy, supa

_mem: dict[str, int] = {}
_mem_lock = threading.Lock()
_warned = False
_mem_log: list[dict[str, Any]] = []

LOGIN_WINDOW_SECONDS = 900  # 15 minutes
LOGIN_MAX_ATTEMPTS = {"student": 10, "admin": 5}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _warn_once() -> None:
    global _warned
    if not _warned:
        config.log.warning("Supabase not configured: counters and the question log are in memory only")
        _warned = True


def _mem_increment(key: str, amount: int, cap: int | None) -> tuple[bool, int]:
    with _mem_lock:
        current = _mem.get(key, 0)
        if cap is not None and current + amount > cap:
            return False, current
        _mem[key] = current + amount
        return True, _mem[key]


def increment(
    key: str, amount: int, cap: int | None = None, ttl_seconds: int = 172800, fail_open: bool = True
) -> tuple[bool, int]:
    """Add `amount` to counter `key` unless that passes `cap`. Returns (allowed, value).

    On a database error, rate limits (`fail_open=True`) fall back to a counter
    in this process's memory, so a short outage neither locks students out nor
    removes the limit. Spending caps (`fail_open=False`: the voice cap, the
    daily model and embedding caps, admin login) fail closed.
    """
    if config.supabase_configured():
        try:
            rows = supa.rpc(
                "ft_increment",
                {"p_key": key, "p_amount": amount, "p_cap": cap, "p_ttl_seconds": ttl_seconds},
            )
            row = rows[0] if isinstance(rows, list) and rows else rows
            return bool(row["allowed"]), int(row["count"])
        except (supa.SupabaseError, KeyError, TypeError, IndexError) as exc:
            config.log.warning("counter %s failed (fail_open=%s): %s", key.split(":")[0], fail_open, exc)
            if not fail_open:
                return False, 0
            return _mem_increment(key, amount, cap)
    _warn_once()
    return _mem_increment(key, amount, cap)


def read_counter(key: str) -> int:
    if config.supabase_configured():
        try:
            rows = supa.select("counters", {"select": "count", "key": f"eq.{key}"})
            return int(rows[0]["count"]) if rows else 0
        except supa.SupabaseError as exc:
            config.log.warning("counter read failed: %s", exc)
            return 0
    with _mem_lock:
        return _mem.get(key, 0)


def reset_memory() -> None:
    """Tests only."""
    with _mem_lock:
        _mem.clear()
    _mem_log.clear()


# ---------------------------------------------------------------- visitor rate limit

def check_ask_rate(visitor_hash: str, address_hash: str | None = None, now: float | None = None) -> None:
    """Per visitor: 5 questions per minute, 30 per day. Per network address: 20 per minute, 300 per day.

    The visitor id lives in the cookie, and logging in again mints a new one,
    so the per-address bucket (a salted hash that rotates daily, kept only in
    `counters`) stops one person from multiplying their allowance. Raises 429.
    """
    t = now or time.time()
    minute = time.strftime("%Y%m%d%H%M", time.gmtime(t))
    day = time.strftime("%Y%m%d", time.gmtime(t))
    checks = [
        (f"rl:min:{visitor_hash}:{minute}", config.PER_MINUTE_LIMIT, 300,
         "That is a lot of questions in one minute. Please wait a moment and try again."),
        (f"rl:day:{visitor_hash}:{day}", config.PER_DAY_LIMIT, 172800,
         "You have reached today's limit of 30 questions. Please come back tomorrow."),
    ]
    if address_hash:
        checks += [
            (f"rl:addr-min:{address_hash}:{minute}", config.PER_ADDRESS_MINUTE_LIMIT, 300,
             "Lots of questions are coming from your network right now. Please wait a minute and try again."),
            (f"rl:addr-day:{address_hash}:{day}", config.PER_ADDRESS_DAY_LIMIT, 172800,
             "Your network has reached today's question limit. Please come back tomorrow."),
        ]
    for key, cap, ttl, message in checks:
        ok, _ = increment(key, 1, cap=cap, ttl_seconds=ttl)
        if not ok:
            increment(f"rate_limited:{_today()}", 1)
            raise HTTPException(429, message)
    increment(f"questions:{_today()}", 1)


# ---------------------------------------------------------------- global spend caps

def daily_llm_call_cap() -> int:
    return config.env_int("DAILY_LLM_CALL_CAP", config.DEFAULT_DAILY_LLM_CALL_CAP)


def daily_embed_cap() -> int:
    return config.env_int("DAILY_EMBED_CAP", config.DEFAULT_DAILY_EMBED_CAP)


def take_llm_call() -> bool:
    """Reserve one narration call from today's global budget. Fails closed."""
    cap = daily_llm_call_cap()
    if cap <= 0:
        return False
    ok, _ = increment(f"llm_calls:{_today()}", 1, cap=cap, fail_open=False)
    return ok


def take_embedding() -> bool:
    """Reserve one question embedding from today's global budget. Fails closed."""
    cap = daily_embed_cap()
    if cap <= 0:
        return False
    ok, _ = increment(f"embeds:{_today()}", 1, cap=cap, fail_open=False)
    return ok


# ---------------------------------------------------------------- login limit

def client_address(request: Request) -> str:
    """The caller's network address, from a source the caller cannot set.

    On Vercel the edge overwrites x-forwarded-for / x-real-ip and sets
    x-vercel-forwarded-for, so those are trusted there. Anywhere else (local
    dev, the Render fallback) a client-sent X-Forwarded-For would let anyone
    reset the login limit per request, so only the socket peer counts.
    """
    if config.is_production():
        for name in ("x-vercel-forwarded-for", "x-real-ip", "x-forwarded-for"):
            value = request.headers.get(name, "").split(",")[0].strip()
            if value:
                return value
    return request.client.host if request.client else "unknown"


def client_hash(request: Request) -> str:
    """Salted, daily-rotating hash of the caller's address. Stored only as a counter key."""
    salt = config.session_secret() + _today().encode()
    return hmac.new(salt, client_address(request).encode(), hashlib.sha256).hexdigest()[:24]


_client_hash = client_hash  # older name


def check_login_rate(request: Request, scope: str) -> None:
    """10 tries per 15 minutes per address for students, 5 for admin.

    Keyed on the address hash, not the cookie, so dropping cookies does not
    reset it. The admin limiter fails closed when the database is unreachable.
    """
    window = int(time.time() // LOGIN_WINDOW_SECONDS)
    ok, _ = increment(
        f"login:{scope}:{client_hash(request)}:{window}",
        1,
        cap=LOGIN_MAX_ATTEMPTS[scope],
        ttl_seconds=LOGIN_WINDOW_SECONDS * 2,
        fail_open=scope != "admin",
    )
    if not ok:
        raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")


# ---------------------------------------------------------------- voice cap

def voice_key() -> str:
    return f"voice_chars:{_today()}"


def take_voice_chars(
    chars: int, cap: int, visitor_hash: str | None = None, address_hash: str | None = None
) -> bool:
    """Reserve `chars` of today's voice budget. False when a cap would be passed.

    One visitor, and one address, may use at most VOICE_VISITOR_SHARE of the
    daily cap, so replaying a signed link in a loop cannot use up the voice for
    everyone. All three counters fail closed: this guards spending.
    """
    if cap <= 0:
        return False
    share = max(int(cap * config.VOICE_VISITOR_SHARE), 1)
    day = _today()
    for who in (visitor_hash and f"v:{visitor_hash}", address_hash and f"a:{address_hash}"):
        if who:
            ok, _ = increment(f"voice_share:{who}:{day}", chars, cap=share, fail_open=False)
            if not ok:
                return False
    ok, _ = increment(voice_key(), chars, cap=cap, fail_open=False)
    return ok


def today_counters() -> dict[str, int]:
    day = _today()
    return {
        "questions": read_counter(f"questions:{day}"),
        "covered": read_counter(f"covered:{day}"),
        "not_covered": read_counter(f"not_covered:{day}"),
        "rate_limited": read_counter(f"rate_limited:{day}"),
        "voice_chars": read_counter(voice_key()),
        "llm_calls": read_counter(f"llm_calls:{day}"),
        "embeddings": read_counter(f"embeds:{day}"),
    }


# ---------------------------------------------------------------- question log

def log_question(
    question: str,
    top_score: float | None,
    covered: bool,
    provider: str | None,
    model: str | None,
    latency_ms: int | None = None,
    course: str | None = None,
) -> None:
    increment(f"{'covered' if covered else 'not_covered'}:{_today()}", 1)
    row = {
        # Scrubbed of emails, numbers, and recognisable names first (app/privacy.py).
        "question": privacy.scrub_question(question)[: config.QUESTION_MAX_CHARS],
        "top_score": None if top_score is None else round(float(top_score), 4),
        "covered": covered,
        "provider": provider,
        "model": model,
        "latency_ms": latency_ms,
        "course": course,
    }
    if config.supabase_configured():
        try:
            supa.insert("question_log", row)
        except supa.SupabaseError as exc:
            config.log.warning("question log insert failed: %s", exc)
        return
    _warn_once()
    row["at"] = datetime.now(timezone.utc).isoformat()
    _mem_log.append(row)
    del _mem_log[:-200]


def recent_questions(limit: int = 50) -> list[dict[str, Any]]:
    if config.supabase_configured():
        return supa.select(
            "question_log",
            {
                "select": "at,question,covered,top_score,provider,model,latency_ms,course",
                "order": "at.desc",
                "limit": str(limit),
            },
        )
    return list(reversed(_mem_log[-limit:]))
