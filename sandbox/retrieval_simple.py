"""Simpler versions of `rank` and `select_segments`: plain loops, little numpy.

Written by an AI tool as a model to type from. The app never imports this file.
"""

from __future__ import annotations

from typing import Any

import numpy as np

TOP_K = 8
MAX_SEGMENTS = 5


def rank(question_vec: np.ndarray, matrix: np.ndarray) -> list[tuple[int, float]]:
    results = []
    for i in range(len(matrix)):
        row = matrix[i]
        score = np.dot(question_vec, row) / (np.linalg.norm(question_vec) * np.linalg.norm(row))
        results.append((i, float(score)))
    results.sort(key=lambda pair: pair[1], reverse=True)
    return results


def select_segments(
    ranked: list[tuple[int, float]],
    records: list[dict[str, Any]],
    threshold: float | None,
) -> list[dict[str, Any]]:
    chosen = []
    for i, score in ranked[:TOP_K]:
        if threshold is None or score >= threshold:
            chosen.append(records[i])

    hits = list(chosen)
    for a in hits:
        for b in hits:
            same_deck = a["course"] == b["course"] and a["session"] == b["session"]
            if same_deck and b["slide_number"] == a["slide_number"] + 2:
                for r in records:
                    if (r["course"] == a["course"] and r["session"] == a["session"]
                            and r["slide_number"] == a["slide_number"] + 1
                            and r not in chosen):
                        chosen.append(r)

    chosen = chosen[:MAX_SEGMENTS]
    chosen.sort(key=lambda r: (r["course"], r["session"], r["slide_number"]))
    return chosen
