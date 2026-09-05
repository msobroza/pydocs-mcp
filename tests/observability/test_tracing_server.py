"""Phase 2 Task 1 — the FastMCP subclass seam + composition wiring.

The seam pin test dispatches through the LOWLEVEL registered handler (not by
calling ``call_tool`` directly): ``FastMCP._setup_handlers`` registers the
bound method ``self.call_tool``, so a subclass override IS the registered
handler — an SDK release that registers handlers differently must fail here
(ADR 0009 action item 3).
"""

from __future__ import annotations

import json
from pathlib import Path

import mcp.types as types
import pytest
from mcp.server.fastmcp import FastMCP

from pydocs_mcp.observability import TraceStartupError, build_traced_fastmcp
from pydocs_mcp.observability.trace_recorder import TraceRecorder
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME
from pydocs_mcp.observability.tracing_server import TracingToolServer
from pydocs_mcp.retrieval.config import TraceConfig


def _register_echo(server: FastMCP) -> None:
    @server.tool(name="echo")
    def echo(value: str) -> str:
        return f"echo:{value}"


def _register_failing(server: FastMCP) -> None:
    @server.tool(name="boom")
    def boom() -> str:
        raise ValueError("boom payload")


async def _dispatch(server: FastMCP, tool: str, arguments: dict[str, object]) -> types.ServerResult:
    """Drive the real registered dispatch path, as a client request would."""
    handler = server._mcp_server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=tool, arguments=arguments),
    )
    return await handler(request)


def _events(trace_dir: Path, trajectory_id: str) -> list[dict[str, object]]:
    path = trace_dir / trajectory_id / SERVER_EVENTS_FILENAME
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _open_tracing_server(trace_dir: Path, trajectory_id: str = "traj-s") -> TracingToolServer:
    recorder = TraceRecorder(trace_dir=trace_dir, trajectory_id=trajectory_id)
    recorder.open_trace()
    return TracingToolServer("test-traced", recorder=recorder)


class TestSeamPin:
    async def test_dispatch_through_registered_handler_produces_event(self, tmp_path: Path) -> None:
        server = _open_tracing_server(tmp_path)
        _register_echo(server)
        try:
            result = await _dispatch(server, "echo", {"value": "hi"})
        finally:
            server.trace_recorder.close()
        assert isinstance(result.root, types.CallToolResult)
        assert not result.root.isError
        events = _events(tmp_path, "traj-s")
        tool_events = [e for e in events if e["_event"] == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0]["tool"] == "echo"
        assert tool_events[0]["args"] == {"value": "hi"}
        assert tool_events[0]["seq"] == 1
        assert tool_events[0]["latency_ms"] >= 0

    async def test_tool_exception_recorded_as_typed_error(self, tmp_path: Path) -> None:
        server = _open_tracing_server(tmp_path)
        _register_failing(server)
        try:
            result = await _dispatch(server, "boom", {})
        finally:
            server.trace_recorder.close()
        # The lowlevel handler flattens the raise into isError=True — but the
        # subclass saw the typed exception before the flattening.
        assert isinstance(result.root, types.CallToolResult)
        assert result.root.isError
        event = next(e for e in _events(tmp_path, "traj-s") if e["_event"] == "tool_call")
        error = event["error"]
        assert isinstance(error, dict)
        assert "boom payload" in error["message"]
        assert event["result_blob"] is None

    async def test_all_tools_covered_with_zero_per_tool_edits(self, tmp_path: Path) -> None:
        """Any registered tool is traced — the seam is the dispatch path,
        not a per-tool wrapper."""
        server = _open_tracing_server(tmp_path)
        _register_echo(server)

        @server.tool(name="second")
        def second() -> str:
            return "two"

        try:
            await _dispatch(server, "echo", {"value": "a"})
            await _dispatch(server, "second", {})
        finally:
            server.trace_recorder.close()
        tool_events = [e for e in _events(tmp_path, "traj-s") if e["_event"] == "tool_call"]
        assert [(e["tool"], e["seq"]) for e in tool_events] == [("echo", 1), ("second", 2)]


class TestDisabledNeutrality:
    async def test_disabled_returns_plain_fastmcp_and_writes_nothing(self, tmp_path: Path) -> None:
        """The byte-neutrality golden: trace.enabled=False must be exactly
        today's server — plain FastMCP, identical wire result, zero files."""
        trace_dir = tmp_path / "never-created"
        cfg = TraceConfig(enabled=False, dir=str(trace_dir), trajectory_id="ignored")
        built = build_traced_fastmcp(cfg, name="pydocs-mcp", instructions="instr")
        assert type(built) is FastMCP
        assert not isinstance(built, TracingToolServer)

        baseline = FastMCP("pydocs-mcp", instructions="instr")
        _register_echo(built)
        _register_echo(baseline)
        built_result = await _dispatch(built, "echo", {"value": "same"})
        baseline_result = await _dispatch(baseline, "echo", {"value": "same"})
        assert built_result.root.model_dump_json() == baseline_result.root.model_dump_json()
        assert not trace_dir.exists()

    def test_default_config_is_disabled(self) -> None:
        assert TraceConfig().enabled is False


class TestStartupGuard:
    def test_enabled_without_trajectory_id_is_hard_error(self, tmp_path: Path) -> None:
        cfg = TraceConfig(enabled=True, dir=str(tmp_path), trajectory_id=None)
        with pytest.raises(TraceStartupError, match="PYDOCS_TRACE__TRAJECTORY_ID"):
            build_traced_fastmcp(cfg, name="pydocs-mcp", instructions="i")

    def test_enabled_without_dir_is_hard_error(self, tmp_path: Path) -> None:
        cfg = TraceConfig(enabled=True, dir=None, trajectory_id="traj-x")
        with pytest.raises(TraceStartupError, match="trace.dir"):
            build_traced_fastmcp(cfg, name="pydocs-mcp", instructions="i")

    async def test_enabled_builds_tracing_server_with_header(self, tmp_path: Path) -> None:
        cfg = TraceConfig(enabled=True, dir=str(tmp_path), trajectory_id="traj-e")
        server = build_traced_fastmcp(cfg, name="pydocs-mcp", instructions="i")
        assert isinstance(server, TracingToolServer)
        server.trace_recorder.close()
        header = _events(tmp_path, "traj-e")[0]
        assert header["_event"] == "trace_header"
        assert header["trajectory_id"] == "traj-e"

    def test_trajectory_id_reuse_across_processes_is_hard_error(self, tmp_path: Path) -> None:
        cfg = TraceConfig(enabled=True, dir=str(tmp_path), trajectory_id="traj-dup")
        server = build_traced_fastmcp(cfg, name="pydocs-mcp", instructions="i")
        assert isinstance(server, TracingToolServer)
        server.trace_recorder.close()
        with pytest.raises(Exception, match="traj-dup"):
            build_traced_fastmcp(cfg, name="pydocs-mcp", instructions="i")
