"""Which voice speaks, what students are told about it, and which daily cap it uses.

Three tiers, chosen in Settings and stored in `settings.voice_id`:

  "eleven:<id>"   an ElevenLabs voice. My clone when its category is "cloned"
                  (or a "professional" clone this account owns); otherwise a
                  stock voice. Costs ElevenLabs characters.
  "edge:<Short>"  a free Microsoft neural voice through edge-tts (no key, no cost).
  "none"          captions only.
  null            the server default: ELEVENLABS_VOICE_ID, or captions only when unset.
  "<id>"          the older bare form of an ElevenLabs id; same as "eleven:<id>".

Honest labeling (docs/SPEC.md, Safety): the label must match the voice that
actually speaks. My clone: "AI voice made from my recordings." Any stock or
free voice: "AI voice (a stock voice, not mine)." Captions only: no label.
If an ElevenLabs voice's category cannot be checked, the label is the neutral
"AI voice." and never the clone label.

Optional fallback (`settings.voice_fallback` = "free"): when the ElevenLabs
voice fails or its daily cap is used up, a free voice
(`settings.voice_fallback_voice`) speaks instead of going silent. The
fallback has its own signed link and its own label, and the student page
switches the label when it switches voices.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional

from . import config, edge_voice, limits, settings_store, speech

ELEVEN = "elevenlabs"
EDGE = "edge"

CLONE_LABEL = "AI voice made from my recordings."
STOCK_LABEL = "AI voice (a stock voice, not mine)."
UNVERIFIED_LABEL = "AI voice."
LABELS = {"clone": CLONE_LABEL, "stock": STOCK_LABEL, "free": STOCK_LABEL}

ELEVEN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,100}$")
FALLBACK_MODES = ("captions", "free")


class BadVoice(ValueError):
    """A voice setting that does not parse; the message is safe to show in Settings."""


@dataclass(frozen=True)
class Voice:
    provider: str  # "elevenlabs" | "edge"
    voice_id: str  # ElevenLabs voice id, or an edge-tts ShortName
    kind: Optional[str] = None  # "clone" | "stock" | "free"; None = ElevenLabs voice not verified

    @property
    def setting(self) -> str:
        return f"eleven:{self.voice_id}" if self.provider == ELEVEN else f"edge:{self.voice_id}"

    @property
    def tag_key(self) -> str:
        """What the audio-link voice tag hashes. ElevenLabs keeps the bare id, so older links and
        `audio/<id>/` folders still match; free voices are prefixed so they never collide."""
        return self.voice_id if self.provider == ELEVEN else self.setting

    @property
    def tag(self) -> str:
        return speech.voice_tag(self.tag_key)

    @property
    def label(self) -> str:
        return LABELS.get(self.kind or "", UNVERIFIED_LABEL)

    @property
    def pool(self) -> str:
        """Which daily character budget this voice spends."""
        return "voice" if self.provider == ELEVEN else "free"

    @property
    def costs_money(self) -> bool:
        return self.provider == ELEVEN

    def public(self) -> dict[str, Any]:
        return {"kind": self.kind or "unverified", "label": self.label}

    def audio_prefixes(self) -> tuple[str, ...]:
        """Bucket folders whose pre-generated mp3s were made with this voice."""
        prefixes = [f"audio/{self.tag}/"]
        if self.provider == ELEVEN:
            prefixes.append(f"audio/{self.voice_id}/")  # older layout: audio/<ElevenLabs id>/
        return tuple(prefixes)


@dataclass(frozen=True)
class Plan:
    primary: Optional[Voice] = None  # None: captions only
    fallback: Optional[Voice] = None  # a free voice for when the ElevenLabs voice fails or is capped

    def match(self, tag: str) -> Optional[Voice]:
        """The voice an audio link's tag belongs to, if it is one of today's voices."""
        for voice in (self.primary, self.fallback):
            if voice is not None and voice.tag == tag:
                return voice
        return None

    def public(self) -> dict[str, Any]:
        if self.primary is None:
            return {"kind": "none", "label": None, "fallback": None}
        return {**self.primary.public(), "fallback": self.fallback.public() if self.fallback else None}


# ---------------------------------------------------------------- parsing

def parse(value: Any) -> Optional[tuple[str, str]]:
    """("elevenlabs"|"edge", id), ("none", "") for captions only, or None for "not set". Raises BadVoice."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text == "none":
        return ("none", "")
    if text.startswith("edge:"):
        name = text[5:]
        if not edge_voice.SHORT_NAME_RE.match(name):
            raise BadVoice("A free voice id looks like edge:en-US-AndrewMultilingualNeural.")
        return (EDGE, name)
    if text.startswith("eleven:"):
        text = text[7:]
    if not ELEVEN_ID_RE.match(text):
        raise BadVoice("That voice id does not look right.")
    return (ELEVEN, text)


def eleven_kind(voice_id: str, lookup: bool = True) -> Optional[str]:
    """"clone" or "stock" for an ElevenLabs voice, or None when it cannot be checked.

    A kind saved with the voice in Settings is used first (it was checked
    against the account when it was saved). Otherwise the account's voice
    list is asked (cached for an hour).
    """
    saved = settings_store.get("voice_kind")
    if isinstance(saved, dict) and saved.get("voice_id") == voice_id and saved.get("kind") in ("clone", "stock"):
        return saved["kind"]
    if not lookup:
        return None
    meta = speech.find_voice(voice_id)
    if meta is None:
        return None
    return "clone" if speech.is_clone(meta) else "stock"


def stored_setting() -> Any:
    """The raw voice setting: the Settings row, else ELEVENLABS_VOICE_ID, else None."""
    return settings_store.get("voice_id") or config.env("ELEVENLABS_VOICE_ID")


def fallback_mode() -> str:
    mode = settings_store.get("voice_fallback")
    return mode if mode in FALLBACK_MODES else "captions"


def fallback_voice_name() -> str:
    raw = settings_store.get("voice_fallback_voice")
    try:
        parsed = parse(raw)
    except BadVoice:
        parsed = None
    if parsed and parsed[0] == EDGE:
        return parsed[1]
    return edge_voice.DEFAULT_FREE_VOICE


def current(resolve_kind: bool = True) -> Plan:
    """Today's voices. `resolve_kind=False` skips the ElevenLabs lookup (routing does not need labels)."""
    try:
        parsed = parse(stored_setting())
    except BadVoice:
        config.log.warning("voice setting does not parse; captions only")
        return Plan()
    if parsed is None or parsed[0] == "none":
        return Plan()
    provider, voice_id = parsed
    if provider == EDGE:
        return Plan(primary=Voice(EDGE, voice_id, "free"))
    primary = Voice(ELEVEN, voice_id, eleven_kind(voice_id, lookup=resolve_kind))
    fallback = Voice(EDGE, fallback_voice_name(), "free") if fallback_mode() == "free" else None
    return Plan(primary=primary, fallback=fallback)


def eleven_budget_left() -> bool:
    """False when today's ElevenLabs character cap is already used up (or set to zero)."""
    cap = settings_store.daily_voice_char_cap()
    if cap <= 0:
        return False
    return limits.read_counter(limits.voice_key("voice")) < cap


def for_answer() -> Plan:
    """The voices an answer should use. When the ElevenLabs cap is already spent and the
    free fallback is on, the free voice speaks from the start (and is labeled as such)."""
    plan = current()
    if plan.primary is not None and plan.primary.provider == ELEVEN and plan.fallback is not None:
        if not eleven_budget_left():
            return Plan(primary=plan.fallback)
    return plan


def daily_cap(voice: Voice) -> int:
    if voice.provider == ELEVEN:
        return settings_store.daily_voice_char_cap()
    return settings_store.daily_free_voice_char_cap()


def setting_kind(voice_setting: Any) -> str:
    """"clone", "stock", "free", "unverified", or "none" for a stored setting (Settings page view)."""
    try:
        parsed = parse(voice_setting)
    except BadVoice:
        return "none"
    if parsed is None or parsed[0] == "none":
        return "none"
    if parsed[0] == EDGE:
        return "free"
    return eleven_kind(parsed[1]) or "unverified"
