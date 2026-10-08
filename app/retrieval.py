"""Retrieval: rank slides against a question and pick the segments to present.

Only the signatures, docstrings, and stubs are scaffolded. Until the
stubs are filled in, `/api/ask` answers 503 "retrieval not implemented yet",
and the three tests in tests/test_retrieval.py fail.

What the backend hands these functions (see app/main.py, /api/ask):
- `question_vec`: the question's Voyage embedding, a float32 numpy array, shape (dim,).
- `matrix`: float32 numpy array, shape (n, dim). Row i is the embedding of
  `records[i]`. Only slide records are passed (code cells are attached later
  through each slide's `related_code`), already filtered to the requested
  course and to visible sessions. Vectors are as Voyage returned them.
- `records`: list of dicts, same order as the matrix rows, each with at least
  `id` ("70445-s06-014"), `course` ("70445"), `session` (6), `slide_number`
  (14, 1-based page in the session's PDF), `title`, `text`, `related_code`.

What the backend does with the result:
- `covered` is true when `select_segments` returns at least one record, so an
  off-topic question must come back as an empty list.
- The returned records are presented in the order given (deck order).
"""

from __future__ import annotations

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

TOP_K = 8
MAX_SEGMENTS = 5


def rank(question_vec: np.ndarray, matrix: np.ndarray) -> list[tuple[int, float]]:
    """Score every row of `matrix` against `question_vec` by cosine similarity.

    Returns (row index, score) pairs for all rows, highest score first.
    Scores are plain Python floats in [-1, 1].
    """
    results = []

    matrix_length = len(matrix)

    # loop through each row of the matrix and calculate the cosine similarity
    # np.linalg.norm returns the Euclidean norm of the vector

    for i in range(matrix_length):
      matrix_row = matrix[i]
      cosine_similarity = np.dot(question_vec, matrix_row) / (np.linalg.norm(question_vec) * np.linalg.norm(matrix_row))
      results.append((i, float(cosine_similarity)))

    # sort the results by the score in descending order
    results.sort(key=lambda pair: pair[1], reverse=True)
    return results

def select_segments(
    ranked: list[tuple[int, float]],
    records: list[dict[str, Any]],
    threshold: float | None,
) -> list[dict[str, Any]]:
    """Choose the slides to present, from the output of `rank`.

    The rules from docs/SPEC.md ("Segment selection"):
    - Take the top 8 slides by score (TOP_K).
    - Drop any under `threshold`.
    - Prefer slides that sit next to each other in the deck: if slides 12, 13,
      and 15 of a session are in the top 8, include 14 as well so the
      explanation does not skip a step.
    - Keep at most 5 (MAX_SEGMENTS), then sort by deck order
      (course, session, slide_number).

    Returns the chosen records (the dicts from `records`), in deck order.
    An empty list means the question is not covered.
    """
    selected_segments = []
    scores = {}  # slide id -> score, so the cut to MAX_SEGMENTS keeps the best slides


    # if we no limited threshold we add all slides, otherwise add slides that are above the threshold
    for i, score in ranked[:TOP_K]:
      if threshold is None or score >= threshold:
        selected_segments.append(records[i])
        scores[records[i]["id"]] = score

    hits = list(selected_segments)
    for a in hits:
      for b in hits:
        same_slide_deck = a["course"] == b["course"] and a["session"] == b["session"]
        if same_slide_deck and b["slide_number"] == a["slide_number"] + 2:
          for r in records:
            if (r["course"] == a["course"] and r["session"] == a["session"]
                and r["slide_number"] == a["slide_number"] + 1
                and r not in selected_segments):
              selected_segments.append(r)
              # a gap-fill slide counts with its two neighbors' average score, so it is not the first one cut
              scores[r["id"]] = (scores[a["id"]] + scores[b["id"]]) / 2

    # best-scoring slides first, cut to MAX_SEGMENTS, then put the survivors in deck order
    selected_segments.sort(key=lambda r: scores[r["id"]], reverse=True)
    selected_segments = selected_segments[:MAX_SEGMENTS]
    selected_segments.sort(key=lambda r: (r["course"], r["session"], r["slide_number"]))
    return selected_segments