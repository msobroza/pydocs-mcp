"""Writes a realistic ask-your-docs trajectory on disk, for tests that read one.

Real bytes, not hand-written JSON: the trace comes from the product recorder and
the turn sidecar from the product writer, so a test that reads a trajectory back
is reading the same file shapes a paid run produces. Callers that want the whole
binding path (fake model included) use ``test_ask_events.FakeTurnScript``; this
helper is for tests whose subject is downstream of the trace.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydocs_mcp.harness.ask_your_docs.model_turns import write_model_turns
from pydocs_mcp.observability.trace_recorder import TraceRecorder

# ``(tool, args, model turn)`` — one recorded call.
ScriptedCall = tuple[str, dict[str, Any], int]


def write_ask_trajectory(
    trace_root: Path,
    *,
    calls: Sequence[ScriptedCall],
    response_text: str = "",
    items: Sequence[dict[str, Any]] | None = None,
) -> Path:
    """Record ``calls`` as one trajectory under ``trace_root``; return its directory.

    ``items`` is the result rows every call returns (one row naming ``a.py`` by
    default), so a caller can make calls resurface each other or surface gold.
    """
    trajectory_id = uuid.uuid4().hex
    rows = list(items if items is not None else [{"path": "a.py"}])
    asyncio.run(_record(trace_root, trajectory_id, calls, response_text, rows))
    trace_dir = trace_root / trajectory_id
    write_model_turns(
        trace_dir,
        seqs=range(1, len(calls) + 1),
        turns=[turn for _, _, turn in calls],
    )
    return trace_dir


async def _record(
    trace_root: Path,
    trajectory_id: str,
    calls: Sequence[ScriptedCall],
    response_text: str,
    items: list[dict[str, Any]],
) -> None:
    recorder = TraceRecorder(trace_dir=trace_root, trajectory_id=trajectory_id)
    recorder.open_trace()
    for tool, args, _turn in calls:
        await recorder.record_tool_success(
            seq=recorder.begin_tool_call(),
            tool=tool,
            args=args,
            result={"text": response_text, "items": items, "meta": {}},
            latency_ms=1.0,
        )
    recorder.close()
