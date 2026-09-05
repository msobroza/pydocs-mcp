"""Campaign budget guards — R6 enforced in the runner loop (ADR 0014 item 4).

Three guards, all mechanical, no heuristics:

- **per-rollout turn cap** — the arm's ``max_turns`` reaches the CLI as
  ``--max-turns`` (``agent_track/_command.build_claude_command``); the runner
  only asserts arm/lockfile agreement so a cap drift is caught before launch.
- **per-rollout wall cap** — the existing ``rollout.run_rollout`` spawn timeout
  (``SpawnSeam.task_timeout_seconds`` → ``RolloutTimeoutError``); the runner
  passes the lockfile's ``wall_seconds`` through, adding nothing new here.
- **campaign cost ceiling** — after each rollout the runner folds
  ``total_cost_usd`` into the persisted queue ledger's spend; once spend reaches
  the ceiling, :meth:`BudgetGuard.is_exhausted` returns True and the runner stops
  *launching* new rollouts (in-flight ones finish), marking the campaign
  ``halted_by_guard``.

The ceiling is ``>=`` not ``>``: at exactly the ceiling the budget is spent, so
the next launch is refused. Spend is summed from the ledger (single source), so
this module holds only the threshold comparison — no second spend accounting.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HaltReason(StrEnum):
    """Why a campaign run stopped launching new rollouts."""

    COMPLETED = "completed"  # every pending item reached a terminal state
    HALTED_BY_GUARD = "halted_by_guard"  # cost ceiling reached (R6)


@dataclass(frozen=True, slots=True)
class BudgetGuard:
    """The campaign cost ceiling check (R6). Stateless — spend is read from the
    ledger and compared here, so there is exactly one spend source of truth.

    ``assumed_cost_on_raise`` is the conservative dollar figure the runner books
    when a rollout RAISES with an unknowable cost (a spawn crash or an un-parsed
    timeout) — booking $0 there let a raising rollout bypass the ceiling entirely
    (money-review finding 1). It is the runtime mirror of the lockfile's hashed
    ``assumed_cost_on_raise`` (same relationship as ``cost_ceiling_usd``), and the
    runbook pins it to the per-rollout worst-case from the cost model.
    """

    cost_ceiling_usd: float
    assumed_cost_on_raise: float

    def __post_init__(self) -> None:
        if self.cost_ceiling_usd <= 0:
            raise ValueError(
                f"cost_ceiling_usd must be positive, got {self.cost_ceiling_usd!r}; "
                "a non-positive ceiling would halt the campaign before the first rollout"
            )
        if self.assumed_cost_on_raise < 0:
            raise ValueError(
                f"assumed_cost_on_raise must be >= 0, got {self.assumed_cost_on_raise!r}; "
                "it is the conservative spend booked when a rollout raises with an "
                "unknowable cost — a negative would credit spend on failure"
            )

    def is_exhausted(self, spent_usd: float) -> bool:
        """True once accumulated spend has reached the ceiling (``>=``).

        Example:
            >>> BudgetGuard(cost_ceiling_usd=10.0, assumed_cost_on_raise=0.5).is_exhausted(10.0)
            True
        """
        return spent_usd >= self.cost_ceiling_usd

    def remaining(self, spent_usd: float) -> float:
        """Dollars left before the ceiling (clamped at 0 — never negative)."""
        return max(0.0, self.cost_ceiling_usd - spent_usd)
