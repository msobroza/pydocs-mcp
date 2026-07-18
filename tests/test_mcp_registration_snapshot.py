"""Golden-file snapshot of the rendered MCP registration surface (ADR 0005/0006).

Registers the nine frozen tools on a real FastMCP instance and dumps
``list_tools()`` — tool name → description + inputSchema + outputSchema — then
compares against ``tests/fixtures/goldens/mcp_registration_surface.json``.

WHY a byte-level golden: the descriptions now come from the packaged
``defaults/descriptions.md`` rather than Python literals; this snapshot is the
long-term drift gate proving that what MCP clients actually see only changes
when someone deliberately edits the source document (or the input models) and
regenerates the golden in the same commit.

Regenerate after an intentional surface change:

    .venv/bin/python -c "import tests.test_mcp_registration_snapshot as t; t.write_golden()"
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

_GOLDEN_PATH = (
    Path(__file__).resolve().parent / "fixtures" / "goldens" / "mcp_registration_surface.json"
)


def _registration_surface() -> dict[str, dict[str, object]]:
    from mcp.server.fastmcp import FastMCP

    from pydocs_mcp.server import _register_tools

    mcp = FastMCP("pydocs-mcp-snapshot")
    # tools=None: handlers close over the router lazily; registration itself
    # never calls it, so no DB/services are needed to advertise the surface.
    _register_tools(mcp, tools=None)

    loop = asyncio.new_event_loop()
    try:
        listed = loop.run_until_complete(mcp.list_tools())
    finally:
        loop.close()

    return {
        tool.name: {
            "description": tool.description,
            "inputSchema": tool.inputSchema,
            "outputSchema": tool.outputSchema,
        }
        for tool in listed
    }


def write_golden() -> None:
    """Regeneration helper — run manually after an intentional surface edit."""
    _GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    surface = _registration_surface()
    _GOLDEN_PATH.write_text(json.dumps(surface, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_registration_surface_matches_golden() -> None:
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))
    surface = _registration_surface()
    assert sorted(surface) == sorted(golden)
    for name in golden:
        assert surface[name] == golden[name], f"{name} registration surface drifted"


def test_registered_descriptions_are_the_live_tool_docs() -> None:
    from pydocs_mcp.application.tool_docs import TOOL_DOCS

    surface = _registration_surface()
    for name, entry in surface.items():
        assert entry["description"] == TOOL_DOCS[name]
