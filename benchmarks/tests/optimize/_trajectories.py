"""Shared builders for the run contract's ``Trajectory`` in optimize tests.

One named helper instead of a per-file fake dataclass: gates, checks, and the
ask_rubric fitness all read the SAME product type now (run-contract design
§2/§3), so the tests should exercise that type — in particular the SERVER vs
CLIENT observation split the ``used_indexed_tools`` gate depends on.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.harness.platform.contract import (
    ToolCallObservation,
    ToolCallRecord,
    Trajectory,
)

_DEFAULT_ANSWER = "x" * 100


def server_call(tool_name: str) -> ToolCallRecord:
    """A tool call the pydocs-mcp server recorded (the authoritative slice)."""
    return ToolCallRecord(
        tool_name=tool_name, args_digest="d", observed_by=ToolCallObservation.SERVER
    )


def client_call(tool_name: str) -> ToolCallRecord:
    """A tool call only the harness saw — never counts as indexed-tool use."""
    return ToolCallRecord(
        tool_name=tool_name, args_digest="d", observed_by=ToolCallObservation.CLIENT
    )


def make_trajectory(
    *,
    answer: str = _DEFAULT_ANSWER,
    tool_calls: tuple[ToolCallRecord, ...] = (),
    turns: int = 3,
    wall_seconds: float = 10.0,
    cost_usd: float = 0.0,
    trajectory_id: str = "traj",
    trace_dir: Path | None = None,
) -> Trajectory:
    """One trajectory with test-friendly defaults (answer long enough to pass gates).

    ``trace_dir`` defaults to a RELATIVE, NON-EXISTENT directory — the shape the
    trajectory-grounded checks must survive (an absent capture reads as no
    evidence, never an exception). Pass a real one to exercise the trace.
    """
    return Trajectory(
        trajectory_id=trajectory_id,
        trace_dir=trace_dir if trace_dir is not None else Path("traces") / trajectory_id,
        answer=answer,
        tool_calls=tool_calls,
        turns=turns,
        cost_usd=cost_usd,
        wall_seconds=wall_seconds,
    )
