"""``TracingToolServer`` — the FastMCP ``call_tool`` capture seam (ADR 0009).

``FastMCP.__init__`` calls ``_setup_handlers()``, which registers the BOUND
method ``self.call_tool`` as the lowlevel tools/call handler (verified
against mcp 1.28.1 ``server/fastmcp/server.py``) — so this subclass override
IS the registered handler: every tool call crosses it with the raw client
``arguments`` dict, timing, the result, and typed exceptions BEFORE the
lowlevel handler flattens them to ``isError=True``. Zero per-tool edits, and
none of the ``inspect.signature`` hazard of wrapping the handler closures
(FastMCP builds each tool's advertised ``inputSchema`` from the function
signature). The seam pin test in ``tests/observability/`` dispatches through
the registered lowlevel handler to guard this SDK internal across upgrades.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ContentBlock

from pydocs_mcp.observability.trace_recorder import TraceRecorder


class TracingToolServer(FastMCP):
    """FastMCP that records every tool call through a :class:`TraceRecorder`."""

    def __init__(self, *args: Any, recorder: TraceRecorder, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.trace_recorder = recorder

    async def call_tool(
        self, name: str, arguments: dict[str, Any]
    ) -> Sequence[ContentBlock] | dict[str, Any]:
        """Dispatch via FastMCP, recording one trace event per call."""
        seq = self.trace_recorder.begin_tool_call()
        started = time.perf_counter()
        try:
            result = await super().call_tool(name, arguments)
        except Exception as error:
            await self.trace_recorder.record_tool_failure(
                seq=seq, tool=name, args=arguments, error=error, latency_ms=_elapsed_ms(started)
            )
            raise
        await self.trace_recorder.record_tool_success(
            seq=seq, tool=name, args=arguments, result=result, latency_ms=_elapsed_ms(started)
        )
        return result


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 3)
