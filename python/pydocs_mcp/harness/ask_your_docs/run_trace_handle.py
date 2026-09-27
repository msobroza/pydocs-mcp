"""Where an ask run is writing its trace, for a caller that may kill the run.

The turn-efficiency spec's step 2a eval seam
(``docs/superpowers/specs/2026-09-25-ask-turn-efficiency-program-design.md``, docs
PR #365). The run contract's port is
``run(sample, guidance_sections) -> Trajectory``, and a run a caller's timeout
cancels returns nothing — so the trajectory id and trace directory the binding
minted were lost with it, and an eval arm booked a timed-out run as infra instead
of a measured timeout. A caller that wants them sets a fresh
:class:`AskRunTraceHandle` in :data:`ACTIVE_RUN_TRACE_HANDLE` before awaiting the
run. The binding records the run's identity into it the moment it mints it, and the
graph's messages as each step lands, so a killed run still leaves what it had.

WHY a ContextVar and not a parameter: the port is frozen, and the handle must be
per call — one runner serves many runs, each in its own task, and a ContextVar
follows the task. The default, :data:`NO_RUN_TRACE_HANDLE`, records nothing, so a
caller that sets none (the page, the CLI, a test) runs exactly as before.

Example:
    >>> handle = AskRunTraceHandle()
    >>> token = ACTIVE_RUN_TRACE_HANDLE.set(handle)
    >>> ACTIVE_RUN_TRACE_HANDLE.get() is handle
    True
    >>> ACTIVE_RUN_TRACE_HANDLE.reset(token)
"""

from __future__ import annotations

from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from pydocs_mcp.harness.ask_your_docs.binding_trajectory import model_reply_count
from pydocs_mcp.harness.core.run_contract import NO_TRACE_DIR


class AskRunTraceHandle:
    """One run's trajectory id, trace directory and progress, filled by the binding.

    ``messages`` is the graph's latest state and ``turns`` its model replies, counted
    by the same rule as ``Trajectory.turns`` — counted as they land, so reading them
    after a kill needs nothing but this object. Until the binding records, the fields
    are the run contract's "no trace" pair, no messages and no turns.

    WHY mutable, against the repo's frozen value objects: this is an accumulator the
    caller holds while the binding writes into it. A killed run returns nothing, so a
    frozen value would need a return path the frozen port does not have.
    """

    __slots__ = ("messages", "trace_dir", "trajectory_id", "turns")

    def __init__(self) -> None:
        self.trajectory_id = ""
        self.trace_dir = NO_TRACE_DIR
        self.messages: tuple[Any, ...] = ()
        self.turns = 0

    def record_identity(self, trajectory_id: str, trace_dir: Path) -> None:
        """Where the run writes: its id and its per-trajectory trace directory."""
        self.trajectory_id = trajectory_id
        self.trace_dir = trace_dir

    def record_messages(self, messages: Sequence[Any]) -> None:
        """The graph's messages after its latest step, and the model replies among them."""
        self.messages = tuple(messages)
        self.turns = model_reply_count(self.messages)


class NullAskRunTraceHandle(AskRunTraceHandle):
    """The handle nobody asked for: it records nothing, so it can be shared by every run.

    WHY a Null Object and not ``AskRunTraceHandle | None``: the binding records into
    whatever handle is active, unguarded (CLAUDE.md, Null Object pattern), and a run
    nobody waits on keeps the empty fields — so the kill path stamps nothing for it.
    """

    __slots__ = ()

    def record_identity(self, trajectory_id: str, trace_dir: Path) -> None:
        """Nothing to keep: no caller is waiting to read where this run writes."""

    def record_messages(self, messages: Sequence[Any]) -> None:
        """Nothing to keep: no caller will stamp this run's sidecars if it is killed."""


NO_RUN_TRACE_HANDLE = NullAskRunTraceHandle()
ACTIVE_RUN_TRACE_HANDLE: ContextVar[AskRunTraceHandle] = ContextVar(
    "active_ask_run_trace_handle", default=NO_RUN_TRACE_HANDLE
)


__all__ = (
    "ACTIVE_RUN_TRACE_HANDLE",
    "NO_RUN_TRACE_HANDLE",
    "AskRunTraceHandle",
    "NullAskRunTraceHandle",
)
