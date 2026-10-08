"""Signed audio links and the ElevenLabs voice call.

The free Microsoft voices live in app/edge_voice.py; app/voices.py decides
which voice speaks (tiers, labels, caps).

The voice only speaks text the backend wrote: `/api/ask` signs each narration
with AUDIO_SIGNING_SECRET and puts the text and signature in the audio link;
`/api/audio` refuses anything whose signature does not match.

Link: /api/audio?t=<base64url text>&v=<voice tag>&s=<hmac>
The voice tag is a short hash of the voice id, so changing the voice in
Settings changes every link (the browser cache never replays the old voice)
and old links stop working (403) instead of speaking in a different voice.

ElevenLabs (checked against elevenlabs.io/docs, Oct 2026):
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream?output_format=mp3_44100_128
  header xi-api-key, body {"text", "model_id"}; returns audio/mpeg bytes as a stream.
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream/with-timestamps (same price, same body;
  checked Oct 8): newline-delimited JSON, each line {"audio_base64", "alignment": {"characters",
  "character_start_times_seconds", "character_end_times_seconds"} | null, "normalized_alignment"}.
  The route uses this one (read-along, Oct 8): it decodes the audio for the browser and keeps the
  character times for the narration box (app/timings.py). A reply that is plain audio is passed through.
POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps: the same as one JSON object
  (indexer/pregenerate.py, for stored clips).
GET  https://api.elevenlabs.io/v2/voices?page_size=100 -> {"voices": [{voice_id, name, category, preview_url,
  is_owner, ...}], has_more, next_page_token}. Category is "cloned" for an instant clone, "professional" for a
  professional clone or a Voice Library voice (is_owner tells them apart), "premade" or "generated" for stock voices.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import threading
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from . import b64url, config
from .timings import ELEVEN_BYTES_PER_SECOND, Collector

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
STREAM_TIMESTAMPS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream/with-timestamps"
TIMESTAMPS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
VOICES_URL = "https://api.elevenlabs.io/v2/voices"
OUTPUT_FORMAT = "mp3_44100_128"
MAX_TEXT_CHARS = 1000  # narration is capped at 900 characters (config.NARRATION_MAX_CHARS)


class VoiceError(RuntimeError):
    pass


_b64e = b64url.encode
_b64d = b64url.decode


def voice_tag(voice_id: str) -> str:
    return hashlib.sha256(("voice:" + voice_id).encode()).hexdigest()[:10]


def sign(text: str, tag: str) -> str:
    mac = hmac.new(config.audio_secret(), f"{tag}\n{text}".encode(), hashlib.sha256).digest()
    return _b64e(mac[:20])


def audio_link(text: str, voice_id: str | None) -> str | None:
    """Signed link for one narration, or None when no voice is configured."""
    if not voice_id or not text:
        return None
    tag = voice_tag(voice_id)
    return f"/api/audio?t={_b64e(text.encode())}&v={tag}&s={sign(text, tag)}"


def timings_link(audio: str | None) -> str | None:
    """The read-along timings link for a live audio link: the same signed values on /api/audio/timings."""
    if not audio or not audio.startswith("/api/audio?"):
        return None
    return "/api/audio/timings?" + audio.split("?", 1)[1]


def verify(t: str, v: str, s: str) -> str | None:
    """Return the narration text if the signature is good, else None."""
    try:
        text = _b64d(t).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not text or len(text) > MAX_TEXT_CHARS:
        return None
    if not hmac.compare_digest(sign(text, v).encode(), s.encode("utf-8", "replace")):
        return None
    return text


def model_id() -> str:
    return config.env("ELEVENLABS_MODEL_ID", config.DEFAULT_ELEVENLABS_MODEL) or config.DEFAULT_ELEVENLABS_MODEL


def tts_request(text: str, voice_id: str,
                url: str = TTS_URL) -> tuple[str, dict[str, str], dict[str, str], dict[str, Any]]:
    """URL, headers, query and body for one ElevenLabs speech request. `url` picks the endpoint."""
    key = config.env("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    accept = "audio/mpeg" if url == TTS_URL else "application/json"
    headers = {"xi-api-key": key, "Content-Type": "application/json", "Accept": accept}
    params = {"output_format": OUTPUT_FORMAT}
    body = {"text": text, "model_id": model_id()}
    return url.format(voice_id=voice_id), headers, params, body


async def open_stream(text: str, voice_id: str) -> tuple[httpx.AsyncClient, httpx.Response]:
    """Start the TTS request (with character timestamps); pass the pair to `stream_bytes`, which closes both."""
    url, headers, params, body = tts_request(text, voice_id, STREAM_TIMESTAMPS_URL)
    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        req = client.build_request("POST", url, headers=headers, params=params, json=body)
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise VoiceError(f"voice request failed: {type(exc).__name__}") from exc
    if resp.status_code >= 400:
        detail = (await resp.aread())[:200]
        await resp.aclose()
        await client.aclose()
        raise VoiceError(f"voice service returned {resp.status_code}: {detail!r}")
    return client, resp


async def stream_bytes(
    client: httpx.AsyncClient, resp: httpx.Response, collector: Collector | None = None
) -> AsyncIterator[bytes]:
    """MP3 bytes for the browser.

    A with-timestamps reply (JSON lines, it starts with "{") is decoded and its
    character times go to `collector`. Anything else is plain audio and is
    passed through as it is, so the voice never depends on the timings.
    """
    sent = 0
    buffer = b""
    mode = ""  # "json" or "raw", decided by the first byte
    try:
        async for chunk in resp.aiter_bytes():
            if not mode:
                head = chunk.lstrip()
                if not head:
                    continue
                mode = "json" if head[:1] == b"{" else "raw"
            if mode == "raw":
                yield chunk
                continue
            buffer += chunk
            *lines, buffer = buffer.split(b"\n")
            for line in lines:
                audio = _json_line(line, collector, sent)
                if audio:
                    sent += len(audio)
                    yield audio
        if mode == "json" and buffer.strip():
            audio = _json_line(buffer, collector, sent)
            if audio:
                yield audio
    except Exception:
        if collector is not None:
            collector.failed = True
        raise
    finally:
        await resp.aclose()
        await client.aclose()


def _json_line(line: bytes, collector: Collector | None, sent: int) -> bytes:
    """One with-timestamps line: its audio bytes; its alignment goes to `collector`."""
    line = line.strip()
    if not line:
        return b""
    try:
        part = json.loads(line)
        audio = base64.b64decode(part.get("audio_base64") or "")
    except (ValueError, binascii.Error, AttributeError):
        return b""  # a line we cannot read: skip it rather than stop the voice
    if collector is not None:
        collector.add_alignment(part.get("alignment") or part.get("normalized_alignment"),
                                sent / ELEVEN_BYTES_PER_SECOND)
    return audio


def eleven_audio_and_words(data: dict[str, Any], text: str) -> tuple[bytes, list[list[float]]]:
    """The MP3 bytes and the aligned word timings from a non-streaming `/with-timestamps` reply."""
    try:
        audio = base64.b64decode(data["audio_base64"])
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        raise VoiceError("ElevenLabs returned an unexpected reply") from exc
    collector = Collector("elevenlabs")
    collector.add_alignment(data.get("alignment") or data.get("normalized_alignment"), 0.0)
    return audio, collector.result(text)


def list_voices(client: httpx.Client | None = None, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Every voice on the account (free to call: no characters are spent). Refreshes the cache."""
    key = config.env("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)))
    voices: list[dict[str, Any]] = []
    token: str | None = None
    try:
        for _page in range(5):
            params: dict[str, Any] = {"page_size": 100}
            if token:
                params["next_page_token"] = token
            resp = client.get(VOICES_URL, params=params, headers={"xi-api-key": key})
            if resp.status_code >= 400:
                raise VoiceError(f"voice list returned {resp.status_code}")
            data = resp.json()
            for v in data.get("voices", []):
                voices.append(
                    {
                        "voice_id": v.get("voice_id"),
                        "name": v.get("name"),
                        "category": v.get("category"),
                        "preview_url": v.get("preview_url"),
                        "is_owner": bool(v.get("is_owner")),
                    }
                )
            token = data.get("next_page_token")
            if not data.get("has_more") or not token:
                break
    except httpx.HTTPError as exc:
        raise VoiceError(f"voice list failed: {type(exc).__name__}") from exc
    finally:
        if own:
            client.close()
    with _cache_lock:
        _cache["voices"], _cache["at"] = voices, time.monotonic()
    return voices


# ---------------------------------------------------------------- cached lookups (labels)

CACHE_SECONDS = 3600.0  # a good voice list is reused for an hour
RETRY_SECONDS = 60.0  # after a failed list call, wait this long before asking ElevenLabs again
_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"voices": None, "at": 0.0, "failed_at": 0.0}


def clear_cache() -> None:
    with _cache_lock:
        _cache.update({"voices": None, "at": 0.0, "failed_at": 0.0})


def find_voice(voice_id: str, timeout: float = 4.0) -> dict[str, Any] | None:
    """One voice from the (cached) account list, or None when it is not there or the list is unavailable.

    Used to decide whether a voice is my clone, so the label students see
    matches the voice. A failure is remembered briefly so a student request
    never waits on a down ElevenLabs more than once a minute.
    """
    now = time.monotonic()
    with _cache_lock:
        voices = _cache["voices"]
        fresh = voices is not None and now - _cache["at"] < CACHE_SECONDS
        recently_failed = now - _cache["failed_at"] < RETRY_SECONDS if _cache["failed_at"] else False
    if not fresh and not recently_failed and config.env("ELEVENLABS_API_KEY"):
        try:
            voices = list_voices(timeout=timeout)
            with _cache_lock:
                _cache["voices"], _cache["at"], _cache["failed_at"] = voices, now, 0.0
        except VoiceError as exc:  # keep a stale list if there is one
            config.log.warning("voice list for labels failed: %s", exc)
            with _cache_lock:
                _cache["failed_at"] = now
    if not voices:
        return None
    return next((v for v in voices if v.get("voice_id") == voice_id), None)


def is_clone(meta: dict[str, Any]) -> bool:
    """True for a voice cloned from the account owner's own recordings.

    "cloned" is an instant clone made on this account. "professional" is a
    professional clone, but voices added from the Voice Library also carry it;
    only the ones this account owns are mine.
    """
    category = meta.get("category")
    return category == "cloned" or (category == "professional" and bool(meta.get("is_owner")))
