"""Needless-call rate + companions: recorded events in, metric values out.

One committed fixture per component of the rate, one for the pointer companion,
one trajectory where none of the conditions fire, and the empty-denominator
cases. Every fixture's ``meta.json`` carries the FULL expected metric block, so
a case is one computed dict against one committed dict.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydocs_eval.trajectory.attribution import attribute_trajectory, load_events
from pydocs_eval.trajectory.call_efficiency import (
    ResponseTextFromBlobs,
    batch_vs_fanout_ratio,
    compute_call_efficiency,
    fan_out_where_batch_calls,
    needless_call_rate,
    needless_call_report,
    parallel_calls_per_turn,
    pointer_followed_rate,
    resurfacing_calls,
    tool_mismatch_calls,
    zero_yield_calls,
)
from pydocs_eval.trajectory.eval_report import GroundTruthOutcome
from pydocs_eval.trajectory.metrics import compute_metrics
from pydocs_eval.trajectory.schema import ToolEvent

_FIXTURES = Path(__file__).parent / "fixtures" / "trajectories"
_CASES = _FIXTURES / "needless_calls"
_EMPTY_TRAJECTORY = _FIXTURES / "synthetic" / "empty_trajectory"

_CASE_NAMES = (
    "resurfacing_repeat_search",
    "zero_yield_empty_search",
    "fan_out_where_batch",
    "tool_mismatch_dotted_query",
    "pointer_followed",
    "clean_trajectory",
)


def _tool(
    seq: int,
    name: str,
    args: dict | None = None,
    ids=None,
    *,
    turn: int = 1,
    preview: str | None = None,
    error: dict | None = None,
) -> ToolEvent:
    return ToolEvent(
        event_id=f"e{seq}",
        trajectory_id="t",
        seq=seq,
        ts=float(seq),
        turn=turn,
        tool=name,
        args=args or {},
        latency_ms=1.0,
        error=error,
        result_ids=ids,
        result_preview=preview,
    )


def _tool_events(case_dir: Path) -> list[ToolEvent]:
    return [e for e in load_events(case_dir / "events.jsonl") if isinstance(e, ToolEvent)]


# --- fixture-driven cases ---------------------------------------------------


@pytest.mark.parametrize("case", _CASE_NAMES)
def test_fixture_case_matches_committed_expectations(case: str) -> None:
    """Each committed case computes exactly the metric block its meta declares."""
    case_dir = _CASES / case
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    computed = compute_call_efficiency(_tool_events(case_dir)).to_dict()
    assert computed == meta["expected"], f"{case}: {meta['description']}"


def test_empty_trajectory_fixture_reads_zero_and_undefined() -> None:
    """No tool calls: the call-share rates read 0.0, the opportunity rates None."""
    efficiency = compute_call_efficiency(_tool_events(_EMPTY_TRAJECTORY))
    assert efficiency.to_dict() == {
        "needless_call_rate": 0.0,
        "needless_calls": 0,
        "total_calls": 0,
        "resurfacing": 0,
        "zero_yield": 0,
        "fan_out_where_batch": 0,
        "tool_mismatch": 0,
        "pointer_followed_rate": None,
        "parallel_calls_per_turn": 0.0,
        "batch_vs_fanout_ratio": None,
        "batch_calls": 0,
        "fan_out_calls": 0,
    }


# --- components -------------------------------------------------------------


def test_resurfacing_needs_every_row_to_be_seen() -> None:
    """A call surfacing one new row among seen ones is not resurfacing."""
    a, b = {"path": "a.py"}, {"path": "b.py"}
    events = [_tool(1, "search_codebase", ids=(a,)), _tool(2, "search_codebase", ids=(a, b))]
    assert resurfacing_calls(events) == frozenset()
    events.append(_tool(3, "search_codebase", ids=(b, a)))
    assert resurfacing_calls(events) == frozenset({3})


def test_resurfacing_ignores_a_call_with_no_rows() -> None:
    """Surfacing nothing is zero-yield, never vacuous resurfacing."""
    events = [_tool(1, "search_codebase", ids=({"path": "a.py"},)), _tool(2, "glob", ids=())]
    assert resurfacing_calls(events) == frozenset()
    assert zero_yield_calls(events) == frozenset({2})


def test_zero_yield_excludes_errored_calls() -> None:
    events = [
        _tool(1, "get_symbol", {"target": "a.B"}, error={"type": "SymbolNotFoundError"}),
        _tool(2, "search_codebase", ids=()),
    ]
    assert zero_yield_calls(events) == frozenset({2})


def test_fan_out_charges_the_whole_group_only_at_the_threshold() -> None:
    """Two single-target calls in a turn are fine; three are one batch call."""
    pair = [_tool(i, "get_symbol", {"target": f"a.B{i}"}) for i in (1, 2)]
    assert fan_out_where_batch_calls(pair) == frozenset()
    trio = [*pair, _tool(3, "get_symbol", {"target": "a.B3"})]
    assert fan_out_where_batch_calls(trio) == frozenset({1, 2, 3})


def test_fan_out_is_scoped_to_one_turn_and_one_tool() -> None:
    spread = [_tool(i, "get_symbol", {"target": f"a.B{i}"}, turn=i) for i in (1, 2, 3)]
    assert fan_out_where_batch_calls(spread) == frozenset()
    mixed = [
        _tool(1, "get_symbol", {"target": "a.B"}),
        _tool(2, "get_references", {"target": "a.B", "direction": "callers"}),
        _tool(3, "get_references", {"target": "a.B", "direction": "callees"}),
        _tool(4, "get_references", {"target": "a.B", "direction": "impact"}),
    ]
    assert fan_out_where_batch_calls(mixed) == frozenset()  # references has no batch counterpart


def test_fan_out_counts_single_target_context_calls() -> None:
    """The context tool is its own batch counterpart: one target at a time fans out."""
    calls = [_tool(i, "get_context", {"targets": [f"a.B{i}"]}) for i in (1, 2, 3)]
    assert fan_out_where_batch_calls(calls) == frozenset({1, 2, 3})


def test_tool_mismatch_is_a_dotted_path_query() -> None:
    dotted = _tool(1, "search_codebase", {"query": "widgetlib.pricing.apply_discount"})
    prose = _tool(2, "search_codebase", {"query": "how discounts apply"})
    single = _tool(3, "search_codebase", {"query": "apply_discount"})
    path = _tool(4, "search_codebase", {"query": "widgetlib/pricing.py"})
    assert tool_mismatch_calls([dotted, prose, single, path]) == frozenset({1})


def test_tool_mismatch_only_charges_the_search_tool() -> None:
    symbol = _tool(1, "get_symbol", {"target": "widgetlib.pricing.apply_discount"})
    assert tool_mismatch_calls([symbol]) == frozenset()


def test_rate_counts_a_doubly_charged_call_once() -> None:
    """A call charged by two components is one needless call, not two."""
    events = [_tool(1, "search_codebase", {"query": "a.B.c"}, ids=())]
    report = needless_call_report(events)
    assert report.zero_yield == report.tool_mismatch == frozenset({1})
    assert report.rate == 1.0
    assert needless_call_rate(events) == 1.0


# --- companions -------------------------------------------------------------


def test_pointer_rate_counts_distinct_pointers_once() -> None:
    """A pointer repeated by two responses is one opportunity, not two."""
    offer = 'together: → get_symbol(target="a.B")'
    events = [
        _tool(1, "search_codebase", {"query": "b"}, ids=({"path": "a.py"},), preview=offer),
        _tool(2, "search_codebase", {"query": "c"}, ids=({"path": "b.py"},), preview=offer),
        _tool(3, "get_symbol", {"target": "a.B"}, ids=({"path": "c.py"},)),
    ]
    assert pointer_followed_rate(events) == 1.0


def test_pointer_rate_ignores_a_call_that_precedes_the_offer() -> None:
    """Only a call AFTER the response that offered a pointer follows it."""
    events = [
        _tool(1, "get_symbol", {"target": "a.B"}, ids=({"path": "a.py"},)),
        _tool(
            2,
            "search_codebase",
            {"query": "b"},
            ids=({"path": "b.py"},),
            preview='→ get_symbol(target="a.B")',
        ),
    ]
    assert pointer_followed_rate(events) == 0.0


def test_pointer_rate_is_undefined_when_nothing_was_offered() -> None:
    assert pointer_followed_rate([_tool(1, "glob", ids=({"path": "a.py"},))]) is None


def test_parallel_calls_per_turn_is_calls_over_calling_turns() -> None:
    burst = [_tool(1, "grep", turn=1), _tool(2, "grep", turn=1), _tool(3, "grep", turn=2)]
    assert parallel_calls_per_turn(burst) == 1.5
    assert parallel_calls_per_turn([]) == 0.0


def test_batch_ratio_is_the_batched_share_of_target_fetching_calls() -> None:
    batch = _tool(1, "get_context", {"targets": ["a.B", "c.D"]})
    single = _tool(2, "get_symbol", {"target": "a.B"})
    assert batch_vs_fanout_ratio([batch, single]) == 0.5
    assert batch_vs_fanout_ratio([batch]) == 1.0
    assert batch_vs_fanout_ratio([single]) == 0.0
    assert batch_vs_fanout_ratio([_tool(3, "grep", {"pattern": "x"})]) is None


# --- response-text sources --------------------------------------------------


def test_blob_reader_reads_the_full_response_text(tmp_path: Path) -> None:
    """The preview is byte-capped; the blob carries the text the pointers sit in."""
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    digest = "b" * 64
    payload = {"items": [], "meta": {}, "text": 'hit\nthen: → get_symbol(target="a.B")'}
    (blobs / digest).write_text(json.dumps(payload), encoding="utf-8")
    event = ToolEvent(
        event_id="e",
        trajectory_id="t",
        seq=1,
        ts=1.0,
        turn=1,
        tool="search_codebase",
        args={"query": "hit"},
        latency_ms=1.0,
        result_blob=digest,
    )
    text = ResponseTextFromBlobs(blobs)(event)
    assert text is not None and 'get_symbol(target="a.B")' in text


def test_blob_reader_reads_a_missing_blob_as_no_text(tmp_path: Path) -> None:
    """A pruned or never-written blob must not fail the metric."""
    read = ResponseTextFromBlobs(tmp_path / "blobs")
    assert read(_tool(1, "grep")) is None  # no blob recorded
    assert read(_tool(2, "grep")) is None


# --- registration -----------------------------------------------------------


def test_compute_metrics_carries_the_call_efficiency_block() -> None:
    """The metrics bundle every consumer receives exposes the four metrics."""
    events = [_tool(1, "search_codebase", {"query": "a.B.c"}, ids=())]
    attribution = attribute_trajectory(events, final_patch_files=frozenset(), workspace_root="/ws")
    metrics = compute_metrics(
        attribution=attribution,
        tool_events=events,
        loop_events=[],
        gold_files=frozenset(),
        gold_line_map={},
        gold_f2p=[],
        gold_p2p=[],
        outcome=GroundTruthOutcome(
            instance_id="i",
            resolved=False,
            patch_applied=False,
            infra_error=False,
            patch_apply_failed=False,
            f2p_passed=frozenset(),
            f2p_failed=frozenset(),
            p2p_passed=frozenset(),
            p2p_failed=frozenset(),
            upstream_resolved=None,
        ),
        cost_usd=0.0,
        workspace_root="/ws",
    )
    assert metrics.call_efficiency.needless.rate == 1.0
    assert metrics.call_efficiency.parallel_calls_per_turn == 1.0
