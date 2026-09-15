"""model_usage — the per-message spend the binding folds beside the trace.

Duck-typed messages only: the module imports no langchain, so these fakes are
the same shape a real run hands it — a ``type`` tag, ``usage_metadata``,
``response_metadata`` and an id.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.model_usage import (
    MODEL_USAGE_FILENAME,
    MessageUsage,
    message_usages,
    reported_cost_usd,
    write_model_usage,
)


class FakeModelMessage:
    """One AI message with whatever usage the endpoint chose to report."""

    type = "ai"

    def __init__(
        self,
        *,
        usage: dict | None = None,
        metadata: dict | None = None,
        identifier: str | None = None,
    ) -> None:
        self.usage_metadata = usage
        self.response_metadata = metadata
        self.id = identifier


class FakeHumanMessage:
    """The prompt. It carries no usage and must never become a record."""

    type = "human"
    usage_metadata = None


def _usage(**overrides: object) -> dict:
    return {"input_tokens": 100, "output_tokens": 20, **overrides}


def test_the_turn_counts_model_messages_only() -> None:
    messages = [
        FakeHumanMessage(),
        FakeModelMessage(usage=_usage()),
        FakeModelMessage(usage=_usage()),
    ]

    assert [record.turn for record in message_usages(messages)] == [1, 2]


def test_a_message_the_endpoint_priced_nothing_for_yields_no_record() -> None:
    """Recording zeros would state a measurement the endpoint never made."""
    records = message_usages([FakeModelMessage(usage=None), FakeModelMessage(usage=_usage())])

    assert len(records) == 1
    # The unreported message still advanced the turn, so turns stay aligned
    # with the turn sidecar's count of model messages.
    assert records[0].turn == 2


def test_reasoning_and_cache_slices_are_read_from_their_detail_blocks() -> None:
    message = FakeModelMessage(
        usage=_usage(
            output_token_details={"reasoning": 7},
            input_token_details={"cache_read": 4, "cache_creation": 1},
        )
    )

    record = message_usages([message])[0]

    assert record.reasoning_tokens == 7
    assert record.cache_read_input_tokens == 4
    assert record.cache_creation_input_tokens == 1


def test_an_endpoint_without_a_reasoning_count_reports_none() -> None:
    assert message_usages([FakeModelMessage(usage=_usage())])[0].reasoning_tokens is None


def test_the_message_id_rides_along_so_a_retry_can_be_deduped() -> None:
    record = message_usages([FakeModelMessage(usage=_usage(), identifier="msg-1")])[0]

    assert record.message_id == "msg-1"
    assert message_usages([FakeModelMessage(usage=_usage())])[0].message_id is None


def test_the_endpoint_price_is_found_wherever_the_client_put_it() -> None:
    assert reported_cost_usd({"cost": 0.5}) == 0.5
    assert reported_cost_usd({"token_usage": {"cost": 0.25}}) == 0.25
    assert reported_cost_usd({"usage": {"cost": 0.75}}) == 0.75


def test_an_endpoint_that_quotes_no_price_reports_none() -> None:
    assert reported_cost_usd({"token_usage": {"total_tokens": 10}}) is None
    assert reported_cost_usd(None) is None
    assert reported_cost_usd({"cost": "free"}) is None


def test_the_sidecar_is_written_even_when_nothing_was_reported(tmp_path: Path) -> None:
    """Present-and-empty means "we looked"; absent means "this run predates the fold"."""
    path = write_model_usage(tmp_path / "traj", ())

    assert path.name == MODEL_USAGE_FILENAME
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema_version": 1, "messages": []}


def test_the_sidecar_round_trips_one_record(tmp_path: Path) -> None:
    record = MessageUsage(
        turn=1,
        message_id="m1",
        input_tokens=100,
        output_tokens=20,
        reasoning_tokens=7,
        cache_read_input_tokens=4,
        cache_creation_input_tokens=0,
        reported_cost_usd=0.25,
    )

    path = write_model_usage(tmp_path / "traj", (record,))

    assert json.loads(path.read_text(encoding="utf-8"))["messages"] == [record.to_dict()]
