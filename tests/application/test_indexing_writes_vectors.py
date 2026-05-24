"""IndexingService writes vectors alongside chunks via composite UoW (AC-24).

When the UoW exposes a ``vectors`` attribute (composite SQLite + TurboQuant),
``reindex_package`` must call ``uow.vectors.add_vectors(ids, embeddings)``
inside the same async-with transaction as the SQLite chunk upsert. The
composite ``commit()`` then attempts both children sequentially.

The IDs paired with each embedding are the autoincrement ``chunks.id``
values SQLite assigned during ``chunks.upsert(...)``. Because the
service ``delete``s the package's chunks first and then performs a single
``executemany`` insert, the persisted rows come back ordered by ``id``
matching the input chunk order — so input embeddings can be paired
positionally with the persisted IDs.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


# turbovec requires dim multiple of 8 (panics otherwise) and bit_width in
# {2, 3, 4} — see tests/storage/test_turboquant_uow.py. Pad embeddings
# to _DIM accordingly.
_DIM = 8
_BW = 4


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


def _vec(*values: float) -> np.ndarray:
    """Pad/truncate ``values`` to a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


def _chunk(pkg: str, title: str, text: str, vec: np.ndarray) -> Chunk:
    return Chunk(
        text=text,
        embedding=vec,
        metadata={"package": pkg, "title": title},
    )


@pytest.mark.asyncio
async def test_reindex_package_writes_chunks_AND_vectors(tmp_path: Path) -> None:
    """Composite UoW path: SQLite chunks + TurboQuant vectors land together."""
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    chunks = (
        _chunk("demo", "alpha", "alpha body", _vec(0.1, 0.2, 0.3, 0.4)),
        _chunk("demo", "beta",  "beta body",  _vec(0.5, 0.6, 0.7, 0.8)),
    )
    package = _pkg("demo")

    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(package, chunks, module_members=())

    # SQLite landed both chunks.
    async with factory() as uow:
        persisted = await uow.chunks.list(filter={"package": "demo"})
    assert len(persisted) == 2

    # TurboQuant sidecar landed both vectors and persisted them to disk.
    assert tq_path.exists()
    async with TurboQuantUnitOfWork(
        index_path=tq_path, dim=_DIM, bit_width=_BW,
    ) as tq_uow:
        assert tq_uow.size() == 2


@pytest.mark.asyncio
async def test_reindex_package_skips_chunks_without_embedding(
    tmp_path: Path,
) -> None:
    """Mixed batch: only chunks carrying an embedding are forwarded to
    ``add_vectors`` — chunks without one still persist to SQLite but do
    not produce vector rows. This protects against partial-embedding
    ingestion (e.g. when ``EmbedChunksStage`` is disabled or fails for
    a subset).

    The test asserts pairing DIRECTION: the kept vector must map to the
    embedded chunk's id, not the bare chunk's id. A regression that
    swapped the input/persisted ordering (e.g. mapped the embedding to
    the wrong row by counting bare chunks) would still pass a
    ``size() == 1`` count check, so we additionally probe membership via
    ``IdMapIndex.contains``.
    """
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    embedded_vec = _vec(0.1, 0.2)
    chunks = (
        # No embedding — must NOT contribute a vector row.
        Chunk(text="bare", metadata={"package": "demo", "title": "bare"}),
        _chunk("demo", "alpha", "alpha body", embedded_vec),
    )
    package = _pkg("demo")
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(package, chunks, module_members=())

    async with factory() as uow:
        persisted = await uow.chunks.list(filter={"package": "demo"})
    assert len(persisted) == 2  # both chunks landed in SQLite

    # Recover the SQLite ids assigned to each input chunk. The service
    # pairs by sorting persisted rows by id (ascending) and zipping with
    # ``input_chunks`` positionally — id 0 = bare, id 1 = alpha — so the
    # correct mapping is "kept vector lives under alpha's id".
    persisted_sorted = sorted(persisted, key=lambda c: c.id or 0)
    bare_id = persisted_sorted[0].id
    embedded_id = persisted_sorted[1].id
    assert bare_id is not None and embedded_id is not None
    assert bare_id != embedded_id  # sanity — distinct rows got distinct ids

    # Only one embedding → exactly one vector AND that vector must be
    # keyed by the embedded chunk's id, NOT the bare chunk's id. A
    # pairing-direction regression (mapping the embedding to the wrong
    # row) would flip these two assertions.
    async with TurboQuantUnitOfWork(
        index_path=tq_path, dim=_DIM, bit_width=_BW,
    ) as tq_uow:
        assert tq_uow.size() == 1
        # IdMapIndex.contains is the direct membership probe — proves
        # WHICH id the stored vector is keyed under, not just the count.
        assert tq_uow.index.contains(embedded_id) is True
        assert tq_uow.index.contains(bare_id) is False


@pytest.mark.asyncio
async def test_reindex_package_with_no_embeddings_does_not_touch_tq(
    tmp_path: Path,
) -> None:
    """All-bare chunk batch leaves the ``.tq`` sidecar untouched.

    The composite ``commit()`` still runs against the TurboQuant child,
    but ``add_vectors`` was never called, so the in-memory index stays
    clean and ``commit()`` is a no-op (``_dirty == False``). The
    sidecar file is therefore not created.
    """
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    chunks = (
        Chunk(text="bare1", metadata={"package": "demo", "title": "bare1"}),
        Chunk(text="bare2", metadata={"package": "demo", "title": "bare2"}),
    )
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(_pkg("demo"), chunks, module_members=())

    async with factory() as uow:
        persisted = await uow.chunks.list(filter={"package": "demo"})
    assert len(persisted) == 2
    # No ``add_vectors`` was issued → no commit fsync → no sidecar file.
    assert not tq_path.exists()
