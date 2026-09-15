"""How many calls did the agent make, with which tools, and how many earned it?

Four counts over one trajectory's recorded tool calls: the total, how many
distinct tools it reached for, the per-tool breakdown, and how many of those
calls were actually USED. The first three are arithmetic; the fourth needs a
definition, and there are two — which one applied is reported alongside the
number, never left for the reader to guess:

- **attributed evidence** (``UsedCallDefinition.ATTRIBUTED_EVIDENCE``) — a call
  is used when a row it returned is part of the trajectory's attributed
  evidence (``attribution.used_files``: rows that reached the final patch). This
  is the definition whenever the run HAS attributed evidence.
- **not needless** (``UsedCallDefinition.NOT_NEEDLESS``) — a call is used when no
  needless-call component charged it. This is the fallback for a run that
  produces no patch to attribute against — a question-answering run, where the
  answer is prose and the evidence link is not recoverable from the trace.

The fallback is a weaker claim than the first (it says a call was not wasteful,
not that its rows were used), which is exactly why the definition travels with
the number.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.trajectory.attribution import Attribution
from pydocs_eval.trajectory.call_efficiency import needless_call_report
from pydocs_eval.trajectory.gold_reach import surfaced_paths
from pydocs_eval.trajectory.schema import ToolEvent


class UsedCallDefinition(StrEnum):
    """Which rule decided that a call counted as used."""

    ATTRIBUTED_EVIDENCE = "attributed_evidence"
    NOT_NEEDLESS = "not_needless"


def calls_by_tool(tool_events: Iterable[ToolEvent]) -> dict[str, int]:
    """Count of tool calls per tool name.

    Example:
        >>> from pydocs_eval.trajectory.schema import ToolEvent
        >>> e = ToolEvent(event_id="e", trajectory_id="t", seq=1, ts=0.0,
        ...     turn=1, tool="grep", args={}, latency_ms=1.0)
        >>> calls_by_tool([e, e])
        {'grep': 2}
    """
    return dict(Counter(event.tool for event in tool_events))


def used_call_seqs(
    tool_events: Iterable[ToolEvent], used_files: frozenset[str], *, workspace_root: str
) -> frozenset[int]:
    """Seqs of the calls that returned a row belonging to the attributed evidence."""
    return frozenset(
        event.seq
        for event in tool_events
        if surfaced_paths(event, workspace_root=workspace_root) & used_files
    )


@dataclass(frozen=True, slots=True)
class ToolUsage:
    """One trajectory's call counts, with the definition of "used" that applied."""

    tool_calls_total: int
    distinct_tools_used: int
    calls_by_tool: dict[str, int]
    tool_calls_used: int
    used_definition: UsedCallDefinition

    @property
    def used_call_ratio(self) -> float:
        """``used / total``; ``0.0`` when the trajectory made no call.

        A rate whose denominator counts CALLS, so it follows the call-share
        convention of the needed-call layer: no call means nothing was used,
        which is a measured zero rather than an undefined value.
        """
        if not self.tool_calls_total:
            return 0.0
        return self.tool_calls_used / self.tool_calls_total

    def to_dict(self) -> dict[str, object]:
        """Report-ready values, JSON types only."""
        return {
            "tool_calls_total": self.tool_calls_total,
            "distinct_tools_used": self.distinct_tools_used,
            "calls_by_tool": dict(self.calls_by_tool),
            "tool_calls_used": self.tool_calls_used,
            "used_call_definition": str(self.used_definition),
            "used_call_ratio": self.used_call_ratio,
        }


def _used_calls(
    events: tuple[ToolEvent, ...], attribution: Attribution | None, workspace_root: str
) -> tuple[int, UsedCallDefinition]:
    """How many calls were used, and under which definition it was decided."""
    used_files = frozenset() if attribution is None else attribution.used_files
    if used_files:
        return (
            len(used_call_seqs(events, used_files, workspace_root=workspace_root)),
            UsedCallDefinition.ATTRIBUTED_EVIDENCE,
        )
    report = needless_call_report(events)
    return report.total_calls - len(report.needless), UsedCallDefinition.NOT_NEEDLESS


def compute_tool_usage(
    tool_events: Iterable[ToolEvent],
    *,
    workspace_root: str,
    attribution: Attribution | None = None,
) -> ToolUsage:
    """Count one trajectory's calls and decide how many of them were used.

    Pass ``attribution`` whenever the run has one: a trajectory whose evidence
    reached a patch gets the attributed-evidence definition. An attribution that
    used nothing — the normal case for a question-answering run — falls back to
    "not needless" rather than reporting every call unused, which would read as
    a measurement and is not one.
    """
    events = tuple(tool_events)
    by_tool = calls_by_tool(events)
    used, definition = _used_calls(events, attribution, workspace_root)
    return ToolUsage(
        tool_calls_total=len(events),
        distinct_tools_used=len(by_tool),
        calls_by_tool=by_tool,
        tool_calls_used=used,
        used_definition=definition,
    )
