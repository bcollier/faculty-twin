"""Read-along word timings (app/timings.py, the /api/audio/timings route, and the playlist links).

No voice service is called: edge-tts's Communicate class and the ElevenLabs
transport are fakes, and the bucket is a dict.
"""

from __future__ import annotations

import asyncio
import base64
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from test_api import use_fakes

from app import config, edge_voice, limits, playlist, settings_store, speech, storage, timings

ANDREW = "en-US-AndrewMultilingualNeural"
TEXT = "On this slide, k-means groups similar points. Then each center moves."


def _q(link: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(link).query).items()}


def _chars(text: str, start: float = 0.0, step: float = 0.05) -> dict:
    return {
        "characters": list(text),
        "character_start_times_seconds": [round(start + i * step, 3) for i in range(len(text))],
        "character_end_times_seconds": [round(start + (i + 1) * step, 3) for i in range(len(text))],
    }


# ---------------------------------------------------------------- conversion

def test_align_finds_words_in_order_and_skips_strays():
    spoken = [(0.1, "On"), (0.3, "this"), (0.5, "slide"), (0.9, "zebra"), (1.0, "k-means"), (1.4, "groups")]
    words = timings.align(TEXT, spoken)
    assert [i for _, i in words] == [0, 3, 8, TEXT.index("k-means"), TEXT.index("groups")]
    assert words[0] == [0.1, 0]


def test_align_strips_punctuation_and_never_goes_back_in_time():
    words = timings.align("Hello, world.", [(0.5, "Hello,"), (0.4, "world.")])
    assert words == [[0.5, 0], [0.5, 7]]
    # "slide," with the comma reported but the text has "slide;" -> found without the punctuation
    assert timings.align("a slide; b", [(0.2, "slide,")]) == [[0.2, 2]]


def test_align_does_not_jump_far_ahead():
    text = "start " + "x " * 60 + "target"
    assert timings.align(text, [(0.0, "start"), (1.0, "target")]) == [[0.0, 0]]


def test_words_from_characters_joins_letters_into_words():
    a = _chars("Hi there")
    assert timings.words_from_characters(a["characters"], a["character_start_times_seconds"]) == [
        (0.0, "Hi"), (0.15, "there")]


def test_collector_absolute_chunks_and_a_word_split_across_chunks():
    c = timings.Collector("elevenlabs")
    first, second = "On this sl", "ide, k-means groups similar points."
    c.add_alignment(_chars(first, 0.0), 0.0)
    c.add_alignment(_chars(second, len(first) * 0.05), 0.4)  # absolute times continue
    words = c.result("On this slide, k-means groups similar points.")
    assert [i for _, i in words][:3] == [0, 3, 8]
    assert words[2][0] == pytest.approx(0.4)  # "slide" starts where its first letter did, in chunk 1
    assert c.source == "elevenlabs" and not c.failed


def test_collector_relative_chunks_are_offset_by_the_audio_already_sent():
    c = timings.Collector("elevenlabs")
    c.add_alignment(_chars("One two three. ", 0.0), 0.0)
    c.add_alignment(_chars("Four five.", 0.0), 2.0)  # starts at zero again: relative
    words = c.result("One two three. Four five.")
    four = next(t for t, i in words if i == 15)
    assert four == pytest.approx(2.0)


def test_collector_ignores_missing_or_empty_alignment():
    c = timings.Collector("elevenlabs")
    c.add_alignment(None, 0.0)
    c.add_alignment({"characters": [], "character_start_times_seconds": []}, 0.0)
    assert c.result("anything") == []


def test_words_from_forced_alignment():
    reply = {"words": [{"text": "On", "start": 0.1, "end": 0.2, "loss": 0.1}, {"text": "", "start": 0.3},
                       {"text": "this", "start": "bad"}, {"text": "slide", "start": 0.5}]}
    assert timings.words_from_forced_alignment(reply) == [(0.1, "On"), (0.5, "slide")]


def test_estimate_weights_syllables_and_pauses():
    words = timings.estimate("Hi there, banana. Go", 10.0)
    assert [i for _, i in words] == [0, 3, 10, 18]
    starts = [t for t, _ in words]
    assert starts == sorted(starts) and starts[0] == 0.0 and starts[-1] < 10.0
    # "there," carries a comma pause, "banana." a sentence pause: the gaps after them are longer
    assert starts[2] - starts[1] > starts[1] - starts[0]
    assert timings.estimate("", 5.0) == [] and timings.estimate("words", 0) == []
    assert timings.syllables("the") == 1 and timings.syllables("banana") == 3 and timings.syllables("42") == 1
    assert timings.syllables("make") == 1 and timings.syllables("table") == 2


# ---------------------------------------------------------------- saving and loading

def test_save_and_load_in_memory():
    assert timings.load("tag1", "text") is None
    timings.save("tag1", "text", [], "edge")  # nothing to keep
    assert timings.load("tag1", "text") is None
    timings.save("tag1", "text", [[0.0, 0]], "edge")
    assert timings.load("tag1", "text") == {"words": [[0.0, 0]], "source": "edge"}
    assert timings.load("tag2", "text") is None  # another voice


def test_memory_is_bounded(monkeypatch):
    monkeypatch.setattr(timings, "MEMORY_MAX", 3)
    for k in range(5):
        timings.save("t", f"text {k}", [[0.0, 0]], "edge")
    assert timings.load("t", "text 0") is None and timings.load("t", "text 4") is not None


@pytest.fixture
def bucket(monkeypatch):
    """Supabase configured, with a dict standing in for the bucket."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-test")
    from app import supa

    objects: dict[str, bytes] = {}
    calls = {"upload": [], "fail": False}

    def upload(path, data, content_type, upsert=False, cache_control=None):
        if calls["fail"]:
            raise supa.SupabaseError("down")
        calls["upload"].append((path, content_type, upsert, cache_control))
        objects[path] = data

    def download_optional(path):
        if calls["fail"]:
            raise supa.SupabaseError("down")
        return objects.get(path)

    monkeypatch.setattr(supa, "upload", upload)
    monkeypatch.setattr(supa, "download_optional", download_optional)
    return objects, calls


def test_save_writes_the_bucket_and_load_reads_it_back(bucket):
    objects, calls = bucket
    assert config.content_dir() is None
    timings.save("abc", "Some text.", [[0.0, 0], [0.4, 5]], "elevenlabs")
    path = timings.bucket_path("abc", "Some text.")
    assert path.startswith("timings/abc/") and path.endswith(".json") and path in objects
    assert calls["upload"][0][1:] == ("application/json", True, "no-cache, max-age=0")
    assert not storage.is_media_path(path)  # never signed into a link
    timings.clear_memory()  # another instance: only the bucket has it
    assert timings.load("abc", "Some text.") == {"words": [[0.0, 0], [0.4, 5]], "source": "elevenlabs"}


def test_bucket_errors_and_bad_objects_are_harmless(bucket):
    objects, calls = bucket
    objects[timings.bucket_path("abc", "x")] = b"not json"
    assert timings.load("abc", "x") is None
    objects[timings.bucket_path("abc", "y")] = b'{"words": "nope"}'
    assert timings.load("abc", "y") is None
    calls["fail"] = True
    timings.save("abc", "z", [[0.0, 0]], "edge")  # does not raise
    timings.clear_memory()
    assert timings.load("abc", "z") is None


def test_bucket_path_is_safe_for_any_tag():
    assert timings.bucket_path("../../x", "t").startswith("timings/x/")
    assert timings.bucket_path("", "t").startswith("timings/none/")


# ---------------------------------------------------------------- ElevenLabs stream with timestamps

def _fake_async_client(monkeypatch, handler):
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(speech.httpx, "AsyncClient", factory)


def _ndjson(parts: list[tuple[bytes, dict | None]]) -> bytes:
    lines = [json.dumps({"audio_base64": base64.b64encode(a).decode(), "alignment": al}) for a, al in parts]
    return ("\n".join(lines) + "\n").encode()


def test_stream_with_timestamps_decodes_audio_and_collects_characters(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    text = "On this slide."
    body = _ndjson([(b"ID3aaaa", _chars("On this ")), (b"bbbb", _chars("slide.", 8 * 0.05))])
    body += b"not json\n" + json.dumps({"audio_base64": base64.b64encode(b"cc").decode()}).encode()  # no newline
    _fake_async_client(monkeypatch, lambda r: httpx.Response(200, content=body,
                                                             headers={"content-type": "application/json"}))

    async def go():
        client, resp = await speech.open_stream(text, "voice123")
        c = timings.Collector("elevenlabs")
        data = b"".join([x async for x in speech.stream_bytes(client, resp, c)])
        return data, c

    data, c = asyncio.run(go())
    assert data == b"ID3aaaabbbbcc"
    assert c.result(text) == [[0.0, 0], [0.15, 3], [0.4, 8]]


def test_stream_failure_marks_the_collector(monkeypatch):
    class Boom:
        async def aiter_bytes(self):
            yield b'{"audio_base64": ""}\n'
            raise httpx.ReadError("cut")

        async def aclose(self):
            pass

    class Client:
        async def aclose(self):
            pass

    async def go():
        c = timings.Collector("elevenlabs")
        with pytest.raises(httpx.ReadError):
            async for _ in speech.stream_bytes(Client(), Boom(), c):
                pass
        return c

    assert asyncio.run(go()).failed


def test_non_streaming_with_timestamps_reply():
    text = "Hi there"
    audio, words = speech.eleven_audio_and_words(
        {"audio_base64": base64.b64encode(b"ID3x").decode(), "alignment": _chars(text)}, text)
    assert audio == b"ID3x" and words == [[0.0, 0], [0.15, 3]]
    with pytest.raises(speech.VoiceError):
        speech.eleven_audio_and_words({"alignment": {}}, text)


def test_timings_link():
    assert speech.timings_link("/api/audio?t=a&v=b&s=c") == "/api/audio/timings?t=a&v=b&s=c"
    assert speech.timings_link(None) is None and speech.timings_link("https://x/a.mp3") is None


# ---------------------------------------------------------------- edge-tts word boundaries

class WordComm:
    """edge_tts.Communicate stand-in: 6,000 bytes (one second) of audio per word, with a WordBoundary each."""

    calls: list = []

    def __init__(self, text, voice, **kwargs):
        WordComm.calls.append(kwargs)
        self.text = text

    async def stream(self):
        for k, w in enumerate(self.text.split()):
            yield {"type": "WordBoundary", "offset": k * 10_000_000, "duration": 1, "text": w}
            yield {"type": "audio", "data": b"x" * 6000}


def test_edge_stream_offsets_each_piece_by_the_audio_before_it(monkeypatch):
    WordComm.calls = []
    monkeypatch.setattr(edge_voice.edge_tts, "Communicate", WordComm)
    text = " ".join(f"Sentence {i} has five words." for i in range(30))  # several 400-character pieces
    pieces = edge_voice.chunk_text(text)
    assert len(pieces) >= 2

    async def go():
        c = timings.Collector("edge")
        stream = await edge_voice.open_stream(text, ANDREW, c)
        data = b"".join([x async for x in stream])
        return data, c

    data, c = asyncio.run(go())
    assert all(kw.get("boundary") == "WordBoundary" for kw in WordComm.calls)
    words = c.result(text)
    n = len(text.split())
    assert len(data) == n * 6000 and len(words) == n
    assert [t for t, _ in words] == [float(k) for k in range(n)]  # one second per word, across pieces
    assert words[-1][1] == text.rindex("words.")


# ---------------------------------------------------------------- the route

def _edge_voice_on(monkeypatch):
    WordComm.calls = []
    monkeypatch.setattr(edge_voice.edge_tts, "Communicate", WordComm)
    settings_store.put({"voice_id": f"edge:{ANDREW}"})


def test_timings_route_needs_the_cookie_and_a_good_signature(client, student, monkeypatch):
    _edge_voice_on(monkeypatch)
    link = speech.audio_link("Signed narration here.", f"edge:{ANDREW}")
    tlink = speech.timings_link(link)
    q = _q(tlink)
    bad = f"/api/audio/timings?t={q['t']}&v={q['v']}&s=AAAA"
    assert student.get(bad).status_code == 403
    other = speech.audio_link("Other words.", f"edge:{ANDREW}")
    assert student.get(f"/api/audio/timings?t={q['t']}&v={q['v']}&s={_q(other)['s']}").status_code == 403
    student.cookies.clear()
    assert student.get(tlink).status_code == 401


def test_timings_route_pending_then_filled_by_the_audio_stream(student, monkeypatch):
    _edge_voice_on(monkeypatch)
    text = "Signed narration here."
    link = speech.audio_link(text, f"edge:{ANDREW}")
    tlink = speech.timings_link(link)
    r = student.get(tlink)
    assert r.status_code == 200 and r.json() == {"words": None, "source": "pending"}
    assert r.headers["cache-control"] == "no-store"
    used = limits.read_counter(limits.voice_key("free"))
    audio = student.get(link)
    assert audio.status_code == 200
    r = student.get(tlink)
    assert r.json() == {"words": [[0.0, 0], [1.0, 7], [2.0, 17]], "source": "edge"}
    assert "max-age=86400" in r.headers["cache-control"]
    assert limits.read_counter(limits.voice_key("free")) == used + len(text)  # timings spent nothing more


def test_a_stream_that_breaks_saves_no_timings(student, monkeypatch):
    class Breaks(WordComm):
        async def stream(self):
            yield {"type": "WordBoundary", "offset": 0, "duration": 1, "text": "One"}
            yield {"type": "audio", "data": b"x" * 10}
            if "Second" in self.text:
                raise RuntimeError("dropped")

    monkeypatch.setattr(edge_voice.edge_tts, "Communicate", Breaks)
    monkeypatch.setattr(edge_voice, "ATTEMPTS", 1)
    settings_store.put({"voice_id": f"edge:{ANDREW}"})
    text = "One. " + "x" * 395 + ". Second piece here."
    link = speech.audio_link(text, f"edge:{ANDREW}")
    assert student.get(link).status_code == 200  # the first piece played; the rest broke off
    assert student.get(speech.timings_link(link)).json()["source"] == "pending"


def test_timings_route_rate_limit(student, monkeypatch):
    import time as _time

    fixed = _time.gmtime(1_800_000_000)  # one minute window, even on a slow runner (as in test_api)
    monkeypatch.setattr(_time, "gmtime", lambda *args: fixed)
    monkeypatch.setattr("app.main.TIMINGS_PER_MINUTE", 2)
    link = speech.timings_link(speech.audio_link("Words.", f"edge:{ANDREW}"))
    assert [student.get(link).status_code for _ in range(3)] == [200, 200, 429]


# ---------------------------------------------------------------- playlists carry the links

def test_live_segments_carry_timings_and_boxes(student, content_dir, monkeypatch):
    use_fakes()
    settings_store.put({"voice_id": f"edge:{ANDREW}"})
    seg = student.post("/api/ask", json={"question": "what is an apple", "course": "70445"}).json()["segments"][0]
    assert seg["timings"] == speech.timings_link(seg["audio"])
    assert seg["timings_fallback"] is None
    assert seg["boxes"].startswith(f"/api/files/slides/70445/s01/{seg['slide_id']}.boxes.json?exp=")
    settings_store.put({"voice_id": "none"})
    seg = student.post("/api/ask", json={"question": "what is an apple", "course": "70445"}).json()["segments"][0]
    assert seg["audio"] is None and seg["timings"] is None and seg["boxes"]


def test_fallback_voice_gets_its_own_timings(student, monkeypatch):
    use_fakes()
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    monkeypatch.setattr(speech, "find_voice", lambda vid, timeout=4.0: None)
    settings_store.put({"voice_id": "eleven:roger", "voice_fallback": "free"})
    seg = student.post("/api/ask", json={"question": "what is an apple", "course": "70445"}).json()["segments"][0]
    assert seg["timings_fallback"] == speech.timings_link(seg["audio_fallback"])


def test_boxes_path():
    assert playlist.boxes_path({"image": "slides/70445/s06/70445-s06-014.webp"}) == \
        "slides/70445/s06/70445-s06-014.boxes.json"
    for image in (None, "", "clips/x.mp4", "slides/a/b-thumb.webp", "other/x.webp"):
        assert playlist.boxes_path({"image": image}) is None


def test_links_route_refreshes_boxes(student):
    r = student.post("/api/links", json={"slide_ids": ["70445-s01-002"]})
    assert r.status_code == 200
    assert "/70445-s01-002.boxes.json?" in r.json()["links"]["70445-s01-002"]["boxes"]


def test_stored_topic_signs_the_words_sidecar(student, monkeypatch, content_dir):
    use_fakes()
    tag = speech.voice_tag("voice777")
    folder = content_dir / "audio" / tag
    folder.mkdir(parents=True)
    (folder / "fakehash.mp3").write_bytes(b"ID3fake")
    (folder / "fakehash.words.json").write_text(json.dumps({"words": [[0.0, 0]], "source": "elevenlabs"}))
    topics_path = content_dir / "topics" / "topics.json"
    topics = json.loads(topics_path.read_text())
    for t in topics:
        for s in (t.get("playlist") or {}).get("segments", []):
            s["audio_path"] = f"audio/{tag}/fakehash.mp3"
    topics_path.write_text(json.dumps(topics))
    storage.store.reset()
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice777")
    seg = student.post("/api/ask", json={"question": "Show me the banana slide"}).json()["segments"][0]
    assert seg["audio"].startswith(f"/api/files/audio/{tag}/fakehash.mp3?")
    assert seg["timings"].startswith(f"/api/files/audio/{tag}/fakehash.words.json?")
    assert student.get(seg["timings"]).json()["words"] == [[0.0, 0]]
