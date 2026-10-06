"""Voice tiers: my clone, ElevenLabs stock voices, free Microsoft voices (edge-tts), captions only.

Neither ElevenLabs nor Microsoft is called: edge-tts's Communicate class and
the ElevenLabs list/stream functions are replaced with fakes.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import pytest

from app import edge_voice, limits, settings_store, speech, voices
from test_api import use_fakes

CLONE_LABEL = "AI voice made from my recordings."
STOCK_LABEL = "AI voice (a stock voice, not mine)."
ANDREW = "en-US-AndrewMultilingualNeural"
SONIA = "en-GB-SoniaNeural"

ACCOUNT = [
    {"voice_id": "benclone", "name": "Ben reading", "category": "cloned", "preview_url": "https://x/1.mp3", "is_owner": True},
    {"voice_id": "benpro", "name": "Ben pro", "category": "professional", "preview_url": None, "is_owner": True},
    {"voice_id": "libvoice", "name": "Library voice", "category": "professional", "preview_url": None, "is_owner": False},
    {"voice_id": "roger", "name": "Roger", "category": "premade", "preview_url": "https://x/2.mp3", "is_owner": False},
    {"voice_id": "designed", "name": "Designed", "category": "generated", "preview_url": None, "is_owner": True},
]


def _q(link: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}


# ---------------------------------------------------------------- fakes

class FakeCommunicate:
    """Stands in for edge_tts.Communicate: yields two audio chunks that spell out the text."""

    calls: list[tuple[str, str, dict]] = []
    failures = 0

    def __init__(self, text, voice, **kwargs):
        FakeCommunicate.calls.append((text, voice, kwargs))
        self.text = text

    async def stream(self):
        if FakeCommunicate.failures > 0:
            FakeCommunicate.failures -= 1
            raise RuntimeError("service dropped the connection")
        yield {"type": "audio", "data": b"<"}
        yield {"type": "SentenceBoundary", "offset": 0, "duration": 1, "text": self.text}
        yield {"type": "audio", "data": self.text.encode() + b">"}


@pytest.fixture
def fake_edge(monkeypatch):
    FakeCommunicate.calls = []
    FakeCommunicate.failures = 0
    monkeypatch.setattr(edge_voice.edge_tts, "Communicate", FakeCommunicate)

    async def no_wait(attempt):
        return None

    monkeypatch.setattr(edge_voice, "_backoff", no_wait)
    return FakeCommunicate


@pytest.fixture
def fake_eleven(monkeypatch):
    """ElevenLabs with a key, a fake account list, and a fake stream that records calls."""
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-key")
    calls = {"list": 0, "stream": []}

    def fake_list(client=None, timeout=15.0):
        calls["list"] += 1
        return [dict(v) for v in ACCOUNT]

    class FakeResp:
        async def aiter_bytes(self):
            yield b"ELEVEN-MP3"

        async def aclose(self):
            pass

    class FakeClient:
        async def aclose(self):
            pass

    async def fake_open(text, voice_id):
        calls["stream"].append((text, voice_id))
        return FakeClient(), FakeResp()

    monkeypatch.setattr(speech, "list_voices", fake_list)
    monkeypatch.setattr(speech, "open_stream", fake_open)
    return calls


def _ask(student) -> dict:
    use_fakes()
    r = student.post("/api/ask", json={"question": "what is an apple", "course": "70445"})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- parsing and backward compatibility

def test_parse_voice_settings():
    assert voices.parse(None) is None and voices.parse("") is None
    assert voices.parse("none") == ("none", "")
    assert voices.parse(f"edge:{ANDREW}") == ("edge", ANDREW)
    assert voices.parse("eleven:abc123") == ("elevenlabs", "abc123")
    assert voices.parse("abc123") == ("elevenlabs", "abc123")  # older bare ElevenLabs id
    for bad in ("edge:not a voice", "edge:", "eleven:bad id!", "bad id!", "edge:en-US-Andrew"):
        with pytest.raises(voices.BadVoice):
            voices.parse(bad)


def test_bare_and_prefixed_elevenlabs_ids_share_one_tag():
    """Old links and audio/<id>/ folders made for a bare id keep working after saving eleven:<id>."""
    bare = voices.Voice("elevenlabs", "voice123")
    assert bare.tag == speech.voice_tag("voice123")
    assert voices.Voice("edge", ANDREW, "free").tag != speech.voice_tag(ANDREW)  # free tags are prefixed
    assert bare.audio_prefixes() == (f"audio/{bare.tag}/", "audio/voice123/")


def test_null_setting_uses_env_default_and_bare_setting_still_routes(student, monkeypatch, fake_eleven):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "benclone")
    plan = voices.current()
    assert plan.primary == voices.Voice("elevenlabs", "benclone", "clone")
    settings_store.put({"voice_id": "roger"})  # an older bare id saved before this change
    seg = _ask(student)["segments"][0]
    assert _q(seg["audio"])["v"] == speech.voice_tag("roger")
    assert student.get(seg["audio"]).content == b"ELEVEN-MP3"
    assert fake_eleven["stream"][-1][1] == "roger"
    assert seg["voice"] == {"kind": "stock", "label": STOCK_LABEL}


def test_stored_topic_audio_under_the_voice_tag(student, monkeypatch, content_dir):
    use_fakes()
    tag = speech.voice_tag("voice777")
    (content_dir / "audio" / tag).mkdir(parents=True)
    (content_dir / "audio" / tag / "fakehash.mp3").write_bytes(b"ID3fake")
    import json

    topics_path = content_dir / "topics" / "topics.json"
    topics = json.loads(topics_path.read_text())
    for t in topics:
        for s in (t.get("playlist") or {}).get("segments", []):
            s["audio_path"] = f"audio/{tag}/fakehash.mp3"
    topics_path.write_text(json.dumps(topics))
    from app import storage

    storage.store.reset()
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice777")
    seg = student.post("/api/ask", json={"question": "Show me the banana slide"}).json()["segments"][0]
    assert seg["audio"].startswith(f"/api/files/audio/{tag}/fakehash.mp3?exp=")
    settings_store.put({"voice_id": f"edge:{ANDREW}"})  # stored mp3 was made with another voice
    seg = student.post("/api/ask", json={"question": "Show me the banana slide"}).json()["segments"][0]
    assert seg["audio"].startswith("/api/audio?t=") and seg["voice"]["kind"] == "free"


# ---------------------------------------------------------------- routing by tier

def test_free_voice_routes_to_edge_tts(student, fake_edge, fake_eleven):
    settings_store.put({"voice_id": f"edge:{SONIA}"})
    seg = _ask(student)["segments"][0]
    q = _q(seg["audio"])
    assert q["v"] == speech.voice_tag(f"edge:{SONIA}")
    assert seg["voice"] == {"kind": "free", "label": STOCK_LABEL}
    r = student.get(seg["audio"])
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content == b"<" + seg["narration"].encode() + b">"
    text, voice, kwargs = fake_edge.calls[-1]
    assert (text, voice) == (seg["narration"], SONIA)
    assert kwargs["rate"] == "-5%" and kwargs["pitch"] == "+0Hz"
    assert kwargs["connect_timeout"] and kwargs["receive_timeout"]
    assert fake_eleven["stream"] == []  # ElevenLabs never called for a free voice


def test_elevenlabs_voice_routes_to_elevenlabs(student, fake_edge, fake_eleven):
    settings_store.put({"voice_id": "eleven:benclone"})
    seg = _ask(student)["segments"][0]
    assert student.get(seg["audio"]).content == b"ELEVEN-MP3"
    assert fake_eleven["stream"] == [(seg["narration"], "benclone")]
    assert fake_edge.calls == []


def test_long_narration_is_split_and_joined_in_order(student, fake_edge):
    settings_store.put({"voice_id": f"edge:{ANDREW}"})
    text = " ".join(f"Sentence number {i} explains one more idea on the slide." for i in range(15))
    assert 400 < len(text) < speech.MAX_TEXT_CHARS
    pieces = edge_voice.chunk_text(text)
    assert len(pieces) >= 2 and all(len(p) <= 400 for p in pieces) and " ".join(pieces) == text
    link = speech.audio_link(text, f"edge:{ANDREW}")
    r = student.get(link)
    assert r.status_code == 200
    assert r.content == b"".join(b"<" + p.encode() + b">" for p in pieces)


def test_free_voice_retries_then_502(student, fake_edge):
    settings_store.put({"voice_id": f"edge:{ANDREW}"})
    link = speech.audio_link("A short signed sentence.", f"edge:{ANDREW}")
    fake_edge.failures = 1  # one dropped connection: retried
    assert student.get(link).status_code == 200
    fake_edge.failures = 10  # the service is down: 502 before any audio, so the page shows captions
    r = student.get(link)
    assert r.status_code == 502 and "Captions only" in r.json()["detail"]


# ---------------------------------------------------------------- the signature covers the voice

def test_signature_covers_text_and_voice(student, fake_edge):
    settings_store.put({"voice_id": f"edge:{ANDREW}"})
    link = speech.audio_link("Signed narration.", f"edge:{ANDREW}")
    q = _q(link)
    other = speech.voice_tag(f"edge:{SONIA}")
    assert student.get(f"/api/audio?t={q['t']}&v={other}&s={q['s']}").status_code == 403  # retagged
    forged = speech._b64e(b"Say something else.")
    assert student.get(f"/api/audio?t={forged}&v={q['v']}&s={q['s']}").status_code == 403
    assert student.get(link).status_code == 200
    settings_store.put({"voice_id": f"edge:{SONIA}"})
    r = student.get(link)  # validly signed, but for a voice that is no longer chosen
    assert r.status_code == 403 and "older voice" in r.json()["detail"]
    settings_store.put({"voice_id": "none"})
    assert student.get(link).status_code == 404


def test_audio_needs_student_cookie_for_free_voice(client, fake_edge):
    settings_store.put({"voice_id": f"edge:{ANDREW}"})
    assert client.get(speech.audio_link("Signed narration.", f"edge:{ANDREW}")).status_code == 401


# ---------------------------------------------------------------- caps per tier

def test_free_voice_has_its_own_cap_and_visitor_share(student, fake_edge):
    settings_store.put({"voice_id": f"edge:{ANDREW}", "daily_voice_char_cap": 0, "daily_free_voice_char_cap": 80})
    link = speech.audio_link("Twenty characters!!!", f"edge:{ANDREW}")
    assert student.get(link).status_code == 200  # the ElevenLabs cap (0) does not apply to the free voice
    r = student.get(link)  # 25% of 80 = 20 characters per visitor
    assert r.status_code == 429
    counters = limits.today_counters()
    assert counters["free_voice_chars"] == 20 and counters["voice_chars"] == 0


def test_free_voice_cap_default_and_env(monkeypatch):
    assert settings_store.daily_free_voice_char_cap() == 200000
    monkeypatch.setenv("DAILY_FREE_VOICE_CHAR_CAP", "1234")
    assert settings_store.daily_free_voice_char_cap() == 1234


def test_elevenlabs_cap_does_not_touch_free_pool(student, fake_eleven):
    settings_store.put({"voice_id": "eleven:roger", "daily_voice_char_cap": 80})
    link = speech.audio_link("Twenty characters!!!", "roger")
    assert student.get(link).status_code == 200
    assert student.get(link).status_code == 429
    counters = limits.today_counters()
    assert counters["voice_chars"] == 20 and counters["free_voice_chars"] == 0


def test_pools_are_separate_counters():
    assert limits.take_voice_chars(60, cap=100, pool="voice")
    assert not limits.take_voice_chars(60, cap=100, pool="voice")
    assert limits.take_voice_chars(60, cap=100, pool="free")
    assert limits.voice_key("voice").startswith("voice_chars:") and limits.voice_key("free").startswith("free_voice_chars:")


# ---------------------------------------------------------------- fallback to a free voice

def test_fallback_captions_is_the_default(student, fake_eleven):
    settings_store.put({"voice_id": "eleven:benclone"})
    seg = _ask(student)["segments"][0]
    assert seg["audio_fallback"] is None and seg["voice_fallback"] is None


def test_fallback_free_adds_a_second_signed_link_with_its_own_label(student, fake_eleven, fake_edge):
    settings_store.put({"voice_id": "eleven:benclone", "voice_fallback": "free", "voice_fallback_voice": f"edge:{SONIA}"})
    seg = _ask(student)["segments"][0]
    assert seg["voice"] == {"kind": "clone", "label": CLONE_LABEL}
    assert seg["voice_fallback"] == {"kind": "free", "label": STOCK_LABEL}
    q = _q(seg["audio_fallback"])
    assert q["v"] == speech.voice_tag(f"edge:{SONIA}") and speech.verify(q["t"], q["v"], q["s"]) == seg["narration"]
    r = student.get(seg["audio_fallback"])
    assert r.status_code == 200 and fake_edge.calls[-1][1] == SONIA
    assert fake_eleven["stream"] == []
    # turning the fallback off makes the fallback link stop working
    settings_store.put({"voice_fallback": "captions"})
    assert student.get(seg["audio_fallback"]).status_code == 403


def test_fallback_takes_over_when_elevenlabs_cap_is_spent(student, fake_eleven, fake_edge):
    settings_store.put({"voice_id": "eleven:benclone", "voice_fallback": "free", "daily_voice_char_cap": 100})
    assert limits.take_voice_chars(100, cap=100, pool="voice")  # today's ElevenLabs budget is used up
    seg = _ask(student)["segments"][0]
    assert _q(seg["audio"])["v"] == speech.voice_tag(f"edge:{ANDREW}")  # the default free fallback voice
    assert seg["voice"] == {"kind": "free", "label": STOCK_LABEL}  # never the clone label for the free voice
    assert seg["audio_fallback"] is None
    assert student.get("/api/voice").json() == {"kind": "free", "label": STOCK_LABEL, "fallback": None}
    assert student.get(seg["audio"]).status_code == 200


def test_capped_elevenlabs_without_fallback_still_429s(student, fake_eleven):
    settings_store.put({"voice_id": "eleven:benclone", "daily_voice_char_cap": 0})
    seg = _ask(student)["segments"][0]
    assert seg["voice"]["kind"] == "clone"  # no fallback: the clone link is issued and refused at play time
    assert student.get(seg["audio"]).status_code == 429


# ---------------------------------------------------------------- honest labels

@pytest.mark.parametrize(
    "setting, kind, label",
    [
        ("eleven:benclone", "clone", CLONE_LABEL),
        ("eleven:benpro", "clone", CLONE_LABEL),  # a professional clone this account owns
        ("eleven:libvoice", "stock", STOCK_LABEL),  # a Voice Library voice: someone else's
        ("eleven:roger", "stock", STOCK_LABEL),
        ("eleven:designed", "stock", STOCK_LABEL),
        (f"edge:{ANDREW}", "free", STOCK_LABEL),
    ],
)
def test_label_matches_the_voice(student, admin, fake_eleven, setting, kind, label):
    r = admin.put("/api/admin/settings", json={"voice_id": setting})
    assert r.status_code == 200, r.text
    assert r.json()["voice_kind"] == kind and r.json()["voice_label"] == label
    assert student.get("/api/voice").json() == {"kind": kind, "label": label, "fallback": None}
    seg = _ask(student)["segments"][0]
    assert seg["voice"] == {"kind": kind, "label": label}


def test_captions_only_has_no_voice_label(student, admin):
    admin.put("/api/admin/settings", json={"voice_id": "none"})
    assert student.get("/api/voice").json() == {"kind": "none", "label": None, "fallback": None}
    seg = _ask(student)["segments"][0]
    assert seg["audio"] is None and seg["voice"] is None and seg["audio_fallback"] is None


def test_saved_kind_for_another_voice_is_never_reused(student, fake_eleven, monkeypatch):
    """A clone label saved for one voice must not leak onto a different voice."""
    settings_store.put({"voice_id": "eleven:roger", "voice_kind": {"voice_id": "benclone", "kind": "clone"}})
    assert student.get("/api/voice").json()["label"] == STOCK_LABEL  # looked up: roger is premade


def test_unverifiable_voice_gets_neutral_label_not_clone(student, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test-key")

    def down(client=None, timeout=15.0):
        raise speech.VoiceError("voice list failed: ConnectError")

    monkeypatch.setattr(speech, "list_voices", down)
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "benclone")
    assert student.get("/api/voice").json() == {"kind": "unverified", "label": "AI voice.", "fallback": None}


def test_label_lookup_is_cached(student, fake_eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "benclone")
    for _ in range(3):
        assert student.get("/api/voice").json()["kind"] == "clone"
    assert fake_eleven["list"] == 1


def test_voice_route_needs_cookie(client):
    assert client.get("/api/voice").status_code == 401


# ---------------------------------------------------------------- Settings: saving voices

def test_saving_voices_validates_and_records_kind(admin, fake_eleven, monkeypatch):
    assert admin.put("/api/admin/settings", json={"voice_id": "eleven:notmine"}).status_code == 400
    s = admin.put("/api/admin/settings", json={"voice_id": "eleven:benclone"}).json()
    assert s["voice_id"] == "eleven:benclone" and s["voice_costs_money"] is True
    assert settings_store.get("voice_kind") == {"voice_id": "benclone", "kind": "clone"}
    s = admin.put("/api/admin/settings", json={"voice_id": f"edge:{ANDREW}"}).json()
    assert s["voice_id"] == f"edge:{ANDREW}" and s["voice_costs_money"] is False and s["voice_kind"] == "free"
    assert settings_store.get("voice_kind") is None
    assert admin.put("/api/admin/settings", json={"voice_id": "edge:nonsense"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"voice_fallback": "loud"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"voice_fallback_voice": "eleven:roger"}).status_code == 400
    assert admin.put("/api/admin/settings", json={"daily_free_voice_char_cap": -5}).status_code == 400
    s = admin.put("/api/admin/settings", json={"voice_fallback": "free", "voice_fallback_voice": f"edge:{SONIA}",
                                               "daily_free_voice_char_cap": 50000}).json()
    assert s["voice_fallback"] == "free" and s["voice_fallback_voice"] == f"edge:{SONIA}"
    assert s["daily_free_voice_char_cap"] == 50000


def test_free_text_short_name_checked_against_microsofts_list(admin, monkeypatch):
    calls = {"n": 0}

    async def fake_list():
        calls["n"] += 1
        return [{"ShortName": "en-AU-NatashaNeural"}, {"ShortName": ANDREW}]

    monkeypatch.setattr(edge_voice.edge_tts, "list_voices", fake_list)
    s = admin.put("/api/admin/settings", json={"voice_id": "edge:en-AU-NatashaNeural"})
    assert s.status_code == 200 and s.json()["voice_id"] == "edge:en-AU-NatashaNeural"
    r = admin.put("/api/admin/settings", json={"voice_id": "edge:en-AU-WilliamNeural"})
    assert r.status_code == 400 and "no voice named" in r.json()["detail"]
    assert calls["n"] == 1  # the list is cached
    assert admin.put("/api/admin/settings", json={"voice_id": f"edge:{SONIA}"}).status_code == 200  # curated: no lookup
    assert calls["n"] == 1


def test_free_text_short_name_when_microsoft_is_unreachable(admin, monkeypatch):
    async def down():
        raise OSError("no route")

    monkeypatch.setattr(edge_voice.edge_tts, "list_voices", down)
    r = admin.put("/api/admin/settings", json={"voice_id": "edge:en-AU-NatashaNeural"})
    assert r.status_code == 502


def test_admin_voice_groups(admin, fake_eleven, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "benclone")
    body = admin.get("/api/admin/voices").json()
    assert body["elevenlabs_error"] is None
    groups = {g["id"]: g for g in body["groups"]}
    assert [g["id"] for g in body["groups"]] == ["clone", "elevenlabs", "free"]
    assert groups["clone"]["label"] == "My voice clone" and groups["clone"]["costs_money"] is True
    assert [v["voice_id"] for v in groups["clone"]["voices"]] == ["eleven:benclone", "eleven:benpro"]
    assert groups["clone"]["voices"][0]["is_default"] is True
    assert {v["voice_id"] for v in groups["elevenlabs"]["voices"]} == {"eleven:libvoice", "eleven:roger", "eleven:designed"}
    assert groups["free"]["costs_money"] is False and groups["free"]["label"] == "Free Microsoft voices"
    free_ids = [v["voice_id"] for v in groups["free"]["voices"]]
    assert f"edge:{ANDREW}" in free_ids and len(free_ids) == 8
    assert all(v["preview_url"] == f"/api/admin/voice-preview?voice={v['voice_id']}" for v in groups["free"]["voices"])
    assert groups["clone"]["student_label"] == CLONE_LABEL and groups["elevenlabs"]["student_label"] == STOCK_LABEL


# ---------------------------------------------------------------- previews

def test_preview_is_admin_only(client, student, fake_edge):
    url = f"/api/admin/voice-preview?voice=edge:{ANDREW}"
    assert client.get(url).status_code == 401
    assert student.get(url).status_code == 401
    assert fake_edge.calls == []


def test_preview_speaks_fixed_text_and_is_cached(admin, fake_edge):
    r = admin.get(f"/api/admin/voice-preview?voice=edge:{ANDREW}&text=Say+anything+I+want")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    spoken = [c[0] for c in fake_edge.calls]
    assert " ".join(spoken) == edge_voice.PREVIEW_TEXT and "anything" not in r.content.decode()
    n = len(fake_edge.calls)
    assert admin.get(f"/api/admin/voice-preview?voice=edge:{ANDREW}").content == r.content
    assert len(fake_edge.calls) == n  # served from memory
    assert limits.today_counters()["free_voice_chars"] == len(edge_voice.PREVIEW_TEXT)


def test_preview_rejects_elevenlabs_and_unknown_voices(admin, fake_edge, monkeypatch):
    async def fake_list():
        return [{"ShortName": ANDREW}]

    monkeypatch.setattr(edge_voice.edge_tts, "list_voices", fake_list)
    assert admin.get("/api/admin/voice-preview?voice=eleven:roger").status_code == 400
    assert admin.get("/api/admin/voice-preview?voice=roger").status_code == 400
    assert admin.get("/api/admin/voice-preview?voice=edge:en-AU-WilliamNeural").status_code == 400
    assert admin.get("/api/admin/voice-preview?voice=edge:<script>").status_code == 400
    assert fake_edge.calls == []


# ---------------------------------------------------------------- edge module units

def test_chunk_text_handles_one_enormous_sentence():
    text = "word " * 300
    pieces = edge_voice.chunk_text(text.strip())
    assert all(0 < len(p) <= 400 for p in pieces) and " ".join(pieces) == text.strip()


def test_rate_and_pitch_env_are_validated(monkeypatch):
    monkeypatch.setenv("EDGE_TTS_RATE", "+10%")
    monkeypatch.setenv("EDGE_TTS_PITCH", "-2Hz")
    assert (edge_voice.rate(), edge_voice.pitch()) == ("+10%", "-2Hz")
    monkeypatch.setenv("EDGE_TTS_RATE", "fast; <prosody>")
    assert edge_voice.rate() == "-5%"


def test_open_stream_raises_before_any_byte_when_service_is_down(fake_edge):
    fake_edge.failures = 99

    async def go():
        with pytest.raises(edge_voice.FreeVoiceError):
            await edge_voice.open_stream("Hello there. Second sentence.", ANDREW)

    asyncio.run(go())
