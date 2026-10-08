"""Instructor alerts: a student reports a broken quiz, a broken submission, or an API key out of credits,
and Ben gets a text on his cell (docs/SPEC.md, "Instructor alerts").

Ben, Oct 8: "if a student mentions an api key out of Money or a submission broken or a
quiz broken text my cell and say there is a simple problem to fix have the agent figure
out which course is the issue". `answer()` calls `check()` first, before the stored-topic
and FAQ checks:

1. A keyword pre-check (no model call) looks for the three problem families. Concept
   questions ("what is an API key") are never hits.
2. On a hit, one small classifier call (prompt `incident_classifier`, app/prompts.py)
   returns `{incident, type, course, item, confidence}`.
3. Alert at confidence >= 0.6, or on a strong keyword hit unless the classifier is
   confident it is not an incident.
4. The course comes from the student's course filter, then the assignment and quiz
   titles in the Canvas info index, then the classifier.
5. Guards: the Settings switch, one alert per visitor per day, a 2 hour dedupe on
   course + type + item (repeats are counted into the next text), a daily cap, and no
   text at all for test traffic. Every alert is stored in the private bucket at
   `alerts/<UTC>.json` whether or not the text went out, so Settings shows it.
6. The text goes out through Twilio's Messages API (plain httpx, basic auth). It
   carries the course, the type, the Canvas item and link, and at most about 120
   characters of the scrubbed question: never a visitor id, an address, or a name.

The student sees "Thanks, I've flagged this for Prof. Collier." only when a text was sent
or the alert was stored for Settings; otherwise "Please email Prof. Collier or the TA."
with the TA card. Nothing here ever blocks a normal answer: any failure in detection
lets the question continue as before.
"""

from __future__ import annotations

import json
import math
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from . import config, course_info, faq, limits, llm, privacy, prompts, settings_store, supa, usage

KIND = "alert"
TYPES = ("api_credits", "submission", "quiz", "other_course_tech")
TYPE_LABELS = {"api_credits": "API key", "submission": "submission", "quiz": "quiz", "other_course_tech": "course tech"}
PROMPT_NAME = "incident_classifier"
MAX_TOKENS = 200
CONFIDENCE_MIN = 0.6
DEDUPE_SECONDS = 2 * 3600
DEFAULT_DAILY_CAP = 10
MAX_DAILY_CAP = 50
MAX_TESTS_PER_DAY = 5
MAX_SMS_CHARS = 300
QUOTE_CHARS = 120
MIN_QUOTE_CHARS = 40
RECENT_SCAN = 40  # newest alert files read for the dedupe check
LIST_LIMIT = 50
PREFIX = "alerts"
TWILIO_URL = "https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
TWILIO_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
ENV_VARS = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM", "ALERT_TO_PHONE")
# Replies to the student.
FLAGGED_TITLE = "Thanks, I've flagged this for Prof. Collier."
EMAIL_TITLE = "Please email Prof. Collier or the TA."
LABEL = "Tech problem"
# Ben's FAQ already answers these, so they never become alerts.
FAQ_FIRST = ("canvas_participation", "late_work")
# Statuses that mean the alert reached Ben (a text, or a record waiting in Settings).
FLAGGED_STATUSES = ("sent", "failed", "not_configured", "repeat")
VERSION_RE = re.compile(r"^[0-9]{8}T[0-9]{6}\.[0-9]{6}Z$")
_E164 = re.compile(r"^\+[1-9][0-9]{6,14}$")


# ---------------------------------------------------------------- keyword pre-check

def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(f"(?:{p})" for p in parts), re.I)


_NOT_WORKING = (r"not working|doesn'?t work|does not work|isn'?t working|is not working|stopped working|won'?t work|"
                r"didn'?t work|broken|fail(?:s|ed|ing)?|errors?|erroring")
# An API problem needs some API context: "the company ran out of money" is a case study, not a key.
_API_CONTEXT = _rx(r"\bapi\b", r"\bkeys?\b", r"\bopenai\b", r"\banthropic\b", r"\bclaude\b", r"\bgemini\b",
                   r"\bopenrouter\b", r"\bgpt\b", r"\bllm\b", r"\bcolab\b", r"\bn8n\b", r"\bbilling\b")
_QUIZ_CONTEXT = _rx(r"\bquiz(?:zes)?\b", r"\bexam\b", r"\btest\b")

STRONG: dict[str, re.Pattern[str]] = {
    "api_credits": _rx(
        r"\binsufficient[_ ]quota\b",
        r"\bexceeded (?:your|my|our|the) (?:current )?quota\b",
        r"\b(?:out of|ran out of|run out of|running out of|no more|used up(?: all)?(?: my| the| our)?|exhausted"
        r"(?: my| the| our)?|no) (?:api |openai |anthropic |claude |gemini |openrouter )?(?:credits?|quota|money|funds|"
        r"balance)\b",
        r"\b(?:credits?|quota|balance|money|funds)\b.{0,30}\b(?:ran out|run out|used up|exhausted|exceeded|empty|"
        r"is (?:at )?zero|gone|depleted)\b",
    ),
    "submission": _rx(
        r"\b(?:can'?t|cannot|can not|unable to|won'?t let me|not able to|couldn'?t|could not) (?:submit|upload|turn in|"
        r"hand in)\b",
        r"\b(?:submission|submit button|submit page|upload)s?\b.{0,40}\b(?:broken|error\w*|fail\w*|not working|"
        r"doesn'?t work|isn'?t working|won'?t (?:go through|work|upload|submit|load)|stuck|gr[ae]yed out|disabled|"
        r"missing|crash\w*)\b",
        r"\bgradescope\b.{0,50}\b(?:error\w*|broken|fail\w*|not working|won'?t|can'?t|down)\b",
        r"\b(?:error\w*|broken|down)\b.{0,30}\bgradescope\b",
    ),
    "quiz": _rx(
        r"\bquiz(?:zes)?\b.{0,50}\b(?:broken|won'?t (?:load|open|start|submit|work|let me|accept)|not loading|"
        r"isn'?t loading|doesn'?t load|not working|isn'?t working|doesn'?t work|crash\w*|froze|frozen|"
        r"glitch\w*|buggy|not opening|"
        r"(?:gives?|gave|got|getting|shows?|showing|throws?|threw) (?:me )?(?:an? )?error)\b",
        r"\b(?:can'?t|cannot|can not|unable to|couldn'?t|could not) (?:open|start|access|take|load|submit|see|find) "
        r"(?:the |my |this |today'?s |our |a )?(?:\w+ )?quiz\b",
        r"\b(?:access|quiz) code\b.{0,40}\b(?:not working|doesn'?t work|isn'?t working|didn'?t work|invalid|wrong|"
        r"incorrect|rejected|won'?t work|not accepted)\b",
    ),
}
WEAK: dict[str, re.Pattern[str]] = {
    "api_credits": _rx(
        r"\b(?:api|openai|anthropic|claude|gemini|openrouter) key\b.{0,40}\b(?:" + _NOT_WORKING + r"|invalid|rejected|"
        r"expired)\b",
        r"\b(?:invalid|expired|rejected)\b.{0,20}\bapi key\b",
        r"\b(?:429|rate[- ]limit(?:ed)?|quota exceeded)\b",
        r"\bbilling\b.{0,30}\b(?:error|issue|problem)\b",
    ),
    "submission": _rx(
        r"\bsubmission\b.{0,40}\b(?:issue|problem|trouble)\b",
        r"\b(?:issue|problem|trouble)\b.{0,30}\b(?:submitting|uploading|submission)\b",
    ),
    "quiz": _rx(
        r"\bquiz(?:zes)?\b.{0,40}\b(?:locked|closed|unavailable|not available|disappeared|missing|stuck|bug|error)\b",
        r"\btimer\b.{0,40}\b(?:wrong|broken|ran out|expired|reset|glitch\w*|bug\w*|jumped|skipped|not right)\b",
        r"\b(?:wrong|broken|glitch\w*)\b.{0,30}\btimer\b",
    ),
}
_LATE = _rx(r"\blate\b", r"\bextension\b", r"\bafter the deadline\b", r"\bpast the deadline\b")
_CONCEPT_START = re.compile(
    r"^\s*(?:what|what'?s|whats|how (?:do|does|is|are|can|should|would|to)|why|when|where|explain|define|describe|"
    r"tell me about|can you explain|could you explain|is it|is there|are there|which)\b",
    re.I,
)
_PROBLEM_WORDS = _rx(
    r"\b(?:my|mine|i|i'm|im|i've|ive|me|we|our|us)\b", r"\bbroken\b", r"\bnot working\b", r"\bdoesn'?t work\b",
    r"\bwon'?t\b", r"\bcan'?t\b", r"\bcannot\b", r"\bcouldn'?t\b", r"\berror\b", r"\bfail(?:ing|ed)\b", r"\bstuck\b",
    r"\bran out\b", r"\bout of\b",
)


@dataclass
class KeywordHit:
    """The problem phrase found, and whether it is strong enough to alert without the classifier."""

    type: str
    phrase: str
    strong: bool


def _text(question: str) -> str:
    return re.sub(r"\s+", " ", (question or "").replace("’", "'").replace("‘", "'")).strip()


def is_concept_question(question: str) -> bool:
    """A question about an idea ("what is an API key", "how do rate limits work"), not a report of a problem."""
    text = _text(question)
    return bool(_CONCEPT_START.search(text)) and not _PROBLEM_WORDS.search(text)


def keyword_hit(question: str) -> KeywordHit | None:
    """The first problem phrase in the question (strong before weak), or None. Concept questions never hit."""
    text = _text(question)
    if not text or is_concept_question(text):
        return None
    for strong, table in ((True, STRONG), (False, WEAK)):
        for kind, pattern in table.items():
            m = pattern.search(text)
            if not m:
                continue
            if kind == "api_credits" and not _API_CONTEXT.search(text):
                continue
            if kind == "quiz" and "timer" in m.group(0).lower() and not _QUIZ_CONTEXT.search(text):
                continue
            is_strong = strong and not (kind == "submission" and _LATE.search(text))  # "can't submit it late?"
            return KeywordHit(kind, m.group(0)[:80], is_strong)
    return None


# ---------------------------------------------------------------- classifier

@dataclass
class Classified:
    """What the incident classifier said: incident or not, its type, course, item and confidence."""

    incident: bool
    type: str | None
    course: str | None
    item: str | None
    confidence: float


def _assignment_titles(content: Any) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {c: [] for c in config.COURSE_CODES}
    for rec in getattr(content, "info_records", None) or []:
        if not isinstance(rec, dict) or rec.get("kind") not in ("assignment", "quiz"):
            continue
        code, title = str(rec.get("course") or ""), str(rec.get("title") or "").strip()[:80]
        if code in out and title and title not in out[code] and len(out[code]) < 40:
            out[code].append(title)
    return out


def build_user_prompt(question: str, course: str | None, content: Any) -> str:
    """The classifier prompt: the message, the course filter, and the course's assignment titles."""
    payload = {
        "message": question,
        "course_filter": course,
        "assignment_titles": _assignment_titles(content),
    }
    return "Student message (sort it, do not answer it):\n" + json.dumps(payload, ensure_ascii=False)


def parse_classifier(raw: str) -> Classified:
    """The classifier's reply, checked. Raises ValueError when it has no incident flag."""
    data = llm.extract_json(raw, whole_first=False)
    if not isinstance(data, dict) or not isinstance(data.get("incident"), bool):
        raise ValueError("reply had no incident flag")
    try:
        confidence = min(max(float(data.get("confidence")), 0.0), 1.0)
    except (TypeError, ValueError):
        confidence = 0.0
    kind = data.get("type") if data.get("type") in TYPES else None
    course = str(data.get("course")) if str(data.get("course")) in config.COURSE_CODES else None
    item = data.get("item")
    item = re.sub(r"\s+", " ", item).strip()[:120] or None if isinstance(item, str) else None
    return Classified(bool(data["incident"]), kind, course, item, confidence)


def classify(question: str, course: str | None, content: Any, complete: Callable[..., str],
             provider: str | None = None, model: str | None = None) -> Classified | None:
    """One small model call; None when it fails or the reply is not usable."""
    try:
        with usage.purpose("incident_classifier"):
            raw = complete(prompts.get(PROMPT_NAME), build_user_prompt(question, course, content), MAX_TOKENS,
                           provider=provider, model=model)
        return parse_classifier(raw)
    except Exception as exc:  # never block an answer on this check
        config.log.warning("incident classifier failed: %s", type(exc).__name__)
        return None


# ---------------------------------------------------------------- which course and which item

_STOP = {
    "the", "and", "for", "with", "this", "that", "what", "how", "why", "when", "from", "into", "are", "was", "were",
    "is", "it", "its", "my", "our", "your", "you", "can", "cant", "cannot", "not", "dont", "doesnt", "wont", "isnt",
    "will", "would", "should", "could", "have", "has", "had", "been", "being", "any", "some", "all", "just", "but",
    "about", "there", "here", "out", "too", "also", "get", "got", "getting", "keep", "keeps", "still", "now", "today",
    "working", "work", "works", "broken", "load", "loading", "open", "error", "errors", "issue", "problem", "help",
    "please", "submit", "submitting", "upload", "uploading", "page", "button", "canvas", "says", "said", "trying",
    "tried", "try", "one", "after", "before", "class", "course", "professor", "prof", "collier", "ben", "hi", "hey",
}
_GENERIC = {"quiz", "lab", "homework", "assignment", "exercise", "memo", "project", "exam", "module", "week", "part",
            "check", "discussion", "submission", "participation", "optional", "final", "survey"}
_ALIASES = {"hw": "homework", "assignment": "homework", "wk": "week"}
_NUMBERED = re.compile(
    r"\b(quiz|lab|homework|hw|memo|module|week|wk|part|project|assignment|exercise)\s*#?\s*0*(\d{1,2})\b", re.I)
MATCH_MIN = 1.5


def _tokens(text: str) -> tuple[set[str], set[str]]:
    low = (text or "").lower().replace("’", "'")
    numbered = {f"{_ALIASES.get(k, k)}#{int(n)}" for k, n in _NUMBERED.findall(low)}
    words = {w for w in re.findall(r"[a-z0-9]+", low.replace("'", "")) if not w.isdigit() and len(w) >= 3}
    return numbered, words - _STOP


def title_score(query: str, title: str) -> float:
    """How well a question names a Canvas title: shared words, with "Quiz 4" as one strong token."""
    qn, qw = _tokens(query)
    tn, tw = _tokens(title)
    score = 3.0 * len(qn & tn)
    score += sum(0.5 if w in _GENERIC else 1.0 for w in qw & tw)
    # "quiz 4" against "Quiz 3: ...": the same kind with a different number is the wrong item.
    q_kinds = {n.split("#")[0]: n for n in qn}
    if any(n.split("#")[0] in q_kinds and n not in qn for n in tn):
        score -= 2.0
    return score


@dataclass
class Resolution:
    """Which course and Canvas item an alert is about, and how sure the match is."""

    course: str | None = None
    source: str | None = None  # "filter" | "title" | "classifier" | None
    item: str | None = None
    url: str | None = None
    score: float | None = None


def _items(content: Any) -> list[dict[str, Any]]:
    seen, out = set(), []
    for rec in getattr(content, "info_records", None) or []:
        if not isinstance(rec, dict):
            continue
        code, title = str(rec.get("course") or ""), str(rec.get("title") or "").strip()
        if code not in config.COURSE_CODES or not title or rec.get("kind") in ("announcement", "syllabus"):
            continue
        url = str(rec.get("canvas_url") or "").strip()
        key = (code, title)
        if key in seen:
            continue
        seen.add(key)
        out.append({"course": code, "title": title, "kind": rec.get("kind"),
                    "url": url if url.startswith("https://") else None})
    return out


def _best(query: str, items: list[dict[str, Any]], course: str | None) -> dict[str, tuple[float, dict[str, Any]]]:
    """Best item per course (assignments and quizzes first; other kinds only when none of those match)."""
    best: dict[str, tuple[float, dict[str, Any]]] = {}
    for pool in ([i for i in items if i["kind"] in ("assignment", "quiz")],
                 [i for i in items if i["kind"] not in ("assignment", "quiz")]):
        for it in pool:
            if course and it["course"] != course:
                continue
            s = title_score(query, it["title"])
            if s >= MATCH_MIN and (it["course"] not in best or s > best[it["course"]][0]):
                best[it["course"]] = (s, it)
        if best:
            break
    return best


def resolve_course(question: str, course_filter: str | None, content: Any,
                   classified: Classified | None = None) -> Resolution:
    """The course filter, then Canvas titles (a tie between courses settles nothing), then the classifier."""
    query = question + (" " + classified.item if classified and classified.item else "")
    items = _items(content)

    def found(code: str, source: str, best: dict[str, tuple[float, dict[str, Any]]]) -> Resolution:
        if code in best:
            s, it = best[code]
            return Resolution(code, source, it["title"], it["url"], round(s, 2))
        return Resolution(code, source)

    if course_filter:
        return found(course_filter, "filter", _best(query, items, course_filter))
    best = _best(query, items, None)
    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])
    if ranked and (len(ranked) == 1 or ranked[0][1][0] > ranked[1][1][0]):
        return found(ranked[0][0], "title", best)
    if classified and classified.course:
        return found(classified.course, "classifier", best)
    return Resolution()


# ---------------------------------------------------------------- detection

@dataclass
class Detection:
    """Whether a message reports a broken course tool, and what about."""

    incident: bool
    type: str | None = None
    keyword: KeywordHit | None = None
    classified: Classified | None = None
    classifier_source: str | None = None  # "llm" | "error" | None (not called)
    resolution: Resolution = field(default_factory=Resolution)

    @property
    def confidence(self) -> float | None:
        return self.classified.confidence if self.classified else None

    def public(self) -> dict[str, Any]:
        """The detection as plain JSON, for Settings > Prompts' test of the alert prompt (no question text)."""
        return {
            "incident": self.incident,
            "type": self.type,
            "keyword": asdict(self.keyword) if self.keyword else None,
            "classifier": asdict(self.classified) if self.classified else None,
            "classifier_source": self.classifier_source,
            "course": asdict(self.resolution),
        }


def decide(hit: KeywordHit | None, classified: Classified | None) -> bool:
    """Alert at confidence >= 0.6, or on a strong keyword hit unless the classifier is confident it is not one."""
    if hit is None:
        return False
    if classified is not None and classified.confidence >= CONFIDENCE_MIN:
        return classified.incident
    return hit.strong


def detect(question: str, course: str | None, content: Any, complete: Callable[..., str],
           provider: str | None = None, model: str | None = None) -> Detection:
    """Keyword pre-check, then the classifier: whether this message reports a broken quiz, submission or key."""
    hit = keyword_hit(question)
    if hit is None:
        return Detection(False)
    faq_hit = faq.match(question, course)
    if faq_hit is not None and faq_hit.entry.id in FAQ_FIRST:
        return Detection(False, keyword=hit)
    classified = classify(question, course, content, complete, provider=provider, model=model)
    det = Detection(decide(hit, classified), keyword=hit, classified=classified,
                    classifier_source="llm" if classified else "error")
    if not det.incident:
        return det
    det.type = classified.type if classified and classified.incident and classified.type else hit.type
    det.resolution = resolve_course(question, course, content, classified)
    return det


# ---------------------------------------------------------------- the text

_URL = re.compile(r"\b(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|org|net|edu|io|ai|dev|app)(?:/\S*)?", re.I)
_KEYLIKE = re.compile(r"\b(?:sk|pk|rk|pa|ghp|gho|xox[a-z]|AKIA|AC|SK)[-_A-Za-z0-9]{8,}|"
                      r"\b(?=[A-Za-z0-9_\-]*\d)(?=[A-Za-z0-9_\-]*[A-Za-z])[A-Za-z0-9_\-]{20,}\b")
_ASCII = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                        "…": "...", " ": " "})
_GSM_EXT = set("^{}\\[~]|")


def ascii_text(text: str) -> str:
    out = (text or "").translate(_ASCII)
    return re.sub(r"\s+", " ", "".join(ch if 32 <= ord(ch) < 127 else " " for ch in out)).strip()


def scrub(text: str) -> str:
    """The question as it may appear in a text: privacy scrub, then keys, codes, and links removed."""
    out = privacy.scrub_question(text or "")
    out = _URL.sub("[link]", out)
    out = _KEYLIKE.sub("[key]", out)
    out = course_info._CODE_LIKE.sub("[code]", out)
    return ascii_text(out)


def shorten(text: str, limit: int) -> str:
    """At most `limit` characters, cut at a space in the second half, with "..."."""
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 3, 1)]
    if " " in cut[limit // 2 :]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:") + "..."


def segments(body: str) -> int:
    """SMS segments for a GSM-7 body (extension characters count twice)."""
    n = sum(2 if ch in _GSM_EXT else 1 for ch in body)
    return 1 if n <= 160 else math.ceil(n / 153)


def eastern_time(when: datetime) -> str:
    local = course_info._eastern(when)
    return f"{local.strftime('%b')} {local.day} {local.strftime('%I:%M %p').lstrip('0')} ET"


def _problem(kind: str, item: str | None) -> str:
    if kind == "api_credits":
        return f"an API key may be out of credits ('{item}')" if item else "an API key may be out of credits"
    if kind == "submission":
        return f"'{item}' submissions may be broken" if item else "submissions may be broken"
    if kind == "quiz":
        return f"'{item}' may be broken" if item else "a quiz may be broken"
    return f"'{item}' may have a tech problem" if item else "a course tool may have a tech problem"


def compose(course: str | None, kind: str, item: str | None, url: str | None, quote: str, reports: int,
            when: datetime) -> str:
    """The text Ben gets: at most 300 plain characters, no names and no ids."""
    where = f"in {faq.COURSE_LABELS[course]}" if course in faq.COURSE_LABELS else "(course unclear)"
    count = f", {reports} reports" if reports > 1 else ""
    item = ascii_text(item or "") or None
    url = url if url and url.startswith("https://") and len(url) <= 120 and " " not in url else None
    tail = " ".join(x for x in (url, eastern_time(when)) if x)

    def build(q: str, it: str | None) -> str:
        return (f"Faculty Twin: simple problem to fix {where} ({TYPE_LABELS.get(kind, kind)}{count}): "
                f"{_problem(kind, it)}. Student said: '{q}'. {tail}")

    quote = shorten(quote, QUOTE_CHARS)
    body = build(quote, item)
    if len(body) > MAX_SMS_CHARS:  # shorter quote first, then a shorter item title
        room = QUOTE_CHARS - (len(body) - MAX_SMS_CHARS)
        quote = shorten(quote, max(room, MIN_QUOTE_CHARS))
        body = build(quote, item)
    if len(body) > MAX_SMS_CHARS and item:
        body = build(quote, shorten(item, max(len(item) - (len(body) - MAX_SMS_CHARS), 20)))
    if len(body) > MAX_SMS_CHARS:
        body = shorten(body, MAX_SMS_CHARS)
    return body


def test_message(when: datetime) -> str:
    return f"Faculty Twin: test text from Settings. Student alerts can reach this phone. {eastern_time(when)}"


# ---------------------------------------------------------------- Twilio

@dataclass
class TwilioConfig:
    """The Twilio account, token and sender from the environment."""

    sid: str
    token: str
    sender: str
    to: str

    @property
    def problems(self) -> list[str]:
        """What is missing or malformed in the Twilio settings (empty when ready)."""
        out = []
        if not self.sid or not self.sid.startswith("AC"):
            out.append("TWILIO_ACCOUNT_SID is not set (it starts with AC)")
        if not self.token:
            out.append("TWILIO_AUTH_TOKEN is not set")
        if not (self.sender.startswith("MG") or _E164.match(self.sender)):
            out.append("TWILIO_FROM must be an E.164 number (+15551234567) or a Messaging Service SID (MG...)")
        if not _E164.match(self.to):
            out.append("ALERT_TO_PHONE must be an E.164 number (+15551234567)")
        return out

    @property
    def ready(self) -> bool:
        return not self.problems


def twilio_config() -> TwilioConfig:
    return TwilioConfig(*(str(config.env(name) or "").strip() for name in ENV_VARS))


def masked_destination() -> str | None:
    digits = re.sub(r"\D", "", str(config.env("ALERT_TO_PHONE") or ""))
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else None


@dataclass
class SendResult:
    """What Twilio said about one text."""

    status: str  # "sent" | "failed" | "not_configured"
    sid: str | None = None
    twilio_status: str | None = None
    error_code: Any | None = None
    error: str | None = None


def build_request(cfg: TwilioConfig, body: str) -> tuple[str, dict[str, str]]:
    """The Twilio Messages API request for one text to Ben's phone."""
    data = {"To": cfg.to, "Body": body}
    if cfg.sender.startswith("MG"):
        data["MessagingServiceSid"] = cfg.sender
    else:
        data["From"] = cfg.sender
    return TWILIO_URL.format(sid=cfg.sid), data


def _http() -> httpx.Client:
    """The HTTP client for Twilio (tests swap in a mock transport here)."""
    return httpx.Client(timeout=TWILIO_TIMEOUT)


def send_sms(body: str, client: httpx.Client | None = None) -> SendResult:
    """POST one message to Twilio. Never raises; never returns or logs the auth token."""
    cfg = twilio_config()
    if not cfg.ready:
        return SendResult("not_configured", error="; ".join(cfg.problems)[:300])
    url, data = build_request(cfg, body)
    own = client is None
    client = client or _http()
    try:
        resp = client.post(url, data=data, auth=(cfg.sid, cfg.token))
    except httpx.HTTPError as exc:
        return SendResult("failed", error=f"Twilio request failed: {type(exc).__name__}")
    finally:
        if own:
            client.close()
    try:
        payload = resp.json()
    except ValueError:
        payload = {}
    payload = payload if isinstance(payload, dict) else {}
    if 200 <= resp.status_code < 300 and payload.get("sid"):
        return SendResult("sent", sid=str(payload["sid"])[:64], twilio_status=str(payload.get("status") or "")[:20])
    message = str(payload.get("message") or f"HTTP {resp.status_code}").replace(cfg.token, "[token]")[:200]
    return SendResult("failed", error_code=payload.get("code") or resp.status_code, error=message)


# ---------------------------------------------------------------- settings

def enabled() -> bool:
    return settings_store.get("alerts_enabled", True) is not False


def daily_cap() -> int:
    """Texts allowed per day: the Settings value, else ALERT_DAILY_CAP, within 0 and MAX_DAILY_CAP."""
    raw = settings_store.get("alert_daily_cap")
    if raw is None:
        raw = config.env_int("ALERT_DAILY_CAP", DEFAULT_DAILY_CAP)
    try:
        return min(max(int(raw), 0), MAX_DAILY_CAP)
    except (TypeError, ValueError):
        return DEFAULT_DAILY_CAP


def _day(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y-%m-%d")


def sent_key(day: str) -> str:
    return f"alerts_sent:{day}"


def test_key(day: str) -> str:
    return f"alert_tests:{day}"


def visitor_key(visitor: str, day: str) -> str:
    return f"alert_visitor:{visitor}:{day}"


def take_send(now: datetime | None = None) -> bool:
    """One text from today's cap. Fails closed."""
    cap = daily_cap()
    if cap <= 0:
        return False
    ok, _ = limits.increment(sent_key(_day(now)), 1, cap=cap, fail_open=False)
    return ok


# ---------------------------------------------------------------- storage

_local: list[dict[str, Any]] = []
_local_lock = threading.Lock()


def reset_memory() -> None:
    """Tests: forget in-memory alerts."""
    with _local_lock:
        _local.clear()


def new_id(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%S.%fZ")


def save(record: dict[str, Any]) -> bool:
    """Write one record to `alerts/<id>.json` (memory without Supabase). False when storage failed."""
    if config.supabase_configured():
        try:
            supa.upload(f"{PREFIX}/{record['id']}.json", json.dumps(record, ensure_ascii=False).encode("utf-8"),
                        "application/json")
            return True
        except supa.SupabaseError as exc:
            config.log.warning("alert save failed: %s", exc)
            return False
    with _local_lock:
        _local.append(dict(record))
        del _local[:-500]
    return True


def _read(name: str) -> dict[str, Any] | None:
    try:
        data = json.loads(supa.download(f"{PREFIX}/{name}"))
    except (supa.SupabaseError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def recent(limit: int = LIST_LIMIT) -> list[dict[str, Any]]:
    """Newest records first (alerts, repeats, and test texts)."""
    if not config.supabase_configured():
        with _local_lock:
            return [dict(r) for r in sorted(_local, key=lambda r: r["id"], reverse=True)[:limit]]
    names = [n for n in supa.list_objects(f"{PREFIX}/", limit=limit * 2)
             if n.endswith(".json") and VERSION_RE.match(n[: -len(".json")])]
    names = sorted(names, reverse=True)[:limit]
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(_read, names))
    return [r for r in rows if r]


def _norm_item(item: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (item or "").lower()).strip()


def signature(course: str | None, kind: str | None, item: str | None) -> str:
    return f"{course or ''}|{kind or ''}|{_norm_item(item)}"


def _at(record: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(record.get("at")))
    except ValueError:
        return None


def previous(records: list[dict[str, Any]], sig: str) -> dict[str, Any] | None:
    """The newest alert (not a repeat or test) with this signature."""
    for r in records:
        if r.get("kind") == "alert" and r.get("signature") == sig:
            return r
    return None


def with_counts(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Alerts and test texts, each with `repeats` (later reports folded into it)."""
    repeats: dict[str, int] = {}
    for r in records:
        if r.get("kind") == "repeat" and r.get("of"):
            repeats[r["of"]] = repeats.get(r["of"], 0) + 1
    return [{**r, "repeats": repeats.get(r["id"], 0)} for r in records if r.get("kind") in ("alert", "test")]


# ---------------------------------------------------------------- the whole flow

@dataclass
class Outcome:
    """What happened to one alert: whether it was stored or sent, why, and the record."""

    flagged: bool
    # sent | failed | not_configured | repeat | over_cap | disabled | visitor_limit | dry_run | store_failed
    status: str
    record: dict[str, Any] | None = None


def raise_alert(det: Detection, question: str, visitor: str | None, now: datetime | None = None,
                client: httpx.Client | None = None) -> Outcome:
    """Guards, dedupe, the text, and the record. Only real student traffic (a visitor) can text."""
    now = now or datetime.now(UTC)
    if visitor is None:
        return Outcome(False, "dry_run")
    if not enabled():
        return Outcome(False, "disabled")
    day = _day(now)
    ok, _ = limits.increment(visitor_key(visitor, day), 1, cap=1, ttl_seconds=172800, fail_open=False)
    if not ok:
        return Outcome(False, "visitor_limit")
    res = det.resolution
    sig = signature(res.course, det.type, res.item)
    quote = scrub(question)
    base = _record_base(det, quote, sig, now)
    try:
        records = recent(RECENT_SCAN)
    except supa.SupabaseError as exc:
        config.log.warning("alert history read failed: %s", exc)
        records = []
    last = previous(records, sig)
    last_at = _at(last) if last else None
    if last and last_at and now - last_at < timedelta(seconds=DEDUPE_SECONDS) and last.get("status") in (
            "sent", "failed", "not_configured"):
        record = {**base, "kind": "repeat", "of": last["id"], "status": "repeat"}
        return Outcome(save(record), "repeat", record)
    reports = 1 + (sum(1 for r in records if r.get("kind") == "repeat" and r.get("of") == last["id"]) if last else 0)
    body = compose(res.course, det.type or "other_course_tech", res.item, res.url, quote, reports, now)
    record = {**base, "kind": "alert", "reports": reports, "of": last["id"] if last else None, "message": body,
              "segments": segments(body)}
    return _send_and_record(record, body, now, client)


def _record_base(det: Detection, quote: str, sig: str, now: datetime) -> dict[str, Any]:
    """The fields every stored alert record has: what was detected, where, and the scrubbed quote."""
    res = det.resolution
    return {
        "id": new_id(now), "at": now.isoformat(), "course": res.course, "course_source": res.source,
        "type": det.type, "item": res.item, "item_url": res.url, "match_score": res.score,
        "confidence": det.confidence, "keyword": det.keyword.phrase if det.keyword else None,
        "strong": bool(det.keyword and det.keyword.strong), "classifier": det.classifier_source,
        "quote": shorten(quote, QUOTE_CHARS), "signature": sig,
    }


def _send_and_record(record: dict[str, Any], body: str, now: datetime, client: httpx.Client | None) -> Outcome:
    """Text Ben (unless today's cap is spent) and store the record with Twilio's answer.

    Without Twilio set up nothing is called, and the record says why: Settings still shows the alert.
    """
    if not twilio_config().ready:
        sent = send_sms(body)  # reports why without calling anyone
    elif not take_send(now):
        record["status"] = "over_cap"
        save(record)
        return Outcome(False, "over_cap", record)
    else:
        sent = send_sms(body, client)
    record["status"] = sent.status
    record["twilio"] = {"sid": sent.sid, "status": sent.twilio_status, "error_code": sent.error_code,
                        "error": sent.error}
    if sent.status == "sent":
        usage.record_sms(record["segments"])
    stored = save(record)
    if sent.status == "sent":
        return Outcome(True, "sent", record)
    return Outcome(stored, sent.status if stored else "store_failed", record)


def send_test(now: datetime | None = None, client: httpx.Client | None = None) -> dict[str, Any]:
    """Settings "Send test text": a fixed message, counted against the daily cap. Stored like an alert."""
    now = now or datetime.now(UTC)
    body = test_message(now)
    record = {"id": new_id(now), "at": now.isoformat(), "kind": "test", "type": "test", "course": None,
              "item": None, "reports": 1, "message": body, "segments": segments(body)}
    sent = send_sms(body, client)
    record["status"] = sent.status
    record["twilio"] = {"sid": sent.sid, "status": sent.twilio_status, "error_code": sent.error_code,
                        "error": sent.error}
    if sent.status == "sent":
        usage.record_sms(record["segments"])
    save(record)
    return record


# ---------------------------------------------------------------- the student's reply

def _what(kind: str | None) -> str:
    names = {"api_credits": "an API key", "submission": "a submission", "quiz": "a quiz"}
    return names.get(kind or "", "a course tool")


def reply(question: str, course: str | None, det: Detection, outcome: Outcome) -> dict[str, Any]:
    """The FAQ-style card. "Flagged" only when a text was sent or the alert was stored for Settings."""
    res = det.resolution
    what = _what(det.type)
    if outcome.flagged:
        title = FLAGGED_TITLE
        message = (f"It sounds like a technical problem with {what}, which is usually a quick fix on my side. "
                   "Prof. Collier has the details and will look into it.")
    else:
        title = EMAIL_TITLE
        message = (f"It sounds like a technical problem with {what}. Please email Prof. Collier or the TA with "
                   "what you see, so it can be fixed.")
    code = res.course or course or ""
    links = [{"label": shorten(ascii_text(res.item or "Open on Canvas"), 80), "url": res.url}] if res.url else []
    return {
        "question": question,
        "covered": False,
        "kind": KIND,
        "label": LABEL,
        "flagged": outcome.flagged,
        "title": title,
        "message": message,
        "answers": [{"course": code, "course_label": faq.COURSE_LABELS.get(code, ""), "text": message}],
        "links": links,
        "contacts": [] if outcome.flagged else faq.ta_contacts(code or None),
        "segments": [],
        "sources": [],
        "follow_ups": [],
    }


@dataclass
class Handled:
    reply: dict[str, Any]
    info: dict[str, Any]


def check(question: str, course: str | None, content: Any, complete: Callable[..., str],
          provider: str | None = None, model: str | None = None,
          visitor: str | None = None) -> Handled | None:
    """The hook at the start of `answer()`: None (answer as usual) unless the question reports an incident."""
    try:
        det = detect(question, course, content, complete, provider=provider, model=model)
    except Exception as exc:  # detection must never block a real answer
        config.log.warning("incident detection failed: %s", type(exc).__name__)
        return None
    if not det.incident:
        return None
    try:
        outcome = raise_alert(det, question, visitor)
    except Exception as exc:  # a guard or storage error: tell the student to email instead
        config.log.warning("alert failed: %s", type(exc).__name__)
        outcome = Outcome(False, "error")
    info: dict[str, Any] = {"kind": KIND, "alert_status": outcome.status, "alert_type": det.type,
                            "alert_course": det.resolution.course}
    if det.classifier_source is not None:  # a model was called
        info["provider"], info["model"] = provider, model
    return Handled(reply(question, course, det, outcome), info)
