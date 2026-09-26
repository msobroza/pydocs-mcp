"""Named fakes shared by the binding's test files (clean-code rule: no ad-hoc mocks).

``record_server_calls`` writes the REAL ADR 0009 server trace a serve child launched
with the binding's trace environment would write — through the recorder itself, so a
test exercises the binding against the writer's actual bytes without spawning a server.
``binding_settings`` is the plain settings mapping every binding test builds a runner from.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydocs_mcp.observability.trace_env import (
    TRACE_DIR_ENV_VAR,
    TRACE_ENABLED_ENV_VAR,
    TRACE_ENABLED_VALUE,
    TRACE_TRAJECTORY_ID_ENV_VAR,
)
from pydocs_mcp.observability.trace_recorder import TraceRecorder


async def record_server_calls(
    trace_env: Mapping[str, str], calls: Sequence[Mapping[str, Any]]
) -> None:
    """Record ``calls`` (``{"name", "args"}`` each) as the serve child would; ``[]`` = header only."""
    assert trace_env[TRACE_ENABLED_ENV_VAR] == TRACE_ENABLED_VALUE
    recorder = TraceRecorder(
        trace_dir=Path(trace_env[TRACE_DIR_ENV_VAR]),
        trajectory_id=trace_env[TRACE_TRAJECTORY_ID_ENV_VAR],
    )
    recorder.open_trace()
    for call in calls:
        await recorder.record_tool_success(
            seq=recorder.begin_tool_call(),
            tool=call["name"],
            args=dict(call["args"]),
            result={"results": []},
            latency_ms=1.0,
        )
    recorder.close()


def binding_settings(tmp_path: Path, **extra: object) -> dict[str, object]:
    """A runner's settings mapping: a workspace, a model and a trace root under ``tmp_path``."""
    return {
        "workspace": str(tmp_path / "ws"),
        "model": "fake-model",
        "trace_root": str(tmp_path / "traces"),
        **extra,
    }
