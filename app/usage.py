"""Usage metering: every model call, embedding, voice request, and client event, counted in one place.

Settings > Analytics reads these counters to show tokens and characters per
provider, model and purpose, and to estimate spend (app/pricing.py). They live
in the existing `counters` table, bumped through the atomic `ft_increment`
RPC, so no new table is needed. Keys (docs/SPEC.md, "Usage counters"):

    usage:<YYYY-MM-DD>:<purpose>:<provider>:<model>:in|out|calls   model tokens and calls
    embed:<YYYY-MM-DD>:voyage:<model>:tokens|calls                 Voyage query embeddings
    tts:<YYYY-MM-DD>:<voice tier>:chars|calls                      clone | stock | unverified | free
    event:<YYYY-MM-DD>:<name>                                      allowlisted client events
    faq:<YYYY-MM-DD>:<entry id>                                    FAQ hits per entry

Model ids may contain colons (OpenRouter "...:free"), so `parse_key` reads the
day, purpose and provider from the left and the metric from the right.

Recording never slows or breaks an answer: with Supabase configured each
record is handed to a small thread pool (fire and forget, failures logged);
`flush()` runs as a background task after the response is sent so a Vercel
invocation does not freeze with writes still pending. Without Supabase (local
dev, tests) the in-memory counters are bumped inline.

Purpose: callers wrap model calls in `with usage.purpose("narration"):`. An
outer `sticky` purpose (a Settings test, an eval run) wins over the inner tags,
so a test's narration call is counted as `prompt_test`, not `narration`.
`with usage.tally() as t:` adds up one request's tokens for its question-log row.
"""

from __future__ import annotations

import contextlib
import contextvars
import re
import threading
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

from . import config

PURPOSES = (
    "narration",
    "logistics",
    "course_info",
    "prompt_test",
    "smoke_test",
    "eval_generate",
    "eval_judge",
    "topic_label",
    "embed_query",
    "tts",
    "other",
)
# Client events the student page may send (POST /api/event). Names only: no free text, no ids.
EVENT_NAMES = (
    "chip_tap",
    "question_typed",
    "segment_played",  # once per answer, when its first segment starts
    "walkthrough_completed",
    "clip_played",
    "audio_failed",
    "follow_up_tapped",
    "course_filter_changed",
)
VOICE_TIERS = ("clone", "stock", "unverified", "free")
# question_log.source: where a student's question came from, or which kind of test sent it.
QUESTION_SOURCES = ("chip", "typed", "follow_up")
TEST_SOURCES = ("smoke", "eval", "prompt_test")
SOURCES = QUESTION_SOURCES + TEST_SOURCES
# The test sources a client may claim with the X-FT-Source header (a tag only; it changes nothing else).
HEADER_SOURCES = TEST_SOURCES
# Test traffic spends under these purposes, so student narration totals stay clean.
TEST_PURPOSES = {"smoke": "smoke_test", "eval": "eval_generate", "prompt_test": "prompt_test"}
# The three questions scripts/live_smoke.py asks. Rows logged before `source` existed that match one
# exactly are shown as likely tests.
SMOKE_QUESTIONS = (
    "How does k-means decide which cluster a point belongs to?",
    "Who won the Stanley Cup last year?",
    "When are your office hours?",
)


def is_test_traffic(row: dict[str, Any]) -> tuple[bool, bool]:
    """(is test traffic, only inferred) for a question-log row.

    Recorded `source` wins. A row with no source whose question is exactly a smoke-check
    question is a likely test (inferred), since real students rarely type those word for word.
    """
    source = row.get("source")
    if source:
        return source in TEST_SOURCES, False
    return str(row.get("question") or "").strip() in SMOKE_QUESTIONS, True
TTL_SECONDS = 400 * 86400  # analytics counters outlive the 90-day view

_SAFE = re.compile(r"[^A-Za-z0-9._/~@+\-:]")

# ---------------------------------------------------------------- purpose and per-request tally

_purpose: contextvars.ContextVar[Optional[tuple[str, bool]]] = contextvars.ContextVar("ft_usage_purpose", default=None)


@dataclass
class Tally:
    tokens_in: int = 0
    tokens_out: int = 0
    calls: int = 0


_tally: contextvars.ContextVar[Optional[Tally]] = contextvars.ContextVar("ft_usage_tally", default=None)


@contextlib.contextmanager
def purpose(name: str, sticky: bool = False) -> Iterator[None]:
    """Tag the model calls inside this block. A sticky outer tag is never overridden."""
    outer = _purpose.get()
    if outer is not None and outer[1]:
        yield
        return
    token = _purpose.set((name if name in PURPOSES else "other", sticky))
    try:
        yield
    finally:
        _purpose.reset(token)


def current_purpose() -> str:
    value = _purpose.get()
    return value[0] if value else "other"


@contextlib.contextmanager
def tally() -> Iterator[Tally]:
    """Add up the tokens of every model call in this block (one question's log row)."""
    t = Tally()
    token = _tally.set(t)
    try:
        yield t
    finally:
        _tally.reset(token)


# ---------------------------------------------------------------- provider usage fields

def _int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def parse_llm_usage(provider: str, data: Any) -> tuple[int, int]:
    """(input tokens, output tokens) from a provider reply; (0, 0) when it has none.

    Anthropic: usage.input_tokens (+ cache_creation_input_tokens + cache_read_input_tokens), usage.output_tokens.
    OpenAI and OpenRouter: usage.prompt_tokens, usage.completion_tokens.
    """
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    if provider == "anthropic":
        tin = _int(usage.get("input_tokens")) + _int(usage.get("cache_creation_input_tokens")) + _int(
            usage.get("cache_read_input_tokens")
        )
        return tin, _int(usage.get("output_tokens"))
    return _int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens"))


def parse_embed_usage(data: Any, texts: list[str] | None = None) -> int:
    """Voyage `usage.total_tokens`; a rough estimate (4 characters a token) when the field is missing."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict) and usage.get("total_tokens") is not None:
        return _int(usage.get("total_tokens"))
    return sum(max(1, len(t) // 4) for t in texts or [])


# ---------------------------------------------------------------- keys

def _day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _part(text: Any) -> str:
    return _SAFE.sub("_", str(text or "unknown"))[:120] or "unknown"


def usage_key(day: str, purpose_name: str, provider: str, model: str, metric: str) -> str:
    return f"usage:{day}:{_part(purpose_name)}:{_part(provider)}:{_part(model)}:{metric}"


def embed_key(day: str, model: str, metric: str) -> str:
    return f"embed:{day}:voyage:{_part(model)}:{metric}"


def tts_key(day: str, tier: str, metric: str = "chars") -> str:
    return f"tts:{day}:{tier if tier in VOICE_TIERS else 'unverified'}:{metric}"


def event_key(day: str, name: str) -> str:
    return f"event:{day}:{name}"


def faq_key(day: str, entry_id: str) -> str:
    return f"faq:{day}:{_part(entry_id)}"


PREFIXES = ("usage:", "embed:", "tts:", "event:", "faq:")


def parse_key(key: str) -> Optional[dict[str, Any]]:
    """Split an analytics counter key into its parts, or None when it is not one."""
    parts = key.split(":")
    kind = parts[0]
    try:
        if kind == "usage" and len(parts) >= 6:
            return {"kind": kind, "day": parts[1], "purpose": parts[2], "provider": parts[3],
                    "model": ":".join(parts[4:-1]), "metric": parts[-1]}
        if kind == "embed" and len(parts) >= 5:
            return {"kind": kind, "day": parts[1], "provider": parts[2], "model": ":".join(parts[3:-1]),
                    "metric": parts[-1]}
        if kind == "tts" and len(parts) == 4:
            return {"kind": kind, "day": parts[1], "tier": parts[2], "metric": parts[3]}
        if kind in ("event", "faq") and len(parts) >= 3:
            return {"kind": kind, "day": parts[1], "name": ":".join(parts[2:])}
    except IndexError:
        return None
    return None


# ---------------------------------------------------------------- fire and forget

_pool: Optional[ThreadPoolExecutor] = None
_pool_lock = threading.Lock()
_pending: set[Future] = set()


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ft-usage")
        return _pool


def _bump_all(items: list[tuple[str, int]]) -> None:
    from . import limits

    for key, amount in items:
        if amount <= 0:
            continue
        try:
            limits.increment(key, amount, ttl_seconds=TTL_SECONDS)
        except Exception as exc:  # never let metering raise
            config.log.warning("usage counter %s failed: %s", key.split(":")[0], exc)


def _submit(items: list[tuple[str, int]]) -> None:
    if not config.supabase_configured():
        _bump_all(items)  # in-memory counters: instant, and deterministic for tests
        return
    try:
        fut = _executor().submit(_bump_all, items)
    except RuntimeError as exc:  # interpreter shutting down
        config.log.warning("usage recording skipped: %s", exc)
        return
    with _pool_lock:
        _pending.add(fut)
    fut.add_done_callback(_forget)


def _forget(fut: Future) -> None:
    with _pool_lock:
        _pending.discard(fut)


def flush(timeout: float = 3.0) -> None:
    """Wait (at most `timeout` seconds) for pending counter writes. Runs after the response is sent."""
    with _pool_lock:
        pending = list(_pending)
    if pending:
        wait(pending, timeout=timeout)


def pending_count() -> int:
    with _pool_lock:
        return len(_pending)


def _safely(fn: Callable[..., None]) -> Callable[..., None]:
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as exc:  # metering must never break the caller
            config.log.warning("usage %s failed: %s", fn.__name__, exc)

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


# ---------------------------------------------------------------- record

@_safely
def record_llm(provider: str, model: str, data: Any, purpose_name: Optional[str] = None) -> None:
    """One model call: its input and output tokens, under the current purpose."""
    tin, tout = parse_llm_usage(provider, data)
    t = _tally.get()
    if t is not None:
        t.tokens_in += tin
        t.tokens_out += tout
        t.calls += 1
    day, p = _day(), purpose_name or current_purpose()
    _submit([
        (usage_key(day, p, provider, model, "calls"), 1),
        (usage_key(day, p, provider, model, "in"), tin),
        (usage_key(day, p, provider, model, "out"), tout),
    ])


@_safely
def record_embed(model: str, tokens: int) -> None:
    day = _day()
    _submit([(embed_key(day, model, "calls"), 1), (embed_key(day, model, "tokens"), _int(tokens))])


@_safely
def record_tts(tier: Optional[str], chars: int) -> None:
    """Characters sent to a voice: ElevenLabs tiers (clone, stock, unverified) cost money; "free" is edge-tts."""
    day, tier = _day(), tier if tier in VOICE_TIERS else "unverified"
    _submit([(tts_key(day, tier, "calls"), 1), (tts_key(day, tier, "chars"), _int(chars))])


@_safely
def record_event(name: str) -> None:
    if name not in EVENT_NAMES:
        return
    _submit([(event_key(_day(), name), 1)])


@_safely
def record_faq(entry_id: Optional[str]) -> None:
    if entry_id:
        _submit([(faq_key(_day(), entry_id), 1)])
