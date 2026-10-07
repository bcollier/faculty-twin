"""Table-driven checks for `rank` and `select_segments`.

Each case lists what goes in and what should come out, then prints PASS or
FAIL with both values side by side.

    uv run --no-project --with numpy python sandbox/retrieval_harness.py
    uv run --no-project --with numpy python sandbox/retrieval_harness.py --target app

The default target is app/retrieval.py (the hand-written version). `--target simple`
runs the same cases against sandbox/retrieval_simple.py.
"""

from __future__ import annotations

import argparse
import importlib
import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ---------- helpers for building inputs ----------

def rec(course: str, session: int, slide: int) -> dict:
    return {
        "id": f"{course}-s{session:02d}-{slide:03d}",
        "course": course,
        "session": session,
        "slide_number": slide,
        "title": f"slide {slide}",
        "text": "",
        "related_code": [],
    }


def one_session(n_slides: int, course: str = "70445", session: int = 6) -> list[dict]:
    return [rec(course, session, n) for n in range(1, n_slides + 1)]


def ranked_from(records: list[dict], scores: dict[str, float], default: float = 0.0):
    """Build `rank`-style output from {"70445/6/12": 0.9, ...}; unlisted slides get `default`."""
    pairs = []
    for i, r in enumerate(records):
        key = f"{r['course']}/{r['session']}/{r['slide_number']}"
        pairs.append((i, scores.get(key, default)))
    return sorted(pairs, key=lambda p: p[1], reverse=True)


def labels(chosen: list[dict]) -> list[str]:
    return [f"{r['course']}/{r['session']}/{r['slide_number']}" for r in chosen]


def at_angle(cos: float, other_axis: int, dim: int = 8, length: float = 1.0) -> np.ndarray:
    """A vector whose cosine with axis 0 is `cos`, scaled to `length`."""
    v = np.zeros(dim, dtype=np.float32)
    v[0] = cos
    v[other_axis] = math.sqrt(max(0.0, 1.0 - cos * cos))
    return v * length


# ---------- rank cases ----------

def rank_cases():
    e0 = np.eye(8, dtype=np.float32)[0]

    yield (
        "orders rows by cosine, highest first",
        (e0, np.stack([at_angle(0.2, 1), at_angle(0.9, 2), at_angle(0.5, 3)])),
        {"order": [1, 2, 0], "scores": [0.9, 0.5, 0.2]},
    )
    yield (
        "question length does not change scores (cosine, not dot product)",
        (e0 * 3.0, np.stack([at_angle(0.6, 1), at_angle(0.8, 2)])),
        {"order": [1, 0], "scores": [0.8, 0.6]},
    )
    yield (
        "a long row with a worse angle does not win",
        (e0, np.stack([at_angle(0.5, 1, length=10.0), at_angle(0.9, 2)])),
        {"order": [1, 0], "scores": [0.9, 0.5]},
    )
    yield (
        "opposite direction scores -1, orthogonal scores 0",
        (e0, np.stack([-e0, np.eye(8, dtype=np.float32)[3], e0])),
        {"order": [2, 1, 0], "scores": [1.0, 0.0, -1.0]},
    )
    yield (
        "every row is returned, not just the top few",
        (e0, np.stack([at_angle(c, 1 + k % 7) for k, c in enumerate([0.1] * 12)])),
        {"length": 12},
    )
    yield (
        "empty matrix returns an empty list",
        (e0, np.zeros((0, 8), dtype=np.float32)),
        {"order": [], "scores": []},
    )


def check_rank(rank, args, expected):
    out = rank(*args)
    problems = []
    if not isinstance(out, list):
        return [f"returned {type(out).__name__}, not list"]
    for pair in out:
        if not (isinstance(pair[0], int) and isinstance(pair[1], float)):
            problems.append(f"pair {pair!r} is not (int, float); use int(...) and float(...)")
            break
        if not -1.0001 <= pair[1] <= 1.0001:
            problems.append(f"score {pair[1]} is outside [-1, 1]")
            break
    if "length" in expected and len(out) != expected["length"]:
        problems.append(f"expected {expected['length']} pairs, got {len(out)}")
    if "order" in expected:
        got = [i for i, _ in out]
        if got != expected["order"]:
            problems.append(f"order: expected {expected['order']}, got {got}")
    if "scores" in expected:
        got = [s for _, s in out]
        if len(got) != len(expected["scores"]) or any(
            abs(a - b) > 1e-4 for a, b in zip(got, expected["scores"])
        ):
            problems.append(f"scores: expected {expected['scores']}, got {[round(s, 4) for s in got]}")
    return problems


# ---------- select_segments cases ----------

def select_cases():
    s = one_session(12)
    yield (
        "best slide is 9, but the answer plays 7, 8, 9 in deck order",
        (ranked_from(s, {"70445/6/7": 0.80, "70445/6/8": 0.85, "70445/6/9": 0.95}), s, 0.5),
        ["70445/6/7", "70445/6/8", "70445/6/9"],
    )

    s = one_session(20)
    yield (
        "one-slide hole (14) between hits is filled even though 14 scores low",
        (ranked_from(s, {"70445/6/12": 0.90, "70445/6/13": 0.88, "70445/6/14": 0.10, "70445/6/15": 0.86}), s, 0.5),
        ["70445/6/12", "70445/6/13", "70445/6/14", "70445/6/15"],
    )
    yield (
        "two-slide hole (13, 14) is not filled",
        (ranked_from(s, {"70445/6/12": 0.90, "70445/6/15": 0.86}), s, 0.5),
        ["70445/6/12", "70445/6/15"],
    )
    yield (
        "everything under the threshold means not covered",
        (ranked_from(s, {"70445/6/3": 0.40, "70445/6/4": 0.30}), s, 0.5),
        [],
    )
    yield (
        "a score exactly at the threshold is kept",
        (ranked_from(s, {"70445/6/5": 0.50, "70445/6/9": 0.49}), s, 0.5),
        ["70445/6/5"],
    )
    yield (
        "empty ranking returns nothing",
        ([], [], 0.5),
        [],
    )

    s = one_session(30)
    hits = {f"70445/6/{n}": 0.95 - 0.01 * k for k, n in enumerate([2, 5, 8, 11, 14, 17, 20, 23, 26])}
    yield (
        "only the top 8 are considered (slide 26 is 9th), then capped at 5 by score",
        (ranked_from(s, hits), s, 0.5),
        ["70445/6/2", "70445/6/5", "70445/6/8", "70445/6/11", "70445/6/14"],
    )

    s = one_session(12)
    yield (
        "threshold None keeps the top 8 regardless of score, then caps at 5",
        (ranked_from(s, {f"70445/6/{n}": 0.01 * n for n in range(1, 13)}), s, None),
        ["70445/6/8", "70445/6/9", "70445/6/10", "70445/6/11", "70445/6/12"],
    )

    s = one_session(20) + one_session(20, session=7)
    yield (
        "slides in different sessions are not neighbors (no fill across sessions)",
        (ranked_from(s, {"70445/6/19": 0.90, "70445/7/1": 0.88}), s, 0.5),
        ["70445/6/19", "70445/7/1"],
    )
    yield (
        "same slide numbers in different sessions do not bridge each other",
        (ranked_from(s, {"70445/6/4": 0.90, "70445/7/6": 0.88}), s, 0.5),
        ["70445/6/4", "70445/7/6"],
    )
    yield (
        "deck order sorts by session before slide number",
        (ranked_from(s, {"70445/7/2": 0.95, "70445/6/15": 0.90}), s, 0.5),
        ["70445/6/15", "70445/7/2"],
    )

    s = one_session(10, course="70445", session=3) + one_session(10, course="45884", session=3)
    yield (
        "deck order sorts by course first",
        (ranked_from(s, {"70445/3/1": 0.95, "45884/3/9": 0.90}), s, 0.5),
        ["45884/3/9", "70445/3/1"],
    )

    s = [r for r in one_session(20) if r["slide_number"] != 14]
    yield (
        "a hidden middle slide (not in records) cannot be filled",
        (ranked_from(s, {"70445/6/13": 0.90, "70445/6/15": 0.86}), s, 0.5),
        ["70445/6/13", "70445/6/15"],
    )

    s = one_session(20)
    yield (
        "fill pushes past 5, so the low-scoring bridge is the one dropped",
        (ranked_from(s, {
            "70445/6/2": 0.95, "70445/6/4": 0.94, "70445/6/8": 0.93,
            "70445/6/11": 0.92, "70445/6/17": 0.91,
        }), s, 0.5),
        ["70445/6/2", "70445/6/4", "70445/6/8", "70445/6/11", "70445/6/17"],
    )


def check_select(select_segments, args, expected):
    out = select_segments(*args)
    if not isinstance(out, list):
        return [f"returned {type(out).__name__}, not list"]
    if out and not all(isinstance(r, dict) for r in out):
        return ["should return the record dicts themselves, not indexes or ids"]
    got = labels(out)
    return [] if got == expected else [f"expected {expected}\n          got      {got}"]


# ---------- runner ----------

def run(module) -> int:
    failures = 0
    for title, cases, fn, checker in (
        ("rank", rank_cases(), module.rank, check_rank),
        ("select_segments", select_cases(), module.select_segments, check_select),
    ):
        print(f"\n{title}")
        for name, args, expected in cases:
            try:
                problems = checker(fn, args, expected)
            except NotImplementedError:
                problems = ["still raises NotImplementedError"]
            except Exception as exc:
                problems = [f"raised {type(exc).__name__}: {exc}"]
            print(f"  {'PASS' if not problems else 'FAIL'}  {name}")
            for p in problems:
                print(f"          {p}")
            failures += bool(problems)
    print(f"\n{failures} failing" if failures else "\nall cases pass")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["simple", "app"], default="app")
    target = parser.parse_args().target
    name = {
        "simple": "sandbox.retrieval_simple",
        "app": "app.retrieval",
    }[target]
    print(f"target: {name}")
    return run(importlib.import_module(name))


if __name__ == "__main__":
    raise SystemExit(main())
