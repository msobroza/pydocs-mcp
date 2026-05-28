"""TurboQuantVectorStore — implements VectorSearchable (spec §5.3).

Decoupled from SQLite: the constructor takes a ``CandidateIdResolver`` +
``ChunkHydrator`` callable pair. The store itself never imports any
SQLite module — that's the SOLID seam (spec §7 risk row 1) that lets a
future Qdrant / Postgres / etc. swap its own resolver + hydrator in
without touching this class.

The store wraps the in-memory ``turbovec.IdMapIndex`` exposed by
``TurboQuantUnitOfWork.index``. Pre-filtering happens upstream: the
resolver turns a ``Filter`` into a ``uint64`` allowlist of candidate
chunk IDs which is then passed to ``IdMapIndex.search``, so the
ANN search is restricted to rows the metadata filter already approved.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

CandidateIdResolver = Callable[[Filter], Awaitable[np.ndarray]]
ChunkHydrator = Callable[[Sequence[int]], Awaitable[tuple[Chunk, ...]]]


@dataclass
class TurboQuantVectorStore:
    """VectorSearchable backed by ``turbovec.IdMapIndex``.

    ``vector_search(query_vector, limit, filter=None)``:
      1. If ``filter`` is set, call ``candidate_id_resolver(filter)`` for
         an allowlist of ``uint64`` chunk IDs.
      2. Call ``IdMapIndex.search(queries, k, allowlist=...)`` off the
         event loop via ``asyncio.to_thread`` — the underlying PyO3 call
         is sync + CPU-bound.
      3. Hydrate returned IDs into ``Chunk`` records via ``chunk_hydrator``.
      4. Stamp each ``Chunk`` with ``relevance`` (the index score) +
         ``retriever_name`` so the downstream pipeline can fuse / rank.
    """
    uow: TurboQuantUnitOfWork
    candidate_id_resolver: CandidateIdResolver
    chunk_hydrator: ChunkHydrator
    retriever_name: str = "turboquant_dense"

    async def vector_search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]:
        # ``IdMapIndex.search`` requires a 2-D ``(nq, dim)`` float32 array
        # even for a single query — reshape at the boundary so callers can
        # pass a flat ``Sequence[float]`` per the VectorSearchable Protocol.
        query = np.asarray(query_vector, dtype=np.float32).reshape(1, -1)

        if filter is not None:
            allowlist = await self.candidate_id_resolver(filter)
            if allowlist.size == 0:
                return ()
            scores_2d, ids_2d = await asyncio.to_thread(
                self.uow.index.search, query, limit, allowlist=allowlist,
            )
        else:
            scores_2d, ids_2d = await asyncio.to_thread(
                self.uow.index.search, query, limit,
            )

        # Unwrap the single-query row from the ``(1, k)`` 2-D results.
        scores = scores_2d[0]
        ids = ids_2d[0]
        if len(ids) == 0:
            return ()

        id_list = [int(i) for i in ids.tolist()]
        chunks = await self.chunk_hydrator(id_list)
        # strict=True: ids/scores come from the same TurboQuant query
        # response and have identical length by construction.
        id_to_score = {
            int(i): float(s)
            for i, s in zip(ids.tolist(), scores.tolist(), strict=True)
        }
        # Re-emit each hydrated Chunk with the score + retriever name
        # populated. Frozen dataclass, so we rebuild rather than mutate.
        return tuple(
            Chunk(
                text=c.text,
                id=c.id,
                relevance=id_to_score.get(int(c.id)) if c.id is not None else None,
                retriever_name=self.retriever_name,
                embedding=c.embedding,
                metadata=c.metadata,
            )
            for c in chunks
        )


__all__ = ("CandidateIdResolver", "ChunkHydrator", "TurboQuantVectorStore")
