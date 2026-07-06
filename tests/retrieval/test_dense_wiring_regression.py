"""#64 regression: dense indexing + retrieval through the converged path
produce real dense hits, not a silent BM25 fallback.

This is the keystone end-to-end guard for the unified SearchBackend seam.
On a pre-fix tree (``build_retrieval_context`` sourcing the dense leg from
the FTS-only ``SqliteLexicalStore`` instead of ``backend.dense()``) the read
half returns zero hits / the wrong ``retriever_name`` and this test fails.

Both halves of the contract run through the SAME ``SearchBackend`` so the
write path that persists the ``.tq`` sidecar and the read path that mmaps it
cannot drift:

1. Write half — index chunks carrying ``np.ndarray`` embeddings through
   ``IndexingService`` using the backend's composite ``uow_factory``; assert
   the ``.tq`` sidecar exists and is non-empty.
2. Read half — ``build_retrieval_context(...).vector_store.vector_search(...)``
   returns real dense hits whose ``retriever_name == "turboquant_dense"``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.storage.factories import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend


def _pkg(name: str = "demo") -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _vec(dim: int, *values: float) -> np.ndarray:
    """Build a ``dim``-wide float32 vector from a leading prefix of values.

    Trailing slots are zero-filled so the caller only spells out the
    discriminating leading components. Sizing to ``dim`` (384 by default)
    matters: TurboQuant's ``IdMapIndex`` is dimensioned by the embedder's
    ``dim`` and a mismatch panics, so the synthetic vectors must match.
    """
    padded = list(values) + [0.0] * max(0, dim - len(values))
    return np.asarray(padded[:dim], dtype=np.float32)


def _chunk(pkg: str, title: str, text: str, vec: np.ndarray) -> Chunk:
    # ``content_hash`` is auto-derived in ``Chunk.__post_init__`` from
    # (package, module, title, text). Distinct title+text per chunk →
    # distinct hashes → ``_maybe_write_vectors`` maps each embedding to its
    # own persisted row, so BOTH vectors land in the ``.tq`` sidecar (a
    # shared hash would collapse them to one).
    return Chunk(
        text=text,
        embedding=vec,
        metadata={"package": pkg, "title": title},
    )


@pytest.mark.asyncio
async def test_dense_indexing_then_retrieval_hits_tq(tmp_path: Path) -> None:
    """Index dense vectors through the backend's composite UoW, then prove
    retrieval returns real TurboQuant hits (not a silent BM25 fallback)."""
    db_path = tmp_path / "index.sqlite"
    open_index_database(db_path).close()

    cfg = AppConfig.load()
    dim = cfg.embedding.dim  # 384 by default — size synthetic vectors to match.

    backend = build_search_backend(cfg, db_path)
    uow_factory = build_composite_uow_factory(backend.write_uow_children())

    # Two chunks with distinct dim-sized float32 embeddings. The vectors
    # differ in their leading components so a query equal to chunk[0]'s
    # embedding ranks chunk[0]'s row first under MaxSim / cosine.
    chunks = (
        _chunk("demo", "alpha", "alpha body", _vec(dim, 1.0, 0.0, 0.0, 0.0)),
        _chunk("demo", "beta", "beta body", _vec(dim, 0.0, 1.0, 0.0, 0.0)),
    )
    package = _pkg("demo")

    svc = IndexingService(uow_factory=uow_factory)
    await svc.reindex_package(package, chunks, module_members=())

    # Write half: the ``.tq`` sidecar exists AND was actually written.
    tq_path = db_path.with_suffix(".tq")
    assert tq_path.exists()
    assert tq_path.stat().st_size > 0

    # Read half: dense retrieval through the converged path returns real
    # TurboQuant hits keyed under the dense retriever name. A pre-fix tree
    # (FTS-only ``vector_store``) returns 0 hits or a non-dense retriever.
    ctx = build_retrieval_context(db_path, cfg)
    hits = await ctx.vector_store.vector_search(list(chunks[0].embedding), limit=5)

    assert len(hits) > 0
    assert hits[0].retriever_name == "turboquant_dense"
    # Querying with chunk[0]'s exact embedding must surface chunk[0]'s row.
    assert hits[0].text == "alpha body"
