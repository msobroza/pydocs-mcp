"""Pin mean_with_bootstrap_ci: deterministic 95% CI via seeded resampling.
Empty input degrades to (0, 0, 0). Constant input gives zero-width CI.
Determinism is critical — same seed twice MUST yield identical output so
runs are reproducible across reports."""
from __future__ import annotations

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
