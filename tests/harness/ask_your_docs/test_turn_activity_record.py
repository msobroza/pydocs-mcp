"""The one ``turn_activity`` log record per turn carries counts only (PROPOSAL §6, TDD 8)."""

from __future__ import annotations

import json

from pydocs_mcp.harness.ask_your_docs.activity_events import ProposedToolCall, RoundEnded
from pydocs_mcp.harness.ask_your_docs.activity_trace import TraceLimits, turn_activity_record
from pydocs_mcp.harness.ask_your_docs.activity_trace_builder import TraceBuilder
from pydocs_mcp.retrieval.config.ask_your_docs_ui_models import AskYourDocsUiConfig

_KEYS = {"event", "state", "steps", "tools", "failed", "files", "reasoning", "elapsed_s"}


def test_the_record_holds_counts_and_states_never_content() -> None:
    builder = TraceBuilder(
        limits=TraceLimits.from_ui_config(AskYourDocsUiConfig()), redact=str, scope={}
    )
    call = ProposedToolCall("c1", "search_codebase", {"query": "SECRET-QUERY"})
    builder.apply(RoundEnded("", "SECRET-THOUGHT", False, (call,), None, "m"))
    record = turn_activity_record(builder.finish(at=1.234))
    assert set(record) == _KEYS and record["event"] == "turn_activity"
    assert (record["steps"], record["tools"], record["failed"], record["files"]) == (2, 1, 1, 0)
    assert record["state"] == "complete" and record["reasoning"] == "shown"
    assert record["elapsed_s"] == 1.2
    assert "SECRET" not in json.dumps(record)
