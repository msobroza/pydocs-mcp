"""ChunkStore.list_id_hash_pairs / delete_by_ids / insert + content_hash round-trip.

Per spec Decision 7. Lightweight (id, content_hash) fetch for diff-merge.
delete_by_ids removes only specific rows (vs delete(package=X) which wipes
the whole package). insert is the additive path (vs upsert's silent
overwrite).
"""
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


@pytest.mark.asyncio
async def test_insert_then_list_id_hash_pairs_returns_assigned_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    chunks = (
        Chunk(text="alpha", metadata={"package": "demo", "module": "m", "title": "t1"}),
        Chunk(text="beta", metadata={"package": "demo", "module": "m", "title": "t2"}),
    )
    async with factory() as uow:
        await uow.chunks.insert(chunks)
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(
            filter={"package": "demo"},
        )

    assert len(pairs) == 2
    for cid, h in pairs:
        assert isinstance(cid, int)
        assert isinstance(h, str)
        assert len(h) == 64  # SHA-256 hex


@pytest.mark.asyncio
async def test_delete_by_ids_removes_only_requested(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.insert((
            Chunk(text="a", metadata={"package": "demo", "title": "t1"}),
            Chunk(text="b", metadata={"package": "demo", "title": "t2"}),
            Chunk(text="c", metadata={"package": "demo", "title": "t3"}),
        ))
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
        all_ids = [cid for cid, _ in pairs]
        await uow.chunks.delete_by_ids([all_ids[0]])
        await uow.commit()

    async with factory() as uow:
        remaining = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})

    assert len(remaining) == 2
    assert all_ids[0] not in {cid for cid, _ in remaining}


@pytest.mark.asyncio
async def test_delete_by_ids_empty_list_is_no_op(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.insert((Chunk(text="a", metadata={"package": "demo"}),))
        await uow.commit()

    async with factory() as uow:
        await uow.chunks.delete_by_ids([])  # empty list → no-op
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert len(pairs) == 1


@pytest.mark.asyncio
async def test_list_id_hash_pairs_returns_null_for_pre_migration_rows(tmp_path: Path) -> None:
    """A row inserted via legacy upsert (no content_hash) shows hash=None.

    The diff-merge treats None as 'removed' so pre-existing rows
    self-heal on the first reindex per package (spec AC-8).
    """
    import sqlite3
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    # Insert a row directly via legacy SQL (no content_hash column populated)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin) "
        "VALUES (?, ?, ?, ?, ?)",
        ("demo", "m", "t", "legacy text", "doc"),
    )
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db_path)
    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})

    assert len(pairs) == 1
    cid, h = pairs[0]
    assert isinstance(cid, int)
    assert h is None or h == ""  # legacy row has NULL/empty hash


@pytest.mark.asyncio
async def test_insert_persists_content_hash(tmp_path: Path) -> None:
    """The content_hash field on each Chunk is written to SQLite."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    explicit_hash = "abc" * 21 + "d"  # 64 chars
    chunk = Chunk(
        text="hello",
        metadata={"package": "demo"},
        content_hash=explicit_hash,
    )
    async with factory() as uow:
        await uow.chunks.insert((chunk,))
        await uow.commit()

    async with factory() as uow:
        pairs = await uow.chunks.list_id_hash_pairs(filter={"package": "demo"})
    assert pairs[0][1] == explicit_hash
