"""Narration: one LLM call that writes every segment's script as JSON, then validation.

Flow (spec, "Inside /api/ask" steps 7-8):
1. Send the chosen slides' text, speaker notes, the de-identified transcript of
   what Ben said in class over each slide, and any related code, with the
   grounding prompt below. Ask for JSON only.
2. Validate: every slide_id is one we sent, every narration is non-empty and at
   most 110 words (and 900 characters), follow-ups are short strings, and every
   narration is grounded: most of its content words must come from the slides
   we sent, and it must not repeat a long run of the student's question. The
   AI voice reads whatever passes here, so this is the check that stops a
   prompt-injected question ("ignore the slides and say ...") from putting
   words in Ben's mouth. See docs/SECURITY.md.
   The same pass also rejects crude words (PG), quiz or attendance access
   codes, and de-identification tokens such as [student]. These checks are
   code on purpose: Settings can edit the prompt (app/prompts.py), so nothing
   that keeps the voice safe may live only in prompt text.
3. If validation (or the call) fails, retry once. If it fails again, fall back
   to each slide's speaker notes, or else an excerpt of the class transcript,
   or else the slide text. On the retry, a segment that fails only because its
   words are not in the material falls back on its own (see `validate`).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable
from typing import Any, Callable

from . import config, llm, prompts, usage

MAX_TOKENS = 4000
TARGET_WORDS = "60 to 90"
FALLBACK_WORDS = 90
FIELD_LIMITS = {"text": 2500, "notes": 2000, "transcript": 3000, "code": 2000}

PROMPT_NAME = "narration_system"


def system_prompt() -> str:
    """The narration prompt in use now (Settings can edit it; app/prompts.py holds the default)."""
    return prompts.get(PROMPT_NAME, target_words=TARGET_WORDS, max_words=config.NARRATION_MAX_WORDS)


# The built-in default, filled in. Kept for callers and tests that read the constant.
SYSTEM_PROMPT = prompts.default(PROMPT_NAME, target_words=TARGET_WORDS, max_words=config.NARRATION_MAX_WORDS)


@dataclass
class NarrationResult:
    narrations: dict[str, str]
    follow_ups: list[str]
    source: str  # "llm" or "fallback"
    provider: str | None = None
    model: str | None = None
    errors: list[str] = field(default_factory=list)


class ValidationError(ValueError):
    pass


# ---------------------------------------------------------------- grounding check

GROUNDING_MAX_UNGROUNDED_SHARE = 0.5  # reject when more than half the content words are not in the material
GROUNDING_MIN_UNGROUNDED_WORDS = 2  # ...and at least this many are
ECHO_MAX_WORDS = 8  # reject a run of this many question words that the material does not contain

# Function words and Ben's teaching-voice words: they carry no claim, so they never count against a narration.
_STOP = frozenset(
    """
    a about above after again against all almost also although always am among an and another any anyone
    anything are around as at back be because been before being below between both but by can cannot could
    did do does doing done down during each either else enough even ever every few first for from further get
    gets getting give given go goes going gone good got had has have having he her here hers him his how
    however i if in into is it its itself just keep kind last least less let lets like likely little look
    looking looks lot lots made make makes making many may maybe me might mine more most much must my
    myself need needs never new next no nor not now of off often on once one ones only onto or other others
    our ours out over own part per perhaps put quite rather really right same say says said see seen seem
    seems several shall she should show shows shown since so some something sometimes still such take
    takes than that the their theirs them themselves then there these they thing things think this those
    though three through thus to together too took toward two under until up upon us use used uses using
    very want wants was way ways we well were what when where whether which while who whom whose why will
    with within without would yes yet you your yours yourself
    slide slides screen class lecture course session today talk talked talking question questions asked ask
    remember recall mean means meant idea ideas example examples point points notice explain explained
    walk start end step steps line lines code here's that's it's let's we're you're i'm i've don't
    doesn't isn't aren't can't won't first second third left top bottom earlier later again key main big
    simple simply basically actually important really notice consider imagine whole each different
    """.split()
)
_WORD = re.compile(r"[a-z][a-z'\-]*|[0-9][\w.\-]*")
_URLISH = re.compile(r"https?://|www\.|\b[a-z0-9\-]+\.(?:com|net|org|io|ai|ly|xyz|co|me|app)\b", re.I)


# ---------------------------------------------------------------- PG, access codes, name tokens

# Crude words the voice must never say (PG rule, docs/SPEC.md). Narrower than the clip list in
# indexer/clips.py on purpose: "hell", "crap", "god" and "christ" can be ordinary course words, and
# the grounding check already keeps the narration to the slides' own vocabulary.
_CRUDE = re.compile(
    r"(?<![a-z])(?:f+u+c+k\w*|motherf\w*|shit\w*|bullshit\w*|damn\w*|dammit|goddam\w*|bitch\w*|"
    r"bastard\w*|asshole\w*|jackass\w*|dumbass\w*|piss(?:ed|ing)?|dick|dicks|cock|cocks|cunt\w*|wtf|"
    r"slut\w*|whore\w*)(?![a-z])"
    r"|(?<![a-z])f[\-*]+(?:ing|ed|er|in)?(?![\w\-*])"
    r"|(?<![a-z])(?:sh|s)\*+t(?![a-z])",
    re.I,
)
# Quiz, survey, and attendance access codes: the sentence shape indexer/assessment_filter.py strips
# at import (tests/test_prompts.py checks they catch the same codes), checked again on the way out.
# "class" is not a gate word here: "in class, the code is short" is ordinary narration.
_CODE_GATE = r"(?:quiz|survey|attendance|access|exam|check[- ]?in|canvas|entry)"
_CODE_VALUE = r"[\"“”'‘’]?[A-Za-z0-9][A-Za-z0-9 _-]{0,30}"
_ACCESS_CODE = re.compile(
    rf"\b{_CODE_GATE}\b[^.!?\n]{{0,60}}?\b(?:code|password|passcode|pin)\b\s*(?:is|:|=|will be|was)\s*{_CODE_VALUE}"
    rf"|\b(?:code|password|passcode)\b\s*(?:is|:)\s*[\"“'‘][^\"”'’]{{1,30}}[\"”'’]"
    r"|\[access code removed\]",
    re.I,
)
# Tokens the pipeline puts where a name or removed text was. Speaking one means the model copied
# masked material, or tried to describe a student.
_NAME_TOKEN = re.compile(r"\[\s*(?:student|students|person|people|name|removed|redacted)\s*\]", re.I)


def speech_problem(text: str) -> str | None:
    """Why the voice must not say this text (PG, access code, name token), or None. Never reads a prompt."""
    if _CRUDE.search(text):
        return "it has a crude word (PG rule)"
    if _ACCESS_CODE.search(text):
        return "it gives an access code"
    if _NAME_TOKEN.search(text):
        return "it has a [student] or [person] token"
    return None


# "didn't", "I'd", "we'll", "they're", "you've", "I'm": function words, never a claim.
_CONTRACTION = re.compile(r"(?:n't|'d|'ll|'re|'ve|'m)$")
# Past forms the suffix rules cannot reach, mapped to the form the slides and transcripts use.
_IRREGULAR = {
    "came": "come", "told": "tell", "brought": "bring", "thought": "think", "taught": "teach", "gave": "give",
    "saw": "see", "went": "go", "ran": "run", "found": "find", "felt": "feel", "met": "meet", "built": "build",
    "chose": "choose", "chosen": "choose", "wrote": "write", "written": "write", "spoke": "speak",
    "began": "begin", "begun": "begin", "drew": "draw", "drawn": "draw", "grew": "grow", "grown": "grow",
    "knew": "know", "known": "know", "understood": "understand", "held": "hold", "led": "lead", "lost": "lose",
}
_NOT_PAST = frozenset({"embed", "hundred", "speed", "seed", "feed", "breed", "proceed", "exceed", "succeed"})


def _apostrophes(text: Any) -> str:
    """Curly quotes as straight ones, so "didn’t" and "didn't" read the same."""
    return str(text or "").replace("’", "'").replace("‘", "'").replace("ʼ", "'")


def _stem(word: str) -> str:
    """A light, symmetric stem: slide words and narration words go through the same rules.

    Plural or third person first ("embeddings" -> "embedding"), then one verb or adverb ending
    ("embedding" -> "embedd" -> "embed"), then a final silent "e" ("take", "taking", "takes" -> "tak").
    Changed Oct 8 (code review): the old single-suffix rule left "taking"/"take", "mapping"/"map",
    "titled"/"title" and "embeddings"/"embedding" apart, which rejected well-grounded narration.
    """
    word = word.strip("'-")
    if word.endswith("'s"):
        word = word[:-2]
    word = _IRREGULAR.get(word, word)
    if word.endswith("ies") and len(word) - 3 >= 3:
        word = word[:-3] + "y"  # "categories" -> "category"
    elif word.endswith("es") and len(word) - 2 >= 3:
        word = word[:-2]  # "classes" -> "class", "images" -> "imag" (and "image" loses its "e" below)
    elif word.endswith("s") and len(word) - 1 >= 3 and not word.endswith(("ss", "us", "is")):
        word = word[:-1]  # not "class", "focus", "axis"
    stripped = ""
    if word in _NOT_PAST:
        pass
    elif word.endswith("ied") and len(word) - 3 >= 3:
        word = word[:-3] + "y"
    else:
        for suffix in ("ing", "ed", "ly"):
            if word.endswith(suffix) and len(word) - len(suffix) >= (4 if suffix == "ly" else 3):
                word, stripped = word[: -len(suffix)], suffix
                break
    if stripped in ("ing", "ed") and len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "lsz":
        word = word[:-1]  # "mapping" -> "mapp" -> "map", "running" -> "run"; "called" keeps "call"
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    return word


def _content_words(text: str) -> list[str]:
    out = []
    for token in _WORD.findall(_apostrophes(text).lower()):
        if token[0].isdigit():  # numbers, ids, versions: neutral
            continue
        if len(token) < 3 or token in _STOP or _CONTRACTION.search(token):
            continue
        stem = _stem(token)
        if stem and stem not in _STOP:
            out.append(stem)
    return out


def _plain_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", _apostrophes(text).lower())


def _ngrams(words: list[str], n: int) -> set[tuple[str, ...]]:
    return {tuple(words[i : i + n]) for i in range(len(words) - n + 1)}


@dataclass
class Grounding:
    """What a narration may draw on: the vocabulary of the slides sent, and the question's long runs."""

    vocab: set[str]
    question_runs: set[tuple[str, ...]]

    @classmethod
    def build(cls, question: str, materials: Iterable[str]) -> "Grounding":
        text = "\n".join(str(m or "") for m in materials)
        vocab = set(_content_words(text))
        runs = _ngrams(_plain_words(question), ECHO_MAX_WORDS) - _ngrams(_plain_words(text), ECHO_MAX_WORDS)
        return cls(vocab, runs)

    def problem(self, narration: str) -> str | None:
        """Why this narration is not grounded, or None when it is."""
        return self.vocab_problem(narration) or self.echo_problem(narration)

    def vocab_problem(self, narration: str) -> str | None:
        """Most of its content words are not in the material (a thin slide, or invented content)."""
        words = _content_words(narration)
        ungrounded = [w for w in words if w not in self.vocab]
        if (
            len(ungrounded) >= GROUNDING_MIN_UNGROUNDED_WORDS
            and len(ungrounded) > GROUNDING_MAX_UNGROUNDED_SHARE * len(words)
        ):
            return f"{len(ungrounded)} of {len(words)} content words are not in the slides"
        return None

    def echo_problem(self, narration: str) -> str | None:
        """It repeats a long run of the question that the material does not contain (prompt injection)."""
        if self.question_runs and _ngrams(_plain_words(narration), ECHO_MAX_WORDS) & self.question_runs:
            return "it repeats the question's wording"
        return None


def grounding_for(question: str, slides: list[dict[str, Any]], codes: dict[str, str | None]) -> Grounding:
    materials: list[str] = []
    for rec in slides:
        materials += [rec.get(k) or "" for k in ("title", "text", "notes", "transcript", "course_title", "session_title")]
        materials.append(codes.get(rec["id"]) or "")
    return Grounding.build(question, materials)


def _clip(text: Any, limit: int) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + " ..."


def slide_payload(rec: dict[str, Any], code: str | None) -> dict[str, Any]:
    return {
        "slide_id": rec["id"],
        "course": rec.get("course_title") or rec.get("course"),
        "session": rec.get("session_title"),
        "date": rec.get("date"),
        "slide_number": rec.get("slide_number"),
        "title": rec.get("title") or "",
        "slide_text": _clip(rec.get("text"), FIELD_LIMITS["text"]),
        "speaker_notes": _clip(rec.get("notes"), FIELD_LIMITS["notes"]),
        "what_ben_said_in_class": _clip(rec.get("transcript"), FIELD_LIMITS["transcript"]),
        "code": _clip(code, FIELD_LIMITS["code"]) if code else "",
    }


def build_user_prompt(question: str, slides: list[dict[str, Any]]) -> str:
    return (
        "Student question (answer it only from the slides below):\n"
        + json.dumps(question)
        + "\n\nSlides, in the order they will be shown:\n"
        + json.dumps(slides, ensure_ascii=False, indent=1)
    )


def word_count(text: str) -> int:
    return len(text.split())


def clean_speech(text: str) -> str:
    """Visitor-facing copy uses no em dashes; collapse whitespace."""
    text = re.sub(r"\s*[—–]\s*", ", ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_json(raw: str) -> Any:
    raw = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    if fence:
        raw = fence.group(1)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
    raise ValidationError("reply was not JSON")


_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+[\"'”’)\]]*|$)\s*")


def trim_to_sentences(
    text: str, max_words: int, max_chars: int | None = None, min_words: int | None = None
) -> str | None:
    """`text` cut back to its leading whole sentences that fit the caps, or None.

    A reply a few words over a cap used to be thrown away whole (Oct 8 code review: Opus 5.5 wrote
    122-126 word course-info answers, so every one fell back to raw Canvas text). Only whole sentences
    are kept, nothing is added, and every check still runs on what is kept. None when no sentence
    boundary fits, or when what fits is under `min_words` (default half the word cap: then the retry
    or fallback is better). Shared by narration, course-info, and web answers.
    """
    min_words = max_words // 2 if min_words is None else min_words
    if word_count(text) <= max_words and (max_chars is None or len(text) <= max_chars):
        return text
    kept, raw = "", ""
    for sentence in _SENTENCE.findall(text):
        raw += sentence
        candidate = raw.strip()
        if word_count(candidate) > max_words or (max_chars is not None and len(candidate) > max_chars):
            break
        kept = candidate
    if not kept or kept[-1] not in ".!?\"'”’)]" or word_count(kept) < min_words:
        return None
    return kept


def validate(
    raw: str, sent_ids: list[str], grounding: Grounding | None = None, dropped: list[str] | None = None
) -> tuple[dict[str, str], list[str]]:
    """Return ({slide_id: narration}, follow_ups) or raise ValidationError.

    With `grounding` (always passed by `narrate`), each narration must also pass
    the grounding check; one failing segment fails the reply, which leads to a
    retry. On the retry `narrate` passes a `dropped` list: a segment whose only
    problem is that most of its words are not in the material (typically a thin
    slide such as "End of class", where the model talks about the slide instead
    of explaining it) is left out and named in `dropped`, so that slide falls
    back to its own notes while the grounded segments are kept. Every other
    problem (unknown slide, length, web address, PG, access code, name token,
    and repeating the question) still fails the whole reply.
    """
    data = _extract_json(raw)
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ValidationError("missing segments list")
    allowed = set(sent_ids)
    out: dict[str, str] = {}
    for seg in data["segments"]:
        if not isinstance(seg, dict):
            raise ValidationError("segment is not an object")
        sid, text = seg.get("slide_id"), seg.get("narration")
        if sid not in allowed:
            raise ValidationError(f"unknown slide_id {sid!r}")
        if not isinstance(text, str) or not text.strip():
            raise ValidationError(f"empty narration for {sid}")
        text = clean_speech(text)
        trimmed = trim_to_sentences(text, config.NARRATION_MAX_WORDS, config.NARRATION_MAX_CHARS)
        if trimmed is None:
            if word_count(text) > config.NARRATION_MAX_WORDS:
                raise ValidationError(f"narration for {sid} is {word_count(text)} words")
            raise ValidationError(f"narration for {sid} is {len(text)} characters")
        text = trimmed
        if _URLISH.search(text):
            raise ValidationError(f"narration for {sid} contains a web address")
        problem = speech_problem(text)
        if problem:
            raise ValidationError(f"narration for {sid} is not allowed: {problem}")
        if grounding is not None:
            problem = grounding.echo_problem(text)
            if problem:
                raise ValidationError(f"narration for {sid} is not grounded: {problem}")
            problem = grounding.vocab_problem(text)
            if problem:
                if dropped is None:
                    raise ValidationError(f"narration for {sid} is not grounded: {problem}")
                dropped.append(f"narration for {sid} is not grounded: {problem}")
                continue
        out[sid] = text
    if not out:
        raise ValidationError("no segments")
    follow = data.get("follow_ups") or []
    if not isinstance(follow, list):
        follow = []
    follow_ups = [
        clean_speech(f)[:150]
        for f in follow
        if isinstance(f, str) and f.strip() and not _URLISH.search(f) and not speech_problem(f)
    ][:2]
    return out, follow_ups


def _first_words(text: str, limit: int = FALLBACK_WORDS) -> str:
    words = text.split()
    if len(words) <= limit:
        return " ".join(words)
    cut = " ".join(words[:limit])
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[: stop + 1] if stop > len(cut) // 2 else cut + " ..."


def fallback_narration(rec: dict[str, Any]) -> str:
    for key in ("notes", "transcript", "text"):
        value = str(rec.get(key) or "").strip()
        if value:
            return clean_speech(_first_words(value))
    return clean_speech(rec.get("title") or "This slide.")


def narrate(
    question: str,
    slides: list[dict[str, Any]],
    codes: dict[str, str | None] | None = None,
    provider: str | None = None,
    model: str | None = None,
    complete: Callable[..., str] | None = None,
) -> NarrationResult:
    """Write narration for `slides` (records, in play order). Never raises for model trouble."""
    codes = codes or {}
    complete = complete or llm.complete_json
    if provider is None or model is None:
        from . import settings_store

        provider, model = settings_store.llm_choice()
    sent_ids = [r["id"] for r in slides]
    user = build_user_prompt(question, [slide_payload(r, codes.get(r["id"])) for r in slides])
    grounding = grounding_for(question, slides, codes)
    system = system_prompt()  # read once per answer, so a retry uses the same text
    errors: list[str] = []
    for attempt in range(2):
        # The first reply must pass whole; on the retry an ungrounded segment falls back on its own.
        dropped: list[str] | None = [] if attempt else None
        try:
            with usage.purpose("narration"):
                raw = complete(system, user, MAX_TOKENS, provider=provider, model=model)
            narrations, follow_ups = validate(raw, sent_ids, grounding, dropped)
        except (llm.LLMError, ValidationError) as exc:
            errors.append(str(exc)[:200])
            config.log.warning("narration attempt failed: %s", str(exc)[:200])
            continue
        for problem in dropped or []:
            errors.append(problem[:200])
            config.log.warning("narration segment fell back: %s", problem[:200])
        for rec in slides:  # a slide the model skipped (or whose narration was dropped) gets its notes
            narrations.setdefault(rec["id"], fallback_narration(rec))
        return NarrationResult(narrations, follow_ups, "llm", provider, model, errors)
    return NarrationResult(
        {r["id"]: fallback_narration(r) for r in slides}, [], "fallback", provider, model, errors
    )
