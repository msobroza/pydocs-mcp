"""observability/trace_reader — the product reading its own trace schema.

ADR 0010 §Amendment 2026-07-27: read-only, product-side raw capture only,
``args_digest`` derived (never stored — ``TRACE_SCHEMA_VERSION`` stays 1)
and pinned by a golden. Fixtures are written through the REAL recorder so
the reader is tested against the writer's actual bytes, never a hand-rolled
imitation of the schema.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from pydocs_mcp.harness.platform.contract import ToolCallObservation
from pydocs_mcp.observability.trace_reader import (
    TraceReadError,
    read_tool_call_records,
    tool_args_digest,
)
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME, canonical_trace_json

_TRAJECTORY_ID = "trajectory-under-test"


async def _record_fixture_trace(trace_root: Path) -> Path:
    """One header + two tool calls + one failure, via the real recorder."""
    recorder = TraceRecorder(trace_dir=trace_root, trajectory_id=_TRAJECTORY_ID)
    recorder.open_trace()
    await recorder.record_tool_success(
        seq=recorder.begin_tool_call(),
        tool="search_codebase",
        args={"query": "auth flow"},
        result={"results": []},
        latency_ms=12.0,
    )
    await recorder.record_tool_success(
        seq=recorder.begin_tool_call(),
        tool="read_file",
        args={"file_path": "a.py"},
        result="content",
        latency_ms=3.0,
    )
    await recorder.record_tool_failure(
        seq=recorder.begin_tool_call(),
        tool="grep",
        args={"pattern": "("},
        error=ValueError("bad pattern"),
        latency_ms=1.0,
    )
    recorder.close()
    return trace_root / _TRAJECTORY_ID


async def test_reader_projects_tool_events_in_seq_order(tmp_path: Path) -> None:
    trace_dir = await _record_fixture_trace(tmp_path)
    records = read_tool_call_records(trace_dir)
    assert [r.tool_name for r in records] == ["search_codebase", "read_file", "grep"]
    assert all(r.observed_by is ToolCallObservation.SERVER for r in records)
    # Failures are still calls the model made: the trace records them, so
    # the derived view keeps them (a gate counting attempts must see them).
    assert records[0].args_digest == tool_args_digest({"query": "auth flow"})


def test_args_digest_golden_is_canonical_json_sha256() -> None:
    # The documented derivation, pinned (ADR 0010 amendment): sha256 over
    # the recorder's own canonical JSON of the args mapping.
    expected = hashlib.sha256(canonical_trace_json({"query": "x"}).encode("utf-8")).hexdigest()
    assert tool_args_digest({"query": "x"}) == expected
    assert tool_args_digest({"query": "x"}) == tool_args_digest({"query": "x"})


def test_missing_events_file_is_a_legitimate_empty_slice(tmp_path: Path) -> None:
    # An arm with no MCP server attached carries no server events — the ADR
    # 0009 correlation carve-out; the CALLER hard-errors on trace-enabled
    # runs, never the reader.
    assert read_tool_call_records(tmp_path / "no-such-trajectory") == ()


def test_corrupt_line_fails_loud(tmp_path: Path) -> None:
    trace_dir = tmp_path / "t"
    trace_dir.mkdir()
    (trace_dir / SERVER_EVENTS_FILENAME).write_text(
        '{"_event":"trace_header","schema_version":1}\nnot json at all\n', encoding="utf-8"
    )
    with pytest.raises(TraceReadError) as excinfo:
        read_tool_call_records(trace_dir)
    assert "not json at all" in str(excinfo.value)


async def test_header_and_fired_rule_lines_never_project(tmp_path: Path) -> None:
    trace_dir = await _record_fixture_trace(tmp_path)
    recorder_lines = (trace_dir / SERVER_EVENTS_FILENAME).read_text().splitlines()
    assert len(recorder_lines) == 4  # header + three tool events
    assert len(read_tool_call_records(trace_dir)) == 3
