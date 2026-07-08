"""``_TurboQuantReadStore.score`` / ``TurboQuantVectorStore.score`` — allowlist re-rank.

Mirrors ``tests/storage/test_turboquant_read_store.py``'s fixture (dim=8,
bit_width=4, seed chunks via SQLite, add_vectors, then ``_TurboQuantReadStore``)
but exercises the new ``score(query, subset_chunk_ids=..., top_k=...)`` method
that ``DenseScorerStep`` (the post-fusion dense re-ranker) calls: turbovec's
``IdMapIndex.search(query, k, allowlist=...)`` scores ONLY the given id subset
instead of doing an open ANN search — this is the same allowlist hook
``vector_search`` already uses for metadata pre-filtering, applied here to a
pipeline-provided candidate subset instead of a SQL-derived one.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, ChunkFilterField, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.search_backend import _TurboQuantReadStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

# turbovec.IdMapIndex requires ``dim`` to be a multiple of 8 (it packs bits
# into u8 chunks; non-multiples panic at the Rust layer). Match the rest of
# the TurboQuant test suite.
_DIM = 8
_BIT_WIDTH = 4


def _pkg(name: str) -> Package:
    """Minimal Package — mirrors tests/retrieval/steps/test_dense_fetcher.py."""
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


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(_DIM).astype(np.float32)


async def _seed_three_chunks(tmp_path: Path) -> tuple[Path, Path, list[int]]:
    """Index 3 chunks in SQLite + TurboQuant; return (db_path, tq_path, ids)."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
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
    async with sqlite_factory() as uow:
        seeded = await uow.chunks.list(filter={"package": "demo"})
    ids = sorted(c.id for c in seeded if c.id is not None)
    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
    ) as tq:
        await tq.add_vectors(ids, [_vec(i) for i in ids])
        await tq.commit()
    return db_path, tq_path, ids


@pytest.mark.asyncio
async def test_score_returns_pairs_only_for_present_subset_ids(tmp_path: Path) -> None:
    db_path, tq_path, ids = await _seed_three_chunks(tmp_path)
    assert len(ids) == 3

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    query = _vec(ids[0]).tolist()
    out = await store.score(query, subset_chunk_ids=ids, top_k=10)

    assert len(out) == 3
    returned_ids = {cid for cid, _ in out}
    assert returned_ids == set(ids)
    for _cid, score in out:
        assert isinstance(score, float)


@pytest.mark.asyncio
async def test_score_silently_skips_ids_absent_from_index(tmp_path: Path) -> None:
    db_path, tq_path, ids = await _seed_three_chunks(tmp_path)
    absent_id = max(ids) + 1000  # never added to the .tq index

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    query = _vec(ids[0]).tolist()
    # Must NOT raise KeyError — turbovec raises on absent allowlist ids;
    # the store must intersect with present-in-index ids first.
    out = await store.score(query, subset_chunk_ids=[*ids, absent_id], top_k=10)

    returned_ids = {cid for cid, _ in out}
    assert returned_ids == set(ids)
    assert absent_id not in returned_ids


@pytest.mark.asyncio
async def test_score_subset_smaller_than_full_index(tmp_path: Path) -> None:
    db_path, tq_path, ids = await _seed_three_chunks(tmp_path)
    subset = ids[:2]

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    query = _vec(ids[0]).tolist()
    out = await store.score(query, subset_chunk_ids=subset, top_k=10)

    returned_ids = {cid for cid, _ in out}
    assert returned_ids == set(subset)
    assert ids[2] not in returned_ids


@pytest.mark.asyncio
async def test_score_empty_tq_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"  # never written
    open_index_database(db_path).close()
    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    out = await store.score(_vec(0).tolist(), subset_chunk_ids=[1, 2, 3], top_k=10)
    assert out == ()


@pytest.mark.asyncio
async def test_score_empty_subset_ids_returns_empty(tmp_path: Path) -> None:
    db_path, tq_path, _ids = await _seed_three_chunks(tmp_path)
    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    out = await store.score(_vec(0).tolist(), subset_chunk_ids=[], top_k=10)
    assert out == ()
