"""Tests for MCP server tool handlers (server.py).

Since tool handlers are closures inside run(), we mock FastMCP to capture
the registered tool functions, then call them directly. All 5 handlers are
async in sub-PR #2, so tests invoke them via asyncio.run().
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.server import _scope_from_internal, _validate_submodule
from pydocs_mcp.models import SearchScope


def _arun(coro):
    """Run an async coroutine in a fresh event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# -- Fixture: capture tool handlers from run() --

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
        pass  # Don't actually start the server


@pytest.fixture
def server_tools(tmp_path):
    """Run server.run() with a FakeMCP to capture all tool closures."""
    db_path = tmp_path / "test.db"
    conn = open_index_database(db_path)

    # Seed data
    conn.execute(
        "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
        ("__project__", "local", "Test project", "", "[]", "aaa", "project"),
    )
    conn.execute(
        "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
        ("fastapi", "0.100", "Web framework", "https://fastapi.example.com",
         '["starlette", "pydantic"]', "bbb", "dependency"),
    )
    conn.execute(
        "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
        ("__project__", "Overview", "Project overview with useful code", "project_module_doc"),
    )
    conn.execute(
        "INSERT INTO chunks(package,title,text,origin) VALUES(?,?,?,?)",
        ("fastapi", "Getting Started", "FastAPI is a modern web framework for APIs", "dependency_readme"),
    )
    conn.execute(
        "INSERT INTO module_members(package,module,name,kind,signature,return_annotation,parameters,docstring) VALUES(?,?,?,?,?,?,?,?)",
        ("__project__", "mymod", "compute", "function", "(x)", "int", "[]", "Compute things"),
    )
    conn.execute(
        "INSERT INTO module_members(package,module,name,kind,signature,return_annotation,parameters,docstring) VALUES(?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi", "FastAPI", "class", "()", "", "[]", "Main app class"),
    )
    conn.commit()
    rebuild_fulltext_index(conn)
    conn.close()

    fake_mcp = FakeMCP("test")

    # Create a fake mcp module with FastMCP class
    fake_mcp_module = MagicMock()
    fake_mcp_module.FastMCP = lambda name: fake_mcp

    with patch.dict(sys.modules, {"mcp": MagicMock(), "mcp.server": MagicMock(), "mcp.server.fastmcp": fake_mcp_module}):
        from pydocs_mcp.server import run
        run(db_path)

    return fake_mcp.tools, db_path


class TestListPackages:
    def test_returns_all_packages(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["list_packages"]())
        assert "__project__" in result
        assert "fastapi" in result

    def test_includes_version_and_summary(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["list_packages"]())
        assert "0.100" in result
        assert "Web framework" in result


class TestGetPackageDoc:
    def test_returns_project_doc(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("__project__"))
        assert "__project__" in result
        assert "local" in result

    def test_returns_dep_doc(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("fastapi"))
        assert "fastapi" in result
        assert "0.100" in result

    def test_includes_homepage(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("fastapi"))
        assert "https://fastapi.example.com" in result

    def test_includes_deps(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("fastapi"))
        assert "starlette" in result

    def test_includes_chunks(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("fastapi"))
        assert "Getting Started" in result

    def test_includes_api_symbols(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("fastapi"))
        assert "FastAPI" in result

    def test_unknown_package_returns_not_found(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("nonexistent_pkg"))
        assert "not found" in result

    def test_normalizes_package_name(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["get_package_doc"]("FastAPI"))
        assert "fastapi" in result


class TestSearchDocs:
    def test_returns_matching_chunks(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_docs"]("framework"))
        assert "fastapi" in result.lower()

    def test_no_matches_returns_message(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_docs"]("zzznonexistenttermzzz"))
        assert "No matches" in result

    def test_package_filter(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_docs"]("framework", package="fastapi"))
        assert "fastapi" in result.lower()

    def test_internal_true(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_docs"]("overview", internal=True))
        assert "project" in result.lower() or "overview" in result.lower()

    def test_internal_false(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_docs"]("framework", internal=False))
        assert "fastapi" in result.lower()

    def test_topic_filter(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_docs"]("overview", topic="Overview"))
        assert "overview" in result.lower() or "No matches" in result


class TestSearchApi:
    def test_returns_matching_symbols(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_api"]("compute"))
        assert "compute" in result

    def test_no_matches_returns_message(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_api"]("zzznonexistenttermzzz"))
        assert "No symbols" in result

    def test_package_filter(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_api"]("FastAPI", package="fastapi"))
        assert "fastapi" in result

    def test_internal_filter(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["search_api"]("compute", internal=True))
        assert "compute" in result


class TestInspectModule:
    def test_package_not_indexed_returns_error(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["inspect_module"]("nonexistent_pkg_xyz"))
        assert "not indexed" in result

    def test_invalid_submodule_rejected(self, server_tools):
        tools, _ = server_tools
        result = _arun(tools["inspect_module"]("fastapi", submodule="../evil"))
        assert "Invalid" in result

    def test_import_failure_returns_error(self, server_tools):
        tools, _ = server_tools
        # fastapi is indexed but likely not importable in test env
        result = _arun(tools["inspect_module"]("fastapi"))
        assert "Cannot import" in result or "No API" in result or "fastapi" in result

    def test_valid_stdlib_module(self, server_tools):
        """Test with a module that's guaranteed to be importable."""
        tools, db_path = server_tools
        conn = open_index_database(db_path)
        conn.execute(
            "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
            ("json", "stdlib", "JSON encoder/decoder", "", "[]", "jjj", "dependency"),
        )
        conn.commit()
        conn.close()
        result = _arun(tools["inspect_module"]("json"))
        assert "json" in result

    def test_submodule_inspection(self, server_tools):
        tools, db_path = server_tools
        conn = open_index_database(db_path)
        conn.execute(
            "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
            ("os", "stdlib", "OS interface", "", "[]", "ooo", "dependency"),
        )
        conn.commit()
        conn.close()
        result = _arun(tools["inspect_module"]("os", submodule="path"))
        assert "path" in result.lower()

    def test_package_with_submodules_listing(self, server_tools):
        """Test inspect_module on a package that has submodules but no direct API."""
        tools, db_path = server_tools
        conn = open_index_database(db_path)
        conn.execute(
            "INSERT INTO packages VALUES(?,?,?,?,?,?,?)",
            ("email", "stdlib", "Email library", "", "[]", "eee", "dependency"),
        )
        conn.commit()
        conn.close()
        result = _arun(tools["inspect_module"]("email"))
        # email package has submodules like mime, policy, etc.
        assert "email" in result.lower()


class TestValidateSubmodule:
    def test_empty_is_valid(self):
        assert _validate_submodule("") is True

    def test_simple_name_valid(self):
        assert _validate_submodule("routing") is True

    def test_dotted_name_valid(self):
        assert _validate_submodule("a.b.c") is True

    def test_path_traversal_invalid(self):
        assert _validate_submodule("../evil") is False

    def test_semicolon_invalid(self):
        assert _validate_submodule("a;drop") is False


class TestScopeFromInternal:
    def test_true_is_project_only(self):
        assert _scope_from_internal(True) is SearchScope.PROJECT_ONLY

    def test_false_is_dependencies_only(self):
        assert _scope_from_internal(False) is SearchScope.DEPENDENCIES_ONLY

    def test_none_is_all(self):
        assert _scope_from_internal(None) is SearchScope.ALL
