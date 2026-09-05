"""Fully-offline mini-optimization loop through the REAL gepa.optimize (ADR 0017/0019).

A fake reflection callable (scripted fenced rewrites) + a scripted
``rollout_fn``/``derive_fn`` drive ``GepaHarnessOptimizer.optimize`` end-to-end for
a few iterations over a 2-instance fake set. The loop asserts the load-bearing
invariants: every proposed candidate lands in the ledger with lineage, the
campaign ``BudgetGuardStopper`` is the sole halt authority, and no acceptance
ever flows from GEPA's shaped scores (the J2 lock holds under the real loop).

These are ``def`` (not ``async def``): ``optimize`` offloads the synchronous
``gepa.optimize`` to a worker thread where the adapter's per-candidate
``asyncio.run`` is safe; the test just wraps the coroutine in ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.budget import BudgetGuard
from pydocs_eval.campaign.ledger import CampaignLedger, LedgerRecord, WorkState
from pydocs_eval.optimize._types import OptimizationBudget
from pydocs_eval.optimize.candidates.candidate import Candidate
from pydocs_eval.optimize.gepa_harness.neutralization import (
    BudgetGuardStopper,
    LedgerDebitingReflectionLM,
    ReflectionSpendLedger,
)
from pydocs_eval.optimize.gepa_harness.optimizer import GepaHarnessOptimizer
from pydocs_eval.optimize.ladder import FitnessLadder, Rung

_INSTANCES = ("i1", "i2")
_LADDER = FitnessLadder(rungs=(Rung(fitness_name="campaign", max_tasks=2, survivors=1),))
_REFLECTION_COST = 0.01


@dataclass(frozen=True, slots=True)
class _SeedArtifact:
    """A minimal ``OptimizableArtifact`` whose render() is the full 11-section doc."""

    content: str
    name: str = "descriptions"

    def render(self) -> str:
        return self.content

    def with_content(self, content: str) -> _SeedArtifact:
        return _SeedArtifact(content=content)

    def validate(self) -> tuple[str, ...]:
        return ()

    def landing_note(self) -> str:
        return ""

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _optimizer(harness, tmp_path: Path, *, stopper: object) -> GepaHarnessOptimizer:
    reflection = LedgerDebitingReflectionLM(
        inner=harness.FakeReflection(),
        ledger=ReflectionSpendLedger(),
        cost_per_call=_REFLECTION_COST,
    )
    return GepaHarnessOptimizer(
        fitness=harness.make_fitness(tmp_path, _INSTANCES, soft=0.5),
        ledger=harness.make_ledger(tmp_path),
        reflection_lm=reflection,
        stopper=stopper,
        trainset=_INSTANCES,
        valset=_INSTANCES,
        module_selector=harness.always_server,
        reflection_minibatch_size=2,
    )


def _ledger_with_spend(tmp_path: Path, spend: float) -> CampaignLedger:
    """A campaign ledger already carrying ``spend`` (the stopper's read source)."""
    ledger = CampaignLedger(tmp_path / "queue.jsonl")
    ledger.record(LedgerRecord(cell="c", instance_id="i", state=WorkState.DONE, cost_usd=spend))
    return ledger


def test_loop_records_every_proposal_with_lineage(harness, tmp_path: Path) -> None:
    """2-3 iterations: seed + reflection children, each in the ledger with parent lineage."""
    stopper = harness.CountingStopper(2)
    optimizer = _optimizer(harness, tmp_path, stopper=stopper)
    seed = _SeedArtifact(Candidate.seed().render())

    result = asyncio.run(optimizer.optimize(seed, _LADDER, OptimizationBudget()))

    records = optimizer.ledger.records()
    assert len(records) >= 2  # seed + >=1 mutated proposal
    assert stopper.calls > 0  # the injected stopper drove termination (num_metric_calls=0)
    assert optimizer.reflection_lm.ledger.spent_usd > 0  # the reflection seam debited

    seed_hash = Candidate.seed().candidate_hash
    seed_record = optimizer.ledger.latest(seed_hash)
    assert seed_record.mutation_record.proposer == "seed" and seed_record.lineage_parent is None

    children = [r for r in records if r.candidate_hash != seed_hash]
    assert children, "the loop proposed no mutation"
    assert all(c.mutation_record.proposer == "reflection" for c in children)
    assert all(c.lineage_parent == seed_hash for c in children)  # parent stashed + attributed
    assert all(c.reflector_input_refs for c in children)  # reflector-input blobs referenced
    assert all(r.valid for r in records)  # every candidate cleared the firewall (no wasted rollout)
    # Reflector-input blobs are content-addressed on disk (R3 auditability).
    for child in children:
        for ref in child.reflector_input_refs:
            assert (optimizer.ledger.blobs_dir / ref).is_file()


def test_acceptance_never_flows_from_shaped_scores(harness, tmp_path: Path) -> None:
    """The J2 lock under the real loop: GEPA's shaped scores never set acceptance (R2)."""
    optimizer = _optimizer(harness, tmp_path, stopper=harness.CountingStopper(2))
    seed = _SeedArtifact(Candidate.seed().render())
    result = asyncio.run(optimizer.optimize(seed, _LADDER, OptimizationBudget()))
    # accepted is hard-False — the held-out gate is the ONLY acceptance path.
    assert result.accepted is False
    assert result.best is not None
    # No gate decision was ever recorded off a shaped score (ADR 0017 §8).
    assert all(r.gate is None for r in optimizer.ledger.records())


def test_tripped_budget_guard_halts_the_loop(harness, tmp_path: Path) -> None:
    """An exhausted BudgetGuardStopper stops the loop after the seed — the sole authority."""
    guard = BudgetGuard(cost_ceiling_usd=5.0, assumed_cost_on_raise=0.5)
    exhausted = BudgetGuardStopper(guard=guard, ledger=_ledger_with_spend(tmp_path, 10.0))
    assert exhausted.should_stop() is True  # precondition: the guard is tripped
    optimizer = _optimizer(harness, tmp_path, stopper=exhausted)
    seed = _SeedArtifact(Candidate.seed().render())

    result = asyncio.run(optimizer.optimize(seed, _LADDER, OptimizationBudget()))

    records = optimizer.ledger.records()
    assert len(records) == 1  # only the seed base eval ran; no mutation was proposed
    assert records[0].candidate_hash == Candidate.seed().candidate_hash
    assert optimizer.reflection_lm.ledger.spent_usd == 0.0  # no reflection spend under a halt
    assert result.accepted is False
