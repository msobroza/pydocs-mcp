"""SQLite-flavored CandidateIdResolver + ChunkHydrator (spec §5.3, §7 risk row 1)."""

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
from pydocs_mcp.storage.filters import FieldEq


def _pkg(name: str) -> Package:
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
    # ``id`` is left unset — SqliteChunkRepository.upsert auto-assigns ids via
    # SQLite's INTEGER PRIMARY KEY. The test queries the resulting ids back
    # via the same FieldEq("package", ...) filter the resolver uses.
    return Chunk(text=text, metadata={ChunkFilterField.PACKAGE.value: package})


async def _seed(tmp_path: Path) -> Path:
    db_path = tmp_path / "seed.db"
    open_index_database(db_path).close()
    factory = build_sqlite_uow_factory(db_path)
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.packages.upsert(_pkg("other"))
        await uow.chunks.upsert(
            (
                _chunk("alpha", "demo"),
                _chunk("beta", "demo"),
                _chunk("gamma", "other"),
            )
        )
        await uow.commit()
    return db_path


async def test_candidate_id_resolver_returns_uint64_ids_matching_filter(
    tmp_path: Path,
) -> None:
    db_path = await _seed(tmp_path)
    resolver = build_sqlite_candidate_id_resolver(db_path)
    ids = await resolver(FieldEq("package", "demo"))
    assert isinstance(ids, np.ndarray)
    assert ids.dtype == np.uint64
    # Two "demo" chunks were seeded — their ids (whatever SQLite auto-assigned)
    # must match what `SqliteChunkRepository.list(filter=...)` returns.
    factory = build_sqlite_uow_factory(db_path)
    async with factory() as uow:
        demo_chunks = await uow.chunks.list(filter={"package": "demo"})
    expected = {c.id for c in demo_chunks}
    assert set(ids.tolist()) == expected
    assert len(expected) == 2


async def test_candidate_id_resolver_with_no_matches_returns_empty_uint64_array(
    tmp_path: Path,
) -> None:
    db_path = await _seed(tmp_path)
    resolver = build_sqlite_candidate_id_resolver(db_path)
    ids = await resolver(FieldEq("package", "nope"))
    assert isinstance(ids, np.ndarray)
    assert ids.dtype == np.uint64
    assert ids.shape == (0,)


async def test_chunk_hydrator_returns_chunks_for_given_ids(
    tmp_path: Path,
) -> None:
    db_path = await _seed(tmp_path)
    # Use the repository to discover which ids SQLite assigned; the hydrator
    # contract is "give me these ids back as Chunks", not "guess SQLite's
    # autoincrement".
    factory = build_sqlite_uow_factory(db_path)
    async with factory() as uow:
        all_chunks = await uow.chunks.list()
    by_text = {c.text: c for c in all_chunks}
    beta_id = by_text["beta"].id
    gamma_id = by_text["gamma"].id

    hydrator = build_sqlite_chunk_hydrator(db_path)
    chunks = await hydrator([beta_id, gamma_id])
    assert len(chunks) == 2
    by_id = {c.id: c for c in chunks}
    assert by_id[beta_id].text == "beta"
    assert by_id[gamma_id].text == "gamma"
    # Metadata round-trips through the same row_to_chunk path the repos use.
    assert by_id[beta_id].metadata.get("package") == "demo"
    assert by_id[gamma_id].metadata.get("package") == "other"


async def test_chunk_hydrator_empty_ids_returns_empty_tuple(
    tmp_path: Path,
) -> None:
    db_path = await _seed(tmp_path)
    hydrator = build_sqlite_chunk_hydrator(db_path)
    chunks = await hydrator([])
    assert chunks == ()


@pytest.mark.parametrize("safe_column", sorted({"package", "module", "origin", "title"}))
async def test_candidate_id_resolver_accepts_every_safe_chunk_column(
    tmp_path: Path,
    safe_column: str,
) -> None:
    """The resolver must use the canonical chunk safe-column whitelist —
    rejecting a known-safe column would break TurboQuantVectorStore filters."""
    db_path = await _seed(tmp_path)
    resolver = build_sqlite_candidate_id_resolver(db_path)
    # Empty result is fine; the assertion is "no ValueError from the adapter".
    ids = await resolver(FieldEq(safe_column, "value_that_will_not_match"))
    assert isinstance(ids, np.ndarray)
    assert ids.dtype == np.uint64
