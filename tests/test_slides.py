"""Tests for indexer/slides.py using synthetic data only (no archive, no roster).

Run: uv run --no-project --with pytest --with nbformat python -m pytest tests/test_slides.py -q
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "indexer"))

import slides  # noqa: E402
from slides import NameScrubber, PptxSlide  # noqa: E402

# Invented names, not from any roster.
FAKE_ROSTER = [
    {"Last Name": "Quenwick", "Preferred/First Name": "Zorblat", "Andrew ID": "zquenwic", "Email": "zquenwic@example.edu"},
    {"Last Name": "Brightwater-Xu", "Preferred/First Name": "Ilsabet", "Andrew ID": "ibx", "Email": "ibx@example.edu"},
]


def slide(i, text, hidden=False, notes="", title=""):
    return PptxSlide(i, hidden, title, text, notes)


def test_align_skips_hidden_slide_and_dropped_page():
    pdf = ["intro to clustering", "k means steps assign update", "choosing k elbow method", "summary next week"]
    pptx = [
        slide(1, "intro to clustering"),
        slide(2, "secret backup slide", hidden=True),
        slide(3, "k means steps assign update"),
        slide(4, "a slide cut before export"),
        slide(5, "choosing k elbow method"),
        slide(6, "summary next week"),
    ]
    got = [j for j, _ in slides.align(pdf, pptx)]
    assert got == [0, 2, 4, 5]


def test_positional_trusts_image_pages_inside_unbroken_run():
    pdf = ["intro", "", "", "wrap up"]
    pptx = [slide(1, "intro"), slide(2, ""), slide(3, ""), slide(4, "wrap up")]
    al = slides.align(pdf, pptx)
    assert [j for j, _ in al] == [0, 1, 2, 3]
    assert slides.positional_ok(al, pptx) == [False, True, True, False]


def test_positional_refuses_ambiguous_run():
    # Two image pages but three candidate slides between the anchors: no notes.
    pdf = ["intro", "", "", "wrap up"]
    pptx = [slide(1, "intro"), slide(2, ""), slide(3, ""), slide(4, ""), slide(5, "wrap up")]
    al = slides.align(pdf, pptx)
    assert slides.positional_ok(al, pptx)[1:3] == [False, False]


def test_scrubber_full_name_and_ids_replaced():
    s = NameScrubber(FAKE_ROSTER, dictionary=set())
    text, flags = s.check("Thanks to Zorblat Quenwick and zquenwic@example.edu for this.")
    assert "Zorblat" not in text and "Quenwick" not in text and "zquenwic" not in text
    assert text.count("[student]") == 2
    assert "student_names_possible" in flags


def test_scrubber_surname_or_first_name_alone_only_flags():
    s = NameScrubber(FAKE_ROSTER, dictionary=set())
    text, flags = s.check("Quenwick et al. showed it; Zorblat agreed.")
    assert text == "Quenwick et al. showed it; Zorblat agreed."
    assert "student_surname_possible" in flags and "student_first_name_possible" in flags


def test_scrubber_ignores_dictionary_words_and_lowercase():
    roster = [{"Last Name": "Young", "Preferred/First Name": "Will", "Andrew ID": "", "Email": ""}]
    s = NameScrubber(roster, dictionary={"young", "will"})
    _, flags = s.check("Young companies will grow.")
    assert flags == []


def test_secret_redaction():
    text, hit = slides.redact_secrets('client = OpenAI(api_key="' + "s" + "k-" + "a" * 40 + '")')
    assert hit and "[REDACTED_KEY]" in text


def test_content_flags():
    assert "in_the_news" in slides.content_flags("AI Methods in the News: presentation schedule")
    assert "in_the_news" not in slides.content_flags("the classic way to end up in the news")
    assert "in_the_news" in slides.content_flags("Presentation Sign Up - Al in the News")  # OCR reads I as l
    assert "copyright_notice" in slides.content_flags("© 2024 Some Publisher. All rights reserved.")
    assert "third_party_source" in slides.content_flags("Source: Gartner survey")


def test_titles_and_boilerplate():
    pages = ["Agenda\nCarnegie Mellon Tepper\n3", "K-means   step 1\nCarnegie Mellon Tepper", "Carnegie Mellon Tepper\n12"]
    boiler = slides.boilerplate_lines(pages)
    assert "carnegie mellon tepper" in boiler
    stripped = [slides.strip_boilerplate(t, boiler) for t in pages]
    assert slides.pick_title(stripped[0]) == "Agenda"
    assert slides.pick_title(stripped[1]) == "K-means"
    assert slides.pick_title(stripped[2]) == ""
    assert slides.pick_title("https://example.com/x\nReal title") == "Real title"
    assert slides.pick_title("body", pptx_title="Choosing k") == "Choosing k"
    assert slides.pick_title("AI Methods for Social and\nVisual Data\nComputer Vision") == "AI Methods for Social and Visual Data"


def test_notebook_extraction_strips_outputs(tmp_path, monkeypatch):
    # nbformat is an indexer-only dependency (not in requirements.txt); run with --with nbformat.
    nbformat = pytest.importorskip("nbformat")

    folder = tmp_path / "70445" / "01 2026-08-25 Demo"
    (folder / "notebooks").mkdir(parents=True)
    nb = nbformat.v4.new_notebook()
    code = nbformat.v4.new_code_cell("print(1)")
    code.outputs = [nbformat.v4.new_output("stream", text="1\n")]
    nb.cells = [nbformat.v4.new_markdown_cell("# Step 1"), code, nbformat.v4.new_code_cell("x = 2")]
    nbformat.write(nb, str(folder / "notebooks" / "demo.ipynb"))
    monkeypatch.setattr(slides, "ARCHIVE", tmp_path)

    sess = slides.Session("70445", 1, "2026-08-25", "Demo", folder)
    n_nb, n_cells, _ = slides.process_notebooks(sess, NameScrubber([], set()))
    assert (n_nb, n_cells) == (1, 2)
    cells = json.loads((tmp_path / "_build" / "code" / "70445" / "s01.json").read_text())
    assert cells[0]["markdown_above"] == "# Step 1" and cells[0]["source"] == "print(1)"
    assert cells[1]["markdown_above"] == ""
    assert "outputs" not in cells[0]
    assert cells[0]["cell_id"] == "70445-s01-nb1-c001"
