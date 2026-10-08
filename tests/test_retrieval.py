"""Tests for slide retrieval in app/retrieval.py: `rank` and `select_segments`.

First written by hand by Ben for the course; rebuilt Oct 8 with evals (docs/SPEC.md, "Slide retrieval").

The vectors are synthetic. Direction 0 is "the topic"; every slide is a unit vector, so its cosine with
the question is easy to read off: a slide built as cos*e0 + sin*e_k scores `cos`. Property tests
(hypothesis, derandomized) check the rules on random decks and random scores.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import retrieval
from app.retrieval import (
    COURSE_MARGIN,
    MAX_SEGMENTS,
    RELATIVE_CUTOFF,
    TOP_K,
    deck_order,
    is_navigation,
    rank,
    row_norms,
    select_segments,
    slide_words,
)

DIM = 32
THRESHOLD = 0.5
PROPS = settings(max_examples=150, deadline=None, derandomize=True, suppress_health_check=[HealthCheck.too_slow])


def unit(score: float, other_axis: int) -> np.ndarray:
    """A unit vector whose cosine with the question (axis 0) is `score`."""
    v = np.zeros(DIM, dtype=np.float32)
    v[0] = score
    v[other_axis] = math.sqrt(max(0.0, 1.0 - score * score))
    return v


def slide(n: int, course: str = "70445", session: int = 6, **extra) -> dict:
    rec = {
        "id": f"{course}-s{session:02d}-{n:03d}",
        "kind": "slide",
        "course": course,
        "session": session,
        "slide_number": n,
        "title": f"slide {n} of {course} session {session}",
        "text": "",
        "related_code": [],
    }
    rec.update(extra)
    return rec


def deck(scores: dict[int, float], n_slides: int, course: str = "70445", session: int = 6,
         extra: dict[int, dict] | None = None):
    """Records and matrix for one session; slides not in `scores` sit far off topic."""
    records, rows = [], []
    for n in range(1, n_slides + 1):
        records.append(slide(n, course, session, **(extra or {}).get(n, {})))
        rows.append(unit(scores.get(n, 0.02 * (n % 5)), other_axis=1 + n % (DIM - 1)))
    return records, np.stack(rows)


def pick(records, matrix, threshold=THRESHOLD):
    return select_segments(rank(QUESTION, matrix), records, threshold)


def numbers(chosen):
    return [r["slide_number"] for r in chosen]


QUESTION = np.eye(DIM, dtype=np.float32)[0] * 3.0  # not unit length on purpose: cosine, not dot product


# ---------------------------------------------------------------- rank

def brute_cosine(q, m):
    out = []
    for row in m:
        d = float(np.linalg.norm(q) * np.linalg.norm(row))
        out.append(float(q @ row) / d if d else 0.0)
    return out


def test_rank_matches_cosine_highest_first():
    records, matrix = deck({3: 0.9, 7: 0.7, 9: 0.8}, n_slides=10)
    ranked = rank(QUESTION, matrix)
    assert [i for i, _ in ranked[:3]] == [2, 8, 6]
    assert ranked[0][1] == pytest.approx(0.9, abs=1e-6)
    assert all(type(i) is int and type(s) is float for i, s in ranked)
    expected = brute_cosine(QUESTION, matrix)
    for i, s in ranked:
        assert s == pytest.approx(expected[i], abs=1e-6)


def test_rank_of_an_empty_matrix_is_empty():
    assert rank(np.ones(DIM, dtype=np.float32), np.zeros((0, DIM), dtype=np.float32)) == []


def test_zero_question_scores_zero_in_row_order():
    _, matrix = deck({2: 0.9}, n_slides=5)
    ranked = rank(np.zeros(DIM, dtype=np.float32), matrix)
    assert ranked == [(0, 0.0), (1, 0.0), (2, 0.0), (3, 0.0), (4, 0.0)]


def test_zero_or_broken_rows_score_zero_not_nan():
    _, matrix = deck({1: 0.9, 2: 0.8}, n_slides=5)
    matrix[3] = 0.0
    matrix[4, 0] = np.nan
    scores = dict(rank(QUESTION, matrix))
    assert scores[3] == 0.0 and scores[4] == 0.0
    assert all(math.isfinite(s) for s in scores.values())
    assert [i for i, _ in rank(QUESTION, matrix)][:2] == [0, 1]


def test_non_finite_question_scores_zero():
    _, matrix = deck({1: 0.9}, n_slides=3)
    q = QUESTION.copy()
    q[1] = np.inf
    assert [s for _, s in rank(q, matrix)] == [0.0, 0.0, 0.0]


def test_ties_keep_row_order():
    row = unit(0.6, 1)
    matrix = np.stack([unit(0.2, 2), row, row, unit(0.9, 3), row])
    assert [i for i, _ in rank(QUESTION, matrix)] == [3, 1, 2, 4, 0]


def test_row_norms_are_cached_for_a_read_only_matrix_only():
    _, matrix = deck({1: 0.9}, n_slides=4)
    matrix = matrix * 2.0
    writable = row_norms(matrix)
    assert writable is not row_norms(matrix)  # a writable array can change, so nothing is cached
    matrix.flags.writeable = False
    first = row_norms(matrix)
    assert first is row_norms(matrix)
    assert np.allclose(first, 2.0)
    assert not first.flags.writeable
    view = matrix.view()  # read-only but not the owner: its base could still change, so it is not cached
    assert row_norms(view) is not row_norms(view)
    del view
    key = id(matrix)
    del matrix
    import gc

    gc.collect()
    assert key not in retrieval._norms  # forgotten with the array


def test_cached_norms_do_not_change_scores():
    _, matrix = deck({2: 0.9, 3: 0.5}, n_slides=6)
    before = rank(QUESTION, matrix)
    matrix.flags.writeable = False
    assert rank(QUESTION, matrix) == before == rank(QUESTION, matrix)


@PROPS
@given(st.integers(1, 40), st.integers(2, 12), st.integers(0, 10_000))
def test_rank_properties(n, dim, seed):
    rng = np.random.default_rng(seed)
    m = rng.normal(size=(n, dim)).astype(np.float32)
    m[rng.random(n) < 0.1] = 0.0  # some zero rows
    q = rng.normal(size=dim).astype(np.float32)
    ranked = rank(q, m)
    assert sorted(i for i, _ in ranked) == list(range(n))  # every row once
    scores = [s for _, s in ranked]
    assert all(-1.0 <= s <= 1.0 for s in scores)
    assert scores == sorted(scores, reverse=True)
    for (i, s), (j, t) in zip(ranked, ranked[1:]):
        assert s > t or i < j  # ties in row order
    expected = brute_cosine(q, m)
    assert all(abs(s - expected[i]) < 1e-5 for i, s in ranked)


# ---------------------------------------------------------------- select_segments: Ben's original rules

def test_on_topic_question_returns_slides_in_deck_order():
    # Slide 9 scores highest, but the answer must play 7, 8, 9 in deck order.
    records, matrix = deck({7: 0.85, 8: 0.88, 9: 0.95}, n_slides=12)
    assert [r["id"] for r in pick(records, matrix)] == ["70445-s06-007", "70445-s06-008", "70445-s06-009"]


def test_gap_between_adjacent_slides_is_filled():
    # 12, 13 and 15 match; 14 is a step in between that scores low. It is included.
    records, matrix = deck({12: 0.90, 13: 0.88, 14: 0.10, 15: 0.86}, n_slides=20)
    assert numbers(pick(records, matrix)) == [12, 13, 14, 15]


def test_off_topic_question_returns_nothing():
    records, matrix = deck({}, n_slides=15)  # every slide scores under 0.1
    assert pick(records, matrix) == []


def test_no_threshold_still_answers_with_the_best_slides():
    records, matrix = deck({4: 0.30, 5: 0.28}, n_slides=10)
    assert numbers(pick(records, matrix, threshold=None)) == [4, 5]


def test_cut_to_five_keeps_the_best_slides_and_the_bridge():
    # Six slides clear the threshold, and 4 bridges 3 and 5: seven in all, so two are cut. The cut goes by
    # score (the bridge counts with its neighbours' average), so the bridge stays and the weakest go.
    records, matrix = deck({2: 0.95, 3: 0.94, 5: 0.93, 8: 0.86, 9: 0.85, 10: 0.84}, n_slides=12)
    chosen = pick(records, matrix)
    assert len(chosen) == MAX_SEGMENTS
    assert numbers(chosen) == [2, 3, 4, 5, 8]


def test_bridge_only_counts_with_both_neighbours():
    # 3 would bridge 2 and 4, and its score (0.895) beats 10, 13 and 16. But 4 (0.84) is too weak to make
    # the five, so 3 would be half a bridge: it stays out, and the next slide by score takes its place.
    records, matrix = deck({2: 0.95, 4: 0.84, 7: 0.90, 10: 0.89, 13: 0.88, 16: 0.87}, n_slides=18)
    assert numbers(pick(records, matrix)) == [2, 7, 10, 13, 16]


def test_bridge_is_added_after_its_second_neighbour():
    records, matrix = deck({2: 0.95, 4: 0.90}, n_slides=8)
    assert numbers(pick(records, matrix)) == [2, 3, 4]


# ---------------------------------------------------------------- select_segments: the Oct 8 rules

def test_far_weaker_slides_are_dropped():
    # 0.60 clears the threshold but sits more than RELATIVE_CUTOFF below the best slide.
    records, matrix = deck({3: 0.90, 4: 0.85, 9: 0.90 - RELATIVE_CUTOFF - 0.02}, n_slides=12)
    assert numbers(pick(records, matrix)) == [3, 4]


def test_slides_just_inside_the_relative_cutoff_stay():
    records, matrix = deck({3: 0.90, 9: 0.90 - RELATIVE_CUTOFF + 0.01}, n_slides=12)
    assert numbers(pick(records, matrix)) == [3, 9]


def test_one_course_per_answer():
    # Two strong 70-445 slides and four weaker 45-884 slides: only the best slide's course answers,
    # even though "45884" sorts first.
    rec_a, mat_a = deck({10: 0.92, 11: 0.91}, n_slides=12, course="70445", session=6)
    rec_b, mat_b = deck({3: 0.88, 4: 0.87, 5: 0.86, 6: 0.85}, n_slides=8, course="45884", session=2)
    chosen = pick(rec_a + rec_b, np.vstack([mat_a, mat_b]))
    assert [r["id"] for r in chosen] == ["70445-s06-010", "70445-s06-011"]


def test_other_course_joins_only_when_tied_with_the_best():
    rec_a, mat_a = deck({10: 0.90, 11: 0.85}, n_slides=12, course="70445", session=6)
    rec_b, mat_b = deck({3: 0.90 - COURSE_MARGIN / 2, 5: 0.90 - COURSE_MARGIN * 2}, n_slides=8,
                        course="45884", session=2)
    chosen = pick(rec_a + rec_b, np.vstack([mat_a, mat_b]))
    assert [r["id"] for r in chosen] == ["45884-s02-003", "70445-s06-010", "70445-s06-011"]


def test_best_slide_sets_the_course_even_when_the_other_has_more_slides():
    rec_a, mat_a = deck({10: 0.93}, n_slides=12, course="70445", session=6)
    rec_b, mat_b = deck({3: 0.90, 4: 0.89, 5: 0.88}, n_slides=8, course="45884", session=2)
    assert [r["course"] for r in pick(rec_a + rec_b, np.vstack([mat_a, mat_b]))] == ["70445"]


def test_thin_slides_never_lead_but_fill_gaps():
    # Slide 5 scores best but is picture-only: it is not chosen for its score. Slide 8 is thin too and
    # sits between 7 and 9, so it joins as the step in between.
    thin = {"thin": True, "title": "", "text": ""}
    records, matrix = deck({5: 0.97, 7: 0.90, 8: 0.20, 9: 0.89}, n_slides=12, extra={5: thin, 8: thin})
    assert numbers(pick(records, matrix)) == [7, 8, 9]


def test_only_thin_slides_over_the_threshold_is_not_covered():
    records, matrix = deck({4: 0.9}, n_slides=6, extra={4: {"thin": True}})
    assert pick(records, matrix) == []


@pytest.mark.parametrize("title", ["Today's Agenda", "Today’s Agenda", "Agenda", "Questions?", "Q&A",
                                   "Thank you!", "End of Thursday Class", "End of Class Thursday"])
def test_navigation_slides_never_lead(title):
    records, matrix = deck({3: 0.95, 6: 0.90}, n_slides=8, extra={3: {"title": title}})
    assert is_navigation(records[2])
    assert numbers(pick(records, matrix)) == [6]


def test_title_page_never_leads():
    records, matrix = deck({1: 0.95, 6: 0.90}, n_slides=8)
    assert is_navigation(records[0]) and not is_navigation(records[5])
    assert numbers(pick(records, matrix)) == [6]


def test_ordinary_titles_are_not_navigation():
    for title in ("Questions to ask a vendor", "Agenda setting in media", "", None):
        assert not is_navigation(slide(4, title=title))


def test_copies_of_a_slide_take_one_place():
    # The same slide shown again in a later session (same words) and in the other course (tied with the
    # best, so the course rule alone would let it in): it takes one place, the best-scoring copy.
    same = {"title": "Jevons Paradox", "text": "Cheaper compute, more compute used."}
    rec_a, mat_a = deck({4: 0.90, 9: 0.89, 12: 0.85}, n_slides=12, session=4, extra={4: same})
    rec_b, mat_b = deck({2: 0.88}, n_slides=6, session=5, extra={2: same})
    rec_c, mat_c = deck({7: 0.895}, n_slides=8, course="45884", session=8, extra={7: same})
    chosen = pick(rec_a + rec_b + rec_c, np.vstack([mat_a, mat_b, mat_c]))
    assert [r["id"] for r in chosen] == ["70445-s04-004", "70445-s04-009", "70445-s04-012"]


def test_copy_of_a_chosen_slide_does_not_fill_a_gap():
    same = {"title": "Multi Agent Architectures", "text": "Many agents."}
    records, matrix = deck({3: 0.9, 5: 0.88}, n_slides=8, extra={3: same, 4: same})
    assert numbers(pick(records, matrix)) == [3, 5]


def test_blank_slides_are_not_copies_of_each_other():
    blank = {"title": "", "text": ""}
    records, matrix = deck({3: 0.9, 6: 0.88}, n_slides=8, extra={3: blank, 6: blank})
    assert numbers(pick(records, matrix)) == [3, 6]


def test_at_most_top_k_candidates():
    scores = {n: 0.95 - 0.001 * n for n in range(2, 40, 3)}  # no two adjacent, so no gap fills
    records, matrix = deck(scores, n_slides=40)
    pool = retrieval._candidates(rank(QUESTION, matrix), records, THRESHOLD)
    assert len(pool) == TOP_K
    assert len(pick(records, matrix)) == MAX_SEGMENTS


def test_out_of_range_and_nan_pairs_are_ignored():
    records, matrix = deck({3: 0.9}, n_slides=5)
    ranked = [(99, 0.99), (0, float("nan")), *rank(QUESTION, matrix)]
    assert numbers(select_segments(ranked, records, THRESHOLD)) == [3]


def test_reading_stops_at_the_threshold():
    records, matrix = deck({3: 0.9}, n_slides=5)
    ranked = rank(QUESTION, matrix)

    class Exploding(list):
        def __iter__(self):
            yield from ranked[:2]  # 0.9, then the first pair under the threshold
            raise AssertionError("read past the threshold")

    assert numbers(select_segments(Exploding(), records, THRESHOLD)) == [3]


def test_deck_order_and_words_helpers():
    assert deck_order({"course": "70445", "session": 3, "slide_number": 12}) == ("70445", 3, 12)
    assert deck_order({}) == ("", 0, 0)
    assert slide_words({"title": "TF-IDF!", "text": "Term  Frequency", "ocr_text": None}) == "tf idf term frequency"


# ---------------------------------------------------------------- select_segments: properties

@st.composite
def two_course_index(draw):
    """Random decks in two courses, random scores (some thin, some navigation, some copies)."""
    records, scores = [], []
    for course in ("45884", "70445"):
        for session in range(1, draw(st.integers(1, 3)) + 1):
            for n in range(1, draw(st.integers(1, 12)) + 1):
                kind = draw(st.sampled_from(["plain"] * 6 + ["thin", "agenda", "copy"]))
                extra = {"title": f"{course} {session} {n}", "text": ""}
                if kind == "thin":
                    extra = {"title": "", "text": "", "thin": True}
                elif kind == "agenda":
                    extra["title"] = "Today's Agenda"
                elif kind == "copy":
                    extra = {"title": "Shared slide", "text": "Same words in both courses."}
                records.append(slide(n, course, session, **extra))
                scores.append(draw(st.floats(0.0, 1.0, allow_nan=False)))
    return records, scores


@PROPS
@given(two_course_index(), st.sampled_from([None, 0.3, 0.52, 0.8]))
def test_selection_properties(index, threshold):
    records, scores = index
    ranked = sorted(enumerate(scores), key=lambda p: (-p[1], p[0]))
    chosen = select_segments(ranked, records, threshold)
    score = dict(ranked)
    pos = {r["id"]: i for i, r in enumerate(records)}
    assert len(chosen) <= MAX_SEGMENTS
    assert chosen == sorted(chosen, key=deck_order)  # deck order
    assert len({r["id"] for r in chosen}) == len(chosen)  # no slide twice
    words = [slide_words(r) for r in chosen if slide_words(r)]
    assert len(words) == len(set(words))  # no copies
    leaders = [r for r in chosen if retrieval.can_lead(r)]
    usable = [(i, s) for i, s in ranked if retrieval.can_lead(records[i]) and (threshold is None or s >= threshold)]
    if not usable:
        assert chosen == []
        return
    best_i, best = usable[0]
    assert chosen, "a usable slide over the threshold always gives an answer"
    course = records[best_i]["course"]
    assert records[best_i] in chosen  # the best usable slide always makes the answer
    for r in chosen:
        s = score[pos[r["id"]]]
        on_merit = retrieval.can_lead(r) and (threshold is None or s >= threshold) and s >= best - RELATIVE_CUTOFF
        bridge = sum(1 for x in chosen if deck_order(x)[:2] == deck_order(r)[:2]
                     and abs(deck_order(x)[2] - deck_order(r)[2]) == 1) == 2
        # chosen for its own score (in the best slide's course, or tied with the best), or a gap fill
        assert (on_merit and (r["course"] == course or s >= best - COURSE_MARGIN)) or bridge
    assert leaders, "at least one slide is chosen for its own score"


# ---------------------------------------------------------------- playlist.searchable: one read-only view per filter

def test_searchable_reuses_one_read_only_view_per_filter(monkeypatch):
    from app import playlist, storage

    records = [slide(n) for n in range(1, 5)] + [slide(2, course="45884", session=2),
                                                 {"id": "70445-c01", "kind": "code", "course": "70445"}]
    content = storage.Content(records=records, matrix=np.eye(6, DIM, dtype=np.float32))
    hidden: set = set()
    monkeypatch.setattr(playlist, "hidden_sessions", lambda: hidden)

    recs, matrix = playlist.searchable(content, None)
    again, same = playlist.searchable(content, None)
    assert same is matrix and not matrix.flags.writeable  # rank() caches this array's row lengths
    assert again == recs and again is not recs  # callers get their own list
    assert [r["id"] for r in recs] == [r["id"] for r in records[:5]]
    assert row_norms(matrix) is row_norms(same)

    only, m_only = playlist.searchable(content, "45884")
    assert [r["id"] for r in only] == ["45884-s02-002"] and m_only.shape == (1, DIM)

    hidden.add(("70445", 6))  # hiding a session is a new view, never a stale one
    shown, m_shown = playlist.searchable(content, None)
    assert [r["id"] for r in shown] == ["45884-s02-002"] and m_shown is not matrix

    content.views.update({i: None for i in range(playlist.SEARCHABLE_VIEWS)})
    playlist.searchable(content, "70445")
    assert len(content.views) == 1  # bounded: a full cache starts over


def test_searchable_without_a_views_cache_still_works(monkeypatch):
    from types import SimpleNamespace

    from app import playlist

    monkeypatch.setattr(playlist, "hidden_sessions", lambda: set())
    content = SimpleNamespace(records=[slide(2)], matrix=np.ones((1, DIM), dtype=np.float32))
    recs, matrix = playlist.searchable(content, None)
    assert [r["id"] for r in recs] == ["70445-s06-002"] and matrix.shape == (1, DIM)
