"""The MCP surface is exactly the six task-shaped tools (spec §D1/§D2)."""

import inspect

from pydocs_mcp import server
from pydocs_mcp.application.tool_docs import TOOL_DOCS

_EXPECTED = {
    "get_overview",
    "search_codebase",
    "get_symbol",
    "get_context",
    "get_references",
    "get_why",
}


def test_run_registers_exactly_six_tools() -> None:
    source = inspect.getsource(server)
    for name in _EXPECTED:
        assert f"async def {name}(" in source
    for legacy in ("async def search(", "async def lookup("):
        assert legacy not in source


def test_tool_docstrings_come_from_tool_docs() -> None:
    source = inspect.getsource(server)
    assert "TOOL_DOCS" in source and "SERVER_INSTRUCTIONS" in source
    # Silence the "imported but unused" lint by touching the import here.
    assert set(TOOL_DOCS) == _EXPECTED
