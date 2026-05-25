"""Startup integrity check detects + repairs chunks-vs-vectors mismatch (AC-25).

Composite SQLite + TurboQuant deployments can drift if a write lands in
one backend but not the other (process killed between commits, disk
full mid-flush, etc.). :func:`check_integrity_and_repair` compares
``SELECT COUNT(*) FROM chunks`` against
:meth:`TurboQuantUnitOfWork.size`. On mismatch it logs a warning and
clears ``packages.content_hash`` on every package so the next indexing
sweep re-extracts (and re-embeds) them. The fresh-project case
(both counts == 0) must not false-alarm.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, ChunkFilterField, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
    check_integrity_and_repair,
)

# turbovec requires dim % 8 == 0 and bit_width ∈ {2, 3, 4} — see
# tests/storage/test_turboquant_uow.py.
_DIM = 8
_BW = 4


def _pkg(name: str, content_hash: str = "abc123") -> Package:
    """Fully-formed Package — mirrors helpers in tests/storage/."""
    return Package(
        name=name,
        version="1.0",
        summary="",
        homepage="",
        dependencies=(),
        content_hash=content_hash,
        origin=PackageOrigin.DEPENDENCY,
    )


def _chunk(text: str, package: str) -> Chunk:
    """Minimal Chunk — id is auto-assigned by SQLite at upsert time."""
    return Chunk(text=text, metadata={ChunkFilterField.PACKAGE.value: package})


def _vec(*values: float) -> np.ndarray:
    """Pad/truncate ``values`` to a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


@pytest.mark.asyncio
async def test_integrity_check_clears_content_hash_on_size_mismatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    # Seed 3 chunks in SQLite but only 1 vector in TurboQuant — mismatch.
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert((
            _chunk("a", "demo"),
            _chunk("b", "demo"),
            _chunk("c", "demo"),
        ))
        # Re-fetch to discover the IDs SQLite auto-assigned.
        persisted = await uow.chunks.list(filter={"package": "demo"})
        first_id = sorted(persisted, key=lambda c: c.id or 0)[0].id
        await uow.vectors.add_vectors([first_id], [_vec(0.1, 0.2, 0.3, 0.4)])
        await uow.commit()

    caplog.set_level(logging.WARNING)
    repaired_pkg_names = await check_integrity_and_repair(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    assert "demo" in repaired_pkg_names
    # demo's content_hash was cleared so the next index sweep re-extracts.
    async with factory() as uow:
        pkgs = await uow.packages.list(filter={"name": "demo"})
        assert pkgs[0].content_hash in (None, "")
    assert any(
        "mismatch" in r.message.lower() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_integrity_check_passes_when_counts_match(tmp_path: Path) -> None:
    db_path = tmp_path / "y.db"
    tq_path = tmp_path / "y.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert((_chunk("a", "demo"),))
        persisted = await uow.chunks.list(filter={"package": "demo"})
        first_id = persisted[0].id
        await uow.vectors.add_vectors([first_id], [_vec(0.1, 0.2, 0.3, 0.4)])
        await uow.commit()
    repaired = await check_integrity_and_repair(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    assert repaired == []


@pytest.mark.asyncio
async def test_integrity_check_no_op_on_fresh_project(tmp_path: Path) -> None:
    """Both chunks=0 and vectors=0 → no repair needed (fresh project / never
    indexed). The integrity check must not false-alarm in this case because
    a fresh ``.tq`` file gets created on first ``__aenter__`` of
    ``TurboQuantUnitOfWork`` with ``size() == 0``, matching the empty
    ``chunks`` table."""
    db_path = tmp_path / "z.db"
    tq_path = tmp_path / "z.tq"
    open_index_database(db_path).close()
    repaired = await check_integrity_and_repair(
        db_path=db_path, tq_path=tq_path, dim=_DIM, bit_width=_BW,
    )
    assert repaired == []
