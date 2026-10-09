"""Slide retrieval: score every slide against the question, then choose the few slides that teach the answer.

First written by hand by Ben for the course (CMU 15-113 Project 2), rebuilt Oct 8, 2026 with evals
(docs/SPEC.md, "Slide retrieval"). The not-covered threshold below is still Ben's number.

What the backend hands these functions (app/main.py, /api/ask):
- `question_vec`: the question's Voyage embedding, a float32 numpy array, shape (dim,).
- `matrix`: float32 numpy array, shape (n, dim). Row i is the embedding of `records[i]`. Only slide
  records are passed (code cells are attached later through each slide's `related_code`), already
  filtered to the requested course and to visible sessions (`playlist.searchable`, which hands out the
  same read-only array for the same index, course and hidden sessions, so its row lengths are cached).
- `records`: list of dicts, same order as the matrix rows, each with at least `id` ("70445-s06-014"),
  `course` ("70445"), `session` (6), `slide_number` (14, 1-based page in the session's PDF), `title`,
  `text`; optionally `ocr_text` and `thin` (true for a picture-only slide).

What the backend does with the result:
- `covered` is true when `select_segments` returns at least one record, so an off-topic question must
  come back as an empty list.
- The returned records are presented in the order given (deck order).
"""

from __future__ import annotations

import math
import re
import threading
import weakref
from collections.abc import Iterable, Iterator
from typing import Any

import numpy as np

# How this was chosen (Oct 7, 2026): test questions were run through rank() on the live index
# (Mac mini). Top-1 cosine scores: on-topic 0.543 to 0.693, off-topic 0.377 to 0.448.
# At the first value, 0.30, every off-topic question passed (e.g. "who won the Stanley Cup").
# Any value in the gap (0.448, 0.543) separates the two groups. I chose to lean toward
# declining, closer to the on-topic end: a wrongly declined question costs a retry, while a
# wrongly answered one puts unrelated slides in my voice. 0.52 leaves 0.072 above the highest
# off-topic score and 0.023 below the lowest on-topic one. Re-check as questions come in.
NOT_COVERED_THRESHOLD = 0.52

TOP_K = 8  # distinct candidate slides considered before gap fills and the cut
MAX_SEGMENTS = 5  # slides in one answer

# Oct 8 evals (docs/SPEC.md, "Slide retrieval"). On the 21 concept questions of
# evals/questions.course.jsonl, every expected slide that cleared the threshold sat at most 0.113
# below the best slide, so 0.12 drops only slides further away than any slide a good answer used.
RELATIVE_CUTOFF = 0.12
# The two courses share many slides. The same slide in both courses scores a median 0.009 apart
# (its lecture transcripts differ), and the closest other-course slide that was not such a copy sat
# 0.018 below the best slide. Another course's slide joins an answer only when it is within 0.01 of
# the best one: a tie, not a weaker match.
COURSE_MARGIN = 0.01

# Slides that point around the deck rather than teach: never a primary hit (a gap fill is fine).
_NAVIGATION_TITLE = re.compile(r"^(?:today s )?agenda$|^questions$|^q a$|^thank you$|^end of (?:\w+ )?class(?: \w+)?$")
_WORD = re.compile(r"[a-z0-9]+")

_norms: dict[int, tuple[weakref.ref, np.ndarray]] = {}
_norms_lock = threading.RLock()  # re-entrant: a weakref callback can run while this thread holds it


def rank(question_vec: np.ndarray, matrix: np.ndarray) -> list[tuple[int, float]]:
    """Score every row of `matrix` against `question_vec` by cosine similarity.

    Returns (row index, score) pairs for all rows, highest score first; equal scores keep row
    order, so the result is deterministic. Scores are plain Python floats in [-1, 1]. A zero or
    non-finite vector (the question or a row) scores 0 rather than NaN.
    """
    m = np.asarray(matrix)
    if m.ndim != 2 or m.shape[0] == 0:
        return []
    q = np.asarray(question_vec, dtype=m.dtype if m.dtype.kind == "f" else np.float32).reshape(-1)
    q_norm = float(np.linalg.norm(q))
    if not math.isfinite(q_norm) or q_norm == 0.0:
        scores = np.zeros(m.shape[0], dtype=np.float64)
    else:
        lengths = row_norms(m) * q_norm
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            dots = (m @ q).astype(np.float64)
            scores = np.divide(dots, lengths, out=np.zeros_like(dots), where=lengths > 0)
        scores = np.clip(np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0), -1.0, 1.0)
    order = np.argsort(-scores, kind="stable")
    return list(zip(order.tolist(), scores[order].tolist()))


def row_norms(matrix: np.ndarray) -> np.ndarray:
    """Each row's Euclidean length.

    Cached while the array lives when it is read-only and owns its memory (so nothing can change it in
    place): `playlist.searchable` hands out such arrays. Any other array is measured on every call.
    """
    if matrix.flags.writeable or not matrix.flags.owndata:
        return np.linalg.norm(matrix, axis=1)
    key = id(matrix)
    with _norms_lock:
        hit = _norms.get(key)
        if hit is not None and hit[0]() is matrix:
            return hit[1]
    lengths = np.linalg.norm(matrix, axis=1)
    lengths.flags.writeable = False

    def forget(ref: weakref.ref, key: int = key) -> None:
        with _norms_lock:
            if key in _norms and _norms[key][0] is ref:
                del _norms[key]

    with _norms_lock:
        _norms[key] = (weakref.ref(matrix, forget), lengths)
    return lengths


def select_segments(
    ranked: list[tuple[int, float]],
    records: list[dict[str, Any]],
    threshold: float | None,
) -> list[dict[str, Any]]:
    """Choose the slides to present, from the output of `rank`.

    The rules (docs/SPEC.md, "Slide retrieval"):
    1. Candidates: slides scoring at least `threshold` (None: no floor) and within RELATIVE_CUTOFF of
       the best one, best first. A picture-only slide (`thin`) or a navigation slide (title page,
       agenda, "Questions") is never a candidate.
    2. One course: the best slide's course. Another course's slide stays only within COURSE_MARGIN
       of the best score.
    3. No copies: a slide whose words (title, text and OCR text) match a candidate already taken is
       skipped, so the same slide shown in two sessions or two courses takes one place.
    4. Keep the first TOP_K candidates.
    5. Gap fills: if slides n and n+2 of one session are candidates, slide n+1 joins (any slide,
       thin or not, unless it is a copy), scored as the average of its two neighbours.
    6. Cut to MAX_SEGMENTS by score. A gap fill only counts once both its neighbours are in.
    7. Return the survivors in deck order (course, session, slide_number).

    An empty list means the question is not covered.
    """
    pool = _candidates(ranked, records, threshold)
    if not pool:
        return []
    fills = _gap_fills(pool, records)
    return sorted(_cut(pool, fills), key=deck_order)


def deck_order(rec: dict[str, Any]) -> tuple[str, int, int]:
    """Where a slide sits: (course, session, slide_number)."""
    return str(rec.get("course") or ""), int(rec.get("session") or 0), int(rec.get("slide_number") or 0)


def slide_words(rec: dict[str, Any]) -> str:
    """The words a student sees on the slide (title, text, OCR text), lowercased; '' for a blank slide."""
    parts = (rec.get("title"), rec.get("text"), rec.get("ocr_text"))
    return " ".join(_WORD.findall(" ".join(str(p or "") for p in parts).lower()))


def is_navigation(rec: dict[str, Any]) -> bool:
    """The course title page (slide 1), an agenda, or a "Questions" / "Thank you" / "End of class" slide."""
    if int(rec.get("slide_number") or 0) == 1:
        return True
    title = " ".join(_WORD.findall(str(rec.get("title") or "").lower()))
    return bool(title) and bool(_NAVIGATION_TITLE.match(title))


def can_lead(rec: dict[str, Any]) -> bool:
    """Whether a slide may be chosen for its own score: not picture-only and not a navigation slide."""
    return not rec.get("thin") and not is_navigation(rec)


def _by_score(ranked: Iterable[tuple[int, float]], n: int, threshold: float | None) -> Iterator[tuple[int, float]]:
    """Valid (index, score) pairs at or above the threshold, read in order until the first one below it.

    `ranked` is best first, as `rank` returns it (and as main._other_course passes it, with one course's
    pairs left out), so nothing past the threshold needs reading.
    """
    for i, s in ranked:
        if threshold is not None and s < threshold:
            return
        if 0 <= i < n and math.isfinite(s):
            yield i, float(s)


def _candidates(
    ranked: list[tuple[int, float]], records: list[dict[str, Any]], threshold: float | None
) -> list[tuple[dict[str, Any], float]]:
    """Rules 1 to 4: the distinct candidate slides of one course, best first."""
    pool: list[tuple[dict[str, Any], float]] = []
    seen: set[str] = set()
    best: float | None = None
    course = ""
    for i, score in _by_score(ranked, len(records), threshold):
        rec = records[i]
        if not can_lead(rec):
            continue
        if best is None:
            best, course = score, str(rec.get("course") or "")
        if score < best - RELATIVE_CUTOFF:
            break
        if str(rec.get("course") or "") != course and score < best - COURSE_MARGIN:
            continue
        words = slide_words(rec)
        if words and words in seen:
            continue
        seen.add(words)
        pool.append((rec, score))
        if len(pool) == TOP_K:
            break
    return pool


def _gap_fills(
    pool: list[tuple[dict[str, Any], float]], records: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], float, tuple[str, str]]]:
    """Rule 5: (slide, score, its two neighbours' ids) for each one-slide gap between two candidates."""
    at = {deck_order(rec): rec for rec, _ in pool}
    score = {rec["id"]: s for rec, s in pool}
    wanted: dict[tuple[str, int, int], tuple[float, tuple[str, str]]] = {}
    for (course, session, n), low in at.items():
        high = at.get((course, session, n + 2))
        if high is not None and (course, session, n + 1) not in at:
            wanted[(course, session, n + 1)] = ((score[low["id"]] + score[high["id"]]) / 2, (low["id"], high["id"]))
    if not wanted:
        return []
    taken = {slide_words(rec) for rec, _ in pool} - {""}
    fills = []
    for rec in records:  # one pass, only when there is a gap to fill
        spot = wanted.pop(deck_order(rec), None)
        if spot is not None and slide_words(rec) not in taken:
            fills.append((rec, spot[0], spot[1]))
        if not wanted:
            break
    return fills


def _cut(
    pool: list[tuple[dict[str, Any], float]],
    fills: list[tuple[dict[str, Any], float, tuple[str, str]]],
) -> list[dict[str, Any]]:
    """Rule 6: the best MAX_SEGMENTS slides by score; a gap fill only once both neighbours are kept."""
    items = [(rec, s, None) for rec, s in pool] + [(rec, s, pair) for rec, s, pair in fills]
    items.sort(key=lambda item: (-item[1], deck_order(item[0])))
    kept: dict[str, dict[str, Any]] = {}
    waiting = [item for item in items if item[2] is not None]
    for rec, _, pair in items:
        if len(kept) >= MAX_SEGMENTS:
            break
        if pair is not None:
            continue  # a gap fill is added below, right after its second neighbour
        kept[rec["id"]] = rec
        for fill in list(waiting):
            if len(kept) < MAX_SEGMENTS and all(n in kept for n in fill[2]):
                kept[fill[0]["id"]] = fill[0]
                waiting.remove(fill)
    return list(kept.values())
