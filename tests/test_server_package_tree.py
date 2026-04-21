"""Tests for the MCP ``get_package_tree`` handler (spec §13.2, §16 AC #2).

Covers the sub-PR #5 tool that assembles a PACKAGE arborescence from
the set of per-module :class:`DocumentNode` trees stored in
``document_trees``. Relies on :func:`build_package_tree` (module-path
trie) to synthesize PACKAGE + SUBPACKAGE scaffolding around the stored
MODULE leaves.
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
    text: str = "# module body\n",
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
        content_hash=f"mh-{module}",
    )


async def _seed_tree(db_path, package: str, tree: DocumentNode) -> None:
    provider = build_connection_provider(db_path)
    store = SqliteDocumentTreeStore(provider=provider)
    await store.save_many([tree], package=package)


def _boot_server(db_path):
    """Boot ``server.run`` with a ``FakeMCP`` and return the captured tools."""
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
    return fake_mcp.tools


@pytest.fixture
def single_module_tools(tmp_path):
    """Package with exactly one MODULE — arborescence has a single leaf."""
    db_path = tmp_path / "single.db"
    open_index_database(db_path).close()
    _arun(_seed_tree(db_path, "mypkg", _module_tree("mypkg.mymod")))
    return _boot_server(db_path), db_path


@pytest.fixture
def multi_module_tools(tmp_path):
    """Package with a SUBPACKAGE branch plus a sibling MODULE leaf."""
    db_path = tmp_path / "multi.db"
    open_index_database(db_path).close()
    _arun(_seed_tree(db_path, "multipkg", _module_tree("multipkg.top")))
    _arun(_seed_tree(db_path, "multipkg", _module_tree("multipkg.sub.deep")))
    return _boot_server(db_path), db_path


class TestGetPackageTree:
    def test_single_module_returns_package_with_module_child(
        self, single_module_tools,
    ):
        tools, _ = single_module_tools
        result = _arun(tools["get_package_tree"]("mypkg"))
        data = json.loads(result)
        assert data["kind"] == "package"
        assert data["qualified_name"] == "mypkg"
        # Single module → the only child is the MODULE leaf directly
        # (no SUBPACKAGE synthesis needed).
        assert len(data["children"]) == 1
        assert data["children"][0]["kind"] == "module"
        assert data["children"][0]["qualified_name"] == "mypkg.mymod"

    def test_multi_module_with_subpackages(self, multi_module_tools):
        tools, _ = multi_module_tools
        result = _arun(tools["get_package_tree"]("multipkg"))
        data = json.loads(result)
        assert data["kind"] == "package"
        child_kinds = {c["kind"] for c in data["children"]}
        assert "subpackage" in child_kinds
        assert "module" in child_kinds
        # SUBPACKAGE should contain the nested module as a MODULE child.
        sub = next(c for c in data["children"] if c["kind"] == "subpackage")
        assert sub["qualified_name"] == "multipkg.sub"
        assert any(
            gc["qualified_name"] == "multipkg.sub.deep" for gc in sub["children"]
        )

    def test_unknown_package_returns_empty_message(self, single_module_tools):
        tools, _ = single_module_tools
        result = _arun(tools["get_package_tree"]("nonexistent_pkg_zzz"))
        assert "No indexed trees" in result
        assert "nonexistent_pkg_zzz" in result

    def test_storage_error_returns_error_message(
        self, single_module_tools, monkeypatch,
    ):
        """Downstream failure must not leak a traceback across the MCP boundary."""
        tools, _ = single_module_tools
        from pydocs_mcp.application.document_tree_service import DocumentTreeService

        async def _boom(self, package):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated fault")
        monkeypatch.setattr(
            DocumentTreeService, "list_package_modules", _boom,
        )
        result = _arun(tools["get_package_tree"]("mypkg"))
        assert "Error" in result
        assert "mypkg" in result

    def test_normalizes_pypi_package_name(self, tmp_path):
        """User-facing ``Flask-Login`` must resolve to the DB-stored ``flask_login``."""
        db_path = tmp_path / "flask.db"
        open_index_database(db_path).close()
        _arun(_seed_tree(db_path, "flask_login", _module_tree("flask_login.login")))
        tools = _boot_server(db_path)

        result = _arun(tools["get_package_tree"]("Flask-Login"))
        data = json.loads(result)
        # Root must use the normalised name so build_package_tree's trie
        # prefix-match finds the stored modules under ``flask_login``.
        assert data["kind"] == "package"
        assert data["qualified_name"] == "flask_login"
        assert len(data["children"]) == 1
        assert data["children"][0]["qualified_name"] == "flask_login.login"
