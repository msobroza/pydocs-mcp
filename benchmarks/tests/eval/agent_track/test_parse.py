"""Claude CLI output parsers for agent-track metrics (spec §D15).

Pure fixture tests: ``parse_result_json`` reads the final ``--output-format
json`` payload (cost / turns / answer); ``parse_stream_events`` folds the
``--output-format stream-json`` lines into efficiency stats (tool calls,
distinct files read, MCP tool calls, cache tokens). Fixtures are hand-built
to the documented CLI shape; the Task-7 preflight re-validates the REAL CLI
still matches, so fixture drift is caught before any paid run.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.agent_track._parse import (
    parse_result_json,
    parse_stream_events,
)

_FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixture_result() -> str:
    return (_FIXTURES / "claude_result.json").read_text(encoding="utf-8")


@pytest.fixture
def fixture_stream() -> str:
    return (_FIXTURES / "claude_stream.jsonl").read_text(encoding="utf-8")


def test_parse_result_json_extracts_cost_and_answer(fixture_result: str) -> None:
    parsed = parse_result_json(fixture_result)
    assert parsed.cost_usd == pytest.approx(0.1834)
    assert parsed.turns == 12 and parsed.answer.startswith("The synchronization")


def test_parse_stream_counts_tool_calls_and_distinct_files(fixture_stream: str) -> None:
    stats = parse_stream_events(fixture_stream)
    assert stats.tool_calls == 5
    assert stats.distinct_files_read == 2  # Read a.py twice + b.py once → 2
    assert stats.cache_read_tokens == 84_000  # summed across usage events


def test_mcp_tool_calls_counted_separately(fixture_stream: str) -> None:
    stats = parse_stream_events(fixture_stream)
    assert stats.mcp_tool_calls == 2  # mcp__pydocs-mcp__* names


def test_malformed_lines_are_skipped_not_fatal() -> None:
    stats = parse_stream_events('{"type":"junk"\nnot json\n')
    assert stats.tool_calls == 0
