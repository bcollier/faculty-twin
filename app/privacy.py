"""Scrub obvious personal details from a student's question before it is logged.

The question log may hold question text and scores only (docs/SPEC.md), but a
student can type anything, including their own or a classmate's name. This is a
roster-free heuristic: rosters never leave Ben's Mac, so the Vercel function
cannot know who is in the class. It removes what can be recognised by shape:

- email addresses -> [email]
- phone numbers and long digit runs (student ids) -> [number]
- web handles (@name) -> [handle]
- "my name is X", "I'm called X", "call me X" -> [name]
- a capitalised name after a title (Prof., Dr., Mr., Ms., Mrs.) -> [person],
  except Ben's own (Collier)
- a capitalised name after "classmate", "teammate", "partner", "friend",
  "roommate", "TA", or "student" -> [student]

It does not catch a bare first name ("why did Maria get a different answer"),
which is why the question box also asks students not to type names. Only the
log row is scrubbed; the answer pipeline uses the question as typed.
"""

from __future__ import annotations

import re

_NAME = r"[A-Z][a-zA-Z'\-]+(?:\s+[A-Z][a-zA-Z'\-]+)?"
_KEEP = {"collier", "ben", "benjamin"}

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]\d{4}(?![\w.])")
_LONG_DIGITS = re.compile(r"\b\d{7,}\b")
_HANDLE = re.compile(r"(?<![\w@])@[A-Za-z0-9_.]{2,}")
_SELF = re.compile(
    r"\b((?i:my name is|my name's|i am called|i'm called|call me)\s+)(" + _NAME + r")"
)
_TITLED = re.compile(r"\b((?:Prof|Professor|Dr|Mr|Mrs|Ms|Mx)\.?\s+)(" + _NAME + r")")
_PEER = re.compile(
    r"\b((?i:classmate|teammate|partner|friend|roommate|TA|student|groupmate)s?\s+)(" + _NAME + r")"
)


def _sub_name(pattern: re.Pattern[str], token: str, text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        words = m.group(2).split()
        if words and words[0].lower() in _KEEP:
            return m.group(0)
        if not words[0][:1].isupper():  # "call me later" is not a name
            return m.group(0)
        return m.group(1) + token

    return pattern.sub(repl, text)


def scrub_question(text: str) -> str:
    """Return `text` with recognisable personal details replaced by tokens."""
    if not text:
        return text
    out = _EMAIL.sub("[email]", text)
    out = _HANDLE.sub("[handle]", out)
    out = _PHONE.sub("[number]", out)
    out = _LONG_DIGITS.sub("[number]", out)
    out = _sub_name(_SELF, "[name]", out)
    out = _sub_name(_TITLED, "[person]", out)
    out = _sub_name(_PEER, "[student]", out)
    return out
