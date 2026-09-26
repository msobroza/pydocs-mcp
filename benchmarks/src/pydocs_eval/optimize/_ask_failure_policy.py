"""The eval side's failed-run policy: a run that fails still scores, as a sentinel.

The product binding owns the whole rollout (held serve session, trace, guidance
delivery). :class:`TimeoutBoundedAskRunner` wraps it with the one policy the
eval side adds — a hung run (the per-task timeout) or a runaway candidate (the
typed turn-budget error) comes back as a FAILED trajectory rather than an
exception — and :func:`failed_trajectory` builds that sentinel.

``ask_binding`` re-exports the wrapper and stays its public import path: the
bridge registry there and this policy change for different reasons. Every
product import here is DEFERRED behind the ``[retrieval]`` guard, so importing
this module pulls in no ``pydocs_mcp``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

from pydocs_eval._retrieval_extra import raise_missing_retrieval_extra

if TYPE_CHECKING:
    from pydocs_mcp.harness.core.run_contract import HarnessRunner, Trajectory


def _run_contract() -> ModuleType:
    """The product run-contract module, imported behind the extras guard.

    DEFERRED (ADR 0009's 2026-07-27 amendment, route 1 in its guarded form):
    this module declares the run-contract coupling, but importing it eagerly
    would drag ``pydocs_mcp`` into the fitness REGISTRY population path — a
    base-install surface that must stay library-free. The guard turns a
    missing/too-old library into the actionable install hint.
    """
    try:
        import pydocs_mcp.harness.core.run_contract as contract
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    return contract


@dataclass(frozen=True, slots=True)
class TimeoutBoundedAskRunner:
    """Bounds one product harness run by the campaign's per-task timeout.

    The product binding owns the whole rollout (held serve session, trace,
    guidance delivery); this wrapper adds only the eval-side failure policy:
    a hung tool call (timeout) or a runaway candidate
    (``TurnBudgetExceededError``) yields a sentinel FAILED trajectory rather
    than an exception, so one bad candidate costs its own sample and never the
    whole campaign — the gates then fail that sample deterministically
    (``turns = cap + 1`` fails ``max_turns``; the empty answer fails
    ``min_answer_chars``).

    The two failures stay told apart: the typed error is flagged
    ``budget_exhausted``, a timeout ``timed_out`` — flags a product declares
    from issue #371 on (see :func:`_declared_flags`).
    """

    inner: HarnessRunner
    task_timeout_seconds: float
    max_agent_turns: int

    async def run(
        self, sample: Mapping[str, object], guidance_sections: Mapping[str, str]
    ) -> Trajectory:
        turn_budget_exceeded = _run_contract().TurnBudgetExceededError
        started = time.monotonic()
        try:
            return await asyncio.wait_for(
                self.inner.run(sample, guidance_sections), timeout=self.task_timeout_seconds
            )
        except turn_budget_exceeded as exc:
            return _budget_exhausted_trajectory(
                exc, max_agent_turns=self.max_agent_turns, wall_seconds=time.monotonic() - started
            )
        except TimeoutError:
            # A killed run reports no spend and, until the product exposes where
            # it was writing, no trace: the traceless sentinel, flagged.
            return failed_trajectory(
                turns=_traceless_sentinel_turns(self.max_agent_turns),
                wall_seconds=time.monotonic() - started,
                timed_out=True,
            )


def _traceless_sentinel_turns(max_agent_turns: int) -> int:
    """One past the budget: the turn count that makes the ``max_turns`` gate fail a run."""
    return max_agent_turns + 1


def _budget_exhausted_trajectory(
    exc: BaseException, *, max_agent_turns: int, wall_seconds: float
) -> Trajectory:
    """The typed turn-budget error as a failed trajectory, keeping its trace when it has one.

    From issue #371 on the product's error carries the run's id, trace directory
    and turn count; an older product's carries none, and neither does one raised
    with defaults. A metered engine reports what the capped run already cost
    (see :func:`failed_trajectory`).

    WHY the error's own ``turns`` is read only WITH an id: from issue #371 on it
    defaults to ``turn_limit`` — the cap itself — which PASSES the optimizer's
    ``max_turns`` gate. A traceless error must stay the cap + 1 sentinel it has
    always been, failing that gate and booked by an arm as infra.
    """
    trajectory_id = str(getattr(exc, "trajectory_id", ""))
    traced = bool(trajectory_id)
    sentinel = _traceless_sentinel_turns(max_agent_turns)
    return failed_trajectory(
        turns=int(getattr(exc, "turns", sentinel)) if traced else sentinel,
        wall_seconds=wall_seconds,
        cost_usd=float(getattr(exc, "cost_usd", 0.0)),
        trajectory_id=trajectory_id,
        trace_dir=Path(getattr(exc, "trace_dir", _NO_TRACE_DIR)) if traced else _NO_TRACE_DIR,
        budget_exhausted=True,
    )


# The trace directory of a run that has none — the run contract's own spelling
# (``Trajectory.trace_dir`` is a ``Path``, never ``None``).
_NO_TRACE_DIR = Path()


def failed_trajectory(
    *,
    turns: int,
    wall_seconds: float,
    cost_usd: float = 0.0,
    trajectory_id: str = "",
    trace_dir: Path = _NO_TRACE_DIR,
    budget_exhausted: bool = False,
    timed_out: bool = False,
) -> Trajectory:
    """The sentinel a timed-out / runaway candidate scores as.

    WHY cost_usd defaults to 0.0: the in-process agent runs against an
    OpenAI-compatible endpoint whose pricing the harness cannot know (often a
    local server); its metered spend is the judge arm, bounded by
    ``budget.max_judge_calls`` — the documented spend asymmetry, mirrored in
    the runbook. A METERED harness (the external CLI track) does know, and
    carries the figure on ``TurnBudgetExceededError.cost_usd``: dropping it
    would enforce ``budget.max_usd`` against a number arbitrarily below actual
    spend, since turn-capping is a common failure mode on a long-horizon arm.

    A run that kept its trace keeps its calls too: the SERVER slice is read
    back from that trace, the single source of tool-call truth.
    """
    contract = _run_contract()
    failed: Trajectory = contract.Trajectory(
        trajectory_id=trajectory_id,
        trace_dir=trace_dir,
        answer="",
        tool_calls=_recorded_server_calls(trace_dir) if trajectory_id else (),
        turns=turns,
        cost_usd=cost_usd,
        wall_seconds=wall_seconds,
        **_declared_flags(
            contract.Trajectory, budget_exhausted=budget_exhausted, timed_out=timed_out
        ),
    )
    return failed


def _declared_flags(trajectory_type: type, **flags: bool) -> dict[str, bool]:
    """The outcome flags the product's ``Trajectory`` declares, and only those.

    Issue #371 adds ``budget_exhausted`` and ``timed_out``; an older product has
    neither, and passing them would raise. Dropping them there loses nothing:
    an older product's failed run never carries a trace, so an arm books it as
    infra and no outcome is ever read off it.
    """
    declared = {each.name for each in dataclasses.fields(trajectory_type)}
    return {name: value for name, value in flags.items() if name in declared}


def _recorded_server_calls(trace_dir: Path) -> tuple[object, ...]:
    """The calls a traced failed run recorded, read the way the product binding reads them."""
    try:
        from pydocs_mcp.observability.trace_reader import read_tool_call_records
    except ImportError as exc:
        raise_missing_retrieval_extra(exc)
    return tuple(read_tool_call_records(trace_dir))
