"""indexer/pregenerate.py against the synthetic fixture.

Ranking and selection are Ben's hand-written code, so the end-to-end test
injects a clearly labeled TEST FAKE retriever (as tests/test_api.py does).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import numpy as np

from app import main, speech, storage
from indexer import pregenerate


def TEST_FAKE_embedder(question: str) -> np.ndarray:
    v = np.zeros(8, dtype=np.float32)
    v[0] = 1.0
    return v


def TEST_FAKE_rank(question_vec, matrix):
    """TEST FAKE: no similarity math, a fixed ordering."""
    return [(i, 0.9) for i in range(matrix.shape[0])]


def TEST_FAKE_select(ranked, records, threshold):
    """TEST FAKE: canned selection by id, not Ben's rules."""
    return [r for r in records if r["id"] in ("70445-s01-002", "70445-s01-003")]


def TEST_FAKE_llm(system, user, max_tokens, provider=None, model=None):
    ids = [s["slide_id"] for s in json.loads(user.split("Slides, in the order they will be shown:\n", 1)[1])]
    return json.dumps(
        {"segments": [{"slide_id": i, "narration": f"On this slide, apples {i}."} for i in ids], "follow_ups": ["Next?"]}
    )


def test_draft_is_written_once_and_never_overwritten(tmp_path: Path):
    qs = pregenerate.ensure_draft(tmp_path, log=lambda s: None)
    assert 8 <= len(qs) <= 10 and {q["course"] for q in qs} == {"70445", "45884"}
    path = pregenerate.draft_path(tmp_path)
    path.write_text(json.dumps([{"question": "Ben's own question?", "course": "70445"}]))
    assert pregenerate.ensure_draft(tmp_path, log=lambda s: None) == [{"question": "Ben's own question?", "course": "70445"}]


def test_stops_while_retrieval_is_not_written(content_dir: Path, monkeypatch):
    def stub(*args, **kwargs):  # TEST FAKE: raises like the original stubs did
        raise NotImplementedError("stub")

    monkeypatch.setattr("app.retrieval.rank", stub)
    monkeypatch.setattr("app.retrieval.select_segments", stub)
    lines: list[str] = []
    code = pregenerate.generate(content_dir, [{"question": "What is an apple?", "course": "70445"}], None, log=lines.append)
    assert code == pregenerate.EXIT_RETRIEVAL
    assert "NotImplementedError" in "\n".join(lines)


def test_generates_playlists_and_audio_with_test_fakes(content_dir: Path, monkeypatch):
    spoken: list[str] = []

    def tts(request: httpx.Request) -> httpx.Response:
        spoken.append(json.loads(request.content)["text"])
        assert request.url.path == "/v1/text-to-speech/voice123/with-timestamps"  # read-along, Oct 8
        text = json.loads(request.content)["text"]
        alignment = {"characters": list(text), "character_start_times_seconds": [i * 0.05 for i in range(len(text))]}
        return httpx.Response(200, json={"audio_base64": base64.b64encode(b"ID3fake-mp3").decode(),
                                         "alignment": alignment})

    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-eleven-key")
    code = pregenerate.generate(
        content_dir,
        [{"question": "What is an apple?", "course": "70445"}],
        "voice123",
        retriever=main.Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5),
        embedder=TEST_FAKE_embedder,
        completer=TEST_FAKE_llm,
        tts_client=httpx.Client(transport=httpx.MockTransport(tts)),
        log=lambda s: None,
    )
    assert code == 0
    topics = json.loads((content_dir / "topics" / "topics.json").read_text())
    assert [t["question"] for t in topics] == ["What is an apple?"]
    segs = topics[0]["playlist"]["segments"]
    assert [s["slide_id"] for s in segs] == ["70445-s01-002", "70445-s01-003"]
    tag = speech.voice_tag("voice123")
    assert all(s["audio_path"].startswith(f"audio/{tag}/") and (content_dir / s["audio_path"]).exists() for s in segs)
    for s in segs:  # each clip has its word timings next to it
        assert (content_dir / s["audio_path"]).read_bytes() == b"ID3fake-mp3"
        words = json.loads((content_dir / s["audio_path"].replace(".mp3", ".words.json")).read_text())
        assert words["source"] == "elevenlabs" and words["words"][0] == [0.0, 0]
    assert len(spoken) == 2

    # The backend replays it as a stored topic.
    content = storage.load_local(content_dir)
    assert main._stored_topic(content, "what is an apple", "70445") is not None
