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
question log stores question text, scores, coverage, and model only.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, Request

from . import config, supa

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


def increment(
    key: str, amount: int, cap: int | None = None, ttl_seconds: int = 172800, fail_open: bool = True
) -> tuple[bool, int]:
    """Add `amount` to counter `key` unless that passes `cap`. Returns (allowed, value).

    On a database error, rate limits fail open (a short outage should not lock
    students out) but the voice cap fails closed (`fail_open=False`), since
    that one guards spending.
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
            return fail_open, 0
    _warn_once()
    with _mem_lock:
        current = _mem.get(key, 0)
        if cap is not None and current + amount > cap:
            return False, current
        _mem[key] = current + amount
        return True, _mem[key]


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

def check_ask_rate(visitor_hash: str, now: float | None = None) -> None:
    """5 questions per minute, 30 per day, per visitor. Raises 429 with a readable message."""
    t = now or time.time()
    minute = time.strftime("%Y%m%d%H%M", time.gmtime(t))
    day = time.strftime("%Y%m%d", time.gmtime(t))
    ok, _ = increment(f"rl:min:{visitor_hash}:{minute}", 1, cap=config.PER_MINUTE_LIMIT, ttl_seconds=300)
    if not ok:
        increment(f"rate_limited:{_today()}", 1)
        raise HTTPException(429, "That is a lot of questions in one minute. Please wait a moment and try again.")
    ok, _ = increment(f"rl:day:{visitor_hash}:{day}", 1, cap=config.PER_DAY_LIMIT, ttl_seconds=172800)
    if not ok:
        increment(f"rate_limited:{_today()}", 1)
        raise HTTPException(429, "You have reached today's limit of 30 questions. Please come back tomorrow.")
    increment(f"questions:{_today()}", 1)


# ---------------------------------------------------------------- login limit

def _client_hash(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    addr = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    salt = config.session_secret() + _today().encode()
    return hmac.new(salt, addr.encode(), hashlib.sha256).hexdigest()[:24]


def check_login_rate(request: Request, scope: str) -> None:
    window = int(time.time() // LOGIN_WINDOW_SECONDS)
    ok, _ = increment(
        f"login:{scope}:{_client_hash(request)}:{window}",
        1,
        cap=LOGIN_MAX_ATTEMPTS[scope],
        ttl_seconds=LOGIN_WINDOW_SECONDS * 2,
    )
    if not ok:
        raise HTTPException(429, "Too many tries. Wait a few minutes and try again.")


# ---------------------------------------------------------------- voice cap

def voice_key() -> str:
    return f"voice_chars:{_today()}"


def take_voice_chars(chars: int, cap: int) -> bool:
    """Reserve `chars` of today's voice budget. False when the cap would be passed."""
    if cap <= 0:
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
        "question": question[: config.QUESTION_MAX_CHARS],
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
