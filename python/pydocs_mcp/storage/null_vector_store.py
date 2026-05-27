"""Null Object impl of the vector backend for dense-disabled deployments (spec S15).

Lets ``uow.vectors`` be always-present, removing the
``getattr(uow, "vectors", None)`` guards previously scattered across
:mod:`pydocs_mcp.application.indexing_service`. Mirrors the surface of
:class:`pydocs_mcp.storage.turboquant_uow.TurboQuantUnitOfWork`'s
``vectors`` self-reference — ``add_vectors`` / ``remove_vectors`` /
``clear_all`` are all silent no-ops, and ``vector_search`` returns an
empty tuple so search-side callers can branch on backend presence by
result emptiness instead of attribute presence.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NullVectorStore:
    """No-op VectorStore used when dense embeddings are disabled.

    Drop-in for ``uow.vectors`` in the SQLite-only composition root.
    The composite SQLite + TurboQuant root still uses the real
    :class:`TurboQuantUnitOfWork`; production code should never need
    to branch on backend identity now that this null object exists.
    """

    @property
    def vectors(self) -> "NullVectorStore":
        """Self-reference mirroring :class:`TurboQuantUnitOfWork.vectors`.

        ``CompositeUnitOfWork`` delegates by attribute name; were a
        future composition route through that path the symmetry
        preserves call semantics (``await uow.vectors.add_vectors(...)``).
        """
        return self

    async def add_vectors(
        self, ids: Sequence[int], embeddings: Sequence[object],  # noqa: ARG002
    ) -> None:
        # Silent no-op: deployments without dense embeddings drop vectors.
        return None

    async def remove_vectors(self, ids: Sequence[int]) -> None:  # noqa: ARG002
        return None

    async def clear_all(self) -> None:
        return None

    async def vector_search(
        self,
        query_vector: Sequence[float],  # noqa: ARG002
        *,
        limit: int = 10,  # noqa: ARG002
        filter: object | None = None,  # noqa: A002, ARG002
    ) -> tuple:
        """Empty result — uniform surface for search-side callers."""
        return ()


__all__ = ("NullVectorStore",)
