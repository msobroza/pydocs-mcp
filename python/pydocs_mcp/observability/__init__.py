"""Server-side trace capture for Phase 2 rollout analysis (ADRs 0009/0010).

Default-off product surface consumed only by the eval harness: a
``FastMCP.call_tool`` subclass intercepts every tool call (zero per-tool
edits), a per-trajectory JSONL writer appends one event line per call, and a
``logging.Handler`` on ``pydocs_mcp.application.suggestions`` captures
fired-rule machinery records losslessly. Stdlib-only — no new dependencies.

Overhead budget (ADR 0009 action item 8): JSONL append with a held file
handle + flush measured at 25.3 µs/call (bench: N=10,000 appends of a
realistic 788-byte tool-event line on the development machine; per-call
open/close costs 255.1 µs and +fsync 140.5 µs — hence the held handle; see
``docs/superpowers/research/2026-07-18-phase2-evidence-mcp-middleware.md``
§5.2). Re-run that bench if the writer design changes.

Import discipline: ``serve_wiring`` (this module's re-export surface) keeps
the ``TracingToolServer`` import lazy so a trace-disabled serve path never
touches ``mcp.server.fastmcp`` through this package.
"""

from pydocs_mcp.observability.serve_wiring import TraceStartupError, build_traced_fastmcp
from pydocs_mcp.observability.trace_writer import TrajectoryIdReuseError

__all__ = [
    "TraceStartupError",
    "TrajectoryIdReuseError",
    "build_traced_fastmcp",
]
