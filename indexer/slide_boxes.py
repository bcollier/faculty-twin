"""Word boxes per slide, for the read-along highlights (docs/SPEC.md, "Read-along narration and slide spotlight").

While the twin narrates a slide, the page draws a highlighter over the words
on the slide image that the narrator is saying. It needs to know where each
word sits on the image. For every page of every deck this writes

  _build/slides/<course>/s<NN>/<slide_id>.boxes.json
  {"v": 1, "src": "pdf" | "ocr", "words": [[text, x0, y0, x1, y1, line], ...]}

with coordinates as fractions (0 to 1) of the page from the top left, which is
also the slide image (indexer/slides.py renders the same page to 1600 px).
Words are in reading order; `line` numbers the text lines within the page.

Where the words come from:
  - `pdftotext -bbox-layout` (Poppler): every word on the PDF page and its box,
    divided by the page width and height.
  - Pages with fewer than 3 PDF words (image-only slides): Apple Vision OCR
    through `indexer/ocr_vision.swift --boxes` (macOS only; elsewhere those
    pages get no file and keep only the spotlight).

Privacy: the words are my own slide's words, already visible in the image, but
they leave the build machine, so each text line goes through the same name
scrub, secret redaction, and PG filter as the slide text (indexer/slides.py).
A line any of them changes is dropped whole, a capitalized roster first name
or surname is dropped as a word, and a page flagged `student_names_possible`
gets no file at all (an older one is removed). Text is never printed.

`indexer/upload.py` uploads the file of every indexed slide (through the
roster leak check), and the backend signs it next to the slide image.

Run (no .venv in the repo):
  uv run --no-project --with-requirements requirements.txt python -m indexer.slide_boxes [--course 70445]
      [--session 6] [--force] [--no-ocr]
`indexer/slides.py` also calls `build_deck` for every deck it renders.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:  # run as a script or with indexer/ on sys.path
    from pg_filter import smooth as pg_smooth
except ImportError:  # imported as a package module
    from indexer.pg_filter import smooth as pg_smooth

VERSION = 1
MIN_PDF_WORDS = 3  # fewer than this on a page: try OCR boxes instead
MAX_WORDS = 600  # a page with more words than this keeps the first 600 (a dense table)
XHTML = "{http://www.w3.org/1999/xhtml}"
SUFFIX = ".boxes.json"

Word = list  # [text, x0, y0, x1, y1, line]


# ---------------------------------------------------------------- PDF words

def parse_bbox_layout(xhtml: str) -> list[list[Word]]:
    """Pages of words from `pdftotext -bbox-layout` output, normalized to 0..1 from the top left."""
    xhtml = re.sub(r"<!DOCTYPE[^>]*>", "", xhtml, count=1)  # the DTD is never fetched
    root = ET.fromstring(xhtml)
    pages: list[list[Word]] = []
    for page in root.iter(f"{XHTML}page"):
        width = float(page.get("width") or 0)
        height = float(page.get("height") or 0)
        words: list[Word] = []
        line_no = -1
        if width > 0 and height > 0:
            for line in page.iter(f"{XHTML}line"):
                line_no += 1
                for w in line.iter(f"{XHTML}word"):
                    text = (w.text or "").strip()
                    if not text:
                        continue
                    x0, y0 = float(w.get("xMin", 0)) / width, float(w.get("yMin", 0)) / height
                    x1, y1 = float(w.get("xMax", 0)) / width, float(w.get("yMax", 0)) / height
                    words.append([text, *(_clip(v) for v in (x0, y0, x1, y1)), line_no])
        pages.append(words)
    return pages


def _clip(v: float) -> float:
    return round(min(1.0, max(0.0, v)), 4)


def pdf_word_boxes(pdf: Path) -> list[list[Word]]:
    """Every page's words and boxes (one pdftotext call for the whole deck)."""
    out = subprocess.run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", str(pdf), "-"],
                         capture_output=True, check=True)
    return parse_bbox_layout(out.stdout.decode("utf-8", "replace"))


# ---------------------------------------------------------------- OCR words (image-only pages)

OCR_SOURCE = Path(__file__).with_name("ocr_vision.swift")


def ocr_word_boxes(images: list[Path], cache: Path) -> dict[str, list[Word]] | None:
    """Apple Vision word boxes for these images, or None when OCR is not available here."""
    if not images:
        return {}
    swiftc = shutil.which("swiftc")
    if sys.platform != "darwin" or not swiftc or not OCR_SOURCE.exists():
        return None
    binary = cache / "ocr_vision"
    if not binary.exists() or binary.stat().st_mtime < OCR_SOURCE.stat().st_mtime:
        binary.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run([swiftc, "-O", "-o", str(binary), str(OCR_SOURCE)], capture_output=True)
        if r.returncode != 0:
            return None
    out: dict[str, list[Word]] = {}
    for i in range(0, len(images), 200):
        r = subprocess.run([str(binary), "--boxes", *map(str, images[i:i + 200])], capture_output=True, timeout=3600)
        if r.returncode != 0:
            return None
        for path, words in (json.loads(r.stdout or b"{}") or {}).items():
            out[path] = reading_order([[str(w[0]), *(_clip(float(v)) for v in w[1:5]), int(w[5])]
                                       for w in words if isinstance(w, list) and len(w) >= 6])
    return out


def reading_order(words: list[Word]) -> list[Word]:
    """OCR lines sorted top to bottom (then left to right), words left to right, lines renumbered."""
    lines: dict[int, list[Word]] = {}
    for w in words:
        lines.setdefault(int(w[5]), []).append(w)
    ordered = sorted(lines.values(), key=lambda ws: (round(min(w[2] for w in ws), 2), min(w[1] for w in ws)))
    out = []
    for n, ws in enumerate(ordered):
        for w in sorted(ws, key=lambda w: w[1]):
            out.append([w[0], w[1], w[2], w[3], w[4], n])
    return out


# ---------------------------------------------------------------- privacy filter

SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|gh[po]_[A-Za-z0-9]{20,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{5,}|pa-[A-Za-z0-9_\-]{20,}"
    r"|AIza[0-9A-Za-z_\-]{30,}|hf_[A-Za-z0-9]{20,}|sk_[a-f0-9]{40,})"
)
WEAK_FLAGS = {"student_surname_possible", "student_first_name_possible"}


def clean_words(words: list[Word], check: Callable[[str], tuple[str, list[str]]] | None) -> list[Word]:
    """Drop every line the name scrub, secret redaction or PG filter would change, and weak name words.

    `check` is `NameScrubber.check` (scrubbed text, flags). Without one (no
    rosters here) nothing is kept: boxes are never written unscrubbed.
    """
    if check is None:
        return []
    lines: dict[int, list[Word]] = {}
    for w in words:
        lines.setdefault(int(w[5]), []).append(w)
    kept: list[Word] = []
    for ws in lines.values():
        text = " ".join(str(w[0]) for w in ws)
        scrubbed, flags = check(text)
        if scrubbed != text or "student_names_possible" in flags:
            continue
        if SECRET_RE.search(text) or pg_smooth(text)[1]:
            continue
        for w in ws:
            if set(check(str(w[0]))[1]) & WEAK_FLAGS:
                continue  # a roster first name or surname on its own: never sent, even if it is an author
            kept.append(w)
    return kept[:MAX_WORDS]


# ---------------------------------------------------------------- writing

def sidecar(image: Path) -> Path:
    """`<slide_id>.webp` -> `<slide_id>.boxes.json` in the same folder."""
    return image.with_name(image.name[: -len(".webp")] + SUFFIX)


def current(path: Path, pdf: Path) -> bool:
    """True when the sidecar exists, has this version, and is newer than the PDF."""
    try:
        if path.stat().st_mtime < pdf.stat().st_mtime:
            return False
        return json.loads(path.read_text()).get("v") == VERSION
    except (OSError, ValueError, AttributeError):
        return False


def write(path: Path, src: str, words: list[Word]) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"v": VERSION, "src": src, "words": words}, separators=(",", ":"), ensure_ascii=False))
    os.replace(tmp, path)


def build_deck(
    pdf: Path,
    out: Path,
    slide_ids: list[str],
    check: Callable[[str], tuple[str, list[str]]] | None,
    flagged: set[str] | None = None,
    ocr: bool = True,
    force: bool = False,
    cache: Path | None = None,
) -> dict[str, int]:
    """Write one sidecar per page of `pdf` into `out` (pages in `slide_ids` order). Returns counts."""
    counts = {"pdf": 0, "ocr": 0, "none": 0, "skipped": 0, "flagged": 0}
    flagged = flagged or set()
    todo = []
    for n, sid in enumerate(slide_ids):
        path = out / f"{sid}{SUFFIX}"
        if sid in flagged:
            path.unlink(missing_ok=True)
            counts["flagged"] += 1
        elif force or not current(path, pdf):
            todo.append((n, sid, path))
        else:
            counts["skipped"] += 1
    if not todo:
        return counts
    pages = pdf_word_boxes(pdf)
    need_ocr = []
    for n, sid, path in todo:
        words = pages[n] if n < len(pages) else []
        if len(words) >= MIN_PDF_WORDS:
            write(path, "pdf", clean_words(words, check))
            counts["pdf"] += 1
        else:
            need_ocr.append((sid, path))
    found = ocr_word_boxes([out / f"{sid}.webp" for sid, _ in need_ocr], cache or out / ".cache") \
        if (ocr and need_ocr) else None
    for sid, path in need_ocr:
        words = (found or {}).get(str(out / f"{sid}.webp"))
        if words:
            write(path, "ocr", clean_words(words, check))
            counts["ocr"] += 1
        else:
            path.unlink(missing_ok=True)
            counts["none"] += 1
    return counts


def deck_inputs(deck_dir: Path) -> tuple[list[str], set[str]]:
    """(slide ids in page order, ids flagged for student names) from the deck's slides.json."""
    rows = json.loads((deck_dir / "slides.json").read_text())
    rows.sort(key=lambda r: int(r.get("slide_number") or 0))
    ids = [str(r["slide_id"]) for r in rows]
    flagged = {str(r["slide_id"]) for r in rows if "student_names_possible" in (r.get("flags") or [])}
    return ids, flagged


def main(argv: Iterable[str] | None = None) -> int:
    try:
        import slides as slides_mod  # type: ignore
    except ImportError:
        from indexer import slides as slides_mod  # type: ignore

    ap = argparse.ArgumentParser(description="Write word-box sidecars for every slide (read-along highlights)")
    ap.add_argument("--course")
    ap.add_argument("--session", type=int)
    ap.add_argument("--force", action="store_true", help="rewrite sidecars even when they are current")
    ap.add_argument("--no-ocr", action="store_true", help="no Apple Vision boxes for image-only slides")
    a = ap.parse_args(list(argv) if argv is not None else None)
    t0 = time.time()
    scrubber = slides_mod.NameScrubber.from_dir(slides_mod.ARCHIVE / "_private" / "rosters")
    if not (scrubber.full or scrubber.ids):
        print("No rosters found: boxes are only written after the name scrub, so nothing was written.")
        return 2
    total: dict[str, int] = {}
    for sess in slides_mod.discover_sessions(a.course, a.session):
        out = slides_mod.build_dir() / "slides" / sess.course / f"s{sess.number:02d}"
        pdf = sess.folder / "slides.pdf"
        if not pdf.exists():
            pdf = out / "source_converted.pdf"
        if not pdf.exists() or not (out / "slides.json").exists():
            continue
        ids, flagged = deck_inputs(out)
        counts = build_deck(pdf, out, ids, scrubber.check, flagged, ocr=not a.no_ocr, force=a.force,
                            cache=slides_mod.build_dir() / ".cache")
        print(f"{sess.tag}: " + ", ".join(f"{k} {v}" for k, v in counts.items() if v))
        for k, v in counts.items():
            total[k] = total.get(k, 0) + v
    print("total: " + ", ".join(f"{k} {v}" for k, v in total.items()) + f" ({time.time() - t0:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
