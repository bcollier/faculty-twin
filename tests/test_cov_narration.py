"""app/narration.py validators: every rule the voice depends on, one case each, plus fallbacks."""

from __future__ import annotations

import json

import pytest

from app import config, narration

IDS = ["70445-s01-002", "70445-s01-003"]


def reply(segments, follow_ups=None):
    data = {"segments": segments}
    if follow_ups is not None:
        data["follow_ups"] = follow_ups
    return json.dumps(data)


def seg(text, sid="70445-s01-002"):
    return {"slide_id": sid, "narration": text}


# ---------------------------------------------------------------- JSON extraction

def test_json_inside_chatter_is_found():
    raw = "Sure! Here it is:\n" + reply([seg("Apples are fruit.")]) + "\nHope that helps."
    out, follow = narration.validate(raw, IDS)
    assert out == {"70445-s01-002": "Apples are fruit."} and follow == []


def test_braces_without_json_is_rejected():
    with pytest.raises(narration.ValidationError, match="not JSON"):
        narration.validate("{ this is not json }", IDS)


@pytest.mark.parametrize("raw,match", [
    (json.dumps([1, 2]), "missing segments"),
    (json.dumps({"segments": ["text"]}), "not an object"),
    (reply([{"slide_id": "70445-s01-002", "narration": 5}]), "empty narration"),
    (reply([{"slide_id": "70445-s01-002", "narration": "   "}]), "empty narration"),
    (reply([{"narration": "No id."}]), "unknown slide_id"),
])
def test_structure_rules(raw, match):
    with pytest.raises(narration.ValidationError, match=match):
        narration.validate(raw, IDS)


def test_character_cap_independent_of_words():
    long_words = ("supercalifragilistic " * 50).strip()  # 50 words, ~1000 characters
    assert narration.word_count(long_words) <= config.NARRATION_MAX_WORDS
    with pytest.raises(narration.ValidationError, match="characters"):
        narration.validate(reply([seg(long_words)]), IDS)


@pytest.mark.parametrize("text,spoken", [
    ("See www.example.com for more.", "See the link on the slide for more."),
    ("The notebook is at https://colab.example/x.", "The notebook is at the link on the slide."),
    ("Check kmeans.io for a demo.", "Check the link on the slide for a demo."),
    ("Install it from https://docs.n8n.io/hosting/installation/npm/, then open the editor.",
     "Install it from the link on the slide, then open the editor."),
    ("The free version is at n8n.io (or docs.n8n.io).", "The free version is at the link on the slide (or the link on the slide)."),
    ("Try n8n.io or make.com for this.", "Try the links on the slide for this."),
])
def test_web_addresses_become_the_link_on_the_slide(text, spoken):
    # Changed Oct 8: the voice never speaks a web address, but a slide that shows one (n8n slide
    # 45884-s08-069) no longer throws away the whole narration and falls back to the notes.
    out, _ = narration.validate(reply([seg(text)]), IDS)
    assert out["70445-s01-002"] == spoken
    assert narration.speakable_links(spoken) == spoken
    assert not narration._URLISH.search(spoken)


def test_web_address_rule_still_holds_after_the_swap(monkeypatch):
    # The swap runs before validation; the old check still runs after it, so anything it misses is refused.
    monkeypatch.setattr(narration, "speakable_links", lambda text: text)
    with pytest.raises(narration.ValidationError, match="web address"):
        narration.validate(reply([seg("See www.example.com for more.")]), IDS)


def test_fallback_narration_never_speaks_a_web_address():
    rec = {"id": "45884-s08-069", "title": "Agents Toolkits", "notes": "",
           "transcript": "", "text": "Agents Toolkits\n\nhttps://n8n.io/"}
    assert narration.fallback_narration(rec) == "Agents Toolkits the link on the slide"


@pytest.mark.parametrize("text,why", [
    ("This damn model overfits.", "crude"),
    ("The quiz access code is BLUE42 today.", "access code"),
    ("The [student] asked about apples.", "token"),
    ("As [ Person ] said, apples matter.", "token"),
])
def test_speech_problems_are_rejected(text, why):
    assert why in narration.speech_problem(text)
    with pytest.raises(narration.ValidationError, match="not allowed"):
        narration.validate(reply([seg(text)]), IDS)


@pytest.mark.parametrize("text", [
    "In class, the code is short and the idea is simple.",
    "Hello, this is the shell of the idea.",
    "Assessment of the classic method.",
])
def test_ordinary_words_are_not_speech_problems(text):
    assert narration.speech_problem(text) is None


def test_follow_ups_are_filtered_trimmed_and_capped():
    follow = [
        "What is an apple?",
        "",
        7,
        "See www.example.com?",
        "Why the damn pears?",
        "x" * 300,
        "A fourth good one?",
    ]
    _out, ups = narration.validate(reply([seg("Apples are fruit.")], follow), IDS)
    assert ups == ["What is an apple?", "x" * 150]


def test_follow_ups_not_a_list_are_ignored():
    _out, ups = narration.validate(reply([seg("Apples are fruit.")], "What next?"), IDS)
    assert ups == []


def test_one_bad_segment_fails_the_whole_reply():
    raw = reply([seg("Apples are fruit."), seg("This damn slide.", "70445-s01-003")])
    with pytest.raises(narration.ValidationError):
        narration.validate(raw, IDS)


def test_clean_speech_removes_em_dashes_and_extra_space():
    out, _ = narration.validate(reply([seg("Apples — and  pears\n\nare fruit.")]), IDS)
    assert out["70445-s01-002"] == "Apples, and pears are fruit."


# ---------------------------------------------------------------- fallback narration

def test_fallback_prefers_notes_then_transcript_then_text_then_title():
    assert narration.fallback_narration({"notes": "Notes.", "transcript": "T.", "text": "X."}) == "Notes."
    assert narration.fallback_narration({"notes": " ", "transcript": "Transcript.", "text": "X."}) == "Transcript."
    assert narration.fallback_narration({"text": "Slide text."}) == "Slide text."
    assert narration.fallback_narration({"title": "Title"}) == "Title"
    assert narration.fallback_narration({}) == "This slide."


def test_fallback_cuts_at_a_sentence_when_it_can():
    notes = "First sentence here. " * 20 + "word " * 100
    out = narration.fallback_narration({"notes": notes})
    assert out == ("First sentence here. " * 20).strip()
    no_stops = "word " * 200
    out2 = narration.fallback_narration({"notes": no_stops})
    assert out2.endswith("...") and narration.word_count(out2) <= narration.FALLBACK_WORDS + 1


def test_narrate_uses_the_active_provider_when_none_given(monkeypatch):
    from app import settings_store

    settings_store.put({"provider": "openai", "model": "gpt-6-luna"})
    seen = {}

    def fake(system, user, max_tokens, provider=None, model=None):
        seen["pm"] = (provider, model)
        return "not json"

    slides = [{"id": "a", "title": "Apples", "text": "Apples.", "notes": "Apple notes."}]
    result = narration.narrate("apples?", slides, complete=fake)
    assert seen["pm"] == ("openai", "gpt-6-luna")
    assert result.source == "fallback" and result.narrations == {"a": "Apple notes."}
    assert len(result.errors) == 2


def test_narrate_fills_skipped_slides_with_notes():
    slides = [
        {"id": "a", "title": "Apples", "text": "Apples are fruit.", "notes": "Apple notes."},
        {"id": "b", "title": "Pears", "text": "Pears are fruit.", "notes": "Pear notes."},
    ]

    def fake(system, user, max_tokens, provider=None, model=None):
        return reply([{"slide_id": "a", "narration": "Apples are fruit."}])

    result = narration.narrate("apples?", slides, provider="anthropic", model="m", complete=fake)
    assert result.source == "llm"
    assert result.narrations == {"a": "Apples are fruit.", "b": "Pear notes."}
