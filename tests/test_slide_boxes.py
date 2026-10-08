"""Slide word boxes for the read-along highlights (indexer/slide_boxes.py) and their upload and timings tools.

A synthetic PDF is written by hand (no third-party PDF library); the real
`pdftotext -bbox-layout` runs on it when Poppler is installed. OCR is faked.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import httpx
import pytest

from indexer import pregenerate, slide_boxes, upload

PDFTOTEXT = shutil.which("pdftotext")


def make_pdf(path: Path, pages: list[list[tuple[float, float, str]]], width: int = 400, height: int = 200) -> Path:
    """A minimal PDF: each page has Helvetica 20 pt text at (x, y from the bottom) positions."""
    objects: list[bytes] = []
    n_pages = len(pages)
    font_id = 3 + 2 * n_pages
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>".encode())
    for i, lines in enumerate(pages):
        content = "".join(f"BT /F1 20 Tf {x} {y} Td ({text}) Tj ET\n" for x, y, text in lines).encode()
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                       f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {4 + 2 * i} 0 R >>".encode())
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream")
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for k, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{k} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))
    return path


def passthrough(text: str) -> tuple[str, list[str]]:
    return text, []


BBOX = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml"><head><title>t</title></head><body><doc>
<page width="400.000000" height="200.000000"><flow><block>
<line xMin="40" yMin="20" xMax="200" yMax="40">
<word xMin="40.000000" yMin="20.000000" xMax="120.000000" yMax="40.000000">Gradient</word>
<word xMin="125.000000" yMin="20.000000" xMax="200.000000" yMax="40.000000">descent</word></line>
<line xMin="40" yMin="60" xMax="420" yMax="80">
<word xMin="40.000000" yMin="60.000000" xMax="420.000000" yMax="80.000000">overflow&amp;co</word>
<word xMin="1" yMin="1" xMax="2" yMax="2"> </word></line>
</block></flow></page>
<page width="0" height="0"></page>
</doc></body></html>"""


def test_parse_bbox_layout_normalizes_from_the_top_left():
    pages = slide_boxes.parse_bbox_layout(BBOX)
    assert len(pages) == 2 and pages[1] == []
    assert pages[0] == [["Gradient", 0.1, 0.1, 0.3, 0.2, 0],
                        ["descent", 0.3125, 0.1, 0.5, 0.2, 0],
                        ["overflow&co", 0.1, 0.3, 1.0, 0.4, 1]]  # clipped to the page


@pytest.mark.skipif(PDFTOTEXT is None, reason="pdftotext (Poppler) is not installed")
def test_real_pdf_boxes(tmp_path):
    pdf = make_pdf(tmp_path / "deck.pdf", [[(40, 150, "Gradient descent"), (40, 60, "Learning rate")], []])
    pages = slide_boxes.pdf_word_boxes(pdf)
    assert len(pages) == 2 and pages[1] == []
    words = pages[0]
    assert [w[0] for w in words] == ["Gradient", "descent", "Learning", "rate"]
    g = words[0]
    assert g[1] == pytest.approx(0.1, abs=0.01)  # x = 40 / 400
    # "Gradient" sits 150 pt above the bottom of a 200 pt page: near the top (y from the top left)
    assert 0.1 < g[2] < g[4] < 0.3 and g[3] > g[1]
    assert words[2][5] != words[0][5] and words[2][2] > g[4]  # a lower line, further down


def test_clean_words_drops_lines_the_filters_change():
    def scrub(text):
        if "Jane Roe" in text:
            return text.replace("Jane Roe", "[student]"), ["student_names_possible"]
        if text == "Roe":
            return text, ["student_surname_possible"]
        return text, []

    words = [["Thanks", 0, 0, .1, .1, 0], ["Jane", .1, 0, .2, .1, 0], ["Roe", .2, 0, .3, .1, 0],
             ["Gradient", 0, .2, .1, .3, 1], ["descent", .1, .2, .2, .3, 1],
             ["key", 0, .4, .1, .5, 2], ["sk-" + "x" * 20, .1, .4, .2, .5, 2],
             ["Prof", 0, .6, .1, .7, 3], ["Roe", .1, .6, .2, .7, 3],
             ["What", 0, .8, .1, .9, 4], ["the", .1, .8, .2, .9, 4], ["hell", .2, .8, .3, .9, 4]]
    kept = slide_boxes.clean_words(words, scrub)
    assert [w[0] for w in kept] == ["Gradient", "descent", "Prof"]
    assert slide_boxes.clean_words(words, None) == []  # no scrubber, nothing written


def test_reading_order_sorts_ocr_lines():
    words = [["second", .5, .5, .6, .55, 0], ["first", .1, .1, .2, .15, 1], ["b", .3, .5, .35, .55, 0]]
    assert slide_boxes.reading_order(words) == [["first", .1, .1, .2, .15, 0], ["b", .3, .5, .35, .55, 1],
                                                ["second", .5, .5, .6, .55, 1]]


def test_build_deck_pdf_ocr_flagged_and_current(tmp_path, monkeypatch):
    out = tmp_path / "s01"
    out.mkdir()
    pdf = tmp_path / "deck.pdf"
    pdf.write_bytes(b"%PDF fake")
    pages = [[["Gradient", .1, .1, .2, .2, 0], ["descent", .2, .1, .3, .2, 0], ["works", .3, .1, .4, .2, 0]],
             [],  # image-only: OCR
             [["Hi", .1, .1, .2, .2, 0]],  # too few words, and OCR finds none
             [["Secret", .1, .1, .2, .2, 0]] * 3]
    calls = {"pdf": 0, "ocr": []}

    def fake_pdf(path):
        calls["pdf"] += 1
        return pages

    def fake_ocr(images, cache):
        calls["ocr"].append([p.name for p in images])
        return {str(out / "d-002.webp"): [["Neural", .1, .5, .3, .6, 0]]}

    monkeypatch.setattr(slide_boxes, "pdf_word_boxes", fake_pdf)
    monkeypatch.setattr(slide_boxes, "ocr_word_boxes", fake_ocr)
    (out / "d-004.boxes.json").write_text("{}")  # an older file for a page now flagged: removed
    ids = ["d-001", "d-002", "d-003", "d-004"]
    counts = slide_boxes.build_deck(pdf, out, ids, passthrough, {"d-004"})
    assert counts == {"pdf": 1, "ocr": 1, "none": 1, "skipped": 0, "flagged": 1}
    assert calls["ocr"] == [["d-002.webp", "d-003.webp"]]
    first = json.loads((out / "d-001.boxes.json").read_text())
    assert first == {"v": 1, "src": "pdf", "words": pages[0]}
    assert json.loads((out / "d-002.boxes.json").read_text())["src"] == "ocr"
    assert not (out / "d-003.boxes.json").exists() and not (out / "d-004.boxes.json").exists()
    # A second run finds the files current and reads nothing.
    again = slide_boxes.build_deck(pdf, out, ids, passthrough, {"d-004"})
    assert again["skipped"] == 2 and calls["pdf"] == 2  # d-003 (no boxes) is retried
    assert slide_boxes.build_deck(pdf, out, ids[:2], passthrough, force=True)["pdf"] == 1
    # No OCR here: image-only pages get no file.
    monkeypatch.setattr(slide_boxes, "ocr_word_boxes", lambda images, cache: None)
    (out / "d-002.boxes.json").unlink()
    assert slide_boxes.build_deck(pdf, out, ids[:2], passthrough)["none"] == 1


def test_deck_inputs_and_sidecar(tmp_path):
    (tmp_path / "slides.json").write_text(json.dumps([
        {"slide_id": "a-002", "slide_number": 2, "flags": ["student_names_possible"]},
        {"slide_id": "a-001", "slide_number": 1, "flags": []}]))
    assert slide_boxes.deck_inputs(tmp_path) == (["a-001", "a-002"], {"a-002"})
    assert slide_boxes.sidecar(Path("x/70445-s01-002.webp")) == Path("x/70445-s01-002.boxes.json")
    assert not slide_boxes.current(tmp_path / "missing.json", tmp_path / "slides.json")


def test_ocr_unavailable_off_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(slide_boxes.sys, "platform", "linux")
    assert slide_boxes.ocr_word_boxes([tmp_path / "a.webp"], tmp_path) is None
    assert slide_boxes.ocr_word_boxes([], tmp_path) == {}


# ---------------------------------------------------------------- upload and stored-clip timings

def test_upload_allows_boxes_and_words_sidecars_only_in_place():
    assert upload.allowed("slides/70445/s06/70445-s06-014.boxes.json")
    assert upload.allowed("audio/d478cab42a/02203a4790bfc24a41ec9b40.words.json")
    assert not upload.allowed("slides/70445/s06/slides.boxes.json")
    assert not upload.allowed("timings/abc/key.json")
    assert upload.boxes_for("slides/70445/s06/70445-s06-014.webp") == "slides/70445/s06/70445-s06-014.boxes.json"
    assert upload.boxes_for(None) is None and upload.words_for("x.wav") is None
    assert upload.words_for("audio/t/h.mp3") == "audio/t/h.words.json"


def _stored_clip(build: Path) -> Path:
    mp3 = build / "audio" / "tag1" / "hash1.mp3"
    mp3.parent.mkdir(parents=True)
    mp3.write_bytes(b"ID3fake")
    topics = [{"question": "q", "course": "70445", "playlist": {"segments": [
        {"slide_id": "70445-s01-002", "narration": "On this slide.", "audio_path": "audio/tag1/hash1.mp3"},
        {"slide_id": "70445-s01-003", "narration": "No audio here."}]}}]
    (build / "topics").mkdir(exist_ok=True)
    (build / "topics" / "topics.json").write_text(json.dumps(topics))
    return mp3


def test_add_timings_with_forced_alignment(tmp_path, monkeypatch):
    mp3 = _stored_clip(tmp_path)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.url.path == "/v1/forced-alignment" and request.headers["xi-api-key"] == "el-test"
        assert b'name="text"' in request.content and b"On this slide." in request.content
        return httpx.Response(200, json={"words": [{"text": "On", "start": 0.1, "end": 0.2},
                                                   {"text": "this", "start": 0.3}, {"text": "slide.", "start": 0.5}]})

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert pregenerate.add_timings(tmp_path, client, log=lambda s: None) == pregenerate.EXIT_NEEDS_KEYS
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    lines: list[str] = []
    assert pregenerate.add_timings(tmp_path, client, log=lines.append) == 0
    words = json.loads(pregenerate.words_file(mp3).read_text())
    assert words == {"words": [[0.1, 0], [0.3, 3], [0.5, 8]], "source": "elevenlabs-forced-alignment"}
    assert "1 of 1" in lines[-1]
    assert pregenerate.add_timings(tmp_path, client, log=lines.append) == 0 and len(seen) == 1  # already done
    assert "already" in lines[-1]


def test_add_timings_failure_leaves_no_file(tmp_path, monkeypatch):
    mp3 = _stored_clip(tmp_path)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422, text="bad")))
    lines: list[str] = []
    assert pregenerate.add_timings(tmp_path, client, log=lines.append) == 0
    assert not pregenerate.words_file(mp3).exists() and "0 of 1" in lines[-1]


def test_speak_reads_plain_audio_too(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-test")
    plain = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"ID3", headers={"content-type": "audio/mpeg"})))
    assert pregenerate.speak("Hi", "v", plain) == (b"ID3", [])
    stamped = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "audio_base64": base64.b64encode(b"ID3").decode(),
        "alignment": {"characters": list("Hi"), "character_start_times_seconds": [0.0, 0.1]}})))
    assert pregenerate.speak("Hi", "v", stamped) == (b"ID3", [[0.0, 0]])
    failing = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(RuntimeError):
        pregenerate.speak("Hi", "v", failing)
