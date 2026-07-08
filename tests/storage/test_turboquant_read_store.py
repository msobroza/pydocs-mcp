"""_TurboQuantReadStore re-enters a TurboQuantUnitOfWork per query."""

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


@pytest.mark.asyncio
async def test_read_store_returns_hits_without_externally_entered_uow(
    tmp_path: Path,
) -> None:
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
            )
        )
        await uow.commit()
    # SqliteChunkRepository.upsert IGNORES any Chunk.id passed in and lets
    # SQLite autoincrement assign the value — query the assigned ids back.
    async with sqlite_factory() as uow:
        seeded = await uow.chunks.list(filter={"package": "demo"})
    ids = [c.id for c in seeded]
    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
    ) as tq:
        await tq.add_vectors(ids, [_vec(i) for i in ids])
        await tq.commit()

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    # No surrounding ``async with`` — the read store opens its own uow.
    out = await store.vector_search(_vec(ids[0]).tolist(), limit=5)
    assert len(out) > 0


@pytest.mark.asyncio
async def test_read_store_empty_tq_returns_empty(tmp_path: Path) -> None:
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
    out = await store.vector_search(_vec(0).tolist(), limit=5)
    assert out == ()


@pytest.mark.asyncio
async def test_read_store_corrupt_tq_degrades_instead_of_raising(tmp_path: Path) -> None:
    """A ``.tq`` file that EXISTS but is unreadable (truncated by disk
    trouble, hand-edited, or written by an incompatible turbovec version)
    must degrade the dense leg to ``()``, not raise a raw ``OSError``.

    ``_TurboQuantReadStore`` opens a FRESH ``TurboQuantUnitOfWork`` per
    query (module docstring); every hybrid/dense search hits this same
    path, so an unreadable sidecar would otherwise take down BM25+dense
    fusion entirely instead of degrading to the BM25 leg alone.
    ``IdMapIndex.load`` raises ``OSError('not a TVIM file: wrong magic')``
    for any existing-but-invalid file — confirmed against the real
    turbovec extension, distinct from the missing-file branch already
    covered by ``test_read_store_empty_tq_returns_empty`` above (that one
    never calls ``IdMapIndex.load`` at all; ``Path.exists()`` is False so
    ``_open_index`` takes the constructor branch instead).
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    tq_path.write_bytes(b"not a real tq file, just garbage bytes")

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    out = await store.vector_search(_vec(0).tolist(), limit=5)
    assert out == ()
