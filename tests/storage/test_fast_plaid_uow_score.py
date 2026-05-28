"""score(): subset-filtered MaxSim over fast-plaid (Decision B REVISED)."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from tests.storage.test_fast_plaid_uow_writes import _FakeFastPlaid


class _PersistentFakeFastPlaid(_FakeFastPlaid):
    """Stub that persists ``_matrices`` across re-instantiations.

    The real ``fast_plaid`` writes the index to disk on each ``.update`` /
    ``.delete`` and re-opens it on the next ``FastPlaid(index=...)`` call.
    The base ``_FakeFastPlaid`` is in-memory-only, so two ``async with uow``
    blocks each get a fresh empty stub — which breaks ``score`` tests that
    need to read back what an earlier block wrote. This subclass restores
    persistence by caching ``_matrices`` on the class keyed on the index path.
    """

    _by_path: dict = {}

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        key = kw.get("index")
        self._matrices = self._by_path.setdefault(key, [])
        self._key = key

    def create(self, documents_embeddings):
        super().create(documents_embeddings)
        self._by_path[self._key] = self._matrices

    def update(self, documents_embeddings):
        super().update(documents_embeddings)
        self._by_path[self._key] = self._matrices


@pytest.mark.asyncio
async def test_score_translates_chunk_ids_to_plaid_ids(tmp_path, monkeypatch) -> None:
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _PersistentFakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        for i in range(3):
            conn.execute(
                "INSERT INTO chunks(package, title, text, origin) "
                "VALUES('p',?,?, 'dep_doc')",
                (f"t{i}", f"b{i}"),
            )
        conn.commit()
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    docs = [
        [np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32),
         np.array([0.0, 1.0, 0.0, 0.0], dtype=np.float32)],
        [np.array([0.0, 0.0, 1.0, 0.0], dtype=np.float32)],
        [np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32)],
    ]
    async with uow:
        await uow.add_vectors([1, 2, 3], docs)
        await uow.commit()

    # Subset to chunk_ids [1, 2]; query token aligns with chunk 1.
    async with uow:
        results = await uow.score(
            query_embedding=[np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)],
            subset_chunk_ids=[1, 2],
            top_k=2,
        )
    assert isinstance(results, tuple)
    assert len(results) == 2
    ids = [r[0] for r in results]
    # Chunk 1 must rank above chunk 2 (perfect alignment) and chunk 3 must be absent.
    assert 3 not in ids
    assert ids[0] == 1


@pytest.mark.asyncio
async def test_score_empty_subset_returns_empty(tmp_path, monkeypatch) -> None:
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _PersistentFakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        results = await uow.score(
            query_embedding=[np.zeros((4,), dtype=np.float32)],
            subset_chunk_ids=[],
            top_k=10,
        )
    assert results == ()
