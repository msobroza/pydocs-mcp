"""Composition-root factory: plain FastMCP or the tracing subclass.

``server.run`` calls :func:`build_traced_fastmcp` at its one FastMCP
construction site. Disabled (the default) returns a plain ``FastMCP`` —
byte-neutral with an untraced server. Enabled validates the ADR 0009
correlation identity up front: a trace-enabled server without a
``trajectory_id`` (or without a writable ``dir``) cannot produce a
correlatable trace, and correlation failures are hard errors, never
warnings.

Import discipline: ``TracingToolServer`` (whose module subclasses FastMCP at
import time) is imported lazily so the disabled path never loads it — test
doubles that stub out ``mcp.server.fastmcp`` keep working.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.exceptions import PydocsMCPError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

    from pydocs_mcp.retrieval.config import TraceConfig


class TraceStartupError(PydocsMCPError, RuntimeError):
    """trace.enabled with an incomplete correlation identity (ADR 0009)."""


def build_traced_fastmcp(trace_config: TraceConfig, *, name: str, instructions: str) -> FastMCP:
    """The serve-time FastMCP construction site, conditional on tracing.

    Example:
        >>> from pydocs_mcp.retrieval.config import TraceConfig
        >>> server = build_traced_fastmcp(
        ...     TraceConfig(), name="pydocs-mcp", instructions="…"
        ... )  # disabled default → plain FastMCP  # doctest: +SKIP
    """
    from mcp.server.fastmcp import FastMCP

    if not trace_config.enabled:
        return FastMCP(name, instructions=instructions)

    from pydocs_mcp.observability.trace_recorder import TraceRecorder
    from pydocs_mcp.observability.tracing_server import TracingToolServer

    trajectory_id, trace_dir = _validated_trace_identity(trace_config)
    recorder = TraceRecorder(trace_dir=trace_dir, trajectory_id=trajectory_id)
    recorder.open_trace()
    return TracingToolServer(name, instructions=instructions, recorder=recorder)


def _validated_trace_identity(trace_config: TraceConfig) -> tuple[str, Path]:
    """Hard-error on an incomplete correlation identity at serve startup."""
    if not trace_config.trajectory_id:
        raise TraceStartupError(
            f"trace.enabled=True but trajectory_id={trace_config.trajectory_id!r};"
            " the runner must inject a per-rollout UUID via the env-only channel"
            " PYDOCS_TRACE__TRAJECTORY_ID (ADR 0009 — correlation failures are"
            " hard errors)"
        )
    if not trace_config.dir:
        raise TraceStartupError(
            f"trace.enabled=True but trace.dir={trace_config.dir!r};"
            " set trace.dir in YAML or PYDOCS_TRACE__DIR to the run's trace"
            " directory (expected a writable path)"
        )
    return trace_config.trajectory_id, Path(trace_config.dir).expanduser()
