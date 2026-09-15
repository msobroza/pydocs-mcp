"""Did any call put a gold file in front of the model, and which one first?

Two numbers over one trajectory's recorded tool calls, both read off the SAME
predicate so they can never disagree:

- **tool calls to first gold** — the 1-indexed position, in call order, of the
  first call that surfaced a gold file; ``None`` when none ever did.
- **needle reached** — whether ANY call surfaced a gold file, by any tool. A
  search hit, a symbol lookup, a read, a grep and a reference edge all count:
  the question is whether the evidence reached the model at all, not whether the
  ranked retriever is what delivered it.

``needle_reached`` is therefore ``tool_calls_to_first_gold(...) is not None`` by
construction, which is why both live here rather than beside a second copy of
the path-matching rule. A row's path is folded through the one path normalizer
(``path_normalizer``) and compared to the gold set exactly, so a dependency file
outside the workspace can never match a workspace-relative gold path.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydocs_eval.trajectory.path_normalizer import normalize_path
from pydocs_eval.trajectory.schema import ToolEvent


def surfaced_paths(event: ToolEvent, *, workspace_root: str) -> frozenset[str]:
    """The gold-matchable, workspace-relative paths one call returned.

    Rows without a usable ``path`` atom (reference edges, decision rows) and
    rows outside the workspace (dependency files) are dropped — neither can
    match a gold path, which is workspace-relative by construction.
    """
    paths: set[str] = set()
    for item in event.result_ids or ():
        raw = item.get("path")
        if not isinstance(raw, str) or not raw:
            continue
        norm = normalize_path(raw, workspace_root=workspace_root)
        if norm.gold_matchable:
            paths.add(norm.value)
    return frozenset(paths)


def surfaces_gold(event: ToolEvent, gold_files: frozenset[str], *, workspace_root: str) -> bool:
    """True when any row of ``event`` normalizes into ``gold_files``."""
    return bool(surfaced_paths(event, workspace_root=workspace_root) & gold_files)


def tool_calls_to_first_gold(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> int | None:
    """Number of tool calls (seq order) through the first to surface a gold file.

    ``None`` when no tool call ever surfaces a gold file. Counts tool events
    only (loop Reads are not MCP tool calls). 1-indexed: the first call
    surfacing a gold file returns ``1``.
    """
    ordered = sorted(tool_events, key=lambda e: e.seq)
    for index, event in enumerate(ordered, start=1):
        if surfaces_gold(event, gold_files, workspace_root=workspace_root):
            return index
    return None


def needle_reached(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> bool:
    """Whether ANY tool call of the trajectory surfaced a gold file.

    The same question :func:`tool_calls_to_first_gold` answers with a position,
    answered with a yes/no — one predicate, so a run can never report a first
    gold call and an unreached needle.

    Example:
        >>> from pydocs_eval.trajectory.schema import ToolEvent
        >>> e = ToolEvent(event_id="e", trajectory_id="t", seq=1, ts=0.0, turn=1,
        ...     tool="read_file", args={}, latency_ms=1.0,
        ...     result_ids=({"path": "a.py"},))
        >>> needle_reached([e], frozenset({"a.py"}), workspace_root="/ws")
        True
    """
    return any(
        surfaces_gold(event, gold_files, workspace_root=workspace_root) for event in tool_events
    )
