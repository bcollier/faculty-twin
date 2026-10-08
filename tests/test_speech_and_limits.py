from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import HTTPException

from app import limits, settings_store, speech


def _parts(link: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}


def test_sign_and_verify():
    link = speech.audio_link("Hello from the slide.", "voice123")
    q = _parts(link)
    assert speech.verify(q["t"], q["v"], q["s"]) == "Hello from the slide."
    # changed text, changed voice tag, or a bad signature all fail
    other = _parts(speech.audio_link("Something else.", "voice123"))
    assert speech.verify(other["t"], q["v"], q["s"]) is None
    assert speech.verify(q["t"], speech.voice_tag("voice999"), q["s"]) is None
    assert speech.verify(q["t"], q["v"], "AAAA") is None
    assert speech.audio_link("text", None) is None


def test_rate_limit_per_minute_and_day():
    for _ in range(5):
        limits.check_ask_rate("visitorA", now=1_000_000)
    with pytest.raises(HTTPException) as exc:
        limits.check_ask_rate("visitorA", now=1_000_000)
    assert exc.value.status_code == 429 and "minute" in exc.value.detail
    limits.check_ask_rate("visitorB", now=1_000_000)  # other visitors unaffected

    limits.reset_memory()
    t = 2_000_000 - 2_000_000 % 86400 + 60  # start of a day
    for i in range(30):
        limits.check_ask_rate("visitorC", now=t + i * 61)
    with pytest.raises(HTTPException) as exc:
        limits.check_ask_rate("visitorC", now=t + 31 * 61)
    assert "today" in exc.value.detail


def test_voice_cap():
    assert limits.take_voice_chars(60, cap=100)
    assert not limits.take_voice_chars(60, cap=100)
    assert limits.take_voice_chars(40, cap=100)
    assert not limits.take_voice_chars(1, cap=0)


def test_question_log_has_no_identity():
    limits.log_question("what is k-means", 0.71234, True, "anthropic", "claude-sonnet-5-5", 900, "70445")
    row = limits.recent_questions(1)[0]
    assert set(row) == {"question", "top_score", "covered", "provider", "model", "latency_ms", "course", "kind", "at"}


class FakeStream:
    def __init__(self):
        self.closed = False

    async def aiter_bytes(self):
        yield b"ID3fake-mp3"

    async def aclose(self):
        self.closed = True


class FakeAsyncClient:
    async def aclose(self):
        pass


def _install_fake_voice(monkeypatch, calls):
    async def fake_open(text, voice_id):
        calls.append((text, voice_id))
        return FakeAsyncClient(), FakeStream()

    monkeypatch.setattr(speech, "open_stream", fake_open)


def test_audio_route(student, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    calls: list = []
    _install_fake_voice(monkeypatch, calls)
    link = speech.audio_link("Signed narration.", "voice123")
    r = student.get(link)
    assert r.status_code == 200 and r.content == b"ID3fake-mp3"
    assert r.headers["content-type"] == "audio/mpeg"
    assert calls == [("Signed narration.", "voice123")]

    q = _parts(link)
    forged = speech._b64e("Say something else.".encode())
    assert student.get(f"/api/audio?t={forged}&v={q['v']}&s={q['s']}").status_code == 403


def test_audio_route_cap_and_voice_change(student, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    _install_fake_voice(monkeypatch, [])
    link = speech.audio_link("Twenty characters!!!", "voice123")
    settings_store.put({"daily_voice_char_cap": 80})  # per-visitor share is 25% = 20 chars
    assert student.get(link).status_code == 200
    r = student.get(link)
    assert r.status_code == 429 and "limit" in r.json()["detail"]

    settings_store.put({"daily_voice_char_cap": 1000, "voice_id": "voice456"})
    assert student.get(link).status_code == 403  # old voice tag
    settings_store.put({"voice_id": "none"})
    assert student.get(link).status_code == 404


def test_audio_needs_cookie(client):
    link = speech.audio_link("Signed narration.", "voice123")
    assert client.get(link).status_code == 401


def test_tts_request_shape(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    url, headers, params, body = speech.tts_request("Hi", "abc")
    assert url == "https://api.elevenlabs.io/v1/text-to-speech/abc/stream"
    assert headers["xi-api-key"] == "el-test"
    assert params == {"output_format": "mp3_44100_128"}
    assert body == {"text": "Hi", "model_id": "eleven_multilingual_v2"}


# ---------------------------------------------------------------- cap math (Oct 8 code review)

def test_a_question_refused_by_the_network_limit_does_not_use_the_visitors_quota(monkeypatch):
    # Each bucket was charged before the next was checked, so a question refused by the per-address
    # limit (a classroom behind one NAT) still used up the student's own minute and day quota.
    monkeypatch.setattr(limits.config, "PER_ADDRESS_MINUTE_LIMIT", 1)
    limits.check_ask_rate("visitor-a", "room-1")
    with pytest.raises(HTTPException) as exc:
        limits.check_ask_rate("visitor-b", "room-1")
    assert exc.value.status_code == 429
    import time

    minute = time.strftime("%Y%m%d%H%M", time.gmtime())
    day = time.strftime("%Y%m%d", time.gmtime())
    assert limits.read_counter(f"rl:min:visitor-b:{minute}") == 0
    assert limits.read_counter(f"rl:day:visitor-b:{day}") == 0


def test_voice_characters_refused_by_the_daily_cap_do_not_use_the_visitors_share():
    cap = 1000  # share is 250 per visitor
    assert limits.take_voice_chars(100, cap, "v1", "a1")
    limits.increment(limits.voice_key("voice"), 850)  # everyone else used most of today's cap
    assert not limits.take_voice_chars(100, cap, "v1", "a1")  # within the share, over the daily cap
    day = limits._today()
    assert limits.read_counter(f"voice_share:v:v1:{day}") == 100
    assert limits.read_counter(f"voice_share:a:a1:{day}") == 100


def test_characters_are_given_back_when_the_voice_service_fails(student, monkeypatch):
    # An ElevenLabs outage used to burn today's paid cap: every failed play was charged.
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")

    async def down(text, voice_id):
        raise speech.VoiceError("voice service returned 503")

    monkeypatch.setattr(speech, "open_stream", down)
    link = speech.audio_link("Twenty characters!!!", "voice123")
    assert student.get(link).status_code == 502
    assert limits.read_counter(limits.voice_key("voice")) == 0
