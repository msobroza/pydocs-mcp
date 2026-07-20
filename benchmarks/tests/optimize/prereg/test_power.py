"""Exact gate-power arithmetic pinned to the ADR 0018 / evidence rows.

Every number here is a closed-form binomial sum (no simulation, no paid calls);
the pins are the gate-power-costs evidence §1c/§1d table cells the ADR 0018
pre-registration quotes.
"""

from __future__ import annotations

import pytest

from pydocs_eval.optimize.prereg.power import (
    false_accept_rate,
    gate_accept_prob,
    power_at,
    power_rows,
)


@pytest.mark.parametrize(
    ("pi_d", "fa", "power", "powered_n"),
    [
        (0.10, 0.0186, 0.9628, 300),
        (0.20, 0.0199, 0.7279, 624),
        (0.30, 0.0208, 0.5504, 936),
    ],
)
def test_n559_rows_match_adr_0018(pi_d: float, fa: float, power: float, powered_n: int) -> None:
    """N_val=559 exact rule: FA≈α/2, honest-weak power, ADR-pinned powered N."""
    (row,) = power_rows(559, (pi_d,), alpha=0.05, delta_min=0.05)
    assert row.false_accept == pytest.approx(fa, abs=1e-3)
    assert row.power == pytest.approx(power, abs=1e-3)
    assert row.powered_n == powered_n


@pytest.mark.parametrize(
    ("pi_d", "power_rounded"),
    [(0.10, 0.96), (0.20, 0.73), (0.30, 0.55)],
)
def test_n559_power_rounds_to_adr_headline(pi_d: float, power_rounded: float) -> None:
    """ADR 0018 §Decision headline: power 0.96/0.73/0.55 at pi_d 0.10/0.20/0.30."""
    assert round(power_at(559, pi_d, 0.05, alpha=0.05), 2) == power_rounded


def test_false_accept_held_near_half_alpha_across_grid() -> None:
    """The one property strict/margin rules lack: FA ≈ α/2 everywhere probed."""
    for pi_d in (0.10, 0.20, 0.30):
        assert false_accept_rate(559, pi_d, alpha=0.05) < 0.025


def test_n200_pi020_matches_evidence_row() -> None:
    """Evidence §1c N=200 row: exact FA=0.017, power δ=.05=0.298."""
    assert false_accept_rate(200, 0.20, alpha=0.05) == pytest.approx(0.0173, abs=1e-3)
    assert power_at(200, 0.20, 0.05, alpha=0.05) == pytest.approx(0.2982, abs=1e-3)


def test_n100_pi010_matches_evidence_row() -> None:
    """Evidence §1c N=100 row: exact FA=0.011, power δ=.05=0.241 (small-N weak)."""
    assert false_accept_rate(100, 0.10, alpha=0.05) == pytest.approx(0.011, abs=1e-3)
    assert power_at(100, 0.10, 0.05, alpha=0.05) == pytest.approx(0.241, abs=1e-3)


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
