"""Free Microsoft neural voices through the edge-tts package (no key, no cost).

edge-tts speaks to the same endpoint the Edge browser's Read Aloud uses, over
a websocket, and hands back MP3 (24 kHz mono, 48 kbit/s, constant bit rate).
Everything stays in memory: the route streams the bytes straight to the
browser, so a Vercel function needs no temp files.

The approach follows Ben's Ignatius app (app/tts.py there): split the text
into pieces of at most 400 characters at sentence boundaries, record the
pieces in parallel (at most 6 at once per process, to be a good citizen to a
free service), retry a piece with a growing pause, and join the MP3 pieces
byte for byte (constant bit rate, same format). Here the first piece is
streamed as it arrives so audio starts quickly, while the later pieces are
recorded in the background.
"""

from __future__ import annotations

import asyncio
import re
import time
import weakref
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import Any

import edge_tts

from . import config

# Curated teaching voices (Microsoft neural voices). Ids are edge-tts ShortNames.
FREE_VOICES: dict[str, tuple[str, str]] = {
    "en-US-AndrewMultilingualNeural": ("Andrew", "warm, confident, American male"),
    "en-US-AvaMultilingualNeural": ("Ava", "expressive, caring, American female"),
    "en-US-BrianMultilingualNeural": ("Brian", "approachable, easygoing, American male"),
    "en-US-EmmaMultilingualNeural": ("Emma", "clear, cheerful, American female"),
    "en-US-ChristopherNeural": ("Christopher", "steady, authoritative, American male"),
    "en-US-AriaNeural": ("Aria", "positive, confident, American female"),
    "en-GB-RyanNeural": ("Ryan", "calm, British male"),
    "en-GB-SoniaNeural": ("Sonia", "gentle, British female"),
}
DEFAULT_FREE_VOICE = "en-US-AndrewMultilingualNeural"

# Shape check before asking Microsoft: en-US-AndrewMultilingualNeural, zh-CN-liaoning-XiaobeiNeural.
SHORT_NAME_RE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,12}){1,3}-[A-Za-z]{2,40}Neural$")

CHUNK_CHARS = 400
PARALLEL = 6
ATTEMPTS = 3
CONNECT_TIMEOUT = 5  # seconds, passed to edge-tts
RECEIVE_TIMEOUT = 20  # seconds without a message from the service before edge-tts gives up
FIRST_AUDIO_TIMEOUT = 12.0  # seconds to the first audio bytes before a retry
PIECE_TIMEOUT = 45.0  # seconds for one background piece
DEFAULT_RATE = "-5%"  # a touch slower than Microsoft's default: easier to follow while reading a slide
DEFAULT_PITCH = "+0Hz"
LIST_CACHE_SECONDS = 12 * 3600.0
LIST_RETRY_SECONDS = 60.0

# Previews are fixed server-side text, never caller-supplied.
PREVIEW_TEXT = (
    "This is how the twin sounds when it explains a slide. "
    "On this slide, k-means groups similar points into clusters, then moves each center to the middle of its group."
)
PREVIEW_CACHE_MAX = 40

_RATE_RE = re.compile(r"^[+-]\d{1,2}%$")
_PITCH_RE = re.compile(r"^[+-]\d{1,2}Hz$")


class FreeVoiceError(RuntimeError):
    """The free voice service failed; safe to log, not shown to students."""


def rate() -> str:
    value = config.env("EDGE_TTS_RATE", DEFAULT_RATE) or DEFAULT_RATE
    return value if _RATE_RE.match(value) else DEFAULT_RATE


def pitch() -> str:
    value = config.env("EDGE_TTS_PITCH", DEFAULT_PITCH) or DEFAULT_PITCH
    return value if _PITCH_RE.match(value) else DEFAULT_PITCH


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """Split on paragraph, then sentence, boundaries so no piece exceeds `limit`."""
    pieces: list[str] = []
    current = ""
    sentences = [s for para in text.split("\n") for s in re.split(r"(?<=[.!?])\s+", para.strip()) if s]
    for sentence in sentences:
        while len(sentence) > limit:  # one enormous sentence: cut at the last space that fits
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > limit // 2 else limit
            if current:
                pieces.append(current)
                current = ""
            pieces.append(sentence[:cut].strip())
            sentence = sentence[cut:].strip()
        if current and len(current) + 1 + len(sentence) > limit:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


# One semaphore per event loop (asyncio primitives are bound to the loop that first waits on them).
_slots: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = weakref.WeakKeyDictionary()


def _slot() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    sem = _slots.get(loop)
    if sem is None:
        sem = _slots[loop] = asyncio.Semaphore(PARALLEL)
    return sem


def _communicate(text: str, voice: str) -> Any:
    return edge_tts.Communicate(
        text,
        voice,
        rate=rate(),
        pitch=pitch(),
        connect_timeout=CONNECT_TIMEOUT,
        receive_timeout=RECEIVE_TIMEOUT,
    )


async def _audio_chunks(text: str, voice: str) -> AsyncIterator[bytes]:
    async for chunk in _communicate(text, voice).stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            yield chunk["data"]


async def _backoff(attempt: int) -> None:
    await asyncio.sleep(0.5 * 2**attempt)  # 0.5 s, 1 s


async def synthesize(text: str, voice: str) -> bytes:
    """Record one piece to MP3 bytes, retried with a growing pause."""
    last: BaseException | None = None
    for attempt in range(ATTEMPTS):
        try:
            async with _slot():
                parts = await asyncio.wait_for(_collect(text, voice), PIECE_TIMEOUT)
            if parts:
                return parts
            last = FreeVoiceError("no audio returned")
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # edge-tts raises aiohttp, websocket, and its own errors
            last = exc
        if attempt < ATTEMPTS - 1:
            await _backoff(attempt)
    raise FreeVoiceError(f"free voice failed: {type(last).__name__}") from last


async def _collect(text: str, voice: str) -> bytes:
    return b"".join([c async for c in _audio_chunks(text, voice)])


async def _start(text: str, voice: str) -> tuple[AsyncIterator[bytes], bytes]:
    """Open the first piece and wait for its first audio bytes, retrying a dead start."""
    last: BaseException | None = None
    for attempt in range(ATTEMPTS):
        gen = _audio_chunks(text, voice)
        try:
            first = await asyncio.wait_for(gen.__anext__(), FIRST_AUDIO_TIMEOUT)
            return gen, first
        except asyncio.CancelledError:
            await gen.aclose()
            raise
        except StopAsyncIteration:
            last = FreeVoiceError("no audio returned")
        except Exception as exc:
            last = exc
        await gen.aclose()
        if attempt < ATTEMPTS - 1:
            await _backoff(attempt)
    raise FreeVoiceError(f"free voice failed to start: {type(last).__name__}") from last


async def open_stream(text: str, voice: str) -> AsyncIterator[bytes]:
    """Start speaking `text`; returns an async iterator of MP3 bytes once the first bytes exist.

    Raises FreeVoiceError before any byte is sent, so the route can still
    answer 502 (the frontend then shows captions). A failure later in the
    stream ends the audio early instead.
    """
    pieces = chunk_text(text)
    if not pieces:
        raise FreeVoiceError("nothing to say")
    rest = [asyncio.create_task(synthesize(p, voice)) for p in pieces[1:]]
    try:
        gen, first = await _start(pieces[0], voice)
    except BaseException:
        for task in rest:
            task.cancel()
        raise
    return _stream(gen, first, rest)


async def _stream(gen: AsyncIterator[bytes], first: bytes, rest: list[asyncio.Task]) -> AsyncIterator[bytes]:
    try:
        yield first
        async for chunk in gen:
            yield chunk
        for task in rest:
            yield await task
    except Exception as exc:
        config.log.warning("free voice stopped mid-stream: %s", type(exc).__name__)
    finally:
        for task in rest:
            if not task.done():
                task.cancel()
        await gen.aclose()  # type: ignore[attr-defined]


# ---------------------------------------------------------------- which voices exist

_list_cache: dict[str, Any] = {"names": None, "at": 0.0, "failed_at": 0.0}


def clear_cache() -> None:
    _list_cache.update({"names": None, "at": 0.0, "failed_at": 0.0})
    _preview_cache.clear()


async def available_voices() -> set[str] | None:
    """ShortNames Microsoft offers (cached 12 h), or None when the list cannot be fetched."""
    now = time.monotonic()
    names = _list_cache["names"]
    if names is not None and now - _list_cache["at"] < LIST_CACHE_SECONDS:
        return names
    if _list_cache["failed_at"] and now - _list_cache["failed_at"] < LIST_RETRY_SECONDS:
        return names
    try:
        voices = await asyncio.wait_for(edge_tts.list_voices(), 15.0)
        names = {str(v["ShortName"]) for v in voices if v.get("ShortName")}
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        config.log.warning("free voice list failed: %s", type(exc).__name__)
        _list_cache["failed_at"] = now
        return _list_cache["names"]
    _list_cache.update({"names": names, "at": now, "failed_at": 0.0})
    return names


async def is_known_voice(short_name: str) -> bool | None:
    """True or False, or None when Microsoft's list cannot be reached to check a non-curated name."""
    if short_name in FREE_VOICES:
        return True
    if not SHORT_NAME_RE.match(short_name or ""):
        return False
    names = await available_voices()
    if names is None:
        return None
    return short_name in names


def is_known_voice_sync(short_name: str) -> bool | None:
    """For sync routes (they run in a worker thread with no event loop)."""
    if short_name in FREE_VOICES:
        return True
    if not SHORT_NAME_RE.match(short_name or ""):
        return False
    return asyncio.run(is_known_voice(short_name))


def describe(short_name: str) -> tuple[str, str]:
    """(display name, description) for a ShortName."""
    if short_name in FREE_VOICES:
        return FREE_VOICES[short_name]
    parts = short_name.split("-")
    name = re.sub(r"(Multilingual)?Neural$", "", parts[-1]) or short_name
    return name, f"Microsoft neural voice ({'-'.join(parts[:-1])})"


# ---------------------------------------------------------------- previews (admin only)

_preview_cache: OrderedDict[str, bytes] = OrderedDict()


def preview_cached(short_name: str) -> bytes | None:
    """A voice preview already made in this process, or None."""
    data = _preview_cache.get(short_name)
    if data is not None:
        _preview_cache.move_to_end(short_name)
    return data


async def preview(short_name: str) -> bytes:
    """The fixed preview sentence in this voice, cached in memory."""
    cached = preview_cached(short_name)
    if cached is not None:
        return cached
    data = b"".join([await synthesize(p, short_name) for p in chunk_text(PREVIEW_TEXT)])
    _preview_cache[short_name] = data
    while len(_preview_cache) > PREVIEW_CACHE_MAX:
        _preview_cache.popitem(last=False)
    return data
