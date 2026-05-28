"""DenseFetcherStep reads pre_filter + queries TurboQuantVectorStore (AC-17)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    Package,
    PackageOrigin,
    SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.dense_fetcher import (
    _DEFAULT_LIMIT,
    DenseFetcherStep,
)
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.filters import FieldEq
from pydocs_mcp.storage.turboquant_store import TurboQuantVectorStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork
from tests._fakes import MockEmbedder

# turbovec.IdMapIndex requires ``dim`` to be a multiple of 8 (it packs bits
# into u8 chunks; non-multiples panic at the Rust layer). It also requires
# ``bit_width`` ∈ {2, 3, 4}. Match the rest of the TurboQuant test suite.
_DIM = 8
_BIT_WIDTH = 4


def _pkg(name: str) -> Package:
    """Minimal Package — mirrors tests/storage/test_candidate_resolver_hydrator.py."""
    return Package(
        name=name,
        version="1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="",
        origin=PackageOrigin.DEPENDENCY,
    )


def _chunk(text: str, package: str) -> Chunk:
    """A chunk with a single ``package`` metadata field.

    ``id`` is left unset — :meth:`SqliteChunkRepository.upsert` auto-assigns
    ids via SQLite's INTEGER PRIMARY KEY. Tests must query the assigned ids
    back via the same store to seed the TurboQuant index with the right keys.
    """
    return Chunk(text=text, metadata={ChunkFilterField.PACKAGE.value: package})


async def test_dense_fetcher_end_to_end(tmp_path: Path) -> None:
    """Embedder → store.vector_search() → state.candidates (no pre_filter)."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    embedder = MockEmbedder(dim=_DIM)

    sqlite_factory = build_sqlite_uow_factory(db_path)
    async with sqlite_factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert(
            (
                _chunk("alpha", "demo"),
                _chunk("beta", "demo"),
                _chunk("gamma", "demo"),
            )
        )
        await uow.commit()

    # Discover assigned ids dynamically — SqliteChunkRepository.upsert IGNORES
    # any Chunk.id passed in and lets SQLite autoincrement assign the value.
    async with sqlite_factory() as uow:
        seeded = await uow.chunks.list(filter={"package": "demo"})
    text_to_id = {c.text: c.id for c in seeded}
    seeded_texts = ["alpha", "beta", "gamma"]
    seeded_ids = [text_to_id[t] for t in seeded_texts]

    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
    ) as tq_uow:
        vecs = [await embedder.embed_query(t) for t in seeded_texts]
        await tq_uow.add_vectors(seeded_ids, vecs)
        await tq_uow.commit()

    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
    ) as tq_uow:
        store = TurboQuantVectorStore(
            uow=tq_uow,
            candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
            chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
            retriever_name="dense",
        )
        step = DenseFetcherStep(store=store, embedder=embedder, limit=10)
        state = RetrieverState(
            query=SearchQuery(terms="alpha", max_results=10),
        )
        out = await step.run(state)

    assert isinstance(out.candidates, ChunkList)
    items = out.candidates.items
    assert len(items) > 0
    # Querying for ``alpha`` should rank the ``alpha`` chunk first — the
    # MockEmbedder is deterministic per input text, so re-embedding ``alpha``
    # at query time produces the same vector that was inserted.
    assert items[0].id == text_to_id["alpha"]


async def test_dense_fetcher_with_pre_filter_restricts_results(
    tmp_path: Path,
) -> None:
    """PreFilterResult in scratch is consumed and passed through to the store."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    embedder = MockEmbedder(dim=_DIM)

    sqlite_factory = build_sqlite_uow_factory(db_path)
    async with sqlite_factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.packages.upsert(_pkg("other"))
        await uow.chunks.upsert(
            (
                _chunk("alpha", "demo"),
                _chunk("beta", "other"),
            )
        )
        await uow.commit()

    async with sqlite_factory() as uow:
        all_chunks = await uow.chunks.list()
    text_to_id = {c.text: c.id for c in all_chunks}

    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
    ) as tq_uow:
        vecs = [await embedder.embed_query(t) for t in ("alpha", "beta")]
        await tq_uow.add_vectors(
            [text_to_id["alpha"], text_to_id["beta"]],
            vecs,
        )
        await tq_uow.commit()

    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
    ) as tq_uow:
        store = TurboQuantVectorStore(
            uow=tq_uow,
            candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
            chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
            retriever_name="dense",
        )
        step = DenseFetcherStep(store=store, embedder=embedder, limit=10)
        state = RetrieverState(
            query=SearchQuery(
                terms="alpha",
                max_results=10,
                pre_filter={"package": "demo"},
            ),
        )
        # Simulate PreFilterStep having run upstream — write a typed result
        # that restricts the allowlist to ``demo``-package chunks. Post-C5
        # commit 2 the result carries only ``tree`` + ``scope``; the
        # dense fetcher consumes ``tree`` directly through
        # ``VectorSearchable.vector_search(filter=...)``.
        state.scratch["pre_filter.result"] = PreFilterResult(
            tree=FieldEq("package", "demo"),
            scope=None,
        )
        out = await step.run(state)

    assert isinstance(out.candidates, ChunkList)
    # Allowlist restricts to {alpha} — beta belongs to ``other`` and is
    # filtered out by the resolver before the ANN search runs.
    assert all(c.id == text_to_id["alpha"] for c in out.candidates.items)


async def test_dense_fetcher_empty_query_short_circuits() -> None:
    """Empty terms (post-strip) returns empty candidates without calling embedder/store.

    ``SearchQuery._terms_non_empty`` rejects whitespace at construction, so we
    build a valid query then bypass the frozen pydantic dataclass via
    ``object.__setattr__`` to land whitespace-only ``terms`` on the instance.
    This exercises the empty-terms guard branch directly.
    """
    embedder_called = False
    store_called = False

    class _RecordingEmbedder:
        dim = _DIM

        async def embed_query(self, text):
            nonlocal embedder_called
            embedder_called = True
            import numpy as np

            return np.zeros(_DIM, dtype=np.float32)

        async def embed_chunks(self, texts):
            return ()

    class _RecordingStore:
        async def vector_search(self, query_vector, limit, filter=None):
            nonlocal store_called
            store_called = True
            return ()

    query = SearchQuery(terms="ok", max_results=10)
    # Bypass the frozen-dataclass barrier. The post-strip empty-terms branch
    # in ``DenseFetcherStep.run`` is unreachable through normal construction
    # because the field validator rejects whitespace — direct mutation is the
    # only clean way to verify the guard fires when called.
    object.__setattr__(query, "terms", "   ")
    state = RetrieverState(query=query)

    step = DenseFetcherStep(
        store=_RecordingStore(),  # type: ignore[arg-type]
        embedder=_RecordingEmbedder(),  # type: ignore[arg-type]
        limit=10,
    )
    out = await step.run(state)

    assert isinstance(out.candidates, ChunkList)
    assert out.candidates.items == ()
    assert embedder_called is False
    assert store_called is False


def test_dense_fetcher_from_dict_requires_vector_store_and_embedder() -> None:
    """from_dict raises a clear ValueError when context fields are missing."""
    # Build a minimal context with NEITHER vector_store NOR embedder.
    from pydocs_mcp.retrieval.protocols import ConnectionProvider

    class _NullProvider:
        cache_path = Path("/tmp/unused.db")

        async def acquire(self):  # pragma: no cover - never called
            raise NotImplementedError

    ctx = BuildContext(connection_provider=_NullProvider())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="vector_store"):
        DenseFetcherStep.from_dict({"type": "dense_fetcher"}, ctx)


def test_dense_fetcher_to_dict_round_trip_default_omits_limit() -> None:
    """to_dict omits limit when default; from_dict restores via the default constant."""
    embedder = MockEmbedder(dim=_DIM)

    # Tiny stub VectorSearchable — only needs the method signature for typing.
    class _StubStore:
        async def vector_search(self, query_vector, limit, filter=None):
            return ()

    step = DenseFetcherStep(store=_StubStore(), embedder=embedder)  # type: ignore[arg-type]
    d = step.to_dict()
    # default limit is omitted to keep YAML output clean
    assert d == {"type": "dense_fetcher"}

    step2 = DenseFetcherStep(store=_StubStore(), embedder=embedder, limit=99)  # type: ignore[arg-type]
    d2 = step2.to_dict()
    assert d2 == {"type": "dense_fetcher", "limit": 99}
    # The from_dict fallback uses _DEFAULT_LIMIT — verify symbol exists so
    # the single-source-of-truth contract isn't accidentally broken.
    assert _DEFAULT_LIMIT == 50
