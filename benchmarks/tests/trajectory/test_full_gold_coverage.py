"""Calls to full coverage: how many calls it took to surface EVERY gold file.

The multi-location twin of tool calls to first gold, read off the same surfaced
paths, so on a one-file needle the two coincide.
"""

from __future__ import annotations

from pydocs_eval.trajectory.gold_reach import (
    tool_calls_to_first_gold,
    tool_calls_to_full_gold_coverage,
)
from pydocs_eval.trajectory.schema import ToolEvent

_WORKSPACE = "/ws"


def _event(seq: int, *paths: str) -> ToolEvent:
    rows = tuple({"path": path} for path in paths)
    return ToolEvent(
        event_id=f"t:{seq}",
        trajectory_id="t",
        seq=seq,
        ts=0.0,
        turn=seq,
        tool="search_codebase",
        args={},
        latency_ms=1.0,
        result_ids=rows,
        hit_count=len(rows),
    )


def test_the_call_that_surfaces_the_last_unseen_gold_file_completes_coverage() -> None:
    events = [_event(1, "a.py", "x.py"), _event(2, "x.py"), _event(3, "b.py"), _event(4, "a.py")]

    assert (
        tool_calls_to_full_gold_coverage(
            events, frozenset({"a.py", "b.py"}), workspace_root=_WORKSPACE
        )
        == 3
    )


def test_a_gold_file_never_surfaced_leaves_coverage_undefined() -> None:
    events = [_event(1, "a.py"), _event(2, "x.py")]

    assert (
        tool_calls_to_full_gold_coverage(
            events, frozenset({"a.py", "b.py"}), workspace_root=_WORKSPACE
        )
        is None
    )


def test_calls_are_counted_in_recorded_order() -> None:
    events = [_event(3, "b.py"), _event(1, "a.py"), _event(2, "x.py")]

    assert (
        tool_calls_to_full_gold_coverage(
            events, frozenset({"a.py", "b.py"}), workspace_root=_WORKSPACE
        )
        == 3
    )


def test_on_one_file_it_is_the_first_gold_call() -> None:
    events = [_event(1, "x.py"), _event(2, "a.py"), _event(3, "a.py")]
    gold = frozenset({"a.py"})

    full = tool_calls_to_full_gold_coverage(events, gold, workspace_root=_WORKSPACE)

    assert full == tool_calls_to_first_gold(events, gold, workspace_root=_WORKSPACE) == 2


def test_an_empty_gold_set_has_nothing_to_cover() -> None:
    assert (
        tool_calls_to_full_gold_coverage(
            [_event(1, "a.py")], frozenset(), workspace_root=_WORKSPACE
        )
        is None
    )
