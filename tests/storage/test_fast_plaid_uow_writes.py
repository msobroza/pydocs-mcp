"""Write-path tests for FastPlaidUnitOfWork. Uses a stub FastPlaid so
the tests run without the optional extra."""
from __future__ import annotations

import sqlite3

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database


class _FakeFastPlaid:
    """In-memory stub matching the FastPlaid public surface for tests."""
    def __init__(self, *a, **kw):
        self._matrices: list = []
    def create(self, documents_embeddings):
        self._matrices = list(documents_embeddings)
    def update(self, documents_embeddings):
        self._matrices.extend(documents_embeddings)
    def delete(self, subset):
        # Keep slots so plaid_doc_ids remain stable; mark as None. The
        # stub is in-memory, so a fresh handle (next ``async with``) has
        # no prior matrices — guard the bounds rather than padding,
        # which mirrors the real fast-plaid's "soft delete missing" tolerance.
        for i in subset:
            if 0 <= i < len(self._matrices):
                self._matrices[i] = None
    def search(self, queries_embeddings, top_k, subset=None):
        if subset is None:
            subset = list(range(len(self._matrices)))
        scored = []
        for i in subset:
            if i >= len(self._matrices) or self._matrices[i] is None:
                continue
            doc = self._matrices[i].numpy() if hasattr(self._matrices[i], "numpy") else np.asarray(self._matrices[i])
            q = queries_embeddings.squeeze(0).numpy() if hasattr(queries_embeddings, "numpy") else np.asarray(queries_embeddings).squeeze(0)
            scored.append((i, float((q @ doc.T).max(axis=1).sum())))
        scored.sort(key=lambda t: -t[1])
        return [scored[:top_k]]


@pytest.mark.asyncio
async def test_add_vectors_writes_mapping_rows(tmp_path, monkeypatch) -> None:
    """add_vectors assigns plaid_doc_ids 0..N-1 and writes chunk_multi_vector_ids."""
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)

    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t1','b','dep_doc')")
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t2','b','dep_doc')")
        conn.commit()

    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        await uow.add_vectors(
            ids=[1, 2],
            embeddings=[
                [np.ones((4,), dtype=np.float32), np.ones((4,), dtype=np.float32)],
                [np.full((4,), 0.5, dtype=np.float32)],
            ],
        )
        await uow.commit()

    with sqlite3.connect(db_path) as conn:
        rows = list(conn.execute(
            "SELECT chunk_id, plaid_doc_id FROM chunk_multi_vector_ids ORDER BY chunk_id"
        ))
    assert rows == [(1, 0), (2, 1)]


@pytest.mark.asyncio
async def test_remove_vectors_drops_mapping(tmp_path, monkeypatch) -> None:
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t','b','dep_doc')")
        conn.commit()

    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        await uow.add_vectors([1], [[np.ones((4,), dtype=np.float32)]])
        await uow.commit()
    async with uow:
        await uow.remove_vectors([1])
        await uow.commit()
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunk_multi_vector_ids").fetchone()[0]
    assert n == 0


@pytest.mark.asyncio
async def test_clear_all_wipes_mapping(tmp_path, monkeypatch) -> None:
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod
    monkeypatch.setattr(mod, "_FastPlaidCls", _FakeFastPlaid, raising=False)
    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO chunks(package, title, text, origin) VALUES('p','t','b','dep_doc')")
        conn.commit()
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=tmp_path / "x.plaid",
        db_path=db_path,
        pipeline_hash="h",
        device="cpu",
    )
    async with uow:
        await uow.add_vectors([1], [[np.ones((4,), dtype=np.float32)]])
        await uow.clear_all()
        await uow.commit()
    with sqlite3.connect(db_path) as conn:
        n = conn.execute("SELECT COUNT(*) FROM chunk_multi_vector_ids").fetchone()[0]
    assert n == 0
