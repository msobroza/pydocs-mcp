"""harness/ask_your_docs/model_turns — which model turn proposed each served call.

The join is what makes ``parallel_calls_per_turn`` and the fan-out-where-batch
component mean anything: without it every call of a run reads as one turn. These
tests pin the three things that can go wrong — the turn index itself, a
within-turn reordering (parallel calls finish in any order), and a proposal the
server never saw.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.model_turns import (
    MODEL_TURNS_FILENAME,
    MODEL_TURNS_SCHEMA_VERSION,
    ProposedCall,
    join_model_turns,
    proposed_calls,
    write_model_turns,
)


class FakeModelMessage:
    """A message duck-typed like langchain's, with no langchain in the test."""

    def __init__(self, kind: str, tool_calls: list[dict] | None = None) -> None:
        self.type = kind
        self.tool_calls = tool_calls or []


def _turn(calls: list[tuple[str, dict]]) -> FakeModelMessage:
    return FakeModelMessage(
        "ai", [{"name": name, "args": args, "id": name} for name, args in calls]
    )


def test_proposed_calls_number_turns_over_model_messages_only() -> None:
    messages = [
        FakeModelMessage("human"),
        _turn([("get_symbol", {"target": "a.B"}), ("get_symbol", {"target": "c.D"})]),
        FakeModelMessage("tool"),
        _turn([("search_codebase", {"query": "q"})]),
    ]

    assert [(c.turn, c.tool_name) for c in proposed_calls(messages)] == [
        (1, "get_symbol"),
        (1, "get_symbol"),
        (2, "search_codebase"),
    ]


def test_proposed_calls_keeps_the_models_own_arguments() -> None:
    (call,) = proposed_calls([_turn([("grep", {"pattern": "x"})])])

    assert call.args == {"pattern": "x"}


def test_join_stamps_each_server_call_with_its_proposing_turn() -> None:
    proposals = proposed_calls(
        [
            _turn([("get_symbol", {"target": "a.B"}), ("get_symbol", {"target": "c.D"})]),
            _turn([("search_codebase", {"query": "q"})]),
        ]
    )

    join = join_model_turns(proposals, ["get_symbol", "get_symbol", "search_codebase"])

    assert join.server_turns == (1, 1, 2)
    assert join.client_only == ()


def test_join_survives_parallel_calls_observed_out_of_order() -> None:
    """Parallel calls of DIFFERENT tools can reach the server in any order."""
    proposals = proposed_calls(
        [
            _turn([("get_symbol", {"target": "a.B"}), ("search_codebase", {"query": "q"})]),
            _turn([("get_context", {"targets": ["a.B", "c.D"]})]),
        ]
    )

    join = join_model_turns(proposals, ["search_codebase", "get_symbol", "get_context"])

    assert join.server_turns == (1, 1, 2)


def test_join_sets_aside_a_proposal_no_server_call_consumed() -> None:
    proposals = proposed_calls(
        [_turn([("search_codebase", {"query": "q"}), ("reinspect_images", {"names": ["a"]})])]
    )

    join = join_model_turns(proposals, ["search_codebase"])

    assert join.server_turns == (1,)
    assert [(c.turn, c.tool_name) for c in join.client_only] == [(1, "reinspect_images")]


def test_join_gives_an_unclaimed_server_call_the_previous_turn() -> None:
    """A capture disagreement degrades one stamp; it never fails the run."""
    proposals = (ProposedCall(turn=2, tool_name="get_symbol", args={}),)

    join = join_model_turns(proposals, ["get_symbol", "grep"])

    assert join.server_turns == (2, 2)


def test_write_model_turns_keys_the_map_by_the_recorders_seq(tmp_path: Path) -> None:
    path = write_model_turns(tmp_path, seqs=[1, 2, 5], turns=[1, 1, 3])

    assert path.name == MODEL_TURNS_FILENAME
    assert json.loads(path.read_text()) == {
        "schema_version": MODEL_TURNS_SCHEMA_VERSION,
        "turns": {"1": 1, "2": 1, "5": 3},
    }
