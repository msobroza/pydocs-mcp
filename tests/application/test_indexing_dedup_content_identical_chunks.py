"""Content-identical chunks must not crash the dense vector write (#69).

A package can legitimately carry two (or more) chunks whose
``(package, module, title, text)`` identity tuple is identical — so their
auto-derived ``content_hash`` is EQUAL. Those collisions persist as
SEPARATE SQLite rows (distinct autoincrement ``chunks.id``). Originally
``IndexingService._maybe_write_vectors`` keyed the persisted snapshot by
``content_hash`` via a plain ``{hash: chunk}`` dict, which collapsed both
distinct rows to a single (last-write-wins) persisted id — the
per-input-chunk loop then emitted that SHARED id once per colliding input
chunk, so ``uow.vectors.add_vectors`` handed the same id to TurboQuant
twice -> ``IdMapIndex.add_with_ids`` raised
``ValueError: id N already present``, crashing ``reindex_package`` for
any package with content-identical chunks (real dependencies, DS-1000
reference libs).

MULTISET fix (mirrors the diff-merge multiset fix that closed the
matching row-count gap): ``_maybe_write_vectors`` now pairs each input
chunk with its OWN distinct persisted row instead of collapsing by hash,
so both SEPARATE rows get their OWN vector written — 2 rows, 2 vectors,
still zero crashes. Collapsing to 1 vector would have silently left one
persisted row's id with no vector in the ``.tq`` sidecar, which is its
own latent bug (dense lookups for that id would come back empty).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

# turbovec requires dim multiple of 8 and bit_width in {2, 3, 4} — mirror
# the constants used by tests/application/test_indexing_writes_vectors.py.
_DIM = 8
_BW = 4


def _vec(*values: float) -> np.ndarray:
    """Pad/truncate ``values`` to a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


def _identical_chunk() -> Chunk:
    """Build a chunk whose identity tuple is fixed, so two instances
    auto-derive the SAME ``content_hash`` (the collision under test)."""
    return Chunk(
        text="identical body",
        embedding=_vec(0.1, 0.2, 0.3, 0.4),
        metadata={"package": "demo", "module": "demo.mod", "title": "dup"},
    )


@pytest.mark.asyncio
async def test_reindex_package_dedups_content_identical_chunks(
    tmp_path: Path,
) -> None:
    """Two content-identical chunks → two distinct rows, two vectors, no crash (#69)."""
    db_path = tmp_path / "cache.db"
    tq_path = tmp_path / "cache.tq"
    open_index_database(db_path).close()

    c1 = _identical_chunk()
    c2 = _identical_chunk()
    # Precondition: the test genuinely exercises the collision path. If the
    # hashes differed, by_hash would not collapse the rows and the bug would
    # never trip.
    assert c1.content_hash == c2.content_hash

    package = Package(
        name="demo",
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path,
        tq_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    )
    svc = IndexingService(uow_factory=factory)

    # Pre-fix this raised ``ValueError: id N already present in index``.
    await svc.reindex_package(package, (c1, c2), module_members=())

    # Both content-identical chunks land as SEPARATE SQLite rows — the
    # guard (persisted count == input count) does NOT skip, so we genuinely
    # reach the vector-write path.
    async with factory() as uow:
        persisted = await uow.chunks.list(filter={"package": "demo"})
    assert len(persisted) == 2

    # Each of the two distinct persisted rows gets its OWN vector (multiset
    # pairing) — the TurboQuant sidecar holds two vectors, one per row id,
    # not one collapsed-by-hash vector. Zero crashes either way.
    assert tq_path.exists()
    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_DIM,
        bit_width=_BW,
    ) as tq_uow:
        assert tq_uow.size() == 2
