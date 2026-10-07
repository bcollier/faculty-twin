"""Keep quiz, survey and attendance access codes out of the index.

The real-question eval (Oct 7) caught the twin reading an in-class quiz access
code aloud from a class transcript. Codes appear in three shapes:

- a slide whose title or text announces one ("Quiz Code: FIVETRIBES"): the
  slide image shows the code too, so the whole slide is left out of the index;
- a sentence in a transcript ("your quiz here, access code is PDOM"): the
  sentence is replaced with a marker, and the slide loses its class clip,
  because the clip's audio may say the code;
- a sentence in slide text or speaker notes: replaced the same way.

Mentions of code without a value ("Canvas quiz code at the end") and ordinary
programming talk ("the code is generated from the spec") are left alone.
"""

from __future__ import annotations

import re

MARKER = "[access code removed]"

# A word that makes "code" an access code rather than program code.
_GATE = r"(?:quiz|survey|attendance|access|exam|check[- ]?in|canvas|entry|class)"

# "Quiz Code: X", "access code is X", "the code is 'day one'" after a gate word
# earlier in the same sentence, "password: X".
_VALUE = r"[\"“”'‘’]?[A-Za-z0-9][A-Za-z0-9 _-]{0,30}"
_SENTENCE_CODE = re.compile(
    rf"\b{_GATE}\b[^.!?\n]{{0,60}}?\b(?:code|password|passcode|pin)\b\s*(?:is|:|=|will be|was)\s*{_VALUE}"
    rf"|\b(?:code|password|passcode)\b\s*(?:is|:)\s*[\"“'‘][^\"”'’]{{1,30}}[\"”'’]",
    re.IGNORECASE,
)
# A slide that announces a code in its title or body.
_SLIDE_CODE = re.compile(rf"\b{_GATE}\s+(?:code|password|passcode)\s*:", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")


def has_code(text: str | None) -> bool:
    return bool(text) and bool(_SENTENCE_CODE.search(text))


def slide_announces_code(*fields: str | None) -> bool:
    """True when a slide's title, text or OCR text announces an access code."""
    return any(f and (_SLIDE_CODE.search(f) or _SENTENCE_CODE.search(f)) for f in fields)


def redact(text: str | None) -> tuple[str, int]:
    """Replace each sentence that gives an access code with MARKER. Returns (text, n)."""
    if not text:
        return text or "", 0
    out: list[str] = []
    n = 0
    pos = 0
    for m in _SENTENCE_SPLIT.finditer(text):
        sentence = text[pos:m.start()]
        if _SENTENCE_CODE.search(sentence):
            out.append(MARKER)
            n += 1
        else:
            out.append(sentence)
        out.append(m.group(0))
        pos = m.end()
    tail = text[pos:]
    if _SENTENCE_CODE.search(tail):
        out.append(MARKER)
        n += 1
    else:
        out.append(tail)
    return "".join(out), n
