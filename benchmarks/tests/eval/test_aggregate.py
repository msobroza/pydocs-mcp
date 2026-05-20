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
from benchmarks.eval.metrics.aggregate import mean_with_bootstrap_ci


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
