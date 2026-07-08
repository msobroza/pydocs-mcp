"""Pin mean_with_bootstrap_ci: deterministic 95% CI via seeded resampling.
Empty input degrades to (0, 0, 0). Constant input gives zero-width CI.
Determinism is critical — same seed twice MUST yield identical output so
runs are reproducible across reports.

Also pins ``percentile``: linear-interpolation, deterministic — the
latency-aggregation companion to ``mean_with_bootstrap_ci``. Empty input
degrades to 0.0 so a metric with no observations does not abort the run.
"""

from __future__ import annotations

import pytest
from benchmarks.eval.metrics.aggregate import (
    mean_with_bootstrap_ci,
    paired_bootstrap_ci,
)


def test_mean_no_resamples_edge_case() -> None:
    assert mean_with_bootstrap_ci([]) == (0.0, 0.0, 0.0)


def test_mean_of_constant_values() -> None:
    mean, low, high = mean_with_bootstrap_ci([0.5, 0.5, 0.5, 0.5])
    assert mean == 0.5
    # WHY: every bootstrap resample of a constant is the same constant; CI
    # must collapse to a single point.
    assert low == 0.5
    assert high == 0.5


def test_seed_makes_ci_deterministic() -> None:
    values = [0.1, 0.4, 0.7, 0.9, 1.0]
    first = mean_with_bootstrap_ci(values, seed=42)
    second = mean_with_bootstrap_ci(values, seed=42)
    assert first == second


def test_seed_different_inputs_different_ci() -> None:
    # WHY: ensure the bootstrap actually uses the input, not just the seed —
    # a buggy impl that ignores values would return identical CIs.
    a = mean_with_bootstrap_ci([0.0, 0.0, 0.0, 0.0, 0.0], seed=0)
    b = mean_with_bootstrap_ci([1.0, 1.0, 1.0, 1.0, 1.0], seed=0)
    assert a != b


def test_percentile_simple_linear() -> None:
    """Linear-interpolation percentile, matching numpy default convention.
    percentile([1, 2, 3, 4], 0.5) == 2.5 (midpoint of 2 and 3)."""
    from benchmarks.eval.metrics.aggregate import percentile

    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5


def test_percentile_extremes() -> None:
    from benchmarks.eval.metrics.aggregate import percentile

    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0


def test_percentile_p95_on_100_values() -> None:
    from benchmarks.eval.metrics.aggregate import percentile

    values = [float(i) for i in range(100)]  # 0..99
    # p95 = 0.95 * 99 = 94.05 → 94 + 0.05 * (95 - 94) = 94.05
    assert percentile(values, 0.95) == pytest.approx(94.05)


def test_percentile_empty_returns_zero() -> None:
    from benchmarks.eval.metrics.aggregate import percentile

    assert percentile([], 0.5) == 0.0


def test_percentile_deterministic_on_repeated_calls() -> None:
    from benchmarks.eval.metrics.aggregate import percentile

    values = [0.1, 0.3, 0.5, 0.7, 0.9]
    p50_a = percentile(values, 0.5)
    p50_b = percentile(values, 0.5)
    assert p50_a == p50_b  # no internal randomness


def test_paired_bootstrap_brackets_zero_for_identical_arrays() -> None:
    # WHY: identical per-task scores → every paired diff (a[i] - b[i]) is 0,
    # so every resample diff is exactly 0 and the CI collapses to a point
    # straddling zero. A "no difference between systems" sanity floor.
    values = [0.2, 0.5, 0.8, 1.0, 0.0]
    mean_diff, low, high = paired_bootstrap_ci(values, values)
    assert mean_diff == 0.0
    assert low <= 0.0 <= high
    assert (mean_diff, low, high) == (0.0, 0.0, 0.0)


def test_paired_bootstrap_a_strictly_greater_ci_above_zero() -> None:
    # WHY: A clearly beats B on every task with some variance; a correct
    # paired test must place the WHOLE 95% CI above zero (significant win).
    # mean(a) = 4/5 = 0.8, mean(b) = 0.0 ⇒ mean_diff = 0.8.
    mean_diff, low, high = paired_bootstrap_ci([1, 1, 1, 1, 0], [0, 0, 0, 0, 0])
    assert mean_diff == pytest.approx(0.8)
    assert low > 0.0


def test_paired_bootstrap_seed_determinism() -> None:
    a = [0.9, 0.8, 0.7, 0.6, 0.1]
    b = [0.1, 0.2, 0.3, 0.4, 0.0]
    first = paired_bootstrap_ci(a, b, seed=7)
    second = paired_bootstrap_ci(a, b, seed=7)
    assert first == second  # same seed ⇒ bit-identical triple
    # A different seed may shift the interval but must stay a valid CI.
    diff, low, high = paired_bootstrap_ci(a, b, seed=99)
    assert low <= diff <= high


def test_paired_bootstrap_length_mismatch_raises() -> None:
    # WHY: a paired test on unequal-length series is a caller bug — pairing
    # is undefined — so abort loudly rather than silently degrade.
    with pytest.raises(ValueError):
        paired_bootstrap_ci([0.1, 0.2, 0.3], [0.1, 0.2])


def test_mean_bootstrap_zero_resamples_raises_value_error() -> None:
    # WHY: n_resamples is a public keyword; 0 looks like a natural "skip the
    # CI" value to a programmatic caller. Before the guard this indexed into
    # an empty resample_means list and raised a bare IndexError with none of
    # the offending value or expected shape — a caller-bug case like the
    # length-mismatch check above, so it must raise loudly and informatively
    # instead of degrading silently or crashing opaquely.
    with pytest.raises(ValueError, match="n_resamples"):
        mean_with_bootstrap_ci([1.0, 0.0], n_resamples=0)


def test_paired_bootstrap_zero_resamples_raises_value_error() -> None:
    with pytest.raises(ValueError, match="n_resamples"):
        paired_bootstrap_ci([1.0, 0.0], [0.0, 1.0], n_resamples=0)
