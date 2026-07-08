"""Regression test — embedding.dim config drift against an existing ``.tq``.

Gap (storage, medium risk): ``TurboQuantUnitOfWork._open_index`` passes
``self._dim`` only on the CONSTRUCT branch; a LOADED ``.tq`` keeps its
on-disk dim. If a user changes ``embedding.dim`` (or the embedding model)
in YAML without reindexing, the loaded index silently disagrees with the
configured dim:

  * write-side: ``IdMapIndex.add_with_ids`` raises a raw, un-anchored
    ``ValueError: dim mismatch: index dim=X, batch dim=Y`` mid-indexing.
  * read-side (the worse failure): ``IdMapIndex.search`` with a
    wrong-dim query vector SUCCEEDS silently and returns meaningless
    hits — confirmed empirically against turbovec (see gap record).

``IndexMetadata.embedder_matches`` exists precisely for this class of
check (see ``multirepo.validate_project_embedder``) but nothing in the
TurboQuant read/write path consulted it before this fix — the
``TurboQuantUnitOfWork`` doesn't even hold a db handle to read
``index_metadata`` from, so the gate has to be turbovec's own
authoritative ``IdMapIndex.dim`` (the on-disk dim of the loaded index),
not a second copy of the SQLite-side check.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.storage.errors import EmbeddingDimMismatchError
from pydocs_mcp.storage.search_backend import _TurboQuantReadStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

_BIT_WIDTH = 4
_OLD_DIM = 8
_NEW_DIM = 16


def _vec(dim: int, seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(dim).astype(np.float32)


async def _build_index_at_dim(tq_path: Path, dim: int) -> None:
    async with TurboQuantUnitOfWork(index_path=tq_path, dim=dim, bit_width=_BIT_WIDTH) as uow:
        await uow.add_vectors([1, 2], [_vec(dim, 1), _vec(dim, 2)])
        await uow.commit()


async def test_write_side_reindex_at_new_dim_raises_actionable_error(tmp_path: Path) -> None:
    """Entering a UoW configured for a new dim over an old-dim .tq must fail
    loudly and name ``embedding.dim`` — not surface turbovec's raw ValueError
    mid-``add_vectors`` with no pointer to the fix.
    """
    tq_path = tmp_path / "test.tq"
    await _build_index_at_dim(tq_path, _OLD_DIM)

    with pytest.raises(EmbeddingDimMismatchError, match="embedding.dim"):
        async with TurboQuantUnitOfWork(
            index_path=tq_path,
            dim=_NEW_DIM,
            bit_width=_BIT_WIDTH,
        ) as uow:
            await uow.add_vectors([3], [_vec(_NEW_DIM, 3)])


async def test_read_side_dim_divergent_load_rejects_instead_of_garbage_hits(
    tmp_path: Path,
) -> None:
    """Today (pre-fix) this silently returns hits with meaningless scores —
    turbovec's ``IdMapIndex.search`` does NOT validate query dim against a
    loaded index built at a different dim. Desired contract: reject with an
    actionable error instead of returning garbage.
    """
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import (
        build_sqlite_candidate_id_resolver,
        build_sqlite_chunk_hydrator,
    )

    open_index_database(db_path).close()
    await _build_index_at_dim(tq_path, _OLD_DIM)

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_NEW_DIM,  # config now says 16 — the on-disk .tq is still 8
        bit_width=_BIT_WIDTH,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    with pytest.raises(EmbeddingDimMismatchError, match="embedding.dim"):
        await store.vector_search(_vec(_NEW_DIM, 99).tolist(), limit=5)


async def test_matching_dim_load_is_unaffected(tmp_path: Path) -> None:
    """Sanity check: the new guard must not false-positive on the normal
    (dim-stable) reindex path.
    """
    tq_path = tmp_path / "test.tq"
    await _build_index_at_dim(tq_path, _OLD_DIM)

    async with TurboQuantUnitOfWork(
        index_path=tq_path,
        dim=_OLD_DIM,
        bit_width=_BIT_WIDTH,
    ) as uow:
        await uow.add_vectors([3], [_vec(_OLD_DIM, 3)])
        await uow.commit()
        assert uow.size() == 3
