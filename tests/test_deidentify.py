"""Tests for indexer/deidentify.py.

Every name here is INVENTED for the test (synthetic roster, synthetic VTT).
No real student data is used or needed.

Run: uv run --no-project --with rapidfuzz --with nicknames --with pytest pytest tests/test_deidentify.py
"""

import csv
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import deidentify as d  # noqa: E402

SYNTHETIC_ROSTER = [
    # Last Name, Preferred/First Name, Andrew ID
    ("Pellwick", "Wilhelmina", "wpellwic"),
    ("Vantreek", "Marisol", "mvantree"),
    ("Brightwater", "Will", "wbrightw"),
    ("Obuya", "Quentavius", "qobuya"),
]


def write_roster(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "CourseRoster_TEST.csv"
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Semester", "Course", "Last Name", "Preferred/First Name", "MI", "Andrew ID", "Email"])
        for last, first, aid in SYNTHETIC_ROSTER:
            w.writerow(["F26", "00000", last, first, "", aid, f"{aid}@example.edu"])
    return path


@pytest.fixture(scope="module")
def words():
    return d.load_english_words(), d.load_given_names()


@pytest.fixture(scope="module")
def scrubber(tmp_path_factory, words):
    english, given = words
    roster_dir = tmp_path_factory.mktemp("rosters")
    write_roster(roster_dir)
    people = d.read_rosters(roster_dir)
    scrub = d.build_scrub_list(people, english, extra_terms=["Zorbek"], person_terms=["Quillfeather"])
    return d.Scrubber(scrub, english, given, keep_terms=["Jetstream"])


def masked(s: d.Scrubber, text: str) -> tuple[str, Counter]:
    c: Counter = Counter()
    return s.scrub(text, c), c


# --- roster students -> [student] -----------------------------------------
def test_roster_first_and_full_name(scrubber):
    out, c = masked(scrubber, "Thanks, Marisol. And Marisol Vantreek had a good point.")
    assert "Marisol" not in out and "Vantreek" not in out
    assert out.count("[student]") == 2
    assert c["roster_exact"] >= 2


def test_possessive_keeps_suffix(scrubber):
    out, _ = masked(scrubber, "Building on Vantreek's point about embeddings.")
    assert out == "Building on [student]'s point about embeddings."


def test_nickname_of_roster_name(scrubber):
    out, c = masked(scrubber, "Minnie, what do you think about this one?")
    assert out.startswith("[student],")
    assert c["roster_nickname"] == 1


def test_short_form_of_long_first_name(scrubber):
    out, _ = masked(scrubber, "So Quent asked about tokenizers.")
    assert "Quent" not in out


def test_asr_misspelling_fuzzy(scrubber):
    out, c = masked(scrubber, "I think Pellwik made a similar argument.")
    assert "Pellwik" not in out
    assert c["roster_fuzzy"] == 1


def test_andrew_id(scrubber):
    out, _ = masked(scrubber, "Email me from wpellwic and I will add you.")
    assert "wpellwic" not in out


def test_common_word_first_name(scrubber):
    # "Will" is also an English word: keep it at the start of a question, mask it mid-sentence.
    out, _ = masked(scrubber, "Will this model overfit?")
    assert out == "Will this model overfit?"
    out, _ = masked(scrubber, "I agree with Will on that.")
    assert out == "I agree with [student] on that."


def test_override_student_term(scrubber):
    out, _ = masked(scrubber, "Nice job, Zorbek.")
    assert out == "Nice job, [student]."


# --- people addressed in class but not on the roster -> [student] ---------
def test_vocative_non_roster_name(scrubber):
    out, _ = masked(scrubber, "Go ahead, Zandrikka.")
    assert out == "Go ahead, [student]."
    out, _ = masked(scrubber, "Thalorin, what do you think?")
    assert out.startswith("[student],")


def test_self_introduction_and_handoff(scrubber):
    out, _ = masked(scrubber, "My name is Meadow, and I'm here with QZ and Harbor Dalebrook. Next I'll pass it on to Ravindrel.")
    for name in ("Meadow", "QZ", "Harbor", "Dalebrook", "Ravindrel"):
        assert name not in out
    assert "[person]" not in out


def test_display_name_label_in_caption(scrubber):
    out, _ = masked(scrubber, "Okay. Dravenna Quillos: can you hear me now?")
    assert "Dravenna" not in out and "Quillos" not in out


def test_roll_call_single_name(scrubber):
    out, _ = masked(scrubber, "Yorvanni?")
    assert out == "[student]?"


# --- everyone else -> [person] ----------------------------------------------
def test_public_figure_is_masked_as_person(scrubber):
    out, _ = masked(scrubber, "Geoffrey Hinton shared the Nobel Prize, and Hinton keeps warning about it.")
    assert "Hinton" not in out and "Geoffrey" not in out
    assert out.count("[person]") == 2
    assert "[student]" not in out


def test_given_name_mid_sentence_is_person(scrubber):
    out, c = masked(scrubber, "My neighbor Gertrude likes chatbots.")
    assert "Gertrude" not in out
    assert "[person]" in out


def test_title_name(scrubber):
    out, _ = masked(scrubber, "There is a paper by Dr. Quorvath on this.")
    assert "Quorvath" not in out


def test_override_person_term(scrubber):
    out, _ = masked(scrubber, "The founder, Quillfeather, gave a talk.")
    assert "Quillfeather" not in out and "[person]" in out


def test_everyday_word_first_name_joins_the_mask(scrubber):
    out, _ = masked(scrubber, "The former provost was Mark Quillfeather, I think.")
    assert out == "The former provost was [person], I think."


# --- things that are not people stay ----------------------------------------
def test_companies_products_models_stay(scrubber):
    text = "Claude, OpenAI, Anthropic and ResNet are not people, and neither is Pittsburgh."
    out, c = masked(scrubber, text)
    assert out == text and not c


def test_claude_the_person_vs_claude_the_model(scrubber):
    out, _ = masked(scrubber, "Claude Shannon invented information theory; Claude is a model.")
    assert out == "[person] invented information theory; Claude is a model."


def test_eponyms_stay(scrubber):
    text = "The Turing test, Naive Bayes, a Markov chain and Moore's law are concepts."
    out, _ = masked(scrubber, text)
    assert out == text


def test_instructor_forms_stay(scrubber):
    text = "I'm Ben. Professor Collier, or Dr. Collier, is Benjamin Collier."
    out, _ = masked(scrubber, text)
    assert out == text


def test_keep_term_override(scrubber):
    text = "Paste the key next to Jetstream, please."
    out, _ = masked(scrubber, text)
    assert out == text


# --- VTT parsing, merging, labels -------------------------------------------
SYNTHETIC_VTT = """WEBVTT

1
00:00:01.000 --> 00:00:03.000
Ben Collier: Hi, everyone.

2
00:00:10.000 --> 00:00:40.000
Ben Collier: All right, good afternoon. Today we are going to talk about how gradient descent works, why the learning rate matters so much for training, and what happens when you pick a value that is far too large for the problem in front of you

3
00:00:40.100 --> 00:01:05.000
Ben Collier: because the loss will bounce around and never settle into a minimum, which is what we saw last week with the housing data set and the small neural network we trained together in class.

4
00:01:06.000 --> 00:01:09.000
Ben Collier: What do you think happens if it is too small?

5
00:01:10.000 --> 00:01:13.000
Ben Collier: It takes forever to converge, I think.

6
00:01:14.000 --> 00:01:45.000
Ben Collier: Exactly, thanks Marisol. It takes forever, and you burn compute while the model barely moves, so in practice we use schedules that start larger and shrink the step size as training goes on, which gives us the best of both worlds for most problems.
"""


def test_parse_vtt_and_merge():
    cues = d.parse_vtt(SYNTHETIC_VTT)
    assert len(cues) == 6
    assert cues[0] == {"start": 1.0, "end": 3.0, "text": "Hi, everyone."}
    assert not any(c["text"].startswith("Ben Collier:") for c in cues)
    merged = d.merge_cues(cues)
    assert len(merged) == 5  # cue 2 ends mid-sentence and joins cue 3
    assert merged[1]["start"] == 10.0 and merged[1]["end"] == 65.0


def test_speaker_heuristics_and_overrides():
    cues = d.merge_cues(d.parse_vtt(SYNTHETIC_VTT))
    labels = d.label_speakers(cues)
    assert labels[0] == "unclear"          # chatter before the lecture starts
    assert labels[1] == "instructor"       # long explanation
    assert labels[3] == "unclear"          # short answer right after a question
    assert labels[4] == "instructor"
    n = d.apply_label_overrides(cues, labels, [{"from": 66, "to": 74, "speaker": "student"}])
    assert n == 2 and labels[2] == "student" and labels[3] == "student"


def test_whisper_hallucination_detected():
    fake = [{"start": i * 30.0, "end": i * 30.0 + 29.98, "text": "Thank you."} for i in range(100)]
    assert d.whisper_problem(fake) is not None
    real = d.parse_vtt(SYNTHETIC_VTT)
    assert d.whisper_problem(real) is None


# --- sweep -------------------------------------------------------------------
def test_sweep_finds_raw_names_and_passes_scrubbed_text(scrubber, words):
    english, given = words
    raw = ["Thanks, Marisol.", "Wilhelmina Pellwick asked a question.", "Andrew Ng agrees with Gertrude."]
    hits = d.sweep_texts(raw, scrubber.s, english, given)
    kinds = Counter(k for k, _ in hits)
    assert kinds["roster"] >= 3 and kinds["known_person"] >= 1 and kinds["given_name"] >= 1
    # the masked context never shows the matched token
    assert all("Marisol" not in ctx for _, ctx in hits if "<R" in ctx and "Thanks" in ctx)
    clean = [scrubber.scrub(t) for t in raw]
    assert [h for h in d.sweep_texts(clean, scrubber.s, english, given) if h[0] not in d.REVIEW_ONLY_KINDS] == []


# --- end to end on a synthetic archive ---------------------------------------
def test_process_all_end_to_end(tmp_path):
    archive = tmp_path / "archive"
    sess = archive / "00-000 Test Course" / d.TERM / "01 2026-08-25 Gradient Descent"
    sess.mkdir(parents=True)
    (sess / "transcript_raw.vtt").write_text(SYNTHETIC_VTT)
    (sess / "transcript_drive.md").write_text("[1:14] Exactly, thanks Marisol Vantreek.\n")
    write_roster(archive / "_private" / "rosters")
    summary = d.process_all(archive, verbose=False)
    assert summary[0]["replacements"] >= 1 and summary[0]["source"] == "zoom_vtt"
    doc = json.loads((archive / "_build" / "transcripts" / "00000" / "s01.json").read_text())
    assert doc["course"] == "00000" and doc["session"] == 1 and doc["date"] == "2026-08-25"
    assert all(set(c) == {"start", "end", "text", "speaker"} for c in doc["cues"])
    blob = json.dumps(doc) + (archive / "_build" / "transcripts" / "00000" / "s01.drive.md").read_text()
    for last, first, aid in SYNTHETIC_ROSTER:
        assert last not in blob and first not in blob and aid not in blob
    review = (archive / "_build" / "transcripts" / "00000" / "s01.review.md").read_text()
    assert "Marisol" not in review and "Replacements by rule" in review
    result = d.sweep(archive, verbose=False)
    assert all(result[k] == 0 for k in d.SWEEP_KINDS if k not in d.REVIEW_ONLY_KINDS)
