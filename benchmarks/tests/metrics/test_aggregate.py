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
from pydocs_eval.metrics.aggregate import (
    mcnemar_exact_p,
    mcnemar_from_pairs,
    mcnemar_sample_size,
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
    from pydocs_eval.metrics.aggregate import percentile

    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5


def test_percentile_extremes() -> None:
    from pydocs_eval.metrics.aggregate import percentile

    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0


def test_percentile_p95_on_100_values() -> None:
    from pydocs_eval.metrics.aggregate import percentile

    values = [float(i) for i in range(100)]  # 0..99
    # p95 = 0.95 * 99 = 94.05 → 94 + 0.05 * (95 - 94) = 94.05
    assert percentile(values, 0.95) == pytest.approx(94.05)


def test_percentile_empty_returns_zero() -> None:
    from pydocs_eval.metrics.aggregate import percentile

    assert percentile([], 0.5) == 0.0


def test_percentile_deterministic_on_repeated_calls() -> None:
    from pydocs_eval.metrics.aggregate import percentile

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


# --- exact McNemar (ADR 0016 §Statistics) --------------------------------


def test_mcnemar_exact_p_extreme_discordance() -> None:
    # Hand-checked: b=10, c=0, n=10 discordant, k=min=0.
    # two-sided p = 2 * C(10,0) * 0.5^10 = 2/1024 = 0.001953125.
    assert mcnemar_exact_p(10, 0) == pytest.approx(0.001953125)


def test_mcnemar_exact_p_moderate_table() -> None:
    # Hand-checked: b=8, c=2, n=10, k=2.
    # tail = C(10,0)+C(10,1)+C(10,2) = 1+10+45 = 56.
    # two-sided p = 2 * 56 / 1024 = 112/1024 = 0.109375.
    assert mcnemar_exact_p(8, 2) == pytest.approx(0.109375)


def test_mcnemar_exact_p_even_split_caps_at_one() -> None:
    # Hand-checked: b=5, c=5, n=10, k=5.
    # tail = sum_{i=0}^{5} C(10,i) = 638; 2*638/1024 = 1.246 → capped at 1.0.
    assert mcnemar_exact_p(5, 5) == 1.0


def test_mcnemar_exact_p_no_discordant_pairs_is_one() -> None:
    # No signal at all (b=c=0) → no evidence against H0 → p = 1.0.
    assert mcnemar_exact_p(0, 0) == 1.0


def test_mcnemar_exact_p_symmetric_in_arguments() -> None:
    # WHY: two-sided test depends only on {b, c} as a set (min drives the tail).
    assert mcnemar_exact_p(3, 12) == mcnemar_exact_p(12, 3)


def test_mcnemar_exact_p_negative_count_raises() -> None:
    with pytest.raises(ValueError, match="-1"):
        mcnemar_exact_p(-1, 4)


# --- power-curve sizing (ADR 0016 Δ_min-pinned table) --------------------


def test_mcnemar_sample_size_reproduces_adr_table() -> None:
    # Pin ADR 0016 §Evidence table exactly (Δ_min=0.05, α=0.05, power=0.80):
    #   π_d | p_bc  | N_disc | N_total | ↑mult-12
    #  0.10 | 0.750 |   29   |   289   |   300
    #  0.20 | 0.625 |  123   |   616   |   624
    #  0.30 | 0.583 |  280   |   934   |   936
    assert mcnemar_sample_size(0.05, 0.10) == (pytest.approx(0.750), 29, 289, 300)
    assert mcnemar_sample_size(0.05, 0.20) == (pytest.approx(0.625), 123, 616, 624)
    assert mcnemar_sample_size(0.05, 0.30) == (pytest.approx(0.5833333333), 280, 934, 936)


def test_mcnemar_sample_size_pins_z_constants() -> None:
    # p_bc = 0.5 + Δ_min/(2π_d); at π_d = Δ_min*2 = 0.10, Δ_min=0.05 → p_bc=0.75.
    p_bc, _, _, _ = mcnemar_sample_size(0.05, 0.10)
    assert p_bc == pytest.approx(0.75)


def test_mcnemar_sample_size_mult12_is_multiple_of_12() -> None:
    for pi_d in (0.10, 0.20, 0.30):
        _, _, _, n12 = mcnemar_sample_size(0.05, pi_d)
        assert n12 % 12 == 0


def test_mcnemar_sample_size_forbids_pi_d_le_delta_min() -> None:
    # p_bc = 0.5 + Δ_min/(2π_d) ≤ 1 requires Δ_min ≤ π_d; at π_d == Δ_min the
    # winning arm takes ALL discordant pairs (degenerate) — raise with values.
    with pytest.raises(ValueError, match="0.05"):
        mcnemar_sample_size(0.05, 0.05)
    with pytest.raises(ValueError, match="0.04"):
        mcnemar_sample_size(0.05, 0.04)


# --- paired-cell convenience ---------------------------------------------


def test_mcnemar_from_pairs_counts_and_delta() -> None:
    # arm A resolves i1,i2,i3; arm B resolves i1 only.
    #   i1: 1/1 concordant; i2: 1/0 → b; i3: 1/0 → b; i4: 0/0 concordant.
    # b=2 (A-only), c=0 (B-only), n=4, delta = mean_a - mean_b = 3/4 - 1/4 = 0.5.
    a = {"i1": 1, "i2": 1, "i3": 1, "i4": 0}
    b = {"i1": 1, "i2": 0, "i3": 0, "i4": 0}
    b_cnt, c_cnt, n, delta, p, ci = mcnemar_from_pairs(a, b)
    assert (b_cnt, c_cnt, n) == (2, 0, 4)
    assert delta == pytest.approx(0.5)
    assert p == mcnemar_exact_p(2, 0)
    assert ci == paired_bootstrap_ci([1, 1, 1, 0], [1, 0, 0, 0])


def test_mcnemar_from_pairs_key_mismatch_raises() -> None:
    # WHY: the campaign guarantees identical instance lists; a mismatch is a
    # bug, not data — abort loudly naming the offending key.
    with pytest.raises(ValueError, match="i9"):
        mcnemar_from_pairs({"i1": 1, "i9": 0}, {"i1": 1, "i2": 0})


def test_mcnemar_from_pairs_non_binary_value_raises() -> None:
    with pytest.raises(ValueError, match="2"):
        mcnemar_from_pairs({"i1": 2}, {"i1": 1})
