"""Tool-usage counts and gold reach: how many calls, and how many earned their place.

The two definitions of a used call are the subject here: the attributed-evidence
one when a trajectory has a patch to attribute against, and the "not needless"
fallback when it has none. A trajectory that reaches the gold through a read
rather than a search is the gold-reach counterpart.
"""

from __future__ import annotations

from typing import Any

import pytest

from pydocs_eval.trajectory.attribution import attribute_trajectory
from pydocs_eval.trajectory.gold_reach import (
    needle_reached,
    surfaces_gold,
    tool_calls_to_first_gold,
)
from pydocs_eval.trajectory.schema import ToolEvent
from pydocs_eval.trajectory.tool_usage import (
    UsedCallDefinition,
    calls_by_tool,
    compute_tool_usage,
)

_WORKSPACE = "/ws"


def _tool(
    seq: int,
    name: str,
    ids: tuple[dict[str, Any], ...] | None = None,
    args: dict[str, Any] | None = None,
) -> ToolEvent:
    return ToolEvent(
        event_id=f"e{seq}",
        trajectory_id="t",
        seq=seq,
        ts=float(seq),
        turn=seq,
        tool=name,
        args=args or {},
        latency_ms=1.0,
        result_ids=ids,
    )


def _rows(*paths: str) -> tuple[dict[str, Any], ...]:
    return tuple({"path": p} for p in paths)


# --- the counts -------------------------------------------------------------


def test_the_counts_cover_every_call_and_every_tool() -> None:
    events = [
        _tool(1, "search_codebase", _rows("a.py")),
        _tool(2, "search_codebase", _rows("b.py")),
        _tool(3, "read_file", _rows("b.py")),
    ]

    usage = compute_tool_usage(events, workspace_root=_WORKSPACE)

    assert usage.tool_calls_total == 3
    assert usage.distinct_tools_used == 2
    assert usage.calls_by_tool == {"search_codebase": 2, "read_file": 1}


def test_calls_by_tool_counts() -> None:
    assert calls_by_tool([_tool(1, "grep"), _tool(2, "grep"), _tool(3, "read_file")]) == {
        "grep": 2,
        "read_file": 1,
    }


def test_an_empty_trajectory_counts_nothing_and_its_ratio_reads_zero() -> None:
    """A rate whose denominator counts calls reads 0.0 when there were none."""
    usage = compute_tool_usage((), workspace_root=_WORKSPACE)

    assert usage.to_dict() == {
        "tool_calls_total": 0,
        "distinct_tools_used": 0,
        "calls_by_tool": {},
        "tool_calls_used": 0,
        "used_call_definition": "not_needless",
        "used_call_ratio": 0.0,
    }


# --- which calls count as used ----------------------------------------------


def _mixed_trajectory() -> list[ToolEvent]:
    """A call reaching the patched file, an aside, and a call yielding nothing."""
    return [
        _tool(1, "search_codebase", _rows("gold.py", "util.py"), {"query": "rounding"}),
        _tool(2, "get_symbol", _rows("helpers.py"), {"target": "pkg.helpers.render"}),
        _tool(3, "search_codebase", (), {"query": "audit"}),
    ]


def test_attribution_names_the_calls_whose_rows_became_evidence() -> None:
    events = _mixed_trajectory()
    attribution = attribute_trajectory(
        events, final_patch_files=frozenset({"gold.py"}), workspace_root=_WORKSPACE
    )

    usage = compute_tool_usage(events, workspace_root=_WORKSPACE, attribution=attribution)

    assert usage.used_definition is UsedCallDefinition.ATTRIBUTED_EVIDENCE
    assert usage.tool_calls_used == 1
    assert usage.used_call_ratio == pytest.approx(1 / 3)


def test_without_attribution_the_used_calls_are_the_ones_nothing_charged() -> None:
    events = _mixed_trajectory()

    usage = compute_tool_usage(events, workspace_root=_WORKSPACE)

    # Only the empty search is charged as needless, so the other two count.
    assert usage.used_definition is UsedCallDefinition.NOT_NEEDLESS
    assert usage.tool_calls_used == 2
    assert usage.used_call_ratio == pytest.approx(2 / 3)


def test_an_attribution_that_used_nothing_falls_back_rather_than_reading_zero() -> None:
    """A question-answering run has no patch, so its attribution used set is empty."""
    events = _mixed_trajectory()
    attribution = attribute_trajectory(
        events, final_patch_files=frozenset(), workspace_root=_WORKSPACE
    )

    usage = compute_tool_usage(events, workspace_root=_WORKSPACE, attribution=attribution)

    assert usage.used_definition is UsedCallDefinition.NOT_NEEDLESS
    assert usage.tool_calls_used == 2


# --- gold reach -------------------------------------------------------------


def test_the_needle_is_reached_by_any_tool_not_only_the_searcher() -> None:
    events = [
        _tool(1, "search_codebase", _rows("util.py"), {"query": "rounding"}),
        _tool(2, "read_file", _rows("gold.py"), {"path": "gold.py"}),
    ]
    gold = frozenset({"gold.py"})

    assert needle_reached(events, gold, workspace_root=_WORKSPACE) is True
    assert tool_calls_to_first_gold(events, gold, workspace_root=_WORKSPACE) == 2


def test_needle_reached_says_exactly_what_tool_calls_to_first_gold_says() -> None:
    """One predicate, so the two numbers can never disagree."""
    gold = frozenset({"gold.py"})
    for events in ([], [_tool(1, "grep", _rows("other.py"))], [_tool(1, "grep", _rows("gold.py"))]):
        reached = needle_reached(events, gold, workspace_root=_WORKSPACE)
        first = tool_calls_to_first_gold(events, gold, workspace_root=_WORKSPACE)
        assert reached is (first is not None)


def test_a_dependency_path_outside_the_workspace_never_reaches_the_gold() -> None:
    event = _tool(1, "read_file", _rows("/venv/lib/site-packages/gold.py"))

    assert surfaces_gold(event, frozenset({"gold.py"}), workspace_root=_WORKSPACE) is False
