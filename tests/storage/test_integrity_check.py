"""Startup integrity check detects + repairs chunks-vs-vectors mismatch (AC-25).

Composite SQLite + TurboQuant deployments can drift if a write lands in
one backend but not the other (process killed between commits, disk
full mid-flush, etc.). :func:`check_integrity_and_repair` compares
``SELECT COUNT(*) FROM chunks WHERE embedded = 1`` (INTENDED embeddings —
the vector-write path stamps the flag in the same transaction) against
:meth:`TurboQuantUnitOfWork.size`. On mismatch it logs a warning and
clears ``packages.content_hash`` on every package so the next indexing
sweep re-extracts (and re-embeds) them. The fresh-project case
(both counts == 0) must not false-alarm, and deliberately-unembedded
chunks (selective embed policies) never count as drift — see
tests/storage/test_embedded_flag.py.
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
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    # Seed 3 chunks that all CLAIM embeddings but only 1 vector actually
    # landed in TurboQuant (crash between the flag commit and the .tq
    # flush) — genuine drift.
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert(
            (
                _chunk("a", "demo"),
                _chunk("b", "demo"),
                _chunk("c", "demo"),
            )
        )
        # Re-fetch to discover the IDs SQLite auto-assigned.
        persisted = await uow.chunks.list(filter={"package": "demo"})
        all_ids = sorted(c.id for c in persisted if c.id is not None)
        await uow.vectors.add_vectors([all_ids[0]], [_vec(0.1, 0.2, 0.3, 0.4)])
        await uow.chunks.mark_embedded(all_ids)  # all 3 claim a vector
        await uow.commit()

    caplog.set_level(logging.WARNING)
    repaired_pkg_names = await check_integrity_and_repair(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    assert "demo" in repaired_pkg_names
    # demo's content_hash was cleared so the next index sweep re-extracts.
    async with factory() as uow:
        pkgs = await uow.packages.list(filter={"name": "demo"})
        assert pkgs[0].content_hash in (None, "")
    assert any("mismatch" in r.message.lower() for r in caplog.records)


@pytest.mark.asyncio
async def test_integrity_check_passes_when_counts_match(tmp_path: Path) -> None:
    db_path = tmp_path / "y.db"
    tq_path = tmp_path / "y.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert((_chunk("a", "demo"),))
        persisted = await uow.chunks.list(filter={"package": "demo"})
        first_id = persisted[0].id
        await uow.vectors.add_vectors([first_id], [_vec(0.1, 0.2, 0.3, 0.4)])
        # Mirror the production vector-write path (post-v12): the same UoW
        # stamps chunks.embedded so the integrity count matches the .tq.
        await uow.chunks.mark_embedded([first_id])
        await uow.commit()
    repaired = await check_integrity_and_repair(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    assert repaired == []


@pytest.mark.asyncio
async def test_integrity_check_misses_disjoint_id_drift_with_matching_counts(
    tmp_path: Path,
) -> None:
    """Count-based check is blind to stale .tq ids colliding with reused
    chunk rowids (gap: storage — "Stale .tq vector ids collide with reused
    chunk rowids; the count-based integrity check cannot see this drift").

    ``chunks.id`` is ``INTEGER PRIMARY KEY`` without ``AUTOINCREMENT``
    (db.py), so SQLite reuses rowids after a delete. Simulate the
    documented non-ACID crash window (SQLite child committed, TurboQuant
    commit not reached — CompositeUnitOfWork docstring) by writing
    TurboQuant vectors under an OLD id set ({101, 102}) directly, then
    persisting chunks with a DISJOINT NEW id set ({1, 2}, SQLite's natural
    reissue for an empty table) that both claim ``embedded = 1``. The two
    backends have the SAME count (2 == 2) but disjoint identities — dense
    search would silently hydrate the old vector content onto the new
    chunk rows (wrong relevance, no error).

    Today's count-only check cannot see this: ``embedded_count == vec_count``
    so it returns ``[]`` even though the ids never overlap. This test pins
    that blind spot; a future content-aware check (id-set comparison, not
    just counts) should turn this red.
    """
    db_path = tmp_path / "w.db"
    tq_path = tmp_path / "w.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    # Old generation: vectors land under ids {101, 102} in TurboQuant only
    # (simulates a prior index run whose chunk rows have since been
    # deleted — SQLite's rowid counter resets for an empty table, so
    # those high ids are now permanently free for a DIFFERENT future
    # generation to reuse at low numbers; deliberately chosen far from
    # SQLite's next-assigned {1, 2} so overlap here can only mean the
    # check is comparing identities, not merely counts).
    async with factory() as uow:
        await uow.vectors.add_vectors(
            [101, 102],
            [_vec(0.1, 0.2, 0.3, 0.4), _vec(0.5, 0.6, 0.7, 0.8)],
        )
        await uow.commit()
    # New generation: SQLite alone commits fresh chunks into the still-empty
    # ``chunks`` table. ``INTEGER PRIMARY KEY`` without AUTOINCREMENT
    # (db.py) means SQLite reissues rowids starting at 1 for an empty
    # table — ids {1, 2}, disjoint from the TurboQuant ids {101, 102}
    # above. This is the exact mechanism the gap describes: a crash left
    # stale vectors under old ids, and the counts now match (2 == 2) even
    # though the identities never overlap.
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert(
            (
                _chunk("a", "demo"),
                _chunk("b", "demo"),
            )
        )
        persisted = await uow.chunks.list(filter={"package": "demo"})
        new_ids = sorted(c.id for c in persisted if c.id is not None)
        assert set(new_ids).isdisjoint({101, 102}), (
            "test setup requires disjoint ids to reproduce the drift; "
            f"got overlapping ids {new_ids}"
        )
        await uow.chunks.mark_embedded(new_ids)
        await uow.commit()

    repaired = await check_integrity_and_repair(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    # DESIRED: drift should be flagged (repaired == ["demo"]) because the
    # embedded chunk ids and the TurboQuant vector ids are disjoint sets
    # despite equal counts. CURRENT behavior: the count-only check sees
    # 2 == 2 and passes silently — pin that gap explicitly here.
    assert repaired == [], (
        "count-based check unexpectedly detected disjoint-id drift; "
        "if this now fails, the integrity check has been upgraded to "
        "compare id sets — update this test to assert repaired == ['demo']."
    )


@pytest.mark.asyncio
async def test_integrity_check_repairs_corrupt_tq_instead_of_raising(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A truncated/corrupt ``.tq`` sidecar (disk trouble, hand-edited, or
    written by an incompatible turbovec version) must be treated as
    repairable drift, not crash startup.

    ``IdMapIndex.load`` raises ``OSError('not a TVIM file: wrong magic')``
    for any file that exists but isn't a valid TVIM container (empirically
    confirmed against the real turbovec extension). Before the fix,
    ``check_integrity_and_repair`` — whose entire job is repairing sidecar
    drift at startup — let that ``OSError`` propagate straight out of
    ``TurboQuantUnitOfWork.__aenter__``, aborting startup for the one
    input this function exists to protect against.
    """
    db_path = tmp_path / "corrupt.db"
    tq_path = tmp_path / "corrupt.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    async with factory() as uow:
        await uow.packages.upsert(_pkg("demo"))
        await uow.chunks.upsert((_chunk("a", "demo"),))
        persisted = await uow.chunks.list(filter={"package": "demo"})
        first_id = persisted[0].id
        assert first_id is not None
        await uow.chunks.mark_embedded([first_id])
        await uow.commit()
    # Truncate/corrupt the .tq sidecar AFTER it would have been written —
    # simulates disk trouble, a hand-edit, or an incompatible turbovec
    # version, per the gap's edge case.
    tq_path.write_bytes(b"not a real tq file, just garbage bytes")

    caplog.set_level(logging.WARNING)
    repaired_pkg_names = await check_integrity_and_repair(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    assert "demo" in repaired_pkg_names
    async with factory() as uow:
        pkgs = await uow.packages.list(filter={"name": "demo"})
        assert pkgs[0].content_hash in (None, "")


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
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    assert repaired == []
