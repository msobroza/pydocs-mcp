"""Regression tests: ``FastPlaidUnitOfWork.add_vectors`` create-vs-update
branch must survive SQLite-mapping / ``.plaid``-sidecar divergence.

``add_vectors`` picks ``handle.create`` vs ``handle.update`` purely from
``next_plaid_offset()`` (a ``chunk_multi_vector_ids`` SQLite read) — it
never asks the ``.plaid`` sidecar what state it's actually in. The two
sides can drift out of lockstep:

  (a) the ``.plaid`` directory is deleted (or never existed) while
      ``chunk_multi_vector_ids`` still has rows — ``next_plaid_offset()``
      returns > 0 so ``add_vectors`` calls ``handle.update`` against an
      index that ``fast_plaid`` will see as empty/nonexistent.
  (b) the mapping table is emptied (``clear_all``, or every mapped chunk
      removed) while the ``.plaid`` dir still holds a non-empty (even if
      soft-deleted) index — ``next_plaid_offset()`` returns 0 so
      ``add_vectors`` calls ``handle.create`` over an existing index.

Real ``fast_plaid`` raises when the wrong one is picked (see the
``# fast-plaid contract`` comment in ``fast_plaid_uow.py``); the shipped
test double up to now accepted ``create``/``update`` interchangeably, so
this branch was enforced nowhere. ``_StrictFakeFastPlaid`` below mirrors
the real contract so these tests fail loudly if ``add_vectors`` ever
mis-picks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.factories import build_connection_provider


class _StrictFakeFastPlaid:
    """Fake that enforces the real fast-plaid create/update contract.

    Mirrors the ACTUAL on-disk contract of ``fast_plaid.search.FastPlaid``
    (verified against the installed ``fast_plaid`` 1.3.0 source):

      - ``create`` unconditionally (re)initializes the index directory —
        safe to call on an existing dir, but destroys whatever was there.
      - ``update`` raises ``FileNotFoundError`` unless
        ``<index>/metadata.json`` already exists on disk — that file is
        the real, filesystem-verifiable "does an index already exist"
        signal, independent of anything SQLite tracks.

    Documents are kept in-memory (``_matrices``, keyed by index path like
    ``test_fast_plaid_uow_score.py``'s ``_PersistentFakeFastPlaid``) so
    ``score``/repeat-instantiation tests still work; ``metadata.json`` is
    a REAL file written to ``index`` so the fix under test — probing the
    filesystem instead of trusting the SQLite offset — is exercised
    against genuine filesystem state, not another in-memory shortcut.
    """

    _by_path: ClassVar[dict] = {}

    def __init__(self, *a, **kw):
        key = kw.get("index")
        self._matrices: list = self._by_path.setdefault(key, [])
        self._key = key
        self._index_dir = Path(key)

    def _marker(self) -> Path:
        return self._index_dir / "metadata.json"

    def create(self, documents_embeddings):
        # Real FastPlaid.create() always succeeds — it wipes+reinitializes
        # the directory regardless of prior contents.
        self._index_dir.mkdir(parents=True, exist_ok=True)
        self._marker().write_text("{}")
        self._matrices = list(documents_embeddings)
        self._by_path[self._key] = self._matrices

    def update(self, documents_embeddings):
        if not self._marker().exists():
            raise FileNotFoundError(
                f"Index directory '{self._index_dir}' does not exist or is invalid. "
                "Please create an index first using the .create() method."
            )
        self._matrices.extend(documents_embeddings)
        self._by_path[self._key] = self._matrices

    def delete(self, subset):
        for i in subset:
            if 0 <= i < len(self._matrices):
                self._matrices[i] = None

    def search(self, queries_embeddings, top_k, subset=None):  # pragma: no cover - unused here
        raise NotImplementedError


def _insert_chunk(db_path, title: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO chunks(package, title, text, origin) VALUES('p', ?, 'b', 'dep_doc')",
            (title,),
        )
        conn.commit()


@pytest.mark.asyncio
async def test_add_vectors_recovers_when_mapping_stale_but_sidecar_deleted(
    tmp_path, monkeypatch
) -> None:
    """Edge (a): mapping rows survive a deleted/never-created ``.plaid`` dir.

    Pre-seed ``chunk_multi_vector_ids`` with a row (so
    ``next_plaid_offset() > 0``) but give the fake a FRESH (no
    ``metadata.json``) sidecar — reproducing "user deleted the .plaid
    directory while the mapping table still has rows". Trusting the
    SQLite offset alone picks ``handle.update`` against the nonexistent
    index and real fast-plaid raises ``FileNotFoundError`` (mirrored by
    the strict fake). ``add_vectors`` must instead probe the sidecar
    directory for the real on-disk marker and fall back to ``.create``
    so indexing recovers instead of crashing.
    """
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod

    monkeypatch.setattr(mod, "_FastPlaidCls", _StrictFakeFastPlaid, raising=False)
    _StrictFakeFastPlaid._by_path.clear()

    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    _insert_chunk(db_path, "t1")
    _insert_chunk(db_path, "t2")

    # Pre-seed the mapping table for chunk 1 as if a PRIOR add_vectors had
    # run against a now-deleted .plaid sidecar — offset resolves to 1 (>0)
    # even though the fake's on-disk index (keyed by this exact path) is
    # untouched / empty.
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO chunk_multi_vector_ids"
            "(chunk_id, plaid_doc_id, package, pipeline_hash) VALUES (1, 0, 'p', 'h')"
        )
        conn.commit()

    sidecar_path = tmp_path / "x.plaid"
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=sidecar_path,
        pipeline_hash="h",
        provider=build_connection_provider(db_path),
        device="cpu",
    )
    async with uow:
        # Must NOT raise — the divergence is detected and the write recovers.
        await uow.add_vectors([2], [[np.ones((4,), dtype=np.float32)]])
        await uow.commit()

    with sqlite3.connect(db_path) as conn:
        rows = list(
            conn.execute(
                "SELECT chunk_id, plaid_doc_id FROM chunk_multi_vector_ids ORDER BY chunk_id"
            )
        )
    # Recovery re-created the index from scratch — chunk 2 must land at a
    # fresh, valid slot the sidecar actually holds (slot 0 of the recreated
    # index), not the stale offset 1 the empty SQLite-derived count implied.
    assert rows == [(2, 0)]


@pytest.mark.asyncio
async def test_add_vectors_recreates_deliberately_after_clear_all(tmp_path, monkeypatch) -> None:
    """Edge (b): mapping table legitimately emptied via ``clear_all`` while
    the ``.plaid`` dir still holds soft-deleted (dead) slots.

    ``clear_all`` soft-deletes every plaid slot (``handle.delete``) and
    drops every mapping row, but the on-disk index directory (and its
    ``metadata.json`` marker) survives — real ``fast_plaid.create()``
    unconditionally wipes+reinitializes that directory regardless of
    prior contents (verified against the installed 1.3.0 source), so
    picking ``.create`` here is safe and correct, never raises, and is
    the RIGHT way to start clean rather than ``.update``-appending after
    dead slots that ``next_plaid_offset()`` can no longer account for
    (their mapping rows are gone). This test pins that ``.create`` is
    chosen DELIBERATELY (offset resets to 0, the recreated index holds
    exactly the new doc) — not merely "whichever branch happens not to
    raise" the way the pre-fix offset-only heuristic left ambiguous.
    """
    pytest.importorskip("torch")
    import pydocs_mcp.storage.fast_plaid_uow as mod

    monkeypatch.setattr(mod, "_FastPlaidCls", _StrictFakeFastPlaid, raising=False)
    _StrictFakeFastPlaid._by_path.clear()

    db_path = tmp_path / "db.db"
    open_index_database(db_path).close()
    _insert_chunk(db_path, "t1")
    _insert_chunk(db_path, "t2")

    sidecar_path = tmp_path / "x.plaid"
    uow = mod.FastPlaidUnitOfWork(
        sidecar_path=sidecar_path,
        pipeline_hash="h",
        provider=build_connection_provider(db_path),
        device="cpu",
    )
    async with uow:
        await uow.add_vectors([1], [[np.ones((4,), dtype=np.float32)]])
        # clear_all soft-deletes plaid slot 0 and drops the mapping row —
        # the on-disk marker survives untouched.
        await uow.clear_all()
        await uow.commit()

    assert (sidecar_path / mod._FAST_PLAID_INDEX_MARKER).exists()

    async with uow:
        await uow.add_vectors([2], [[np.ones((4,), dtype=np.float32)]])
        await uow.commit()

    # .create was chosen: the recreated index holds exactly the new doc
    # (the old dead slot 0 is gone, not merely marked None among leftovers).
    fake = mod._FastPlaidCls._by_path[str(sidecar_path)]
    assert len(fake) == 1
    with sqlite3.connect(db_path) as conn:
        rows = list(
            conn.execute(
                "SELECT chunk_id, plaid_doc_id FROM chunk_multi_vector_ids ORDER BY chunk_id"
            )
        )
    assert rows == [(2, 0)]
