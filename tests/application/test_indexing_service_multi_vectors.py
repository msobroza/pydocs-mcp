"""IndexingService dispatches multi-vector embeddings to ``uow.multi_vectors``.

Late-interaction (ColBERT/PyLate) embedders return ``list[np.ndarray]``
(MultiVector) per chunk; single-vector embedders return a single
``np.ndarray``. ``IndexingService._maybe_write_vectors`` must dispatch on
``is_multi_vector(emb)`` and route each chunk's embedding to the right
store — ``uow.vectors`` for the dense single-vector case,
``uow.multi_vectors`` for the late-interaction case.

A regression that routed multi-vectors to ``uow.vectors`` would break
the .tq sidecar (TurboQuant expects flat float32 arrays) and silently
drop the late-interaction data.
"""

from __future__ import annotations

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from tests._fakes import InMemoryChunkStore, make_fake_uow_factory


class _SpyMultiVectorStore:
    """Recording stand-in for ``MultiVectorStore`` — captures ``add_vectors``
    calls so the test can assert on (ids, embeddings) pairs.

    Mirrors :class:`NullMultiVectorStore` shape: same method signatures,
    same async semantics. ``score`` returns an empty tuple instead of
    raising so the test never accidentally triggers the read path.
    """

    def __init__(self) -> None:
        self.adds: list[tuple[list[int], list[list[np.ndarray]]]] = []
        self.removes: list[list[int]] = []
        self.cleared: int = 0

    async def add_vectors(self, ids, embeddings) -> None:
        self.adds.append((list(ids), list(embeddings)))

    async def remove_vectors(self, ids) -> None:
        self.removes.append(list(ids))

    async def clear_all(self) -> None:
        self.cleared += 1

    async def score(self, query_embedding, *, subset_chunk_ids, top_k):
        return ()


class _SpyVectorStore:
    """Recording stand-in for ``VectorStore`` — captures ``add_vectors``
    so the test can assert single-vector embeddings stay on the dense
    path.
    """

    def __init__(self) -> None:
        self.adds: list[tuple[list[int], list[np.ndarray]]] = []

    async def add_vectors(self, ids, embeddings) -> None:
        self.adds.append((list(ids), list(embeddings)))

    async def remove_vectors(self, ids) -> None:
        return None

    async def clear_all(self) -> None:
        return None

    async def vector_search(self, query, *, top_k):  # pragma: no cover
        return ()


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


def _mv(n_tokens: int, dim: int = 4) -> list[np.ndarray]:
    """Build an ``n_tokens × dim`` multi-vector embedding (unit-norm rows)."""
    return [np.full((dim,), 1.0 / np.sqrt(dim), dtype=np.float32) for _ in range(n_tokens)]


def _sv(dim: int = 4) -> np.ndarray:
    return np.full((dim,), 1.0 / np.sqrt(dim), dtype=np.float32)


def _chunk(pkg: str, title: str, text: str, emb) -> Chunk:
    return Chunk(text=text, embedding=emb, metadata={"package": pkg, "title": title})


@pytest.mark.asyncio
async def test_reindex_routes_multi_vector_embeddings_to_uow_multi_vectors() -> None:
    """A chunk whose embedding is ``list[np.ndarray]`` lands on
    ``uow.multi_vectors.add_vectors``, NOT ``uow.vectors.add_vectors``.

    This is the dispatch contract: ``is_multi_vector(c.embedding)`` is
    the type guard. A regression that forwarded the list to
    ``uow.vectors`` would either crash TurboQuant or silently corrupt
    the .tq sidecar.
    """
    mv_store = _SpyMultiVectorStore()
    sv_store = _SpyVectorStore()
    uow_factory = make_fake_uow_factory(vectors=sv_store, multi_vectors=mv_store)
    svc = IndexingService(uow_factory=uow_factory)

    chunks = (_chunk("demo", "alpha", "alpha body", _mv(n_tokens=3)),)
    await svc.reindex_package(_pkg("demo"), chunks, module_members=())

    # Multi-vector store saw exactly one add with one chunk's embedding.
    assert len(mv_store.adds) == 1
    ids, embs = mv_store.adds[0]
    assert len(ids) == 1
    assert len(embs) == 1
    assert len(embs[0]) == 3  # n_tokens preserved end-to-end
    assert all(isinstance(tok, np.ndarray) for tok in embs[0])
    # Single-vector store stayed untouched — the dispatch correctly
    # routed away from it.
    assert sv_store.adds == []


@pytest.mark.asyncio
async def test_reindex_routes_mixed_batch_by_embedding_shape() -> None:
    """Mixed batch: one single-vector chunk + one multi-vector chunk go
    to their respective stores in the same reindex call. The dispatch
    is per-chunk, not per-batch.
    """
    mv_store = _SpyMultiVectorStore()
    sv_store = _SpyVectorStore()
    uow_factory = make_fake_uow_factory(vectors=sv_store, multi_vectors=mv_store)
    svc = IndexingService(uow_factory=uow_factory)

    chunks = (
        _chunk("demo", "single", "single body", _sv()),
        _chunk("demo", "multi", "multi body", _mv(n_tokens=2)),
    )
    await svc.reindex_package(_pkg("demo"), chunks, module_members=())

    # Each store got exactly the chunk that matched its embedding shape.
    assert len(sv_store.adds) == 1
    sv_ids, sv_embs = sv_store.adds[0]
    assert len(sv_ids) == 1
    assert isinstance(sv_embs[0], np.ndarray)

    assert len(mv_store.adds) == 1
    mv_ids, mv_embs = mv_store.adds[0]
    assert len(mv_ids) == 1
    assert isinstance(mv_embs[0], list)
    assert len(mv_embs[0]) == 2

    # The two stores received DIFFERENT ids — distinct chunk rows.
    assert sv_ids != mv_ids


@pytest.mark.asyncio
async def test_reindex_with_only_single_vectors_skips_multi_vector_store() -> None:
    """Single-vector-only batch: ``multi_vectors.add_vectors`` is never
    called. Protects against an over-eager dispatch that forwards an
    empty list (which would still mutate the .tq sidecar).
    """
    mv_store = _SpyMultiVectorStore()
    sv_store = _SpyVectorStore()
    uow_factory = make_fake_uow_factory(vectors=sv_store, multi_vectors=mv_store)
    svc = IndexingService(uow_factory=uow_factory)

    chunks = (_chunk("demo", "alpha", "alpha body", _sv()),)
    await svc.reindex_package(_pkg("demo"), chunks, module_members=())

    assert len(sv_store.adds) == 1
    assert mv_store.adds == []


@pytest.mark.asyncio
async def test_reindex_mixed_batch_marks_embedded_only_for_single_vector_ids() -> None:
    """``chunks.embedded`` is stamped ONLY for single-vector chunk ids —
    NEVER for multi-vector (ColBERT) chunk ids, even in a mixed batch.

    ``check_integrity_and_repair`` (storage/factories.py) compares
    ``COUNT(chunks.embedded = 1)`` against the TurboQuant ``.tq``
    ``IdMapIndex`` size. Multi-vectors live in fast-plaid, not the .tq,
    so stamping an mv id as embedded would create permanent count drift:
    every startup would see embedded-count > .tq vector count, clear
    every affected package's ``content_hash``, and force a full
    re-extract of every late-interaction package on EVERY index/serve
    run — an infinite re-extract loop.

    A regression that "unifies" the sv/mv branches to mark all embedded
    chunks (instead of only ``sv_ids``) would pass the existing
    dispatch-routing tests in this file (which only assert on
    ``uow.vectors`` / ``uow.multi_vectors`` add calls) while silently
    reintroducing this drift. This test asserts directly on the fake
    chunk store's ``mark_embedded`` calls, the one surface that would
    catch it.
    """
    chunk_store = InMemoryChunkStore()
    mv_store = _SpyMultiVectorStore()
    sv_store = _SpyVectorStore()
    uow_factory = make_fake_uow_factory(
        chunks=chunk_store,
        vectors=sv_store,
        multi_vectors=mv_store,
    )
    svc = IndexingService(uow_factory=uow_factory)

    chunks = (
        _chunk("demo", "single", "single body", _sv()),
        _chunk("demo", "multi", "multi body", _mv(n_tokens=2)),
    )
    await svc.reindex_package(_pkg("demo"), chunks, module_members=())

    # Exactly one mark_embedded call, carrying only the single-vector id.
    mark_embedded_calls = [c for c in chunk_store.calls if c.method == "mark_embedded"]
    assert len(mark_embedded_calls) == 1
    (sv_ids, _embs) = sv_store.adds[0]
    (mv_ids, _mv_embs) = mv_store.adds[0]
    stamped_ids = mark_embedded_calls[0].payload
    assert stamped_ids == sv_ids
    assert not set(stamped_ids) & set(mv_ids)
    # The store's own bookkeeping mirrors chunks.embedded=1 rows — same
    # assertion via the persisted-state surface, not just the call log.
    assert chunk_store.embedded_ids == set(sv_ids)
