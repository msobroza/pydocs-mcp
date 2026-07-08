"""Regression: a chunk with malformed/missing ``package`` metadata must not
silently disable vector writes for an entire package, nor accumulate
undeletable orphan rows on repeated reindexes.

Gap: ``_chunk_to_row`` (storage/sqlite/row_mappers.py) defaults a missing
``metadata["package"]`` to ``""``. ``_diff_merge_chunks`` and
``_maybe_write_vectors`` (application/indexing_service.py) both re-fetch
persisted rows filtered on ``package.name`` — so a malformed chunk's row,
persisted under ``package=""``, is invisible to that re-fetch. The resulting
length mismatch used to trip ``_maybe_write_vectors``'s defensive guard,
which skipped forwarding embeddings for the ENTIRE batch (not just the
offending chunk) — one bad chunk blinded dense search for every well-formed
sibling in the same reindex. The ``""``-package row was also permanently
orphaned: ``remove_package(name)`` filters strictly on ``package.name`` and
can never see or delete it (only ``clear_all`` sweeps unconditionally, per
``test_indexing_service_clear_all_also_removes_null_package_rows``).

Fix: ``reindex_package`` now validates every incoming chunk's
``metadata["package"]`` against ``package.name`` BEFORE opening a UoW /
writing anything, and raises ``ValueError`` naming the offending chunk. This
turns a silent whole-batch vector blackout + unbounded orphan accumulation
into a loud, immediate, atomic failure — nothing is written for a batch that
contains a malformed chunk.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import build_sqlite_plus_turboquant_uow_factory
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

# turbovec requires dim multiple of 8 and bit_width in {2, 3, 4} — mirrors
# tests/application/test_indexing_writes_vectors.py.
_DIM = 8
_BW = 4


def _pkg(name: str = "demo") -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _vec(*values: float) -> np.ndarray:
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


def _count_empty_package_chunks(db_path: Path) -> int:
    conn = open_index_database(db_path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE package = '' OR package IS NULL"
        ).fetchone()[0]
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_chunk_missing_package_metadata_raises_and_writes_nothing(
    tmp_path: Path,
) -> None:
    """One malformed chunk (no 'package' key) alongside one well-formed,
    embedded chunk in the same ``reindex_package`` call must reject the
    WHOLE batch loudly, with the offending chunk identifiable from the
    error — not silently persist an orphan row + blind vector writes for
    the well-formed sibling.
    """
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    package = _pkg("demo")

    # Well-formed: correct package, carries an embedding.
    good_chunk = Chunk(
        text="alpha body",
        embedding=_vec(0.1, 0.2, 0.3, 0.4),
        metadata={"package": "demo", "title": "alpha"},
    )
    # Malformed: no 'package' key at all -> would have persisted under package="".
    malformed_chunk = Chunk(
        text="beta body",
        embedding=_vec(0.5, 0.6, 0.7, 0.8),
        metadata={"title": "beta"},
    )

    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    with pytest.raises(ValueError, match="beta"):
        await svc.reindex_package(
            package,
            (good_chunk, malformed_chunk),
            module_members=(),
        )

    # Nothing landed in SQLite -- the validation runs before the UoW opens,
    # so the well-formed sibling chunk was not persisted either.
    async with factory() as uow:
        demo_rows = await uow.chunks.list(filter={"package": "demo"})
    assert demo_rows == []
    assert _count_empty_package_chunks(db_path) == 0

    # No vectors were written for the rejected batch.
    if tq_path.exists():
        async with TurboQuantUnitOfWork(index_path=tq_path, dim=_DIM, bit_width=_BW) as tq_uow:
            assert tq_uow.size() == 0


@pytest.mark.asyncio
async def test_chunk_package_mismatch_raises_before_touching_other_packages(
    tmp_path: Path,
) -> None:
    """A chunk carrying a DIFFERENT package string than ``package.name``
    (not just a missing key) must also be rejected — the same silent
    ''-style corruption applies to any mismatch, not only the empty-string
    default.
    """
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    package = _pkg("demo")
    mismatched_chunk = Chunk(
        text="wrong pkg body",
        metadata={"package": "other-package", "title": "gamma"},
    )

    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    with pytest.raises(ValueError, match="other-package"):
        await svc.reindex_package(package, (mismatched_chunk,), module_members=())

    async with factory() as uow:
        demo_rows = await uow.chunks.list(filter={"package": "demo"})
        other_rows = await uow.chunks.list(filter={"package": "other-package"})
    assert demo_rows == []
    assert other_rows == []


@pytest.mark.asyncio
async def test_reindex_package_repeated_calls_do_not_accumulate_orphans_when_input_is_wellformed(
    tmp_path: Path,
) -> None:
    """Companion sanity check: well-formed batches (the common case) are
    unaffected by the new guard and never produce ''-package orphan rows
    across repeated reindexes.
    """
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    package = _pkg("demo")
    good_chunk = Chunk(
        text="alpha body",
        embedding=_vec(0.1, 0.2, 0.3, 0.4),
        metadata={"package": "demo", "title": "alpha"},
    )
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    await svc.reindex_package(package, (good_chunk,), module_members=())
    await svc.reindex_package(package, (good_chunk,), module_members=())

    assert _count_empty_package_chunks(db_path) == 0
    async with factory() as uow:
        demo_rows = await uow.chunks.list(filter={"package": "demo"})
    assert len(demo_rows) == 1
