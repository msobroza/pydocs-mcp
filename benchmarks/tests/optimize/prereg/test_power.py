"""Exact gate-power arithmetic pinned to the ADR 0018 / evidence rows.

Every number here is a closed-form binomial sum (no simulation, no paid calls);
the pins are the gate-power rows the ADR 0018 pre-registration quotes, recomputed
to the registered ONE-SIDED rule ``mcnemar_exact_p_one_sided(b, c) <= alpha`` (the
2026-07-20 amendment that aligned the power tables to the live gate).
"""

from __future__ import annotations

import pytest

from pydocs_eval.metrics.aggregate import mcnemar_exact_p_one_sided
from pydocs_eval.optimize.prereg.power import (
    false_accept_rate,
    gate_accept_prob,
    gate_accepts,
    power_at,
    power_rows,
)


@pytest.mark.parametrize(
    ("pi_d", "fa", "power", "powered_n"),
    [
        (0.10, 0.0383, 0.9822, 300),
        (0.20, 0.0410, 0.8224, 624),
        (0.30, 0.0427, 0.6717, 936),
    ],
)
def test_n559_rows_match_adr_0018(pi_d: float, fa: float, power: float, powered_n: int) -> None:
    """N_val=559 one-sided rule: FA≈α, honest-weak power, ADR-pinned powered N."""
    (row,) = power_rows(559, (pi_d,), alpha=0.05, delta_min=0.05)
    assert row.false_accept == pytest.approx(fa, abs=1e-3)
    assert row.power == pytest.approx(power, abs=1e-3)
    assert row.powered_n == powered_n


@pytest.mark.parametrize(
    ("pi_d", "power_rounded"),
    [(0.10, 0.98), (0.20, 0.82), (0.30, 0.67)],
)
def test_n559_power_rounds_to_adr_headline(pi_d: float, power_rounded: float) -> None:
    """ADR 0018 amended headline: power 0.98/0.82/0.67 at pi_d 0.10/0.20/0.30."""
    assert round(power_at(559, pi_d, 0.05, alpha=0.05), 2) == power_rounded


def test_false_accept_held_near_alpha_across_grid() -> None:
    """The registered one-sided rule holds FA ≈ α (conservative, below nominal α)."""
    for pi_d in (0.10, 0.20, 0.30):
        fa = false_accept_rate(559, pi_d, alpha=0.05)
        assert 0.03 < fa < 0.05  # near α, no longer the two-sided ≈ α/2


def test_n200_pi020_matches_evidence_row() -> None:
    """N=200 pi=0.20 one-sided row: exact FA=0.037, power delta=.05=0.420."""
    assert false_accept_rate(200, 0.20, alpha=0.05) == pytest.approx(0.0369, abs=1e-3)
    assert power_at(200, 0.20, 0.05, alpha=0.05) == pytest.approx(0.4202, abs=1e-3)


def test_n100_pi010_matches_evidence_row() -> None:
    """N=100 pi=0.10 one-sided row: exact FA=0.023, power delta=.05=0.348 (small-N weak)."""
    assert false_accept_rate(100, 0.10, alpha=0.05) == pytest.approx(0.0234, abs=1e-3)
    assert power_at(100, 0.10, 0.05, alpha=0.05) == pytest.approx(0.3476, abs=1e-3)


@pytest.mark.parametrize("alpha", [0.01, 0.05, 0.10])
def test_accept_predicate_agrees_with_gate_across_grid(alpha: float) -> None:
    """Drift-class killer: power.py's accept region == the registered gate rule.

    ``gate_accepts`` is the exact boundary ``gate_accept_prob`` sums over; it MUST
    equal ``decide_acceptance``'s statistical predicate
    ``mcnemar_exact_p_one_sided(b, c) <= alpha`` on every (b, c) in [0..30]^2. Any
    re-drift of the power accept-region (e.g. back to a two-sided ``< alpha``) fails
    here, not silently in a shipped power table.
    """
    for b in range(31):
        for c in range(31):
            expected = mcnemar_exact_p_one_sided(b, c) <= alpha
            assert gate_accepts(b, c, alpha=alpha) is expected, (b, c, alpha)


def test_accept_boundary_is_inclusive() -> None:
    """The gate rule is ``<= alpha`` (inclusive), not ``< alpha``.

    (b=1, c=0) has one-sided p = 0.5 exactly; at alpha=0.5 the inclusive gate
    accepts and a strict ``<`` would not. Pins the boundary direction the ADR 0018
    registered rule uses (matching acceptance.decide_acceptance).
    """
    assert mcnemar_exact_p_one_sided(1, 0) == 0.5
    assert gate_accepts(1, 0, alpha=0.5) is True


def test_gate_accepts_rejects_bad_inputs() -> None:
    with pytest.raises(ValueError, match="discordant counts"):
        gate_accepts(-1, 0, alpha=0.05)
    with pytest.raises(ValueError, match="alpha"):
        gate_accepts(1, 0, alpha=1.5)


def test_zero_effect_equals_false_accept() -> None:
    """P(accept | delta=0) IS the false-accept rate (definitional consistency)."""
    assert gate_accept_prob(559, 0.20, 0.0, alpha=0.05) == false_accept_rate(559, 0.20, alpha=0.05)


def test_delta_too_large_for_pi_d_raises() -> None:
    """p_win leaves [0,1] when delta > pi_d — a typed error naming the values."""
    with pytest.raises(ValueError, match="p_win"):
        gate_accept_prob(559, 0.10, 0.30, alpha=0.05)


def test_bad_pi_d_raises() -> None:
    with pytest.raises(ValueError, match="pi_d"):
        gate_accept_prob(100, 1.5, 0.0, alpha=0.05)
