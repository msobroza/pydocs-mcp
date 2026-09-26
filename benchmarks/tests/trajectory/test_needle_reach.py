"""What the trajectory did AFTER the Needle reached the model.

Gold reached answers "did it get there at all"; these four answer "and then
what". Every number is read off a trajectory the product recorder wrote, through
the one gold predicate the other gold-reach numbers already share, and every
one is undefined — never zero — when the Needle never surfaced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.gold_reach import (
    calls_after_first_gold,
    calls_after_first_gold_read,
    tool_calls_to_first_gold_read,
    turns_after_first_gold,
)
from pydocs_eval.trajectory.schema import ToolEvent

from ._ask_traces import write_ask_trajectory

_WORKSPACE = "/ws"
_GOLD = frozenset({"pkg/needle.py"})
_NEEDLE: list[dict[str, Any]] = [{"path": "pkg/needle.py"}]
_ELSEWHERE: list[dict[str, Any]] = [{"path": "pkg/other.py"}]


def _events(
    tmp_path: Path, calls: list[tuple[str, int, list[dict[str, Any]]]]
) -> tuple[ToolEvent, ...]:
    """Record ``(tool, model turn, rows returned)`` calls and read them back."""
    trace_dir = write_ask_trajectory(
        tmp_path / "traces",
        calls=[(tool, {}, turn) for tool, turn, _ in calls],
        items_per_call=[rows for _, _, rows in calls],
    )
    return load_ask_trajectory_events(trace_dir).events


def test_the_worked_example_reads_all_four_numbers(tmp_path: Path) -> None:
    """A search shows the Needle at call 1 of turn 1; a read of it is call 3 of 5;
    the answer comes at turn 4."""
    events = _events(
        tmp_path,
        [
            ("search_codebase", 1, _NEEDLE),
            ("grep", 2, _ELSEWHERE),
            ("read_file", 2, _NEEDLE),
            ("get_overview", 3, _ELSEWHERE),
            ("search_codebase", 3, _ELSEWHERE),
        ],
    )

    assert turns_after_first_gold(events, _GOLD, workspace_root=_WORKSPACE, total_turns=4) == 3
    assert calls_after_first_gold(events, _GOLD, workspace_root=_WORKSPACE) == 4
    assert tool_calls_to_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) == 3
    assert calls_after_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) == 2


def test_a_needle_that_never_surfaced_leaves_all_four_undefined(tmp_path: Path) -> None:
    events = _events(tmp_path, [("search_codebase", 1, _ELSEWHERE), ("read_file", 2, _ELSEWHERE)])

    assert turns_after_first_gold(events, _GOLD, workspace_root=_WORKSPACE, total_turns=3) is None
    assert calls_after_first_gold(events, _GOLD, workspace_root=_WORKSPACE) is None
    assert tool_calls_to_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) is None
    assert calls_after_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) is None


def test_a_needle_shown_but_never_read_has_no_read_numbers(tmp_path: Path) -> None:
    """A search hit is not a read: the read numbers stay undefined."""
    events = _events(
        tmp_path, [("search_codebase", 1, _NEEDLE), ("grep", 2, _NEEDLE), ("glob", 2, _ELSEWHERE)]
    )

    assert calls_after_first_gold(events, _GOLD, workspace_root=_WORKSPACE) == 2
    assert tool_calls_to_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) is None
    assert calls_after_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) is None


def test_a_symbol_lookup_that_returns_the_needle_counts_as_its_read(tmp_path: Path) -> None:
    events = _events(tmp_path, [("get_symbol", 1, _NEEDLE), ("search_codebase", 2, _ELSEWHERE)])

    assert tool_calls_to_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) == 1
    assert calls_after_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) == 1


def test_a_read_of_another_file_is_not_the_needles_read(tmp_path: Path) -> None:
    events = _events(
        tmp_path,
        [("read_file", 1, _ELSEWHERE), ("search_codebase", 1, _NEEDLE), ("read_file", 2, _NEEDLE)],
    )

    assert tool_calls_to_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) == 3
    assert calls_after_first_gold_read(events, _GOLD, workspace_root=_WORKSPACE) == 0


def test_answering_straight_after_the_needle_is_one_turn_after_it(tmp_path: Path) -> None:
    """The needle reached the model in turn 2's results; turn 3 answered."""
    events = _events(tmp_path, [("search_codebase", 1, _ELSEWHERE), ("read_file", 2, _NEEDLE)])

    assert turns_after_first_gold(events, _GOLD, workspace_root=_WORKSPACE, total_turns=3) == 1
