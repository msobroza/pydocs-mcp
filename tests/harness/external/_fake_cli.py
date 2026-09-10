"""A fake CLI process that behaves like a traced ``claude -p`` run.

The one seam the external harness spends through is ``_run_cli_process``. The
fake below replaces it and does what the real child would: it reads the
``.mcp.json`` the harness just wrote, honours the ADR 0009 env block by writing
a REAL trace through the recorder, and returns canned stream-json stdout. The
derived-view pin is therefore exercised against the writer's actual bytes,
without spawning anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_mcp.observability.trace_recorder import TraceRecorder

# One MCP call (the server will also see it) plus one bare file read (only the
# client ever sees it) — the two observation points the join must separate.
TRANSCRIPT = "\n".join(
    (
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","id":"tu_01","name":"mcp__pydocs-mcp__search_codebase",'
        '"input":{"query":"q"}}]}}',  # the CLIENT spelling of SERVER_TOOL_NAME
        '{"type":"assistant","message":{"role":"assistant","content":'
        '[{"type":"tool_use","id":"tu_02","name":"Read","input":{"file_path":"a.py"}}]}}',
        '{"type":"result","subtype":"success","num_turns":3,"total_cost_usd":0.25,'
        '"result":"the answer"}',
    )
)

TURN_CAPPED_TRANSCRIPT = (
    '{"type":"result","subtype":"error_max_turns","num_turns":40,"total_cost_usd":3.10,"result":""}'
)

# The name the SERVER records: FastMCP's bare registration name, which is what
# ``TracingToolServer.call_tool`` writes and ``read_tool_call_records`` reads.
# The transcript above reports the CLIENT's namespaced spelling of the SAME
# call — the vocabulary gap the join must bridge, and the reason this constant
# must never be written as ``mcp__pydocs-mcp__…``.
SERVER_TOOL_NAME = "search_codebase"
CLIENT_TOOL_NAME = "mcp__pydocs-mcp__search_codebase"


class FakeCliProcess:
    """Records every spawn; optionally writes the trace the run would leave."""

    def __init__(self, *, stdout: str = TRANSCRIPT, write_trace: bool = True) -> None:
        self.stdout = stdout
        self.write_trace = write_trace
        self.calls: list[dict[str, object]] = []

    async def __call__(self, cmd: list[str], *, cwd: Path, timeout_seconds: float) -> str:
        self.calls.append({"cmd": list(cmd), "cwd": cwd, "timeout_seconds": timeout_seconds})
        if self.write_trace:
            await self._record_server_trace(cmd)
        return self.stdout

    async def _record_server_trace(self, cmd: list[str]) -> None:
        """Write one server-observed tool call, correlated exactly as the
        served child would be — from the ``.mcp.json`` env block alone."""
        if "--mcp-config" not in cmd:
            return
        config = json.loads(Path(cmd[cmd.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
        env = config["mcpServers"]["pydocs-mcp"]["env"]
        assert env["PYDOCS_TRACE__ENABLED"] == "true"
        recorder = TraceRecorder(
            trace_dir=Path(env["PYDOCS_TRACE__DIR"]),
            trajectory_id=env["PYDOCS_TRACE__TRAJECTORY_ID"],
        )
        recorder.open_trace()
        await recorder.record_tool_success(
            seq=recorder.begin_tool_call(),
            tool=SERVER_TOOL_NAME,
            args={"query": "q"},
            result={"results": []},
            latency_ms=1.0,
        )
        recorder.close()

    def argv(self) -> list[str]:
        """The argv of the single recorded spawn."""
        return list(self.calls[-1]["cmd"])  # type: ignore[arg-type]
