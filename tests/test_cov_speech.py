"""app/speech.py: audio-link signing edge cases, the ElevenLabs stream and voice list (faked), and the label cache."""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from app import speech


def _parts(link: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}


# ---------------------------------------------------------------- signing

def test_link_round_trips_unicode_and_tag_is_per_voice():
    text = "Café, naïve, and 50% more: résumé."
    q = _parts(speech.audio_link(text, "voiceA"))
    assert speech.verify(q["t"], q["v"], q["s"]) == text
    assert q["v"] == speech.voice_tag("voiceA") != speech.voice_tag("voiceB")
    assert len(q["v"]) == 10


def test_no_link_without_text_or_voice():
    assert speech.audio_link("", "voiceA") is None
    assert speech.audio_link("Hello", "") is None


@pytest.mark.parametrize("t", ["***", "%%%", "a"])
def test_verify_rejects_garbage_text(t):
    assert speech.verify(t, "tag", "sig") is None


def test_verify_rejects_non_utf8_and_empty():
    bad = speech._b64e(b"\xff\xfe\xfa")
    assert speech.verify(bad, "tag", speech.sign("x", "tag")) is None
    assert speech.verify("", "tag", speech.sign("", "tag")) is None


def test_verify_rejects_text_over_the_cap_even_when_signed():
    text = "a" * (speech.MAX_TEXT_CHARS + 1)
    tag = speech.voice_tag("v")
    assert speech.verify(speech._b64e(text.encode()), tag, speech.sign(text, tag)) is None
    ok = "a" * speech.MAX_TEXT_CHARS
    assert speech.verify(speech._b64e(ok.encode()), tag, speech.sign(ok, tag)) == ok


def test_signature_depends_on_the_secret(monkeypatch):
    q = _parts(speech.audio_link("Signed with secret one.", "v1"))
    monkeypatch.setenv("AUDIO_SIGNING_SECRET", "a-completely-different-secret-0123456789")
    assert speech.verify(q["t"], q["v"], q["s"]) is None


def test_tampered_signature_and_swapped_tag_fail():
    q = _parts(speech.audio_link("Exactly this text.", "v1"))
    flipped = ("A" if q["s"][0] != "A" else "B") + q["s"][1:]
    assert speech.verify(q["t"], q["v"], flipped) is None
    assert speech.verify(q["t"], speech.voice_tag("v2"), q["s"]) is None
    assert speech.verify(q["t"], q["v"], q["s"] + "é") is None  # non-ascii signature never matches


def test_model_id_env_override(monkeypatch):
    assert speech.model_id() == "eleven_multilingual_v2"
    monkeypatch.setenv("ELEVENLABS_MODEL_ID", "eleven_flash_v3")
    assert speech.model_id() == "eleven_flash_v3"


def test_tts_request_needs_a_key():
    with pytest.raises(speech.VoiceError, match="ELEVENLABS_API_KEY"):
        speech.tts_request("Hi", "abc")


# ---------------------------------------------------------------- streaming (faked transport)

def _fake_async_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(speech.httpx, "AsyncClient", factory)


def test_open_stream_and_stream_bytes(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"ID3" + b"x" * 5000, headers={"content-type": "audio/mpeg"})

    _fake_async_client(monkeypatch, handler)

    async def go():
        client, resp = await speech.open_stream("Narration.", "voice123")
        chunks = [c async for c in speech.stream_bytes(client, resp)]
        return client, b"".join(chunks)

    client, data = asyncio.run(go())
    assert data.startswith(b"ID3") and len(data) == 5003
    assert client.is_closed
    req = seen[0]
    assert req.url.path == "/v1/text-to-speech/voice123/stream/with-timestamps"  # read-along, Oct 8
    assert req.url.params["output_format"] == "mp3_44100_128"
    assert req.headers["xi-api-key"] == "el-test"


def test_open_stream_error_status_is_voice_error(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    _fake_async_client(monkeypatch, lambda r: httpx.Response(401, text="quota_exceeded"))
    with pytest.raises(speech.VoiceError, match="401") as err:
        asyncio.run(speech.open_stream("Narration.", "voice123"))
    assert "quota_exceeded" in str(err.value)
    assert "el-test" not in str(err.value)


def test_open_stream_transport_error_is_voice_error(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")

    def boom(request):
        raise httpx.ConnectTimeout("slow")

    _fake_async_client(monkeypatch, boom)
    with pytest.raises(speech.VoiceError, match="ConnectTimeout"):
        asyncio.run(speech.open_stream("Narration.", "voice123"))


# ---------------------------------------------------------------- voice list

def _voices_client(pages: list[dict], status: int = 200, calls: list | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(request)
        if status >= 400:
            return httpx.Response(status, json={})
        token = request.url.params.get("next_page_token")
        idx = int(token) if token else 0
        return httpx.Response(200, json=pages[idx])

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_voices_needs_key():
    with pytest.raises(speech.VoiceError, match="ELEVENLABS_API_KEY"):
        speech.list_voices()


def test_list_voices_follows_pages_and_normalises(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    pages = [
        {"voices": [{"voice_id": "a", "name": "Ben", "category": "cloned", "preview_url": "p", "extra": 1}],
         "has_more": True, "next_page_token": "1"},
        {"voices": [{"voice_id": "b", "name": "Stock", "category": "premade", "is_owner": 1}],
         "has_more": False, "next_page_token": None},
    ]
    calls: list = []
    with _voices_client(pages, calls=calls) as c:
        voices = speech.list_voices(client=c)
    assert [v["voice_id"] for v in voices] == ["a", "b"]
    assert voices[0] == {"voice_id": "a", "name": "Ben", "category": "cloned", "preview_url": "p", "is_owner": False}
    assert voices[1]["is_owner"] is True
    assert calls[0].url.params["page_size"] == "100" and "next_page_token" not in calls[0].url.params
    assert calls[1].url.params["next_page_token"] == "1"
    assert calls[0].headers["xi-api-key"] == "el-test"


def test_list_voices_stops_after_five_pages(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    calls: list = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"voices": [{"voice_id": str(len(calls))}], "has_more": True,
                                         "next_page_token": "more"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        voices = speech.list_voices(client=c)
    assert len(calls) == 5 and len(voices) == 5


def test_list_voices_errors(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    with _voices_client([], status=500) as c, pytest.raises(speech.VoiceError, match="500"):
        speech.list_voices(client=c)

    def boom(request):
        raise httpx.ReadTimeout("slow")

    with httpx.Client(transport=httpx.MockTransport(boom)) as c, pytest.raises(speech.VoiceError, match="ReadTimeout"):
        speech.list_voices(client=c)


# ---------------------------------------------------------------- find_voice cache

def test_find_voice_without_key_is_none():
    assert speech.find_voice("a") is None


def test_find_voice_caches_and_backs_off_after_failure(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    calls = []

    def ok(timeout=4.0):
        calls.append("ok")
        return [{"voice_id": "a", "category": "cloned"}]

    monkeypatch.setattr(speech, "list_voices", ok)
    assert speech.find_voice("a") == {"voice_id": "a", "category": "cloned"}
    assert speech.find_voice("missing") is None
    assert calls == ["ok"]  # second lookup used the cache

    speech.clear_cache()

    def fail(timeout=4.0):
        calls.append("fail")
        raise speech.VoiceError("down")

    monkeypatch.setattr(speech, "list_voices", fail)
    assert speech.find_voice("a") is None
    assert speech.find_voice("a") is None
    assert calls == ["ok", "fail"]  # failure is remembered; no second call within a minute


def test_find_voice_keeps_stale_list_when_refresh_fails(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setattr(speech, "list_voices", lambda timeout=4.0: [{"voice_id": "a", "category": "premade"}])
    assert speech.find_voice("a")["category"] == "premade"
    speech._cache["at"] = -10 * speech.CACHE_SECONDS  # stale

    def fail(timeout=4.0):
        raise speech.VoiceError("down")

    monkeypatch.setattr(speech, "list_voices", fail)
    assert speech.find_voice("a")["category"] == "premade"


@pytest.mark.parametrize("meta,expected", [
    ({"category": "cloned"}, True),
    ({"category": "professional", "is_owner": True}, True),
    ({"category": "professional", "is_owner": False}, False),
    ({"category": "premade"}, False),
    ({}, False),
])
def test_is_clone(meta, expected):
    assert speech.is_clone(meta) is expected
