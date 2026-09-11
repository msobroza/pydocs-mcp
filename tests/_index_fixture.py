"""Shared test helper: index a real project directory into a real SQLite bundle.

Drives the SAME write-side composition root the ``pydocs-mcp index`` command
uses — ``build_project_indexer`` (wiring) plus ``run_index_pass`` (integrity
sweep → index → FTS rebuild → metadata stamp) — so any test that needs a
genuine index (trees, module members, reference edges, mined decisions, node
scores, a stamped identity) gets one without re-deriving the wiring or
monkeypatching ``sys.argv``.

Offline by construction: ``tests/conftest.py``'s autouse fixtures already swap
``build_embedder`` / ``build_llm_client`` for fakes, and ``.db`` / ``.tq``
bundles land under the per-test ``PYDOCS_CACHE_DIR`` sandbox.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.application.index_project import run_index_pass
from pydocs_mcp.application.indexing_service import IndexingStats
from pydocs_mcp.application.mcp_inputs import configure_from_app_config
from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.factories import IndexerBundle, build_project_indexer


def index_project_to_db(
    project_dir: Path,
    db_path: Path,
    *,
    config: AppConfig | None = None,
    include_dependencies: bool = False,
) -> Path:
    """Index ``project_dir`` into ``db_path`` and return the database path.

    ``include_dependencies`` defaults to False: a tmp fixture project declares
    no dependencies worth indexing, and resolving site-packages would make the
    fixture slow and machine-dependent. ``use_inspect=False`` (static mode) for
    the same reason — importing fixture modules must never run their code.

    Example::

        db = index_project_to_db(tmp_path / "proj", tmp_path / "proj.db")
    """
    resolved = config if config is not None else AppConfig.load()
    # Same startup call the CLI makes before indexing: it pushes the YAML
    # reference-capture settings into the slot ``ReferenceCaptureStage`` reads,
    # so the fixture captures the same edges a production index would.
    configure_from_app_config(resolved)
    open_index_database(db_path).close()
    bundle = build_project_indexer(resolved, db_path, use_inspect=False, inspect_depth=None)
    asyncio.run(_run_one_index_pass(bundle, resolved, project_dir, include_dependencies))
    return db_path


def run_pass_with_embedder(
    config: AppConfig,
    db_path: Path,
    project_dir: Path,
    *,
    embedder: object,
) -> IndexingStats:
    """One index pass over an EXISTING bundle, driven by a chosen embedder.

    Separate from :func:`index_project_to_db` because the suites that pin cache
    behavior run several passes against the same database and read the returned
    stats (``project_indexed``) to tell a re-extraction from a cache hit. The
    bundle is rebuilt per call, exactly as a fresh ``pydocs-mcp index`` process
    would, so nothing in-memory can carry state between passes and mask a hash
    that fails to settle.

    ``embedder`` replaces ``build_embedder`` for the duration of the pass — a
    counting fake, normally — which is how a suite proves that a re-extraction
    did or did not re-embed.

    Example::

        stats = run_pass_with_embedder(config, db, project, embedder=counting)
        assert stats.project_indexed is False   # a cache hit
    """
    # Imported here, not at module scope: patching the embedders module by name
    # is what the monkeypatch below targets, and the caller may have already
    # imported it.
    from pydocs_mcp.extraction.strategies import embedders as _embedders

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(_embedders, "build_embedder", lambda cfg: embedder)
        bundle = build_project_indexer(config, db_path, use_inspect=False, inspect_depth=None)
        return asyncio.run(_run_one_index_pass(bundle, config, project_dir, False))
    finally:
        monkeypatch.undo()


async def _run_one_index_pass(
    bundle: IndexerBundle,
    config: AppConfig,
    project_dir: Path,
    include_dependencies: bool,
) -> IndexingStats:
    """One full ``run_index_pass`` with the CLI's own argument mapping."""
    return await run_index_pass(
        orchestrator=bundle.orchestrator,
        indexing_service=bundle.indexing_service,
        pipeline_hash=bundle.pipeline_hash,
        project=project_dir,
        embedding_provider=config.embedding.provider,
        embedding_model=config.embedding.model_name,
        embedding_dim=config.embedding.dim,
        force=False,
        include_project_source=True,
        include_dependencies=include_dependencies,
        workers=1,
        check_integrity=bundle.check_integrity,
        rebuild_fts=bundle.rebuild_fts,
        stamp_metadata=bundle.stamp_metadata,
        write_aggregates=bundle.write_aggregates,
        # run_index_pass grew these two keyword-only handles with the per-bundle
        # grammar stamp (index_metadata.loadable_grammars); pass the bundle's own,
        # exactly as the composition root does in __main__.py.
        read_prior_state=bundle.read_prior_state,
        grammar_fingerprint=bundle.grammar_fingerprint,
    )
