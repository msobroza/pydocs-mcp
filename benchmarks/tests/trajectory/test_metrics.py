"""Metric-library unit tests (plan R3): every metric has its own formula check,
plus one end-to-end ``compute_metrics`` over a committed fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.trajectory.attribution import attribute_trajectory, load_events
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome
from pydocs_eval.trajectory.gold_diff import target_line_map
from pydocs_eval.trajectory.metrics import (
    calls_by_tool,
    compute_metrics,
    deduped_token_totals,
    f2p_fraction,
    gold_file_recall,
    hunk_overlap,
    hunk_overlap_report,
    p2p_regression_count,
    per_tool_yield,
    tool_calls_to_first_gold,
    total_cost_usd,
    turn_count,
    wall_clock_seconds,
    wasted_read_ratio,
)
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent

_ATTR = Path(__file__).parent / "fixtures" / "trajectories" / "attribution"


def _tool(seq: int, name: str, ids=None, ts: float = 0.0) -> ToolEvent:
    return ToolEvent(
        event_id=f"e{seq}",
        trajectory_id="t",
        seq=seq,
        ts=ts,
        turn=seq,
        tool=name,
        args={},
        latency_ms=1.0,
        result_ids=ids,
    )


def _loop(mid: str | None, usage: dict, turn: int = 0) -> LoopEvent:
    return LoopEvent(
        event_id=f"l{mid}",
        trajectory_id="t",
        kind="assistant",
        turn=turn,
        message_id=mid,
        usage=usage,
        text="x",
    )


def _outcome(**kw) -> GroundTruthOutcome:
    base = dict(
        instance_id="i",
        resolved=False,
        patch_applied=True,
        infra_error=False,
        patch_apply_failed=False,
        f2p_passed=frozenset(),
        f2p_failed=frozenset(),
        p2p_passed=frozenset(),
        p2p_failed=frozenset(),
        upstream_resolved=None,
    )
    base.update(kw)
    return GroundTruthOutcome(**base)


# --- localization -----------------------------------------------------------


def test_gold_file_recall_formula() -> None:
    assert gold_file_recall(frozenset({"a"}), frozenset({"a", "b"})) == 0.5
    assert gold_file_recall(frozenset(), frozenset()) == 1.0  # vacuous


def test_wasted_read_ratio_formula() -> None:
    assert wasted_read_ratio(frozenset({"a", "b"}), frozenset({"a"})) == 0.5
    assert wasted_read_ratio(frozenset(), frozenset()) == 0.0  # nothing inspected


def test_hunk_overlap_formula() -> None:
    assert hunk_overlap(frozenset({1, 2, 3}), frozenset({2, 3})) == 1.0
    assert hunk_overlap(frozenset({1}), frozenset({2, 3})) == 0.0
    assert hunk_overlap(frozenset({5}), frozenset()) == 1.0  # no gold lines


def test_tool_calls_to_first_gold_counts_in_seq_order() -> None:
    events = [
        _tool(1, "glob", [{"path": "other.py"}]),
        _tool(2, "read_file", [{"path": "gold.py", "start_line": 1, "end_line": 2}]),
    ]
    assert tool_calls_to_first_gold(events, frozenset({"gold.py"}), workspace_root="/ws") == 2
    assert tool_calls_to_first_gold(events, frozenset({"nope.py"}), workspace_root="/ws") is None


def test_hunk_overlap_report_stamps_fidelity() -> None:
    content = _tool(1, "read_file", [{"path": "a.py", "start_line": 1, "end_line": 5}])
    attribution = attribute_trajectory(
        [content], final_patch_files=frozenset({"a.py"}), workspace_root="/ws"
    )
    report = hunk_overlap_report(attribution, {"a.py": frozenset({3})})
    assert report.by_file == {"a.py": 1.0}
    assert report.file_level_only == frozenset()


# --- per-tool yield ---------------------------------------------------------


def test_per_tool_yield_counts_by_tier() -> None:
    events = [
        _tool(1, "glob", [{"path": "a.py"}]),
        _tool(2, "read_file", [{"path": "a.py", "start_line": 1, "end_line": 2}]),
    ]
    attribution = attribute_trajectory(
        events, final_patch_files=frozenset({"a.py"}), workspace_root="/ws"
    )
    yields = per_tool_yield(attribution)
    assert yields["glob"].surfaced == 1 and yields["glob"].inspected == 0
    assert yields["read_file"].inspected == 1 and yields["read_file"].used == 1


# --- edit layer -------------------------------------------------------------


def test_f2p_fraction_formula() -> None:
    outcome = _outcome(f2p_passed=frozenset({"t::a"}))
    assert f2p_fraction(outcome, ["t::a", "t::b"]) == 0.5
    assert f2p_fraction(outcome, []) == 1.0


def test_p2p_regression_count_counts_missing_as_regression() -> None:
    outcome = _outcome(p2p_passed=frozenset({"t::a"}))
    assert p2p_regression_count(outcome, ["t::a", "t::b"]) == 1


def test_f2p_uses_harness_name_truncation() -> None:
    # A space-truncated parametrized gold id matches the harness-stored form.
    outcome = _outcome(f2p_passed=frozenset({"t::x[Invalid"}))
    assert f2p_fraction(outcome, ["t::x[Invalid foo]"]) == 1.0


# --- cost layer -------------------------------------------------------------


def test_deduped_token_totals_dedupes_by_message_id() -> None:
    dup = _loop("m", {"input_tokens": 5, "output_tokens": 2})
    other = _loop("n", {"input_tokens": 3, "output_tokens": 0})
    totals = deduped_token_totals([dup, dup, other])
    assert totals.input_tokens == 8 and totals.output_tokens == 2


def test_deduped_token_totals_sums_anonymous_usage() -> None:
    a = _loop(None, {"input_tokens": 4})
    b = _loop(None, {"input_tokens": 6})
    assert deduped_token_totals([a, b]).input_tokens == 10


def test_calls_by_tool_counts() -> None:
    assert calls_by_tool([_tool(1, "grep"), _tool(2, "grep"), _tool(3, "read_file")]) == {
        "grep": 2,
        "read_file": 1,
    }


def test_turn_count_distinct() -> None:
    assert turn_count([_tool(1, "grep", ts=0.0), _loop("m", {}, turn=2)]) == 2


def test_wall_clock_seconds_span() -> None:
    assert wall_clock_seconds([_tool(1, "grep", ts=10.0), _tool(2, "grep", ts=13.5)]) == 3.5
    assert wall_clock_seconds([_tool(1, "grep", ts=10.0)]) == 0.0


def test_total_cost_usd_validates() -> None:
    assert total_cost_usd(0.42) == 0.42
    with pytest.raises(ValueError):
        total_cost_usd(-1.0)
    with pytest.raises(ValueError):
        total_cost_usd("free")


# --- gold_diff target line map ----------------------------------------------


def test_target_line_map_covers_hunk_target_range() -> None:
    patch = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,2 +1,2 @@\n line\n-old\n+new\n"
    assert target_line_map(patch)["x.py"] == frozenset({1, 2})


# --- integration ------------------------------------------------------------


def test_compute_metrics_over_fixture() -> None:
    case = _ATTR / "search_surfaces_gold"
    meta = json.loads((case / "meta.json").read_text(encoding="utf-8"))
    events = load_events(case / "events.jsonl")
    tools = [e for e in events if isinstance(e, ToolEvent)]
    loops = [e for e in events if isinstance(e, LoopEvent)]
    attribution = attribute_trajectory(
        events,
        final_patch_files=frozenset(meta["final_patch_files"]),
        workspace_root=meta["workspace_root"],
    )
    gold_line_map = {k: frozenset(v) for k, v in meta["gold_line_map"].items()}
    metrics = compute_metrics(
        attribution=attribution,
        tool_events=tools,
        loop_events=loops,
        gold_files=frozenset(meta["gold_files"]),
        gold_line_map=gold_line_map,
        gold_f2p=["t::a"],
        gold_p2p=["t::b"],
        outcome=_outcome(f2p_passed=frozenset({"t::a"}), p2p_passed=frozenset({"t::b"})),
        cost_usd=0.1,
        workspace_root=meta["workspace_root"],
    )
    assert metrics.gold_file_recall == 1.0
    assert metrics.mean_hunk_overlap == meta["expected"]["mean_hunk_overlap"]
    assert metrics.f2p_fraction == 1.0
    assert metrics.p2p_regression_count == 0
    assert metrics.tool_calls == 1
