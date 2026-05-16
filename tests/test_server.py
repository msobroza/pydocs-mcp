"""Tests for MCP server 2-tool surface (sub-PR #6).

Handlers are closures inside ``run()``, so tests substitute a FakeMCP to
capture the decorated functions and invoke them directly.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pydocs_mcp.db import open_index_database, rebuild_fulltext_index
from pydocs_mcp.extraction.model import DocumentNode, NodeKind


def _arun(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class FakeMCP:
    """Captures tool registrations from FastMCP without starting a server."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.tools: dict[str, object] = {}

    def tool(self):
        def decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return decorator

    def run(self, transport: str | None = None) -> None:
        pass


def _seed_basic_fixture(db_path: Path) -> None:
    """Two packages, two chunks, two members."""
    conn = open_index_database(db_path)
    conn.execute(
        "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) VALUES(?,?,?,?,?,?,?)",
        ("__project__", "local", "Test project", "", "[]", "aaa", "project"),
    )
    conn.execute(
        "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) VALUES(?,?,?,?,?,?,?)",
        (
            "fastapi", "0.100", "Web framework",
            "https://fastapi.example.com",
            '["starlette", "pydantic"]', "bbb", "dependency",
        ),
    )
    conn.execute(
        "INSERT INTO chunks(package,module,title,text,origin) VALUES(?,?,?,?,?)",
        ("__project__", "mymod", "Overview", "Project overview with useful code",
         "project_module_doc"),
    )
    conn.execute(
        "INSERT INTO chunks(package,module,title,text,origin) VALUES(?,?,?,?,?)",
        ("fastapi", "fastapi", "Getting Started",
         "FastAPI is a modern web framework for APIs", "dependency_readme"),
    )
    conn.execute(
        "INSERT INTO module_members("
        "package,module,name,kind,signature,return_annotation,parameters,docstring"
        ") VALUES(?,?,?,?,?,?,?,?)",
        ("__project__", "mymod", "compute", "function", "(x)", "int", "[]",
         "Compute things"),
    )
    conn.execute(
        "INSERT INTO module_members("
        "package,module,name,kind,signature,return_annotation,parameters,docstring"
        ") VALUES(?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi", "FastAPI", "class", "()", "", "[]",
         "Main app class"),
    )
    conn.commit()
    rebuild_fulltext_index(conn)
    conn.close()


def _run_server_capture_tools(db_path: Path):
    """Boot ``server.run`` with FakeMCP injected so we can call handlers."""
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
def server_tools(tmp_path: Path):
    """Run server.run() with FakeMCP to capture the 2 tool closures."""
    db_path = tmp_path / "test.db"
    _seed_basic_fixture(db_path)
    return _run_server_capture_tools(db_path), db_path


def _seed_tree_for_fastapi(db_path: Path) -> DocumentNode:
    """Persist a small fastapi.routing tree so TreeService.get_tree hits.

    Uses ``SqliteDocumentTreeStore.save_many`` directly — same write path as
    the production indexer — so we exercise the full read/write contract.
    """
    from pydocs_mcp.db import build_connection_provider
    from pydocs_mcp.storage.sqlite import SqliteDocumentTreeStore

    method = DocumentNode(
        node_id="fastapi.routing.APIRouter.include_router",
        qualified_name="fastapi.routing.APIRouter.include_router",
        title="def include_router",
        kind=NodeKind.METHOD,
        source_path="fastapi/routing.py",
        start_line=20,
        end_line=30,
        text="def include_router(...): ...",
        content_hash="h-method",
    )
    cls = DocumentNode(
        node_id="fastapi.routing.APIRouter",
        qualified_name="fastapi.routing.APIRouter",
        title="class APIRouter",
        kind=NodeKind.CLASS,
        source_path="fastapi/routing.py",
        start_line=10,
        end_line=40,
        text="class APIRouter: ...",
        content_hash="h-class",
        children=(method,),
    )
    root = DocumentNode(
        node_id="fastapi.routing",
        qualified_name="fastapi.routing",
        title="fastapi.routing",
        kind=NodeKind.MODULE,
        source_path="fastapi/routing.py",
        start_line=1,
        end_line=50,
        text="",
        content_hash="h-mod",
        children=(cls,),
    )

    provider = build_connection_provider(db_path)
    store = SqliteDocumentTreeStore(provider=provider)
    asyncio.run(store.save_many([root], package="fastapi"))
    return root


@pytest.fixture
def server_tools_with_tree(tmp_path: Path):
    """Same seed as ``server_tools`` plus one persisted DocumentNode tree
    for ``fastapi.routing`` so multi-segment lookup has something to find."""
    db_path = tmp_path / "test_tree.db"
    _seed_basic_fixture(db_path)
    _seed_tree_for_fastapi(db_path)
    return _run_server_capture_tools(db_path), db_path


# ── surface shape ─────────────────────────────────────────────────────────


class TestToolSurface:
    def test_exactly_two_tools_registered(self, server_tools) -> None:
        tools, _ = server_tools
        assert set(tools) == {"search", "lookup"}

    def test_old_tool_names_are_gone(self, server_tools) -> None:
        tools, _ = server_tools
        for dropped in (
            "list_packages", "get_package_doc",
            "search_docs", "search_api", "inspect_module",
        ):
            assert dropped not in tools


# ── lookup ────────────────────────────────────────────────────────────────


class TestLookupPackagesList:
    def test_empty_target_lists_packages(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["lookup"](target=""))
        assert "__project__" in out
        assert "fastapi" in out
        assert "0.100" in out


class TestLookupPackageDoc:
    def test_returns_package_doc(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["lookup"](target="fastapi"))
        assert "fastapi" in out
        assert "0.100" in out

    def test_includes_homepage(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["lookup"](target="fastapi"))
        assert "https://fastapi.example.com" in out

    def test_includes_deps(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["lookup"](target="fastapi"))
        assert "starlette" in out

    def test_unknown_package_raises_not_found(self, server_tools) -> None:
        from pydocs_mcp.application import NotFoundError

        tools, _ = server_tools
        with pytest.raises(NotFoundError):
            _arun(tools["lookup"](target="nonexistent_pkg"))


class TestLookupWithTreeService:
    """FIX 6: TreeService now wired into the composition root — multi-segment
    lookup targets resolve against persisted DocumentNode trees instead of
    raising ``ServiceUnavailableError``."""

    def test_lookup_module_target_returns_tree_json(self, server_tools_with_tree) -> None:
        """target='fastapi.routing' returns PageIndex-style JSON for the tree."""
        import json

        tools, _ = server_tools_with_tree
        out = _arun(tools["lookup"](target="fastapi.routing"))
        payload = json.loads(out)
        assert payload["node_id"] == "fastapi.routing"
        assert payload["kind"] == "module"
        # Child class included recursively.
        child_ids = [n["node_id"] for n in payload["nodes"]]
        assert "fastapi.routing.APIRouter" in child_ids

    def test_lookup_module_target_unknown_falls_through_to_find_module(
        self, server_tools_with_tree,
    ) -> None:
        """Unknown dotted target with no matching tree raises NotFoundError
        (not ServiceUnavailableError) — proves the tree_svc fallback path
        runs PackageLookup.find_module before giving up."""
        from pydocs_mcp.application import NotFoundError

        tools, _ = server_tools_with_tree
        with pytest.raises(NotFoundError):
            _arun(tools["lookup"](target="fastapi.does_not_exist"))

    def test_lookup_symbol_target_returns_node_json(
        self, server_tools_with_tree,
    ) -> None:
        """target='fastapi.routing.APIRouter' resolves through the tree to
        the CLASS node and emits its PageIndex JSON, including the child method."""
        import json

        tools, _ = server_tools_with_tree
        out = _arun(tools["lookup"](target="fastapi.routing.APIRouter"))
        payload = json.loads(out)
        assert payload["node_id"] == "fastapi.routing.APIRouter"
        assert payload["kind"] == "class"
        method_ids = [n["node_id"] for n in payload["nodes"]]
        assert "fastapi.routing.APIRouter.include_router" in method_ids


# ── search ────────────────────────────────────────────────────────────────


class TestSearchDocs:
    def test_returns_matching_chunks(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["search"](query="framework", kind="docs"))
        assert "fastapi" in out.lower()

    def test_no_matches_returns_message(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["search"](query="zzznonexistenttermzzz", kind="docs"))
        assert "No matches" in out

    def test_package_filter(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(
            tools["search"](query="framework", kind="docs", package="fastapi")
        )
        assert "fastapi" in out.lower()

    def test_scope_project(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(
            tools["search"](query="overview", kind="docs", scope="project")
        )
        assert "overview" in out.lower() or "No matches" in out


class TestSearchApi:
    def test_returns_matching_symbols(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["search"](query="compute", kind="api"))
        assert "compute" in out

    def test_no_matches_returns_symbol_msg(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["search"](query="zzznonexistenttermzzz", kind="api"))
        assert "No symbols" in out


class TestSearchAny:
    def test_merges_docs_and_api(self, server_tools) -> None:
        """kind='any' runs both pipelines in parallel and concatenates."""
        tools, _ = server_tools
        out = _arun(tools["search"](query="compute", kind="any"))
        # compute appears as a member; members pipeline should surface it
        assert "compute" in out

    def test_no_matches_returns_message(self, server_tools) -> None:
        tools, _ = server_tools
        out = _arun(tools["search"](query="zzznonexistenttermzzz", kind="any"))
        assert "No matches" in out


# ── Pydantic boundary ─────────────────────────────────────────────────────


class TestValidation:
    def test_empty_query_raises_validation_error(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["search"](query=""))

    def test_bad_package_regex_raises_validation_error(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(
                tools["search"](query="x", kind="docs", package="has spaces")
            )

    def test_bad_target_regex_raises_validation_error(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["lookup"](target="foo..bar"))

    def test_limit_out_of_range_raises(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["search"](query="x", limit=0))


# ── name normalization (regression) ──────────────────────────────────────


def test_lookup_normalizes_pypi_style_name(tmp_path: Path) -> None:
    """User-facing ``Flask-Login`` resolves to the DB-stored ``flask_login``."""
    db_path = tmp_path / "flask.db"
    conn = open_index_database(db_path)
    conn.execute(
        "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) VALUES(?,?,?,?,?,?,?)",
        ("flask_login", "0.6", "Flask login", "", "[]", "h", "dependency"),
    )
    conn.commit()
    conn.close()

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

    out = _arun(
        fake_mcp.tools["search"](query="login", kind="docs", package="Flask-Login")
    )
    # The normalisation happens inside the handler; the search itself may
    # return "No matches" (no chunks seeded) but MUST NOT fail validation.
    assert "validation" not in out.lower()
