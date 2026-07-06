"""Capability-based SearchBackend seam (spec 2026-05-30-unified-search-backend).

One factory Protocol over the storage capabilities. Accessors return the
read-only ``*Searchable`` view, or ``None`` when the backend lacks that
capability. Ingestion-write participation flows through
``write_uow_children()``. The default :class:`SqliteCompositeBackend`
wraps the existing SQLite / TurboQuant / fast-plaid adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

from pydocs_mcp.models import Chunk
from pydocs_mcp.retrieval.protocols import ConnectionProvider
from pydocs_mcp.storage.factories import (
    build_connection_provider,
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.protocols import (
    GraphSearchable,
    HybridSearchable,
    MultiVectorSearchable,
    TextSearchable,
    VectorSearchable,
)
from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteVectorStore
from pydocs_mcp.storage.turboquant_store import (
    CandidateIdResolver,
    ChunkHydrator,
    TurboQuantVectorStore,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

if TYPE_CHECKING:
    import numpy as np

    from pydocs_mcp.retrieval.config import AppConfig


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

    def write_uow_children(self) -> tuple[Callable[[], object], ...]: ...

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


@dataclass(frozen=True, slots=True)
class _FastPlaidReadStore:
    """MultiVectorSearchable view opening a FastPlaidUnitOfWork per score call.

    The shipped ``LateInteractionScorerStep`` reads multi-vectors via a
    ``uow_factory`` (``uow.multi_vectors``); this view exists for capability
    negotiation + future backends. fast_plaid is an optional dep, imported
    lazily so the default install path stays free of the ``[late-interaction]``
    extra.
    """

    sidecar_path: Path
    pipeline_hash: str
    device: str
    provider: ConnectionProvider

    async def score(
        self,
        query_embedding: Sequence[np.ndarray],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]:
        from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork

        async with FastPlaidUnitOfWork(
            sidecar_path=self.sidecar_path,
            pipeline_hash=self.pipeline_hash,
            provider=self.provider,
            device=self.device,
        ) as uow:
            return await uow.score(
                query_embedding,
                subset_chunk_ids=subset_chunk_ids,
                top_k=top_k,
            )


@dataclass(frozen=True, slots=True)
class SqliteCompositeBackend:
    """Default backend: SQLite FTS (lexical) + TurboQuant (dense) + fast-plaid
    (multi, when ``late_interaction.enabled``) + SQLite reference graph.

    Read accessors return a fresh ``*Searchable`` view per call (each opens its
    own connection/index handle on use); write participation flows through
    ``write_uow_children()`` so a composite UoW can be assembled by the caller.
    """

    config: AppConfig
    db_path: Path
    tq_path: Path

    def lexical(self) -> TextSearchable:
        return SqliteVectorStore(provider=build_connection_provider(self.db_path))

    def dense(self) -> VectorSearchable:
        embed = self.config.embedding
        return _TurboQuantReadStore(
            tq_path=self.tq_path,
            dim=embed.dim,
            bit_width=embed.bit_width,
            candidate_id_resolver=build_sqlite_candidate_id_resolver(self.db_path),
            chunk_hydrator=build_sqlite_chunk_hydrator(self.db_path),
        )

    def multi(self) -> MultiVectorSearchable | None:
        if not self.config.late_interaction.enabled:
            return None
        return _FastPlaidReadStore(
            sidecar_path=self._plaid_sidecar_path,
            pipeline_hash=self.config.ingestion_pipeline_hash,
            device=self.config.late_interaction.device,
            provider=build_connection_provider(self.db_path),
        )

    def hybrid(self) -> None:
        # No combined lexical+dense engine yet; RRF fusion composes the two
        # single-capability accessors in the retrieval pipeline instead.
        return None

    def graph(self) -> GraphSearchable:
        return SqliteReferenceStore(provider=build_connection_provider(self.db_path))

    def filter_strategy(self, capability: Literal["dense", "multi"]) -> FilterStrategy:
        return {
            "dense": FilterStrategy.PREFILTER_IDS,
            "multi": FilterStrategy.RERANK_ONLY,
        }[capability]

    def write_uow_children(self) -> tuple[Callable[[], object], ...]:
        # Canonical write-child assembler: production indexing sources its
        # ``CompositeUnitOfWork`` children from here. Builds
        # ``[SQLite, TurboQuant, optional FastPlaid]`` — the fast-plaid child
        # only when ``late_interaction.enabled``.
        # ``factories.build_sqlite_plus_turboquant_uow_factory`` remains as a
        # test-only SQLite + TurboQuant subset helper (no fast-plaid leg).
        embed = self.config.embedding
        tq_path = self.tq_path
        # ONE provider shared by the SQLite child AND the fast-plaid child:
        # the SQLite UoW's ``__aenter__`` sets the ``_sqlite_transaction``
        # ambient connection, and the fast-plaid child's mapping repository
        # routes through ``_maybe_acquire`` — so both resolve the SAME open
        # write transaction. Without sharing, the fast-plaid child would open
        # a second connection and deadlock on the held write lock.
        provider = build_connection_provider(self.db_path)
        children: list[Callable[[], object]] = [
            build_sqlite_uow_factory(self.db_path, provider=provider),
            lambda: TurboQuantUnitOfWork(
                index_path=tq_path,
                dim=embed.dim,
                bit_width=embed.bit_width,
            ),
        ]
        if self.config.late_interaction.enabled:
            from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork

            sidecar = self._plaid_sidecar_path
            pipeline_hash = self.config.ingestion_pipeline_hash
            device = self.config.late_interaction.device
            children.append(
                lambda: FastPlaidUnitOfWork(
                    sidecar_path=sidecar,
                    pipeline_hash=pipeline_hash,
                    provider=provider,
                    device=device,
                ),
            )
        return tuple(children)

    def capabilities(self) -> Mapping[str, bool]:
        return {
            "lexical": True,
            "dense": True,
            "multi": self.config.late_interaction.enabled,
            "hybrid": False,
            "graph": True,
        }

    @property
    def _plaid_sidecar_path(self) -> Path:
        # Sidecar lives next to the SQLite DB with a ``.plaid`` suffix, mirroring
        # the ``.tq`` TurboQuant sidecar convention.
        return self.db_path.parent / f"{self.db_path.stem}.plaid"


_BACKEND_FACTORIES: dict[str, Callable[..., SearchBackend]] = {}


def register_backend(
    kind: str,
) -> Callable[[Callable[..., SearchBackend]], Callable[..., SearchBackend]]:
    """Register a backend factory under ``kind``.

    Self-contained decorator registry (intentionally NOT
    ``retrieval.serialization.ComponentRegistry``) so ``storage`` stays
    independent of ``retrieval.serialization`` and no import cycle can form.
    """

    def deco(fn: Callable[..., SearchBackend]) -> Callable[..., SearchBackend]:
        if kind in _BACKEND_FACTORIES:
            raise ValueError(f"backend {kind!r} already registered")
        _BACKEND_FACTORIES[kind] = fn
        return fn

    return deco


@register_backend("sqlite_composite")
def _sqlite_composite_factory(config: AppConfig, *, db_path: Path) -> SearchBackend:
    tq_path = db_path.with_suffix(".tq")
    return SqliteCompositeBackend(config=config, db_path=db_path, tq_path=tq_path)


def build_search_backend(config: AppConfig, db_path: Path) -> SearchBackend:
    """Resolve the configured backend kind to a :class:`SearchBackend`.

    Looks up ``config.search_backend.kind`` in the decorator registry. An
    unregistered kind raises a ``ValueError`` listing the registered kinds.
    """
    kind = config.search_backend.kind
    try:
        factory = _BACKEND_FACTORIES[kind]
    except KeyError as e:
        raise ValueError(
            f"unknown search_backend.kind={kind!r}; registered: "
            f"{sorted(_BACKEND_FACTORIES)}. Set search_backend.kind in your "
            f"AppConfig YAML.",
        ) from e
    return factory(config, db_path=db_path)


def format_capabilities(backend: SearchBackend) -> str:
    """One-line capability matrix for the startup diagnostic (spec invariant C).

    A log line (not an MCP/CLI param) so an operator can see at a glance which
    retrieval capabilities the configured backend actually serves — the
    visibility whose absence let the dense/LI wiring bug stay silent.
    """
    caps = backend.capabilities()
    name = type(backend).__name__
    cells = " ".join(f"{k}{'✓' if v else '✗'}" for k, v in caps.items())
    return f"SearchBackend={name}: {cells}"
