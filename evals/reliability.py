"""Reliability statistics for eval runs: do the scores repeat, and do the judges agree?

Pure functions over plain lists (numpy only), so they are easy to test by hand:

- `icc_2_1`: intraclass correlation ICC(2,1), two-way random effects, absolute agreement, one rater.
  Used for test-retest: the same answer scored twice, or the same question answered twice.
- `pearson`, `spearman`: correlation between two runs (Spearman uses ranks, ties averaged).
- `cohen_kappa`: agreement on pass/fail beyond chance.
- `krippendorff_alpha_ordinal`: agreement among several judges on an ordinal 1 to 5 scale,
  missing scores allowed.
- `pairwise_agreement`: share of judge pairs that gave exactly the same score, and within one point.
- `mean_ci`: a mean with its 95% confidence interval (t distribution).

`PLAIN` holds the one-line plain-words reading printed next to each statistic in the reports.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from itertools import combinations
from typing import Any

import numpy as np

PLAIN = {
    "icc": "ICC (0 to 1): 0.8 or more = scores repeat closely; 0.5 to 0.8 = moderate; below 0.5 = mostly noise.",
    "spearman": "Spearman (-1 to 1): do the answers keep the same order from one run to the next? 1 = same order.",
    "flip": "Verdict flip rate: how often the same judge gave pass once and fail the other time (0% = never).",
    "kappa": "Cohen's kappa on pass/fail: 1 = always the same verdict; 0 = no better than chance; "
             "0.6 or more is usually read as substantial agreement.",
    "alpha": "Krippendorff's alpha (ordinal): agreement among the judges beyond chance; 1 = perfect, 0 = chance, "
             "0.8 or more is reliable, 0.67 to 0.8 tentative, below 0.67 the judges disagree too much to trust "
             "one alone.",
    "exact": "Exact agreement: share of judge pairs that gave the same score on the same answer.",
    "within_one": "Within one: share of judge pairs whose scores differ by at most one point.",
    "ci": "95% confidence interval: the range the mean would likely fall in with a fresh set of similar questions.",
}

# Two-sided 95% critical values of Student's t by degrees of freedom (1 to 30); 1.96 beyond.
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
        2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
        2.045, 2.042]


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None or not math.isfinite(x) else round(float(x), nd)


def mean_ci(values: Iterable[float], lo: float | None = None, hi: float | None = None) -> dict[str, Any]:
    """{mean, low, high, n}: the mean and its 95% t interval (low = high = mean when n is 1), clipped to the
    scale's own bounds `lo` and `hi` when given (a 1 to 5 score cannot have an interval below 1)."""
    v = np.asarray([float(x) for x in values if x is not None], dtype=float)
    n = len(v)
    if n == 0:
        return {"mean": None, "low": None, "high": None, "n": 0}
    m = float(v.mean())
    if n == 1:
        return {"mean": _r(m), "low": _r(m), "high": _r(m), "n": 1}
    half = (_T95[n - 2] if n - 1 <= len(_T95) else 1.96) * float(v.std(ddof=1)) / math.sqrt(n)
    low, high = m - half, m + half
    if lo is not None:
        low = max(low, lo)
    if hi is not None:
        high = min(high, hi)
    return {"mean": _r(m), "low": _r(low), "high": _r(high), "n": n}


def icc_2_1(matrix: Sequence[Sequence[float]]) -> float | None:
    """ICC(2,1) (Shrout and Fleiss): rows are targets (answers), columns are occasions or raters.

    Rows with a missing value are dropped. None when there are fewer than 2 complete rows or 2 columns,
    or when the scores do not vary at all (the statistic is undefined; agreement is then shown by
    the exact-agreement share instead).
    """
    rows = [list(map(float, r)) for r in matrix if r is not None and all(x is not None for x in r)]
    if len(rows) < 2 or len(rows[0]) < 2:
        return None
    x = np.asarray(rows, dtype=float)
    n, k = x.shape
    grand = x.mean()
    ss_rows = k * float(((x.mean(axis=1) - grand) ** 2).sum())
    ss_cols = n * float(((x.mean(axis=0) - grand) ** 2).sum())
    ss_total = float(((x - grand) ** 2).sum())
    ss_err = ss_total - ss_rows - ss_cols
    msr = ss_rows / (n - 1)
    msc = ss_cols / (k - 1)
    mse = ss_err / ((n - 1) * (k - 1))
    denom = msr + (k - 1) * mse + k * (msc - mse) / n
    if denom <= 1e-12:
        return None
    return _r((msr - mse) / denom)


def pearson(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson correlation of paired values (pairs with a missing value dropped); None if undefined."""
    pairs = [(float(x), float(y)) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    x, y = np.asarray(pairs).T
    if x.std() == 0 or y.std() == 0:
        return None
    return _r(float(np.corrcoef(x, y)[0, 1]))


def _ranks(v: np.ndarray) -> np.ndarray:
    order = np.argsort(v, kind="mergesort")
    ranks = np.empty(len(v), dtype=float)
    ranks[order] = np.arange(1, len(v) + 1)
    for value in np.unique(v):  # average ranks for ties
        idx = v == value
        ranks[idx] = ranks[idx].mean()
    return ranks


def spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Spearman rank correlation, ties given average ranks; None if undefined."""
    pairs = [(float(x), float(y)) for x, y in zip(a, b) if x is not None and y is not None]
    if len(pairs) < 3:
        return None
    x, y = np.asarray(pairs).T
    return pearson(_ranks(x), _ranks(y))


def cohen_kappa(a: Sequence[Any], b: Sequence[Any]) -> float | None:
    """Kappa for two lists of labels. None with fewer than 2 pairs; 1.0 when both always give one same label."""
    pairs = [(x, y) for x, y in zip(a, b) if x is not None and y is not None]
    n = len(pairs)
    if n < 2:
        return None
    labels = sorted({x for p in pairs for x in p}, key=str)
    po = sum(1 for x, y in pairs if x == y) / n
    pe = sum((sum(1 for x, _ in pairs if x == lab) / n) * (sum(1 for _, y in pairs if y == lab) / n) for lab in labels)
    if pe >= 1 - 1e-12:
        return 1.0 if po == 1 else None
    return _r((po - pe) / (1 - pe))


def krippendorff_alpha_ordinal(units: Sequence[Sequence[float | None]]) -> float | None:
    """Krippendorff's alpha with the ordinal distance. `units` is one list per answer, one entry per judge
    (None when that judge gave no score). Units with fewer than 2 scores are left out."""
    clean = [[float(v) for v in u if v is not None] for u in units]
    clean = [u for u in clean if len(u) >= 2]
    if not clean:
        return None
    values = sorted({v for u in clean for v in u})
    if len(values) < 2:
        return None  # every score identical: no variation to measure agreement against
    index = {v: i for i, v in enumerate(values)}
    m = len(values)
    o = np.zeros((m, m))  # coincidence matrix
    for u in clean:
        mu = len(u)
        for i, j in ((i, j) for i in range(mu) for j in range(mu) if i != j):
            o[index[u[i]], index[u[j]]] += 1.0 / (mu - 1)
    n_c = o.sum(axis=1)
    n = n_c.sum()
    delta = np.zeros((m, m))
    for c in range(m):
        for k in range(m):
            lo, hi = min(c, k), max(c, k)
            delta[c, k] = (n_c[lo:hi + 1].sum() - (n_c[c] + n_c[k]) / 2.0) ** 2
    d_o = float((o * delta).sum()) / n
    d_e = float((np.outer(n_c, n_c) * delta).sum()) / (n * (n - 1))
    if d_e <= 1e-12:
        return None
    return _r(1.0 - d_o / d_e)


def pairwise_agreement(units: Sequence[Sequence[float | None]]) -> dict[str, Any]:
    """Over every pair of judges that both scored an answer: share with the same score, and within one point."""
    same = within = total = 0
    for u in units:
        vals = [float(v) for v in u if v is not None]
        for a, b in combinations(vals, 2):
            total += 1
            same += a == b
            within += abs(a - b) <= 1
    return {"exact": _r(same / total) if total else None, "within_one": _r(within / total) if total else None,
            "pairs": total}
