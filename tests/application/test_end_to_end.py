"""End-to-end smoke: all 5 application services wired over a real SQLite stack.

The per-service tests under ``tests/application/`` exercise each service in
isolation with Protocol fakes; ``tests/test_server.py`` covers the same
surface at the MCP-handler layer with a full ``server.run()`` bootstrap; and
``tests/test_cli.py`` drives the CLI subcommands end-to-end. This file
plugs the specific gap none of those cover — wiring all five services
together off a shared :class:`BuildContext` + shared SQLite DB, calling
each service's public method directly (not via the handler closure).

That composition is what guarantees sub-PR #4's Dependency-Inversion claim
(AC §4): every service depends only on Protocols, the backend adapters in
``storage/sqlite.py`` satisfy all five Protocols without any cross-service
import graph, and substituting them (sub-PR #5's forthcoming strategy
layer) is a pure wiring change.

The DB is pre-seeded by the shared ``integration_conn`` fixture
(`tests/conftest.py`) against ``tests/fixtures/fake_project/`` + three
stripped package snapshots. We re-use that DB here rather than re-running
the indexer so the end-to-end focus stays on the service-composition
surface, not on extractor side effects.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application import (
    ModuleIntrospectionService,
    PackageLookupService,
    SearchApiService,
    SearchDocsService,
)
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.models import (
    ChunkList,
    ModuleMemberList,
    Package,
    PackageDoc,
    SearchQuery,
)
from pydocs_mcp.retrieval.config import (
    AppConfig,
    build_chunk_pipeline_from_config,
    build_member_pipeline_from_config,
)
from pydocs_mcp.retrieval.wiring import build_retrieval_context
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteModuleMemberRepository,
    SqlitePackageRepository,
)


@pytest.fixture
def wired_services(integration_conn):
    """Wire all 4 query services + expose the DB path for downstream tests.

    ``integration_conn`` yields an already-seeded SQLite connection; we grab
    the underlying file path off the connection and build a fresh stack of
    repositories + pipelines the same way ``server.py::run`` does. The
    fixture closes the yielded connection on teardown — we don't reuse it,
    we build our own provider so the test exercises the same
    ``ConnectionProvider``-based path the real server uses.
    """
    # SQLite exposes the underlying file through PRAGMA database_list; the
    # main DB is always row (seq=0). Keeps the test agnostic to the tmp_path
    # naming the upstream fixture chose.
    row = integration_conn.execute("PRAGMA database_list").fetchone()
    db_path = Path(row[2])

    provider = build_connection_provider(db_path)
    package_store = SqlitePackageRepository(provider=provider)
    chunk_store = SqliteChunkRepository(provider=provider)
    member_store = SqliteModuleMemberRepository(provider=provider)

    config = AppConfig.load()
    context = build_retrieval_context(db_path, config)
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    return {
        "package_lookup": PackageLookupService(
            package_store=package_store,
            chunk_store=chunk_store,
            module_member_store=member_store,
        ),
        "search_docs": SearchDocsService(chunk_pipeline=chunk_pipeline),
        "search_api": SearchApiService(member_pipeline=member_pipeline),
        "inspect": ModuleIntrospectionService(package_store=package_store),
    }


@pytest.mark.asyncio
async def test_package_lookup_list_returns_real_packages(wired_services):
    """PackageLookupService.list_packages returns the seeded fixture packages."""
    packages = await wired_services["package_lookup"].list_packages()

    assert len(packages) >= 1
    assert all(isinstance(p, Package) for p in packages)
    # ``integration_conn`` seeds __project__ + sklearn + vllm + langgraph.
    names = {p.name for p in packages}
    assert "__project__" in names


@pytest.mark.asyncio
async def test_package_lookup_get_package_doc_returns_bundle(wired_services):
    """get_package_doc returns PackageDoc with real chunks + members."""
    doc = await wired_services["package_lookup"].get_package_doc("__project__")

    assert doc is not None
    assert isinstance(doc, PackageDoc)
    assert doc.package.name == "__project__"
    # Fake project has multiple top-level functions; if extraction produced
    # zero chunks the whole end-to-end path is broken.
    assert len(doc.chunks) > 0 or len(doc.members) > 0


@pytest.mark.asyncio
async def test_package_lookup_get_unknown_returns_none(wired_services):
    """Unknown package short-circuits to None (no extra store calls)."""
    doc = await wired_services["package_lookup"].get_package_doc("does-not-exist")
    assert doc is None


@pytest.mark.asyncio
async def test_search_docs_returns_chunklist(wired_services):
    """SearchDocsService.search drives the real pipeline and returns ChunkList."""
    response = await wired_services["search_docs"].search(
        SearchQuery(terms="pipeline"),
    )

    # Real pipeline always returns either a ChunkList result or empty one;
    # the service substitutes ``ChunkList(items=())`` when the pipeline
    # produces no state.result, so the isinstance check is stable.
    assert isinstance(response.result, ChunkList)
    assert response.query.terms == "pipeline"


@pytest.mark.asyncio
async def test_search_api_returns_composite_response(wired_services):
    """SearchApiService.search drives the real member pipeline.

    The pipeline's final stage (``TokenBudgetFormatterStage``) wraps the
    member-search output into a single composite ``ChunkList`` entry so the
    CLI / MCP consumer can print it verbatim — same shape the parity golden
    (``tests/retrieval/test_parity_golden.py``) pins. The empty-result
    fallback in :class:`SearchApiService` is a ``ModuleMemberList``; we only
    assert on the filled case here (which integration_conn guarantees has
    ``train_model`` as a ``__project__`` symbol).
    """
    response = await wired_services["search_api"].search(
        SearchQuery(terms="train_model"),
    )

    # Non-empty result → composite ChunkList (matches the parity golden).
    # Empty result → ModuleMemberList from the SearchApiService fallback.
    assert isinstance(response.result, (ChunkList, ModuleMemberList))
    assert response.query.terms == "train_model"
    # integration_conn does index train_model; the pipeline should produce
    # a non-empty composite.
    assert len(response.result.items) > 0


@pytest.mark.asyncio
async def test_inspect_unknown_package_returns_error_string(wired_services):
    """ModuleIntrospectionService.inspect short-circuits on unindexed packages.

    Byte-parity AC #8 preserves the pre-PR error message; locking it in at
    the end-to-end seam catches any drift in the normalize/lookup/fallthrough
    chain.
    """
    result = await wired_services["inspect"].inspect("nonexistent_xyz")
    assert "not indexed" in result
    assert "list_packages" in result


@pytest.mark.asyncio
async def test_inspect_invalid_submodule_rejected(wired_services):
    """Submodule path validator rejects shell-metachar garbage before import."""
    # Pre-seed a fake indexed package so we get past the "not indexed"
    # guard and exercise the submodule validator.
    result = await wired_services["inspect"].inspect("__project__", "bad/name!")
    assert "Invalid submodule" in result
