"""Signed audio links and the ElevenLabs voice call.

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
GET  https://api.elevenlabs.io/v2/voices?page_size=100 -> {"voices": [{voice_id, name, category, preview_url}], ...}
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any, AsyncIterator

import httpx

from . import config

TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
VOICES_URL = "https://api.elevenlabs.io/v2/voices"
OUTPUT_FORMAT = "mp3_44100_128"
MAX_TEXT_CHARS = 1500  # 110 words is well under this


class VoiceError(RuntimeError):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


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


def verify(t: str, v: str, s: str) -> str | None:
    """Return the narration text if the signature is good, else None."""
    try:
        text = _b64d(t).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if not text or len(text) > MAX_TEXT_CHARS:
        return None
    if not hmac.compare_digest(sign(text, v), s):
        return None
    return text


def model_id() -> str:
    return config.env("ELEVENLABS_MODEL_ID", config.DEFAULT_ELEVENLABS_MODEL) or config.DEFAULT_ELEVENLABS_MODEL


def tts_request(text: str, voice_id: str) -> tuple[str, dict[str, str], dict[str, str], dict[str, Any]]:
    key = config.env("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    url = TTS_URL.format(voice_id=voice_id)
    headers = {"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"}
    params = {"output_format": OUTPUT_FORMAT}
    body = {"text": text, "model_id": model_id()}
    return url, headers, params, body


async def open_stream(text: str, voice_id: str) -> tuple[httpx.AsyncClient, httpx.Response]:
    """Start the TTS request; caller iterates `resp.aiter_bytes()` then closes both."""
    url, headers, params, body = tts_request(text, voice_id)
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


async def stream_bytes(client: httpx.AsyncClient, resp: httpx.Response) -> AsyncIterator[bytes]:
    try:
        async for chunk in resp.aiter_bytes():
            yield chunk
    finally:
        await resp.aclose()
        await client.aclose()


def list_voices(client: httpx.Client | None = None) -> list[dict[str, Any]]:
    key = config.env("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    own = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0))
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
    return voices
