"""A stdio MCP fixture server traced exactly as ``pydocs_mcp serve`` is (ADR 0009).

It reads its trace switch the way ``serve`` does — ``AppConfig.load().trace``, so the three
``PYDOCS_TRACE__*`` names the page overlays are what turn it on — and records every call of its
one tool through the real recorder. It ignores argv, so it stands in for ``pydocs_mcp serve``
behind the real serve argv. The leading underscore keeps pytest from collecting it.
"""

from __future__ import annotations

import json
import os

from pydocs_mcp.observability import build_traced_fastmcp
from pydocs_mcp.retrieval.config import AppConfig

server = build_traced_fastmcp(AppConfig.load().trace, name="traced-probe", instructions="")


@server.tool()
def echo(text: str = "") -> str:
    """The text this call carried, plus the answering PID, as JSON."""
    return json.dumps({"text": text, "pid": os.getpid()})


if __name__ == "__main__":
    server.run("stdio")
