"""Course FAQ answers: matching, course filtering, TA contacts, the /api/ask path, and copy rules."""

from __future__ import annotations

import json
import re

import pytest

from app import faq
from app.main import Retriever, app, get_completer, get_embedder, get_retriever

TA = json.dumps({"70445": {"name": "", "email": "ta-one@example.edu"},
                 "45884": {"name": "Pat Example", "email": "ta-two@example.edu"}})


@pytest.mark.parametrize(
    "question, course, expected",
    [
        ("Can we meet on Thursday afternoon?", None, "meeting"),
        ("Could I set up a meeting with you next week?", "70445", "meeting"),
        ("Could I reschedule our team's presentation to next week?", "45884", "reschedule_presentation"),
        ("Can we swap our presentation slot with another team?", None, "reschedule_presentation"),
        ("I missed class today because of a job interview. Can I make up the exercise?", "70445", "missed_class"),
        ("Can I turn in lab 2 late?", None, "late_work"),
        ("How many grace days do we get?", "45884", "late_work"),
        ("My participation point didn't count on Canvas", "70445", "canvas_participation"),
        ("Can I do everything in R instead of Python?", "45884", "r_instead_of_python"),
        ("Can I use ChatGPT for the report?", "45884", "generative_ai_policy"),
        ("When is the final paper due compared to the final presentation?", "45884", "final_presentation_vs_paper"),
    ],
)
def test_logistics_questions_match_ben_faq(question, course, expected):
    m = faq.match(question, course)
    assert m is not None and m.entry.id == expected


@pytest.mark.parametrize(
    "question",
    ["How do I choose k for k-means?", "What is the difference in R-squared between models?",
     "Why does my KNN get worse with more columns?", "Explain how a neural network learns"],
)
def test_course_content_questions_do_not_match(question):
    assert faq.match(question, None) is None


def test_course_specific_entries_respect_the_course_filter():
    assert faq.match("Can I do everything in R instead of Python?", "70445") is None  # 45-884 only
    both = faq.match("I missed class today, what should I do?", None)
    assert [a["course_label"] for a in both.answers] == ["70-445", "45-884"]
    assert faq.message(both).startswith("For 70-445: ") and "\n\nFor 45-884: " in faq.message(both)
    one = faq.match("I missed class today, what should I do?", "45884")
    assert len(one.answers) == 1 and "master's program" in one.answers[0]["text"]


def test_ta_contacts_come_from_the_environment_and_are_checked(monkeypatch):
    assert faq.ta_contacts(None) == []
    monkeypatch.setenv("TA_CONTACTS", TA)
    assert [c["email"] for c in faq.ta_contacts(None)] == ["ta-one@example.edu", "ta-two@example.edu"]
    assert [c["course_label"] for c in faq.ta_contacts("45884")] == ["45-884"]
    monkeypatch.setenv("TA_CONTACTS", json.dumps({"70445": {"email": "not an email"}}))
    assert faq.ta_contacts("70445") == []
    monkeypatch.setenv("TA_CONTACTS", "{broken")
    assert faq.ta_contacts(None) == []


def test_entries_follow_the_copy_rules():
    raw = faq.ENTRIES_FILE.read_text(encoding="utf-8")
    assert "—" not in raw  # no em dashes in visitor copy
    assert "@" not in raw.replace("calendly.com/bencollierphd", "")  # no email addresses in the repo
    for e in faq.entries():
        for text in e.answers.values():
            assert not re.search(r"\b(code|passcode)\b[^.]{0,20}\b\d{3,}\b", text, re.I)  # no access codes
        for link in e.links:
            assert link["url"].startswith("https://")


def _no_retrieval():
    def boom(*args, **kwargs):
        raise AssertionError("an FAQ hit must not reach retrieval, embeddings, or a model")
    app.dependency_overrides[get_retriever] = lambda: Retriever(boom, boom, 0.5)
    app.dependency_overrides[get_embedder] = lambda: boom
    app.dependency_overrides[get_completer] = lambda: boom


def test_ask_answers_from_the_faq_before_retrieval(student, monkeypatch):
    monkeypatch.setenv("TA_CONTACTS", TA)
    _no_retrieval()
    r = student.post("/api/ask", json={"question": "Can we reschedule our presentation?", "course": "45884"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kind"] == "faq" and body["faq_id"] == "reschedule_presentation" and body["covered"] is False
    assert body["segments"] == [] and "TA" in body["message"]
    assert body["contacts"] == [{"course": "45884", "course_label": "45-884", "name": "Pat Example",
                                 "email": "ta-two@example.edu"}]
    assert "Pat Example" not in body["message"]  # names are only on the card, never in the answer text

    r = student.post("/api/ask", json={"question": "Can we meet on Friday?"})
    body = r.json()
    assert body["faq_id"] == "meeting" and body["links"][0]["url"] == "https://calendly.com/bencollierphd"
    assert body["contacts"] == []
