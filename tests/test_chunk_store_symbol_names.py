"""ChunkStore.list_symbol_names — the text-free, ordered miss-path projection.

Target resolution (get_symbol fallbacks) scans one package's distinct
``(qualified_name, module, source_path)`` triples. The scan is ORDER BY'd so a
``limit + 1`` read detects truncation deterministically; both implementers
(SQLite repo + the in-memory fake) must agree row for row.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, ChunkSymbolName
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from tests._fakes import InMemoryChunkStore

SymbolNameScan = Callable[[tuple[Chunk, ...], str, int], Awaitable[tuple[ChunkSymbolName, ...]]]


def _chunk(qname: str | None, *, package: str = "demo", module: str = "m", **md: object) -> Chunk:
    metadata: dict[str, object] = {"package": package, "module": module, **md}
    if qname is not None:
        metadata["qualified_name"] = qname
    return Chunk(text=f"body of {qname}", metadata=metadata)


async def _scan_sqlite(
    tmp_path: Path, chunks: tuple[Chunk, ...], package: str, limit: int
) -> tuple[ChunkSymbolName, ...]:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    async with factory() as uow:
        await uow.chunks.insert(chunks)
        await uow.commit()
    async with factory() as uow:
        return await uow.chunks.list_symbol_names(package, limit=limit)


async def _scan_in_memory(
    chunks: tuple[Chunk, ...], package: str, limit: int
) -> tuple[ChunkSymbolName, ...]:
    store = InMemoryChunkStore()
    await store.upsert(chunks)
    return await store.list_symbol_names(package, limit=limit)


@pytest.fixture(params=["sqlite", "in_memory"])
def scan_symbol_names(request: pytest.FixtureRequest, tmp_path: Path) -> SymbolNameScan:
    if request.param == "sqlite":
        return lambda chunks, package, limit: _scan_sqlite(tmp_path, chunks, package, limit)
    return _scan_in_memory


@pytest.mark.asyncio
async def test_rows_are_ordered_by_qualified_name(scan_symbol_names: SymbolNameScan) -> None:
    chunks = (_chunk("pkg.zeta"), _chunk("pkg.alpha"), _chunk("pkg.Mid"))
    rows = await scan_symbol_names(chunks, "demo", 10)
    assert [r.qualified_name for r in rows] == ["pkg.Mid", "pkg.alpha", "pkg.zeta"]


@pytest.mark.asyncio
async def test_duplicate_triples_collapse_to_one_row(scan_symbol_names: SymbolNameScan) -> None:
    # Two chunks of one symbol (e.g. a split body) share the whole triple.
    twin = {"source_path": "src/pkg/m.py"}
    chunks = (_chunk("pkg.m.Cls", **twin), _chunk("pkg.m.Cls", **twin))
    rows = await scan_symbol_names(chunks, "demo", 10)
    assert rows == (ChunkSymbolName("pkg.m.Cls", "m", "src/pkg/m.py"),)


@pytest.mark.asyncio
async def test_missing_and_empty_qualified_names_are_excluded(
    scan_symbol_names: SymbolNameScan,
) -> None:
    chunks = (_chunk(None), _chunk(""), _chunk("pkg.kept"))
    rows = await scan_symbol_names(chunks, "demo", 10)
    assert [r.qualified_name for r in rows] == ["pkg.kept"]


@pytest.mark.asyncio
async def test_limit_truncates_after_ordering(scan_symbol_names: SymbolNameScan) -> None:
    chunks = tuple(_chunk(f"pkg.n{i}") for i in (3, 0, 2, 1))
    capped = await scan_symbol_names(chunks, "demo", 2)
    assert [r.qualified_name for r in capped] == ["pkg.n0", "pkg.n1"]
    # limit + 1 pattern: asking for one more than the bound reveals truncation.
    probe = await scan_symbol_names(chunks, "demo", 3 + 1)
    assert len(probe) > 3


@pytest.mark.asyncio
async def test_other_packages_are_never_returned(scan_symbol_names: SymbolNameScan) -> None:
    chunks = (_chunk("pkg.mine"), _chunk("dep.theirs", package="other"))
    rows = await scan_symbol_names(chunks, "demo", 10)
    assert [r.qualified_name for r in rows] == ["pkg.mine"]


@pytest.mark.asyncio
async def test_absent_source_path_round_trips_as_none(scan_symbol_names: SymbolNameScan) -> None:
    rows = await scan_symbol_names((_chunk("pkg.m.f", module="pkg.m"),), "demo", 10)
    assert rows == (ChunkSymbolName("pkg.m.f", "pkg.m", None),)


@pytest.mark.asyncio
async def test_sqlite_null_qualified_name_legacy_row_is_excluded(tmp_path: Path) -> None:
    """Pre-v7 rows carry a NULL column (the write path stores '' instead)."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin, qualified_name) "
        "VALUES ('demo', 'm', 't', 'legacy', 'doc', NULL)"
    )
    conn.commit()
    conn.close()
    async with build_sqlite_uow_factory(db_path)() as uow:
        assert await uow.chunks.list_symbol_names("demo", limit=10) == ()
