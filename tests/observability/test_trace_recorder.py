"""Phase 2 Task 1 — TraceRecorder: events, distillation, header, determinism.

The recorder is driven directly with fake ``CallToolResult``-shaped objects
(no real MCP dispatch — that seam is pinned in ``test_tracing_server.py``).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from pydocs_mcp.application.suggestions import log_suggestion_fired
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME, TRACE_SCHEMA_VERSION


class FakeCallToolResult:
    """Duck-typed stand-in for ``mcp.types.CallToolResult`` — the distiller
    reads ``structuredContent`` via ``getattr``, never isinstance."""

    def __init__(self, structured: dict[str, Any] | None) -> None:
        self.structuredContent = structured
        self.content: list[Any] = []


def _envelope(
    *,
    text: str = "## hit\n",
    items: tuple[dict[str, Any], ...] = (),
    truncated: bool = False,
    suggestion: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {"tool": "search_codebase", "project": "p", "truncated": truncated}
    if suggestion is not None:
        meta["suggestion"] = suggestion
    return {"text": text, "items": list(items), "meta": meta}


_ITEM = {
    "kind": "api",
    "id": "chunk-7",
    "qualified_name": "pkg.mod.fn",
    "package": "pkg",
    "path": "pkg/mod.py",
    "start_line": 3,
    "end_line": 19,
    "score": 0.42,
}


def _read_events(trace_dir: Path, trajectory_id: str) -> list[dict[str, Any]]:
    path = trace_dir / trajectory_id / SERVER_EVENTS_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _open_recorder(trace_dir: Path, trajectory_id: str = "traj-a") -> TraceRecorder:
    recorder = TraceRecorder(trace_dir=trace_dir, trajectory_id=trajectory_id)
    recorder.open_trace()
    return recorder


class TestHeader:
    def test_header_stamps_identity_block(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        recorder.close()
        header = _read_events(tmp_path, "traj-a")[0]
        assert header["_event"] == "trace_header"
        assert header["trajectory_id"] == "traj-a"
        assert header["schema_version"] == TRACE_SCHEMA_VERSION
        assert header["ts"] > 0

    def test_header_artifact_hash_matches_live_surface(self, tmp_path: Path) -> None:
        from pydocs_mcp.application.description_source import current_artifact_hash

        recorder = _open_recorder(tmp_path)
        recorder.close()
        header = _read_events(tmp_path, "traj-a")[0]
        assert header["artifact_hash"] == current_artifact_hash()

    def test_header_stamps_versions(self, tmp_path: Path) -> None:
        import importlib.metadata

        recorder = _open_recorder(tmp_path)
        recorder.close()
        header = _read_events(tmp_path, "traj-a")[0]
        assert header["mcp_version"] == importlib.metadata.version("mcp")
        assert header["pydocs_mcp_version"]


class TestToolEvents:
    async def test_success_event_distills_envelope(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        try:
            seq = recorder.begin_tool_call()
            result = FakeCallToolResult(
                _envelope(items=(_ITEM,), truncated=True, suggestion="[suggestion: x]")
            )
            await recorder.record_tool_success(
                seq=seq, tool="search_codebase", args={"query": "q"}, result=result, latency_ms=1.5
            )
        finally:
            recorder.close()
        event = _read_events(tmp_path, "traj-a")[1]
        assert event["_event"] == "tool_call"
        assert event["seq"] == 1
        assert event["initiator"] == "model"
        assert event["tool"] == "search_codebase"
        assert event["args"] == {"query": "q"}
        assert event["hit_count"] == 1
        assert event["truncated"] is True
        assert event["suggestion"] == "[suggestion: x]"
        assert event["error"] is None
        assert event["latency_ms"] == 1.5
        assert event["result_ids"] == [
            {
                "id": "chunk-7",
                "qualified_name": "pkg.mod.fn",
                "path": "pkg/mod.py",
                "start_line": 3,
                "end_line": 19,
            }
        ]

    async def test_seq_is_monotonic_per_process(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        try:
            assert [recorder.begin_tool_call() for _ in range(3)] == [1, 2, 3]
        finally:
            recorder.close()

    async def test_result_blob_holds_full_serialization(self, tmp_path: Path) -> None:
        """Preview covers 2048 bytes; the blob preserves the whole envelope."""
        recorder = _open_recorder(tmp_path)
        big_text = "x" * 5000
        try:
            seq = recorder.begin_tool_call()
            await recorder.record_tool_success(
                seq=seq,
                tool="read_file",
                args={"path": "a.py"},
                result=FakeCallToolResult(_envelope(text=big_text)),
                latency_ms=0.1,
            )
        finally:
            recorder.close()
        event = _read_events(tmp_path, "traj-a")[1]
        assert event["result_bytes"] > 2048
        assert len(event["result_preview"].encode("utf-8")) <= 2048
        blob = (tmp_path / "blobs" / event["result_blob"]).read_bytes()
        assert len(blob) == event["result_bytes"]
        assert big_text in blob.decode("utf-8")

    async def test_repeated_identical_results_share_one_blob(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        result = FakeCallToolResult(_envelope(items=(_ITEM,)))
        try:
            for _ in range(2):
                seq = recorder.begin_tool_call()
                await recorder.record_tool_success(
                    seq=seq, tool="grep", args={"pattern": "x"}, result=result, latency_ms=0.2
                )
        finally:
            recorder.close()
        events = _read_events(tmp_path, "traj-a")[1:]
        assert events[0]["result_blob"] == events[1]["result_blob"]
        assert len(list((tmp_path / "blobs").iterdir())) == 1

    async def test_failure_event_captures_typed_error(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        try:
            seq = recorder.begin_tool_call()
            error = ValueError("invalid target: got '', expected dotted identifier")
            await recorder.record_tool_failure(
                seq=seq, tool="get_symbol", args={"target": ""}, error=error, latency_ms=0.3
            )
        finally:
            recorder.close()
        event = _read_events(tmp_path, "traj-a")[1]
        assert event["error"] == {
            "type": "ValueError",
            "message": "invalid target: got '', expected dotted identifier",
        }
        assert event["result_blob"] is None
        assert event["hit_count"] is None

    async def test_failure_event_records_cause_chain(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        try:
            seq = recorder.begin_tool_call()
            try:
                try:
                    raise KeyError("missing")
                except KeyError as inner:
                    raise RuntimeError("wrapped") from inner
            except RuntimeError as wrapped:
                await recorder.record_tool_failure(
                    seq=seq, tool="grep", args={}, error=wrapped, latency_ms=0.1
                )
        finally:
            recorder.close()
        event = _read_events(tmp_path, "traj-a")[1]
        assert event["error"]["cause"] == "KeyError"

    async def test_non_envelope_result_is_opaque(self, tmp_path: Path) -> None:
        """A result without the {text, items, meta} envelope keys gets no
        derived fields — hit_count must not be fabricated (ADR 0010)."""
        recorder = _open_recorder(tmp_path)
        try:
            seq = recorder.begin_tool_call()
            await recorder.record_tool_success(
                seq=seq, tool="echo", args={}, result=({"result": "hi"}), latency_ms=0.1
            )
        finally:
            recorder.close()
        event = _read_events(tmp_path, "traj-a")[1]
        assert event["hit_count"] is None
        assert event["result_ids"] is None
        assert event["result_blob"] is not None


class TestSuggestionCapture:
    async def test_fired_rule_lands_keyed_to_in_flight_seq(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        try:
            seq = recorder.begin_tool_call()
            log_suggestion_fired("grep", "grep_zero_hit")
            await recorder.record_tool_success(
                seq=seq,
                tool="grep",
                args={"pattern": "zzz"},
                result=FakeCallToolResult(_envelope()),
                latency_ms=0.1,
            )
        finally:
            recorder.close()
        events = _read_events(tmp_path, "traj-a")
        fired = [e for e in events if e["_event"] == "suggestion_fired"]
        assert fired == [
            {
                "_event": "suggestion_fired",
                "trajectory_id": "traj-a",
                "seq": 1,
                "ts": fired[0]["ts"],
                "tool": "grep",
                "rule": "grep_zero_hit",
            }
        ]

    async def test_capture_survives_quiet_logging_config(self, tmp_path: Path) -> None:
        """Without -v the suggestions logger sits above INFO — the recorder
        must still capture losslessly (ADR 0009: handler, not stderr scrape)."""
        logger = logging.getLogger("pydocs_mcp.application.suggestions")
        previous = logger.level
        logger.setLevel(logging.WARNING)
        try:
            recorder = _open_recorder(tmp_path)
            try:
                recorder.begin_tool_call()
                log_suggestion_fired("search_codebase", "search_zero_hit")
            finally:
                recorder.close()
        finally:
            logger.setLevel(previous)
        events = _read_events(tmp_path, "traj-a")
        assert any(e["_event"] == "suggestion_fired" for e in events)

    async def test_non_machinery_log_lines_are_ignored(self, tmp_path: Path) -> None:
        """Only ``suggestion_fired`` JSON records are machinery events —
        anything else on the logger must neither crash capture nor pollute
        the trace."""
        recorder = _open_recorder(tmp_path)
        try:
            logger = logging.getLogger("pydocs_mcp.application.suggestions")
            logger.info("plain text, not JSON")
            logger.info(json.dumps({"event": "something_else"}))
            logger.info(json.dumps(["not", "a", "dict"]))
        finally:
            recorder.close()
        assert [e["_event"] for e in _read_events(tmp_path, "traj-a")] == ["trace_header"]

    async def test_close_detaches_handler(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path)
        recorder.close()
        log_suggestion_fired("grep", "grep_zero_hit")
        events = _read_events(tmp_path, "traj-a")
        assert all(e["_event"] != "suggestion_fired" for e in events)


class TestDeterminism:
    async def _drive_fake_sequence(self, trace_dir: Path) -> None:
        recorder = TraceRecorder(trace_dir=trace_dir, trajectory_id="traj-d")
        recorder.open_trace()
        try:
            seq = recorder.begin_tool_call()
            await recorder.record_tool_success(
                seq=seq,
                tool="search_codebase",
                args={"query": "q"},
                result=FakeCallToolResult(_envelope(items=(_ITEM,))),
                latency_ms=1.0,
            )
            seq = recorder.begin_tool_call()
            await recorder.record_tool_failure(
                seq=seq,
                tool="get_symbol",
                args={"target": "x"},
                error=KeyError("x"),
                latency_ms=2.0,
            )
        finally:
            recorder.close()

    async def test_identical_fake_sequences_identical_modulo_timestamps(
        self, tmp_path: Path
    ) -> None:
        """R6: two identical fake call sequences produce identical files once
        the recorded-but-excluded wall-clock fields (`ts`) are stripped."""
        await self._drive_fake_sequence(tmp_path / "run1")
        await self._drive_fake_sequence(tmp_path / "run2")

        def _stable(trace_dir: Path) -> list[dict[str, Any]]:
            events = _read_events(trace_dir, "traj-d")
            for event in events:
                assert "ts" in event  # timestamps recorded ...
                event.pop("ts")  # ... but excluded from the comparison key
            return events

        assert _stable(tmp_path / "run1") == _stable(tmp_path / "run2")
        blobs = sorted(p.name for p in (tmp_path / "run1" / "blobs").iterdir())
        assert blobs == sorted(p.name for p in (tmp_path / "run2" / "blobs").iterdir())


class TestIdReuseGuard:
    def test_second_open_same_trajectory_hard_errors(self, tmp_path: Path) -> None:
        recorder = _open_recorder(tmp_path, "traj-r")
        recorder.close()
        with pytest.raises(Exception, match="traj-r"):
            _open_recorder(tmp_path, "traj-r")
