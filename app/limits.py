"""Rate limits, the daily voice caps, and the question log.

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
from datetime import UTC, datetime, timedelta
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
    return datetime.now(UTC).strftime("%Y-%m-%d")


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
    """A counter's value today (0 when it does not exist or cannot be read)."""
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
    taken: list[tuple[str, int]] = []
    for key, cap, ttl, message in checks:
        ok, _ = increment(key, 1, cap=cap, ttl_seconds=ttl)
        if not ok:
            _give_back(taken, 1)  # a refused question uses no one's quota (Oct 8 code review)
            increment(f"rate_limited:{_today()}", 1)
            raise HTTPException(429, message)
        taken.append((key, ttl))
    increment(f"questions:{_today()}", 1)


def _give_back(taken: list[tuple[str, int]], amount: int) -> None:
    """Undo increments already made for a request that was then refused. Best effort, no cap."""
    for key, ttl in taken:
        increment(key, -amount, ttl_seconds=ttl, fail_open=False)  # on a database error, skip (never in memory)


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


def daily_eval_call_cap() -> int:
    return config.env_int("DAILY_EVAL_LLM_CALL_CAP", config.DEFAULT_DAILY_EVAL_LLM_CALL_CAP)


def eval_calls_key() -> str:
    return f"eval_llm_calls:{_today()}"


def take_eval_call() -> bool:
    """Reserve one model call from today's admin-eval budget (Settings > Evals). Fails closed.

    Eval calls also go through `llm.complete_json`, so each one counts against
    the global cap as well; this budget keeps evals from using up the students' share.
    """
    cap = daily_eval_call_cap()
    if cap <= 0:
        return False
    ok, _ = increment(eval_calls_key(), 1, cap=cap, fail_open=False)
    return ok


def web_answers_key() -> str:
    """Today's count of web answers (app/web_answer.py takes one per answer, capped)."""
    return f"web_answers:{_today()}"


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

VOICE_POOLS = {"voice": "voice", "free": "free_voice"}  # ElevenLabs, free Microsoft voices


# Safari's <audio> fetches one link in pieces ("bytes=0-1", then the rest), and /api/audio streams the
# whole mp3 each time. Added Oct 8 (code review): only the first play of a signed link by a visitor in a
# day is charged to the voice cap, plus every play after AUDIO_FREE_REPEATS more, so a replay loop
# still spends the cap.
AUDIO_FREE_REPEATS = 3


def audio_play_is_charged(signature: str, visitor_hash: str | None) -> bool:
    """True when this request for a signed audio link should be charged to today's voice cap."""
    who = hashlib.sha256(f"{visitor_hash or '-'}:{signature}".encode()).hexdigest()[:24]
    _, plays = increment(f"audio_link:{_today()}:{who}", 1, ttl_seconds=172800)
    return plays <= 1 or plays > 1 + AUDIO_FREE_REPEATS


def voice_key(pool: str = "voice") -> str:
    return f"{VOICE_POOLS[pool]}_chars:{_today()}"


def take_voice_chars(
    chars: int,
    cap: int,
    visitor_hash: str | None = None,
    address_hash: str | None = None,
    pool: str = "voice",
) -> bool:
    """Reserve `chars` of today's budget for one voice pool. False when a cap would be passed.

    Two pools with separate caps: "voice" (ElevenLabs, costs money) and
    "free" (Microsoft voices through edge-tts). In each pool one visitor, and
    one address, may use at most VOICE_VISITOR_SHARE of the daily cap, so
    replaying a signed link in a loop cannot use up the voice for everyone.
    All counters fail closed: this guards spending and a free service.
    """
    if cap <= 0:
        return False
    taken = [(key, 172800) for key in _voice_keys(visitor_hash, address_hash, pool)]
    for i, (key, _ttl) in enumerate(taken):
        is_pool = i == len(taken) - 1
        limit = cap if is_pool else max(int(cap * config.VOICE_VISITOR_SHARE), 1)
        ok, _ = increment(key, chars, cap=limit, fail_open=False)
        if not ok:
            _give_back(taken[:i], chars)  # refused characters use no one's share (Oct 8 code review)
            return False
    return True


def _voice_keys(visitor_hash: str | None, address_hash: str | None, pool: str) -> list[str]:
    """The counters one voice request charges: the visitor's share, the address's share, then the pool."""
    prefix, day = VOICE_POOLS[pool], _today()
    keys = [f"{prefix}_share:{who}:{day}"
            for who in (visitor_hash and f"v:{visitor_hash}", address_hash and f"a:{address_hash}") if who]
    return keys + [voice_key(pool)]


def give_back_voice_chars(chars: int, visitor_hash: str | None = None, address_hash: str | None = None,
                          pool: str = "voice") -> None:
    """Return characters taken by `take_voice_chars` when the voice service then failed before any audio."""
    _give_back([(key, 172800) for key in _voice_keys(visitor_hash, address_hash, pool)], chars)


def today_counters() -> dict[str, int]:
    """Today's counters for Settings > Activity.

    Questions, coverage, rate limits, voice characters, model calls, embeddings and web answers.
    """
    day = _today()
    return {
        "questions": read_counter(f"questions:{day}"),
        "covered": read_counter(f"covered:{day}"),
        "not_covered": read_counter(f"not_covered:{day}"),
        "rate_limited": read_counter(f"rate_limited:{day}"),
        "voice_chars": read_counter(voice_key("voice")),
        "free_voice_chars": read_counter(voice_key("free")),
        "llm_calls": read_counter(f"llm_calls:{day}"),
        "embeddings": read_counter(f"embeds:{day}"),
        "eval_llm_calls": read_counter(f"eval_llm_calls:{day}"),
        "web_answers": read_counter(web_answers_key()),
    }


# ---------------------------------------------------------------- question log

# Added Oct 7 (analytics). Written when the columns exist; left out (with a warning) until the
# migration block in supabase/schema.sql has been run, so logging never stops.
ANALYTICS_COLUMNS = ("top_slide_id", "session", "session_title", "tokens_in", "tokens_out", "voice_chars", "source")
# Added Oct 8: why an answer fell back (app/course_info.py FALLBACK_REASONS), for Settings > Activity.
REASON_COLUMNS = ("fallback_reason",)


def log_question(
    question: str,
    top_score: float | None,
    covered: bool,
    provider: str | None,
    model: str | None,
    latency_ms: int | None = None,
    course: str | None = None,
    kind: str | None = None,
    **extra: Any,
) -> None:
    """One question-log row. See docs/TESTING_AND_SCORES.md for what each field means.

    `kind` is what answered: "course_content", "stored_topic", "faq", "course_info",
    "logistics", "web" (beyond the slides, from a web search), "cross_course" (the other course's slides
    when the course filter had none over the threshold), or "not_covered".
    `provider` and `model` are None when no model was called.
    `extra` may carry the analytics columns (ANALYTICS_COLUMNS): the top slide and its
    session, tokens in/out, voice characters signed, and where the question came from
    (chip, typed, follow_up, or a test source). Never a visitor id, a cookie, or an address.
    """
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
        "kind": kind,
    }
    for name in ANALYTICS_COLUMNS:
        if name in extra:
            row[name] = extra[name]
    for name in REASON_COLUMNS:  # only on rows that fell back, so other rows never need the column
        if extra.get(name):
            row[name] = extra[name]
    if config.supabase_configured():
        _insert_log_row(row)
        return
    _warn_once()
    row["at"] = datetime.now(UTC).isoformat()
    _mem_log.append(row)
    del _mem_log[:-2000]


def _insert_log_row(row: dict[str, Any]) -> None:
    """Insert, retrying with fewer columns on a database that has not had every migration.

    Until the migrations in supabase/schema.sql have run, keep logging with fewer columns:
    first without the fallback reason, then without the analytics columns, then without `kind`.
    A failed insert is logged and dropped: the question log never blocks an answer.
    """
    attempts = [row, {k: v for k, v in row.items() if k not in REASON_COLUMNS}]
    attempts.append({k: v for k, v in attempts[1].items() if k not in ANALYTICS_COLUMNS})
    attempts.append({k: v for k, v in attempts[2].items() if k != "kind"})
    for i, attempt in enumerate(attempts):
        if i and attempt == attempts[i - 1]:
            continue
        try:
            supa.insert("question_log", attempt)
            return
        except supa.SupabaseError as exc:
            config.log.warning("question log insert failed (%s columns): %s", len(attempt), exc)


LOG_COLUMNS = "at,question,covered,top_score,provider,model,latency_ms,course"


def missing_kind_column(exc: Exception) -> bool:
    """True when PostgREST refused a request because question_log has no `kind` column yet.

    A select of an unknown column fails with Postgres code 42703 ("column
    question_log.kind does not exist"); an insert fails with PGRST204 ("Could not
    find the 'kind' column ... in the schema cache").
    """
    text = str(exc)
    return "kind" in text and ("42703" in text or "PGRST204" in text or "does not exist" in text
                               or "Could not find" in text)


def recent_questions(limit: int = 50) -> list[dict[str, Any]]:
    """The newest question-log rows. Works before and after the `kind` column exists."""
    if config.supabase_configured():
        params = {"select": LOG_COLUMNS + ",kind", "order": "at.desc", "limit": str(limit)}
        try:  # with the fallback reason once its migration has run
            return supa.select("question_log", {**params, "select": LOG_COLUMNS + ",kind,source,fallback_reason"})
        except supa.SupabaseError as exc:
            if not missing_column(exc):
                raise
        try:
            # With `source` (test traffic badge) once the analytics migration has run.
            return supa.select("question_log", {**params, "select": LOG_COLUMNS + ",kind,source"})
        except supa.SupabaseError as exc:
            if not missing_column(exc):
                raise
        try:
            return supa.select("question_log", params)
        except supa.SupabaseError as exc:
            if not missing_kind_column(exc):
                raise
            # Run `alter table question_log add column if not exists kind text;` (supabase/schema.sql).
            config.log.warning("question_log has no kind column yet; reading without it")
            return supa.select("question_log", {**params, "select": LOG_COLUMNS})
    return list(reversed(_mem_log[-limit:]))


def missing_column(exc: Exception) -> bool:
    """True when PostgREST refused a request because some question_log column does not exist yet."""
    text = str(exc)
    return "42703" in text or "PGRST204" in text or ("column" in text and "does not exist" in text)


PAGE_ROWS = 1000  # PostgREST's default maximum rows per request


def log_rows(since_iso: str, max_rows: int = 10000) -> tuple[list[dict[str, Any]], bool]:
    """Question-log rows at or after `since_iso`, newest first, and whether the analytics columns exist.

    Tries every column, then without the analytics columns, then without `kind`.
    """
    if not config.supabase_configured():
        rows = [r for r in _mem_log if str(r.get("at", "")) >= since_iso]
        return list(reversed(rows))[:max_rows], True
    shapes = [
        (LOG_COLUMNS + ",kind," + ",".join(ANALYTICS_COLUMNS), True),
        (LOG_COLUMNS + ",kind", False),
        (LOG_COLUMNS, False),
    ]
    for columns, full in shapes:
        out: list[dict[str, Any]] = []
        try:
            while len(out) < max_rows:
                page = supa.select("question_log", {
                    "select": columns, "at": f"gte.{since_iso}", "order": "at.desc",
                    "limit": str(min(PAGE_ROWS, max_rows - len(out))), "offset": str(len(out)),
                })
                out.extend(page)
                if len(page) < PAGE_ROWS:
                    break
            return out, full
        except supa.SupabaseError as exc:
            if not missing_column(exc):
                raise
            config.log.warning("question_log lacks some columns; reading fewer (run supabase/schema.sql)")
    return [], False


def counters_since(prefixes: tuple[str, ...], since_day: str, max_rows: int = 20000) -> dict[str, int]:
    """Counters whose key starts with one of `prefixes` and whose day (second key part) is >= since_day."""

    def wanted(key: str) -> bool:
        parts = key.split(":")
        return key.startswith(prefixes) and len(parts) > 1 and parts[1] >= since_day

    if not config.supabase_configured():
        with _mem_lock:
            return {k: v for k, v in _mem.items() if wanted(k)}
    ors = ",".join(f'key.like."{p}*"' for p in prefixes)
    # The row's `day` is the database's date at insert; allow a day of slack, then filter on the key.
    db_since = (datetime.fromisoformat(since_day) - timedelta(days=1)).date().isoformat()
    out: dict[str, int] = {}
    offset = 0
    while offset < max_rows:
        page = supa.select("counters", {
            "select": "key,count", "or": f"({ors})", "day": f"gte.{db_since}", "order": "key",
            "limit": str(PAGE_ROWS), "offset": str(offset),
        })
        for r in page:
            if wanted(str(r.get("key", ""))):
                out[r["key"]] = int(r.get("count") or 0)
        if len(page) < PAGE_ROWS:
            break
        offset += PAGE_ROWS
    return out
