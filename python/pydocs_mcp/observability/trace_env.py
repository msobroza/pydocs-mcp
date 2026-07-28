"""The ADR 0009 correlation identity as a subprocess environment map.

One spelling of the three ``PYDOCS_TRACE__*`` variables for every product-side
caller that launches a traced ``pydocs_mcp serve`` child — the in-process
harness (through its MCP stdio connection's env map) and the composed CLI
harness (through the ``.mcp.json`` server ``env`` block). A second spelling is
how a rename silently disables capture on one path only; Phase 2's
silently-disabled-capture incident is the motivating scar.

WHY an explicit map and never ``os.environ`` mutation: MCP stdio children start
from a MINIMAL default environment, so a parent mutation would not reach them —
and would race concurrent runs in the same process.

``PYDOCS_TRACE__DIR`` is the trace ROOT, not the per-trajectory directory: the
recorder writes ``<root>/<trajectory_id>/server_events.jsonl``, so a caller's
``Trajectory.trace_dir`` is ``root / trajectory_id`` while the env var carries
``root``.
"""

from __future__ import annotations

from pathlib import Path

# The recorder's master switch. ``TraceConfig.enabled`` defaults False so a
# non-eval deployment is byte-identical to an untraced server; a traced child
# must therefore be told. The value is the STRING "true" because it crosses the
# subprocess / .mcp.json boundary as text before pydantic-settings coerces it.
TRACE_ENABLED_ENV_VAR = "PYDOCS_TRACE__ENABLED"
TRACE_ENABLED_VALUE = "true"
TRACE_DIR_ENV_VAR = "PYDOCS_TRACE__DIR"
TRACE_TRAJECTORY_ID_ENV_VAR = "PYDOCS_TRACE__TRAJECTORY_ID"


def trace_subprocess_env(trace_root: Path, trajectory_id: str) -> dict[str, str]:
    """The correlation identity a traced serve child must be launched with.

    Example:
        >>> trace_subprocess_env(Path("/traces"), "3c63ee67")
        {'PYDOCS_TRACE__ENABLED': 'true', 'PYDOCS_TRACE__DIR': '/traces', 'PYDOCS_TRACE__TRAJECTORY_ID': '3c63ee67'}
    """
    return {
        TRACE_ENABLED_ENV_VAR: TRACE_ENABLED_VALUE,
        TRACE_DIR_ENV_VAR: str(trace_root),
        TRACE_TRAJECTORY_ID_ENV_VAR: trajectory_id,
    }
