"""The logistics check: meetings, absences, grades and deadlines go to Ben, not the twin.

Every model here is a TEST FAKE; retrieval uses the TEST FAKE retriever from test_api.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import limits, logistics
from app.main import Retriever, app, get_completer, get_embedder, get_retriever

from test_api import TEST_FAKE_embedder, TEST_FAKE_llm, TEST_FAKE_rank, TEST_FAKE_select

ROOT = Path(__file__).resolve().parents[1]

LOGISTICS_QUESTIONS = [
    "Could we set up a meeting this week to talk about my project?",
    "Can I come to office hours on Thursday?",
    "Are you free for a zoom call tomorrow?",
    "I missed class on Tuesday, what should I do?",
    "I will be absent next week for a conference",
    "I'm sick and can't make it today",
    "Can I get an extension on homework 3?",
    "Is a deadline extension possible for the project?",
    "Could you extend the deadline for the report?",
    "I'd like to request a regrade for quiz 2",
    "Why is my grade on the midterm so low?",
    "How is the final project graded?",
    "Can we reschedule our presentation?",
    "Can my team swap presentation slots with another team?",
    "I can't access the course on Canvas",
    "The Canvas page won’t load and I can’t access the slides",
    "My team member is not responding to messages",
    "Is it too late to drop the class?",
    "What is the attendance policy?",
]

CONCEPT_QUESTIONS = [
    "how do I choose k",
    "how do I choose k for k-means?",
    "what is overfitting",
    "What is gradient descent?",
    "How does backpropagation update the weights?",
    "What is the business model canvas for an AI product?",
    "How does the model handle the absence of labels?",
    "What is an extension of logistic regression to many classes?",
    "Why does the training loss go down but the test loss go up?",
    "What roles do team members play in an AI project?",
    "How do I read a confusion matrix?",
    "What is the silhouette score?",
    "How do agents call tools?",
    "Explain how a decision tree picks a split",
]


@pytest.mark.parametrize("question", LOGISTICS_QUESTIONS)
def test_keyword_precheck_catches_obvious_logistics(question):
    assert logistics.keyword_hit(question), question


@pytest.mark.parametrize("question", CONCEPT_QUESTIONS)
def test_keyword_precheck_leaves_concept_questions_alone(question):
    assert logistics.keyword_hit(question) is None, logistics.keyword_hit(question)


# ---------------------------------------------------------------- classifier with a TEST FAKE model

class FakeModel:
    """TEST FAKE: replies with a canned string (or raises) and records the calls."""

    def __init__(self, reply=None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls: list[tuple[str, str, int, dict]] = []

    def __call__(self, system, user, max_tokens, **kwargs):
        self.calls.append((system, user, max_tokens, kwargs))
        if self.error:
            raise self.error
        return self.reply


def test_keyword_hit_skips_the_model():
    model = FakeModel(error=AssertionError("must not be called"))
    result = logistics.classify("Can I get an extension on the homework?", model)
    assert result.kind == logistics.LOGISTICS and result.source == "keyword"
    assert model.calls == []


def test_model_routes_logistics():
    model = FakeModel(json.dumps({"kind": "logistics", "reason": "asks about a recording"}))
    result = logistics.classify("Where can I find the recording from week 3?", model, provider="anthropic", model="m")
    assert result.kind == logistics.LOGISTICS and result.source == "llm"
    assert len(model.calls) == 1
    system, user, max_tokens, kwargs = model.calls[0]
    assert system == logistics.SYSTEM_PROMPT and max_tokens == logistics.MAX_TOKENS
    assert kwargs == {"provider": "anthropic", "model": "m"}
    assert json.loads(user.split("\n", 1)[1]) == {"message": "Where can I find the recording from week 3?"}


def test_model_routes_course_content_even_in_a_code_fence():
    model = FakeModel('```json\n{"kind": "course_content", "reason": "concept"}\n```')
    result = logistics.classify("what is overfitting", model)
    assert result.kind == logistics.COURSE_CONTENT and result.source == "llm"


@pytest.mark.parametrize(
    "model",
    [
        FakeModel(error=RuntimeError("provider down")),
        FakeModel("not json at all"),
        FakeModel(json.dumps({"kind": "chitchat"})),
        FakeModel(json.dumps(["logistics"])),
        FakeModel(None),
    ],
)
def test_classifier_failure_falls_through_to_course_content(model):
    result = logistics.classify("what is overfitting", model)
    assert result.kind == logistics.COURSE_CONTENT and result.source == "error"


def test_referral_shape_and_copy():
    body = logistics.referral("can we meet?", ["a", "b", "c", "d"])
    assert body == {
        "question": "can we meet?",
        "covered": False,
        "kind": "logistics",
        "segments": [],
        "sources": [],
        "message": "That one is for me directly, not my twin. Please email me or come to office hours.",
        "follow_ups": ["a", "b", "c"],
    }
    assert "—" not in body["message"] and "–" not in body["message"]


# ---------------------------------------------------------------- through /api/ask

def _use(completer):
    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: completer


def _routing_llm(kind: str, calls: list[str]):
    """TEST FAKE: answers the logistics check with `kind`, narration like TEST_FAKE_llm."""

    def fake(system, user, max_tokens, provider=None, model=None):
        if system == logistics.SYSTEM_PROMPT:
            calls.append("classify")
            return json.dumps({"kind": kind, "reason": "fake"})
        calls.append("narrate")
        return TEST_FAKE_llm(system, user, max_tokens, provider, model)

    return fake


def test_ask_keyword_logistics_returns_referral_without_audio(student, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice123")
    calls: list[str] = []
    _use(_routing_llm("course_content", calls))
    # Office hours and meetings now get Ben's Calendly FAQ answer (app/faq.py); a regrade has no FAQ entry.
    r = student.post("/api/ask", json={"question": "Can I get a regrade on my fruit quiz?", "course": "70445"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "logistics" and body["covered"] is False and body["segments"] == []
    assert body["message"] == logistics.MESSAGE
    assert "audio" not in json.dumps(body)
    assert body["follow_ups"] and all(isinstance(f, str) for f in body["follow_ups"])
    assert calls == []  # keyword path: no model call at all
    row = limits._mem_log[-1]
    assert row["kind"] == "logistics" and row["covered"] is False


def test_ask_model_logistics_returns_referral(student):
    calls: list[str] = []
    _use(_routing_llm("logistics", calls))
    r = student.post("/api/ask", json={"question": "Where is the fruit recording from last week?"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "logistics"
    assert calls == ["classify"]  # no narration call


def test_ask_course_content_is_narrated_and_logged(student):
    calls: list[str] = []
    _use(_routing_llm("course_content", calls))
    r = student.post("/api/ask", json={"question": "Tell me about fruit"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["covered"] is True and len(body["segments"]) == 3 and "kind" not in body
    assert calls == ["classify", "narrate"]
    assert limits._mem_log[-1]["kind"] == "course_content"


def test_ask_classifier_failure_still_answers(student):
    calls: list[str] = []

    def broken(system, user, max_tokens, provider=None, model=None):
        if system == logistics.SYSTEM_PROMPT:
            calls.append("classify")
            raise RuntimeError("provider down")
        return TEST_FAKE_llm(system, user, max_tokens, provider, model)

    _use(broken)
    r = student.post("/api/ask", json={"question": "Tell me about fruit"})
    assert r.status_code == 200, r.text
    assert r.json()["covered"] is True and len(r.json()["segments"]) == 3
    assert calls == ["classify"]


def test_not_covered_skips_the_check_and_logs_no_kind(student):
    calls: list[str] = []
    _use(_routing_llm("logistics", calls))
    r = student.post("/api/ask", json={"question": "who won the Stanley Cup"})
    assert r.status_code == 200 and r.json()["covered"] is False and "kind" not in r.json()
    assert calls == []
    assert limits._mem_log[-1]["kind"] is None


def test_question_log_keeps_the_scrub_with_kind():
    limits.log_question("email me at someone@example.com about my grade", 0.55, False, "anthropic", "m", 10, None,
                        kind="logistics")
    row = limits._mem_log[-1]
    assert row["kind"] == "logistics"
    assert "someone@example.com" not in row["question"]


# ---------------------------------------------------------------- frontend

def test_frontend_shows_the_logistics_message():
    js = (ROOT / "public" / "app.js").read_text()
    copy = re.search(r"logistics: \[(.*?)\],\n", js, re.S)
    assert copy, "COPY.logistics is missing"
    assert "That one is for me directly." in copy.group(1)
    assert "email me or come to office hours" in copy.group(1)
    assert "—" not in copy.group(1) and "–" not in copy.group(1)
    assert "answer.kind === 'logistics'" in js
    assert "showStageError('logistics', question, answer)" in js
