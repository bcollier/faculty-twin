"""Suggest `indexer/code_map.json` (slide -> notebook cells) for Ben to review.

The spec keeps related_code a short hand mapping. This script writes a first
draft so the hand work is striking bad rows, not finding good ones:

- Slides come from F26 sessions that have both a deck and notebooks.
- Cells come from the same course (any session), so a lab notebook can attach
  to the lecture it practices.
- Score: TF-IDF cosine between a cell (its source plus the markdown above it)
  and a slide (title, text, notes, OCR text). The vectorizer is fit on the
  whole course, so words every deck uses count for little. Identifiers are
  split (`fit_transform` -> "fit transform", `kMeans` -> "k means") so code
  can meet prose, and words are cut to a rough stem ("retrieval" and
  "retrieve" meet). A cell that defines a class or function named like the
  slide title ("class Frame" on a "Frames" slide) gets TITLE_BONUS. A cell
  from another session is multiplied by CROSS_SESSION_WEIGHT: same-session
  pairs win ties.
- A pair is kept when its score is at least `--threshold`, and each slide
  keeps at most MAX_PER_SLIDE cells (best first; the player shows the first).

Privacy. Cells were scrubbed of full roster names and ids by `slides.py`, but
they are not de-identified course text. Any cell with even a single roster
first name or surname (the leak check's strict level) is left out, and so is
any slide whose title has one, because the review table puts titles and code
lines in the repo. Nothing is printed but counts and ids.

Writes:
  indexer/code_map.json       {slide_id: [code_id, ...]} plus "_" keys (method, scores);
                              build_index.py ignores keys that start with "_"
  docs/code_map_review.md     one row per pair: slide id, slide title, cell id,
                              first code line, score

Run from the repo root:
  uv run --no-project --with-requirements requirements.txt --with scikit-learn \\
      python -m indexer.suggest_code_map [--threshold 0.2] [--dry-run]
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # allow `python indexer/suggest_code_map.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer import common  # noqa: E402
from indexer.leakcheck import RosterChecker  # noqa: E402
from indexer.pg_filter import is_pg  # noqa: E402

DEFAULT_THRESHOLD = 0.2
CROSS_SESSION_WEIGHT = 0.8
NEAR_MISS_FLOOR = 0.15  # pairs from here up to the threshold are listed for review, not mapped
TITLE_BONUS = 0.1  # the cell defines a class or function named like the slide title ("class Frame" / "Frames")
MAX_PER_SLIDE = 2
SOURCE_NOTE = "suggested by similarity; Ben reviews"

# Words that say "this is code" rather than what the code is about.
CODE_STOPWORDS = {
    "import", "from", "as", "def", "return", "print", "self", "none", "true", "false", "lambda", "elif",
    "else", "for", "while", "in", "is", "not", "and", "or", "if", "with", "try", "except", "pass", "class",
    "len", "range", "str", "int", "float", "list", "dict", "append", "np", "pd", "plt", "df", "os", "sys",
    "json", "format", "shape", "head", "values", "items", "keys", "get", "set", "type", "args", "kwargs",
    "pip", "install", "content", "response", "result", "results", "output", "input", "data",
    "file", "path", "open", "read", "write", "show", "display", "api", "key", "env", "getenv", "environ",
    "userdata", "colab", "google", "drive", "mount", "ipython", "http", "https", "www", "com", "org",
}


def split_identifiers(text: str) -> str:
    """`fit_transform` -> "fit transform"; `KMeans` -> "K Means"; `df.groupby` -> "df groupby"."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "")
    return re.sub(r"[_./\\]+", " ", text)


SUFFIXES = ("ational", "ations", "ation", "ings", "ing", "ies", "als", "al", "ers", "er", "es", "ed", "ly", "s", "e")
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]{2,}")


def stem(word: str) -> str:
    """A tiny suffix stripper, so "retrieve", "retrieval" and "retrieving" meet ("retriev")."""
    for suf in SUFFIXES:
        if word.endswith(suf) and len(word) - len(suf) >= 4:
            return word[: -len(suf)] + ("y" if suf == "ies" else "")
    return word


def analyzer(stop_words: set[str]):
    def analyze(doc: str) -> list[str]:
        words = (w.lower() for w in TOKEN_RE.findall(split_identifiers(doc)))
        return [stem(w) for w in words if w not in stop_words]

    return analyze


DEF_RE = re.compile(r"^\s*(?:class|def)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)


def defines_title(source: str, title: str, stop_words: set[str]) -> bool:
    """True when a class or function the cell defines shares a content word with the slide title."""
    words = analyzer(stop_words)
    defined = {w for name in DEF_RE.findall(source or "") for w in words(name) if len(w) >= 4}
    return bool(defined & {w for w in words(title or "") if len(w) >= 4})


def first_code_line(source: str, limit: int = 90) -> str:
    """The first line that is not blank, a comment, an import, or a shell/magic line."""
    lines = [ln.strip() for ln in (source or "").splitlines() if ln.strip()]
    skip = re.compile(r"^(#|!|%|import\s|from\s+\S+\s+import\s)")
    line = next((ln for ln in lines if not skip.match(ln)), lines[0] if lines else "")
    return line if len(line) <= limit else line[: limit - 3].rstrip() + "..."


def load_course(build: Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """({course: [slide rows]}, {course: [cell rows]}) from the build folder."""
    slides: dict[str, list[dict]] = defaultdict(list)
    cells: dict[str, list[dict]] = defaultdict(list)
    for course, session, path in common.iter_session_files(build / "slides", "s[0-9][0-9]/slides.json"):
        for row in common.read_json(path, []) or []:
            slides[course].append({**row, "course": course, "session": session})
    for course, session, path in common.iter_session_files(build / "code", "s[0-9][0-9].json"):
        for cell in common.read_json(path, []) or []:
            if isinstance(cell, dict) and str(cell.get("source") or "").strip():
                cells[course].append({**cell, "course": course, "session": session})
    return slides, cells


def slide_doc(row: dict[str, Any]) -> str:
    return "\n".join(str(row.get(k) or "") for k in ("title", "text", "notes", "ocr_text"))


def cell_doc(cell: dict[str, Any]) -> str:
    return f"{cell.get('markdown_above') or ''}\n{cell.get('source') or ''}"


def suggest(
    slides: dict[str, list[dict]],
    cells: dict[str, list[dict]],
    checker: RosterChecker | None,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Rows {slide_id, title, session, cell_id, cell_session, line, score}, best first per slide."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    stats = {"cells": 0, "cells_left_out_names": 0, "slides": 0, "slides_left_out": 0, "sessions": 0}
    rows: list[dict[str, Any]] = []
    for course in sorted(cells):
        course_cells = cells[course]
        course_slides = slides.get(course, [])
        stats["cells"] += len(course_cells)
        both = sorted({c["session"] for c in course_cells} & {s["session"] for s in course_slides})
        stats["sessions"] += len(both)
        if not both:
            continue
        ok_cells = []
        for c in course_cells:
            if checker is not None and checker.strict(f"{c.get('source')}\n{c.get('markdown_above')}"):
                stats["cells_left_out_names"] += 1
                continue
            ok_cells.append(c)
        targets = []
        for s in course_slides:
            if s["session"] not in both:
                continue
            flags = set(s.get("flags") or [])
            if flags & common.EXCLUDE_FLAGS or (checker is not None and checker.strict(str(s.get("title") or ""))):
                stats["slides_left_out"] += 1
                continue
            targets.append(s)
        stats["slides"] += len(targets)
        if not ok_cells or not targets:
            continue
        stop = set(ENGLISH_STOP_WORDS) | CODE_STOPWORDS
        vec = TfidfVectorizer(analyzer=analyzer(stop), sublinear_tf=True)
        vec.fit([slide_doc(s) for s in course_slides] + [cell_doc(c) for c in course_cells])
        S = vec.transform([slide_doc(s) for s in targets])
        C = vec.transform([cell_doc(c) for c in ok_cells])
        sim = cosine_similarity(S, C)
        for i, s in enumerate(targets):
            cands = []
            for j, c in enumerate(ok_cells):
                score = float(sim[i, j])
                if defines_title(str(c.get("source") or ""), str(s.get("title") or ""), stop):
                    score += TITLE_BONUS
                score *= 1.0 if c["session"] == s["session"] else CROSS_SESSION_WEIGHT
                if score >= threshold:
                    cands.append((score, c))
            cands.sort(key=lambda t: (-t[0], t[1]["cell_id"]))
            for score, c in cands[:MAX_PER_SLIDE]:
                line = first_code_line(str(c.get("source") or ""))
                rows.append({
                    "slide_id": s["slide_id"],
                    "title": str(s.get("title") or "").strip(),
                    "session": s["session"],
                    "cell_id": c["cell_id"],
                    "cell_session": c["session"],
                    "notebook": c.get("notebook") or "",
                    "line": line if is_pg(line) else "(line not shown)",
                    "score": round(score, 3),
                })
    return rows, stats


def code_map_json(rows: list[dict[str, Any]], threshold: float) -> dict[str, Any]:
    out: dict[str, Any] = {
        "_source": SOURCE_NOTE,
        "_method": (
            "TF-IDF cosine, cell (markdown above + source) vs slide (title, text, notes, OCR text), "
            f"same course; +{TITLE_BONUS} when the cell defines a class or function named like the slide title; "
            f"other-session cells x{CROSS_SESSION_WEIGHT}; kept when score >= {threshold}; "
            f"at most {MAX_PER_SLIDE} cells per slide, best first. Generated by indexer/suggest_code_map.py; "
            "see docs/code_map_review.md. Delete a cell id (or a slide's whole entry) to strike a pair."
        ),
        "_scores": {},
    }
    for r in rows:
        out.setdefault(r["slide_id"], []).append(r["cell_id"])
        out["_scores"].setdefault(r["slide_id"], {})[r["cell_id"]] = r["score"]
    return out


def _md(text: str) -> str:
    return str(text).replace("|", "\\|").replace("`", "'").replace("\n", " ").strip()


def _table(rows: list[dict[str, Any]]) -> list[str]:
    out = ["| Slide | Slide title | Cell | First code line | Score |", "| --- | --- | --- | --- | --- |"]
    for r in sorted(rows, key=lambda r: (r["slide_id"], -r["score"], r["cell_id"])):
        cell = r["cell_id"] + ("" if r["cell_session"] == r["session"] else " (other session)")
        out.append(f"| {r['slide_id']} | {_md(r['title'])[:80]} | {cell} | `{_md(r['line'])}` | {r['score']:.3f} |")
    return out


def review_markdown(rows: list[dict[str, Any]], threshold: float, stats: dict[str, int],
                    near: list[dict[str, Any]] | None = None) -> str:
    slides_with = len({r["slide_id"] for r in rows})
    lines = [
        "# Slide-to-code mapping: review draft",
        "",
        "> AI-generated draft (Claude, Oct 5). The spec keeps `related_code` a short hand mapping; this is a first",
        "> pass for me to strike, not a decision. Generated by `indexer/suggest_code_map.py`, which also writes",
        "> `indexer/code_map.json`.",
        "",
        "How to review: strike a bad row by deleting its cell id from that slide's list in `indexer/code_map.json`",
        "(delete the slide's whole entry if no cell fits), then re-run `python -m indexer.build_index` and",
        "`python -m indexer.upload`. The player shows the first cell in a slide's list.",
        "",
        f"Method: TF-IDF cosine between each notebook cell (its source plus the markdown above it) and each slide",
        f"(title, text, notes, OCR text), within one course, with identifiers split and words cut to a rough stem.",
        f"A cell that defines a class or function named like the slide title gets +{TITLE_BONUS}. Cells from another",
        f"session of the same course are scored at x{CROSS_SESSION_WEIGHT}. A pair is kept at score >= {threshold},",
        f"at most {MAX_PER_SLIDE} cells per slide.",
        f"Slides come only from sessions that have both a deck and notebooks. Cells with any roster first name or",
        f"surname ({stats['cells_left_out_names']} of {stats['cells']}) and slides whose title has one were left out.",
        "",
        f"**{len(rows)} pairs on {slides_with} slides.**",
        "",
        *_table(rows),
    ]
    if near:
        lines += [
            "",
            f"## Near misses ({NEAR_MISS_FLOOR} to {threshold}), not in the map",
            "",
            "Most of these are noise (a shared word like \"model\" or \"token\"). Add one to `indexer/code_map.json`",
            "by hand if it fits.",
            "",
            *_table(near),
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Suggest indexer/code_map.json from TF-IDF similarity")
    ap.add_argument("--archive", help="Lecture Archive folder (default ~/Lecture Archive)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--dry-run", action="store_true", help="print counts and the score spread; write nothing")
    a = ap.parse_args(argv)
    archive = common.archive_dir(a.archive)
    checker = RosterChecker.from_dir(common.roster_dir(archive))  # refuses to run without rosters
    slides, cells = load_course(common.build_dir(archive))
    found, stats = suggest(slides, cells, checker, min(a.threshold, NEAR_MISS_FLOOR))
    rows = [r for r in found if r["score"] >= a.threshold]
    near = [r for r in found if r["score"] < a.threshold]
    slides_with = len({r["slide_id"] for r in rows})
    print(
        f"{stats['sessions']} sessions with slides and notebooks; {stats['slides']} slides, {stats['cells']} cells "
        f"({stats['cells_left_out_names']} cells and {stats['slides_left_out']} slides left out by the name check)"
    )
    print(f"threshold {a.threshold}: {len(rows)} pairs on {slides_with} slides "
          f"({sum(r['cell_session'] != r['session'] for r in rows)} from another session); "
          f"{len(near)} near misses listed for review")
    if a.dry_run:
        return 0
    common.write_json(common.REPO / "indexer" / "code_map.json", code_map_json(rows, a.threshold))
    (common.REPO / "docs" / "code_map_review.md").write_text(review_markdown(rows, a.threshold, stats, near), encoding="utf-8")
    print("wrote indexer/code_map.json and docs/code_map_review.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
