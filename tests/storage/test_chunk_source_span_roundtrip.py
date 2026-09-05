"""Chunk source spans (schema v15) survive the SQLite round-trip.

The tool-contracts items[] fields (``path`` / ``start_line`` / ``end_line``)
hydrate from ``Chunk.metadata`` — the spans are computed by extraction
(``DocumentNode``) but were dropped at the SQLite persistence boundary, so
every store-loaded chunk read them back absent. These tests drive a ``Chunk``
through the mappers and the REAL ``SqliteChunkRepository`` (the in-memory fake
round-trips ``Chunk`` verbatim and would hide the bug).
"""

from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from pydocs_mcp.storage.sqlite.row_mappers import _chunk_to_row, row_to_chunk

_SPAN_METADATA = {
    "package": "__project__",
    "qualified_name": "pkg.mod.foo",
    "source_path": "pkg/mod.py",
    "start_line": 3,
    "end_line": 17,
}


def test_chunk_to_row_carries_span_columns() -> None:
    row = _chunk_to_row(Chunk(text="def foo(): ...", metadata=_SPAN_METADATA))
    assert row["source_path"] == "pkg/mod.py"
    assert row["start_line"] == 3
    assert row["end_line"] == 17


def test_row_to_chunk_restores_span_metadata() -> None:
    row = _chunk_to_row(Chunk(text="def foo(): ...", metadata=_SPAN_METADATA))
    loaded = row_to_chunk(row)
    assert loaded.metadata["source_path"] == "pkg/mod.py"
    assert loaded.metadata["start_line"] == 3
    assert loaded.metadata["end_line"] == 17


def test_row_to_chunk_omits_absent_spans() -> None:
    row = _chunk_to_row(Chunk(text="x", metadata={"package": "p"}))
    loaded = row_to_chunk(row)
    assert "source_path" not in loaded.metadata
    assert "start_line" not in loaded.metadata
    assert "end_line" not in loaded.metadata


def test_spans_do_not_change_content_hash() -> None:
    """No re-embed storm: the hash covers (package, module, title, text,
    pipeline_hash) only, so adding spans to metadata must not shift it."""
    bare = Chunk(text="body", metadata={"package": "p", "module": "m", "title": "t"})
    spanned = Chunk(
        text="body",
        metadata={
            "package": "p",
            "module": "m",
            "title": "t",
            "source_path": "p/m.py",
            "start_line": 1,
            "end_line": 9,
        },
    )
    assert bare.content_hash == spanned.content_hash


@pytest.mark.asyncio
async def test_spans_round_trip_through_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.upsert((Chunk(text="def foo(): ...", metadata=_SPAN_METADATA),))
        await uow.commit()

    async with factory() as uow:
        loaded = await uow.chunks.list(filter={"package": "__project__"})

    assert len(loaded) == 1
    md = loaded[0].metadata
    assert md.get("source_path") == "pkg/mod.py"
    assert md.get("start_line") == 3
    assert md.get("end_line") == 17


@pytest.mark.asyncio
async def test_chunk_without_spans_omits_the_keys(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.upsert((Chunk(text="x", metadata={"package": "p"}),))
        await uow.commit()
    async with factory() as uow:
        loaded = await uow.chunks.list(filter={"package": "p"})

    assert len(loaded) == 1
    for key in ("source_path", "start_line", "end_line"):
        assert key not in loaded[0].metadata
