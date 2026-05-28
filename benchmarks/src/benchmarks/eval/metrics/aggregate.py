"""Bootstrap-CI aggregation for per-task metric values (spec §4.11).

Single function, no class — aggregation has no state and no plug-in axis
to swap. If a second strategy is ever needed (e.g. studentized CI), add a
sibling function rather than retrofitting a Protocol.

Reference: https://en.wikipedia.org/wiki/Bootstrapping_(statistics)
"""

from __future__ import annotations

import random
from collections.abc import Sequence

# Single source of truth for the bootstrap resample count (CLAUDE.md
# §"Default values"): both the single-sample and paired helpers read it,
# so bumping the resolution is a one-line change with no drift between siblings.
_DEFAULT_BOOTSTRAP_ITER = 1000


def mean_with_bootstrap_ci(
    values: Sequence[float],
    *,
    n_resamples: int = _DEFAULT_BOOTSTRAP_ITER,
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

    n = len(values)
    mean = sum(values) / n

    # WHY: a fresh Random instance instead of touching the module-level RNG
    # keeps callers deterministic regardless of any other randomness in the
    # process (test parallelism, other libs reseeding). ``rng.choices``
    # samples with replacement in one C-level call, replacing what would
    # otherwise be n_resamples × n Python-level ``randrange`` invocations.
    rng = random.Random(seed)
    resample_means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(n_resamples))

    # WHY: symmetric inclusive percentile — 25 samples trimmed each tail at
    # n=1000, n=2.5%. ``high = n - 1 - low`` mirrors ``low`` around the median
    # so an asymmetry in indexing never biases the interval.
    low_idx = int(0.025 * n_resamples)
    high_idx = n_resamples - 1 - low_idx
    return (mean, resample_means[low_idx], resample_means[high_idx])


def paired_bootstrap_ci(
    values_a: Sequence[float],
    values_b: Sequence[float],
    *,
    n_resamples: int = _DEFAULT_BOOTSTRAP_ITER,
    seed: int = 0,
) -> tuple[float, float, float]:
    """Return ``(mean_diff, ci_low, ci_high)`` for the paired difference ``a - b``.

    ``values_a`` and ``values_b`` are per-task scores for two systems over the
    SAME tasks (index ``i`` is the same task in both). Used to answer "is system
    A better than system B on the same tasks?" with a 95% percentile bootstrap.

    WHY paired: the two systems are correlated through the task — a hard task
    drags both scores down, an easy task lifts both. Resampling ``a`` and ``b``
    independently would shatter that correlation and inflate the variance of the
    difference, producing a falsely WIDE (and statistically wrong) CI. So each
    resample draws ONE set of task indices with replacement and applies those
    SAME indices to both arrays before differencing the means. Preserving the
    pairing yields the tighter, correct interval for the head-to-head question.

    ``mean_diff`` is the point estimate ``mean(a) - mean(b)`` on the original
    data (not resampled), so it is exact regardless of ``n_resamples``.

    Empty input degrades to ``(0.0, 0.0, 0.0)`` — matching the sibling's
    degrade-don't-abort philosophy when a metric has no eligible tasks. A length
    mismatch, however, raises ``ValueError``: a paired test on unequal-length
    series is undefined (the pairing has no meaning), i.e. a caller bug, not a
    degrade case.

    Determinism: ``seed`` fully controls the resampling — identical inputs plus
    identical seed yield bit-identical output across runs.
    """
    if len(values_a) != len(values_b):
        raise ValueError(
            "paired_bootstrap_ci requires equal-length series "
            f"(got {len(values_a)} and {len(values_b)}); pairing is undefined "
            "for unequal lengths"
        )
    if not values_a:
        return (0.0, 0.0, 0.0)

    n = len(values_a)
    mean_diff = sum(values_a) / n - sum(values_b) / n

    # WHY: one ``rng.choices(range(n), ...)`` call per resample yields the shared
    # index set; reusing ``idx`` for both arrays is what keeps the pairing intact
    # (see the docstring). A fresh Random isolates determinism from any other RNG
    # use in the process, exactly as the single-sample sibling does.
    rng = random.Random(seed)
    resample_diffs = sorted(
        sum(values_a[i] for i in idx) / n - sum(values_b[i] for i in idx) / n
        for idx in (rng.choices(range(n), k=n) for _ in range(n_resamples))
    )

    # Same symmetric inclusive percentile indexing as ``mean_with_bootstrap_ci``.
    low_idx = int(0.025 * n_resamples)
    high_idx = n_resamples - 1 - low_idx
    return (mean_diff, resample_diffs[low_idx], resample_diffs[high_idx])


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, deterministic. Empty → 0.0.

    ``q`` is in [0, 1]. Matches numpy.percentile's default ``linear``
    interpolation method so external comparisons stay sane.

    Used by ``runner.run_sweep`` to aggregate per-task latency observations
    (``indexing_seconds`` / ``search_seconds``) into p50/p95/p99 triples.
    The semantic disambiguation between ``(mean, ci_low, ci_high)`` quality
    aggregates and ``(p50, p95, p99)`` latency aggregates is by metric-name
    suffix convention: any name ending ``_seconds`` → percentile.
    """
    if not values:
        return 0.0
    s = sorted(values)
    k = q * (len(s) - 1)
    f = int(k)
    if f >= len(s) - 1:
        return s[-1]
    frac = k - f
    return s[f] + frac * (s[f + 1] - s[f])
