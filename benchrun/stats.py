"""Statistics for the bench: paired item bootstrap and exact McNemar.

Items are resampled, never individual passes: the three passes of an item are
correlated, and resampling them would shrink the interval dishonestly.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict
from statistics import fmean, quantiles


def item_scores(results: list[dict]) -> dict[str, float]:
    acc = defaultdict(list)
    for r in results:
        acc[r["item_id"]].append(float(r["passed"]))
    return {k: fmean(v) for k, v in acc.items()}


def bootstrap_ci(scores: dict[str, float], b: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    vals = list(scores.values())
    rng = random.Random(seed)
    n = len(vals)
    means = [fmean(rng.choices(vals, k=n)) for _ in range(b)]
    # 40 inclusive quantiles: the first cut is the 2.5th percentile, the last the 97.5th.
    cuts = quantiles(means, n=40, method="inclusive")
    return fmean(vals), cuts[0], cuts[-1]


def paired_diff_ci(a: dict, b: dict, b_iter: int = 10000, seed: int = 0) -> tuple[float, float, float]:
    # Sorted so the seeded bootstrap sees items in the same order on every run:
    # set order of strings changes with the interpreter's hash seed.
    common = sorted(set(a) & set(b))
    diffs = {k: a[k] - b[k] for k in common}
    return bootstrap_ci(diffs, b=b_iter, seed=seed)


def mcnemar_exact(a: dict, b: dict) -> float:
    common = set(a) & set(b)
    only_a = sum(1 for k in common if a[k] > 0.5 and b[k] <= 0.5)
    only_b = sum(1 for k in common if b[k] > 0.5 and a[k] <= 0.5)
    n = only_a + only_b
    if n == 0:
        return 1.0
    k = min(only_a, only_b)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def min_detectable_gap(n_items: int, p: float = 0.6) -> float:
    z_alpha, z_beta = 1.959964, 0.841621
    return 100 * (z_alpha + z_beta) * math.sqrt(2 * p * (1 - p) / n_items)
