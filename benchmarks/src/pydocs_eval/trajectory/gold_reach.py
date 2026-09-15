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

**Surfaced versus visible.** The three numbers above read every row a call
RETURNED. A search returns more rows than its token-budgeted text renders, so a
returned row is not proof the model read it. The ``visible_*`` trio answers the
same three questions over the rendered prefix only — ``rendered_rows``, schema
2. Undefined (``None``) rather than zero wherever a capture cannot say: a
version-1 trace, or a search recorded before the field existed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydocs_eval.trajectory.call_efficiency import SEARCH_TOOL
from pydocs_eval.trajectory.path_normalizer import normalize_path
from pydocs_eval.trajectory.schema import ToolEvent


def surfaced_paths(event: ToolEvent, *, workspace_root: str) -> frozenset[str]:
    """The gold-matchable, workspace-relative paths one call returned.

    Rows without a usable ``path`` atom (reference edges, decision rows) and
    rows outside the workspace (dependency files) are dropped — neither can
    match a gold path, which is workspace-relative by construction.
    """
    return _gold_matchable_paths(event.result_ids or (), workspace_root=workspace_root)


def _gold_matchable_paths(
    rows: Sequence[Mapping[str, Any]], *, workspace_root: str
) -> frozenset[str]:
    """The one path-matching rule, shared by the surfaced and visible readings."""
    paths: set[str] = set()
    for item in rows:
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


def rendered_row_count(event: ToolEvent) -> int | None:
    """How many of ``event``'s rows its response TEXT rendered.

    ``None`` — undefined — for a search whose capture predates
    ``rendered_rows``: the honest answer is "unknown", never "all of them".
    Every other tool writes its whole result into the text, so all its rows
    count, exactly as they did before the field existed.
    """
    if event.rendered_rows is not None:
        return event.rendered_rows
    return None if event.tool == SEARCH_TOOL else len(event.result_ids or ())


def visible_paths(event: ToolEvent, *, workspace_root: str) -> frozenset[str] | None:
    """:func:`surfaced_paths` restricted to the rows the text rendered."""
    count = rendered_row_count(event)
    if count is None:
        return None
    return _gold_matchable_paths((event.result_ids or ())[:count], workspace_root=workspace_root)


def visible_hit(
    event: ToolEvent, gold_files: frozenset[str], *, workspace_root: str
) -> bool | None:
    """Whether this ONE call's text put a gold row in front of the model."""
    paths = visible_paths(event, workspace_root=workspace_root)
    return None if paths is None else bool(paths & gold_files)


def visible_hit_rate(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> float | None:
    """Share of the trajectory's SEARCH calls whose text showed a gold row.

    ``None`` when the trajectory made no search call this capture can judge —
    an undefined rate, never a zero one.
    """
    judged = [
        hit
        for event in tool_events
        if event.tool == SEARCH_TOOL
        and (hit := visible_hit(event, gold_files, workspace_root=workspace_root)) is not None
    ]
    return sum(judged) / len(judged) if judged else None


def tool_calls_to_first_visible_gold(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> int | None:
    """Position (1-indexed, seq order) of the first call to make gold visible.

    ``None`` when gold never became visible, matching
    :func:`tool_calls_to_first_gold`'s "no value for this trajectory" — and
    also when an unjudgeable call comes first, because the gold the model saw
    may be exactly the one that call's text rendered.
    """
    for index, event in enumerate(sorted(tool_events, key=lambda e: e.seq), start=1):
        hit = visible_hit(event, gold_files, workspace_root=workspace_root)
        if hit is None:
            return None
        if hit:
            return index
    return None


def gold_visible(
    tool_events: Iterable[ToolEvent], gold_files: frozenset[str], *, workspace_root: str
) -> bool | None:
    """Whether ANY call's text put a gold row in front of the model.

    True is monotone — a visible gold stays visible whatever else is unknown —
    so an unjudgeable call only makes the answer undefined when no call proved
    a hit.
    """
    hits = [visible_hit(e, gold_files, workspace_root=workspace_root) for e in tool_events]
    if any(hit for hit in hits):
        return True
    return None if any(hit is None for hit in hits) else False
