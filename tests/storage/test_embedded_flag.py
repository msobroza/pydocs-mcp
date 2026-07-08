"""chunks.embedded flag (schema v12): migration, stamping, integrity semantics.

The flag records INTENDED embeddings so the startup integrity check compares
vectors against it — selective embed policies (dependency doc pages only) are a
steady state, never mistaken for SQLite/.tq drift.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database
from pydocs_mcp.storage.factories import (
    build_sqlite_uow_factory,
    check_integrity_and_repair,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

_DIM = 8  # turbovec needs a multiple of 8
_BW = 4


def _insert_chunk(conn, package: str, title: str) -> int:
    cur = conn.execute(
        "INSERT INTO chunks(package, module, title, text, origin, content_hash) "
        "VALUES(?, '', ?, 'body', 'python_def', ?)",
        (package, title, f"h-{title}"),
    )
    return cur.lastrowid


def test_fresh_chunks_default_unembedded(tmp_path: Path) -> None:
    conn = open_index_database(tmp_path / "x.db")
    _insert_chunk(conn, "pkg", "a")
    row = conn.execute("SELECT embedded FROM chunks").fetchone()
    assert row["embedded"] == 0


def test_v11_upgrade_backfills_embedded(tmp_path: Path) -> None:
    # Build a v12 db, then simulate a v11 db: drop the column + stamp 11.
    db = tmp_path / "legacy.db"
    conn = open_index_database(db)
    _insert_chunk(conn, "pkg", "a")
    _insert_chunk(conn, "pkg", "b")
    conn.execute("ALTER TABLE chunks DROP COLUMN embedded")
    conn.execute("PRAGMA user_version = 11")
    conn.commit()
    conn.close()

    conn2 = open_index_database(db)  # reopen -> 11 -> 12 upgrade
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    # Pre-v12 rows were written under the embed-everything policy -> backfilled 1.
    rows = conn2.execute("SELECT embedded FROM chunks").fetchall()
    assert [r["embedded"] for r in rows] == [1, 1]


def test_reopen_does_not_rebackfill_selective_flags(tmp_path: Path) -> None:
    # Flags written under a selective policy must survive a v12-on-open sweep.
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    _insert_chunk(conn, "pkg", "a")  # embedded=0 (deliberately unembedded)
    conn.commit()
    conn.close()
    conn2 = open_index_database(db)
    assert conn2.execute("SELECT embedded FROM chunks").fetchone()["embedded"] == 0


@pytest.mark.asyncio
async def test_mark_embedded_flips_rows(tmp_path: Path) -> None:
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    id_a = _insert_chunk(conn, "pkg", "a")
    id_b = _insert_chunk(conn, "pkg", "b")
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db)
    async with factory() as uow:
        await uow.chunks.mark_embedded([id_a])
        await uow.commit()

    conn = open_index_database(db)
    flags = {
        r["id"]: r["embedded"] for r in conn.execute("SELECT id, embedded FROM chunks").fetchall()
    }
    assert flags[id_a] == 1 and flags[id_b] == 0


async def _write_vectors(tq_path: Path, ids: list[int]) -> None:
    async with TurboQuantUnitOfWork(index_path=tq_path, dim=_DIM, bit_width=_BW) as tq:
        rng = np.random.default_rng(0)
        await tq.add_vectors(ids, [rng.standard_normal(_DIM).astype(np.float32) for _ in ids])
        await tq.commit()


@pytest.mark.asyncio
async def test_integrity_partial_embedding_is_steady_state(tmp_path: Path) -> None:
    """THE fix: deliberately-unembedded chunks (embedded=0) never read as drift."""
    db, tq = tmp_path / "x.db", tmp_path / "x.tq"
    conn = open_index_database(db)
    id_doc = _insert_chunk(conn, "torch", "doc-page")  # will be embedded
    _insert_chunk(conn, "torch", "code-1")  # deliberately NOT embedded
    _insert_chunk(conn, "torch", "code-2")
    conn.execute("UPDATE chunks SET embedded = 1 WHERE id = ?", (id_doc,))
    conn.execute("INSERT INTO packages(name, content_hash) VALUES('torch', 'h')")
    conn.commit()
    conn.close()
    await _write_vectors(tq, [id_doc])  # exactly the embedded subset

    repaired = await check_integrity_and_repair(db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW)
    assert repaired == []  # no repair — and on a second run either (stable)
    repaired2 = await check_integrity_and_repair(db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW)
    assert repaired2 == []
    # content_hash untouched (no clear-all loop)
    conn = open_index_database(db)
    assert conn.execute("SELECT content_hash FROM packages").fetchone()[0] == "h"


@pytest.mark.parametrize("n", [500, 501, 1000])
@pytest.mark.asyncio
async def test_mark_embedded_flips_every_row_across_batch_boundary(tmp_path: Path, n: int) -> None:
    """mark_embedded batches at 500 (SQLITE_MAX_VARIABLE_NUMBER headroom).

    range(0, len(ids), 500) must not drop the tail batch — exercise exactly
    at (500), just past (501), and two full batches (1000) so an off-by-one
    in the slicing loop cannot silently leave a partial batch unflagged.
    """
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    ids = [_insert_chunk(conn, "pkg", f"c{i}") for i in range(n)]
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db)
    async with factory() as uow:
        await uow.chunks.mark_embedded(ids)
        await uow.commit()

    conn = open_index_database(db)
    count = conn.execute("SELECT COUNT(*) FROM chunks WHERE embedded = 1").fetchone()[0]
    assert count == n


@pytest.mark.parametrize("n", [500, 501, 1000])
@pytest.mark.asyncio
async def test_delete_by_ids_removes_every_row_across_batch_boundary(
    tmp_path: Path, n: int
) -> None:
    """delete_by_ids shares the same 500-row batching loop as mark_embedded.

    Same boundary sweep (500 / 501 / 1000) — a tail-batch bug here would
    silently leave stale rows behind instead of the deletion being total.
    """
    db = tmp_path / "x.db"
    conn = open_index_database(db)
    ids = [_insert_chunk(conn, "pkg", f"c{i}") for i in range(n)]
    conn.commit()
    conn.close()

    factory = build_sqlite_uow_factory(db)
    async with factory() as uow:
        await uow.chunks.delete_by_ids(ids)
        await uow.commit()

    conn = open_index_database(db)
    remaining = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert remaining == 0


@pytest.mark.asyncio
async def test_integrity_real_drift_still_repairs(tmp_path: Path) -> None:
    """Two chunks CLAIM vectors but only one landed (crash drift) -> repair fires."""
    db, tq = tmp_path / "x.db", tmp_path / "x.tq"
    conn = open_index_database(db)
    id_a = _insert_chunk(conn, "pkg", "a")
    _insert_chunk(conn, "pkg", "b")
    conn.execute("UPDATE chunks SET embedded = 1")  # both claim vectors
    conn.execute("INSERT INTO packages(name, content_hash) VALUES('pkg', 'h')")
    conn.commit()
    conn.close()
    await _write_vectors(tq, [id_a])  # only one vector actually landed

    repaired = await check_integrity_and_repair(db_path=db, tq_path=tq, dim=_DIM, bit_width=_BW)
    assert repaired == ["pkg"]
    conn = open_index_database(db)
    assert conn.execute("SELECT content_hash FROM packages").fetchone()[0] is None
