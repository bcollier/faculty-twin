"""Cross-course fallback (docs/SPEC.md step 7c) and the "Closest material" floor (step 7b).

Oct 8 live bug: with the course filter on 45-884, "What are frames and semantic networks in knowledge
representation?" got a web answer, because its best 45-884 slide scored 0.412 while 70-445 teaches it
(0.594). These tests use the real `retrieval.rank` and `retrieval.select_segments` (first written by Ben, rebuilt Oct 8)
over the synthetic fixture: the fruit slides are all in 70445, the weather slides in 45884. The embedder,
the model and the web search are TEST FAKES.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pytest
from test_course_info import FakeModel as InfoModel
from test_course_info import write_info
from test_web_answer import FakeModel, FakeSearch

# app.main first: it wires the routers that the admin modules import from.
from app.main import Retriever, app, get_completer, get_embedder, get_retriever, get_searcher

# isort: split
from app import limits, narration, playlist, retrieval, storage, web_answer

DIM = 8
E = np.eye(DIM, dtype=np.float32)
FRUIT_IDS = ["70445-s01-002", "70445-s01-003", "70445-s01-004"]
INTRO = ("My 45-884 slides don't cover that, but I taught it in 70-445 (Fake Course A). "
         "Here are 3 slides from session 1 (Fruit basics). I'll walk you through them.")


def TEST_FAKE_embedder(question: str) -> np.ndarray:
    """TEST FAKE: fruit words point at the fruit slides (70445), weather words at 45884, the rest nowhere."""
    q = question.lower()
    if "fruit" in q or "apple" in q or "banana" in q:
        return E[0].copy()
    if "rain" in q or "cloud" in q:
        return E[1].copy()
    return E[5].copy()  # orthogonal to every slide: scores 0 everywhere


def _use(model, searcher=None, threshold=retrieval.NOT_COVERED_THRESHOLD):
    app.dependency_overrides[get_retriever] = lambda: Retriever(retrieval.rank, retrieval.select_segments, threshold)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: model
    app.dependency_overrides[get_searcher] = lambda: searcher or FakeSearch()


def _ask(student, question, course=None):
    r = student.post("/api/ask", json={"question": question, "course": course})
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- the fallback through /api/ask

def test_filter_with_nothing_but_the_other_course_over_the_threshold_answers_from_it(student):
    model, search = FakeModel(), FakeSearch()
    _use(model, search)
    body = _ask(student, "What is an apple?", course="45884")
    assert body["covered"] is True and body["kind"] == "cross_course" and body["asked_course"] == "45884"
    assert [s["slide_id"] for s in body["segments"]] == FRUIT_IDS
    assert body["message"] == INTRO
    # Exactly as an all-courses answer: the logistics check, then narration, signed links, clips and code.
    assert model.calls == ["logistics", "narrate"] and search.calls == []
    seg = body["segments"][0]
    assert seg["narration"].startswith("On this slide") and "sig=" in seg["image"]
    assert seg["clip"]["url"].startswith("/api/files/clips/70445-s01-002.mp4")
    assert body["segments"][2]["code"]["source"].startswith("def peel")
    row = limits._mem_log[-1]
    assert row["kind"] == "cross_course" and row["covered"] is True and row["course"] == "45884"
    assert row["top_slide_id"] == "70445-s01-002" and row["top_score"] > 0.9  # the slides that answered


def test_all_courses_answer_is_unchanged(student):
    model = FakeModel()
    _use(model)
    body = _ask(student, "What is an apple?")
    assert body["covered"] is True and [s["slide_id"] for s in body["segments"]] == FRUIT_IDS
    assert "kind" not in body and "message" not in body and "asked_course" not in body
    assert limits._mem_log[-1]["kind"] == "course_content"


def test_filter_that_has_the_slides_stays_in_its_course(student):
    _use(FakeModel())
    body = _ask(student, "What is an apple?", course="70445")
    assert "kind" not in body and [s["slide_id"] for s in body["segments"]] == FRUIT_IDS
    weather = _ask(student, "Tell me about rain", course="45884")
    assert "kind" not in weather and {s["course"] for s in weather["segments"]} == {"45884"}
    assert limits._mem_log[-1]["kind"] == "course_content"


def test_filter_and_nothing_anywhere_goes_to_the_web_as_before(student):
    model, search = FakeModel(), FakeSearch()
    _use(model, search)
    body = _ask(student, "How do I set up n8n?", course="45884")
    assert body["kind"] == "web" and len(search.calls) == 1
    # Every slide scores 0 here, under the related-slide floor: the card has sources and no "Closest material".
    assert body["related"] == [] and body["links"]
    assert limits._mem_log[-1]["kind"] == "web"


def test_filter_and_nothing_anywhere_declines_as_before(student):
    model, search = FakeModel(), FakeSearch()
    _use(model, search)
    body = _ask(student, "Who won the Stanley Cup?", course="45884")
    assert body == {"question": "Who won the Stanley Cup?", "covered": False, "segments": [], "sources": [],
                    "follow_ups": []}
    assert search.calls == [] and model.calls == []
    assert limits._mem_log[-1]["kind"] == "not_covered"


def test_filter_and_nothing_anywhere_with_web_off_is_the_plain_decline(student):
    from app import settings_store

    settings_store.put({"web_answers_enabled": False})
    _use(FakeModel())
    assert _ask(student, "How do I set up n8n?", course="70445")["covered"] is False


def test_the_logistics_check_still_runs_on_the_other_courses_slides(student):
    class LogisticsModel(FakeModel):
        def __call__(self, system, user, max_tokens, provider=None, model=None):
            from app import logistics

            if system == logistics.SYSTEM_PROMPT:
                self.calls.append("logistics")
                return json.dumps({"kind": "logistics", "reason": "fake"})
            return super().__call__(system, user, max_tokens, provider, model)

    model = LogisticsModel()
    _use(model)
    body = _ask(student, "Something about apples the model sorts as logistics", course="45884")
    assert body["kind"] == "logistics" and body["segments"] == []
    assert model.calls == ["logistics"]  # no narration
    assert limits._mem_log[-1]["kind"] == "logistics"


def test_personal_requests_are_caught_before_the_other_course(student):
    model = FakeModel()
    _use(model)
    body = _ask(student, "Can I get a regrade on my apple quiz?", course="45884")
    assert body["kind"] == "logistics" and model.calls == []  # keyword route: no model call
    assert limits._mem_log[-1]["kind"] == "logistics"


def test_faq_still_wins_with_a_filter(student):
    model = FakeModel()
    _use(model)
    assert _ask(student, "When are your office hours?", course="45884")["kind"] == "faq"
    assert model.calls == []


FRUIT_POLICY = {"id": "info-45884-fruit-1", "course": "45884", "title": "Fruit policy", "kind": "page",
                "canvas_url": "https://canvas.cmu.edu/courses/54496/pages/fruit-policy", "due_at": None,
                "text": "The fruit policy says apples are allowed in class."}


def test_own_course_canvas_still_wins_over_the_other_courses_slides(content_dir, student):
    # A Canvas chunk of the filtered course clears the info threshold: Canvas (spec step 6a) answers before
    # step 7c could reach the other course's fruit slides, both for a "where is X on Canvas" question
    # (canvas_request) and for a plain one (info_margin over the filtered course's best slide).
    write_info(content_dir, [(FRUIT_POLICY, E[0])])
    storage.store.reset()
    model = InfoModel(info_reply=json.dumps({"answer": "The fruit policy says apples are allowed in class."}))
    _use(model)
    for q in ("Where is the fruit policy on Canvas?", "Are apples allowed in class?"):
        body = _ask(student, q, course="45884")
        assert body["kind"] == "course_info", q
        assert body["links"][0]["url"] == FRUIT_POLICY["canvas_url"]
    assert "narrate" not in model.calls


def test_hidden_sessions_stay_hidden_from_the_fallback(student, monkeypatch):
    monkeypatch.setattr(playlist, "hidden_sessions", lambda: {("70445", 1)})
    _use(FakeModel())
    body = _ask(student, "What is an apple?", course="45884")
    # Nothing visible clears the threshold anywhere, so the web path answers, and the hidden slides are not
    # offered as "Closest material" either.
    assert body["kind"] == "web" and body["segments"] == [] and body["related"] == []


def test_the_fallback_needs_no_second_embedding(content_dir):
    from app import main

    calls = []

    def embedder(q):
        calls.append(q)
        return TEST_FAKE_embedder(q)

    content = storage.load_local(content_dir)
    retriever = Retriever(retrieval.rank, retrieval.select_segments, retrieval.NOT_COVERED_THRESHOLD)
    reply, info = main.answer("Tell me about apples", "45884", content, retriever, embedder, FakeModel(),
                              provider="anthropic", model="fake")
    assert info["kind"] == "cross_course" and reply["kind"] == "cross_course" and len(calls) == 1


def test_a_select_that_picks_the_filtered_course_is_never_labeled_cross_course(content_dir):
    """Defensive: only the other course's slides can make a cross-course answer, whatever select returns."""
    from app import main

    def select(ranked, records, threshold):
        return [r for r in records if r["course"] == "45884"] if ranked and ranked[0][1] > 0.9 else []

    content = storage.load_local(content_dir)
    reply, info = main.answer("What is an apple?", "45884", content, Retriever(retrieval.rank, select, 0.52),
                              TEST_FAKE_embedder, FakeModel(), provider="anthropic", model="fake")
    assert info["kind"] == "not_covered" and reply["covered"] is False


# ---------------------------------------------------------------- the intro

REC = {"course": "70445", "course_title": "AI for Business Leaders", "session": 3,
       "session_title": "Rules Search and Expert Systems"}


def test_intro_for_the_frames_question():
    chosen = [{**REC, "id": f"70445-s03-0{n}"} for n in (26, 27, 28)]
    assert playlist.cross_course_intro("45884", chosen) == (
        "My 45-884 slides don't cover that, but I taught it in 70-445 (AI for Business Leaders). "
        "Here are 3 slides from session 3 (Rules Search and Expert Systems). I'll walk you through them.")


def test_intro_for_one_slide_several_sessions_and_missing_titles():
    one = playlist.cross_course_intro("45884", [REC])
    assert one.endswith("Here is 1 slide from session 3 (Rules Search and Expert Systems). I'll walk you through it.")
    two = playlist.cross_course_intro("45884", [REC, {**REC, "session": 4}])
    assert two.endswith("Here are 2 slides from 2 sessions. I'll walk you through them in order.")
    bare = playlist.cross_course_intro("70445", [{"course": "45884", "session": 2}])
    assert bare == ("My 70-445 slides don't cover that, but I taught it in 45-884. "
                    "Here is 1 slide from session 2. I'll walk you through it.")


@pytest.mark.parametrize("title", ["AI for Business Leaders", "Agents — and tools", "Search – Part 2"])
def test_intro_passes_the_voice_checks(title):
    text = playlist.cross_course_intro("45884", [{**REC, "course_title": title, "session_title": title}] * 2)
    assert "—" not in text and "–" not in text  # no em or en dash in student-facing copy
    assert not narration._URLISH.search(text)
    assert narration.speech_problem(text) is None
    assert narration.clean_speech(text) == text


def test_other_course_ranked_drops_the_filtered_course():
    records = [{"course": "45884"}, {"course": "70445"}, {"course": "70445"}]
    ranked = [(0, 0.9), (2, 0.6), (1, 0.5), (7, 0.4)]
    assert playlist.other_course_ranked(ranked, records, "45884") == [(2, 0.6), (1, 0.5)]


# ---------------------------------------------------------------- the related-slide floor

def test_related_slides_keep_only_scores_at_or_over_the_floor(content_dir, monkeypatch):
    monkeypatch.setenv("CONTENT_DIR", str(content_dir))  # signed thumbnails in local dev mode
    content = storage.load_local(content_dir)
    records, _ = playlist.searchable(content, None)
    ranked = [(0, 0.50), (1, web_answer.RELATED_MIN), (2, 0.419), (3, 0.30)]
    picked = web_answer.related_slides(content, ranked, records)
    assert [r["slide_id"] for r in picked] == [records[0]["id"], records[1]["id"]]
    assert web_answer.related_slides(content, [(0, 0.41), (1, 0.40)], records) == []


def test_related_min_sits_between_the_bug_and_the_related_slides():
    # Oct 8 live scores (docs/SPEC.md step 7b): unrelated 45-884 slides for the frames question 0.397 to 0.412;
    # related slides for course-adjacent questions no slide covers 0.416 to 0.443. Under the slide threshold.
    assert 0.412 < web_answer.RELATED_MIN <= 0.424
    assert web_answer.RELATED_MIN < retrieval.NOT_COVERED_THRESHOLD


def test_related_slides_come_from_every_course_whatever_the_filter(student):
    # "Bananas" (fruit) filtered to 45884 with a high threshold: no slide anywhere clears 0.999, so the web path
    # answers; its closest material is the 70445 fruit slides even though the filter is 45884.
    model, search = FakeModel("course_adjacent"), FakeSearch()
    _use(model, search, threshold=0.999)
    body = _ask(student, "How do people grow bananas commercially?", course="45884")
    assert body["kind"] == "web"
    assert body["related"] and {r["course"] for r in body["related"]} == {"70445"}
    assert all(re.match(r"^70445-s01-00[234]$", r["slide_id"]) for r in body["related"])


def test_no_link_and_nothing_over_the_floor_is_the_plain_decline(student):
    from app import llm

    model, search = FakeModel(), FakeSearch(error=llm.LLMError("boom"))
    _use(model, search)
    body = _ask(student, "How do I set up n8n?", course="45884")
    assert body["covered"] is False and body.get("kind") is None
