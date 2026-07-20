"""Campaign runner loop — worker pool + R6 budget guards + R8 retry (ADR 0014 item 4).

The loop dispatches ``(cell, instance)`` work items through a bounded worker pool
(an ``asyncio`` semaphore, single-digit default), enforcing three policies in the
loop itself:

- **R6 cost ceiling** — before launching each new rollout the loop checks
  :class:`~pydocs_eval.campaign.budget.BudgetGuard` against the ledger's
  accumulated spend; once exhausted it STOPS launching new rollouts (in-flight
  ones finish) and marks the run ``halted_by_guard``.
- **R6 per-rollout caps** — turns/wall are enforced downstream (the CLI
  ``--max-turns`` and the spawn timeout of ``rollout.run_rollout``); the loop
  just threads them and folds each rollout's ``total_cost_usd`` into spend.
- **R8 infra retry** — an infra-labeled outcome (Phase 2 taxonomy ``infra_error``,
  or a raised spawn/infra failure) is retried ONCE, then excluded from
  aggregates and counted separately. Retries re-enter the pending pool.

The rollout itself is an injected async seam (``rollout_fn``) so the whole loop —
budget halting, retry/exclude, resume — is offline-testable with fake rollouts;
no ``claude``, no container. Every state transition is durable in the ledger, so
a killed loop resumes from :meth:`CampaignLedger.pending` without re-running
completed items.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from pydocs_eval.campaign.budget import BudgetGuard, HaltReason
from pydocs_eval.campaign.ledger import CampaignLedger, LedgerRecord, WorkItem, WorkState

# Single source of truth for the default worker-pool width and the R8 retry
# budget (retry infra ONCE). Both are single-digit by design — RAM- and
# rate-limit-bound, not CPU (ADR 0014 §Decision 4).
_DEFAULT_CONCURRENCY = 4
_DEFAULT_RETRY_LIMIT = 1


@dataclass(frozen=True, slots=True)
class RolloutOutcome:
    """What one rollout attempt produced, from the runner's control-flow view.

    ``is_infra`` is the Phase 2 taxonomy verdict (``infra_error``) OR a
    spawn/infra failure — either triggers the R8 retry-then-exclude path.
    ``completed`` = trace present + metrics computable (the DONE definition); a
    non-completed, non-infra outcome is retried like an infra failure (a
    transient the ledger should not mark DONE).
    """

    trajectory_id: str
    cost_usd: float
    is_infra: bool
    completed: bool = True


class RolloutRaisedCost(Exception):
    """A rollout that failed AFTER burning billable tokens, carrying the partial cost.

    The wired ``rollout_fn`` raises this (instead of a bare exception) when it could
    parse a ``total_cost_usd`` off the stream's result line before the failure —
    e.g. a wall-timeout after the result line was read. :func:`_attempt` books
    exactly ``cost_usd`` so the R6 ceiling sees the real dollars burned, not $0.
    When even the partial cost is unknowable (a spawn crash, an un-parsed timeout),
    the rollout_fn raises a plain exception and the runner books the conservative
    ``BudgetGuard.assumed_cost_on_raise`` backstop instead (money-review finding 1:
    a $0 booking let a raising rollout bypass the ceiling entirely).
    """

    def __init__(self, cost_usd: float, *, trajectory_id: str | None = None) -> None:
        if cost_usd < 0:
            raise ValueError(
                f"RolloutRaisedCost.cost_usd must be >= 0, got {cost_usd!r}; "
                "a raised partial cost cannot be negative"
            )
        super().__init__(f"rollout failed after burning ${cost_usd:.4f} (partial, booked)")
        self.cost_usd = cost_usd
        self.trajectory_id = trajectory_id


RolloutFn = Callable[[WorkItem], Awaitable[RolloutOutcome]]


@dataclass(frozen=True, slots=True)
class CampaignRunResult:
    """The loop's terminal summary: why it stopped + the per-state tallies."""

    halt_reason: HaltReason
    done: int
    excluded: int
    infra_retries: int
    total_spend: float


@dataclass(slots=True)
class _LoopState:
    """Mutable bookkeeping threaded through the dispatch loop (kept off the
    signature so the helpers stay 4–20 lines)."""

    pending: deque[WorkItem]
    inflight: set[asyncio.Task[tuple[WorkItem, RolloutOutcome]]]
    done: int = 0
    excluded: int = 0
    infra_retries: int = 0
    halted: bool = False


async def run_campaign(
    work: Sequence[WorkItem],
    *,
    ledger: CampaignLedger,
    guard: BudgetGuard,
    rollout_fn: RolloutFn,
    concurrency: int = _DEFAULT_CONCURRENCY,
    retry_limit: int = _DEFAULT_RETRY_LIMIT,
) -> CampaignRunResult:
    """Drive the campaign to completion or a budget halt; return the summary.

    Resumes from the ledger (only :meth:`CampaignLedger.pending` items dispatch),
    launches up to ``concurrency`` at once, halts launching when the ceiling is
    reached, and retries infra outcomes ``retry_limit`` times before excluding.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency!r}")
    state = _LoopState(pending=deque(ledger.pending(work)), inflight=set())
    while state.pending or state.inflight:
        _fill_pool(state, ledger, guard, rollout_fn, concurrency)
        if not state.inflight:
            state.halted = True  # budget-exhausted and nothing left running
            break
        await _drain_one(state, ledger, retry_limit)
    return _summarize(state, ledger)


def _fill_pool(
    state: _LoopState,
    ledger: CampaignLedger,
    guard: BudgetGuard,
    rollout_fn: RolloutFn,
    concurrency: int,
) -> None:
    """Launch pending items up to the pool width, unless the ceiling is reached."""
    while state.pending and len(state.inflight) < concurrency:
        if guard.is_exhausted(ledger.total_spend()):
            return  # stop launching; in-flight rollouts finish, then we halt
        item = state.pending.popleft()
        _record_running(ledger, item)
        state.inflight.add(
            asyncio.create_task(_attempt(item, rollout_fn, guard.assumed_cost_on_raise))
        )


async def _drain_one(state: _LoopState, ledger: CampaignLedger, retry_limit: int) -> None:
    """Await the first in-flight rollout to finish and apply its ledger transition."""
    finished, _ = await asyncio.wait(state.inflight, return_when=asyncio.FIRST_COMPLETED)
    for task in finished:
        state.inflight.discard(task)
        item, outcome = task.result()
        _apply_outcome(state, ledger, item, outcome, retry_limit)


def _apply_outcome(
    state: _LoopState,
    ledger: CampaignLedger,
    item: WorkItem,
    outcome: RolloutOutcome,
    retry_limit: int,
) -> None:
    """Record DONE / EXCLUDED / re-queue for one finished rollout (R8 policy)."""
    if outcome.completed and not outcome.is_infra:
        attempt = ledger.attempt_count(item)
        ledger.record(
            _transition(item, WorkState.DONE, attempt, outcome.trajectory_id, outcome.cost_usd, "")
        )
        state.done += 1
        return
    _apply_retry_or_exclude(state, ledger, item, outcome, retry_limit)


def _apply_retry_or_exclude(
    state: _LoopState,
    ledger: CampaignLedger,
    item: WorkItem,
    outcome: RolloutOutcome,
    retry_limit: int,
) -> None:
    """Infra / transient path: retry up to ``retry_limit``, then exclude (R8).

    ``outcome.cost_usd`` carries the rollout's real (or, on a raise, its parsed
    partial / assumed-backstop) cost — booked on BOTH the retry and the final
    excluded line so every dollar burned counts against the ceiling (R8 + finding
    1). :func:`_attempt` guarantees a costed outcome even on a raise, so this path
    never books $0.
    """
    attempt = ledger.attempt_count(item)
    cost = outcome.cost_usd
    traj = outcome.trajectory_id
    if attempt < retry_limit:
        ledger.record(
            _transition(item, WorkState.INFRA_RETRY, attempt + 1, traj, cost, "infra retry")
        )
        state.pending.append(item)
        state.infra_retries += 1
        return
    ledger.record(_transition(item, WorkState.EXCLUDED, attempt, traj, cost, "infra excluded"))
    state.excluded += 1


async def _attempt(
    item: WorkItem, rollout_fn: RolloutFn, assumed_cost_on_raise: float
) -> tuple[WorkItem, RolloutOutcome]:
    """Run one rollout; a raise becomes a COSTED infra outcome — never a $0 loss.

    A :class:`RolloutRaisedCost` carries the partial cost the rollout_fn parsed
    off the stream before failing (book it exactly). Any other exception means
    the cost is unknowable (spawn crash, un-parsed timeout), so book the
    conservative ``assumed_cost_on_raise`` backstop. Booking $0 here (the old
    behavior) let a raising rollout accrue nothing and bypass the R6 ceiling
    entirely (money-review finding 1). Either way it is an infra failure the R8
    retry-then-exclude path handles — never a campaign abort.
    """
    try:
        return item, await rollout_fn(item)
    except RolloutRaisedCost as exc:
        return item, _raised_outcome(exc.cost_usd, exc.trajectory_id)
    except Exception:
        return item, _raised_outcome(assumed_cost_on_raise, None)


def _raised_outcome(cost_usd: float, trajectory_id: str | None) -> RolloutOutcome:
    """A failed-rollout outcome that still carries its (partial or assumed) cost."""
    return RolloutOutcome(
        trajectory_id=trajectory_id or "",
        cost_usd=cost_usd,
        is_infra=True,
        completed=False,
    )


def _record_running(ledger: CampaignLedger, item: WorkItem) -> None:
    """Mark an item RUNNING at its current attempt (cost 0 — no spend yet)."""
    ledger.record(_transition(item, WorkState.RUNNING, ledger.attempt_count(item), None, 0.0, ""))


def _transition(
    item: WorkItem,
    state: WorkState,
    attempt: int,
    trajectory_id: str | None,
    cost_usd: float,
    detail: str,
) -> LedgerRecord:
    return LedgerRecord(
        cell=item.cell,
        instance_id=item.instance_id,
        state=state,
        attempt=attempt,
        trajectory_id=trajectory_id,
        cost_usd=cost_usd,
        detail=detail,
    )


def _summarize(state: _LoopState, ledger: CampaignLedger) -> CampaignRunResult:
    reason = HaltReason.HALTED_BY_GUARD if state.halted else HaltReason.COMPLETED
    return CampaignRunResult(
        halt_reason=reason,
        done=state.done,
        excluded=state.excluded,
        infra_retries=state.infra_retries,
        total_spend=ledger.total_spend(),
    )
