"""E2E: selective dependency embedding through the real stages + stores.

Runs a dependency's chunks through the REAL hash + embed stages (MockEmbedder)
and persists via IndexingService over real SQLite + TurboQuant, asserting the
whole contract: code chunks indexed-but-vectorless, doc pages embedded, the
integrity check stable across reopens (no re-extract loop), promotion via
full_index_dependencies re-embeds everything, and dense search over the
partial .tq neither errors nor leaks vectorless ids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.embed_policy import EmbedPolicy
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.assign_chunk_content_hash import (
    AssignChunkContentHashStage,
)
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
    check_integrity_and_repair,
)
from tests._fakes import MockEmbedder

_DIM = 8
_BW = 4


def _dep_pkg() -> Package:
    return Package(
        name="somedep",
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="pkg-hash",
        origin=PackageOrigin.DEPENDENCY,
    )


def _dep_chunks() -> tuple[Chunk, ...]:
    return (
        Chunk(
            text="def frobnicate(x):\n    return x * 2",
            metadata={
                "package": "somedep",
                "module": "somedep.core",
                "title": "def frobnicate()",
                "origin": "python_def",
            },
        ),
        Chunk(
            text='Frobnication utilities.\n\ndef frobnicate(x):\n    """Doubles the input."""',
            metadata={
                "package": "somedep",
                "module": "somedep.core",
                "title": "somedep.core documentation",
                "origin": "dependency_module_doc",
            },
        ),
    )


async def _run_stages(policy: EmbedPolicy) -> tuple[Chunk, ...]:
    """Real hash + embed stages over a dependency-kind state."""
    state = IngestionState(
        files=FileBundle(
            target="somedep",
            target_kind=TargetKind.DEPENDENCY,
            package_name="somedep",
            root=Path("/site"),
        ),
        chunks=ChunkBundle(chunks=_dep_chunks()),
        package=_dep_pkg(),
    )
    state = await AssignChunkContentHashStage(pipeline_hash="P", embed_policy=policy).run(state)
    state = await EmbedChunksStage(embedder=MockEmbedder(dim=_DIM), embed_policy=policy).run(state)
    return state.chunks.chunks


@pytest.mark.asyncio
async def test_doc_pages_policy_end_to_end(tmp_path: Path) -> None:
    db, tq = tmp_path / "x.db", tmp_path / "x.tq"
    open_index_database(db).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW
    )
    svc = IndexingService(uow_factory=factory)

    chunks = await _run_stages(EmbedPolicy())  # default: doc_pages
    await svc.reindex_package(_dep_pkg(), chunks=chunks, module_members=(), trees=())

    conn = open_index_database(db)
    rows = {
        r["title"]: r["embedded"]
        for r in conn.execute("SELECT title, embedded FROM chunks").fetchall()
    }
    # Code chunk persisted (BM25-reachable) but NOT embedded; doc page embedded.
    assert rows == {"def frobnicate()": 0, "somedep.core documentation": 1}
    # FTS reaches the vectorless code chunk by keyword (external-content
    # FTS5 needs the rebuild the CLI runs after indexing).
    conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    hit = conn.execute(
        "SELECT title FROM chunks_fts WHERE chunks_fts MATCH 'frobnicate'"
    ).fetchall()
    assert any("frobnicate" in r["title"] for r in hit)
    conn.close()

    # Integrity: 1 embedded flag == 1 vector -> steady state across reopens.
    for _ in range(2):
        assert (
            await check_integrity_and_repair(db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW) == []
        )
    conn = open_index_database(db)
    assert conn.execute("SELECT content_hash FROM packages").fetchone()[0] == "pkg-hash"
    conn.close()


@pytest.mark.asyncio
async def test_promotion_reembeds_only_after_tier_flip(tmp_path: Path) -> None:
    db, tq = tmp_path / "x.db", tmp_path / "x.tq"
    open_index_database(db).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW
    )
    svc = IndexingService(uow_factory=factory)

    # 1st index under doc_pages, then PROMOTE somedep and reindex.
    await svc.reindex_package(
        _dep_pkg(), chunks=await _run_stages(EmbedPolicy()), module_members=(), trees=()
    )
    promoted = EmbedPolicy(full_index_dependencies=("somedep",))
    await svc.reindex_package(
        _dep_pkg(), chunks=await _run_stages(promoted), module_members=(), trees=()
    )

    conn = open_index_database(db)
    rows = conn.execute("SELECT embedded FROM chunks").fetchall()
    assert [r["embedded"] for r in rows] == [1, 1]  # everything embedded now
    conn.close()
    # tier flip changed the hashes -> old rows dropped + vectors replaced; consistent.
    assert await check_integrity_and_repair(db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW) == []


@pytest.mark.asyncio
async def test_dense_search_over_partial_tq_returns_embedded_subset(tmp_path: Path) -> None:
    """The pre-filter allowlist includes vectorless dep-code ids — the ANN
    search must return just the embedded subset, never raise."""
    from pydocs_mcp.storage.factories import (
        build_sqlite_candidate_id_resolver,
        build_sqlite_chunk_hydrator,
    )
    from pydocs_mcp.storage.search_backend import _TurboQuantReadStore

    db, tq = tmp_path / "x.db", tmp_path / "x.tq"
    open_index_database(db).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW
    )
    svc = IndexingService(uow_factory=factory)
    await svc.reindex_package(
        _dep_pkg(), chunks=await _run_stages(EmbedPolicy()), module_members=(), trees=()
    )

    store = _TurboQuantReadStore(
        tq_path=tq,
        dim=_DIM,
        bit_width=_BW,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db),
        chunk_hydrator=build_sqlite_chunk_hydrator(db),
    )
    from pydocs_mcp.storage.filters import FieldEq

    query_vec = await MockEmbedder(dim=_DIM).embed_query("frobnication docs")
    # Filter matches the whole package -> allowlist holds BOTH ids (one vectorless).
    hits = await store.vector_search(
        query_vec, limit=10, filter=FieldEq(field="package", value="somedep")
    )
    titles = [h.metadata.get("title") for h in hits]
    assert titles == ["somedep.core documentation"]  # embedded subset only, no error
