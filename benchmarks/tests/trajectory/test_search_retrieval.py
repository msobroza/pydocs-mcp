"""Search-call retrieval metrics: recorded events plus a gold in, ranked scores out.

Five committed fixture trajectories drive the cases that matter — two searches
whose union covers a gold neither covers alone, a needle reached by a tool that
ranks nothing, the two definitions of a used call, and a trajectory that never
searched — each pinning the FULL block the reports print, so a case is one
computed dict against one committed dict.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pydocs_eval.trajectory.attribution import Attribution, attribute_trajectory, load_events
from pydocs_eval.trajectory.eval_report import no_report_outcome
from pydocs_eval.trajectory.gold_reach import needle_reached, tool_calls_to_first_gold
from pydocs_eval.trajectory.metrics import compute_metrics
from pydocs_eval.trajectory.schema import ToolEvent
from pydocs_eval.trajectory.search_retrieval import (
    RETRIEVAL_K,
    score_search_calls,
)
from pydocs_eval.trajectory.tool_usage import UsedCallDefinition, compute_tool_usage

_CASES = Path(__file__).parent / "fixtures" / "trajectories" / "retrieval"
_CASE_NAMES = (
    "union_of_reformulations",
    "needle_through_read_file",
    "used_calls_attributed",
    "used_calls_not_needless",
    "no_search_calls",
)


def _tool(
    seq: int,
    name: str = "search_codebase",
    args: dict[str, Any] | None = None,
    ids: tuple[dict[str, Any], ...] | None = None,
    *,
    turn: int = 1,
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
        result_ids=ids,
    )


def _search(seq: int, query: str, *paths: str) -> ToolEvent:
    """One search call returning ``paths`` as its ranked rows."""
    return _tool(seq, args={"query": query}, ids=tuple({"path": p} for p in paths))


def _case(name: str) -> tuple[list[ToolEvent], dict[str, Any], Attribution | None]:
    """A committed case: its tool events, its meta, and its attribution if it has one."""
    case_dir = _CASES / name
    meta = json.loads((case_dir / "meta.json").read_text(encoding="utf-8"))
    events = load_events(case_dir / "events.jsonl")
    patch_files = meta.get("final_patch_files")
    attribution = (
        None
        if patch_files is None
        else attribute_trajectory(
            events,
            final_patch_files=frozenset(patch_files),
            workspace_root=meta["workspace_root"],
        )
    )
    return [e for e in events if isinstance(e, ToolEvent)], meta, attribution


def _block(
    events: list[ToolEvent], meta: dict[str, Any], attribution: Attribution | None
) -> dict[str, object]:
    """The retrieval + usage + gold-reach numbers as the reports assemble them."""
    gold = frozenset(meta["gold_files"])
    workspace_root = meta["workspace_root"]
    return {
        **score_search_calls(events, gold).to_dict(),
        **compute_tool_usage(
            events, workspace_root=workspace_root, attribution=attribution
        ).to_dict(),
        "needle_reached": needle_reached(events, gold, workspace_root=workspace_root),
        "tool_calls_to_first_gold": tool_calls_to_first_gold(
            events, gold, workspace_root=workspace_root
        ),
    }


# --- fixture-driven cases ---------------------------------------------------


@pytest.mark.parametrize("case", _CASE_NAMES)
def test_fixture_case_matches_committed_expectations(case: str) -> None:
    """Each committed case computes exactly the metric block its meta declares."""
    events, meta, attribution = _case(case)

    assert _block(events, meta, attribution) == meta["expected"], meta["description"]


def test_the_union_of_two_reformulations_covers_a_gold_neither_covers_alone() -> None:
    """The point of the trajectory-level number: reformulations add up."""
    events, meta, _ = _case("union_of_reformulations")
    gold = frozenset(meta["gold_files"])

    retrieval = score_search_calls(events, gold)

    # Each call reaches one of the two gold files; only their union reaches both.
    assert retrieval.trajectory_recall_at_k[5] == 1.0
    assert all(score.recall_at_k[5] == 1.0 for score in retrieval.calls)
    assert score_search_calls(events[:1], gold).trajectory_recall_at_k[5] == 0.5
    assert score_search_calls(events[1:], gold).trajectory_recall_at_k[5] == 0.5


# --- per-call scores --------------------------------------------------------


def test_a_calls_scores_read_off_the_rank_of_its_first_gold_row() -> None:
    events = [_search(1, "where is routing", "other.py", "other2.py", "gold.py")]

    scores = score_search_calls(events, frozenset({"gold.py"})).calls[0]

    assert scores.recall_at_k == {1: 0.0, 5: 1.0, 10: 1.0}
    assert scores.hit_at_k == scores.recall_at_k  # one implementation, two names
    assert scores.mrr == pytest.approx(1 / 3)


def test_gold_matches_a_row_whose_path_carries_a_workspace_prefix() -> None:
    """Corpus dirs are materialized copies, so a row path may carry a prefix."""
    events = [_search(1, "routing", "/tmp/corpus7/src/pkg/router.py")]

    scores = score_search_calls(events, frozenset({"src/pkg/router.py"})).calls[0]

    assert scores.recall_at_k[1] == 1.0


def test_the_best_call_is_the_one_that_ranked_the_gold_highest() -> None:
    events = [_search(1, "vague", "other.py", "gold.py"), _search(2, "precise", "gold.py")]

    retrieval = score_search_calls(events, frozenset({"gold.py"}))

    assert retrieval.first_call is not None and retrieval.first_call.seq == 1
    assert retrieval.best_call is not None and retrieval.best_call.seq == 2


def test_the_best_call_breaks_a_tie_on_the_earlier_call() -> None:
    events = [_search(1, "first", "gold.py"), _search(2, "second", "gold.py")]

    best = score_search_calls(events, frozenset({"gold.py"})).best_call

    assert best is not None and best.seq == 1


def test_only_search_calls_are_scored() -> None:
    """A read that returns the gold is gold reach, not retrieval."""
    events = [_tool(1, "read_file", {"path": "gold.py"}, ({"path": "gold.py"},))]

    retrieval = score_search_calls(events, frozenset({"gold.py"}))

    assert retrieval.search_calls == 0
    assert retrieval.calls == ()


# --- reformulations ---------------------------------------------------------


def test_reformulations_count_distinct_queries_not_calls() -> None:
    events = [_search(1, "routing", "a.py"), _search(2, "routing", "a.py"), _search(3, "urls")]

    retrieval = score_search_calls(events, frozenset({"a.py"}))

    assert retrieval.search_calls == 3
    assert retrieval.reformulations == 2


# --- the zero cases ---------------------------------------------------------


def test_a_search_that_returns_nothing_is_a_measured_zero() -> None:
    events = [_search(1, "nothing matches")]

    retrieval = score_search_calls(events, frozenset({"gold.py"}))

    assert retrieval.trajectory_recall_at_k == dict.fromkeys(RETRIEVAL_K, 0.0)
    assert retrieval.calls[0].mrr == 0.0


def test_a_trajectory_that_never_searched_is_undefined_not_zero() -> None:
    retrieval = score_search_calls([_tool(1, "glob", {"pattern": "*.py"})], frozenset({"gold.py"}))

    assert retrieval.trajectory_recall_at_k == dict.fromkeys(RETRIEVAL_K, None)
    assert retrieval.first_call is None and retrieval.best_call is None


def test_a_task_without_gold_scores_nothing_rather_than_zero() -> None:
    """No gold means no retrieval question; a zero would drag an arm's mean down."""
    retrieval = score_search_calls([_search(1, "routing", "a.py")], frozenset())

    assert retrieval.search_calls == 1
    assert retrieval.reformulations == 1
    assert retrieval.trajectory_recall_at_k == dict.fromkeys(RETRIEVAL_K, None)
    assert retrieval.calls == ()


def test_the_empty_trajectory_reports_no_search_and_no_score() -> None:
    retrieval = score_search_calls((), frozenset({"gold.py"}))

    assert retrieval.to_dict() == {
        "search_calls": 0,
        "reformulations": 0,
        "trajectory_recall@1": None,
        "trajectory_recall@5": None,
        "trajectory_recall@10": None,
        "first_call": None,
        "best_call": None,
    }


# --- registration -----------------------------------------------------------


def test_compute_metrics_carries_the_retrieval_and_usage_blocks() -> None:
    """The metrics bundle every consumer receives exposes all three new blocks."""
    events = [_search(1, "where is routing", "gold.py")]
    attribution = attribute_trajectory(
        events, final_patch_files=frozenset({"gold.py"}), workspace_root="/ws"
    )

    metrics = compute_metrics(
        attribution=attribution,
        tool_events=events,
        loop_events=[],
        gold_files=frozenset({"gold.py"}),
        gold_line_map={},
        gold_f2p=[],
        gold_p2p=[],
        outcome=no_report_outcome("widgetlib__routing"),
        cost_usd=0.0,
        workspace_root="/ws",
    )

    assert metrics.needle_reached is True
    assert metrics.search_retrieval.trajectory_recall_at_k[1] == 1.0
    assert metrics.tool_usage.used_definition is UsedCallDefinition.ATTRIBUTED_EVIDENCE
    # The gold-reach pair stays consistent on the bundle, not only in isolation.
    assert metrics.needle_reached is (metrics.tool_calls_to_first_gold is not None)
