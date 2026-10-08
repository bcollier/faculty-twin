"""Logistics check: send meeting, absence, grade, deadline and Canvas questions to Ben, not the twin.

The real-question eval (evals/README.md, Oct 7) found these messages score just
above the not-covered threshold (0.54 to 0.55 against 0.52), so retrieval alone
narrates slides at them. `/api/ask` runs this check after the not-covered check
and before narration (docs/SPEC.md, "Inside /api/ask", step 7a):

1. A keyword pre-check routes the obvious cases with no model call.
2. Everything else goes to one small LLM call through app/llm.py (the active
   provider and model) that must reply `{"kind": "course_content" | "logistics", "reason": "..."}`.
3. If that call fails or replies with anything else, the question is treated as
   course content: this check never blocks a real answer.

The prompt text lives in app/prompts.py (`logistics_classifier`) so Settings can
edit it. The keyword pre-check and the reply parser stay in code.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from . import config, prompts, usage

COURSE_CONTENT = "course_content"
LOGISTICS = "logistics"
KINDS = (COURSE_CONTENT, LOGISTICS)

MESSAGE = "That one is for me directly, not my twin. Please email me or come to office hours."
MAX_TOKENS = 200
MAX_FOLLOW_UPS = 3

# Phrases that only show up in logistics messages. Kept narrow on purpose: a
# miss here costs one small model call, a false hit sends away a real question.
_PATTERNS = [
    r"\boffice hours?\b",
    r"\b(zoom|phone|video) (call|meeting)\b",
    r"\b(meet|meeting|chat|talk|call) with you\b",
    r"\b(set up|schedule|book|arrange|request) (a |an )?(time|meeting|call|appointment|chat)\b",
    r"\bcan (we|i) meet\b",
    r"\bmiss(ed|ing)? (the |this |today'?s |tomorrow'?s |next |last |our |a |your )?(class|lecture|recitation)\b",
    r"\bmissed (the |today'?s |last |our )?session\b",
    r"\b(be|was|am|been|i'?m|will be) (absent|out sick)\b",
    r"\b(excused )?absence (from|in) (class|lecture)\b|\bexcused absence\b",
    r"\b(i am|i'?m|i was|i have been|i'?ve been|i got|feeling) sick\b",
    r"\battendance (policy|requirement|grade|points?|count|record)\b|\b(take|took|takes|mark|marked) attendance\b",
    r"\bmy attendance\b",
    r"\b(an|another|a short|any|short) extension\b(?! (of|to)\b)|\bextension (on|for|request)\b|\bdeadline extension\b",
    r"\bextension (of|to) (the |my |our )?(deadline|due date)\b",
    r"\bextend (the |my |our )?(deadline|due date)\b",
    r"\blate (submission|penalty|days?)\b",
    r"\bsubmit (it |this |my \w+ )?late\b",
    r"\bre-?grade\b",
    r"\b(my|our|final|midterm|participation|letter) grades?\b",
    r"\bgrade (for|on) (my|the|our)\b",
    r"\bgrad(ing|ed)\b",
    r"\breschedul(e|ing)\b",
    r"\bswap (our |my |the )?(presentation|slot|time)\b",
    r"\bpresentation (slot|date|time|day)\b",
    r"\bcanvas (page|site|access|login|submission|shell|quiz|assignment)\b|\b(on|in|from|to|into) canvas\b",
    r"\b(can'?t|cannot|can not|unable to) (access|log ?in|submit)\b",
    r"\bmy team ?(member|mate)s?\b",
    r"\bteam ?(member|mate)s? (is|are|isn'?t|aren'?t|won'?t|don'?t|doesn'?t|hasn'?t|haven'?t|never|not)\b",
    r"\bteam (registration|sign ?up|issue|problem|conflict)\b",
    r"\bdrop (the |this |your )?(class|course)\b",
    r"\bwaitlist\b",
    r"\brecommendation letter\b|\bletter of recommendation\b",
]
_KEYWORDS = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.I)

PROMPT_NAME = "logistics_classifier"
SYSTEM_PROMPT = prompts.default(PROMPT_NAME)  # the built-in default; Settings can edit it (app/prompts.py)


@dataclass
class Classification:
    kind: str  # COURSE_CONTENT or LOGISTICS
    source: str  # "keyword", "llm", or "error" (fell through to course content)
    reason: str = ""


def keyword_hit(question: str) -> Optional[str]:
    """The phrase that marks an obvious logistics question, or None."""
    text = re.sub(r"\s+", " ", (question or "").replace("\u2019", "'"))
    m = _KEYWORDS.search(text)
    return m.group(0) if m else None


def _parse(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("reply was not JSON")
    data: Any = json.loads(text[start : end + 1])
    if not isinstance(data, dict) or data.get("kind") not in KINDS:
        raise ValueError("reply had no valid kind")
    return data["kind"], str(data.get("reason") or "")[:200]


def classify(
    question: str,
    complete: Callable[..., str],
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Classification:
    """Keyword pre-check, then one small model call. Any failure means course content."""
    hit = keyword_hit(question)
    if hit:
        return Classification(LOGISTICS, "keyword", hit.lower())
    user = "Student message (sort it, do not answer it):\n" + json.dumps({"message": question})
    try:
        with usage.purpose("logistics"):
            raw = complete(prompts.get(PROMPT_NAME), user, MAX_TOKENS, provider=provider, model=model)
        kind, reason = _parse(raw)
    except Exception as exc:  # never block a real answer on this check
        config.log.warning("logistics check failed, answering normally: %s", type(exc).__name__)
        return Classification(COURSE_CONTENT, "error")
    return Classification(kind, "llm", reason)


def referral(question: str, follow_ups: list[str]) -> dict[str, Any]:
    """The 200 reply for a logistics question: no segments, no audio, Ben's own words."""
    return {
        "question": question,
        "covered": False,
        "kind": LOGISTICS,
        "segments": [],
        "sources": [],
        "message": MESSAGE,
        "follow_ups": follow_ups[:MAX_FOLLOW_UPS],
    }
