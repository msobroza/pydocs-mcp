"""Capability-based SearchBackend seam (spec 2026-05-30-unified-search-backend).

One factory Protocol over the storage capabilities. Accessors return the
read-only ``*Searchable`` view, or ``None`` when the backend lacks that
capability. Ingestion-write participation flows through
``write_uow_children()``. The default :class:`SqliteCompositeBackend`
(added in a later task) wraps the existing SQLite / TurboQuant / fast-plaid
adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.protocols import (
    GraphSearchable,
    HybridSearchable,
    MultiVectorSearchable,
    TextSearchable,
    UnitOfWork,
    VectorSearchable,
)
from pydocs_mcp.storage.turboquant_store import (
    CandidateIdResolver,
    ChunkHydrator,
    TurboQuantVectorStore,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


class FilterStrategy(StrEnum):
    """How a capability scopes a query to a filtered candidate subset."""

    PREFILTER_IDS = "prefilter_ids"  # resolve filter -> id allowlist -> search
    SERVER_SIDE = "server_side"  # push filter into the engine query
    RERANK_ONLY = "rerank_only"  # re-score a pipeline-provided subset


@runtime_checkable
class SearchBackend(Protocol):
    """Factory over storage capabilities. Read accessors return the
    ``*Searchable`` view or ``None``; writes flow via ``write_uow_children``."""

    def lexical(self) -> TextSearchable | None: ...
    def dense(self) -> VectorSearchable | None: ...
    def multi(self) -> MultiVectorSearchable | None: ...
    def hybrid(self) -> HybridSearchable | None: ...
    def graph(self) -> GraphSearchable | None: ...

    def filter_strategy(self, capability: Literal["dense", "multi"]) -> FilterStrategy: ...

    def write_uow_children(self) -> tuple[Callable[[], UnitOfWork], ...]: ...

    def capabilities(self) -> Mapping[str, bool]: ...


@dataclass(frozen=True, slots=True)
class _TurboQuantReadStore:
    """VectorSearchable that opens a fresh read-only ``TurboQuantUnitOfWork``
    per query.

    ``TurboQuantUnitOfWork.index`` requires the uow to be entered via
    ``async with`` (no lazy-load), but ``build_retrieval_context`` has no
    surrounding async scope. mmap-ing the ``.tq`` on each query is cheap;
    a long-lived shared-handle optimization is deferred (spec §9 D).
    Missing/empty ``.tq`` yields an empty index → ``()``.
    """

    tq_path: Path
    dim: int
    bit_width: int
    candidate_id_resolver: CandidateIdResolver
    chunk_hydrator: ChunkHydrator
    retriever_name: str = "turboquant_dense"

    async def vector_search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]:
        async with TurboQuantUnitOfWork(
            index_path=self.tq_path,
            dim=self.dim,
            bit_width=self.bit_width,
        ) as uow:
            store = TurboQuantVectorStore(
                uow=uow,
                candidate_id_resolver=self.candidate_id_resolver,
                chunk_hydrator=self.chunk_hydrator,
                retriever_name=self.retriever_name,
            )
            return await store.vector_search(query_vector, limit, filter)
