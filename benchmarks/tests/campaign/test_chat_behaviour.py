"""The seven pre-registered chat behaviour counts, each against its definition.

Fed real langchain messages (the shapes the agent returns). The seeded search is the
harness's call, never the model's, so no count sees it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import HumanMessage

from pydocs_eval.campaign.chat_behaviour import behaviour_counts, model_turns, observed_calls

from ._chat_messages import MAXSIM, answer, call, conversation, result, turn


def _counts(*messages: object) -> dict[str, int]:
    return behaviour_counts(observed_calls([HumanMessage(content="q"), *messages, answer()]))


def test_each_behaviour_count_follows_its_definition() -> None:
    counts = behaviour_counts(observed_calls(conversation()))

    assert counts == {
        "get_overview_calls": 1,
        "overview_parallel_with_first_search": 1,
        # the card and its source view ask the same symbol: both belong to the chain
        "example_chain_calls": 2,
        "source_then_read_same_file": 1,
        "whole_file_reads": 1,
        "gap_marker_responses": 1,
        "grep_zero_hits": 1,
    }


def test_the_turns_calls_are_numbered_by_are_the_products_turns() -> None:
    """A row counts turns with the product's ONE rule; the calls' turn numbers must agree."""
    from pydocs_mcp.harness.ask_your_docs.binding_trajectory import model_reply_count

    messages = conversation()

    assert len(model_turns(messages)) == model_reply_count(messages) == 5


def test_the_seeded_search_is_the_harness_not_the_model() -> None:
    calls = observed_calls(conversation())

    assert [c.tool for c in calls][:2] == ["get_overview", "search_codebase"]
    assert all(c.turn >= 1 for c in calls)


def test_an_overview_after_the_first_search_is_not_parallel_with_it() -> None:
    counts = _counts(
        turn(call("a", "search_codebase", query="x")),
        result("a", "search_codebase", "hits"),
        turn(call("b", "get_overview")),
        result("b", "get_overview", "map"),
    )

    assert (counts["get_overview_calls"], counts["overview_parallel_with_first_search"]) == (1, 0)


def test_a_bounded_read_is_not_a_whole_file_read() -> None:
    counts = _counts(
        turn(call("a", "read_file", path="src/a.py", offset=10, limit=20)),
        result("a", "read_file", "window"),
    )

    assert counts["whole_file_reads"] == 0


def test_an_offset_only_read_runs_to_eof_so_it_counts() -> None:
    counts = _counts(
        turn(call("a", "read_file", path="src/a.py", offset=10)),
        result("a", "read_file", "tail"),
    )

    assert counts["whole_file_reads"] == 1


def test_the_whole_example_chain_counts_its_first_call_too() -> None:
    """Card, context, source of one symbol (system_v2 rule 5's chain): three calls."""
    counts = _counts(
        turn(call("a", "get_symbol", target=MAXSIM)),
        result("a", "get_symbol", "card"),
        turn(call("b", "get_context", targets=[MAXSIM])),
        result("b", "get_context", "context"),
        turn(call("c", "get_symbol", target=MAXSIM, depth="source")),
        result("c", "get_symbol", "source"),
        turn(call("d", "get_symbol", target="needle.pipeline.search")),
        result("d", "get_symbol", "another symbol, asked once"),
    )

    assert counts["example_chain_calls"] == 3


def test_a_read_of_a_file_no_earlier_view_showed_is_not_a_source_then_read_pair() -> None:
    counts = _counts(
        turn(call("a", "get_symbol", target=MAXSIM), call("b", "read_file", path="src/other.py")),
        result("a", "get_symbol", "src/needle/scoring/strategies.py:10-30"),
        result("b", "read_file", "other"),
    )

    assert counts["source_then_read_same_file"] == 0
