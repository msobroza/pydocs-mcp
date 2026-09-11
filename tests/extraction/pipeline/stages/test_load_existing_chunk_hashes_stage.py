"""LoadExistingChunkHashesStage reads SQLite for the package's existing hashes.

Per spec Decision 5. Populates IngestionState.existing_chunk_hashes so
EmbedChunksStage can skip embedding chunks whose hash is already in the DB.

Every state below leaves ``package=None`` and carries the package identity
in ``files.package_name`` — the shape the shipped ingestion presets
actually produce at this point in the run, since ``package_build`` is
their last stage. Modelling it the other way round is what let the
stage's permanent no-op (skip set never populated → every pass
re-embedded everything) sit green here; see
tests/integration/test_embed_skip_across_index_passes.py.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.extraction.pipeline.ingestion import (
    ChunkBundle,
    FileBundle,
    IngestionState,
    TargetKind,
)
from pydocs_mcp.extraction.pipeline.stages.load_existing_chunk_hashes import (
    LoadExistingChunkHashesStage,
)
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import build_sqlite_uow_factory

# Capture the real ``from_dict`` at import time, before the conftest-level
# autouse fixture replaces it with a permissive stub (the stub bypasses the
# ``uow_factory`` strict check so production-wired pipeline builds in CLI /
# integration tests don't explode). The strict-mode unit test below needs
# the real classmethod to verify the ValueError contract.
_REAL_FROM_DICT = LoadExistingChunkHashesStage.from_dict


def _state(
    chunks: tuple[Chunk, ...],
    *,
    package_name: str = "demo",
) -> IngestionState:
    """A state shaped like the one the shipped presets reach this stage with."""
    return IngestionState(
        files=FileBundle(
            target=Path("demo"),
            target_kind=TargetKind.DEPENDENCY,
            package_name=package_name,
        ),
        chunks=ChunkBundle(chunks=chunks),
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

    # Run the stage — ``package`` is None, exactly as in a real run.
    state = _state((Chunk(text="anything", metadata={"package": "demo"}),))
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)

    assert out.existing_chunk_hashes is not None
    assert len(out.existing_chunk_hashes) == 2
    # Each value is a SQLite ID, each key is the chunk's SHA-256 hex hash
    for h, cid in out.existing_chunk_hashes.items():
        assert len(h) == 64
        assert isinstance(cid, int)


@pytest.mark.asyncio
async def test_load_scopes_to_its_own_package(tmp_path: Path) -> None:
    """Another package's hashes must never reach this package's skip set.

    The skip set has to match what ``IndexingService._diff_merge_chunks``
    keeps — and that diff is scoped by package. A foreign hash leaking in
    would let the embed gate skip a chunk the diff-merge then inserts as
    new, persisting it with no vector at all.
    """
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    async with factory() as uow:
        await uow.chunks.insert(
            (Chunk(text="alpha", metadata={"package": "other", "module": "m", "title": "t"}),),
        )
        await uow.commit()

    state = _state((Chunk(text="anything", metadata={"package": "demo"}),))
    out = await LoadExistingChunkHashesStage(uow_factory=factory).run(state)

    assert out.existing_chunk_hashes == {}


@pytest.mark.asyncio
async def test_load_no_op_when_no_chunks(tmp_path: Path) -> None:
    """No state.chunks.chunks → no read."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(_state(()))
    assert out.existing_chunk_hashes is None or out.existing_chunk_hashes == {}


@pytest.mark.asyncio
async def test_load_no_op_when_uow_factory_none() -> None:
    """Test-path: no composition root → uow_factory=None → stage skips DB."""
    state = _state((Chunk(text="x", metadata={"package": "demo"}),))
    stage = LoadExistingChunkHashesStage(uow_factory=None)
    out = await stage.run(state)
    assert out.existing_chunk_hashes is None


@pytest.mark.asyncio
async def test_load_no_op_when_package_name_missing(tmp_path: Path) -> None:
    """Nothing to scope the query to → skip the read rather than query for ''."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    state = _state((Chunk(text="x", metadata={"package": "demo"}),), package_name="")
    out = await LoadExistingChunkHashesStage(uow_factory=factory).run(state)
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
        "INSERT INTO chunks (package, module, title, text, origin) VALUES (?, ?, ?, ?, ?)",
        ("demo", "m", "t", "legacy", "doc"),
    )
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db_path)
    state = _state((Chunk(text="x", metadata={"package": "demo"}),))
    stage = LoadExistingChunkHashesStage(uow_factory=factory)
    out = await stage.run(state)

    # The NULL-hash row is excluded so EmbedChunksStage will re-embed it
    assert out.existing_chunk_hashes == {}


def test_load_from_dict_raises_without_uow_factory_in_context() -> None:
    # Use the captured pre-stub classmethod — the conftest-level autouse
    # fixture replaces ``LoadExistingChunkHashesStage.from_dict`` with a
    # permissive stub for the duration of every test; verifying the strict
    # production contract requires the original.
    context = MagicMock(uow_factory=None)
    with pytest.raises(ValueError, match="uow_factory"):
        _REAL_FROM_DICT({}, context)


def test_load_to_dict_round_trips() -> None:
    """The stage carries no tunables, so its YAML form is just its type tag.

    ``uow_factory`` is wiring supplied by the composition root at decode time,
    never serialized — round-tripping it would bake a live handle into a config
    file.
    """
    assert LoadExistingChunkHashesStage(uow_factory=None).to_dict() == {
        "type": "load_existing_chunk_hashes"
    }


@pytest.mark.asyncio
async def test_load_counts_persisted_copies_per_hash(tmp_path: Path) -> None:
    """Duplicate-hash rows (#69) must surface as a count, not collapse to one."""
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)

    dup = {"package": "demo", "module": "m", "title": "t"}
    async with factory() as uow:
        await uow.chunks.insert(
            (Chunk(text="same", metadata=dup), Chunk(text="same", metadata=dup))
        )
        await uow.commit()

    out = await LoadExistingChunkHashesStage(uow_factory=factory).run(
        _state((Chunk(text="anything", metadata={"package": "demo"}),))
    )

    assert out.existing_chunk_hashes is not None
    assert list(out.existing_chunk_hashes.values()) == [2]
