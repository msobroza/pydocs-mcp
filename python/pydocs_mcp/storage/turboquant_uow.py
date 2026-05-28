"""TurboQuant UoW wrapping the IdMapIndex lifecycle (spec §5.4).

One child of the future CompositeUnitOfWork. Manages an in-memory
``IdMapIndex`` loaded from / persisted to a ``.tq`` sidecar file
alongside the SQLite cache.

Atomicity model: writes go to ``<path>.tmp`` then atomic-rename into
place — a crash mid-write leaves the previous ``.tq`` intact.

Multi-vector inputs raise NotImplementedError this PR; the typed
``Embedding`` union accepts them but persistence is deferred to a
future PR that adds a ``chunk_vectors`` side-table.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from turbovec import IdMapIndex

from pydocs_mcp.models import Embedding, is_multi_vector

logger = logging.getLogger(__name__)


class TurboQuantUnitOfWork:
    """UoW for the TurboQuant ``.tq`` sidecar. See module docstring."""

    def __init__(
        self,
        *,
        index_path: Path,
        dim: int,
        bit_width: int = 4,
    ) -> None:
        self._index_path = index_path
        self._dim = dim
        self._bit_width = bit_width
        self._index: IdMapIndex | None = None
        self._dirty = False

    async def __aenter__(self) -> TurboQuantUnitOfWork:
        # ``IdMapIndex.load`` / constructor are sync PyO3 calls that perform
        # CPU + IO work (mmap a ``.tq`` file or zero-allocate dim*N bits).
        # Per CLAUDE.md §"Async Patterns", offload via asyncio.to_thread so
        # the event loop doesn't stall on large indices.
        self._index = await asyncio.to_thread(self._open_index)
        return self

    def _open_index(self) -> IdMapIndex:
        """Sync helper — pick load-or-construct branch off the event loop."""
        if self._index_path.exists():
            return IdMapIndex.load(str(self._index_path))
        return IdMapIndex(dim=self._dim, bit_width=self._bit_width)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: Any,
    ) -> None:
        # Safety net matching the SqliteUnitOfWork contract (CLAUDE.md
        # §"Creating new application services"): rollback if exit
        # happens without an explicit commit OR with an exception. For
        # TurboQuant this is harmless when no commit was attempted — the
        # ``.tq`` file on disk hasn't been touched — but it keeps the
        # UoW contract uniform across backends.
        if self._dirty:
            try:
                await self.rollback()
            except Exception as rollback_exc:
                # Best-effort cleanup; don't mask the original exception
                # that triggered ``__aexit__``. Mirrors SqliteUnitOfWork's
                # __aexit__ rollback-failure path — log the inner exception
                # rather than swallowing silently so an operator running
                # at WARNING+ still sees the failure.
                logger.warning(
                    "TurboQuant rollback in __aexit__ failed: %r",
                    rollback_exc,
                )

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[Embedding],
    ) -> None:
        """Buffer (id, vector) pairs in the in-memory index.

        Raises NotImplementedError if any embedding is multi-vector
        (list of vectors) — single-vector only this PR. Persisting
        multi-vector embeddings will land alongside the
        ``chunk_vectors`` side-table in a future PR.
        """
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.add_vectors called outside async with",
            )
        for emb in embeddings:
            if is_multi_vector(emb):
                raise NotImplementedError(
                    "TurboQuantUnitOfWork persists single-vector embeddings "
                    "only. Multi-vector (ColBERT-style) embeddings are "
                    "deferred to a future PR that adds a chunk_vectors "
                    "side-table; see spec §5.4 + plan task notes.",
                )
        # Each ``emb`` is a 1-D float32 ``np.ndarray`` — stack into a 2-D
        # matrix for ``IdMapIndex.add_with_ids``. ``asarray`` with
        # ``dtype=float32`` is a no-op when inputs already come from
        # FastEmbed in that dtype.
        vectors = np.asarray(np.stack(list(embeddings)), dtype=np.float32)
        ids_arr = np.asarray(list(ids), dtype=np.uint64)
        # ``add_with_ids`` is a sync PyO3 call doing per-vector
        # quantization — offload to a worker thread per CLAUDE.md
        # §"Async Patterns".
        await asyncio.to_thread(self._index.add_with_ids, vectors, ids_arr)
        self._dirty = True

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.remove_vectors called outside async with",
            )
        # Batch the per-id PyO3 ``remove`` calls inside a single worker
        # thread: N sync calls hopped across N ``asyncio.to_thread`` boundaries
        # would amplify context-switch overhead linearly in ``len(ids)``. The
        # inner loop runs to completion off the event loop, then yields once.
        ids_list = [int(chunk_id) for chunk_id in ids]
        await asyncio.to_thread(self._remove_ids_sync, ids_list)
        self._dirty = True

    def _remove_ids_sync(self, ids: Sequence[int]) -> None:
        """Sync inner loop for :meth:`remove_vectors` — runs in a worker thread."""
        assert self._index is not None  # noqa: S101 — invariant checked by caller before scheduling this on the worker thread
        for chunk_id in ids:
            self._index.remove(chunk_id)

    async def clear_all(self) -> None:
        """Reset the in-memory index to empty; commit writes empty .tq.

        Used by IndexingService.clear_all (which the --force indexing path
        drives). Atomic with the surrounding UoW transaction — the actual
        file write happens in commit() via the existing tmp + os.replace
        path. No separate unlink() needed.
        """
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.clear_all called outside async with",
            )
        self._index = await asyncio.to_thread(
            IdMapIndex, dim=self._dim, bit_width=self._bit_width,
        )
        self._dirty = True

    async def delete_all(self) -> None:
        """Satisfy :meth:`UnitOfWork.delete_all` (spec I3) by delegating to clear_all.

        The vector backend has only one table-equivalent (the
        ``IdMapIndex``), so a UoW-level wipe is exactly the in-memory
        index reset already exposed as :meth:`clear_all`. Distinct
        method name keeps the Protocol surface uniform across backends.
        """
        await self.clear_all()

    async def commit(self) -> None:
        """Persist in-memory index to ``<path>.tmp`` then atomic-rename.

        A crash mid-write leaves the previous ``.tq`` intact — the temp
        file is the failure quarantine; ``os.replace`` is the success
        signal.
        """
        if not self._dirty or self._index is None:
            return
        tmp = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
        # ``IdMapIndex.write`` serializes the full index to disk — sync +
        # IO-bound, offload per CLAUDE.md §"Async Patterns". ``Path.replace``
        # itself is a single atomic syscall and is fast enough to leave on
        # the event loop.
        await asyncio.to_thread(self._index.write, str(tmp))
        tmp.replace(self._index_path)
        self._dirty = False

    async def rollback(self) -> None:
        """Discard in-memory adds/removes by reloading from disk."""
        if not self._dirty:
            return
        # Same load-or-construct branch as ``__aenter__`` — also offloaded
        # so a large ``.tq`` reload after a failed transaction doesn't
        # stall the loop.
        self._index = await asyncio.to_thread(self._open_index)
        self._dirty = False

    def size(self) -> int:
        """Number of stored vectors (for the integrity check).

        ``turbovec.IdMapIndex`` exposes ``__len__`` rather than a
        ``size()`` method; this wrapper hides that detail so callers
        get a stable API across backends.
        """
        return len(self._index) if self._index is not None else 0

    @property
    def index(self) -> IdMapIndex:
        """Read access to the underlying index — for ``TurboQuantVectorStore``."""
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.index accessed outside async with",
            )
        return self._index

    @property
    def vectors(self) -> TurboQuantUnitOfWork:
        """Self-reference under the canonical name expected by CompositeUnitOfWork.

        CompositeUnitOfWork delegates attribute access by name across children.
        Sibling SqliteUnitOfWork exposes ``packages`` / ``chunks`` / ...; this
        UoW owns the vector backend, so callers reach it via ``uow.vectors`` —
        returning ``self`` keeps the name consistent and lets services call
        ``await uow.vectors.add_vectors(...)`` whether wrapped in a composite
        or used directly.
        """
        return self


__all__ = ("TurboQuantUnitOfWork",)
