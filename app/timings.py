"""Word timings for the read-along narration box (docs/SPEC.md, "Read-along narration and slide spotlight").

The page highlights the word being spoken. It needs, for each segment, when
each word starts and where that word sits in the narration text:

    {"words": [[seconds, char_index], ...], "source": "elevenlabs" | "edge" | ...}

The approach follows Ben's Ignatius app (app/tts.py there): the voice service
reports spoken words (edge-tts WordBoundary events) or characters (ElevenLabs
with-timestamps alignment) with their times; `align()` finds each spoken word
in the narration text, in order, so the page can map a time to a word span.

Live audio: `/api/audio` collects the timings while it streams (a `Collector`)
and, once the stream finished cleanly, saves them here. `/api/audio/timings`
reads them back. They live in memory (this instance) and in the private bucket
at `timings/<voice tag>/<key>.json`, which only the function reads and writes;
`storage.is_media_path` never lets that folder into a signed link.

Nothing here calls a voice service or spends a voice character.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections import OrderedDict
from typing import Any

from . import config

ELEVEN_BYTES_PER_SECOND = 128_000 / 8  # mp3_44100_128, constant bit rate
EDGE_BYTES_PER_SECOND = 48_000 / 8  # audio-24khz-48kbitrate-mono-mp3
MAX_JUMP_CHARS = 80  # a spoken word further ahead than this is skipped, not jumped to
MEMORY_MAX = 400
PREFIX = "timings/"
CACHE_CONTROL = "private, max-age=86400"

Words = list[list[float]]


# ---------------------------------------------------------------- conversion

def align(text: str, spoken: list[tuple[float, str]]) -> Words:
    """[[seconds, character index in text], ...] for each spoken word found in `text`, in order.

    A word the service reports differently from the text (a number read out,
    punctuation attached) is looked for from the cursor on; when it is not
    near, it is skipped rather than jumping ahead.
    """
    out: Words = []
    cursor = 0
    last_t = -1.0
    for t, word in spoken:
        word = (word or "").strip()
        if not word:
            continue
        i = text.find(word, cursor)
        if i < 0:  # try without surrounding punctuation ("k-means," vs "k-means")
            core = word.strip(".,;:!?\"'()[]")
            i = text.find(core, cursor) if core else -1
            word = core or word
        if i < 0 or i - cursor > MAX_JUMP_CHARS:
            continue
        t = max(float(t), last_t)  # times never go backwards
        out.append([round(t, 3), i])
        cursor, last_t = i + len(word), t
    return out


def words_from_characters(chars: list[str], starts: list[float]) -> list[tuple[float, str]]:
    """Character timings (ElevenLabs alignment) to (start time, word) pairs."""
    words: list[tuple[float, str]] = []
    current, start = "", 0.0
    for ch, t in zip(chars, starts):
        if not ch or ch.isspace():
            if current:
                words.append((start, current))
            current = ""
            continue
        if not current:
            start = float(t)
        current += ch
    if current:
        words.append((start, current))
    return words


def words_from_forced_alignment(reply: dict[str, Any]) -> list[tuple[float, str]]:
    """ElevenLabs forced alignment `{words: [{text, start, end, loss}]}` to (start, word) pairs."""
    out = []
    for w in reply.get("words") or []:
        text = str(w.get("text") or "").strip()
        if text and isinstance(w.get("start"), (int, float)):
            out.append((float(w["start"]), text))
    return out


class Collector:
    """Gathers word or character timings while audio streams; `result(text)` aligns them.

    `failed` is set when the stream broke off, so a partial set is never saved.
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.spoken: list[tuple[float, str]] = []
        self._chars: list[str] = []
        self._starts: list[float] = []
        self._last = 0.0
        self.failed = False

    def add_word(self, seconds: float, word: str) -> None:
        self.spoken.append((float(seconds), word))

    def add_alignment(self, alignment: dict[str, Any] | None, audio_seconds_before: float) -> None:
        """One ElevenLabs chunk's character alignment.

        The docs do not say whether a streamed chunk's times start at zero or
        continue from the previous chunk, so both are handled: times that go
        backwards mean the chunk is relative, and it is offset by the audio
        already sent before it.
        """
        if not isinstance(alignment, dict):
            return
        chars = list(alignment.get("characters") or [])
        starts = [float(x) for x in (alignment.get("character_start_times_seconds") or [])]
        n = min(len(chars), len(starts))
        if not n:
            return
        chars, starts = chars[:n], starts[:n]
        offset = 0.0
        if self._starts and starts[0] < self._last - 0.25:
            offset = audio_seconds_before
        starts = [s + offset for s in starts]
        self._chars += chars
        self._starts += starts
        self._last = max(self._last, starts[-1])

    def result(self, text: str) -> Words:
        """Timings for each word of `text`, from everything collected so far."""
        spoken = list(self.spoken)
        if self._chars:
            spoken += words_from_characters(self._chars, self._starts)
        spoken.sort(key=lambda w: w[0])
        return align(text, spoken)


# ---------------------------------------------------------------- estimate (server side, for tests and tools)

_VOWELS = re.compile(r"[aeiouy]+", re.I)


def syllables(word: str) -> int:
    """A rough syllable count: vowel groups, a silent final e dropped, at least one."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:  # a number or symbol: count it as one beat
        return 1
    n = len(_VOWELS.findall(w))
    if w.endswith("e") and not w.endswith(("le", "ee")) and n > 1:
        n -= 1
    return max(1, n)


def estimate(text: str, duration: float) -> Words:
    """Spread `duration` seconds over the words by syllables, with pauses at commas and sentence ends.

    The page does the same in JavaScript (public/readalong.js); this copy is
    used by tools and tests to check the two agree.
    """
    tokens = [(m.start(), m.group(0)) for m in re.finditer(r"\S+", text)]
    if not tokens or duration <= 0:
        return []
    weights = []
    for _, tok in tokens:
        pause = 2 if re.search(r"[.!?][\"')\]]*$", tok) else (1 if re.search(r"[,;:]$", tok) else 0)
        weights.append((syllables(tok), pause))
    total = sum(s + p for s, p in weights)
    out: Words = []
    acc = 0.0
    for (i, _), (s, p) in zip(tokens, weights):
        out.append([round(duration * acc / total, 3), i])
        acc += s + p
    return out


# ---------------------------------------------------------------- storage

_lock = threading.Lock()
_memory: OrderedDict[str, dict[str, Any]] = OrderedDict()


def key(tag: str, text: str) -> str:
    return hashlib.sha256(f"timings\n{tag}\n{text}".encode()).hexdigest()[:32]


def bucket_path(tag: str, text: str) -> str:
    safe_tag = re.sub(r"[^A-Za-z0-9_\-]", "", tag)[:40] or "none"
    return f"{PREFIX}{safe_tag}/{key(tag, text)}.json"


def clear_memory() -> None:
    with _lock:
        _memory.clear()


def save(tag: str, text: str, words: Words, source: str) -> None:
    """Keep the timings for this text in this voice. Never raises: the audio already played."""
    if not words:
        return
    entry = {"words": words, "source": source}
    k = key(tag, text)
    with _lock:
        _memory[k] = entry
        _memory.move_to_end(k)
        while len(_memory) > MEMORY_MAX:
            _memory.popitem(last=False)
    if config.content_dir() is not None or not config.supabase_configured():
        return
    from . import supa

    try:
        supa.upload(bucket_path(tag, text), json.dumps(entry, separators=(",", ":")).encode(),
                    "application/json", upsert=True, cache_control="no-cache, max-age=0")
    except Exception as exc:  # timings are a nicety: a failed save only means the page keeps its estimate
        config.log.warning("saving word timings failed: %s", type(exc).__name__)


def load(tag: str, text: str) -> dict[str, Any] | None:
    """Saved timings for this text in this voice, or None when there are none yet."""
    k = key(tag, text)
    with _lock:
        hit = _memory.get(k)
        if hit is not None:
            _memory.move_to_end(k)
            return hit
    if config.content_dir() is not None or not config.supabase_configured():
        return None
    from . import supa

    try:
        raw = supa.download_optional(bucket_path(tag, text))
    except Exception as exc:
        config.log.warning("reading word timings failed: %s", type(exc).__name__)
        return None
    if not raw:
        return None
    try:
        entry = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("words"), list):
        return None
    with _lock:
        _memory[k] = entry
        while len(_memory) > MEMORY_MAX:
            _memory.popitem(last=False)
    return entry
