"""Capability-based SearchBackend seam (spec 2026-05-30-unified-search-backend).

One factory Protocol over the storage capabilities. Accessors return the
read-only ``*Searchable`` view, or ``None`` when the backend lacks that
capability. Ingestion-write participation flows through
``write_uow_children()``. The default :class:`SqliteCompositeBackend`
(added in a later task) wraps the existing SQLite / TurboQuant / fast-plaid
adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

from pydocs_mcp.storage.protocols import (
    GraphSearchable,
    HybridSearchable,
    MultiVectorSearchable,
    TextSearchable,
    UnitOfWork,
    VectorSearchable,
)


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
