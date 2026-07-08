"""Tests for CLI entry point (__main__.py)."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pydocs_mcp.db import open_index_database, rebuild_fulltext_index


@pytest.fixture
def seeded_project(tmp_path):
    """Create a minimal project with source files and a pyproject.toml."""
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\ndependencies = []\n")
    (project / "app.py").write_text('def hello():\n    """Say hello."""\n    return "hi"\n')
    return project


@pytest.fixture(autouse=True)
def _patch_embedder_with_mock(monkeypatch):
    """Inject MockEmbedder + FakeLlmClient so CLI tests stay offline.

    The shipped default config selects ``provider=fastembed`` for
    embedding and ``provider=openai`` for the LLM. Both are required deps
    so the imports succeed in the test env — but constructing the real
    clients triggers a ~80MB ONNX download (fastembed) or hits the OpenAI
    network (openai). Patching both factories keeps unit tests fast and
    offline. (Production CLI runs the real clients.)
    """
    from tests._fakes import FakeLlmClient, MockEmbedder
    import pydocs_mcp.extraction as _extraction
    from pydocs_mcp.extraction import factories as _factories
    from pydocs_mcp.extraction.strategies import embedders as _embedders
    from pydocs_mcp.retrieval import factories as _retrieval_factories
    from pydocs_mcp.retrieval import llm_clients as _llm_clients

    # Patch the embedder factory so ``build_embedder(cfg)`` returns a mock
    # in the CLI startup path that Task 27 wires.
    monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: MockEmbedder())

    # Patch ``build_llm_client`` at every site where a consumer imports
    # it at module top (so the local binding inside that module is what
    # production code dereferences). The retrieval factory imports it
    # directly; ``storage.factories.build_project_indexer`` resolves it
    # lazily via ``from pydocs_mcp.retrieval.llm_clients import
    # build_llm_client`` — patching the canonical module attribute covers both.
    def _llm_with_mock(cfg):
        return FakeLlmClient(responses={})

    monkeypatch.setattr(_llm_clients, "build_llm_client", _llm_with_mock)
    monkeypatch.setattr(_retrieval_factories, "build_llm_client", _llm_with_mock)

    # Safety net for older callers / fixtures that still hand
    # ``build_ingestion_pipeline`` a bare config — auto-inject a mock when
    # no explicit embedder is threaded. Mirrors the post-Task-12 signature
    # (``uow_factory`` + ``pipeline_hash`` + ``llm_client`` kwargs) so the
    # CLI startup path threads the composite UoW + ingestion identity slot
    # + (future) ingestion LLM client into BuildContext.
    _orig = _factories.build_ingestion_pipeline

    def _build_with_mock(
        cfg,
        *,
        embedder=None,
        uow_factory=None,
        pipeline_hash="",
        llm_client=None,
    ):
        return _orig(
            cfg,
            embedder=embedder or MockEmbedder(),
            uow_factory=uow_factory,
            pipeline_hash=pipeline_hash,
            llm_client=llm_client or FakeLlmClient(responses={}),
        )

    # ``build_project_indexer`` does ``from pydocs_mcp.extraction import build_ingestion_pipeline``
    # at call time (deferred import) — patch both the re-exported attribute
    # on the package and the source attribute on factories, since the
    # deferred import resolves the former and direct callers use the latter.
    monkeypatch.setattr(_extraction, "build_ingestion_pipeline", _build_with_mock)
    monkeypatch.setattr(_factories, "build_ingestion_pipeline", _build_with_mock)


class TestMainNoArgs:
    def test_no_command_prints_help(self, capsys):
        with patch("sys.argv", ["pydocs-mcp"]):
            from pydocs_mcp.__main__ import main

            main()
        captured = capsys.readouterr()
        assert "pydocs-mcp" in captured.out or "usage" in captured.out.lower()


class TestIndexCommand:
    def test_index_creates_database(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main

            main()
        # Verify DB was created
        from pydocs_mcp.db import cache_path_for_project

        db_path = cache_path_for_project(seeded_project)
        assert db_path.exists()

    def test_index_with_force_flag(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--force"]):
            from pydocs_mcp.__main__ import main

            main()
        from pydocs_mcp.db import cache_path_for_project

        db_path = cache_path_for_project(seeded_project)
        assert db_path.exists()

    def test_force_reindex_works_without_tq_unlink_workaround(self, seeded_project):
        """AC-6: ``pydocs-mcp index --force`` succeeds without explicit .tq cleanup.

        The --force path should call ``IndexingService.clear_all`` which atomically
        wipes both SQLite and TurboQuant via the composite UoW; no out-of-band
        ``.tq`` file deletion is needed in the CLI. Smoke-test that a populated
        cache survives a force re-index with both .db and .tq still present.
        """
        from pydocs_mcp.db import (
            cache_path_for_project,
            turboquant_path_for_project,
        )

        # First index — populates both SQLite + the TurboQuant sidecar.
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main

            main()
        db_path = cache_path_for_project(seeded_project)
        tq_path = turboquant_path_for_project(seeded_project)
        assert db_path.exists()

        # --force re-index — must not error on the now-populated .tq. The old
        # ``tq_path.unlink()`` workaround has been removed; clear_all handles it.
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--force"]):
            main()
        assert db_path.exists()
        assert tq_path.exists()

    def test_index_skip_project(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--skip-project"]):
            from pydocs_mcp.__main__ import main

            main()
        from pydocs_mcp.db import cache_path_for_project

        db_path = cache_path_for_project(seeded_project)
        conn = open_index_database(db_path)
        pkg = conn.execute("SELECT * FROM packages WHERE name='__project__'").fetchone()
        conn.close()
        assert pkg is None

    def test_index_skip_deps_only_project_indexed(self, seeded_project):
        """--skip-deps: declared dependencies are never resolved nor indexed —
        the packages table holds only ``__project__``."""
        (seeded_project / "pyproject.toml").write_text('[project]\ndependencies = ["pyyaml"]\n')
        with patch(
            "sys.argv",
            ["pydocs-mcp", "index", str(seeded_project), "--skip-deps", "--no-inspect"],
        ):
            from pydocs_mcp.__main__ import main

            main()
        from pydocs_mcp.db import cache_path_for_project

        db_path = cache_path_for_project(seeded_project)
        conn = open_index_database(db_path)
        names = [r[0] for r in conn.execute("SELECT name FROM packages").fetchall()]
        conn.close()
        assert names == ["__project__"]

    def test_index_verbose(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "-v", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main

            main()

    def test_index_no_inspect(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--no-inspect"]):
            from pydocs_mcp.__main__ import main

            main()

    def test_index_with_depth_and_workers(self, seeded_project):
        with patch(
            "sys.argv",
            ["pydocs-mcp", "index", str(seeded_project), "--depth", "2", "--workers", "2"],
        ):
            from pydocs_mcp.__main__ import main

            main()


class TestSkipDepsWiring:
    """``--skip-deps`` must forward ``include_dependencies`` to ``ProjectIndexer``.

    The integration test above proves the end state (no dependency rows); this
    pair pins the CLI→application wiring in both directions so a regression
    that hardcodes either value is caught.
    """

    @pytest.fixture
    def captured_index_kwargs(self, monkeypatch):
        import pydocs_mcp.application as _application
        from pydocs_mcp.application.indexing_service import IndexingStats

        captured: dict[str, object] = {}

        class _CapturingIndexer:
            def __init__(self, **kwargs):
                pass

            async def index_project(self, project, **kwargs):
                captured.update(kwargs)
                return IndexingStats()

        # ``build_project_indexer`` imports ProjectIndexer lazily from the package,
        # so patching the package attribute intercepts the construction.
        monkeypatch.setattr(_application, "ProjectIndexer", _CapturingIndexer)
        return captured

    def test_skip_deps_forwards_include_dependencies_false(
        self, seeded_project, captured_index_kwargs
    ):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--skip-deps"]):
            from pydocs_mcp.__main__ import main

            main()
        assert captured_index_kwargs["include_dependencies"] is False

    def test_default_forwards_include_dependencies_true(
        self, seeded_project, captured_index_kwargs
    ):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main

            main()
        assert captured_index_kwargs["include_dependencies"] is True


class TestSearchCommand:
    """Sub-PR #6: `query` → `search --kind=docs`, `api` → `search --kind=api`."""

    def test_search_docs_runs_and_prints_results(self, seeded_project, capsys, monkeypatch):
        (seeded_project / "app.py").write_text(
            'def hello():\n    """Say hello to the world with a greeting message."""\n    return "hi"\n'
        )
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch("sys.argv", ["pydocs-mcp", "search", "hello", "--kind=docs"]):
            main()
        captured = capsys.readouterr()
        assert "hello" in captured.out.lower() or "─" in captured.out

    def test_search_docs_with_package_filter(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch(
            "sys.argv",
            ["pydocs-mcp", "search", "hello", "--kind=docs", "-p", "__project__"],
        ):
            main()

    def test_search_api_runs_and_prints_results(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch("sys.argv", ["pydocs-mcp", "search", "hello", "--kind=api"]):
            main()
        captured = capsys.readouterr()
        assert "hello" in captured.out.lower() or "─" in captured.out

    def test_search_api_with_package_filter(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch(
            "sys.argv",
            ["pydocs-mcp", "search", "hello", "--kind=api", "-p", "__project__"],
        ):
            main()

    def test_search_api_prints_symbol_details(self, seeded_project, capsys, monkeypatch):
        """Ensure --kind=api covers the symbol printing path."""
        (seeded_project / "app.py").write_text(
            'def greet(name: str) -> str:\n    """Greet a person by name."""\n    return f"Hello {name}"\n'
        )
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch("sys.argv", ["pydocs-mcp", "search", "greet", "--kind=api"]):
            main()
        captured = capsys.readouterr()
        assert "greet" in captured.out.lower() or "─" in captured.out


class TestNoRustFlag:
    def test_no_rust_forces_python_fallback(self, seeded_project, monkeypatch):
        """--no-rust must disable Rust and use Python fallback for indexing."""
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", ".", "--no-rust"]):
            from pydocs_mcp.__main__ import main

            main()
        import pydocs_mcp._fast as fast_mod

        assert fast_mod.RUST_AVAILABLE is False

    def test_no_rust_produces_same_output(self, seeded_project, monkeypatch):
        """Indexing with --no-rust must produce the same chunks as default."""
        monkeypatch.chdir(seeded_project)
        import sqlite3
        from pydocs_mcp.db import cache_path_for_project

        # Index with default engine
        with patch("sys.argv", ["pydocs-mcp", "index", ".", "--force"]):
            from pydocs_mcp.__main__ import main

            main()
        db = cache_path_for_project(seeded_project)
        conn = sqlite3.connect(str(db))
        default_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        default_headings = {r[0] for r in conn.execute("SELECT title FROM chunks").fetchall()}
        conn.close()

        # Index with --no-rust
        with patch("sys.argv", ["pydocs-mcp", "index", ".", "--force", "--no-rust"]):
            main()
        conn = sqlite3.connect(str(db))
        norust_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        norust_headings = {r[0] for r in conn.execute("SELECT title FROM chunks").fetchall()}
        conn.close()

        assert default_count == norust_count
        assert default_headings == norust_headings


class TestLookupCommand:
    """FIX 6: CLI ``lookup`` wires TreeService just like the MCP server, so
    multi-segment dotted targets resolve to persisted DocumentNode trees."""

    def test_cli_lookup_empty_target_lists_packages(
        self,
        seeded_project,
        capsys,
        monkeypatch,
    ):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch("sys.argv", ["pydocs-mcp", "lookup", ""]):
            main()
        captured = capsys.readouterr()
        # __project__ is always indexed for a fresh project.
        assert "__project__" in captured.out

    def test_cli_lookup_module_target_prints_tree(
        self,
        seeded_project,
        capsys,
        monkeypatch,
    ):
        """A multi-segment target like ``__project__.app`` should print the
        PageIndex JSON of the persisted DocumentNode tree — proves
        TreeService is wired in the CLI composition root."""
        import json

        monkeypatch.chdir(seeded_project)
        # Index first so the document_trees table has rows.
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()

        # __project__.app corresponds to the seeded_project/app.py source file.
        with patch("sys.argv", ["pydocs-mcp", "lookup", "__project__.app"]):
            main()
        captured = capsys.readouterr()
        # Either we get a real PageIndex tree, or the (empty) find_module
        # fallback raises NotFoundError. Both are valid; the bug we're
        # guarding against is the old ServiceUnavailableError that
        # ``tree_svc=None`` produced. So neither stdout nor stderr should
        # carry the "enable via sub-PR #5" string.
        combined = captured.out + captured.err
        assert "enable via sub-PR #5" not in combined
        # When we DO get JSON, it must parse and reference the module.
        stripped = captured.out.strip()
        if stripped.startswith("{"):
            payload = json.loads(stripped)
            assert payload.get("node_id") == "src.app" or "app" in payload.get(
                "node_id",
                "",
            )


def _seed_resolvable_symbol_db(db_path: Path) -> None:
    """Seed a cache DB with a symbol the CLI target parser can resolve.

    WHY a hand-seeded package (``mypkg``) instead of the ``seeded_project``
    fixture: project source is indexed under ``__project__`` with module ids
    that DON'T carry the package prefix (module ``app`` lives under package
    ``__project__``). ``LookupTarget.parse`` uses ``target.split('.')[0]`` as
    the package and re-joins the full dotted prefix as the module-id probe, so
    a ``__project__`` symbol is unreachable through the target string. A
    dependency-style package whose NAME equals its module prefix
    (``mypkg`` / ``mypkg.core``) is the addressable shape — the same shape the
    service-level ref tests use (``pkg.helpers.compute``). Seeding the tree +
    a resolved CALLS edge gives ``refs``/``context`` a symbol that resolves
    end-to-end through the real router.
    """
    import asyncio
    import sqlite3

    from pydocs_mcp.extraction.model import DocumentNode, NodeKind
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.storage.sqlite.document_tree_store import SqliteDocumentTreeStore

    def _node(node_id, kind, title, start, end, text="", children=()):
        return DocumentNode(
            node_id=node_id,
            qualified_name=node_id,
            title=title,
            kind=kind,
            source_path="mypkg/core.py",
            start_line=start,
            end_line=end,
            text=text,
            content_hash="h_" + node_id,
            summary=title,
            children=children,
        )

    open_index_database(db_path).close()
    greet = _node(
        "mypkg.core.greet",
        NodeKind.FUNCTION,
        "def greet()",
        1,
        3,
        "def greet():\n    return farewell()\n",
    )
    farewell = _node(
        "mypkg.core.farewell",
        NodeKind.FUNCTION,
        "def farewell()",
        5,
        7,
        "def farewell():\n    return 'bye'\n",
    )
    module = _node("mypkg.core", NodeKind.MODULE, "core", 1, 7, children=(greet, farewell))
    asyncio.run(
        SqliteDocumentTreeStore(provider=PerCallConnectionProvider(db_path)).save_many(
            [module], package="mypkg"
        )
    )
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO packages (name, version, summary, homepage, dependencies, "
        "content_hash, origin) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("mypkg", "1.0", "My package", "", "[]", "pkg_hash", "dependency"),
    )
    conn.executemany(
        "INSERT INTO node_references (from_package, from_node_id, to_name, to_node_id, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            # greet -> farewell : a resolved forward edge, powers `context`.
            ("mypkg", "mypkg.core.greet", "farewell", "mypkg.core.farewell", "calls"),
            # cli.main -> greet : a resolved reverse edge, powers `callers`.
            ("mypkg", "mypkg.cli.main", "greet", "mypkg.core.greet", "calls"),
        ],
    )
    conn.commit()
    conn.close()


class TestTaskShapedSubcommands:
    """Task 9: six task-shaped subcommands mirror the six MCP tools 1:1.

    ``overview`` / ``symbol`` / ``context`` / ``refs`` / ``why`` join the
    existing ``search`` (each routing through the ToolRouter + shared
    ResponseEnvelope), and the old ``lookup`` stays for one release as a
    deprecated alias that warns + delegates to the new router calls.
    """

    @pytest.fixture
    def symbol_project(self, tmp_path):
        """A project dir whose cache DB holds a resolvable ``mypkg.core.*`` symbol."""
        from pydocs_mcp.db import cache_path_for_project

        project = tmp_path / "symproject"
        project.mkdir()
        db_path = cache_path_for_project(project)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _seed_resolvable_symbol_db(db_path)
        return project

    def test_symbol_subcommand_prints_enveloped_output(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        # ``__project__`` is a single-segment package target → get_symbol renders
        # the package overview (no deep module-id probe needed) inside the envelope.
        with patch("sys.argv", ["pydocs-mcp", "symbol", "__project__", "--project-dir", "."]):
            main()
        out = capsys.readouterr().out
        assert out.startswith("[index:")

    def test_refs_subcommand_direction_flag(self, symbol_project, capsys):
        from pydocs_mcp.__main__ import main

        with patch(
            "sys.argv",
            [
                "pydocs-mcp",
                "refs",
                "mypkg.core.greet",
                "--direction",
                "callers",
                "--project-dir",
                str(symbol_project),
            ],
        ):
            main()
        assert "Callers of" in capsys.readouterr().out

    def test_context_accepts_multiple_targets(self, symbol_project, capsys):
        from pydocs_mcp.__main__ import main

        with patch(
            "sys.argv",
            [
                "pydocs-mcp",
                "context",
                "mypkg.core.greet",
                "mypkg.core.farewell",
                "--project-dir",
                str(symbol_project),
            ],
        ):
            main()
        assert capsys.readouterr().out.count("# Context for") == 2

    def test_overview_subcommand(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch("sys.argv", ["pydocs-mcp", "overview", "--project-dir", "."]):
            main()
        assert "# Overview" in capsys.readouterr().out

    def test_overview_workspace_lists_projects_with_cli_pointers(self, tmp_path, capsys):
        """``overview --workspace`` over >1 stamped dbs renders the workspace
        card end-to-end, with CLI-surface deepening pointers
        (``→ pydocs-mcp overview --project NAME``) — proving the CLI composition
        root selects surface="cli" for the multi-repo empty-selector card."""
        from pydocs_mcp.retrieval.config import AppConfig
        from pydocs_mcp.storage.index_metadata import IndexMetadata, write_index_metadata

        cfg = AppConfig.load()
        ws = tmp_path / "ws"
        ws.mkdir()
        for name, slug in (("frontend", "aaaaaaaaaa"), ("backend", "bbbbbbbbbb")):
            conn = open_index_database(ws / f"{name}_{slug}.db")
            write_index_metadata(
                conn,
                IndexMetadata(
                    project_name=name,
                    project_root=f"/src/{name}",
                    embedding_provider="fastembed",
                    embedding_model=cfg.embedding.model_name,
                    embedding_dim=cfg.embedding.dim,
                    pipeline_hash="h",
                    indexed_at=1.0,
                ),
            )
            conn.close()
        with patch("sys.argv", ["pydocs-mcp", "overview", "--workspace", str(ws)]):
            from pydocs_mcp.__main__ import main

            main()
        out = capsys.readouterr().out
        assert "# Workspace overview" in out
        assert "→ pydocs-mcp overview --project frontend" in out
        assert "→ pydocs-mcp overview --project backend" in out

    def test_why_succeeds_with_capture_enabled(self, seeded_project, capsys, monkeypatch):
        """Default config (``decision_capture.enabled=True``) wires the real
        ``DecisionService`` — ``why <query>`` runs the semantic search and, with
        no mined decisions in this fixture, renders the empty-state on stdout
        (exit 0), NOT the Null impl's unavailable error."""
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch("sys.argv", ["pydocs-mcp", "why", "anything", "--project-dir", "."]):
            rc = main()
        assert rc == 0
        out = capsys.readouterr().out
        # Real service empty-state (no decisions mined for this minimal fixture).
        assert "No decisions found." in out

    def test_why_reports_unavailable_when_capture_disabled(
        self, seeded_project, capsys, monkeypatch
    ):
        """A ``--config`` overlay with ``decision_capture.enabled=false`` keeps the
        ``NullDecisionService`` wired — ``why`` raises and the error points the
        user at the YAML knob (exit != 0)."""
        monkeypatch.chdir(seeded_project)
        overlay = seeded_project / "no-capture.yaml"
        overlay.write_text("decision_capture:\n  enabled: false\n")
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        with patch(
            "sys.argv",
            ["pydocs-mcp", "--config", str(overlay), "why", "anything", "--project-dir", "."],
        ):
            rc = main()
        assert rc != 0
        # NullDecisionService points the user at the YAML knob `decision_capture`.
        assert "decision_capture" in capsys.readouterr().err

    def test_lookup_alias_warns_and_delegates(self, seeded_project, capsys, monkeypatch):
        monkeypatch.chdir(seeded_project)
        with patch("sys.argv", ["pydocs-mcp", "index", "."]):
            from pydocs_mcp.__main__ import main

            main()
        # Empty target on the deprecated alias preserves the old "list packages"
        # behavior (routes to get_overview), so the enveloped listing prints while
        # the deprecation notice + the new-tool pointer land on stderr.
        with patch("sys.argv", ["pydocs-mcp", "lookup", "", "--project-dir", "."]):
            main()
        captured = capsys.readouterr()
        assert "deprecated" in captured.err and "pydocs-mcp symbol" in captured.err
        assert captured.out.startswith("[index:")


class TestServeCommand:
    def test_serve_indexes_then_starts_server(self, seeded_project):
        """Test that serve indexes and calls run() — we mock run() to avoid blocking.

        The handler defers the ``pydocs_mcp.server`` import to its call
        path, so patching happens at the source module rather than the
        pre-refactor ``pydocs_mcp.__main__.run`` attribute.

        EmbedChunksStage's MockEmbedder is wired via the autouse
        ``_patch_embedder_with_mock`` fixture.
        """
        with patch("pydocs_mcp.server.run") as mock_run:
            with patch("sys.argv", ["pydocs-mcp", "serve", str(seeded_project)]):
                from pydocs_mcp.__main__ import main

                main()
            mock_run.assert_called_once()


class TestRunIndexingDelegation:
    """``_run_indexing`` is a thin wrapper: composition lives in
    ``storage.factories.build_project_indexer`` and the pass sequence in
    ``application.run_index_pass``; the CLI only resolves paths + flags."""

    @pytest.fixture
    def delegation_capture(self, monkeypatch):
        from types import SimpleNamespace

        import pydocs_mcp.application as _application
        import pydocs_mcp.storage.factories as _storage_factories
        from pydocs_mcp.application.indexing_service import IndexingStats

        captured: dict[str, object] = {}
        sentinel_bundle = SimpleNamespace(
            orchestrator=object(),
            indexing_service=object(),
            uow_factory=lambda: None,
            pipeline_hash="hash-sentinel",
            check_integrity=object(),
            rebuild_fts=object(),
            stamp_metadata=object(),
            write_aggregates=object(),
        )

        def _fake_build(config, db_path, *, use_inspect, inspect_depth):
            captured["config"] = config
            captured["factory"] = (db_path, use_inspect, inspect_depth)
            return sentinel_bundle

        async def _fake_pass(**kwargs):
            captured["pass"] = kwargs
            return IndexingStats()

        # ``_run_indexing`` resolves both lazily at call time, so patching
        # the module / package attributes intercepts the delegation.
        monkeypatch.setattr(_storage_factories, "build_project_indexer", _fake_build)
        monkeypatch.setattr(_application, "run_index_pass", _fake_pass)
        return captured, sentinel_bundle

    def test_delegates_flags_and_bundle_members(self, seeded_project, delegation_capture):
        captured, sentinel_bundle = delegation_capture
        with patch(
            "sys.argv",
            ["pydocs-mcp", "index", str(seeded_project), "--depth", "3", "--skip-deps"],
        ):
            from pydocs_mcp.__main__ import main

            assert main() == 0

        from pydocs_mcp.retrieval.config import AppConfig

        assert isinstance(captured["config"], AppConfig)
        _db_path, use_inspect, inspect_depth = captured["factory"]
        assert use_inspect is True
        assert inspect_depth == 3  # CLI --depth wins over YAML
        kwargs = captured["pass"]
        assert kwargs["orchestrator"] is sentinel_bundle.orchestrator
        assert kwargs["indexing_service"] is sentinel_bundle.indexing_service
        assert kwargs["pipeline_hash"] == "hash-sentinel"
        assert kwargs["check_integrity"] is sentinel_bundle.check_integrity
        assert kwargs["rebuild_fts"] is sentinel_bundle.rebuild_fts
        assert kwargs["stamp_metadata"] is sentinel_bundle.stamp_metadata
        assert kwargs["write_aggregates"] is sentinel_bundle.write_aggregates
        assert kwargs["project"] == seeded_project.resolve()
        assert kwargs["force"] is False
        assert kwargs["include_project_source"] is True
        assert kwargs["include_dependencies"] is False
        assert kwargs["workers"] == 4

    def test_depth_absent_falls_back_to_yaml(self, seeded_project, delegation_capture):
        captured, _bundle = delegation_capture
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main

            assert main() == 0

        from pydocs_mcp.retrieval.config import AppConfig

        _db_path, _use_inspect, inspect_depth = captured["factory"]
        assert inspect_depth == AppConfig.load().extraction.members.inspect_depth

    def test_no_inspect_flag_forwarded(self, seeded_project, delegation_capture):
        captured, _bundle = delegation_capture
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--no-inspect"]):
            from pydocs_mcp.__main__ import main

            assert main() == 0

        _db_path, use_inspect, _depth = captured["factory"]
        assert use_inspect is False
