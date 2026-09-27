"""A question the chat page kept (``ask_your_docs.trace``) opens as one ask trajectory.

End-to-end across the packaging boundary: a real recorder writes the traced child's capture —
two questions' worth, as a chat session does — and the PRODUCT writer keeps the second one
from its own messages; the eval readers then open ``questions/<n>/`` with no adapter, exactly
as they open a before/after arm's trajectory directory.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("langchain_core")

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from pydocs_eval.trajectory.ask_events import load_ask_trajectory_events
from pydocs_eval.trajectory.blob_store import BLOBS_DIRNAME
from pydocs_eval.trajectory.call_efficiency import ResponseTextFromBlobs
from pydocs_eval.trajectory.token_accounting import account_for_trace
from pydocs_mcp.harness.ask_your_docs.chat_trace import TraceLocation
from pydocs_mcp.observability.trace_recorder import TraceRecorder

_EARLIER_CALLS = [("grep", {"pattern": "Router"})]
_QUESTION_CALLS = [
    ("search_codebase", {"query": "router"}),
    ("get_overview", {"package": "demo"}),
    ("get_symbol", {"target": "demo.Router"}),
]


async def _record(recorder: TraceRecorder, calls: list[tuple[str, dict[str, Any]]]) -> None:
    for tool, args in calls:
        await recorder.record_tool_success(
            seq=recorder.begin_tool_call(),
            tool=tool,
            args=args,
            result={"text": f"{tool} found demo.Router", "items": [{"path": "a.py"}], "meta": {}},
            latency_ms=1.0,
        )


def _usage(input_tokens: int) -> dict[str, int]:
    return {"input_tokens": input_tokens, "output_tokens": 10, "total_tokens": input_tokens + 10}


def _question_messages() -> list[Any]:
    """The kept question's own messages: two parallel calls, then one, then the answer."""
    first, second = _QUESTION_CALLS[:2], _QUESTION_CALLS[2:]
    return [
        HumanMessage("where is the router?"),
        AIMessage("", tool_calls=_calls(first, "a"), usage_metadata=_usage(100)),
        *(ToolMessage("ok", tool_call_id=f"a{i}") for i in range(len(first))),
        AIMessage("", tool_calls=_calls(second, "b"), usage_metadata=_usage(200)),
        ToolMessage("ok", tool_call_id="b0"),
        AIMessage("demo.Router in a.py", usage_metadata=_usage(300)),
    ]


def _calls(calls: list[tuple[str, dict[str, Any]]], prefix: str) -> list[dict[str, Any]]:
    return [
        {"name": tool, "args": args, "id": f"{prefix}{i}", "type": "tool_call"}
        for i, (tool, args) in enumerate(calls)
    ]


@pytest.fixture
def question_dir(tmp_path: Path) -> Path:
    """The second question of a traced chat session, as the page keeps it."""
    trace = TraceLocation.minted_under(tmp_path / "chat-traces")
    recorder = TraceRecorder(trace_dir=trace.trace_root, trajectory_id=trace.trajectory_id)
    recorder.open_trace()
    asyncio.run(_record(recorder, _EARLIER_CALLS))  # an earlier question, never kept
    sink = trace.question_sink("where is the router?", "where is the router?")
    asyncio.run(_record(recorder, _QUESTION_CALLS))
    asyncio.run(sink.stamp_turn(_question_messages()))
    recorder.close()
    return trace.questions_dir / "1"


def test_its_calls_open_with_their_real_turns(question_dir: Path) -> None:
    trajectory = load_ask_trajectory_events(question_dir)
    assert trajectory.turns_recorded is True
    assert [(event.seq, event.tool, event.turn) for event in trajectory.events] == [
        (2, "search_codebase", 1),
        (3, "get_overview", 1),
        (4, "get_symbol", 2),
    ]


def test_its_spend_reads_through_the_usage_sidecar(question_dir: Path) -> None:
    account = account_for_trace(question_dir, usd_per_1m_input=1.0, usd_per_1m_output=0.0)
    assert account is not None
    assert (account.tokens.input_tokens, account.tokens.output_tokens) == (600, 30)


def test_its_response_text_resolves_from_the_blobs_beside_it(question_dir: Path) -> None:
    """The before/after measure reads ``<trace_dir>/../blobs`` — the page puts them there."""
    events = load_ask_trajectory_events(question_dir).events
    response_text = ResponseTextFromBlobs(question_dir.parent / BLOBS_DIRNAME)
    assert [response_text(event) for event in events] == [
        "search_codebase found demo.Router",
        "get_overview found demo.Router",
        "get_symbol found demo.Router",
    ]
