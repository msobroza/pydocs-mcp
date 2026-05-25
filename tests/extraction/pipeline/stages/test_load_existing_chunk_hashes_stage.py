"""LoadExistingChunkHashesStage reads SQLite for the package's existing hashes.

Per spec Decision 5. Populates IngestionState.existing_chunk_hashes so
EmbedChunksStage can skip embedding chunks whose hash is already in the DB.
"""
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.pipeline.ingestion import IngestionState, TargetKind
from pydocs_mcp.extraction.pipeline.stages.load_existing_chunk_hashes import (
    LoadExistingChunkHashesStage,
)
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import build_sqlite_uow_factory


def _pkg(name: str) -> Package:
    """Build a Package with all required fields."""
    return Package(
        name=name,
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


@pytest.mark.asyncio
async def test_load_populates_existing_chunk_hashes(tmp_path: Path) -> None:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    # Seed 2 chunks for "demo" via insert (which persists content_hash).
    seeded = (
        Chunk(text="alpha", metadata={"package": "demo", "module": "m", "title": "t1"}),
        Chunk(text="beta", metadata={"package": "demo", "module": "m", "title": "t2"}),
    )
    async with factory() as uow:
        await uow.chunks.insert(seeded)
        await uow.commit()

    # Run the stage
    state = IngestionState(
        target=Path("demo"),
        target_kind=TargetKind.DEPENDENCY,
        package=_pkg("demo"),
        chunks=(Chunk(text="anything", metadata={"package": "demo"}),),  # presence triggers load
    )
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)

    assert out.existing_chunk_hashes is not None
    assert len(out.existing_chunk_hashes) == 2
    # Each value is a SQLite ID, each key is the chunk's SHA-256 hex hash
    for h, cid in out.existing_chunk_hashes.items():
        assert len(h) == 64
        assert isinstance(cid, int)


@pytest.mark.asyncio
async def test_load_no_op_when_no_chunks(tmp_path: Path) -> None:
    """No state.chunks → no read."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    state = IngestionState(
        target=Path("demo"),
        target_kind=TargetKind.DEPENDENCY,
        package=_pkg("demo"),
        chunks=(),
    )
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)
    assert out.existing_chunk_hashes is None or out.existing_chunk_hashes == {}


@pytest.mark.asyncio
async def test_load_no_op_when_uow_factory_none(tmp_path: Path) -> None:
    """Test-path: no composition root → uow_factory=None → stage skips DB."""
    state = IngestionState(
        target=Path("demo"),
        target_kind=TargetKind.DEPENDENCY,
        package=_pkg("demo"),
        chunks=(Chunk(text="x", metadata={"package": "demo"}),),
    )
    stage = LoadExistingChunkHashesStage(uow_factory=None)
    out = await stage.run(state)
    assert out.existing_chunk_hashes is None


@pytest.mark.asyncio
async def test_load_excludes_null_content_hash_rows(tmp_path: Path) -> None:
    """Pre-migration NULL rows must NOT appear in the skip set (AC-8)."""
    import sqlite3
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    # Insert a legacy NULL-hash row directly
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO chunks (package, module, title, text, origin) "
        "VALUES (?, ?, ?, ?, ?)",
        ("demo", "m", "t", "legacy", "doc"),
    )
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db_path)
    state = IngestionState(
        target=Path("demo"), target_kind=TargetKind.DEPENDENCY,
        package=_pkg("demo"),
        chunks=(Chunk(text="x", metadata={"package": "demo"}),),
    )
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)

    # The NULL-hash row is excluded so EmbedChunksStage will re-embed it
    assert out.existing_chunk_hashes == {}


def test_load_from_dict_raises_without_uow_factory_in_context() -> None:
    context = MagicMock(uow_factory=None)
    with pytest.raises(ValueError, match="uow_factory"):
        LoadExistingChunkHashesStage.from_dict({}, context)
