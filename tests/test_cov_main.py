"""app/main.py: which path answers a question (answer()), the error paths, and the small routes.

Retrieval is Ben's hand-written code, so every test here injects a TEST FAKE
retriever that does no ranking math (fixed scores), the same way tests/test_api.py does.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import numpy as np
import pytest
from fastapi import HTTPException

from app import edge_voice, embed, main, speech, storage
from app.main import Retriever, app, get_completer, get_embedder, get_retriever

FRUIT_IDS = ["70445-s01-002", "70445-s01-003"]


# ---------------------------------------------------------------- TEST FAKES (tests only)

def TEST_FAKE_rank(score: float):
    """TEST FAKE: every row gets the same fixed score; no similarity math."""
    return lambda qvec, matrix: [(i, score) for i in range(matrix.shape[0])]


def TEST_FAKE_select(ranked, records, threshold):
    """TEST FAKE: canned selection by id."""
    if not ranked or ranked[0][1] < 0.5:
        return []
    return [r for r in records if r["id"] in FRUIT_IDS]


def TEST_FAKE_embedder(dim: int = 8):
    return lambda q: np.ones(dim, dtype=np.float32)


class TEST_FAKE_LLM:
    """TEST FAKE: answers the logistics check with `kind`, and narration with one sentence per slide."""

    def __init__(self, kind: str = "course_content"):
        self.kind = kind
        self.calls: list[str] = []

    def __call__(self, system, user, max_tokens, provider=None, model=None):
        if user.startswith("Student message"):
            self.calls.append("logistics")
            return json.dumps({"kind": self.kind, "reason": "fake"})
        self.calls.append("narration")
        ids = [s["slide_id"] for s in json.loads(user.split("Slides, in the order they will be shown:\n", 1)[1])]
        return json.dumps({"segments": [{"slide_id": i, "narration": f"On this slide, fake narration for {i}."}
                                        for i in ids], "follow_ups": []})


@pytest.fixture
def content(content_dir) -> storage.Content:
    return storage.load_local(content_dir)


def run(question, content, score=0.9, llm=None, embedder=None, course=None):
    llm = llm or TEST_FAKE_LLM()
    return main.answer(question, course, content, Retriever(TEST_FAKE_rank(score), TEST_FAKE_select, 0.5),
                       embedder or TEST_FAKE_embedder(), llm, provider="anthropic", model="claude-sonnet-5-5")


# ---------------------------------------------------------------- answer() routing

def test_stored_topic_needs_no_search_or_model(content):
    llm = TEST_FAKE_LLM()

    def no_embed(q):
        raise AssertionError("a stored topic must not embed")

    reply, info = run("show me the BANANA slide!", content, llm=llm, embedder=no_embed)
    assert info["kind"] == "stored_topic" and info["narration"] == "stored"
    assert info["provider"] is None and info["model"] is None
    assert reply["segments"][0]["narration"] == "Stored narration about peeling."
    assert llm.calls == []


def test_stored_topic_respects_the_course_filter(content):
    _reply, info = run("Show me the banana slide", content, course="45884")
    assert info["kind"] != "stored_topic"


def test_covered_question_runs_logistics_check_then_narration(content):
    llm = TEST_FAKE_LLM()
    reply, info = run("Explain what fruit is made of", content, llm=llm)
    assert info["kind"] == "course_content" and info["narration"] == "llm"
    assert info["provider"] == "anthropic" and info["top_score"] == pytest.approx(0.9)
    assert llm.calls == ["logistics", "narration"]
    assert [s["slide_id"] for s in reply["segments"]] == FRUIT_IDS


def test_logistics_by_keyword_makes_no_model_call(content):
    llm = TEST_FAKE_LLM()
    reply, info = run("Is the waitlist moving for the fruit course?", content, llm=llm)
    assert info["kind"] == "logistics" and info["kind_source"] == "keyword"
    assert info["provider"] is None  # no model was called
    assert reply["covered"] is False and reply["segments"] == []
    assert reply["links"] and reply["contacts"] is not None
    assert llm.calls == []


def test_logistics_by_model_records_the_model(content):
    llm = TEST_FAKE_LLM(kind="logistics")
    reply, info = run("Something about fruit that the model sorts as logistics", content, llm=llm)
    assert info["kind"] == "logistics" and info["kind_source"] == "llm"
    assert info["model"] == "claude-sonnet-5-5"
    assert llm.calls == ["logistics"] and reply["kind"] == "logistics"


def test_below_threshold_is_not_covered_without_a_model_call(content):
    llm = TEST_FAKE_LLM()
    reply, info = run("Who won the cup?", content, score=0.1, llm=llm)
    assert info["kind"] == "not_covered" and reply["covered"] is False
    assert llm.calls == [] and info["provider"] is None


def test_course_with_no_slides_is_not_covered(content):
    reply, info = run("What is a cloud", content, course="45884", score=0.1)
    assert info["kind"] == "not_covered"


def test_empty_index_is_not_covered_before_embedding(content):
    empty = storage.Content([], np.zeros((0, 8), dtype=np.float32))

    def no_embed(q):
        raise AssertionError("nothing to search: no embedding call")

    reply, info = run("anything", empty, embedder=no_embed)
    assert info["kind"] == "not_covered" and reply["covered"] is False


def test_embedding_cap_and_failure_are_503(content):
    def capped(q):
        raise embed.EmbeddingCapReached("cap")

    def broken(q):
        raise embed.EmbeddingError("down")

    with pytest.raises(HTTPException) as err:
        run("fruit", content, embedder=capped)
    assert err.value.status_code == 503 and "tomorrow" in err.value.detail
    with pytest.raises(HTTPException) as err:
        run("fruit", content, embedder=broken)
    assert err.value.status_code == 503 and "search service" in err.value.detail


def test_embedding_dimension_mismatch_is_503(content):
    with pytest.raises(HTTPException) as err:
        run("fruit", content, embedder=TEST_FAKE_embedder(5))
    assert err.value.status_code == 503 and "rebuild" in err.value.detail


def test_stale_course_info_index_never_blocks_slide_answers(content):
    content.info_records = [{"id": "info-1", "course": "70445", "title": "Syllabus", "text": "Syllabus text."}]
    content.info_matrix = np.ones((1, 3), dtype=np.float32)  # wrong dimension
    reply, info = run("Explain fruit", content)
    assert info["kind"] == "course_content" and reply["covered"] is True


def test_select_segments_stub_is_retrieval_not_ready(content):
    def stub(*a, **k):
        raise NotImplementedError

    with pytest.raises(main.RetrievalNotReady):
        main.answer("fruit", None, content, Retriever(TEST_FAKE_rank(0.9), stub, 0.5), TEST_FAKE_embedder(),
                    TEST_FAKE_LLM(), provider="anthropic", model="m")


# ---------------------------------------------------------------- helpers

def test_clean_question_and_course():
    assert main.clean_question("  a \n\t b ") == "a b"
    for bad in ("", "   ", "x" * 301):
        with pytest.raises(HTTPException) as err:
            main.clean_question(bad)
        assert err.value.status_code == 400
    assert main.clean_course("all") is None and main.clean_course("") is None and main.clean_course(None) is None
    assert main.clean_course("70445") == "70445"
    with pytest.raises(HTTPException):
        main.clean_course("99999")


# ---------------------------------------------------------------- routes

def test_logout_clears_cookie(student):
    r = student.post("/api/logout")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert 'ft_session=""' in r.headers["set-cookie"] or "Max-Age=0" in r.headers["set-cookie"]
    student.cookies.clear()
    assert student.get("/api/topics").status_code == 401


def test_non_json_body_is_a_readable_400(student):
    r = student.post("/api/ask", content=b"not json", headers={"content-type": "application/json"})
    assert r.status_code == 400 and r.json()["detail"] == "The request body must be JSON."
    r = student.post("/api/ask", json={"course": "70445"})
    assert r.status_code == 400 and r.json()["detail"].startswith("question")


def test_cross_site_post_is_refused(student):
    r = student.post("/api/ask", json={"question": "fruit"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and "Cross-site" in r.json()["detail"]
    r = student.post("/api/ask", json={"question": "fruit"}, headers={"sec-fetch-site": "cross-site"})
    assert r.status_code == 403


def test_json_responses_are_not_cached(client):
    r = client.get("/api/health")
    assert r.headers["cache-control"] == "no-store"


def test_bad_course_on_ask_is_400(student):
    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank(0.9), TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder()
    app.dependency_overrides[get_completer] = lambda: TEST_FAKE_LLM()
    r = student.post("/api/ask", json={"question": "fruit", "course": "12345"})
    assert r.status_code == 400 and "course" in r.json()["detail"]


def test_dev_file_route(student, content_dir):
    links = storage.media_urls(["slides/70445/s01/70445-s01-002.webp"])
    link = links["slides/70445/s01/70445-s01-002.webp"]
    r = student.get(link)
    assert r.status_code == 200 and r.content.startswith(b"RIFF")
    q = parse_qs(urlparse(link).query)
    bad = f"/api/files/slides/70445/s01/70445-s01-002.webp?exp={q['exp'][0]}&sig={'0' * 32}"
    assert student.get(bad).status_code == 403
    missing = storage.media_urls(["slides/none.webp"])["slides/none.webp"]
    assert student.get(missing).status_code == 404


def test_dev_file_route_is_off_without_content_dir(student, monkeypatch):
    link = storage.media_urls(["slides/70445/s01/70445-s01-002.webp"])["slides/70445/s01/70445-s01-002.webp"]
    storage.store.get()  # keep the loaded content
    monkeypatch.delenv("CONTENT_DIR")
    assert student.get(link).status_code == 404


def test_audio_route_elevenlabs_failure_is_502(student, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")

    async def broken(text, voice_id):
        raise speech.VoiceError("down")

    monkeypatch.setattr(speech, "open_stream", broken)
    r = student.get(speech.audio_link("Signed narration.", "voice123"))
    assert r.status_code == 502 and "Captions only" in r.json()["detail"]


def test_audio_route_free_voice_failure_is_502(student, monkeypatch):
    voice = "edge:en-US-AndrewMultilingualNeural"
    from app import settings_store

    settings_store.put({"voice_id": voice})

    async def broken(text, voice_id):
        raise edge_voice.FreeVoiceError("down")

    monkeypatch.setattr(edge_voice, "open_stream", broken)
    r = student.get(speech.audio_link("Signed narration.", voice))
    assert r.status_code == 502


def test_audio_route_free_voice_streams(student, monkeypatch):
    voice = "edge:en-US-AndrewMultilingualNeural"
    from app import settings_store

    settings_store.put({"voice_id": voice})
    seen = []

    async def fake_stream(text, voice_id):
        seen.append((text, voice_id))

        async def gen():
            yield b"ID3free"

        return gen()

    monkeypatch.setattr(edge_voice, "open_stream", fake_stream)
    r = student.get(speech.audio_link("Signed narration.", voice))
    assert r.status_code == 200 and r.content == b"ID3free"
    assert seen and seen[0][0] == "Signed narration."
