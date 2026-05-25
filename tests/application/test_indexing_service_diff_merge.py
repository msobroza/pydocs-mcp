"""IndexingService.reindex_package diff-merge (AC-3 + AC-8 + AC-9).

The new path replaces the legacy ``chunks.delete + chunks.upsert`` pair with
a diff over ``Chunk.content_hash``: keep unchanged rows + their vectors,
insert only added chunks, delete only removed chunks (and remove their
vectors when the UoW is composite SQLite + TurboQuant).

These tests exercise the real SQLite + TurboQuant composite UoW so the
diff covers both backends in one pass, plus a NULL-hash legacy seed that
proves self-healing on the first reindex per package.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
    build_sqlite_uow_factory,
)


# turbovec requires dim multiple of 8 and bit_width in {2, 3, 4} —
# see tests/storage/test_turboquant_uow.py for the rationale.
_DIM = 8
_BW = 4


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _vec(*values: float) -> np.ndarray:
    """Pad / truncate ``values`` into a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


@pytest.mark.asyncio
async def test_reindex_unchanged_chunks_keep_their_ids_and_vectors(
    tmp_path: Path,
) -> None:
    """AC-3 — reindex with identical chunks is a no-op for SQLite + TurboQuant.

    The diff sees every incoming hash already in the existing snapshot, so
    nothing is deleted and nothing is added. Row IDs survive (because the
    rows were never deleted-and-recreated), and the TurboQuant ``.tq`` size
    is unchanged (no vectors were touched).
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    chunks = (
        Chunk(text="a", metadata={"package": "demo", "title": "a"},
              embedding=_vec(0.1)),
        Chunk(text="b", metadata={"package": "demo", "title": "b"},
              embedding=_vec(0.2)),
    )
    await svc.reindex_package(_pkg("demo"), chunks, ())

    async with factory() as uow:
        pairs_first = await uow.chunks.list_id_hash_pairs(
            filter={"package": "demo"},
        )
        tq_size_first = uow.vectors.size()
    ids_first = {cid for cid, _ in pairs_first}

    # Re-index with the SAME chunks (same package/title/text → same hashes).
    await svc.reindex_package(_pkg("demo"), chunks, ())

    async with factory() as uow:
        pairs_after = await uow.chunks.list_id_hash_pairs(
            filter={"package": "demo"},
        )
        tq_size_after = uow.vectors.size()
    ids_after = {cid for cid, _ in pairs_after}

    # Same row IDs survive (no delete-then-recreate churn) and the vector
    # count is unchanged (the diff filter excluded everything from the add).
    assert ids_first == ids_after
    assert tq_size_first == tq_size_after == 2


@pytest.mark.asyncio
async def test_reindex_partial_diff_inserts_added_deletes_removed(
    tmp_path: Path,
) -> None:
    """One chunk unchanged + one removed + one added → diff applies surgically.

    The keeper's row + vector survive intact; the removed row + vector get
    wiped; the added row + vector land fresh. Vector count tracks the new
    chunk count exactly.
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    chunks_first = (
        Chunk(text="keep-body",
              metadata={"package": "demo", "title": "keep"},
              embedding=_vec(0.1)),
        Chunk(text="goner-body",
              metadata={"package": "demo", "title": "will-be-removed"},
              embedding=_vec(0.2)),
    )
    await svc.reindex_package(_pkg("demo"), chunks_first, ())

    # Capture the keeper's row id BEFORE the second reindex — it should
    # survive (the diff must not re-insert it).
    async with factory() as uow:
        first_rows = await uow.chunks.list(filter={"package": "demo"})
    keep_chunk = next(c for c in first_rows if c.metadata.get("title") == "keep")
    keep_id_before = keep_chunk.id
    assert keep_id_before is not None

    # Second batch: same "keep" chunk + new "added" chunk.
    chunks_second = (
        Chunk(text="keep-body",
              metadata={"package": "demo", "title": "keep"},
              embedding=_vec(0.1)),
        Chunk(text="added-body",
              metadata={"package": "demo", "title": "added"},
              embedding=_vec(0.3)),
    )
    await svc.reindex_package(_pkg("demo"), chunks_second, ())

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(
            filter={"package": "demo"},
        )
        tq_size = uow.vectors.size()
        rows_after = await uow.chunks.list(filter={"package": "demo"})

    # Exactly 2 rows persisted ("keep" + "added"); the "removed" row is gone.
    assert len(pairs) == 2
    titles_after = sorted(r.metadata.get("title", "") for r in rows_after)
    assert titles_after == ["added", "keep"]
    # Keeper's row id is unchanged — proof the diff didn't delete-and-recreate it.
    keep_chunk_after = next(
        r for r in rows_after if r.metadata.get("title") == "keep"
    )
    assert keep_chunk_after.id == keep_id_before
    # Vector count tracks the new chunk count exactly: removed wiped, added added.
    assert tq_size == 2


@pytest.mark.asyncio
async def test_reindex_null_hash_rows_self_heal(tmp_path: Path) -> None:
    """AC-8 — pre-migration NULL-hash rows are always treated as 'removed'.

    Seed a legacy row with ``content_hash = NULL`` directly via raw SQL
    (simulates a chunk row written before the diff-merge schema rolled out),
    then reindex with an empty chunk tuple. The diff must see the NULL-hash
    row as removed and delete it — the row count goes to zero.
    """
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO chunks (package, module, title, text, origin, "
            "content_hash) VALUES (?, ?, ?, ?, ?, NULL)",
            ("demo", "m", "t", "legacy body", "doc"),
        )
        conn.commit()
    finally:
        conn.close()

    factory = build_sqlite_uow_factory(db_path)
    svc = IndexingService(uow_factory=factory)
    # Reindex with no chunks — the diff sees the legacy row as 'removed'.
    await svc.reindex_package(_pkg("demo"), (), ())

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert pairs == ()


@pytest.mark.asyncio
async def test_reindex_sqlite_only_uow_works(tmp_path: Path) -> None:
    """AC-9 — SqliteUnitOfWork-only path (no ``.vectors`` attr) stays green.

    The diff-merge gates vector-side work behind ``getattr(uow, 'vectors',
    None)`` so the legacy SQLite-only deployment (no TurboQuant sidecar)
    runs without touching a vector store.
    """
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    svc = IndexingService(uow_factory=factory)
    chunks = (
        Chunk(text="a", metadata={"package": "demo", "title": "a"}),
        Chunk(text="b", metadata={"package": "demo", "title": "b"}),
    )
    await svc.reindex_package(_pkg("demo"), chunks, ())

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert len(pairs) == 2
    # Re-running with the same chunks is a no-op (diff sees them all).
    await svc.reindex_package(_pkg("demo"), chunks, ())
    async with factory() as uow:
        pairs_after = await uow.chunks.list_id_hash_pairs(
            filter={"package": "demo"},
        )
    assert {cid for cid, _ in pairs} == {cid for cid, _ in pairs_after}


@pytest.mark.asyncio
async def test_remove_package_wipes_vectors_atomically(tmp_path: Path) -> None:
    """AC-4: remove_package deletes chunks AND wipes their vectors."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    await svc.reindex_package(_pkg("pkg-a"), (
        Chunk(text="a1", metadata={"package": "pkg-a"}, embedding=_vec(0.1)),
        Chunk(text="a2", metadata={"package": "pkg-a"}, embedding=_vec(0.2)),
    ), ())
    await svc.reindex_package(_pkg("pkg-b"), (
        Chunk(text="b1", metadata={"package": "pkg-b"}, embedding=_vec(0.3)),
    ), ())

    async with factory() as uow:
        assert uow.vectors.size() == 3  # 2 + 1

    await svc.remove_package("pkg-a")

    async with factory() as uow:
        # pkg-a chunks gone; pkg-b chunks remain
        a_pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "pkg-a"})
        b_pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "pkg-b"})
        assert a_pairs == ()
        assert len(b_pairs) == 1
        # Vector count: only pkg-b's 1 vector left
        assert uow.vectors.size() == 1


@pytest.mark.asyncio
async def test_clear_all_wipes_vectors_atomically(tmp_path: Path) -> None:
    """AC-5: clear_all wipes both SQLite AND vectors."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)
    await svc.reindex_package(_pkg("demo"), (
        Chunk(text="a", metadata={"package": "demo"}, embedding=_vec(0.1)),
        Chunk(text="b", metadata={"package": "demo"}, embedding=_vec(0.2)),
    ), ())

    async with factory() as uow:
        assert uow.vectors.size() == 2

    await svc.clear_all()

    async with factory() as uow:
        assert await uow.packages.list() == []
        assert uow.vectors.size() == 0

    # The .tq file still exists (empty serialization, not unlinked)
    assert tq_path.exists()
