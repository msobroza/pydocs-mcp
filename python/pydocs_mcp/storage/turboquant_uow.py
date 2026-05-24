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

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
from turbovec import IdMapIndex

from pydocs_mcp.models import Embedding, is_multi_vector


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

    async def __aenter__(self) -> "TurboQuantUnitOfWork":
        self._index = (
            IdMapIndex.load(str(self._index_path))
            if self._index_path.exists()
            else IdMapIndex(dim=self._dim, bit_width=self._bit_width)
        )
        return self

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
            except Exception:
                # Best-effort cleanup; don't mask the original exception
                # that triggered ``__aexit__``.
                pass

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
        self._index.add_with_ids(vectors, ids_arr)
        self._dirty = True

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.remove_vectors called outside async with",
            )
        for chunk_id in ids:
            self._index.remove(int(chunk_id))
        self._dirty = True

    async def commit(self) -> None:
        """Persist in-memory index to ``<path>.tmp`` then atomic-rename.

        A crash mid-write leaves the previous ``.tq`` intact — the temp
        file is the failure quarantine; ``os.replace`` is the success
        signal.
        """
        if not self._dirty or self._index is None:
            return
        tmp = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
        self._index.write(str(tmp))
        os.replace(tmp, self._index_path)
        self._dirty = False

    async def rollback(self) -> None:
        """Discard in-memory adds/removes by reloading from disk."""
        if not self._dirty:
            return
        self._index = (
            IdMapIndex.load(str(self._index_path))
            if self._index_path.exists()
            else IdMapIndex(dim=self._dim, bit_width=self._bit_width)
        )
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


__all__ = ("TurboQuantUnitOfWork",)
