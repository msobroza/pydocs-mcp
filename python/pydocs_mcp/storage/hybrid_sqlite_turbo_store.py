"""HybridSqliteTurboStore — composes text + vector + ResultFuser (spec §5.3)."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.protocols import (
    ResultFuser,
    TextSearchable,
    VectorSearchable,
)


@dataclass(frozen=True, slots=True)
class HybridSqliteTurboStore:
    """HybridSearchable composing a text store + a vector store + a fuser.

    Runs both stores concurrently via ``asyncio.gather``; hands the two
    ranked lists to the fuser to produce the merged ranking. Cross-store
    ID dedupe is the fuser's responsibility (per ResultFuser Protocol).

    Stateless composition — frozen so injected dependencies cannot be
    swapped at runtime. Future Qdrant / Postgres swap is a one-arg
    constructor change (the text/vector slots accept any TextSearchable
    / VectorSearchable, not just the SQLite / TurboQuant impls).
    """
    text: TextSearchable
    vector: VectorSearchable
    fuser: ResultFuser

    async def hybrid_search(
        self,
        query_terms: str,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | None = None,
    ) -> tuple[Chunk, ...]:
        # Concurrent text + vector retrieval — both stores are independent
        # I/O-bound calls (SQLite FTS5 + TurboQuant search), so gather() runs
        # them in parallel rather than serializing the wait.
        text_task = self.text.text_search(query_terms, limit, filter)
        vec_task = self.vector.vector_search(query_vector, limit, filter)
        text_results, vec_results = await asyncio.gather(text_task, vec_task)
        return await self.fuser.fuse(
            [text_results, vec_results], limit=limit,
        )


__all__ = ("HybridSqliteTurboStore",)
