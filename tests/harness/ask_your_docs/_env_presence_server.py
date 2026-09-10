"""A stdio MCP fixture server answering ONE question: is this variable set in MY environment?

It reports ``"present"`` / ``"absent"`` and never a value (G8), so a test can prove what a
spawned child inherits without the secret ever crossing the pipe. It ignores argv, so it
stands in for ``pydocs_mcp serve`` behind the real serve argv. The leading underscore keeps
pytest from collecting it.
"""

from __future__ import annotations

import os

from mcp.server.fastmcp import FastMCP

server = FastMCP("env-presence")


@server.tool()
def env_presence(name: str) -> str:
    """``"present"`` if ``name`` is set in this process's environment, else ``"absent"``."""
    return "present" if name in os.environ else "absent"


if __name__ == "__main__":
    server.run("stdio")
