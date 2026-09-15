"""trajectory/token_accounting — what a trajectory spent, read back off the sidecar.

End-to-end where it matters: a fake model drives the PRODUCT binding, which
folds its messages into ``model_usage.json``; this module's reader turns that
file into loop events, and the suite's ONE token accounting sums them.

The discriminating cases are the ones a second, private summation would get
wrong: a retried message counted once, a reasoning count that is undefined
rather than zero, an endpoint that quotes no price, and a trajectory with no
sidecar at all — which must stay undefined instead of pulling a mean to zero.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pydocs_eval.trajectory.metrics import deduped_token_totals
from pydocs_eval.trajectory.token_accounting import (
    ASK_MODEL_USAGE_FILENAME,
    MalformedUsageSidecarError,
    account_for_events,
    account_for_trace,
    priced_usd,
    read_ask_usage_events,
)

# A thinking model's round: 7 of the 20 completion tokens were reasoning, and 4
# of the 100 prompt tokens came off the endpoint's cache.
_ROUND: dict[str, Any] = {
    "turn": 1,
    "message_id": "m1",
    "input_tokens": 100,
    "output_tokens": 20,
    "reasoning_tokens": 7,
    "cache_read_input_tokens": 4,
    "cache_creation_input_tokens": 0,
    "reported_cost_usd": None,
}


def _write_sidecar(trace_dir: Path, messages: list[dict[str, Any]]) -> Path:
    """Write a version-1 usage sidecar the way the product writer spells it."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / ASK_MODEL_USAGE_FILENAME
    path.write_text(json.dumps({"schema_version": 1, "messages": messages}), encoding="utf-8")
    return path


# --- reading the sidecar --------------------------------------------------


def test_a_retried_message_is_counted_once(tmp_path: Path) -> None:
    """The endpoint re-sends one message id; the shared dedupe bills it once."""
    _write_sidecar(tmp_path, [_ROUND, dict(_ROUND), {**_ROUND, "turn": 2, "message_id": "m2"}])

    totals = deduped_token_totals(read_ask_usage_events(tmp_path) or ())

    assert totals.input_tokens == 200
    assert totals.output_tokens == 40
    assert totals.reasoning_tokens == 14


def test_an_unreported_reasoning_count_is_undefined_not_zero(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, [{**_ROUND, "reasoning_tokens": None}])

    assert deduped_token_totals(read_ask_usage_events(tmp_path) or ()).reasoning_tokens is None


def test_a_trajectory_without_a_sidecar_reads_as_undefined(tmp_path: Path) -> None:
    """No sidecar means unrecorded spend — an empty tuple would claim a measured zero."""
    assert read_ask_usage_events(tmp_path) is None
    assert account_for_trace(tmp_path, usd_per_1m_input=1.0, usd_per_1m_output=2.0) is None


def test_an_endpoint_that_reported_nothing_leaves_a_measured_zero(tmp_path: Path) -> None:
    """An EMPTY sidecar is a measurement: the run happened and reported no tokens."""
    _write_sidecar(tmp_path, [])

    account = account_for_trace(tmp_path, usd_per_1m_input=1.0, usd_per_1m_output=2.0)

    assert account is not None
    assert account.total_tokens == 0
    assert account.tokens.reasoning_tokens is None
    assert account.reported_usd is None


def test_an_unreadable_sidecar_raises_instead_of_reading_as_zero(tmp_path: Path) -> None:
    (tmp_path / ASK_MODEL_USAGE_FILENAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(MalformedUsageSidecarError, match=ASK_MODEL_USAGE_FILENAME):
        read_ask_usage_events(tmp_path)


def test_a_sidecar_without_a_messages_list_names_the_expected_shape(tmp_path: Path) -> None:
    (tmp_path / ASK_MODEL_USAGE_FILENAME).write_text('{"schema_version": 1}', encoding="utf-8")

    with pytest.raises(MalformedUsageSidecarError, match="messages"):
        read_ask_usage_events(tmp_path)


def test_a_record_without_a_turn_names_the_offending_value(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, [{**_ROUND, "turn": "one"}])

    with pytest.raises(MalformedUsageSidecarError, match="expected an int"):
        read_ask_usage_events(tmp_path)


# --- the two dollar figures ----------------------------------------------


def test_reasoning_tokens_are_not_billed_on_top_of_the_completion(tmp_path: Path) -> None:
    """Reasoning is a SLICE of the completion, so pricing it again would double-charge."""
    _write_sidecar(tmp_path, [_ROUND])

    account = account_for_trace(tmp_path, usd_per_1m_input=1_000_000, usd_per_1m_output=2_000_000)

    assert account is not None
    assert account.estimated_usd == pytest.approx(100 * 1 + 20 * 2)
    assert account.cached_tokens == 4


def test_without_price_flags_the_estimate_is_zero(tmp_path: Path) -> None:
    _write_sidecar(tmp_path, [_ROUND])

    account = account_for_trace(tmp_path, usd_per_1m_input=0.0, usd_per_1m_output=0.0)

    assert account is not None
    assert account.estimated_usd == 0.0


def test_the_reported_price_sums_the_messages_that_quoted_one(tmp_path: Path) -> None:
    _write_sidecar(
        tmp_path,
        [
            {**_ROUND, "reported_cost_usd": 0.25},
            {**_ROUND, "turn": 2, "message_id": "m2", "reported_cost_usd": 0.75},
        ],
    )

    account = account_for_trace(tmp_path, usd_per_1m_input=0.0, usd_per_1m_output=0.0)

    assert account is not None
    assert account.reported_usd == pytest.approx(1.0)


def test_an_endpoint_that_quotes_no_price_reports_none(tmp_path: Path) -> None:
    """``None``, never ``0.0``: an unpriced endpoint has not said the run was free."""
    _write_sidecar(tmp_path, [_ROUND])

    account = account_for_trace(tmp_path, usd_per_1m_input=0.0, usd_per_1m_output=0.0)

    assert account is not None
    assert account.reported_usd is None


def test_priced_usd_is_per_million_tokens() -> None:
    assert priced_usd(
        input_tokens=2_000_000, output_tokens=1_000_000, usd_per_1m_input=3.0, usd_per_1m_output=5.0
    ) == pytest.approx(11.0)


def test_an_empty_event_sequence_prices_at_zero() -> None:
    account = account_for_events((), reported=None, usd_per_1m_input=1.0, usd_per_1m_output=1.0)

    assert account.estimated_usd == 0.0
    assert account.reported_usd is None
    assert account.total_tokens == 0


# --- across the packaging boundary ---------------------------------------


class FakeUsageScript:
    """A scripted run whose model messages carry usage metadata and a price quote.

    Wraps the turn script the ask-events tests already drive the binding with,
    so the trace, the turn sidecar and the usage sidecar all come out of one
    real binding run instead of hand-written bytes.
    """

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]]]) -> None:
        self._turns = turns

    async def __call__(self, **kwargs: Any) -> tuple[str, list[Any]]:
        from tests.trajectory.test_ask_events import FakeTurnScript

        answer, messages = await FakeTurnScript(self._turns)(**kwargs)
        return answer, [self._with_usage(m, index) for index, m in enumerate(messages)]

    def _with_usage(self, message: Any, index: int) -> Any:
        """Stamp usage on the model messages only; the human prompt carries none."""
        from langchain_core.messages import AIMessage

        if not isinstance(message, AIMessage):
            return message
        message.id = f"msg-{index}"
        message.usage_metadata = {
            "input_tokens": 5 + index,
            "output_tokens": 2 + index,
            "total_tokens": 7 + 2 * index,
            "output_token_details": {"reasoning": 1},
            "input_token_details": {"cache_read": 1},
        }
        message.response_metadata = {"token_usage": {"cost": 0.25}}
        return message


@pytest.mark.asyncio
async def test_the_product_binding_writes_a_sidecar_this_reader_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The binding's fold and this reader are ONE contract; exercise both halves."""
    pytest.importorskip("langchain_core")
    from pydocs_mcp.harness.ask_your_docs import binding

    monkeypatch.setattr(
        binding, "_build_and_execute", FakeUsageScript([[("search_codebase", {"query": "q"})]])
    )
    runner = binding.make_harness_runner(
        {"workspace": str(tmp_path / "ws"), "model": "m", "trace_root": str(tmp_path / "traces")}
    )

    trajectory = await runner.run(
        {
            "record_id": "r1",
            "task_name": "repo_qa",
            "rendered_prompt": "where is the router?",
            "gold": {"file_set": ["a.py"]},
        },
        {},
    )
    account = account_for_trace(
        trajectory.trace_dir, usd_per_1m_input=1_000_000, usd_per_1m_output=1_000_000
    )

    # Two model messages (the tool-calling one and the answer), indexes 1 and 2.
    assert account is not None
    assert account.tokens.input_tokens == 6 + 7
    assert account.tokens.output_tokens == 3 + 4
    assert account.tokens.reasoning_tokens == 2
    assert account.cached_tokens == 2
    assert account.reported_usd == pytest.approx(0.5)
    assert account.estimated_usd == pytest.approx(20.0)
