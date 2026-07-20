"""Campaign runner: completion, budget halt, R8 retry/exclude, kill/resume."""

from __future__ import annotations

import uuid

from pydocs_eval.campaign.budget import BudgetGuard, HaltReason
from pydocs_eval.campaign.ledger import CampaignLedger, WorkItem, WorkState, build_work
from pydocs_eval.campaign.runner import RolloutOutcome, run_campaign


def _ok(cost: float = 1.0):
    async def _fn(item: WorkItem) -> RolloutOutcome:
        return RolloutOutcome(trajectory_id=str(uuid.uuid4()), cost_usd=cost, is_infra=False)

    return _fn


async def test_all_complete_marks_done(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    work = build_work(["a", "b"], ["i1", "i2"])
    result = await run_campaign(
        work, ledger=ledger, guard=BudgetGuard(1000.0), rollout_fn=_ok(), concurrency=2
    )
    assert result.halt_reason is HaltReason.COMPLETED
    assert result.done == 4
    assert all(ledger.is_completed(w) for w in work)


async def test_budget_ceiling_halts_launching(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    work = build_work(["a"], [f"i{n}" for n in range(10)])
    # Ceiling 3.0 with cost 1.0/rollout, concurrency 1 → ~3 done then halt.
    result = await run_campaign(
        work, ledger=ledger, guard=BudgetGuard(3.0), rollout_fn=_ok(1.0), concurrency=1
    )
    assert result.halt_reason is HaltReason.HALTED_BY_GUARD
    assert result.done == 3
    assert ledger.total_spend() == 3.0
    assert len(ledger.pending(work)) == 7  # in-flight allowed to finish, rest unlaunched


async def test_infra_retried_once_then_excluded(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    work = build_work(["a"], ["i1"])

    async def _always_infra(item: WorkItem) -> RolloutOutcome:
        return RolloutOutcome(trajectory_id=str(uuid.uuid4()), cost_usd=0.5, is_infra=True)

    result = await run_campaign(
        work, ledger=ledger, guard=BudgetGuard(1000.0), rollout_fn=_always_infra, concurrency=1
    )
    assert result.infra_retries == 1
    assert result.excluded == 1
    assert ledger.latest(WorkItem("a", "i1")).state is WorkState.EXCLUDED
    # Both the retry (0.5) and the final excluded (0.5) count against the ceiling.
    assert ledger.total_spend() == 1.0


async def test_infra_then_success_on_retry(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    work = build_work(["a"], ["i1"])
    seen = {"n": 0}

    async def _flaky(item: WorkItem) -> RolloutOutcome:
        seen["n"] += 1
        infra = seen["n"] == 1  # fail first, succeed on retry
        return RolloutOutcome(trajectory_id=str(uuid.uuid4()), cost_usd=0.5, is_infra=infra)

    result = await run_campaign(
        work, ledger=ledger, guard=BudgetGuard(1000.0), rollout_fn=_flaky, concurrency=1
    )
    assert result.done == 1
    assert result.excluded == 0
    assert ledger.latest(WorkItem("a", "i1")).state is WorkState.DONE


async def test_raised_rollout_is_treated_as_infra(tmp_path) -> None:
    ledger = CampaignLedger(tmp_path / "q.jsonl")
    work = build_work(["a"], ["i1"])

    async def _boom(item: WorkItem) -> RolloutOutcome:
        raise RuntimeError("spawn failed")

    result = await run_campaign(
        work, ledger=ledger, guard=BudgetGuard(1000.0), rollout_fn=_boom, concurrency=1
    )
    assert result.excluded == 1  # retried once, then excluded


async def test_kill_resume_does_not_rerun_done(tmp_path) -> None:
    path = tmp_path / "q.jsonl"
    work = build_work(["a"], ["i1", "i2", "i3"])
    ran: list[str] = []

    def _tracking(cost: float):
        async def _fn(item: WorkItem) -> RolloutOutcome:
            ran.append(item.instance_id)
            return RolloutOutcome(trajectory_id=str(uuid.uuid4()), cost_usd=cost, is_infra=False)

        return _fn

    # First run: ceiling stops after 2.
    ledger = CampaignLedger(path)
    await run_campaign(
        work, ledger=ledger, guard=BudgetGuard(2.0), rollout_fn=_tracking(1.0), concurrency=1
    )
    first_ran = list(ran)
    ran.clear()
    # Resume with a bigger ceiling: only the un-done items run again.
    resumed = CampaignLedger(path)
    await run_campaign(
        work, ledger=resumed, guard=BudgetGuard(100.0), rollout_fn=_tracking(1.0), concurrency=1
    )
    assert set(first_ran).isdisjoint(ran)  # no done item re-ran
    assert set(first_ran) | set(ran) == {"i1", "i2", "i3"}
