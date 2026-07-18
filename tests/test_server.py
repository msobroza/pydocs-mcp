"""Tests for the MCP server's six task-shaped tool surface (spec §D1/§D2).

Handlers are closures inside ``run()``, so tests substitute a FakeMCP to
capture the decorated functions and invoke them directly. Every response is
enveloped (freshness header + pointer resolution), so behavioral assertions
target substrings of the body, not exact equality.
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


def _text(result) -> str:
    """The markdown text block of a handler's ``CallToolResult``."""
    return result.content[0].text


class FakeMCP:
    """Captures tool registrations from FastMCP without starting a server.

    Accepts the same construction shape as FastMCP — positional ``name`` plus
    any kwargs (notably ``instructions=``). ``tool(**kwargs)`` swallows
    annotation kwargs (``annotations=ToolAnnotations(...)``) so tests don't
    need to track every advisory hint plumbed through the decorator.
    """

    def __init__(self, name: str, **kwargs: object) -> None:
        self.name = name
        self.kwargs = kwargs
        self.tools: dict[str, object] = {}

    def tool(self, **kwargs: object):
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
            "fastapi",
            "0.100",
            "Web framework",
            "https://fastapi.example.com",
            '["starlette", "pydantic"]',
            "bbb",
            "dependency",
        ),
    )
    conn.execute(
        "INSERT INTO chunks(package,module,title,text,origin) VALUES(?,?,?,?,?)",
        (
            "__project__",
            "mymod",
            "Overview",
            "Project overview with useful code",
            "project_module_doc",
        ),
    )
    conn.execute(
        "INSERT INTO chunks(package,module,title,text,origin) VALUES(?,?,?,?,?)",
        (
            "fastapi",
            "fastapi",
            "Getting Started",
            "FastAPI is a modern web framework for APIs",
            "dependency_readme",
        ),
    )
    conn.execute(
        "INSERT INTO module_members("
        "package,module,name,kind,signature,return_annotation,parameters,docstring"
        ") VALUES(?,?,?,?,?,?,?,?)",
        ("__project__", "mymod", "compute", "function", "(x)", "int", "[]", "Compute things"),
    )
    conn.execute(
        "INSERT INTO module_members("
        "package,module,name,kind,signature,return_annotation,parameters,docstring"
        ") VALUES(?,?,?,?,?,?,?,?)",
        ("fastapi", "fastapi", "FastAPI", "class", "()", "", "[]", "Main app class"),
    )
    conn.commit()
    rebuild_fulltext_index(conn)
    conn.close()


def _run_server_capture_tools(db_path: Path):
    """Boot ``server.run`` with FakeMCP injected so we can call handlers.

    Pins the vector-free BM25 chunk pipeline via ``config_path``: these tests
    assert search WIRING (matching chunks, package filter) on the deterministic
    FTS path, not dense ranking. The shipped default is dense+graph (needs a
    seeded ``.tq`` sidecar); pinning BM25 keeps the wiring tests deterministic
    without seeding vectors. The dense+graph default is covered by the benchmark
    A/B + the dense-pipeline unit tests.
    """
    config_path = db_path.parent / "bm25_overlay.yaml"
    config_path.write_text(
        "pipelines:\n  chunk:\n    - default: true\n"
        "      pipeline_path: pipelines/chunk_search.yaml\n"
    )
    fake_mcp = FakeMCP("test")
    fake_mcp_module = MagicMock()
    fake_mcp_module.FastMCP = lambda name, **kwargs: fake_mcp

    with patch.dict(
        sys.modules,
        {
            "mcp": MagicMock(),
            "mcp.server": MagicMock(),
            "mcp.server.fastmcp": fake_mcp_module,
            # ``server.run`` imports ``mcp.types.ToolAnnotations`` to attach
            # readOnly / idempotent / openWorld hints to each tool. The fake
            # module just needs to be importable here; FakeMCP swallows the
            # ``annotations=`` kwarg without inspecting the value.
            "mcp.types": MagicMock(),
        },
    ):
        from pydocs_mcp.server import run

        run(db_path, config_path)

    return fake_mcp.tools


@pytest.fixture
def server_tools(tmp_path: Path):
    """Run server.run() with FakeMCP to capture the six tool closures."""
    db_path = tmp_path / "test.db"
    _seed_basic_fixture(db_path)
    return _run_server_capture_tools(db_path), db_path


def _seed_tree_for_fastapi(db_path: Path) -> DocumentNode:
    """Persist a small fastapi.routing tree so TreeService.get_tree hits.

    Uses ``SqliteDocumentTreeStore.save_many`` directly — same write path as
    the production indexer — so we exercise the full read/write contract.
    """
    from pydocs_mcp.storage.factories import build_connection_provider
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
    def test_exactly_nine_tools_registered(self, server_tools) -> None:
        tools, _ = server_tools
        assert set(tools) == {
            "get_overview",
            "search_codebase",
            "get_symbol",
            "get_context",
            "get_references",
            "get_why",
            "grep",
            "glob",
            "read_file",
        }

    def test_old_tool_names_are_gone(self, server_tools) -> None:
        tools, _ = server_tools
        for dropped in (
            "search",
            "lookup",
            "list_packages",
            "get_package_doc",
            "search_docs",
            "search_api",
            "inspect_module",
        ):
            assert dropped not in tools


# ── get_overview ───────────────────────────────────────────────────────────


class TestOverviewStructuralCard:
    """get_overview renders the §D17 structural orientation card (blocks 1,
    3-7) — NOT a package-doc listing. The card scopes to a package
    (``__project__`` by default), reports the corpus census, and degrades the
    communities block to an enablement hint because the basic fixture seeds no
    node_scores."""

    def test_empty_package_scopes_to_project_card(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["get_overview"](package="")))
        # H1 scopes to the project package; the four §D17 H2 blocks are present.
        assert "# Overview — __project__" in out
        assert "## Module map" in out and "## Entry points" in out
        assert "## Structure communities" in out and "## Dependency profile" in out
        # The census counts every loaded package (2) even though the card
        # scopes symbols/modules to __project__.
        assert "2 packages" in out

    def test_named_package_scopes_the_card(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["get_overview"](package="fastapi")))
        assert "# Overview — fastapi" in out

    def test_communities_hint_without_node_scores(self, server_tools) -> None:
        # The basic fixture seeds no node_scores → the communities block
        # renders the enablement hint anchored on the YAML knob.
        tools, _ = server_tools
        out = _text(_arun(tools["get_overview"](package="")))
        assert "enable reference_graph.node_scores" in out

    def test_unknown_package_returns_empty_card_not_error(self, server_tools) -> None:
        # Unlike the 2a package-doc path, the structural card never raises for
        # a missing package — it builds an empty-ish card (all blocks present,
        # no rows). The census still counts the loaded corpus.
        tools, _ = server_tools
        out = _text(_arun(tools["get_overview"](package="nonexistent_pkg")))
        assert "# Overview — nonexistent_pkg" in out


class TestSymbolWithTreeService:
    """TreeService is wired into the composition root — multi-segment
    get_symbol targets resolve against persisted DocumentNode trees instead of
    raising ``ServiceUnavailableError``."""

    def test_symbol_module_target_returns_tree_json(self, server_tools_with_tree) -> None:
        """target='fastapi.routing' returns PageIndex-style JSON for the tree."""
        import json

        tools, _ = server_tools_with_tree
        out = _text(_arun(tools["get_symbol"](target="fastapi.routing")))
        payload = json.loads(out)
        assert payload["node_id"] == "fastapi.routing"
        assert payload["kind"] == "module"
        # Child class included recursively.
        child_ids = [n["node_id"] for n in payload["nodes"]]
        assert "fastapi.routing.APIRouter" in child_ids

    def test_symbol_module_target_unknown_falls_through_to_find_module(
        self,
        server_tools_with_tree,
    ) -> None:
        """Unknown dotted target with no matching tree raises NotFoundError
        (not ServiceUnavailableError) — proves the tree_svc fallback path
        runs PackageLookup.find_module before giving up."""
        from pydocs_mcp.application import NotFoundError

        tools, _ = server_tools_with_tree
        with pytest.raises(NotFoundError):
            _arun(tools["get_symbol"](target="fastapi.does_not_exist"))

    def test_symbol_symbol_target_returns_node_json(
        self,
        server_tools_with_tree,
    ) -> None:
        """target='fastapi.routing.APIRouter' resolves through the tree to
        the CLASS node and emits its PageIndex JSON, including the child method."""
        import json

        tools, _ = server_tools_with_tree
        out = _text(_arun(tools["get_symbol"](target="fastapi.routing.APIRouter")))
        payload = json.loads(out)
        assert payload["node_id"] == "fastapi.routing.APIRouter"
        assert payload["kind"] == "class"
        method_ids = [n["node_id"] for n in payload["nodes"]]
        assert "fastapi.routing.APIRouter.include_router" in method_ids


# ── search ────────────────────────────────────────────────────────────────


class TestSearchDocs:
    def test_returns_matching_chunks(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="framework", kind="docs")))
        assert "fastapi" in out.lower()

    def test_no_matches_returns_message(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="zzznonexistenttermzzz", kind="docs")))
        assert "No matches" in out

    def test_package_filter(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(
            _arun(tools["search_codebase"](query="framework", kind="docs", package="fastapi"))
        )
        assert "fastapi" in out.lower()

    def test_scope_project(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="overview", kind="docs", scope="project")))
        assert "overview" in out.lower() or "No matches" in out


class TestSearchApi:
    def test_returns_matching_symbols(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="compute", kind="api")))
        assert "compute" in out

    def test_no_matches_returns_symbol_msg(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="zzznonexistenttermzzz", kind="api")))
        assert "No symbols" in out


class TestSearchAny:
    def test_merges_docs_and_api(self, server_tools) -> None:
        """kind='any' runs both pipelines in parallel and concatenates."""
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="compute", kind="any")))
        # compute appears as a member; members pipeline should surface it
        assert "compute" in out

    def test_no_matches_returns_message(self, server_tools) -> None:
        tools, _ = server_tools
        out = _text(_arun(tools["search_codebase"](query="zzznonexistenttermzzz", kind="any")))
        assert "No matches" in out


# ── Pydantic boundary ─────────────────────────────────────────────────────


class TestValidation:
    def test_empty_query_raises_validation_error(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["search_codebase"](query=""))

    def test_bad_package_regex_raises_validation_error(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["search_codebase"](query="x", kind="docs", package="has spaces"))

    def test_bad_target_regex_raises_validation_error(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["get_symbol"](target="foo..bar"))

    def test_limit_out_of_range_raises(self, server_tools) -> None:
        from pydantic import ValidationError

        tools, _ = server_tools
        with pytest.raises(ValidationError):
            _arun(tools["search_codebase"](query="x", limit=0))


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
    fake_mcp_module.FastMCP = lambda name, **kwargs: fake_mcp

    with patch.dict(
        sys.modules,
        {
            "mcp": MagicMock(),
            "mcp.server": MagicMock(),
            "mcp.server.fastmcp": fake_mcp_module,
            "mcp.types": MagicMock(),
        },
    ):
        from pydocs_mcp.server import run

        run(db_path)

    out = _text(
        _arun(fake_mcp.tools["search_codebase"](query="login", kind="docs", package="Flask-Login"))
    )
    # The normalisation happens inside the handler; the search itself may
    # return "No matches" (no chunks seeded) but MUST NOT fail validation.
    assert "validation" not in out.lower()


# ── grep / glob / read_file (contract §3.7-3.9, ADR 0003) ─────────────────


def _stamp_project_root(db_path: Path, project_root: Path) -> None:
    """Stamp ``index_metadata.project_root`` so the filesystem tools resolve
    a real source tree (the seeded test dbs otherwise carry no root)."""
    from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata

    conn = open_index_database(db_path)
    write_index_metadata(
        conn,
        IndexMetadata(
            project_name="fsproj",
            project_root=str(project_root),
            embedding_provider="fastembed",
            embedding_model="BAAI/bge-small-en-v1.5",
            embedding_dim=384,
            pipeline_hash="h",
            indexed_at=1.0,
        ),
    )
    conn.commit()
    conn.close()


@pytest.fixture
def fs_server_tools(tmp_path: Path):
    """Server over a db whose stamped project_root is a real source tree —
    the filesystem tools' end-to-end fixture. The tree carries a floor-excluded
    ``.venv`` dir and a non-allowlisted extension to pin discovery-scope parity."""
    source = tmp_path / "src_tree"
    source.mkdir()
    (source / "app.py").write_text('def hello():\n    return "hi"\n')
    (source / "notes.md").write_text("# Notes\n\nhello there\n")
    (source / "data.json").write_text('{"hello": 1}\n')  # extension not allowlisted
    hidden = source / ".venv"
    hidden.mkdir()
    (hidden / "skip.py").write_text("hello = 1\n")  # floor-excluded dir
    db_path = tmp_path / "fsproj.db"
    _seed_basic_fixture(db_path)
    _stamp_project_root(db_path, source)
    return _run_server_capture_tools(db_path), source


class TestFilesystemTools:
    def test_grep_walks_the_discovery_scope(self, fs_server_tools) -> None:
        tools, _ = fs_server_tools
        result = _arun(tools["grep"](pattern="hello"))
        text = _text(result)
        assert "app.py" in text and "notes.md" in text
        # Discovery-scope parity (§4.1): floor-excluded dirs and
        # non-allowlisted extensions are NOT in the corpus.
        assert ".venv" not in text and "data.json" not in text
        items = result.structuredContent["items"]
        assert items and set(items[0]) == {"path", "start_line", "end_line", "text"}
        assert result.structuredContent["meta"]["tool"] == "grep"

    def test_grep_content_mode_via_dash_keyed_wire_call(self, fs_server_tools) -> None:
        """Wire-level §3.7 flags: a client sends ``-i`` (the literal parameter
        name); FastMCP's arg model validates the dash key and dispatches the
        handler by Python field name."""
        from mcp.server.fastmcp.utilities.func_metadata import func_metadata

        tools, _ = fs_server_tools
        fn = tools["grep"]
        parsed = func_metadata(fn).arg_model.model_validate(
            {"pattern": "HELLO", "-i": True, "output_mode": "content"}
        )
        result = _arun(fn(**parsed.model_dump_one_level()))
        assert "app.py:1:def hello():" in _text(result)

    def test_glob_orders_mtime_descending(self, fs_server_tools) -> None:
        import os

        tools, source = fs_server_tools
        os.utime(source / "app.py", (1_000, 1_000))
        os.utime(source / "notes.md", (2_000, 2_000))
        result = _arun(tools["glob"](pattern="*"))
        body_lines = _text(result).splitlines()
        assert body_lines.index("notes.md") < body_lines.index("app.py")
        items = result.structuredContent["items"]
        assert [i["path"] for i in items] == ["notes.md", "app.py"]
        assert set(items[0]) == {"path", "mtime"}

    def test_read_file_renders_cat_n_window(self, fs_server_tools) -> None:
        tools, _ = fs_server_tools
        result = _arun(tools["read_file"](file_path="app.py", offset=2, limit=1))
        text = _text(result)
        assert '     2\t    return "hi"' in text
        assert "def hello" not in text
        assert result.structuredContent["items"] == [
            {"path": "app.py", "start_line": 2, "end_line": 2}
        ]

    def test_read_file_outside_boundary_is_client_error(self, fs_server_tools, tmp_path) -> None:
        from pydocs_mcp.application import InvalidArgumentError

        tools, _ = fs_server_tools
        outside = tmp_path / "outside.txt"
        outside.write_text("secret\n")
        with pytest.raises(InvalidArgumentError, match="outside"):
            _arun(tools["read_file"](file_path=str(outside)))
