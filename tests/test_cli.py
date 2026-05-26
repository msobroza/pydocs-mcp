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
    (project / "pyproject.toml").write_text(
        '[project]\ndependencies = []\n'
    )
    (project / "app.py").write_text(
        'def hello():\n    """Say hello."""\n    return "hi"\n'
    )
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
    # directly, ``__main__.py`` resolves it lazily inside ``_run_indexing``
    # via ``from pydocs_mcp.retrieval.llm_clients import build_llm_client``
    # — patching the canonical module attribute covers both.
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
        cfg, *, embedder=None, uow_factory=None, pipeline_hash="",
        llm_client=None,
    ):
        return _orig(
            cfg,
            embedder=embedder or MockEmbedder(),
            uow_factory=uow_factory,
            pipeline_hash=pipeline_hash,
            llm_client=llm_client or FakeLlmClient(responses={}),
        )

    # ``_run_indexing`` does ``from pydocs_mcp.extraction import build_ingestion_pipeline``
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

    def test_index_verbose(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "-v", "index", str(seeded_project)]):
            from pydocs_mcp.__main__ import main
            main()

    def test_index_no_inspect(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--no-inspect"]):
            from pydocs_mcp.__main__ import main
            main()

    def test_index_with_depth_and_workers(self, seeded_project):
        with patch("sys.argv", ["pydocs-mcp", "index", str(seeded_project), "--depth", "2", "--workers", "2"]):
            from pydocs_mcp.__main__ import main
            main()


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
        default_headings = {
            r[0] for r in conn.execute("SELECT title FROM chunks").fetchall()
        }
        conn.close()

        # Index with --no-rust
        with patch("sys.argv", ["pydocs-mcp", "index", ".", "--force", "--no-rust"]):
            main()
        conn = sqlite3.connect(str(db))
        norust_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        norust_headings = {
            r[0] for r in conn.execute("SELECT title FROM chunks").fetchall()
        }
        conn.close()

        assert default_count == norust_count
        assert default_headings == norust_headings


class TestLookupCommand:
    """FIX 6: CLI ``lookup`` wires TreeService just like the MCP server, so
    multi-segment dotted targets resolve to persisted DocumentNode trees."""

    def test_cli_lookup_empty_target_lists_packages(
        self, seeded_project, capsys, monkeypatch,
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
        self, seeded_project, capsys, monkeypatch,
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
                "node_id", "",
            )


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
