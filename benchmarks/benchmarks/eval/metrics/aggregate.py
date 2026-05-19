"""Bootstrap-CI aggregation for per-task metric values (spec §4.11).

Single function, no class — aggregation has no state and no plug-in axis
to swap. If a second strategy is ever needed (e.g. studentized CI), add a
sibling function rather than retrofitting a Protocol.

Reference: https://en.wikipedia.org/wiki/Bootstrapping_(statistics)
"""
from __future__ import annotations

import random
from collections.abc import Sequence


def mean_with_bootstrap_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 1000,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Return ``(mean, ci_low, ci_high)`` at 95% via percentile bootstrap.

    Empty input degrades to ``(0.0, 0.0, 0.0)`` so a metric with no eligible
    tasks (e.g. dataset filter excluded everything) does not abort the run.

    Determinism: ``seed`` fully controls the resampling — identical inputs
    plus identical seed yield bit-identical output across runs.
    """
    if not values:
        return (0.0, 0.0, 0.0)

    mean = sum(values) / len(values)

    # WHY: a fresh Random instance instead of touching the module-level RNG
    # keeps callers deterministic regardless of any other randomness in the
    # process (test parallelism, other libs reseeding).
    rng = random.Random(seed)
    n = len(values)
    resample_means: list[float] = []
    for _ in range(n_resamples):
        sample_sum = 0.0
        for _ in range(n):
            sample_sum += values[rng.randrange(n)]
        resample_means.append(sample_sum / n)

    resample_means.sort()
    # WHY: symmetric inclusive percentile — 25 samples trimmed each tail at
    # n=1000, n=2.5%. ``high = n - 1 - low`` mirrors ``low`` around the median
    # so an asymmetry in indexing never biases the interval.
    low_idx = int(0.025 * n_resamples)
    high_idx = n_resamples - 1 - low_idx
    return (mean, resample_means[low_idx], resample_means[high_idx])
