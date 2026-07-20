"""The two GEPA money seams (ADR 0017 §2): the campaign ledger is the sole
spend/stop authority — offline, no gepa, no real LLM.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.campaign.budget import BudgetGuard
from pydocs_eval.campaign.ledger import CampaignLedger, LedgerRecord, WorkState
from pydocs_eval.optimize.gepa_harness.neutralization import (
    BudgetGuardStopper,
    LedgerDebitingReflectionLM,
    ReflectionSpendLedger,
)


def _ledger_with_spend(tmp_path: Path, spend: float) -> CampaignLedger:
    ledger = CampaignLedger(tmp_path / "queue.jsonl")
    if spend:
        ledger.record(LedgerRecord(cell="c", instance_id="i", state=WorkState.DONE, cost_usd=spend))
    return ledger


def test_stopper_does_not_stop_below_ceiling(tmp_path: Path) -> None:
    guard = BudgetGuard(cost_ceiling_usd=10.0, assumed_cost_on_raise=1.0)
    stopper = BudgetGuardStopper(guard, _ledger_with_spend(tmp_path, 3.0))
    assert stopper.should_stop() is False
    assert stopper() is False  # StopperProtocol callable entry, gepa state ignored


def test_stopper_stops_at_or_over_ceiling(tmp_path: Path) -> None:
    guard = BudgetGuard(cost_ceiling_usd=10.0, assumed_cost_on_raise=1.0)
    stopper = BudgetGuardStopper(guard, _ledger_with_spend(tmp_path, 10.0))
    assert stopper() is True  # ledger spend reached the ceiling → the loop stops


def test_stopper_ignores_gepa_state_args(tmp_path: Path) -> None:
    """gepa passes engine state to the stopper; the campaign ledger is the authority."""
    guard = BudgetGuard(cost_ceiling_usd=10.0, assumed_cost_on_raise=1.0)
    stopper = BudgetGuardStopper(guard, _ledger_with_spend(tmp_path, 12.0))
    assert (
        stopper(object(), total_num_evals=0) is True
    )  # arbitrary gepa state → still authoritative


def test_reflection_lm_debits_before_forwarding() -> None:
    seen_at_call: list[float] = []
    ledger = ReflectionSpendLedger()

    def inner(prompt: str) -> str:
        seen_at_call.append(ledger.spent_usd)  # observe the ledger AT forward time
        return f"reflected:{prompt}"

    lm = LedgerDebitingReflectionLM(inner=inner, ledger=ledger, cost_per_call=0.05)
    assert lm("mutate section 3") == "reflected:mutate section 3"
    assert seen_at_call == [0.05]  # debited BEFORE inner ran
    assert ledger.spent_usd == pytest.approx(0.05)


def test_reflection_debit_lands_even_when_inner_raises() -> None:
    ledger = ReflectionSpendLedger()

    def inner(_prompt: str) -> str:
        raise RuntimeError("reflection API down")

    lm = LedgerDebitingReflectionLM(inner=inner, ledger=ledger, cost_per_call=0.05)
    with pytest.raises(RuntimeError):
        lm("prompt")
    assert ledger.spent_usd == pytest.approx(0.05)  # a failed call still burned budget


def test_reflection_ledger_rejects_negative_debit() -> None:
    with pytest.raises(ValueError, match="reflection debit must be >= 0"):
        ReflectionSpendLedger().debit(-0.01)


def test_reflection_lm_accepts_chat_message_list() -> None:
    """gepa's LanguageModel Protocol allows a (str | list[dict]) prompt."""
    ledger = ReflectionSpendLedger()
    lm = LedgerDebitingReflectionLM(inner=lambda p: str(len(p)), ledger=ledger, cost_per_call=0.01)
    assert lm([{"role": "user", "content": "hi"}]) == "1"
    assert ledger.spent_usd == pytest.approx(0.01)
