"""Real-rollout trace validation for the held-session binding (stage 3).

Run-contract design §9 stage 3, first integration step: prove that ONE
`_serve_session_tools` context against a REAL indexed workspace yields one
serve subprocess, one trace header, and seq-ordered tool-call events that
`read_tool_call_records` projects back — i.e. the session-per-tool-call
re-spawn (and its `TrajectoryIdReuseError`) is actually gone, not just
designed away. No LLM is involved: the session + tools + trace layers are
what stage 3's fix touched, so the tools are invoked directly.

Usage (manual validation, Phase-2 fixture-gate culture — not a CI test,
because it needs a locally indexed workspace):

    PYTHONPATH=python .venv/bin/python scripts/validate_traced_run.py \
        --workspace ~/pydocs-index
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

from pydocs_mcp.harness.ask_your_docs.binding import (
    AskYourDocsRunnerSettings,
    _serve_session_tools,
    _trace_subprocess_env,
)
from pydocs_mcp.observability.trace_reader import read_tool_call_records
from pydocs_mcp.observability.trace_writer import SERVER_EVENTS_FILENAME


async def _validate(workspace: str, trace_root: Path) -> int:
    settings = AskYourDocsRunnerSettings(
        workspace=workspace, model="none-needed", trace_root=str(trace_root)
    )
    trajectory_id = uuid.uuid4().hex
    trace_env = _trace_subprocess_env(trace_root, trajectory_id)

    async with _serve_session_tools(settings, trace_env) as tools:
        by_name = {tool.name: tool for tool in tools}
        print(f"tools advertised: {sorted(by_name)}")
        # Two sequential calls through ONE held session — the exact shape
        # that used to die on the second spawn's id-reuse guard.
        await by_name["search_codebase"].ainvoke({"query": "retrieval pipeline"})
        await by_name["glob"].ainvoke({"pattern": "**/*.md"})

    trace_dir = trace_root / trajectory_id
    events_path = trace_dir / SERVER_EVENTS_FILENAME
    if not events_path.exists():
        print(f"FAIL: no trace at {events_path}")
        return 1
    lines = [json.loads(line) for line in events_path.read_text().splitlines() if line.strip()]
    headers = [line for line in lines if line.get("_event") == "trace_header"]
    records = read_tool_call_records(trace_dir)

    print(f"trace file: {events_path}")
    print(f"header count: {len(headers)} (must be exactly 1 — one subprocess, one trace)")
    print(f"tool events: {[(r.tool_name, r.args_digest[:8]) for r in records]}")

    failures = []
    if len(headers) != 1:
        failures.append(f"expected exactly 1 trace header, found {len(headers)}")
    if [r.tool_name for r in records] != ["search_codebase", "glob"]:
        failures.append(f"expected [search_codebase, glob], got {[r.tool_name for r in records]}")
    if headers and headers[0].get("trajectory_id") != trajectory_id:
        failures.append("header trajectory_id mismatch")
    for failure in failures:
        print(f"FAIL: {failure}")
    if not failures:
        print("PASS: one held session, one trace, seq-ordered SERVER records")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--trace-root", default=None)
    arguments = parser.parse_args()
    trace_root = Path(arguments.trace_root or tempfile.mkdtemp(prefix="pydocs-trace-validate-"))
    return asyncio.run(_validate(arguments.workspace, trace_root))


if __name__ == "__main__":
    sys.exit(main())
