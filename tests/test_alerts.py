"""Instructor alerts (docs/SPEC.md, "Instructor alerts"): detection, course resolution, guards, the text, Twilio,
the student's reply, and Settings > Student alerts.

Every model here is a TEST FAKE and Twilio is a mock transport: no test reaches a real
Twilio account or sends a real text (conftest.py also clears every TWILIO_* variable).
Canvas titles are invented in the shape of the real index.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from test_api import TEST_FAKE_embedder, TEST_FAKE_llm, TEST_FAKE_rank, TEST_FAKE_select

# app.main first: it wires the routers that the admin modules import from.
from app.main import Retriever, app, get_completer, get_embedder, get_retriever

# isort: split
from app import alerts, analytics, limits, pricing, prompts, settings_store, storage, usage  # noqa: E402,I001

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 8, 18, 30, tzinfo=UTC)
SID = "AC" + "0" * 32
TOKEN = "test-auth-token-not-real"
TO = "+14125550123"
FROM = "+14125550199"

ITEMS = [
    {"course": "70445", "title": "Quiz 4: How Large Language Models Work", "kind": "assignment",
     "canvas_url": "https://canvas.cmu.edu/courses/1/assignments/4"},
    {"course": "70445", "title": "Quiz 3: Machine Learning Fundamentals and Neural Networks", "kind": "assignment",
     "canvas_url": "https://canvas.cmu.edu/courses/1/assignments/3"},
    {"course": "70445", "title": "Homework 3 - Build and Ship", "kind": "assignment",
     "canvas_url": "https://canvas.cmu.edu/courses/1/assignments/30"},
    {"course": "70445", "title": "AI Agents with n8n Exercise", "kind": "assignment",
     "canvas_url": "https://canvas.cmu.edu/courses/1/assignments/40"},
    {"course": "45884", "title": "Quiz 4: Computer Vision Fundamentals", "kind": "assignment",
     "canvas_url": "https://canvas.cmu.edu/courses/2/assignments/4"},
    {"course": "45884", "title": "Lab 3: Text Mining for Business Insight", "kind": "assignment",
     "canvas_url": "https://canvas.cmu.edu/courses/2/assignments/33"},
    {"course": "45884", "title": "Lab 3: Text Mining for Business Insight", "kind": "assignment",  # a second chunk
     "canvas_url": "https://canvas.cmu.edu/courses/2/assignments/33"},
    {"course": "45884", "title": "Week 6 announcement about labs", "kind": "announcement",
     "canvas_url": "https://canvas.cmu.edu/courses/2/announcements/6"},
]
CONTENT = SimpleNamespace(info_records=ITEMS)


def classifier_reply(incident=True, type_="quiz", course=None, item=None, confidence=0.9) -> str:
    return json.dumps({"incident": incident, "type": type_, "course": course, "item": item,
                       "confidence": confidence})


class FakeModel:
    """TEST FAKE completer: answers the incident classifier with `reply`, everything else like test_api."""

    def __init__(self, reply: str | Exception = "") -> None:
        self.reply = reply
        self.incident_calls: list[str] = []
        self.other_calls = 0

    def __call__(self, system, user, max_tokens, provider=None, model=None):
        if system == prompts.get(alerts.PROMPT_NAME):
            self.incident_calls.append(user)
            if isinstance(self.reply, Exception):
                raise self.reply
            return self.reply
        self.other_calls += 1
        return TEST_FAKE_llm(system, user, max_tokens, provider, model)


def twilio_env(monkeypatch, sender: str = FROM) -> None:
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", SID)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.setenv("TWILIO_FROM", sender)
    monkeypatch.setenv("ALERT_TO_PHONE", TO)


class FakeTwilio:
    """A mock Twilio Messages endpoint. Records every request; never touches the network."""

    def __init__(self, status: int = 201, body: dict | None = None, error: Exception | None = None) -> None:
        self.status, self.body, self.error = status, body, error
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.error:
            raise self.error
        body = self.body if self.body is not None else {"sid": "SM" + "1" * 32, "status": "queued"}
        return httpx.Response(self.status, json=body)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def form(self, i: int = -1) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.requests[i].content.decode()).items()}


@pytest.fixture
def twilio(monkeypatch):
    fake = FakeTwilio()
    monkeypatch.setattr(alerts, "_http", fake.client)
    return fake


def detection(type_="quiz", course="70445", item="Quiz 4: How Large Language Models Work",
              url="https://canvas.cmu.edu/courses/1/assignments/4", strong=True) -> alerts.Detection:
    return alerts.Detection(
        True, type_, alerts.KeywordHit(type_, "quiz is broken", strong),
        alerts.Classified(True, type_, course, item, 0.9), "llm",
        alerts.Resolution(course, "filter", item, url, 3.5),
    )


# ---------------------------------------------------------------- 1. detection: keyword pre-check

@pytest.mark.parametrize("question, kind, strong", [
    ("my api key is out of money", "api_credits", True),
    ("The OpenAI key you gave us says insufficient_quota", "api_credits", True),
    ("I ran out of credits on the Anthropic API key", "api_credits", True),
    ("You exceeded your current quota error from the API in lab 2", "api_credits", True),
    ("my api key stopped working", "api_credits", False),
    ("getting a 429 error from openai in lab 2", "api_credits", False),
    ("I cannot submit lab 3", "submission", True),
    ("the submission page for homework 2 is broken", "submission", True),
    ("Gradescope gives an error when I upload", "submission", True),
    ("The upload keeps failing on Canvas for my notebook", "submission", True),
    ("I can't submit it late, right?", "submission", False),  # sounds like late work, not a broken page
    ("quiz 4 won’t load", "quiz", True),
    ("my quiz is broken", "quiz", True),
    ("the access code for the quiz is not working", "quiz", True),
    ("Can't open today's quiz", "quiz", True),
    ("the quiz is locked even though it should be open", "quiz", False),
    ("the quiz timer is wrong", "quiz", False),
])
def test_keyword_positives(question, kind, strong):
    hit = alerts.keyword_hit(question)
    assert hit is not None, question
    assert (hit.type, hit.strong) == (kind, strong)


@pytest.mark.parametrize("question", [
    "what is an API key",
    "What is an API key and why do I need one?",
    "how do API keys work?",
    "What does insufficient_quota mean?",
    "explain rate limits",
    "What is a quiz access code?",
    "the startup in the case ran out of money",  # no API context
    "can I submit late?",
    "how do I submit the homework",
    "I got question 3 on quiz 2 wrong about type I errors",
    "I'm stuck on quiz 2 question 3",
    "How does k-means decide which cluster a point belongs to?",
    "When are your office hours?",
    "",
])
def test_keyword_negatives(question):
    assert alerts.keyword_hit(question) is None, question


def test_concept_questions_about_apis_make_no_model_call():
    model = FakeModel(classifier_reply(True, "api_credits", confidence=0.99))
    for q in ("what is an API key", "how do API keys work?", "Explain what a 429 rate limit is"):
        det = alerts.detect(q, None, CONTENT, model)
        assert det.incident is False and det.keyword is None
    assert model.incident_calls == []


def test_faq_answers_participation_points_first():
    model = FakeModel(classifier_reply())
    question = "My participation point quiz is broken on Canvas"
    assert alerts.keyword_hit(question) is not None
    det = alerts.detect(question, "70445", CONTENT, model)
    assert det.incident is False and model.incident_calls == []


# ---------------------------------------------------------------- 1. detection: classifier and decision

@pytest.mark.parametrize("strong, classified, expected", [
    (True, None, True),  # classifier failed: a strong keyword hit still alerts
    (False, None, False),  # weak hit, no classifier: quiet
    (False, alerts.Classified(True, "quiz", None, None, 0.6), True),
    (False, alerts.Classified(True, "quiz", None, None, 0.59), False),  # below the confidence bar
    (True, alerts.Classified(True, "quiz", None, None, 0.3), True),  # unsure classifier, strong hit
    (True, alerts.Classified(False, None, None, None, 0.9), False),  # confident "not an incident" wins
])
def test_decide(strong, classified, expected):
    assert alerts.decide(alerts.KeywordHit("quiz", "x", strong), classified) is expected


def test_classifier_prompt_sends_titles_as_data_and_parses_reply():
    model = FakeModel("```json\n" + classifier_reply(True, "quiz", "70445", None, 0.8) + "\n```")
    det = alerts.detect("quiz 4 won't load", None, CONTENT, model)
    assert det.incident and det.type == "quiz" and det.classifier_source == "llm"
    sent = json.loads(model.incident_calls[0].split("\n", 1)[1])
    assert sent["message"] == "quiz 4 won't load" and sent["course_filter"] is None
    assert "Quiz 4: How Large Language Models Work" in sent["assignment_titles"]["70445"]
    assert "Week 6 announcement about labs" not in sent["assignment_titles"]["45884"]
    # Both courses have a "Quiz 4": the titles tie, so the classifier's course decides.
    assert det.resolution.course == "70445" and det.resolution.source == "classifier"
    assert det.resolution.item == "Quiz 4: How Large Language Models Work"
    # The classifier's item counts as words too, so it can settle the tie on its own.
    named = alerts.detect("quiz 4 won't load", None, CONTENT,
                          FakeModel(classifier_reply(True, "quiz", None, "Quiz 4: Computer Vision Fundamentals", 0.8)))
    assert (named.resolution.course, named.resolution.source) == ("45884", "title")


@pytest.mark.parametrize("raw", ["not json", json.dumps({"incident": "yes"}), json.dumps([1, 2])])
def test_bad_classifier_reply_falls_back_to_the_keyword(raw):
    strong = alerts.detect("my quiz is broken", "70445", CONTENT, FakeModel(raw))
    assert strong.incident and strong.classifier_source == "error" and strong.type == "quiz"
    weak = alerts.detect("the quiz timer is wrong", "70445", CONTENT, FakeModel(raw))
    assert weak.incident is False


def test_classifier_reply_is_sanitized():
    c = alerts.parse_classifier(json.dumps({"incident": True, "type": "rm -rf", "course": "99999",
                                            "item": "  Quiz\n4  ", "confidence": 7}))
    assert c.type is None and c.course is None and c.item == "Quiz 4" and c.confidence == 1.0


def test_detection_failure_never_blocks_an_answer(monkeypatch):
    monkeypatch.setattr(alerts, "detect", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert alerts.check("my quiz is broken", None, CONTENT, FakeModel(), visitor="v1") is None


# ---------------------------------------------------------------- 1. which course and which item

def test_course_filter_wins_and_finds_the_item():
    r = alerts.resolve_course("quiz 4 won't load", "45884", CONTENT)
    assert (r.course, r.source, r.item) == ("45884", "filter", "Quiz 4: Computer Vision Fundamentals")
    assert r.url == "https://canvas.cmu.edu/courses/2/assignments/4" and r.score == 3.5


def test_titles_pick_the_course():
    r = alerts.resolve_course("I cannot submit lab 3", None, CONTENT)
    assert (r.course, r.source, r.item) == ("45884", "title", "Lab 3: Text Mining for Business Insight")
    r = alerts.resolve_course("the n8n exercise submission is broken", None, CONTENT)
    assert (r.course, r.item) == ("70445", "AI Agents with n8n Exercise")
    r = alerts.resolve_course("Homework 3 build and ship upload failing", None, CONTENT)
    assert r.item == "Homework 3 - Build and Ship" and r.score >= 5


def test_a_tie_between_courses_settles_nothing_without_the_classifier():
    assert alerts.resolve_course("quiz 4 won't load", None, CONTENT) == alerts.Resolution()
    cls = alerts.Classified(True, "quiz", "45884", None, 0.9)
    r = alerts.resolve_course("quiz 4 won't load", None, CONTENT, cls)
    assert (r.course, r.source, r.item) == ("45884", "classifier", "Quiz 4: Computer Vision Fundamentals")


def test_no_match_and_no_classifier_course_is_unclear():
    r = alerts.resolve_course("my api key is out of money", None, CONTENT, alerts.Classified(True, None, None, None, 1))
    assert r.course is None and r.item is None


def test_numbers_must_agree():
    assert alerts.title_score("quiz 4 is broken", "Quiz 4: How Large Language Models Work") > \
        alerts.title_score("quiz 4 is broken", "Quiz 3: Machine Learning Fundamentals and Neural Networks")
    assert alerts.title_score("quiz 4", "Quiz 3: Neural Networks") < alerts.MATCH_MIN
    r = alerts.resolve_course("the quiz on neural networks is broken", "70445", CONTENT)
    assert r.item == "Quiz 3: Machine Learning Fundamentals and Neural Networks"


def test_announcements_are_never_the_item():
    r = alerts.resolve_course("week 6 labs announcement broken", "45884", CONTENT)
    assert r.item != "Week 6 announcement about labs"


# ---------------------------------------------------------------- 2. the text

FAKE_KEY = "sk" + "-proj-" + "ABCDEFGHIJKLMNOPQRST123"  # invented, built at runtime so no scanner mistakes it
LONG = ("Hi Prof Collier, my name is Jordan Smith and quiz 4 won't load for me. My email is jsmith@andrew.cmu.edu, "
        f"call me at 412-555-0188, student id 123456789. Key {FAKE_KEY} fails too, code X7K2QP9. "
        "See https://example.com/screenshot — thanks!")


def test_scrub_removes_names_ids_keys_codes_and_links():
    out = alerts.scrub(LONG)
    for secret in ("Jordan", "Smith", "jsmith", "412-555", "123456789", "sk-proj", "ABCDEFGHIJ", "X7K2QP9",
                   "example.com", "—"):
        assert secret not in out, secret
    for token in ("[name]", "[email]", "[number]", "[key]", "[code]", "[link]"):
        assert token in out, token
    assert all(32 <= ord(ch) < 127 for ch in out)


@pytest.mark.parametrize("course, item, url, reports", [
    ("70445", "Quiz 4: How Large Language Models Work", "https://canvas.cmu.edu/courses/1/assignments/4", 1),
    ("45884", "Lab 3: Text Mining for Business Insight " * 4, "https://canvas.cmu.edu/courses/2/assignments/33", 12),
    (None, None, None, 1),
])
def test_text_is_short_plain_and_has_no_ids(course, item, url, reports):
    body = alerts.compose(course, "quiz", item, url, alerts.scrub(LONG * 3), reports, NOW)
    assert len(body) <= alerts.MAX_SMS_CHARS
    assert body.startswith("Faculty Twin: simple problem to fix ")
    assert all(32 <= ord(ch) < 127 for ch in body) and "—" not in body
    assert alerts.segments(body) <= 2
    for secret in ("Jordan", "Smith", "jsmith", "123456789", "sk-proj", "visitor"):
        assert secret not in body
    assert "Oct 8 2:30 PM ET" in body  # 18:30 UTC is 2:30 PM in Pittsburgh in October
    if course:
        assert ("70-445" if course == "70445" else "45-884") in body and (url in body)
    else:
        assert "(course unclear)" in body
    assert (f"{reports} reports" in body) == (reports > 1)


def test_text_matches_bens_example_shape():
    body = alerts.compose("70445", "quiz", "Quiz Week 06", "https://canvas.cmu.edu/courses/1/assignments/6",
                          "the quiz won't load after I click start", 1, NOW)
    assert body == ("Faculty Twin: simple problem to fix in 70-445 (quiz): 'Quiz Week 06' may be broken. "
                    "Student said: 'the quiz won't load after I click start'. "
                    "https://canvas.cmu.edu/courses/1/assignments/6 Oct 8 2:30 PM ET")


def test_text_for_each_type():
    for kind, phrase in (("api_credits", "an API key may be out of credits"), ("submission", "submissions may be broken"),
                         ("other_course_tech", "a course tool may have a tech problem")):
        assert phrase in alerts.compose("45884", kind, None, None, "x", 1, NOW)


# ---------------------------------------------------------------- 2. Twilio request shape (mocked)

def test_twilio_request_shape(monkeypatch):
    twilio_env(monkeypatch)
    fake = FakeTwilio()
    result = alerts.send_sms("hello", fake.client())
    assert result.status == "sent" and result.sid.startswith("SM") and result.twilio_status == "queued"
    req = fake.requests[0]
    assert req.method == "POST"
    assert str(req.url) == f"https://api.twilio.com/2010-04-01/Accounts/{SID}/Messages.json"
    assert req.headers["authorization"] == "Basic " + base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
    assert req.headers["content-type"] == "application/x-www-form-urlencoded"
    assert fake.form() == {"To": TO, "From": FROM, "Body": "hello"}


def test_twilio_messaging_service_sender(monkeypatch):
    twilio_env(monkeypatch, sender="MG" + "a" * 32)
    fake = FakeTwilio()
    alerts.send_sms("hi", fake.client())
    assert fake.form() == {"To": TO, "MessagingServiceSid": "MG" + "a" * 32, "Body": "hi"}


@pytest.mark.parametrize("env, problem", [
    ({}, "TWILIO_ACCOUNT_SID"),
    ({"ALERT_TO_PHONE": "412-555-0123"}, "ALERT_TO_PHONE must be an E.164"),
    ({"TWILIO_FROM": "Faculty Twin"}, "TWILIO_FROM must be"),
])
def test_twilio_not_configured_never_calls(monkeypatch, env, problem):
    if env:
        twilio_env(monkeypatch)
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    fake = FakeTwilio()
    result = alerts.send_sms("hi", fake.client())
    assert result.status == "not_configured" and problem in result.error and fake.requests == []


def test_twilio_errors_keep_the_code_and_never_the_token(monkeypatch):
    twilio_env(monkeypatch)
    fake = FakeTwilio(400, {"code": 21608, "message": f"The number is unverified. Trial accounts ... {TOKEN}"})
    result = alerts.send_sms("hi", fake.client())
    assert result.status == "failed" and result.error_code == 21608 and TOKEN not in result.error
    down = alerts.send_sms("hi", FakeTwilio(error=httpx.ConnectTimeout("slow")).client())
    assert down.status == "failed" and "ConnectTimeout" in down.error


# ---------------------------------------------------------------- 3. guards, dedupe, storage

def test_not_configured_is_stored_and_flagged():
    out = alerts.raise_alert(detection(), "my quiz 4 is broken", "v1", NOW)
    assert out.flagged and out.status == "not_configured"
    stored = alerts.recent()
    assert len(stored) == 1 and stored[0]["status"] == "not_configured" and stored[0]["kind"] == "alert"
    assert stored[0]["message"].startswith("Faculty Twin:") and "v1" not in json.dumps(stored[0])


def test_sent_alert_records_sms_usage(monkeypatch, twilio):
    twilio_env(monkeypatch)
    out = alerts.raise_alert(detection(), "my quiz 4 is broken", "v1", NOW)
    assert out.flagged and out.status == "sent" and len(twilio.requests) == 1
    assert "70-445 (quiz)" in twilio.form()["Body"]
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    assert limits.read_counter(usage.sms_key(day, "messages")) == 1
    assert limits.read_counter(usage.sms_key(day, "segments")) == out.record["segments"]
    assert limits.read_counter(alerts.sent_key(NOW.strftime("%Y-%m-%d"))) == 1


def test_dedupe_counts_repeats_into_the_next_text(monkeypatch, twilio):
    twilio_env(monkeypatch)
    first = alerts.raise_alert(detection(), "quiz broken", "v1", NOW)
    second = alerts.raise_alert(detection(), "quiz broken too", "v2", NOW + timedelta(minutes=30))
    third = alerts.raise_alert(detection(), "same here", "v3", NOW + timedelta(minutes=119))
    assert first.status == "sent" and second.status == "repeat" and third.status == "repeat"
    assert second.flagged and third.flagged and len(twilio.requests) == 1  # one text for three reports
    listed = alerts.with_counts(alerts.recent())
    assert len(listed) == 1 and listed[0]["repeats"] == 2
    later = alerts.raise_alert(detection(), "still broken", "v4", NOW + timedelta(hours=2, minutes=1))
    assert later.status == "sent" and later.record["reports"] == 3 and len(twilio.requests) == 2
    assert "(quiz, 3 reports)" in twilio.form()["Body"]


def test_a_different_item_or_course_is_not_a_repeat(monkeypatch, twilio):
    twilio_env(monkeypatch)
    alerts.raise_alert(detection(), "q", "v1", NOW)
    other = alerts.raise_alert(detection(item="Quiz 3: x"), "q", "v2", NOW)
    other_course = alerts.raise_alert(detection(course="45884"), "q", "v3", NOW)
    assert other.status == other_course.status == "sent" and len(twilio.requests) == 3


def test_daily_cap(monkeypatch, twilio):
    twilio_env(monkeypatch)
    monkeypatch.setenv("ALERT_DAILY_CAP", "2")
    results = [alerts.raise_alert(detection(item=f"Quiz {i}"), "q", f"v{i}", NOW) for i in range(4)]
    assert [r.status for r in results] == ["sent", "sent", "over_cap", "over_cap"]
    assert [r.flagged for r in results] == [True, True, False, False]
    assert len(twilio.requests) == 2
    assert [r["status"] for r in alerts.recent()].count("over_cap") == 2  # stored for Settings, not texted
    settings_store.put({"alert_daily_cap": 0})
    assert alerts.raise_alert(detection(item="Quiz 9"), "q", "v9", NOW).status == "over_cap"


def test_one_alert_per_visitor_per_day(monkeypatch, twilio):
    twilio_env(monkeypatch)
    assert alerts.raise_alert(detection(), "q", "v1", NOW).status == "sent"
    again = alerts.raise_alert(detection(item="Quiz 3: x"), "q", "v1", NOW + timedelta(minutes=5))
    assert again.status == "visitor_limit" and not again.flagged and len(alerts.recent()) == 1
    tomorrow = alerts.raise_alert(detection(item="Quiz 3: x"), "q", "v1", NOW + timedelta(days=1))
    assert tomorrow.status == "sent"


def test_switch_off_and_dry_runs_store_and_send_nothing(monkeypatch, twilio):
    twilio_env(monkeypatch)
    assert alerts.raise_alert(detection(), "q", None, NOW).status == "dry_run"
    settings_store.put({"alerts_enabled": False})
    off = alerts.raise_alert(detection(), "q", "v1", NOW)
    assert off.status == "disabled" and not off.flagged
    assert alerts.recent() == [] and twilio.requests == []


def test_failed_text_is_stored_and_still_flagged(monkeypatch):
    twilio_env(monkeypatch)
    fake = FakeTwilio(500, {"code": 20500, "message": "Internal error"})
    monkeypatch.setattr(alerts, "_http", fake.client)
    out = alerts.raise_alert(detection(), "q", "v1", NOW)
    assert out.flagged and out.status == "failed"
    assert alerts.recent()[0]["twilio"]["error_code"] == 20500


def test_storage_failure_is_not_flagged_unless_the_text_went_out(monkeypatch):
    monkeypatch.setattr(alerts, "save", lambda record: False)
    out = alerts.raise_alert(detection(), "q", "v1", NOW)
    assert not out.flagged and out.status == "store_failed"


# ---------------------------------------------------------------- 4. the student's reply

def _ask_setup(model: FakeModel) -> None:
    app.dependency_overrides[get_retriever] = lambda: Retriever(TEST_FAKE_rank, TEST_FAKE_select, 0.5)
    app.dependency_overrides[get_embedder] = lambda: TEST_FAKE_embedder
    app.dependency_overrides[get_completer] = lambda: model
    storage.store.get().info_records = list(ITEMS)  # the Canvas titles, without an info matrix


def test_ask_flags_and_stops(student):
    model = FakeModel(classifier_reply(True, "quiz", None, None, 0.9))
    _ask_setup(model)
    r = student.post("/api/ask", json={"question": "my quiz 4 won't load", "course": "70445"})
    body = r.json()
    assert r.status_code == 200 and body["kind"] == "alert" and body["flagged"] is True
    assert body["title"] == "Thanks, I've flagged this for Prof. Collier."
    assert body["segments"] == [] and body["covered"] is False and body["contacts"] == []
    assert body["links"] == [{"label": "Quiz 4: How Large Language Models Work",
                              "url": "https://canvas.cmu.edu/courses/1/assignments/4"}]
    assert model.other_calls == 0  # no narration, no logistics check
    row = limits._mem_log[-1]
    assert row["kind"] == "alert" and row["covered"] is False and row["provider"] == "anthropic"
    stored = alerts.recent()[0]
    assert stored["status"] == "not_configured" and stored["course"] == "70445" and stored["course_source"] == "filter"
    assert "—" not in body["title"] + body["message"]


def test_ask_reply_says_email_when_nothing_was_sent(student, monkeypatch):
    monkeypatch.setenv("TA_CONTACTS", json.dumps({"70445": {"name": "Test TA", "email": "ta@example.edu"}}))
    model = FakeModel(classifier_reply(True, "quiz", None, None, 0.9))
    _ask_setup(model)
    settings_store.put({"alerts_enabled": False})
    body = student.post("/api/ask", json={"question": "my quiz 4 won't load", "course": "70445"}).json()
    assert body["kind"] == "alert" and body["flagged"] is False
    assert body["title"] == "Please email Prof. Collier or the TA."
    assert body["contacts"] and body["contacts"][0]["email"] == "ta@example.edu"
    assert body["links"][0]["url"].endswith("/assignments/4")
    assert alerts.recent() == []


def test_ask_second_report_from_the_same_visitor_says_email(student):
    _ask_setup(FakeModel(classifier_reply(True, "quiz", None, None, 0.9)))
    first = student.post("/api/ask", json={"question": "my quiz 4 won't load", "course": "70445"}).json()
    second = student.post("/api/ask", json={"question": "quiz 3 is broken too", "course": "70445"}).json()
    assert first["flagged"] is True and second["flagged"] is False


def test_ask_test_traffic_never_alerts(student, monkeypatch, twilio):
    twilio_env(monkeypatch)
    _ask_setup(FakeModel(classifier_reply(True, "quiz", None, None, 0.9)))
    r = student.post("/api/ask", json={"question": "my quiz 4 won't load", "course": "70445"},
                     headers={"X-FT-Source": "eval"})
    assert r.json()["flagged"] is False and twilio.requests == [] and alerts.recent() == []


def test_ask_concept_question_answers_normally(student):
    model = FakeModel(classifier_reply(True, "api_credits", None, None, 0.99))
    _ask_setup(model)
    body = student.post("/api/ask", json={"question": "Tell me about fruit"}).json()
    assert body.get("kind") != "alert" and model.incident_calls == []
    body = student.post("/api/ask", json={"question": "what is an API key"}).json()
    assert body.get("kind") != "alert" and model.incident_calls == [] and alerts.recent() == []


def test_ask_confident_no_from_the_classifier_answers_normally(student):
    model = FakeModel(classifier_reply(False, "quiz", None, None, 0.95))
    _ask_setup(model)
    body = student.post("/api/ask", json={"question": "Tell me about fruit, my quiz is broken"}).json()
    assert body.get("kind") != "alert" and len(model.incident_calls) == 1


# ---------------------------------------------------------------- 5. Settings > Student alerts

def test_admin_routes_need_the_admin_cookie(student):
    assert student.get("/api/admin/alerts").status_code == 401
    assert student.put("/api/admin/alerts", json={"enabled": False}).status_code == 401
    assert student.post("/api/admin/alerts/test").status_code == 401


def test_admin_writes_refuse_cross_site_requests(admin):
    evil = {"Origin": "https://evil.example"}
    assert admin.put("/api/admin/alerts", json={"enabled": False}, headers=evil).status_code == 403
    assert admin.post("/api/admin/alerts/test", headers=evil).status_code == 403
    assert alerts.enabled() is True


def test_admin_view_masks_the_destination_and_hides_the_token(admin, monkeypatch, twilio):
    twilio_env(monkeypatch)
    alerts.raise_alert(detection(), "quiz broken", "v1", NOW)  # "sent" to the mock
    body = admin.get("/api/admin/alerts").json()
    text = json.dumps(body)
    assert body["to_masked"] == "***-***-0123" and TO not in text and TOKEN not in text and SID not in text
    assert body["configured"] == {k: True for k in alerts.ENV_VARS} and body["ready"] is True
    assert body["enabled"] is True and body["daily_cap"] == 10 and body["from_kind"] == "number"
    assert body["alerts"][0]["item"] == "Quiz 4: How Large Language Models Work"
    assert "signature" not in body["alerts"][0]
    status = admin.get("/api/admin/status").json()["keys"]
    assert status["TWILIO_AUTH_TOKEN"] is True and status["ALERT_TO_PHONE"] is True


def test_admin_switch_and_cap(admin):
    body = admin.put("/api/admin/alerts", json={"enabled": False, "daily_cap": 3}).json()
    assert body["enabled"] is False and body["daily_cap"] == 3
    assert admin.put("/api/admin/alerts", json={"daily_cap": 51}).status_code == 400
    assert admin.put("/api/admin/alerts", json={}).status_code == 400


def test_admin_test_text(admin, monkeypatch, twilio):
    assert admin.post("/api/admin/alerts/test").status_code == 400  # not configured
    twilio_env(monkeypatch)
    r = admin.post("/api/admin/alerts/test")
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert twilio.form()["Body"].startswith("Faculty Twin: test text from Settings.")
    for _ in range(alerts.MAX_TESTS_PER_DAY - 1):
        assert admin.post("/api/admin/alerts/test").status_code == 200
    assert admin.post("/api/admin/alerts/test").status_code == 429
    listed = admin.get("/api/admin/alerts").json()
    assert listed["tests_today"] == alerts.MAX_TESTS_PER_DAY and listed["alerts"][0]["type"] == "test"


def test_admin_test_text_failure_is_reported(admin, monkeypatch):
    twilio_env(monkeypatch)
    monkeypatch.setattr(alerts, "_http", FakeTwilio(401, {"code": 20003, "message": "Authenticate"}).client)
    r = admin.post("/api/admin/alerts/test")
    assert r.status_code == 502 and "20003" in r.json()["detail"] and TOKEN not in r.text


def test_prompt_is_in_the_registry_and_testable(admin, monkeypatch):
    p = prompts.REGISTRY[alerts.PROMPT_NAME]
    assert p.used_by == "app" and p.testable
    assert prompts.check(alerts.PROMPT_NAME, p.default) == p.default
    with pytest.raises(prompts.PromptError):
        prompts.check(alerts.PROMPT_NAME, "Reply yes or no.")
    model = FakeModel(classifier_reply(True, "quiz", "70445", None, 0.9))
    app.dependency_overrides[get_completer] = lambda: model
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    r = admin.post(f"/api/admin/prompts/{alerts.PROMPT_NAME}/test",
                   json={"text": p.default, "question": "my quiz is broken"})
    out = r.json()["output"]
    assert r.status_code == 200 and out["incident"] is True and out["type"] == "quiz" and alerts.recent() == []


# ---------------------------------------------------------------- 5. analytics: classifier purpose and SMS cost

def test_sms_is_a_cost_line():
    assert "incident_classifier" in usage.PURPOSES
    table = pricing.current({})
    per = table["sms"]["per_segment"] + table["sms"]["carrier_fee_per_segment"]
    day = NOW.strftime("%Y-%m-%d")
    counters = {usage.sms_key(day, "messages"): 3, usage.sms_key(day, "segments"): 6}
    assert usage.parse_key(usage.sms_key(day, "segments")) == {"kind": "sms", "day": day, "provider": "twilio",
                                                                "metric": "segments"}
    out = analytics.aggregate([], counters, table, 7, NOW)
    assert out["sms"]["messages"] == 3 and out["sms"]["cost"] == pytest.approx(6 * per)
    assert out["kpis"]["spend_parts"]["sms"] == pytest.approx(6 * per)
    assert out["kpis"]["est_spend_usd"] == pytest.approx(6 * per)
    assert "Twilio SMS" in out["spend"]["series"]


def test_sms_price_is_editable_and_validated():
    clean = pricing.validate({"sms": {"per_segment": 0.01, "carrier_fee_per_segment": 0.002}})
    assert clean["sms"]["per_segment"] == 0.01 and pricing.sms_cost(clean, 2) == pytest.approx(0.024)
    with pytest.raises(pricing.BadPricing):
        pricing.validate({"sms": {"per_segment": -1}})
    old = pricing.current({"llm": [], "embed": [], "tts": {}})  # saved before texts were priced
    assert old["sms"]["source"].startswith("https://www.twilio.com/") and old["sms"]["verify"] is True


# ---------------------------------------------------------------- pages and docs

def test_pages_and_docs():
    app_js = (ROOT / "public" / "app.js").read_text(encoding="utf-8")
    assert "answer.kind === 'alert'" in app_js
    html = (ROOT / "public" / "admin.html").read_text(encoding="utf-8")
    assert 'id="sec-alerts"' in html and 'href="#sec-alerts"' in html and "admin-alerts.js" in html
    js = (ROOT / "public" / "admin-alerts.js").read_text(encoding="utf-8")
    assert "/api/admin/alerts" in js and "—" not in js
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    for name in (*alerts.ENV_VARS, "ALERT_DAILY_CAP"):
        assert f"{name}=" in env
    spec = (ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert "### Instructor alerts" in spec and "alerts/<UTC>.json" in spec
    for copy in (alerts.FLAGGED_TITLE, alerts.EMAIL_TITLE):
        assert "—" not in copy
    for word in ("Mac mini", "laptop"):
        for path in ("app/alerts.py", "app/admin_alerts.py", "public/admin-alerts.js", "docs/SPEC.md"):
            assert word not in (ROOT / path).read_text(encoding="utf-8"), (word, path)
