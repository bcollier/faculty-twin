"""Property-based (fuzz) tests for the text safety filters, written with hypothesis.

Covers the four filters that stand between raw text and what a student sees or hears:

- the PG filter (indexer/pg_filter.py)
- the access-code filter (indexer/assessment_filter.py), and the same check on the way out
  (app/narration.py speech_problem)
- de-identification masking: the transcript scrubber (indexer/deidentify.py) and the
  question-log scrubber (app/privacy.py)
- the narration validators (app/narration.py validate, clean_speech, speech_problem)

The properties: no crash on any text (unicode, control characters, long input), a second pass
changes nothing (idempotent), and the output never gains a name: every word in the output that
was not in the input comes from the filter's own fixed vocabulary ("darn", "[student]", ...).

Every name and sentence here is invented. Runs are derandomized so CI is deterministic.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from app import narration, privacy
from indexer import assessment_filter as af
from indexer import pg_filter

ROOT = Path(__file__).resolve().parents[1]

FAST = settings(max_examples=150, deadline=None, derandomize=True,
                suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
SLOW = settings(max_examples=40, deadline=None, derandomize=True,
                suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])

# ---------------------------------------------------------------- strategies

# Any unicode text, including control characters, surrogates excluded (not valid in str I/O).
any_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=400)
long_text = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=2000, max_size=6000)

_SAFE_WORDS = [
    "the", "model", "cluster", "points", "centroid", "distance", "we", "compute", "average", "data",
    "embedding", "vector", "and", "then", "assign", "each", "nearest", "so", "this", "loss", "falls",
    "training", "set", "test", "a", "for", "of", "in", "is", "classify", "images", "text", "tokens",
]
safe_word = st.sampled_from(_SAFE_WORDS)
safe_sentence = st.lists(safe_word, min_size=1, max_size=14).map(lambda ws: " ".join(ws).capitalize() + ".")
safe_prose = st.lists(safe_sentence, min_size=0, max_size=5).map(" ".join)
# Prose-like text that mixes ordinary words with punctuation, capitals and the odd symbol.
prose_ish = st.lists(
    st.one_of(safe_word, st.sampled_from([",", ".", "?", "!", ":", ";", "-", "*", "'", '"', "\n", "(", ")"]),
              st.from_regex(r"[A-Z][a-z]{1,9}", fullmatch=True), st.from_regex(r"[0-9]{1,8}", fullmatch=True)),
    max_size=60,
).map(" ".join)
mixed_text = st.one_of(any_text, prose_ish, safe_prose)
invented_name = st.from_regex(r"Q[a-z]{3,8}x", fullmatch=True)  # never an English word or a filter word

_WORDS = re.compile(r"[^\W\d_]+", re.UNICODE)


def words(text: str) -> set[str]:
    return {w.lower() for w in _WORDS.findall(text)}


def new_words(before: str, after: str) -> set[str]:
    return words(after) - words(before)


# ---------------------------------------------------------------- PG filter

# Every replacement the PG filter can make is a literal in its own source file, so any word the
# filter adds must appear there. That is what "never introduces a name" means for this filter.
PG_VOCAB = words((ROOT / "indexer" / "pg_filter.py").read_text())


@FAST
@given(mixed_text)
def test_pg_smooth_never_crashes_and_counts_honestly(text):
    out, n = pg_filter.smooth(text)
    assert isinstance(out, str) and isinstance(n, int) and n >= 0
    if n == 0:
        assert out == text
    assert pg_filter.is_pg(text) == (n == 0)


@SLOW
@given(long_text)
def test_pg_smooth_handles_long_input(text):
    out, n = pg_filter.smooth(text)
    assert isinstance(out, str) and n >= 0


@FAST
@given(mixed_text)
def test_pg_smooth_is_idempotent(text):
    once, _ = pg_filter.smooth(text)
    twice, n2 = pg_filter.smooth(once)
    assert n2 == 0 and twice == once
    assert pg_filter.is_pg(once)


@FAST
@given(mixed_text)
def test_pg_smooth_never_introduces_a_name(text):
    out, _ = pg_filter.smooth(text)
    assert new_words(text, out) <= PG_VOCAB


@FAST
@given(safe_prose, st.sampled_from(["damn", "shit", "fuck", "fucking", "bitch", "bastard", "dammit", "goddamn"]),
       safe_prose, st.sampled_from(["", ",", ".", "!", "?"]))
def test_pg_crude_word_anywhere_is_always_smoothed(before, bad, after, punct):
    text = f"{before} {bad}{punct} {after}".strip()
    out, n = pg_filter.smooth(text)
    assert n >= 1
    assert not re.search(rf"(?<![a-z]){bad}(?![a-z])", out, re.I)
    assert pg_filter.is_pg(out)


@FAST
@given(safe_prose)
def test_pg_clean_prose_is_left_alone(text):
    assert pg_filter.smooth(text) == (text, 0)


# ---------------------------------------------------------------- access-code filter

AF_VOCAB = words(af.MARKER)
code_value = st.from_regex(r"[A-Z]{4,9}", fullmatch=True)
gate = st.sampled_from(["quiz", "survey", "attendance", "access", "exam", "check-in", "canvas", "entry"])


@FAST
@given(mixed_text)
def test_access_redact_never_crashes_and_counts_honestly(text):
    out, n = af.redact(text)
    assert isinstance(out, str) and n >= 0
    assert (n == 0) == (out == text)
    assert out.count(af.MARKER) >= n


@SLOW
@given(long_text)
def test_access_redact_handles_long_input(text):
    out, n = af.redact(text)
    assert isinstance(out, str) and n >= 0
    af.slide_announces_code(text, text[:200])


@FAST
@given(mixed_text)
def test_access_redact_is_idempotent(text):
    once, _ = af.redact(text)
    assert af.redact(once) == (once, 0)


@FAST
@given(mixed_text)
def test_access_redact_never_introduces_a_name(text):
    out, _ = af.redact(text)
    assert new_words(text, out) <= AF_VOCAB


@FAST
@given(safe_prose, gate, st.sampled_from(["code is", "code:", "code =", "password is", "passcode will be"]),
       code_value, safe_prose)
def test_access_code_sentence_anywhere_is_redacted(before, gate_word, phrase, value, after):
    assume(value.lower() not in words(before) | words(after))
    text = f"{before} Your {gate_word} {phrase} {value}. {after}".strip()
    out, n = af.redact(text)
    assert n >= 1
    assert value not in out
    assert af.has_code(text)
    # The narration check catches the same sentence on the way out (gate words minus "class").
    assert narration.speech_problem(text) == "it gives an access code"


@FAST
@given(safe_prose)
def test_access_programming_talk_is_left_alone(text):
    assert af.redact(text) == (text, 0)


@FAST
@given(mixed_text)
def test_access_redacted_text_has_no_code_left(text):
    out, _ = af.redact(text)
    assert not af.has_code(out)


# ---------------------------------------------------------------- question-log scrubber (app/privacy.py)

PRIVACY_VOCAB = {"email", "number", "handle", "name", "person", "student"}


@FAST
@given(mixed_text)
def test_privacy_scrub_never_crashes(text):
    out = privacy.scrub_question(text)
    assert isinstance(out, str)


@SLOW
@given(long_text)
def test_privacy_scrub_handles_long_input(text):
    assert isinstance(privacy.scrub_question(text), str)


@FAST
@given(mixed_text)
def test_privacy_scrub_is_idempotent(text):
    once = privacy.scrub_question(text)
    assert privacy.scrub_question(once) == once


@FAST
@given(mixed_text)
def test_privacy_scrub_never_introduces_a_name(text):
    out = privacy.scrub_question(text)
    assert new_words(text, out) <= PRIVACY_VOCAB


@FAST
@given(safe_prose, st.sampled_from(["my name is", "My name is", "call me", "I'm called"]), invented_name, safe_prose)
def test_privacy_self_introduction_is_always_masked(before, intro, name, after):
    out = privacy.scrub_question(f"{before} {intro} {name}. {after}")
    assert name not in out and "[name]" in out


@FAST
@given(safe_prose, st.sampled_from(["classmate", "teammate", "partner", "roommate", "Dr.", "Prof."]),
       invented_name, safe_prose)
def test_privacy_peer_or_titled_name_is_always_masked(before, lead, name, after):
    out = privacy.scrub_question(f"{before} {lead} {name} said so. {after}")
    assert name not in out


@FAST
@given(st.from_regex(r"[a-z]{2,10}\.[a-z]{2,8}", fullmatch=True), st.sampled_from(["example.edu", "mail.example.com"]),
       safe_prose)
def test_privacy_email_is_always_masked(local, domain, around):
    out = privacy.scrub_question(f"{around} write to {local}@{domain} please")
    assert "@" not in out and local not in out and "[email]" in out


# ---------------------------------------------------------------- transcript scrubber (indexer/deidentify.py)

ROSTER = [("Pellwick", "Wilhelmina", "wpellwic"), ("Vantreek", "Marisol", "mvantree"), ("Obuya", "Quentavius", "qobuya")]
DEID_VOCAB = {"student", "person"}
STUDENT = "[student]"


@pytest.fixture(scope="module")
def scrubber(tmp_path_factory):
    import csv

    pytest.importorskip("rapidfuzz")  # an indexer-only dependency (requirements-test.txt)
    from indexer import deidentify as d

    folder = tmp_path_factory.mktemp("rosters")
    with open(folder / "CourseRoster_TEST.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Semester", "Course", "Last Name", "Preferred/First Name", "MI", "Andrew ID", "Email"])
        for last, first, aid in ROSTER:
            w.writerow(["F26", "00000", last, first, "", aid, f"{aid}@example.edu"])
    english, given_names = d.load_english_words(), d.load_given_names()
    scrub = d.build_scrub_list(d.read_rosters(folder), english)
    return d.Scrubber(scrub, english, given_names)


def deid(s, text: str) -> str:
    return s.scrub(text, Counter())


@SLOW
@given(mixed_text)
def test_deid_scrub_never_crashes(scrubber, text):
    assert isinstance(deid(scrubber, text), str)


@settings(max_examples=5, deadline=None, derandomize=True,
          suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
@given(long_text)
def test_deid_scrub_handles_long_input(scrubber, text):
    assert isinstance(deid(scrubber, text), str)


@SLOW
@given(mixed_text)
def test_deid_scrub_is_idempotent(scrubber, text):
    once = deid(scrubber, text)
    assert deid(scrubber, once) == once


@SLOW
@given(mixed_text)
def test_deid_scrub_never_introduces_a_name(scrubber, text):
    out = deid(scrubber, text)
    assert new_words(text, out) <= DEID_VOCAB


@SLOW
@given(safe_prose, st.sampled_from([r[1] for r in ROSTER] + [r[0] for r in ROSTER] + [f"{r[1]} {r[0]}" for r in ROSTER]),
       safe_prose)
def test_deid_roster_name_anywhere_is_masked(scrubber, before, name, after):
    out = deid(scrubber, f"{before} I agree with {name} on that. {after}")
    for part in name.split():
        assert part not in out
    assert STUDENT in out


# ---------------------------------------------------------------- narration validators (app/narration.py)

SLIDE = "s1"
MATERIAL = ("K-means assigns each point to the nearest centroid, then moves every centroid to the average "
            "of its assigned points, and repeats until the clusters stop changing.")
GROUNDING = narration.Grounding.build("how does k-means work", [MATERIAL])
grounded_narration = st.lists(
    st.sampled_from(["k-means", "assigns", "each", "point", "to", "the", "nearest", "centroid", "then", "moves",
                     "every", "average", "of", "its", "assigned", "points", "and", "repeats", "until", "clusters",
                     "stop", "changing", "so", "we"]),
    min_size=3, max_size=60,
).map(" ".join)
whitespace = st.text(alphabet=" \t\n\r", min_size=1, max_size=4)


def reply(text: str, follow_ups=None) -> str:
    return json.dumps({"segments": [{"slide_id": SLIDE, "narration": text}], "follow_ups": follow_ups or []})


@FAST
@given(mixed_text)
@example("```json\n{\"segments\": []}\n```")
@example("{\"segments\": [{\"slide_id\": null}]}")
def test_validate_raw_text_never_crashes(raw):
    try:
        out, follow = narration.validate(raw, [SLIDE], GROUNDING)
    except narration.ValidationError:
        return
    assert set(out) <= {SLIDE} and len(follow) <= 2


json_value = st.recursive(
    st.none() | st.booleans() | st.integers() | st.floats(allow_nan=False) | mixed_text,
    lambda inner: st.lists(inner, max_size=4) | st.dictionaries(st.sampled_from(["slide_id", "narration", "segments",
                                                                                  "follow_ups", "x"]), inner, max_size=4),
    max_leaves=12,
)


@FAST
@given(st.dictionaries(st.sampled_from(["segments", "follow_ups", "other"]), json_value, max_size=3))
def test_validate_any_json_shape_never_crashes(data):
    try:
        narration.validate(json.dumps(data), [SLIDE], GROUNDING)
    except narration.ValidationError:
        pass


@FAST
@given(mixed_text)
def test_validate_output_never_introduces_words(text):
    try:
        out, _ = narration.validate(reply(text), [SLIDE], None)
    except narration.ValidationError:
        return
    assert new_words(text, out[SLIDE]) == set()
    assert narration.speech_problem(out[SLIDE]) is None


@FAST
@given(mixed_text)
def test_speech_problem_never_crashes_and_clean_speech_is_idempotent(text):
    narration.speech_problem(text)
    once = narration.clean_speech(text)
    assert narration.clean_speech(once) == once
    assert "—" not in once and "–" not in once and "  " not in once


@FAST
@given(grounded_narration, st.data())
def test_valid_narration_survives_whitespace_changes(text, data):
    out, _ = narration.validate(reply(text), [SLIDE], GROUNDING)
    spaced = data.draw(whitespace) + data.draw(whitespace).join(text.split(" ")) + data.draw(whitespace)
    out2, _ = narration.validate(reply(spaced), [SLIDE], GROUNDING)
    assert out2 == out


@FAST
@given(grounded_narration, st.sampled_from(["[student]", "[person]", "[ Student ]", "[name]", "[redacted]"]),
       st.integers(min_value=0, max_value=60))
def test_narration_with_a_name_token_is_always_rejected(text, token, at):
    ws = text.split(" ")
    ws.insert(at % (len(ws) + 1), token)
    with pytest.raises(narration.ValidationError, match="token"):
        narration.validate(reply(" ".join(ws)), [SLIDE], GROUNDING)


@FAST
@given(grounded_narration, st.sampled_from(["damn", "shit", "fuck", "bitch", "wtf", "f***", "dammit"]),
       st.integers(min_value=0, max_value=60))
def test_narration_with_a_crude_word_is_always_rejected(text, bad, at):
    ws = text.split(" ")
    ws.insert(at % (len(ws) + 1), bad)
    with pytest.raises(narration.ValidationError, match="PG"):
        narration.validate(reply(" ".join(ws)), [SLIDE], None)


@FAST
@given(grounded_narration, gate, code_value)
def test_narration_with_an_access_code_is_always_rejected(text, gate_word, value):
    with pytest.raises(narration.ValidationError, match="access code"):
        narration.validate(reply(f"{text}. The {gate_word} code is {value}."), [SLIDE], None)


@FAST
@given(st.lists(mixed_text, max_size=6))
def test_follow_ups_are_capped_and_safe(follow):
    _, out = narration.validate(reply("the nearest centroid", follow), [SLIDE], GROUNDING)
    assert len(out) <= 2
    for f in out:
        assert f and len(f) <= 150 and narration.speech_problem(f) is None
        # each one is an input follow-up, cleaned and cut at 150 characters: nothing added
        assert any(narration.clean_speech(x)[:150] == f for x in follow)
