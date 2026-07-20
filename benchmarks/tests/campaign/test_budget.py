"""Budget guard: ceiling is >=, remaining clamps at 0, non-positive rejected."""

from __future__ import annotations

import pytest

from pydocs_eval.campaign.budget import BudgetGuard, HaltReason


def test_is_exhausted_at_ceiling() -> None:
    guard = BudgetGuard(cost_ceiling_usd=10.0)
    assert not guard.is_exhausted(9.99)
    assert guard.is_exhausted(10.0)  # exactly at ceiling ⇒ spent
    assert guard.is_exhausted(10.01)


def test_remaining_clamps_at_zero() -> None:
    guard = BudgetGuard(cost_ceiling_usd=10.0)
    assert guard.remaining(3.0) == 7.0
    assert guard.remaining(12.0) == 0.0


def test_non_positive_ceiling_rejected() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        BudgetGuard(cost_ceiling_usd=0.0)


def test_halt_reasons_distinct() -> None:
    assert HaltReason.COMPLETED != HaltReason.HALTED_BY_GUARD
