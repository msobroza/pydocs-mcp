"""Visible gold reach: the same three questions, asked of the rendered rows.

A search returns more rows than its token-budgeted text renders, so a returned
row is not proof the model read it. These pin the split — and pin that a
capture which cannot say answers undefined rather than zero.
"""

from __future__ import annotations

from typing import Any

from pydocs_eval.trajectory.gold_reach import (
    gold_visible,
    rendered_row_count,
    tool_calls_to_first_gold,
    tool_calls_to_first_visible_gold,
    visible_hit,
    visible_hit_rate,
    visible_paths,
)
from pydocs_eval.trajectory.schema import ToolEvent

_WORKSPACE = "/ws"
_GOLD = frozenset({"pkg/needle.py"})


def _event(
    seq: int,
    tool: str,
    paths: tuple[str, ...],
    *,
    rendered_rows: int | None = None,
) -> ToolEvent:
    rows: tuple[dict[str, Any], ...] = tuple({"path": p} for p in paths)
    return ToolEvent(
        event_id=f"t:{seq}",
        trajectory_id="t",
        seq=seq,
        ts=0.0,
        turn=1,
        tool=tool,
        args={},
        latency_ms=1.0,
        result_ids=rows,
        hit_count=len(rows),
        rendered_rows=rendered_rows,
    )


def _search(seq: int, paths: tuple[str, ...], *, rendered_rows: int | None = None) -> ToolEvent:
    return _event(seq, "search_codebase", paths, rendered_rows=rendered_rows)


# --- rendered_row_count ---


def test_a_search_names_how_many_rows_its_text_rendered() -> None:
    assert rendered_row_count(_search(1, ("a.py", "b.py"), rendered_rows=1)) == 1


def test_a_search_without_the_field_is_undefined_not_all_rows() -> None:
    """A version-1 capture cannot say; "all of them" would invent a reading."""
    assert rendered_row_count(_search(1, ("a.py", "b.py"))) is None


def test_a_tool_that_writes_its_whole_result_counts_every_row() -> None:
    """grep, read_file and the symbol tools render what they return."""
    assert rendered_row_count(_event(1, "grep", ("a.py", "b.py"))) == 2


# --- visible_hit, one call ---


def test_a_gold_row_inside_the_rendered_prefix_is_a_visible_hit() -> None:
    call = _search(1, ("pkg/needle.py", "pkg/other.py"), rendered_rows=1)
    assert visible_hit(call, _GOLD, workspace_root=_WORKSPACE) is True


def test_a_gold_row_past_the_rendered_prefix_is_not_visible() -> None:
    call = _search(1, ("pkg/other.py", "pkg/needle.py"), rendered_rows=1)
    assert visible_hit(call, _GOLD, workspace_root=_WORKSPACE) is False
    assert visible_paths(call, workspace_root=_WORKSPACE) == frozenset({"pkg/other.py"})


def test_a_call_the_capture_cannot_judge_is_undefined() -> None:
    assert visible_hit(_search(1, ("pkg/needle.py",)), _GOLD, workspace_root=_WORKSPACE) is None


# --- visible_hit_rate, per search call ---


def test_the_rate_is_the_share_of_judgeable_searches_that_showed_gold() -> None:
    events = (
        _search(1, ("pkg/other.py",), rendered_rows=1),
        _search(2, ("pkg/needle.py",), rendered_rows=1),
        _event(3, "grep", ("pkg/needle.py",)),  # not a search: outside the rate
    )
    assert visible_hit_rate(events, _GOLD, workspace_root=_WORKSPACE) == 0.5


def test_a_trajectory_that_never_searched_has_an_undefined_rate() -> None:
    events = (_event(1, "grep", ("pkg/needle.py",)),)
    assert visible_hit_rate(events, _GOLD, workspace_root=_WORKSPACE) is None


# --- gold_visible, per trajectory ---


def test_gold_seen_by_one_call_is_visible_however_ranked() -> None:
    events = (
        _search(1, ("pkg/other.py",), rendered_rows=1),
        _event(2, "read_file", ("pkg/needle.py",)),
    )
    assert gold_visible(events, _GOLD, workspace_root=_WORKSPACE) is True


def test_gold_no_call_rendered_is_not_visible() -> None:
    events = (_search(1, ("pkg/other.py", "pkg/needle.py"), rendered_rows=1),)
    assert gold_visible(events, _GOLD, workspace_root=_WORKSPACE) is False


def test_an_unjudgeable_call_makes_absence_undefined_but_not_presence() -> None:
    """True is monotone; False would be a claim the capture cannot support."""
    unknown = _search(1, ("pkg/other.py",))
    assert gold_visible((unknown,), _GOLD, workspace_root=_WORKSPACE) is None
    proven = _event(2, "read_file", ("pkg/needle.py",))
    assert gold_visible((unknown, proven), _GOLD, workspace_root=_WORKSPACE) is True


# --- tool_calls_to_first_visible_gold ---


def test_the_position_counts_calls_in_seq_order() -> None:
    events = (
        _search(2, ("pkg/needle.py",), rendered_rows=1),
        _search(1, ("pkg/other.py",), rendered_rows=1),
    )
    assert tool_calls_to_first_visible_gold(events, _GOLD, workspace_root=_WORKSPACE) == 2


def test_a_search_whose_text_cut_the_gold_row_does_not_count_as_reaching_it() -> None:
    """The returned-row reading says call 1; the read-row reading says never."""
    events = (_search(1, ("pkg/other.py", "pkg/needle.py"), rendered_rows=1),)
    assert tool_calls_to_first_gold(events, _GOLD, workspace_root=_WORKSPACE) == 1
    assert tool_calls_to_first_visible_gold(events, _GOLD, workspace_root=_WORKSPACE) is None


def test_an_unjudgeable_call_before_the_first_visible_gold_is_undefined() -> None:
    events = (_search(1, ("pkg/other.py",)), _event(2, "read_file", ("pkg/needle.py",)))
    assert tool_calls_to_first_visible_gold(events, _GOLD, workspace_root=_WORKSPACE) is None
