"""DenseScorerStep re-ranks real read-path candidates by turbovec score.

``TurboQuantVectorStore.vector_search`` hydrates vector-search hits through
``build_sqlite_chunk_hydrator`` -> ``row_to_chunk``, and ``row_to_chunk``
never populates ``Chunk.embedding`` (models.py documents this: "stays
``None`` on read paths because dense vectors live in the ``.tq`` sidecar
and the SQL row does not carry them back into Chunk (S13)"). The OLD
``DenseScorerStep`` read ``candidate.embedding`` directly and had an
explicit ``if c.embedding is None: pass through unchanged`` branch — so on
the real read path every candidate hit that branch and the scorer was a
silent no-op in every shipped dense/hybrid pipeline.

The FIX turns ``DenseScorerStep`` into a POST-FUSION re-ranker that calls
``store.score(query_vector, subset_chunk_ids=ids, top_k=K)`` —
:class:`TurboQuantVectorStore.score` uses turbovec's allowlist-search hook
to re-score the given id subset directly from the ``.tq`` index, so it
needs no ``Chunk.embedding`` at all. This test drives the SAME path
production uses end to end: index chunks with embeddings through
IndexingService -> SearchBackend's composite UoW, fetch through
``backend.dense()`` (real hydrated candidates, ``embedding=None``), then
run ``DenseScorerStep`` wired to the real ``backend.dense()`` store and
assert the relevance values actually CHANGE (a real re-rank, not a
pass-through) and match the exact turbovec score for that subset.
"""

from __future__ import annotations

from dataclasses import replace
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


@pytest.mark.asyncio
async def test_dense_scorer_reranks_real_hydrated_candidates_via_turboquant_score(
    tmp_path: Path,
) -> None:
    """End-to-end: index -> dense_fetcher-equivalent hydration -> dense_scorer.

    Candidates come from ``backend.dense().vector_search(...)`` — the exact
    hydration path ``DenseFetcherStep`` uses in every shipped dense YAML.
    The rewritten ``DenseScorerStep`` re-ranks them via
    ``backend.dense().score(...)`` (turbovec allowlist search over the
    candidate subset) instead of reading ``candidate.embedding`` — so
    ``relevance`` changes from whatever the fused/ANN input carried to the
    fresh turbovec score, even though every candidate's ``embedding`` is
    still ``None`` (the read-path contract is unchanged).
    """
    db_path = tmp_path / "index.sqlite"
    open_index_database(db_path).close()

    cfg = AppConfig.load()
    dim = cfg.embedding.dim

    backend = build_search_backend(cfg, db_path)
    uow_factory = build_composite_uow_factory(backend.write_uow_children())

    # Two chunks with distinct dim-sized embeddings, differing enough in
    # their leading components that turbovec ranks them differently from a
    # naive tie.
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

    # Confirms the read-path contract is unchanged: hydrated candidates
    # still carry embedding=None (models.py S13) — DenseScorerStep must NOT
    # need Chunk.embedding to do its job any more.
    assert all(c.embedding is None for c in hits), (
        "hydrated candidates unexpectedly carry embeddings — "
        "if this starts failing, the read-path hydration contract changed "
        "and this test's premise should be re-evaluated"
    )
    # Overwrite the incoming relevance/retriever_name with sentinel values
    # a real dense_fetcher+RRF fusion would NEVER produce (e.g. a BM25-
    # flavoured rank-fusion score), so any surviving sentinel proves a
    # pass-through no-op — the OLD DenseScorerStep's failure mode (it read
    # ``candidate.embedding`` which is always None on the read path, hit
    # the ``if c.embedding is None: pass through`` branch, and left
    # relevance/retriever_name completely untouched).
    sentinel_hits = tuple(
        replace(c, relevance=-999.0, retriever_name="rrf_fusion_sentinel") for c in hits
    )

    state = RetrieverState(
        query=SearchQuery(terms=query_text, max_results=10),
        candidates=ChunkList(items=sentinel_hits),
    )
    step = DenseScorerStep(store=vector_store, embedder=embedder, top_k=10)
    out = await step.run(state)

    by_title = {c.metadata.get("title"): c for c in out.candidates.items}
    assert set(by_title) == {"alpha", "beta"}

    for title in original_vecs:
        chunk = by_title[title]
        assert chunk.relevance is not None
        assert chunk.relevance != -999.0, (
            f"dense_scorer left the sentinel relevance untouched for chunk "
            f"{title!r} — this is the pass-through no-op failure mode "
            f"(OLD DenseScorerStep read candidate.embedding, which is "
            f"always None on the read path, and skipped scoring entirely)"
        )
        assert chunk.retriever_name == "turboquant_dense", (
            f"dense_scorer did not stamp retriever_name for chunk {title!r} "
            f"— got {chunk.retriever_name!r}, still the sentinel means the "
            f"real turbovec re-score never ran"
        )
        expected = await vector_store.score(list(query_vec), subset_chunk_ids=[chunk.id], top_k=1)
        assert expected, f"turboquant re-score returned nothing for chunk {title!r}"
        assert chunk.relevance == pytest.approx(expected[0][1]), (
            f"dense_scorer's relevance for chunk {title!r} does not match "
            f"the exact turbovec score for its id"
        )

    # The re-ranked order must be descending by the fresh score.
    ordered = list(out.candidates.items)
    assert ordered[0].relevance >= ordered[1].relevance
