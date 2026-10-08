"""Tests for the hand-written retrieval code in app/retrieval.py.

These three tests fail until Ben writes `rank` and `select_segments`
(they raise NotImplementedError today). They are deliberately not marked
xfail: a red test is the to-do list.

The vectors are synthetic. Direction 0 is "the topic"; every slide is a unit
vector, so its cosine with the question is easy to read off: a slide built as
cos*e0 + sin*e_k scores `cos`.
"""

from __future__ import annotations

import math

import numpy as np

from app.retrieval import rank, select_segments

DIM = 32
THRESHOLD = 0.5


def unit(score: float, other_axis: int) -> np.ndarray:
    """A unit vector whose cosine with the question (axis 0) is `score`."""
    v = np.zeros(DIM, dtype=np.float32)
    v[0] = score
    v[other_axis] = math.sqrt(max(0.0, 1.0 - score * score))
    return v


def deck(scores: dict[int, float], n_slides: int, course: str = "70445", session: int = 6):
    """Records and matrix for one session; slides not in `scores` sit far off topic."""
    records, rows = [], []
    for n in range(1, n_slides + 1):
        records.append(
            {
                "id": f"{course}-s{session:02d}-{n:03d}",
                "kind": "slide",
                "course": course,
                "session": session,
                "slide_number": n,
                "title": f"slide {n}",
                "text": "",
                "related_code": [],
            }
        )
        rows.append(unit(scores.get(n, 0.02 * (n % 5)), other_axis=1 + n % (DIM - 1)))
    return records, np.stack(rows)


QUESTION = np.eye(DIM, dtype=np.float32)[0] * 3.0  # not unit length on purpose: cosine, not dot product


def test_on_topic_question_returns_slides_in_deck_order():
    # Slide 9 scores highest, but the answer must play 7, 8, 9 in deck order.
    records, matrix = deck({7: 0.80, 8: 0.85, 9: 0.95}, n_slides=12)
    chosen = select_segments(rank(QUESTION, matrix), records, THRESHOLD)
    ids = [r["id"] for r in chosen]
    assert ids == ["70445-s06-007", "70445-s06-008", "70445-s06-009"]


def test_gap_between_adjacent_slides_is_filled():
    # 12, 13 and 15 match; 14 is a step in between that scores low. It is included.
    records, matrix = deck({12: 0.90, 13: 0.88, 14: 0.10, 15: 0.86}, n_slides=20)
    chosen = select_segments(rank(QUESTION, matrix), records, THRESHOLD)
    numbers = [r["slide_number"] for r in chosen]
    assert numbers == [12, 13, 14, 15]
    assert len(chosen) <= 5


def test_off_topic_question_returns_nothing():
    records, matrix = deck({}, n_slides=15)  # every slide scores under 0.1
    chosen = select_segments(rank(QUESTION, matrix), records, THRESHOLD)
    assert chosen == []


def test_cut_to_five_keeps_the_best_slides_and_the_bridge():
    # Six slides clear the threshold, and 3 bridges 2 and 4: seven in all, so two are cut.
    # The cut goes by score (the bridge counts with its neighbors' average), not by list order,
    # so the bridge stays and the weakest slides (8 and 9) go. The old cut kept list order,
    # where the bridge came last, and dropped it.
    records, matrix = deck({1: 0.95, 2: 0.94, 4: 0.93, 7: 0.60, 8: 0.59, 9: 0.58}, n_slides=12)
    chosen = select_segments(rank(QUESTION, matrix), records, THRESHOLD)
    assert [r["slide_number"] for r in chosen] == [1, 2, 3, 4, 7]


def test_cut_to_five_does_not_favor_a_course_by_its_code():
    # Two strong 70-445 slides and four weaker 45-884 slides: the strong ones survive the cut,
    # even though "45884" sorts before "70445".
    rec_a, mat_a = deck({10: 0.92, 11: 0.91}, n_slides=12, course="70445", session=6)
    rec_b, mat_b = deck({3: 0.62, 4: 0.61, 5: 0.60, 6: 0.59}, n_slides=8, course="45884", session=2)
    chosen = select_segments(rank(QUESTION, np.vstack([mat_a, mat_b])), rec_a + rec_b, THRESHOLD)
    ids = [r["id"] for r in chosen]
    assert len(ids) == 5 and "70445-s06-010" in ids and "70445-s06-011" in ids
    assert ids == sorted(
        ids, key=lambda i: next((r["course"], r["session"], r["slide_number"]) for r in rec_a + rec_b if r["id"] == i)
    )
