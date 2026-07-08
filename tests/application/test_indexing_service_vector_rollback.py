"""``reindex_package`` atomicity when the vector store fails mid-transaction.

``_maybe_write_vectors`` (called from inside the same ``async with uow:``
body as ``chunks.insert``) runs BEFORE ``uow.commit()`` — see
``IndexingService.reindex_package`` / ``_maybe_write_vectors`` in
``application/indexing_service.py``. If ``uow.vectors.add_vectors`` raises
(e.g. a dim-mismatch embedding after ``embedding.dim`` changed in YAML
without a model rename — turbovec rejects wrong-width vectors), the
exception must propagate out of the ``async with`` block so the UoW's
safety-net rollback fires and NOTHING for this package is left half-landed:
no committed chunk rows, and ``chunks.mark_embedded`` (which runs
immediately after ``add_vectors`` and stamps ``embedded=1``) must never be
reached.

This closes the gap left by ``test_reindex_package_rolls_back_on_exception``
in ``test_indexing_service.py``, which only bombs ``chunks.insert`` — the
vector-write path that runs AFTER ``chunks.insert`` but BEFORE ``commit()``
was completely unpinned for its own failure point.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.models import Chunk, Package, PackageOrigin
from tests._fakes import FakeUnitOfWork, InMemoryChunkStore, make_fake_uow_factory


def _pkg(name: str = "fastapi") -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _embedded_chunk(package: str, title: str) -> Chunk:
    """A chunk carrying an embedding — required to reach ``add_vectors``.

    ``_maybe_write_vectors`` short-circuits (spec Performance note) when
    no input chunk carries an embedding, so a bare ``Chunk`` would never
    exercise the vector-store failure path this test targets.
    """
    return Chunk(
        text=f"{title} body",
        embedding=np.asarray([0.1, 0.2, 0.3], dtype=np.float32),
        metadata={"package": package, "title": title},
    )


@dataclass
class _BoomVectorStore:
    """Records the call, then raises — mirrors TurboQuant rejecting a
    wrong-width vector (dim mismatch) or any other mid-transaction
    ``add_vectors`` failure."""

    calls: list[tuple[Any, Any]] = field(default_factory=list)

    async def add_vectors(self, ids: Any, embeddings: Any) -> None:
        self.calls.append((tuple(ids), tuple(embeddings)))
        raise RuntimeError("boom: dim mismatch")

    async def remove_vectors(self, ids: Any) -> None:
        return None

    async def clear_all(self) -> None:
        return None


@pytest.mark.asyncio
async def test_reindex_package_rolls_back_when_vectors_add_fails() -> None:
    """``add_vectors`` raising after ``chunks.insert`` → the whole
    transaction rolls back: no committed rows, no ``mark_embedded`` call.
    """
    chunks_store = InMemoryChunkStore()
    vectors_store = _BoomVectorStore()

    captured: list[FakeUnitOfWork] = []
    base_factory = make_fake_uow_factory(chunks=chunks_store, vectors=vectors_store)

    def capture_factory() -> FakeUnitOfWork:
        uow = base_factory()
        captured.append(uow)
        return uow

    service = IndexingService(uow_factory=capture_factory)

    with pytest.raises(RuntimeError, match="boom: dim mismatch"):
        await service.reindex_package(
            _pkg("fastapi"),
            (_embedded_chunk("fastapi", "A"),),
            module_members=(),
        )

    # The vector store really was invoked (proves the test reached the
    # intended failure point, not an earlier short-circuit).
    assert len(vectors_store.calls) == 1

    # Exactly one UoW was produced and used for the whole call.
    assert len(captured) == 1
    uow = captured[0]

    # Safety-net rollback fired; commit() was never reached because
    # add_vectors raises before the orchestrator's final `await uow.commit()`.
    assert uow.rolled_back is True
    assert uow.committed is False

    # Invariant under test: chunk rows, embedded flags, and vector adds
    # live or die together. chunks.insert DID run (it's a real call
    # recorded before the vector-store bomb), but the persisted-rows view
    # must show nothing survives for this package — a SQLite-backed UoW
    # would roll back the transaction; the in-memory fake has no such
    # rollback machinery for the underlying dict, so we instead assert the
    # PROTOCOL-level invariant that stands in for it: mark_embedded (which
    # only ever runs immediately after a successful add_vectors) was never
    # called, so no chunk row for this package can have been flagged
    # embedded=1 without a matching vector — the exact corruption this gap
    # guards against.
    assert chunks_store.embedded_ids == set()
    mark_embedded_calls = [c for c in chunks_store.calls if c.method == "mark_embedded"]
    assert mark_embedded_calls == []
