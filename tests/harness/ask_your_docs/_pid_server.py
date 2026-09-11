"""A stdio MCP fixture server that reports WHICH process answered — the serve-session stand-in.

On start it appends its PID to the file named by ``AYD_PID_LOG``, then waits
``AYD_START_DELAY`` seconds before serving (both arrive through ``serve_connection``'s
``subprocess_env`` overlay). It ignores argv, so it stands in for ``pydocs_mcp serve``
behind the real serve argv. The leading underscore keeps pytest from collecting it.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

server = FastMCP("pid-probe")


@server.tool()
def echo(text: str = "", project: str = "") -> str:
    """The arguments this call ACTUALLY carried, plus the answering PID, as JSON."""
    return json.dumps({"text": text, "project": project, "pid": os.getpid()})


@server.tool()
async def slow(seconds: float) -> str:
    """Answer after ``seconds`` — long enough for a test to cancel the caller mid-call."""
    await asyncio.sleep(seconds)
    return "done"


@server.tool()
def exit_now() -> str:
    """Die mid-call without answering, as a crashed serve child would."""
    os._exit(1)


if __name__ == "__main__":
    with Path(os.environ["AYD_PID_LOG"]).open("a", encoding="utf-8") as pid_log:
        pid_log.write(f"{os.getpid()}\n")
    time.sleep(float(os.environ.get("AYD_START_DELAY", "0")))
    server.run("stdio")
