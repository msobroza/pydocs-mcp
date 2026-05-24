"""TurboQuantVectorStore.vector_search with allowlist + hydration (AC-3, AC-4)."""
from __future__ import annotations

import inspect
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import FieldEq
from pydocs_mcp.storage.turboquant_store import TurboQuantVectorStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

# turbovec.IdMapIndex requires ``dim`` to be a multiple of 8 (it packs bits
# into u8 chunks; non-multiples panic at the Rust layer). It also requires
# ``bit_width`` ∈ {2, 3, 4} — ``bit_width=8`` panics. Match
# ``test_turboquant_uow.py`` so the two suites share the same configuration.
_DIM = 8
_BIT_WIDTH = 4


def _vec(*values: float) -> np.ndarray:
    """Pad / truncate ``values`` to a ``_DIM``-wide float32 vector."""
    padded = list(values) + [0.0] * max(0, _DIM - len(values))
    return np.asarray(padded[:_DIM], dtype=np.float32)


async def _populate_index(tmp_path: Path) -> tuple[Path, dict[int, str]]:
    """Seed a ``.tq`` file with 4 distinct one-hot vectors and return id→text map.

    Async so it shares the test's event loop — pytest-asyncio auto-mode
    already owns a loop per test; calling ``asyncio.run`` from inside it
    raises ``RuntimeError: cannot be called from a running event loop``.
    """
    tq = tmp_path / "vec.tq"
    id_to_text = {1: "alpha", 2: "beta", 3: "gamma", 4: "delta"}
    embeddings = [
        _vec(1.0, 0.0, 0.0, 0.0),
        _vec(0.0, 1.0, 0.0, 0.0),
        _vec(0.0, 0.0, 1.0, 0.0),
        _vec(0.0, 0.0, 0.0, 1.0),
    ]
    async with TurboQuantUnitOfWork(
        index_path=tq, dim=_DIM, bit_width=_BIT_WIDTH,
    ) as uow:
        await uow.add_vectors(list(id_to_text.keys()), embeddings)
        await uow.commit()
    return tq, id_to_text


async def test_vector_search_returns_up_to_k_chunks(tmp_path: Path) -> None:
    tq, id_to_text = await _populate_index(tmp_path)

    async def hydrator(ids: Sequence[int]) -> tuple[Chunk, ...]:
        return tuple(Chunk(text=id_to_text[int(i)], id=int(i)) for i in ids)

    async def all_ids_resolver(_filter: object) -> np.ndarray:
        return np.asarray(list(id_to_text.keys()), dtype=np.uint64)

    async with TurboQuantUnitOfWork(
        index_path=tq, dim=_DIM, bit_width=_BIT_WIDTH,
    ) as uow:
        store = TurboQuantVectorStore(
            uow=uow,
            candidate_id_resolver=all_ids_resolver,
            chunk_hydrator=hydrator,
            retriever_name="dense",
        )
        results = await store.vector_search(
            query_vector=_vec(1.0, 0.0, 0.0, 0.0),
            limit=2,
        )
    assert len(results) == 2
    assert all(isinstance(c, Chunk) for c in results)
    # Closest one-hot match should be id=1 (the (1,0,0,0,...) vector).
    assert results[0].id == 1
    # Each Chunk is stamped with the retriever name + a numeric relevance.
    assert all(c.retriever_name == "dense" for c in results)
    assert all(c.relevance is not None for c in results)


async def test_vector_search_with_filter_restricts_to_allowlist(
    tmp_path: Path,
) -> None:
    tq, id_to_text = await _populate_index(tmp_path)

    async def restricted_resolver(_filter: object) -> np.ndarray:
        # Resolver simulates the SQLite-side filter pushdown returning a
        # narrowed allowlist; the store must honour it and never return
        # ids outside this set, even though the index holds {1,2,3,4}.
        return np.asarray([2, 3], dtype=np.uint64)

    async def hydrator(ids: Sequence[int]) -> tuple[Chunk, ...]:
        return tuple(Chunk(text=id_to_text[int(i)], id=int(i)) for i in ids)

    async with TurboQuantUnitOfWork(
        index_path=tq, dim=_DIM, bit_width=_BIT_WIDTH,
    ) as uow:
        store = TurboQuantVectorStore(
            uow=uow,
            candidate_id_resolver=restricted_resolver,
            chunk_hydrator=hydrator,
            retriever_name="dense",
        )
        results = await store.vector_search(
            query_vector=_vec(0.0, 1.0, 0.0, 0.0),
            limit=10,
            filter=FieldEq("package", "demo"),
        )
    assert {c.id for c in results}.issubset({2, 3})
    # The query vector matches id=2 most closely; with the allowlist of {2,3}
    # the store must surface 2 in the result set.
    assert 2 in {c.id for c in results}


async def test_vector_search_does_not_import_sqlite_module() -> None:
    """The store must never import sqlite3 — that's the SOLID decoupling
    seam (spec §7 risk row 1). A future Qdrant / Postgres adapter swaps
    its own resolver + hydrator without touching this class."""
    import pydocs_mcp.storage.turboquant_store as mod

    src = inspect.getsource(mod)
    assert "import sqlite3" not in src
    assert "from sqlite3" not in src
