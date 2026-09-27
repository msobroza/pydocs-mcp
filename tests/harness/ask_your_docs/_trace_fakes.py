"""A traced serve child, in-process — the named fake every chat-trace test records through.

``FakeTracedChild`` opens a REAL ``TraceRecorder`` at one ``TraceLocation`` — the header a
traced ``serve`` writes at start, and the id-reuse guard it would trip — and records calls
the way the tracing server does, so what a test reads back is the recorder's own bytes.
Core deps only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydocs_mcp.harness.ask_your_docs.chat_trace import TraceLocation
from pydocs_mcp.observability.trace_env import TRACE_DIR_ENV_VAR, TRACE_TRAJECTORY_ID_ENV_VAR
from pydocs_mcp.observability.trace_recorder import TraceRecorder


@dataclass
class FakeTracedChild:
    """One traced child's recorder, at ``location``."""

    location: TraceLocation
    recorder: TraceRecorder

    @classmethod
    def started_at(cls, location: TraceLocation) -> FakeTracedChild:
        """What a traced ``serve`` does at start: its header, under the location's id."""
        recorder = TraceRecorder(
            trace_dir=location.trace_root, trajectory_id=location.trajectory_id
        )
        recorder.open_trace()
        return cls(location, recorder)

    @classmethod
    def launched_with(cls, env: Mapping[str, str]) -> FakeTracedChild | None:
        """The child a launch env describes (the ADR 0009 names), or None when untraced."""
        if TRACE_TRAJECTORY_ID_ENV_VAR not in env:
            return None
        location = TraceLocation(Path(env[TRACE_DIR_ENV_VAR]), env[TRACE_TRAJECTORY_ID_ENV_VAR])
        return cls.started_at(location)

    async def record(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        text: str = "",
        failure: BaseException | None = None,
    ) -> None:
        """One call, recorded as the tracing server records it; a failure stores no blob."""
        seq = self.recorder.begin_tool_call()
        if failure is not None:
            await self.recorder.record_tool_failure(
                seq=seq, tool=tool, args=args, error=failure, latency_ms=1.0
            )
            return
        result = {"text": text or f"{tool} says hi", "items": [{"path": "a.py"}], "meta": {}}
        await self.recorder.record_tool_success(
            seq=seq, tool=tool, args=args, result=result, latency_ms=1.0
        )

    def stop(self) -> None:
        self.recorder.close()
