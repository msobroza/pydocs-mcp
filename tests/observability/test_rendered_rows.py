"""``rendered_rows``: how many of a response's rows its text put in front of
the model, from the renderer that counted them to the trace event line.

A row the text did not render never reached the model, so ``hit_count`` alone
overstates what was seen — these pin the one channel that carries the split.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.application.envelope import ResponseEnvelope
from pydocs_mcp.application.formatting import format_chunks_markdown_within_budget
from pydocs_mcp.application.freshness import EnvelopeInfo
from pydocs_mcp.application.truncation import ledger_scope
from pydocs_mcp.models import Chunk, ChunkFilterField
from pydocs_mcp.observability.rendered_rows import (
    begin_rendered_rows_capture,
    captured_rendered_rows,
    publish_rendered_rows,
)
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME
from pydocs_mcp.pointer_table import PointerTableConfig

_POINTERS = PointerTableConfig()


def _chunk(name: str, body: str) -> Chunk:
    return Chunk(
        text=body,
        metadata={
            "qualified_name": f"pkg.mod.{name}",
            ChunkFilterField.TITLE.value: name,
            ChunkFilterField.SOURCE_PATH.value: "pkg/mod.py",
            ChunkFilterField.START_LINE.value: 1,
            ChunkFilterField.END_LINE.value: 2,
        },
    )


# ── the renderer counts ────────────────────────────────────────────────────


def test_a_render_that_fits_reports_every_row() -> None:
    with ledger_scope() as ledger:
        format_chunks_markdown_within_budget(
            (_chunk("f", "a"), _chunk("g", "b")), 10_000, pointers=_POINTERS
        )
    assert ledger.rendered_rows == 2


def test_a_budgeted_render_reports_only_the_rows_it_emitted() -> None:
    rows = tuple(_chunk(f"f{i}", "x" * 300) for i in range(4))
    with ledger_scope() as ledger:
        format_chunks_markdown_within_budget(rows, 120, pointers=_POINTERS)
    assert ledger.rendered_rows == 1
    assert ledger.entries, "a cut that hid rows must still be marked"


def test_two_listings_in_one_response_add_up() -> None:
    """``kind="any"`` renders chunk hits and member hits into one body."""
    with ledger_scope() as ledger:
        format_chunks_markdown_within_budget((_chunk("f", "a"),), 10_000, pointers=_POINTERS)
        format_chunks_markdown_within_budget((_chunk("g", "b"),), 10_000, pointers=_POINTERS)
    assert ledger.rendered_rows == 2


def test_a_response_that_renders_no_rows_reports_undefined() -> None:
    """Most tools render no rows at all; zero would read as "rendered nothing"."""
    with ledger_scope() as ledger:
        pass
    assert ledger.rendered_rows is None


# ── the channel out to the recorder ────────────────────────────────────────


def test_publishing_outside_a_traced_call_is_a_no_op() -> None:
    """The CLI and an untraced server render through the same envelope."""
    publish_rendered_rows(7)  # no capture installed
    assert captured_rendered_rows() is None


def test_a_published_count_reaches_the_installed_capture() -> None:
    begin_rendered_rows_capture()
    publish_rendered_rows(3)
    assert captured_rendered_rows() == 3


def test_publishing_undefined_leaves_the_capture_undefined() -> None:
    begin_rendered_rows_capture()
    publish_rendered_rows(None)
    assert captured_rendered_rows() is None


class _StaticProbe:
    async def envelope_info(self) -> EnvelopeInfo | None:
        return None


@pytest.mark.asyncio
async def test_the_envelope_publishes_what_its_body_rendered() -> None:
    capture = begin_rendered_rows_capture()
    envelope = ResponseEnvelope(probe=_StaticProbe(), surface="mcp", pointers_enabled=True)

    async def produce() -> tuple[str, tuple[dict[str, Any], ...], dict[str, Any]]:
        rows = (_chunk("f", "a"), _chunk("g", "b"))
        return format_chunks_markdown_within_budget(rows, 10_000, pointers=_POINTERS), (), {}

    await envelope.wrap("search_codebase", "p", produce)
    assert capture.count == 2


# ── the event line ─────────────────────────────────────────────────────────


class _FakeCallToolResult:
    def __init__(self, structured: dict[str, Any]) -> None:
        self.structuredContent = structured
        self.content: list[Any] = []


def _tool_event(trace_dir: Path) -> dict[str, Any]:
    path = trace_dir / "traj-r" / SERVER_EVENTS_FILENAME
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return next(e for e in events if e["_event"] == "tool_call")


async def _record(trace_dir: Path, *, rendered: int | None) -> dict[str, Any]:
    recorder = TraceRecorder(trace_dir=trace_dir, trajectory_id="traj-r")
    recorder.open_trace()
    seq = recorder.begin_tool_call()
    publish_rendered_rows(rendered)
    result = _FakeCallToolResult(
        {"text": "t", "items": [{"path": "a.py"}, {"path": "b.py"}], "meta": {}}
    )
    await recorder.record_tool_success(
        seq=seq, tool="search_codebase", args={}, result=result, latency_ms=1.0
    )
    recorder.close()
    return _tool_event(trace_dir)


@pytest.mark.asyncio
async def test_the_event_line_carries_the_rendered_row_count(tmp_path: Path) -> None:
    event = await _record(tmp_path, rendered=1)
    assert event["hit_count"] == 2, "every returned row is still counted"
    assert event["rendered_rows"] == 1, "only one of them reached the model"


@pytest.mark.asyncio
async def test_a_tool_that_renders_no_rows_records_null(tmp_path: Path) -> None:
    event = await _record(tmp_path, rendered=None)
    assert event["rendered_rows"] is None
