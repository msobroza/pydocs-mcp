"""Tests for the MCP ``get_document_tree`` handler (spec §13.1, §16 AC #2).

Covers the sub-PR #5 tool that returns a module's :class:`DocumentNode`
tree as JSON. Uses the same ``FakeMCP`` pattern as ``test_server.py``
to capture the registered tool closures without starting a real MCP
server.
"""
from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import MagicMock, patch

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind
from pydocs_mcp.storage.sqlite import SqliteDocumentTreeStore


def _arun(coro):
    """Run an async coroutine in a fresh event loop (mirrors test_server.py)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeMCP:
    """Captures tool registrations from FastMCP without starting a server."""

    def __init__(self, name):
        self.name = name
        self.tools = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator

    def run(self, transport=None):
        pass


def _module_tree(
    module: str,
    *children: DocumentNode,
    text: str = "# module body\n",
    content_hash: str | None = None,
) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module.rsplit(".", 1)[-1],
        kind=NodeKind.MODULE,
        source_path=f"{module.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        text=text,
        content_hash=content_hash or f"mh-{module}",
        children=tuple(children),
    )


def _function_leaf(
    node_id: str, parent_id: str, text: str = "def f(): pass",
) -> DocumentNode:
    return DocumentNode(
        node_id=node_id,
        qualified_name=node_id,
        title=node_id.rsplit(".", 1)[-1],
        kind=NodeKind.FUNCTION,
        source_path="x.py",
        start_line=3,
        end_line=4,
        text=text,
        content_hash=f"fh-{node_id}",
        parent_id=parent_id,
    )


async def _seed_tree(db_path, package: str, tree: DocumentNode) -> None:
    """Persist ``tree`` under ``package`` via the real SqliteDocumentTreeStore.

    Using the production store (not hand-written INSERTs) means the test
    exercises the same round-trip the MCP handler will see at runtime.
    """
    provider = build_connection_provider(db_path)
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save_many([tree], package=package)


@pytest.fixture
def server_tools(tmp_path):
    """Boot ``server.run`` against a seeded SQLite DB with one module tree."""
    db_path = tmp_path / "test.db"
    open_index_database(db_path).close()

    child = _function_leaf("mypkg.mymod.foo", parent_id="mypkg.mymod")
    tree = _module_tree("mypkg.mymod", child, text="mod body\n")
    _arun(_seed_tree(db_path, "mypkg", tree))

    fake_mcp = FakeMCP("test")
    fake_mcp_module = MagicMock()
    fake_mcp_module.FastMCP = lambda name: fake_mcp

    with patch.dict(
        sys.modules,
        {
            "mcp": MagicMock(),
            "mcp.server": MagicMock(),
            "mcp.server.fastmcp": fake_mcp_module,
        },
    ):
        from pydocs_mcp.server import run
        run(db_path)

    return fake_mcp.tools, db_path


class TestGetDocumentTree:
    def test_returns_json_with_module_fields(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_document_tree"]("mypkg", "mypkg.mymod"))
        data = json.loads(result)
        assert data["qualified_name"] == "mypkg.mymod"
        assert data["kind"] == "module"
        assert data["title"] == "mymod"

    def test_returns_json_with_children(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_document_tree"]("mypkg", "mypkg.mymod"))
        data = json.loads(result)
        assert isinstance(data["children"], list)
        assert len(data["children"]) == 1
        assert data["children"][0]["qualified_name"] == "mypkg.mymod.foo"
        assert data["children"][0]["kind"] == "function"

    def test_missing_module_returns_not_found_message(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_document_tree"]("mypkg", "nonexistent.module"))
        assert "No tree" in result
        assert "mypkg" in result and "nonexistent.module" in result

    def test_missing_package_returns_not_found_message(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_document_tree"]("no_such_pkg", "some.mod"))
        assert "No tree" in result

    def test_storage_error_returns_error_message(self, server_tools, monkeypatch):
        """When the underlying store raises, the handler must not leak a traceback."""
        tools, _ = server_tools
        from pydocs_mcp.application.document_tree_service import DocumentTreeService

        async def _boom(self, package, module):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated DB fault")
        monkeypatch.setattr(DocumentTreeService, "get_tree", _boom)
        result = _arun(tools["get_document_tree"]("mypkg", "mypkg.mymod"))
        assert "Error" in result
        assert "mypkg" in result and "mypkg.mymod" in result
