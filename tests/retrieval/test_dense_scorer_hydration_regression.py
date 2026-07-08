"""DenseScorerStep silently no-ops on real read-path candidates.

``TurboQuantVectorStore`` hydrates vector-search hits through
``build_sqlite_chunk_hydrator`` -> ``row_to_chunk``, and ``row_to_chunk``
never populates ``Chunk.embedding`` (models.py documents this: "stays
``None`` on read paths because dense vectors live in the ``.tq`` sidecar
and the SQL row does not carry them back into Chunk (S13)"). Every
shipped dense pipeline (chunk_search_dense.yaml, chunk_search_graph.yaml,
chunk_search_hybrid.yaml) chains ``dense_fetcher`` -> ``dense_scorer`` and
the YAML comments promise the scorer "overwrite[s] relevance with exact
cosine sim" because "the fetcher's ANN index score is approximate".

``DenseScorerStep.run`` has an explicit ``if c.embedding is None: pass
through unchanged`` branch (dense_scorer.py) — so on the real read path
every candidate takes that branch and the promised exact-cosine re-rank
never happens. ``tests/retrieval/steps/test_dense_scorer.py`` never
catches this because it hand-constructs ``Chunk`` objects WITH embeddings,
a shape that real read-path candidates never have.

This test drives the SAME path production uses end to end: index chunks
with embeddings through IndexingService -> SearchBackend's composite
UoW, then fetch through ``backend.dense()`` (the real
``TurboQuantVectorStore`` + ``build_sqlite_chunk_hydrator`` wiring) and
run ``DenseScorerStep`` on the hydrated candidates.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import ChunkList, Package, PackageOrigin, SearchQuery
from pydocs_mcp.models import Chunk as ChunkModel
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.dense_scorer import DenseScorerStep
from pydocs_mcp.storage.factories import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend
from tests._fakes import MockEmbedder


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

    Mirrors ``tests/retrieval/test_dense_wiring_regression.py::_vec`` —
    trailing slots zero-filled so TurboQuant's fixed-dim index accepts it.
    """
    padded = list(values) + [0.0] * max(0, dim - len(values))
    return np.asarray(padded[:dim], dtype=np.float32)


def _chunk(pkg: str, title: str, text: str, vec: np.ndarray) -> ChunkModel:
    return ChunkModel(
        text=text,
        embedding=vec,
        metadata={"package": pkg, "title": title},
    )


def _cosine_sim(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


@pytest.mark.xfail(
    reason=(
        "Known gap, unresolved by design decision (not a code bug fixable "
        "in isolation): row_to_chunk() (storage/sqlite/row_mappers.py) never "
        "populates Chunk.embedding on read paths (models.py S13 comment), "
        "and turbovec.IdMapIndex exposes no reconstruct/get-vector API — it "
        "stores only a quantized representation (bit_width=4 default), so "
        "there is no full-precision vector anywhere on the read path for "
        "DenseScorerStep to score against. Fixing this for real requires "
        "either (a) a new SQLite schema version that persists raw float32 "
        "chunk vectors alongside/instead of relying on the .tq quantized "
        "index, wired through row_to_chunk + IndexingService's write path, "
        "or (b) deleting dense_scorer from the shipped dense YAMLs "
        "(chunk_search_dense.yaml / chunk_search_graph.yaml / "
        "chunk_search_hybrid.yaml) and updating their now-inaccurate "
        "'exact cosine re-rank' comments to document that ranking uses the "
        "approximate ANN index score. Both are genuine design decisions "
        "(schema migration vs. changed ranking contract) — see gap "
        "'DenseScorerStep is a silent no-op in every shipped dense "
        "pipeline'. This test pins CURRENT (broken) behavior; remove the "
        "xfail once either fix lands."
    ),
    strict=True,
)
@pytest.mark.asyncio
async def test_dense_scorer_rescopres_real_hydrated_candidates_with_exact_cosine(
    tmp_path: Path,
) -> None:
    """End-to-end: index -> dense_fetcher-equivalent hydration -> dense_scorer.

    Candidates come from ``backend.dense().vector_search(...)`` — the exact
    hydration path ``DenseFetcherStep`` uses in every shipped dense YAML.
    If ``DenseScorerStep`` is doing its documented job, each candidate's
    ``relevance`` after scoring must equal the exact cosine similarity
    between the query vector and that chunk's ORIGINAL embedding (the one
    it was indexed with) — not the approximate ANN index score, and not a
    pass-through no-op.
    """
    db_path = tmp_path / "index.sqlite"
    open_index_database(db_path).close()

    cfg = AppConfig.load()
    dim = cfg.embedding.dim

    backend = build_search_backend(cfg, db_path)
    uow_factory = build_composite_uow_factory(backend.write_uow_children())

    # Two chunks with distinct dim-sized embeddings, differing enough in
    # their leading components that exact cosine ranks them differently
    # from a naive tie — if the scorer is a no-op vs a real re-rank the
    # ordering could otherwise coincide by luck; asserting exact values
    # rules that out.
    original_vecs = {
        "alpha": _vec(dim, 1.0, 0.0, 0.0, 0.0),
        "beta": _vec(dim, 0.6, 0.8, 0.0, 0.0),
    }
    chunks = (
        _chunk("demo", "alpha", "alpha body", original_vecs["alpha"]),
        _chunk("demo", "beta", "beta body", original_vecs["beta"]),
    )
    package = _pkg("demo")

    svc = IndexingService(uow_factory=uow_factory)
    await svc.reindex_package(package, chunks, module_members=())

    # Real read-path hydration: same store class + hydrator DenseFetcherStep
    # uses via BuildContext.vector_store in every shipped dense pipeline.
    vector_store = backend.dense()

    embedder = MockEmbedder(dim=dim)
    query_text = "alpha query"
    query_vec = np.asarray(await embedder.embed_query(query_text), dtype=np.float32)

    hits = await vector_store.vector_search(list(query_vec), limit=5)
    assert len(hits) == 2, "expected both indexed chunks to come back as candidates"

    # Confirms the edge case: real hydrated candidates carry embedding=None
    # (models.py S13) — this is the precondition that makes DenseScorerStep's
    # `if c.embedding is None: pass through unchanged` branch fire in
    # production.
    assert all(c.embedding is None for c in hits), (
        "hydrated candidates unexpectedly carry embeddings — "
        "if this starts failing, the read-path hydration contract changed "
        "and DenseScorerStep may no longer be a no-op (re-evaluate this test)"
    )

    state = RetrieverState(
        query=SearchQuery(terms=query_text, max_results=10),
        candidates=ChunkList(items=hits),
    )
    step = DenseScorerStep(name="dense_scorer", embedder=embedder)
    out = await step.run(state)

    by_title = {c.metadata.get("title"): c for c in out.candidates.items}

    for title, orig_vec in original_vecs.items():
        expected = _cosine_sim(query_vec, orig_vec)
        actual = by_title[title].relevance
        assert actual == pytest.approx(expected, rel=1e-5), (
            f"dense_scorer did not rewrite relevance to the exact cosine "
            f"similarity for chunk {title!r} (real hydrated candidate with "
            f"embedding=None) — got {actual!r}, expected {expected!r} "
            f"(the approximate ANN index score, unchanged, is the observed "
            f"failure mode)"
        )
