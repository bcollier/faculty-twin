"""Access codes must never reach the index (found by the real-question eval, Oct 7)."""

from indexer import assessment_filter as af


def test_transcript_sentence_with_quiz_code_is_replaced():
    text = "Hangnails, those kinds of things. And your quiz here, access code is PDOM. And the assignment is due."
    out, n = af.redact(text)
    assert n == 1
    assert "PDOM" not in out
    assert af.MARKER in out
    assert out.startswith("Hangnails, those kinds of things.")
    assert out.endswith("And the assignment is due.")


def test_survey_code_in_quotes_is_replaced():
    out, n = af.redact("One class survey. It should ask you for a code and the code is “day one.” And we'll end class there.")
    assert n == 1 and "day one" not in out


def test_slide_announcing_a_code_is_detected():
    assert af.slide_announces_code("Quiz Code:", "Quiz Code:  FIVETRIBES", "")
    assert af.slide_announces_code("", "", "Attendance code: BLUE42")


def test_programming_talk_is_left_alone():
    for text in [
        "The more complex you make it, the better the code is compared to the verbal version.",
        "Code is regenerated from it; tests prove the match.",
        "the written spec is the source of truth and the code is generated from it",
        "Canvas quiz code at the end.",
        "Something like 500 lines of code is enough to query this.",
    ]:
        out, n = af.redact(text)
        assert n == 0 and out == text, text
        assert not af.slide_announces_code(text)


def test_empty_input():
    assert af.redact("") == ("", 0)
    assert af.redact(None) == ("", 0)
    assert not af.has_code(None)
