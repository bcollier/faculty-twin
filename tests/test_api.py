"""End-to-end API tests against the synthetic fixture.

Retrieval is Ben's hand-written code, so these tests inject a TEST FAKE
retriever, embedder, and LLM through FastAPI dependency overrides. The fake
does no ranking math: it returns canned results keyed on the question text.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import numpy as np

from app import speech
from app.main import Retriever, app, get_completer, get_embedder, get_retriever

FRUIT_IDS = ["70445-s01-002", "70445-s01-003", "70445-s01-004"]


# ---------------------------------------------------------------- TEST FAKES (tests only)

def TEST_FAKE_embedder(question: str) -> np.ndarray:
    """TEST FAKE: an 8-dim vector; 'fruit' questions point one way, others another."""
    v = np.zeros(8, dtype=np.float32)
    v[0 if "apple" in question.lower() or "fruit" in question.lower() else 1] = 1.0
    return v


def TEST_FAKE_rank(question_vec, matrix):
    """TEST FAKE: no similarity math, just a fixed fake ordering of the rows."""
    on_topic = bool(question_vec[0])
    return [(i, 0.9 if on_topic else 0.05) for i in range(matrix.shape[0])]


def TEST_FAKE_select(ranked, records, threshold):
    """TEST FAKE: canned selection by id, not Ben's selection rules."""
    if not ranked or ranked[0][1] < 0.5:
        return []
    return [r for r in records if r["id"] in FRUIT_IDS]


def TEST_FAKE_llm(system, user, max_tokens, provider=None, model=None):
    ids = [s["slide_id"] for s in json.loads(user.split("Slides, in the order they will be shown:\n", 1)[1])]
    return json.dumps(
        {
            "segments": [{"slide_id": i, "narration": f"On this slide, fake narration for {i}."} for i in ids],
            "follow_ups": ["What is a banana?", "Why peel it?"],
        }
    )


def use_fakes():
    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: TEST_FAKE_llm


# ---------------------------------------------------------------- tests

def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_ask_returns_503_until_retrieval_is_written(student):
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    r = student.post("/api/ask", json={"question": "what is an apple"})
    assert r.status_code == 503
    assert r.json()["detail"] == "retrieval not implemented yet"


def test_ask_end_to_end_with_test_fake(student, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    use_fakes()
    r = student.post("/api/ask", json={"question": "  What is an   apple?  ", "course": "70445"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"question", "covered", "segments", "sources", "follow_ups"}
    assert body["question"] == "What is an apple?"
    assert body["covered"] is True
    assert [s["slide_id"] for s in body["segments"]] == FRUIT_IDS
    seg = body["segments"][0]
    assert set(seg) == {
        "n", "slide_id", "course", "course_title", "session", "session_title", "date", "slide_number",
        "image", "narration", "audio", "code", "clip",
    }
    assert seg["n"] == 1 and seg["course"] == "70445" and seg["session"] == 1 and seg["slide_number"] == 2
    assert seg["date"] == "2026-09-01" and seg["course_title"] == "Fake Course A"
    assert seg["narration"].startswith("On this slide")
    assert seg["image"].startswith("/api/files/slides/70445/s01/70445-s01-002.webp?exp=")
    assert seg["clip"]["url"].startswith("/api/files/clips/70445-s01-002.mp4") and seg["clip"]["start"] == 12.0
    assert seg["code"] is None
    assert body["segments"][2]["code"] == {"source": "def peel(banana):\n    return banana.strip()\n", "mark_lines": [2]}
    assert body["segments"][1]["clip"] is None

    # audio link is signed and plays through the audio route
    q = {k: v[0] for k, v in parse_qs(urlparse(seg["audio"]).query).items()}
    assert speech.verify(q["t"], q["v"], q["s"]) == seg["narration"]

    # signed image link works with the cookie, fails without it or when tampered
    assert student.get(seg["image"]).status_code == 200
    assert student.get(seg["image"].replace("sig=", "sig=x")).status_code == 403
    assert body["sources"][0] == {
        "slide_id": "70445-s01-002", "course": "70445", "session": 1, "date": "2026-09-01",
        "slide_number": 2, "image": seg["image"],
    }
    assert body["follow_ups"] == ["What is a banana?", "Why peel it?"]


def test_ask_not_covered(student):
    use_fakes()
    r = student.post("/api/ask", json={"question": "who won the stanley cup"})
    assert r.status_code == 200
    assert r.json() == {
        "question": "who won the stanley cup", "covered": False, "segments": [], "sources": [], "follow_ups": [],
    }


def test_ask_course_filter(student):
    use_fakes()
    r = student.post("/api/ask", json={"question": "apple", "course": "45884"})
    assert r.json()["covered"] is False  # the fruit slides are all in 70445


def test_ask_without_voice_has_no_audio(student):
    use_fakes()
    r = student.post("/api/ask", json={"question": "apple"})
    assert all(s["audio"] is None for s in r.json()["segments"])


def test_ask_llm_failure_falls_back_to_notes(student):
    use_fakes()

    def broken(*a, **k):
        return "not json"

    app.dependency_overrides[get_completer] = lambda: broken
    body = student.post("/api/ask", json={"question": "apple"}).json()
    assert body["covered"] is True
    assert body["segments"][0]["narration"] == "Fake speaker notes about what is an apple."
    assert body["segments"][1]["narration"] == "Fake class transcript about apple varieties."  # no notes


def test_ask_input_checks(student):
    use_fakes()
    assert student.post("/api/ask", json={"question": "   "}).json()["detail"] == "Please type a question."
    r = student.post("/api/ask", json={"question": "x" * 301})
    assert r.status_code == 400 and "300" in r.json()["detail"]
    r = student.post("/api/ask", json={"question": "apple", "course": "15113"})
    assert r.status_code == 400
    r = student.post("/api/ask", json={})
    assert r.status_code == 400 and r.json()["detail"].startswith("question")
    r = student.post("/api/ask", content=b"{nope", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_ask_rate_limit(student):
    use_fakes()
    for _ in range(5):
        assert student.post("/api/ask", json={"question": "apple"}).status_code == 200
    r = student.post("/api/ask", json={"question": "apple"})
    assert r.status_code == 429


def test_stored_topic_playlist(student):
    use_fakes()
    r = student.post("/api/ask", json={"question": "show me the banana slide!"})
    body = r.json()
    assert [s["slide_id"] for s in body["segments"]] == ["70445-s01-004"]
    assert body["segments"][0]["narration"] == "Stored narration about peeling."
    assert body["segments"][0]["audio"] is None  # captions only: no voice configured


def test_stored_topic_audio_follows_voice(student, monkeypatch):
    use_fakes()
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    seg = student.post("/api/ask", json={"question": "Show me the banana slide"}).json()["segments"][0]
    assert seg["audio"].startswith("/api/files/audio/voice123/fakehash.mp3?exp=")
    assert student.get(seg["audio"]).status_code == 200
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice999")
    seg = student.post("/api/ask", json={"question": "Show me the banana slide"}).json()["segments"][0]
    assert seg["audio"].startswith("/api/audio?t=")  # stored mp3 was made with another voice


def test_courses_and_topics(student):
    courses = student.get("/api/courses").json()
    assert courses == [
        {"course": "45884", "title": "Fake Course B", "sessions": [{"session": 2, "date": "2026-09-03", "title": "Weather words"}]},
        {"course": "70445", "title": "Fake Course A", "sessions": [{"session": 1, "date": "2026-09-01", "title": "Fruit basics"}]},
    ]
    topics = student.get("/api/topics").json()
    assert topics == [
        {"question": "What is an apple?", "course": "70445"},
        {"question": "Show me the banana slide", "course": "70445"},
    ]


def test_dev_file_route_blocks_paths(student):
    r = student.get("/api/files/content/index.json?exp=9999999999&sig=x")
    assert r.status_code == 403


def test_question_log_written(admin):
    use_fakes()
    admin.post("/api/ask", json={"question": "apple"})
    rows = admin.get("/api/admin/log").json()["rows"]
    assert rows[0]["question"] == "apple" and rows[0]["covered"] is True
    assert rows[0]["provider"] == "anthropic"
