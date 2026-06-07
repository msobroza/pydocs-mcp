"""qualified_name (schema v7) survives the SQLite chunk round-trip.

Regression for the tree-reasoning 0%-recall bug: ``llm_tree_reasoning`` joins
LLM-picked nodes to chunks by ``metadata["qualified_name"]``, but the field was
dropped at the SQLite boundary, so every store-loaded chunk read it back as
``None``. These tests drive a ``Chunk`` through the REAL ``SqliteChunkRepository``
(not the in-memory fake, which round-trips ``Chunk`` verbatim and so hid the bug).
"""

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


@pytest.mark.asyncio
async def test_qualified_name_round_trips_through_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    chunk = Chunk(
        text="def foo(): ...",
        metadata={"package": "__project__", "qualified_name": "pkg.mod.foo"},
    )
    async with factory() as uow:
        await uow.chunks.upsert((chunk,))
        await uow.commit()

    async with factory() as uow:
        loaded = await uow.chunks.list(filter={"package": "__project__"})

    assert len(loaded) == 1
    assert loaded[0].metadata.get("qualified_name") == "pkg.mod.foo"


@pytest.mark.asyncio
async def test_chunk_without_qualified_name_omits_the_key(tmp_path: Path) -> None:
    """A chunk with no qualified_name reads back without the key (not '')."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.upsert((Chunk(text="x", metadata={"package": "p"}),))
        await uow.commit()
    async with factory() as uow:
        loaded = await uow.chunks.list(filter={"package": "p"})

    assert len(loaded) == 1
    assert "qualified_name" not in loaded[0].metadata
