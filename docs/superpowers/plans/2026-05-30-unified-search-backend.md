# Unified `SearchBackend` Capability Seam — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a capability-based `SearchBackend` seam, fix the dense/LI ingestion-wiring bug (#64) *through* it on the shared prod+bench path, and delete the dead `HybridSqliteTurboStore`.

**Architecture:** A `SearchBackend` factory Protocol exposes read capabilities (`lexical/dense/multi/hybrid/graph` → `*Searchable` views or `None`) plus `write_uow_children()` (ingestion) and `filter_strategy(capability)`. A single `SqliteCompositeBackend` wraps the existing SQLite/TurboQuant/fast-plaid adapters. `build_retrieval_context` + all indexing call sites source their stores from one `build_search_backend(config, db_path)` so they can never drift. Dense reads re-enter a `TurboQuantUnitOfWork` per query; LI reads keep the existing `uow_factory` path; ingestion writes keep the existing shape-dispatch.

**Tech Stack:** Python 3.11+, pydantic-settings, pytest/pytest-asyncio, ruff. Storage Protocols + `@dataclass(frozen=True, slots=True)`. turbovec (`.tq`), fast-plaid (`.plaid`), SQLite FTS5.

**Spec:** `docs/superpowers/specs/2026-05-30-unified-search-backend-design.md` (16 sections, AC-1…AC-14).

**Authorship:** every commit authored by `msobroza` only — NO `Co-Authored-By` trailers, NO `--author`, NO `git config` changes. Plain `git commit`.

**Baseline:** branch `docs/benchmark-cache-and-dense-caveat` at `84d0bea` (spec commit). Run `pytest -q` once before Task 1 to capture the green baseline count.

---

## File Structure

**New files:**
- `python/pydocs_mcp/storage/search_backend.py` — `FilterStrategy` enum, `SearchBackend` Protocol, `_TurboQuantReadStore`, `SqliteCompositeBackend`, `backend_registry`, `build_search_backend`. One responsibility: the capability seam + the default backend.
- `tests/storage/test_search_backend.py` — backend capability/accessor/registry tests.
- `tests/retrieval/test_dense_wiring_regression.py` — the #64 regression test (index dense → search → real dense hits).

**Modified files:**
- `python/pydocs_mcp/storage/protocols.py` — add `MultiVectorSearchable`, `GraphSearchable`; make `MultiVectorStore`/`ReferenceStore` extend them; bump docstring count.
- `python/pydocs_mcp/retrieval/config.py` — `SearchBackendConfig` sub-model + `AppConfig.search_backend` field + fold backend identity into `ingestion_pipeline_hash`.
- `python/pydocs_mcp/defaults/default_config.yaml` — `search_backend:` block.
- `python/pydocs_mcp/retrieval/factories.py` — `build_retrieval_context` sources `vector_store` + `uow_factory` from the backend.
- `python/pydocs_mcp/retrieval/steps/dense_fetcher.py` — `from_dict` requires `isinstance(store, VectorSearchable)` (invariant A).
- `python/pydocs_mcp/__main__.py` — indexing + diagnostic use `build_search_backend`.
- `python/pydocs_mcp/server.py` — startup capability diagnostic log.
- `benchmarks/src/benchmarks/eval/systems/pydocs.py` — `_do_index` uses `build_search_backend` write children.
- `benchmarks/src/benchmarks/eval/_bench_cache.py` — (no change needed; key already folds `ingestion_pipeline_hash`, which Task 11 extends).

**Deleted files:**
- `python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py`
- `tests/storage/test_hybrid_sqlite_turbo_store.py`

---

## Task 1: ISP read/write split — `MultiVectorSearchable` + `GraphSearchable`

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (around L131-187, L310-371, L431-458, L1)
- Test: `tests/storage/test_protocols_isp_split.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_protocols_isp_split.py
"""ISP split: read-only *Searchable views extracted; *Store extends them."""
from pydocs_mcp.storage.protocols import (
    GraphSearchable,
    MultiVectorSearchable,
    MultiVectorStore,
    ReferenceStore,
)


def test_multi_vector_store_is_a_searchable():
    # A MultiVectorStore is structurally a MultiVectorSearchable (read view).
    assert issubclass(MultiVectorStore, MultiVectorSearchable)


def test_reference_store_is_a_graph_searchable():
    assert issubclass(ReferenceStore, GraphSearchable)


def test_searchable_views_expose_only_read_methods():
    assert hasattr(MultiVectorSearchable, "score")
    assert not hasattr(MultiVectorSearchable, "add_vectors")
    assert hasattr(GraphSearchable, "find_callers")
    assert not hasattr(GraphSearchable, "save_many")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_protocols_isp_split.py -q`
Expected: FAIL with `ImportError: cannot import name 'MultiVectorSearchable'`.

- [ ] **Step 3: Edit `protocols.py` — extract the read views, make the stores extend them**

Add `MultiVectorSearchable` immediately above the existing `MultiVectorStore` (L431) and rewrite `MultiVectorStore` to extend it:

```python
@runtime_checkable
class MultiVectorSearchable(Protocol):
    """Read-only late-interaction view: MaxSim score over a candidate subset.

    The read capability a SearchBackend exposes via ``.multi()``. The
    write surface lives on :class:`MultiVectorStore`, which extends this.
    """

    async def score(
        self,
        query_embedding: list[np.ndarray],
        *,
        subset_chunk_ids: Sequence[int],
        top_k: int,
    ) -> tuple[tuple[int, float], ...]: ...


@runtime_checkable
class MultiVectorStore(MultiVectorSearchable, Protocol):
    """Typed contract for the multi-vector (token-matrix) backend.

    Backend-neutral surface: callers identify chunks by ``chunk_id`` —
    NOT by the backend-internal ``plaid_doc_id``...
    """

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[list[np.ndarray]],
    ) -> None: ...

    async def remove_vectors(self, ids: Sequence[int]) -> None: ...

    async def clear_all(self) -> None: ...
```

Add `GraphSearchable` immediately above `ReferenceStore` (L310) and make `ReferenceStore` extend it. Move `find_callers`/`find_callees`/`find_by_name` into `GraphSearchable`; keep the write methods on `ReferenceStore`:

```python
@runtime_checkable
class GraphSearchable(Protocol):
    """Read-only reference-graph view (callers / callees / by-name).

    The read capability a SearchBackend exposes via ``.graph()``. Consumed
    by the ``lookup`` MCP path (ReferenceService), not the retrieval
    pipeline. The write surface lives on :class:`ReferenceStore`.
    """

    async def find_callers(self, *, target_node_id: str) -> list[NodeReference]: ...
    async def find_callees(self, *, from_node_id: str) -> list[NodeReference]: ...
    async def find_by_name(
        self,
        to_name: str,
        kind: ReferenceKind | None = None,
    ) -> list[NodeReference]: ...


@runtime_checkable
class ReferenceStore(GraphSearchable, Protocol):
    """Storage boundary for the cross-node reference graph (spec §6.2)..."""

    async def save_many(
        self,
        refs: Iterable[NodeReference],
        *,
        package: str,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def delete_for_package(
        self,
        package: str,
        *,
        uow: UnitOfWork | None = None,
    ) -> None: ...

    async def delete_all(self, *, uow: UnitOfWork | None = None) -> None: ...

    async def resolve_unresolved(self, qnames: Iterable[str]) -> int: ...
```

Update the module docstring (L1) from `10 @runtime_checkable contracts` to `12 @runtime_checkable contracts`.

- [ ] **Step 4: Run the new test + full suite to verify no regression**

Run: `pytest tests/storage/test_protocols_isp_split.py -q && pytest -q`
Expected: PASS; total count == baseline + (new tests). Existing `ReferenceStore`/`MultiVectorStore` consumers still type-check (the stores still expose every method, now partly via inheritance).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py tests/storage/test_protocols_isp_split.py
git commit -m "refactor(storage): ISP split — extract MultiVectorSearchable + GraphSearchable read views"
```

---

## Task 2: `FilterStrategy` enum + `SearchBackend` Protocol

**Files:**
- Create: `python/pydocs_mcp/storage/search_backend.py`
- Test: `tests/storage/test_search_backend.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_search_backend.py
"""SearchBackend capability seam — Protocol + FilterStrategy enum."""
from pydocs_mcp.storage.search_backend import FilterStrategy, SearchBackend


def test_filter_strategy_values():
    assert FilterStrategy.PREFILTER_IDS == "prefilter_ids"
    assert FilterStrategy.SERVER_SIDE == "server_side"
    assert FilterStrategy.RERANK_ONLY == "rerank_only"


def test_search_backend_protocol_surface():
    # A duck-typed object with all accessors satisfies the Protocol.
    class _Stub:
        def lexical(self): return None
        def dense(self): return None
        def multi(self): return None
        def hybrid(self): return None
        def graph(self): return None
        def filter_strategy(self, capability): return FilterStrategy.RERANK_ONLY
        def write_uow_children(self): return ()
        def capabilities(self): return {}

    assert isinstance(_Stub(), SearchBackend)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_search_backend.py -q`
Expected: FAIL with `ModuleNotFoundError: pydocs_mcp.storage.search_backend`.

- [ ] **Step 3: Create `search_backend.py` with the enum + Protocol**

```python
# python/pydocs_mcp/storage/search_backend.py
"""Capability-based SearchBackend seam (spec 2026-05-30-unified-search-backend).

One factory Protocol over the storage capabilities. Accessors return the
read-only ``*Searchable`` view, or ``None`` when the backend lacks that
capability. Ingestion-write participation flows through
``write_uow_children()``. The default :class:`SqliteCompositeBackend`
wraps the existing SQLite / TurboQuant / fast-plaid adapters.
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

    PREFILTER_IDS = "prefilter_ids"   # resolve filter -> id allowlist -> search
    SERVER_SIDE = "server_side"       # push filter into the engine query
    RERANK_ONLY = "rerank_only"       # re-score a pipeline-provided subset


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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_search_backend.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/search_backend.py tests/storage/test_search_backend.py
git commit -m "feat(storage): SearchBackend Protocol + FilterStrategy enum"
```

---

## Task 3: `_TurboQuantReadStore` — per-query dense read adapter

**Files:**
- Modify: `python/pydocs_mcp/storage/search_backend.py`
- Test: `tests/storage/test_turboquant_read_store.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_turboquant_read_store.py
"""_TurboQuantReadStore re-enters a TurboQuantUnitOfWork per query."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.search_backend import _TurboQuantReadStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

_DIM = 8


def _vec(seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_normal(_DIM).astype(np.float32)


@pytest.mark.asyncio
async def test_read_store_returns_hits_without_externally_entered_uow(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    sqlite_factory = build_sqlite_uow_factory(db_path)
    async with sqlite_factory() as uow:
        await uow.packages.upsert(__import__("pydocs_mcp.models", fromlist=["Package"]).Package(name="demo"))
        await uow.chunks.upsert((Chunk(text="alpha", package="demo"), Chunk(text="beta", package="demo")))
        await uow.commit()
    async with sqlite_factory() as uow:
        seeded = await uow.chunks.list(filter={"package": "demo"})
    ids = [c.id for c in seeded]
    async with TurboQuantUnitOfWork(index_path=tq_path, dim=_DIM, bit_width=4) as tq:
        await tq.add_vectors(ids, [_vec(i) for i in ids])
        await tq.commit()

    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=4,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    # No external `async with` — the store opens its own read uow per call.
    out = await store.vector_search(_vec(ids[0]).tolist(), limit=5)
    assert len(out) > 0


@pytest.mark.asyncio
async def test_read_store_empty_tq_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"  # never written
    open_index_database(db_path).close()
    store = _TurboQuantReadStore(
        tq_path=tq_path,
        dim=_DIM,
        bit_width=4,
        candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
        chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
    )
    out = await store.vector_search(_vec(0).tolist(), limit=5)
    assert out == ()
```

> Implementation note: confirm `Chunk`/`Package` constructor kwargs against `python/pydocs_mcp/models.py` while writing the test (the survey shows `Chunk(text=..., package=...)`; adjust to the real required fields). The behavior asserted (non-empty hits / empty-tq → `()`) is what matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/storage/test_turboquant_read_store.py -q`
Expected: FAIL with `ImportError: cannot import name '_TurboQuantReadStore'`.

- [ ] **Step 3: Add `_TurboQuantReadStore` to `search_backend.py`**

```python
from collections.abc import Sequence
from pathlib import Path
from dataclasses import dataclass

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.turboquant_store import TurboQuantVectorStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork
from pydocs_mcp.storage.protocols import CandidateIdResolver, ChunkHydrator, Filter
# (import names per turboquant_store.py; CandidateIdResolver/ChunkHydrator are
#  the callable aliases used there — confirm exact import path during impl.)


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
        filter: Filter | None = None,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/storage/test_turboquant_read_store.py -q`
Expected: PASS (both cases).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/search_backend.py tests/storage/test_turboquant_read_store.py
git commit -m "feat(storage): _TurboQuantReadStore — per-query dense read adapter"
```

---

## Task 4: `SqliteCompositeBackend` + `backend_registry` + `build_search_backend`

**Files:**
- Modify: `python/pydocs_mcp/storage/search_backend.py`
- Test: `tests/storage/test_search_backend.py` (extend)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/storage/test_search_backend.py
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.protocols import TextSearchable, VectorSearchable
from pydocs_mcp.storage.search_backend import (
    FilterStrategy,
    SqliteCompositeBackend,
    build_search_backend,
)


def _cfg() -> AppConfig:
    return AppConfig.load()


def test_composite_backend_capabilities_default(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    caps = be.capabilities()
    assert caps == {"lexical": True, "dense": True, "multi": False, "hybrid": False, "graph": True}


def test_composite_backend_accessor_types(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert isinstance(be.lexical(), TextSearchable)
    assert isinstance(be.dense(), VectorSearchable)
    assert be.hybrid() is None
    assert be.multi() is None              # LI disabled by default
    assert be.graph() is not None


def test_composite_filter_strategy_per_capability(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    assert be.filter_strategy("dense") is FilterStrategy.PREFILTER_IDS
    assert be.filter_strategy("multi") is FilterStrategy.RERANK_ONLY


def test_write_uow_children_count_default(tmp_path: Path):
    be = SqliteCompositeBackend(config=_cfg(), db_path=tmp_path / "x.db", tq_path=tmp_path / "x.tq")
    # SQLite + TurboQuant (dense always wired); no fast-plaid when LI off.
    assert len(be.write_uow_children()) == 2


def test_build_search_backend_resolves_default_kind(tmp_path: Path):
    be = build_search_backend(_cfg(), db_path=tmp_path / "x.db")
    assert isinstance(be, SqliteCompositeBackend)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/storage/test_search_backend.py -q`
Expected: FAIL with `ImportError: cannot import name 'SqliteCompositeBackend'`.

- [ ] **Step 3: Implement the backend, registry, and factory**

Add to `search_backend.py`. Reuse existing factory helpers for the write children and the SQLite read adapters; derive the `.tq` path via the canonical helper:

```python
from pydocs_mcp.db import build_connection_provider, turboquant_path_for_project
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.serialization import ComponentRegistry  # the registry class
from pydocs_mcp.storage.composite_uow import build_composite_uow_factory
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.protocols import GraphSearchable, MultiVectorSearchable
from pydocs_mcp.storage.sqlite import SqliteReferenceStore, SqliteVectorStore


@dataclass(frozen=True, slots=True)
class SqliteCompositeBackend:
    """Default backend: SQLite FTS (lexical) + TurboQuant (dense) + fast-plaid
    (multi, when ``late_interaction.enabled``) + SQLite reference graph."""

    config: AppConfig
    db_path: Path
    tq_path: Path

    def lexical(self) -> TextSearchable:
        return SqliteVectorStore(provider=build_connection_provider(self.db_path))

    def dense(self) -> VectorSearchable:
        return _TurboQuantReadStore(
            tq_path=self.tq_path,
            dim=self.config.embedding.dim,
            bit_width=self.config.embedding.bit_width,
            candidate_id_resolver=build_sqlite_candidate_id_resolver(self.db_path),
            chunk_hydrator=build_sqlite_chunk_hydrator(self.db_path),
        )

    def multi(self) -> MultiVectorSearchable | None:
        if not self.config.late_interaction.enabled:
            return None
        # LI retrieval consumes uow.multi_vectors via uow_factory (the shipped
        # LateInteractionScorerStep path, unchanged). This accessor returns a
        # read view for capability negotiation; reads still flow through the
        # composite UoW built from write_uow_children().
        return _FastPlaidReadStore(
            sidecar_path=self.db_path.parent / f"{self.db_path.stem}.plaid",
            db_path=self.db_path,
            pipeline_hash=self.config.ingestion_pipeline_hash,
            device=self.config.late_interaction.device,
        )

    def hybrid(self) -> None:
        return None

    def graph(self) -> GraphSearchable:
        return SqliteReferenceStore(provider=build_connection_provider(self.db_path))

    def filter_strategy(self, capability: Literal["dense", "multi"]) -> FilterStrategy:
        return {
            "dense": FilterStrategy.PREFILTER_IDS,
            "multi": FilterStrategy.RERANK_ONLY,
        }[capability]

    def write_uow_children(self) -> tuple[Callable[[], UnitOfWork], ...]:
        children: list[Callable[[], UnitOfWork]] = [build_sqlite_uow_factory(self.db_path)]
        embed = self.config.embedding
        tq_path = self.tq_path
        children.append(
            lambda: TurboQuantUnitOfWork(index_path=tq_path, dim=embed.dim, bit_width=embed.bit_width),
        )
        if self.config.late_interaction.enabled:
            from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork

            sidecar = self.db_path.parent / f"{self.db_path.stem}.plaid"
            db_path = self.db_path
            pipeline_hash = self.config.ingestion_pipeline_hash
            device = self.config.late_interaction.device
            children.append(
                lambda: FastPlaidUnitOfWork(
                    sidecar_path=sidecar, db_path=db_path,
                    pipeline_hash=pipeline_hash, device=device,
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


backend_registry: ComponentRegistry = ComponentRegistry()


def _sqlite_composite_factory(config: AppConfig, *, db_path: Path) -> SearchBackend:
    tq_path = db_path.with_suffix(".tq")
    return SqliteCompositeBackend(config=config, db_path=db_path, tq_path=tq_path)


# Registered under the YAML key. Mirrors @step_registry.register(...).
_BACKEND_FACTORIES: dict[str, Callable[..., SearchBackend]] = {
    "sqlite_composite": _sqlite_composite_factory,
}


def build_search_backend(config: AppConfig, db_path: Path) -> SearchBackend:
    """Resolve the configured backend kind to a SearchBackend (spec §11)."""
    kind = config.search_backend.kind  # added in Task 5
    try:
        factory = _BACKEND_FACTORIES[kind]
    except KeyError as e:
        raise ValueError(
            f"unknown search_backend.kind={kind!r}; "
            f"registered: {sorted(_BACKEND_FACTORIES)}. "
            f"Set search_backend.kind in your AppConfig YAML.",
        ) from e
    return factory(config, db_path=db_path)
```

Also add a minimal `_FastPlaidReadStore` (mirrors `_TurboQuantReadStore`, opening a `FastPlaidUnitOfWork` per `score()` call):

```python
@dataclass(frozen=True, slots=True)
class _FastPlaidReadStore:
    sidecar_path: Path
    db_path: Path
    pipeline_hash: str
    device: str = "cpu"

    async def score(self, query_embedding, *, subset_chunk_ids, top_k):
        from pydocs_mcp.storage.fast_plaid_uow import FastPlaidUnitOfWork

        async with FastPlaidUnitOfWork(
            sidecar_path=self.sidecar_path, db_path=self.db_path,
            pipeline_hash=self.pipeline_hash, device=self.device,
        ) as uow:
            return await uow.score(query_embedding, subset_chunk_ids=subset_chunk_ids, top_k=top_k)
```

> Implementation note: `build_search_backend` reads `config.search_backend.kind`, which Task 5 adds. Until Task 5 lands, the `test_build_search_backend_resolves_default_kind` test will fail on the missing attribute. **Reorder if needed:** if running strictly task-by-task, gate that one assertion behind Task 5, or implement Task 5's `SearchBackendConfig` first. The simplest path is to do Task 5 immediately after Step 3 here, then run Step 4. (The two-line `kind` field is the only cross-dependency.)

- [ ] **Step 4: Run the suite**

Run: `pytest tests/storage/test_search_backend.py -q && pytest -q`
Expected: PASS once Task 5's `search_backend.kind` exists; otherwise the default-kind test fails on the missing config attr only.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/search_backend.py tests/storage/test_search_backend.py
git commit -m "feat(storage): SqliteCompositeBackend + backend registry + build_search_backend"
```

---

## Task 5: `SearchBackendConfig` + `AppConfig.search_backend` + default YAML

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (add sub-model near `EmbeddingConfig` L301; add field to `AppConfig` near L479)
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (after the `embedding:` block, ~L91)
- Test: `tests/retrieval/test_search_backend_config.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_search_backend_config.py
"""search_backend YAML overlay parses; default kind is sqlite_composite."""
from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig


def test_default_search_backend_kind_is_sqlite_composite():
    cfg = AppConfig.load()
    assert cfg.search_backend.kind == "sqlite_composite"


def test_search_backend_overlay_parses(tmp_path: Path):
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("search_backend:\n  kind: sqlite_composite\n")
    cfg = AppConfig.load(explicit_path=overlay)
    assert cfg.search_backend.kind == "sqlite_composite"
    # dim/bit_width remain sourced from embedding — single source of truth.
    assert cfg.embedding.dim == 384
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/retrieval/test_search_backend_config.py -q`
Expected: FAIL with `AttributeError: 'AppConfig' object has no attribute 'search_backend'`.

- [ ] **Step 3: Add the sub-model + field + YAML block**

In `config.py`, add near the other sub-models:

```python
class SearchBackendConfig(BaseModel):
    """Which storage backend serves retrieval capabilities (spec §8.1).

    ``dim`` / ``bit_width`` are NOT duplicated here — they stay sourced from
    :class:`EmbeddingConfig` (single source of truth). Remote-backend blocks
    (qdrant/elasticsearch) are documented extension points, not parsed yet.
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = "sqlite_composite"

    def compute_identity(self) -> str:
        """Identity string folded into the pipeline hash (spec §10)."""
        return f"search_backend={self.kind}"
```

Add the field to `AppConfig` (near L479, beside `embedding`):

```python
    search_backend: SearchBackendConfig = Field(default_factory=SearchBackendConfig)
```

In `default_config.yaml`, after the `embedding:` block:

```yaml
search_backend:
  kind: sqlite_composite
```

- [ ] **Step 4: Run the test + suite**

Run: `pytest tests/retrieval/test_search_backend_config.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py python/pydocs_mcp/defaults/default_config.yaml tests/retrieval/test_search_backend_config.py
git commit -m "feat(config): SearchBackendConfig + AppConfig.search_backend (default sqlite_composite)"
```

---

## Task 6: Composition-root convergence — `build_retrieval_context` sources from the backend (fixes #64 for production)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/factories.py` (`build_retrieval_context`, L31-78)
- Test: `tests/retrieval/test_build_retrieval_context_dense.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_build_retrieval_context_dense.py
"""build_retrieval_context wires a VectorSearchable dense store from the backend."""
from pathlib import Path

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.storage.protocols import VectorSearchable


def test_context_vector_store_is_vector_searchable(tmp_path: Path):
    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    ctx = build_retrieval_context(db_path, AppConfig.load())
    # The #64 fix: vector_store now answers vector_search (not FTS-only).
    assert isinstance(ctx.vector_store, VectorSearchable)
    assert ctx.uow_factory is not None
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/retrieval/test_build_retrieval_context_dense.py -q`
Expected: FAIL — current `vector_store=SqliteVectorStore(...)` is `TextSearchable`, not `VectorSearchable` (no `vector_search`).

- [ ] **Step 3: Rewire `build_retrieval_context`**

Replace the body (L31-78) so stores come from the backend:

```python
def build_retrieval_context(db_path: Path, config: AppConfig) -> BuildContext:
    """Canonical factory for retrieval-time :class:`BuildContext`.

    Sources the dense store + write-UoW children from the configured
    SearchBackend so production + benchmark share one wiring path (spec
    §11). Dense is always wired (empty ``.tq`` → ``()``); LI reads flow
    through ``uow.multi_vectors`` via the composite uow_factory.
    """
    from pydocs_mcp.storage.composite_uow import build_composite_uow_factory
    from pydocs_mcp.storage.search_backend import build_search_backend

    provider = build_connection_provider(db_path)
    embedder = build_embedder(config.embedding)
    backend = build_search_backend(config, db_path)
    uow_factory = build_composite_uow_factory(backend.write_uow_children())
    return BuildContext(
        connection_provider=provider,
        vector_store=backend.dense(),
        module_member_store=SqliteModuleMemberRepository(provider=provider),
        app_config=config,
        embedder=embedder,
        llm_client=build_llm_client(config.llm),
        filter_adapter=SqliteFilterAdapter(),
        multi_vector_embedder=build_multi_vector_embedder(config.late_interaction),
        uow_factory=uow_factory,
    )
```

> Note: the lexical (BM25) leg of hybrid pipelines uses `ChunkFetcherStep`/`bm25_scorer`, which read `connection_provider` / the SQLite path, not `context.vector_store`. Confirm by running the hybrid pipeline tests in Step 4 — if any step relied on `vector_store` being the FTS store, it will surface there. (Survey shows `DenseFetcherStep` is the only `vector_store` consumer.)

- [ ] **Step 4: Run the new test + full suite**

Run: `pytest tests/retrieval/test_build_retrieval_context_dense.py -q && pytest -q`
Expected: new test PASS. Full suite green (watch `tests/retrieval/`, `tests/pipelines/`, `tests/integration/test_default_install_no_torch.py`). Fix any test that asserted `vector_store` was the FTS store by updating it to the dense store / `connection_provider`.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/factories.py tests/retrieval/test_build_retrieval_context_dense.py
git commit -m "fix(retrieval): wire dense store + composite uow_factory from SearchBackend (#64 prod half)"
```

---

## Task 7: Invariant A — no silent dense fallback at pipeline-build time

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/dense_fetcher.py` (`from_dict`, L98-108)
- Test: `tests/retrieval/test_dense_fetcher_requires_vector_searchable.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_dense_fetcher_requires_vector_searchable.py
"""Invariant A: a dense step + non-vector store raises at build time."""
import pytest

from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep


class _FtsOnly:
    async def text_search(self, query_terms, limit, filter=None):
        return ()


class _Embedder:
    async def embed_query(self, text):  # minimal
        return [0.0]


def test_dense_fetcher_rejects_non_vector_searchable_store():
    ctx = BuildContext(vector_store=_FtsOnly(), embedder=_Embedder())
    with pytest.raises(ValueError) as exc:
        DenseFetcherStep.from_dict({"type": "dense_fetcher"}, ctx)
    msg = str(exc.value)
    assert "vector_search" in msg or "VectorSearchable" in msg
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/retrieval/test_dense_fetcher_requires_vector_searchable.py -q`
Expected: FAIL — current `from_dict` only checks `is None`, so an FTS-only store passes and the failure is deferred (the silent #64 path).

- [ ] **Step 3: Strengthen `from_dict`**

In `dense_fetcher.py`, update the guard (L98-108):

```python
    @classmethod
    def from_dict(cls, data: Mapping, context: BuildContext) -> DenseFetcherStep:
        from pydocs_mcp.storage.protocols import VectorSearchable

        if context.vector_store is None or context.embedder is None:
            raise ValueError(
                "DenseFetcherStep requires BuildContext.vector_store + "
                "BuildContext.embedder; provide both at server/CLI startup "
                "via build_retrieval_context(...).",
            )
        if not isinstance(context.vector_store, VectorSearchable):
            raise ValueError(
                "DenseFetcherStep requires a dense-capable vector_store "
                "(VectorSearchable with vector_search). The configured "
                "search_backend does not provide dense retrieval — set "
                "search_backend.kind to a dense-capable backend in your "
                "AppConfig YAML, or remove the dense step from this pipeline. "
                "(no silent BM25 fallback — spec invariant A / #64).",
            )
        return cls(
            store=context.vector_store,
            embedder=context.embedder,
            limit=data.get("limit", _DEFAULT_LIMIT),
        )
```

- [ ] **Step 4: Run the test + suite**

Run: `pytest tests/retrieval/test_dense_fetcher_requires_vector_searchable.py -q && pytest -q`
Expected: PASS. (The real `_TurboQuantReadStore` satisfies `VectorSearchable`, so production pipelines still build.)

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/dense_fetcher.py tests/retrieval/test_dense_fetcher_requires_vector_searchable.py
git commit -m "fix(retrieval): DenseFetcherStep requires VectorSearchable — no silent BM25 fallback (#64 invariant A)"
```

---

## Task 8: Invariant C — startup capability diagnostic

**Files:**
- Modify: `python/pydocs_mcp/storage/search_backend.py` (add `format_capabilities`)
- Modify: `python/pydocs_mcp/server.py` (`run`, ~L101) + `python/pydocs_mcp/__main__.py` (index/serve paths)
- Test: `tests/storage/test_capability_diagnostic.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_capability_diagnostic.py
"""Capability diagnostic renders the active matrix (spec invariant C)."""
from pathlib import Path

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities


def test_format_capabilities_default(tmp_path: Path):
    be = build_search_backend(AppConfig.load(), db_path=tmp_path / "x.db")
    line = format_capabilities(be)
    assert "SearchBackend" in line
    assert "lexical" in line and "dense" in line
    # checkmark/cross convey on/off without parsing booleans
    assert "multi" in line
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/storage/test_capability_diagnostic.py -q`
Expected: FAIL with `ImportError: cannot import name 'format_capabilities'`.

- [ ] **Step 3: Add `format_capabilities` + wire it into the composition roots**

In `search_backend.py`:

```python
def format_capabilities(backend: SearchBackend) -> str:
    """One-line capability matrix for the startup diagnostic (spec §7 C)."""
    caps = backend.capabilities()
    name = type(backend).__name__
    cells = " ".join(f"{k}{'✓' if v else '✗'}" for k, v in caps.items())
    return f"SearchBackend={name}: {cells}"
```

In `server.py` `run()`, after `context = build_retrieval_context(db_path, config)` (L101), add:

```python
    from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities

    logger.info(format_capabilities(build_search_backend(config, db_path)))
```

In `__main__.py`, after the indexing backend is constructed (Task 9 introduces it there), log the same line so `pydocs-mcp index` surfaces the matrix. (CLI surface = a log line, NOT a new MCP/CLI param — per the MCP-surface rule.)

- [ ] **Step 4: Run the test + suite**

Run: `pytest tests/storage/test_capability_diagnostic.py -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/search_backend.py python/pydocs_mcp/server.py tests/storage/test_capability_diagnostic.py
git commit -m "feat(storage): startup SearchBackend capability diagnostic (#64 invariant C)"
```

---

## Task 9: Production indexing uses `build_search_backend` write children

**Files:**
- Modify: `python/pydocs_mcp/__main__.py` (`_run_indexing`, L349-392)
- Test: `tests/test_cli_indexing_backend.py` (create) or extend an existing CLI test

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_indexing_backend.py
"""Production indexing builds the composite UoW via the SearchBackend."""
from pathlib import Path

import pytest

from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.storage.composite_uow import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend


@pytest.mark.asyncio
async def test_backend_write_children_yield_dense_capable_uow(tmp_path: Path):
    from pydocs_mcp.db import open_index_database

    db_path = tmp_path / "x.db"
    open_index_database(db_path).close()
    backend = build_search_backend(AppConfig.load(), db_path=db_path)
    factory = build_composite_uow_factory(backend.write_uow_children())
    async with factory() as uow:
        # uow.vectors is the TurboQuant child, not NullVectorStore.
        assert type(uow.vectors).__name__ == "TurboQuantUnitOfWork"
```

- [ ] **Step 2: Run to verify it fails (or passes trivially)**

Run: `pytest tests/test_cli_indexing_backend.py -q`
Expected: PASS already if Task 4 landed (the backend exists). If so, treat this as the regression guard; the real change below is the `__main__.py` rewiring — keep this test and add the wiring.

- [ ] **Step 3: Rewire `_run_indexing`**

Replace the `build_sqlite_plus_turboquant_uow_factory(...)` block (L349-354) with the backend path:

```python
    from pydocs_mcp.storage.composite_uow import build_composite_uow_factory
    from pydocs_mcp.storage.search_backend import build_search_backend, format_capabilities

    backend = build_search_backend(config, db_path)
    logger.info(format_capabilities(backend))
    uow_factory = build_composite_uow_factory(backend.write_uow_children())
```

`IndexingService(uow_factory=uow_factory)` (L392) is unchanged. The `.tq` path is now owned by the backend (`db_path.with_suffix(".tq")`); remove the now-unused `_tq_path_for_args` call here if nothing else references it (grep first — keep the helper if `--force` cleanup uses it).

> Note: confirm the backend's `.tq` location matches the prior `turboquant_path_for_project`-derived path so existing on-disk caches aren't orphaned. If they differ, either (a) have `_sqlite_composite_factory` derive `tq_path` via `turboquant_path_for_project` when indexing a project dir, or (b) accept a one-time cache rebuild (the pipeline-hash fold in Task 11 will invalidate stale caches anyway). Pick (a) if the prod cache path must stay stable; document the choice in the commit.

- [ ] **Step 4: Run CLI/indexing tests + suite**

Run: `pytest tests/test_cli_indexing_backend.py -q && pytest tests/ -k "cli or indexing" -q && pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/__main__.py tests/test_cli_indexing_backend.py
git commit -m "refactor(cli): production indexing builds composite UoW via SearchBackend (#64)"
```

---

## Task 10: Benchmark `_do_index` converges on the shared backend path

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/systems/pydocs.py` (`_do_index`, L131-198)
- Test: `benchmarks/tests/test_pydocs_dense_index.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/test_pydocs_dense_index.py
"""Benchmark _do_index persists the .tq sidecar (was dropped pre-#64)."""
from pathlib import Path

import pytest

from benchmarks.eval.systems.pydocs import PydocsMcpSystem
from pydocs_mcp.retrieval.config import AppConfig


@pytest.mark.asyncio
async def test_do_index_persists_tq_sidecar(tmp_path: Path):
    corpus = tmp_path / "corpus"
    (corpus / "pkg").mkdir(parents=True)
    (corpus / "pkg" / "__init__.py").write_text('def alpha():\n    """Alpha."""\n')
    sysm = PydocsMcpSystem()
    cfg = AppConfig.load()
    sysm._db_path = tmp_path / "index.sqlite"
    from pydocs_mcp.db import open_index_database

    open_index_database(sysm._db_path).close()
    await sysm._do_index(corpus, cfg)
    assert sysm._db_path.with_suffix(".tq").exists()
```

> Confirm `PydocsMcpSystem` construction + how `_db_path` is set in the real cache flow (survey: set in `index()` before `_do_index`). Adjust the harness setup to match; the asserted behavior (`.tq` exists after indexing) is the regression contract.

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/test_pydocs_dense_index.py -q`
Expected: FAIL — current `_do_index` uses `build_sqlite_uow_factory` (SQLite-only), so no `.tq` is written.

- [ ] **Step 3: Rewire `_do_index`**

Replace the SQLite-only factory wiring (L151-152) with the backend write children, and feed the same `uow_factory` to both `IndexingService` and `ProjectIndexer`:

```python
    from pydocs_mcp.storage.composite_uow import build_composite_uow_factory
    from pydocs_mcp.application.indexing_service import IndexingService
    from pydocs_mcp.storage.search_backend import build_search_backend

    backend = build_search_backend(config, self._db_path)
    uow_factory = build_composite_uow_factory(backend.write_uow_children())
    indexing_service = IndexingService(uow_factory=uow_factory)
```

Keep the rest (`build_ingestion_pipeline`, `ProjectIndexer`, `rebuild_index`) — pass this `uow_factory` to `ProjectIndexer(uow_factory=uow_factory)` as before. The `build_sqlite_indexing_service` / `build_sqlite_uow_factory` imports are dropped.

- [ ] **Step 4: Run the bench test + bench suite**

Run: `PYTHONPATH=benchmarks/src pytest benchmarks/tests/test_pydocs_dense_index.py -q && PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/src/benchmarks/eval/systems/pydocs.py benchmarks/tests/test_pydocs_dense_index.py
git commit -m "fix(benchmarks): _do_index persists dense .tq via SearchBackend (#64 bench half)"
```

---

## Task 11: Cache identity — fold backend into the pipeline hash

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (`ingestion_pipeline_hash`, L563-612)
- Test: `tests/retrieval/test_pipeline_hash_backend_identity.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_pipeline_hash_backend_identity.py
"""Backend identity folds into the ingestion pipeline hash (spec §10)."""
from pydocs_mcp.retrieval.config import AppConfig


def test_changing_backend_kind_changes_pipeline_hash(tmp_path):
    base = AppConfig.load()
    overlay = tmp_path / "pydocs-mcp.yaml"
    overlay.write_text("search_backend:\n  kind: other_backend\n")
    changed = AppConfig.load(explicit_path=overlay)
    assert base.ingestion_pipeline_hash != changed.ingestion_pipeline_hash
```

> `other_backend` need not be registered — the hash must change on the config string alone. If `SearchBackendConfig` has `extra="forbid"` on `kind`, any non-empty string is valid (it's a free `str`), so this overlay parses.

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/retrieval/test_pipeline_hash_backend_identity.py -q`
Expected: FAIL — both hashes equal (backend identity not folded yet).

- [ ] **Step 3: Fold backend identity into the hash**

In `ingestion_pipeline_hash` (L563-612), add the backend identity to the hashed `identity` bytes before the final digest:

```python
        identity = self.embedding.compute_pipeline_hash().encode("utf-8")
        identity += b"|" + self.search_backend.compute_identity().encode("utf-8")
        if b"embed_chunks_multi_vector" in yaml_bytes:
            identity += b"|" + self.late_interaction.compute_pipeline_hash().encode("utf-8")
        return hashlib.sha256(identity + b"|" + yaml_bytes).hexdigest()
```

This propagates automatically to the benchmark per-sample cache key (`_bench_cache.make_key` folds `compute_ingestion_pipeline_hash()`), so no change to `_bench_cache.py` is needed.

- [ ] **Step 4: Run the test + hash suite + bench cache test**

Run: `pytest tests/retrieval/test_pipeline_hash_backend_identity.py -q && pytest tests/ -k "pipeline_hash or content_hash" -q && PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -k cache -q`
Expected: PASS. (If a golden-hash test pins an exact digest, update the expected value — the fold intentionally changes it.)

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py tests/retrieval/test_pipeline_hash_backend_identity.py
git commit -m "feat(config): fold SearchBackend identity into ingestion pipeline hash (#64 cache identity)"
```

---

## Task 12: The #64 regression test — index dense → search → real dense hits

**Files:**
- Test: `tests/retrieval/test_dense_wiring_regression.py` (create)

- [ ] **Step 1: Write the test (the keystone — it must fail on a pre-fix tree)**

```python
# tests/retrieval/test_dense_wiring_regression.py
"""#64 regression: dense indexing + retrieval through the converged path
produce real dense hits, not a silent BM25 fallback."""
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.factories import build_retrieval_context
from pydocs_mcp.storage.composite_uow import build_composite_uow_factory
from pydocs_mcp.storage.search_backend import build_search_backend


@pytest.mark.asyncio
async def test_dense_indexing_then_retrieval_hits_tq(tmp_path: Path):
    db_path = tmp_path / "index.sqlite"
    open_index_database(db_path).close()
    cfg = AppConfig.load()

    backend = build_search_backend(cfg, db_path)
    uow_factory = build_composite_uow_factory(backend.write_uow_children())

    # Index two chunks WITH embeddings through the IndexingService write path.
    from pydocs_mcp.application.indexing_service import IndexingService
    from tests._fakes import make_indexed_chunks_with_embeddings  # see note

    svc = IndexingService(uow_factory=uow_factory)
    package, chunks = make_indexed_chunks_with_embeddings(cfg.embedding.dim)
    await svc.reindex_package(package, chunks, module_members=())

    # The .tq sidecar exists and is non-empty (Half A fixed).
    tq_path = db_path.with_suffix(".tq")
    assert tq_path.exists()

    # Retrieval through the converged context returns dense hits (Half B fixed).
    ctx = build_retrieval_context(db_path, cfg)
    hits = await ctx.vector_store.vector_search(
        list(chunks[0].embedding), limit=5,
    )
    assert len(hits) > 0
    assert hits[0].retriever_name == "turboquant_dense"
```

> Note: `make_indexed_chunks_with_embeddings` is a small local helper — build two `Chunk`s with `np.ndarray` embeddings of `dim` and a `content_hash` (mirror `tests/application/test_indexing_writes_vectors.py:66-100`, which already does `_chunk(... _vec(...))` + `build_sqlite_plus_turboquant_uow_factory`). Inline it in the test file rather than adding to `_fakes.py` if simpler. The assertions (`.tq` exists; `vector_search` returns hits with `retriever_name == "turboquant_dense"`) are the regression contract.

- [ ] **Step 2: Run on the current tree to confirm it captures the bug**

Run: `git stash` (temporarily revert Tasks 6/9 if validating the failure mode is desired) then `pytest tests/retrieval/test_dense_wiring_regression.py -q`; restore with `git stash pop`. On the post-Task-6 tree it should PASS; on a pre-fix tree it FAILS (no `.tq`, FTS-only `vector_store`). Skip the stash dance if confident — just run it green.

Run: `pytest tests/retrieval/test_dense_wiring_regression.py -q`
Expected: PASS on the current (post-Task-6) tree.

- [ ] **Step 3: (no impl — this is a guard test)**

- [ ] **Step 4: Run the full suite**

Run: `pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/retrieval/test_dense_wiring_regression.py
git commit -m "test(retrieval): #64 regression — dense index+retrieval produces real dense hits"
```

---

## Task 13: Delete `HybridSqliteTurboStore` + scrub references

**Files:**
- Delete: `python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py`
- Delete: `tests/storage/test_hybrid_sqlite_turbo_store.py`
- Modify: `python/pydocs_mcp/storage/fast_plaid_uow.py` (docstring L295), `python/pydocs_mcp/retrieval/steps/rrf_fusion.py` (docstring)

- [ ] **Step 1: Confirm there are no runtime importers**

Run: `grep -rn "HybridSqliteTurboStore" python/ tests/ benchmarks/`
Expected: only the two files to delete + two docstring mentions. If any **runtime** import exists outside tests, STOP and reassess.

- [ ] **Step 2: Delete the store + its test; scrub docstrings**

```bash
git rm python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py tests/storage/test_hybrid_sqlite_turbo_store.py
```

In `fast_plaid_uow.py` (L295), replace the `HybridSqliteTurboStore.clear_all` reference with backend-neutral wording (e.g. "after this returns, the sidecar holds no live vectors and `chunk_multi_vector_ids` is empty"). In `rrf_fusion.py`, remove the "future HybridSqliteTurboStore composes this directly" sentence — pipeline-level RRF is the only fusion path; native `hybrid_search` exists only for a single backend that fuses server-side.

- [ ] **Step 3: Verify removal is clean**

Run: `grep -rn "HybridSqliteTurboStore" python/ tests/ benchmarks/`
Expected: no matches.

- [ ] **Step 4: Run the full suite + ruff**

Run: `pytest -q && ruff check python/ tests/ benchmarks/`
Expected: PASS / no lint errors (no dangling imports).

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor(storage): delete dead HybridSqliteTurboStore + scrub docstrings (spec §12)"
```

---

## Task 14: README-jargon audit + final verification gauntlet

**Files:** none (verification only)

- [ ] **Step 1: README jargon audit** (the spec's own rule)

Run:
```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
  -not -path "*/node_modules/*" -not -path "*/.git/*" | \
  xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" || echo "clean"
```
Expected: `clean`. (If the new feature added any README text, scrub internal refs.)

- [ ] **Step 2: Full Python suite**

Run: `pytest -q && PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`
Expected: all green; count == baseline + net-new tests (Tasks 1-12) − deleted (Task 13's test).

- [ ] **Step 3: Lint**

Run: `ruff check python/ tests/ benchmarks/`
Expected: no errors.

- [ ] **Step 4: Rust checks (unchanged, but confirm)**

Run: `cargo fmt --check && cargo clippy -- -D warnings && cargo test`
Expected: PASS (no Rust touched; this is a guardrail).

- [ ] **Step 5: Final commit if anything was scrubbed; otherwise none**

```bash
git add -A && git commit -m "chore: final verification + README jargon scrub for SearchBackend PR" || echo "nothing to commit"
```

---

## Spec coverage check (self-review)

| Spec AC | Task |
|---|---|
| AC-1 dense persists `.tq` + returns dense hits | Tasks 6, 9, 12 |
| AC-2 LI persists `.plaid` + re-ranks | Task 4 (`multi`/write children), 10 (bench), existing LI path |
| AC-3 SearchBackend Protocol + SqliteCompositeBackend | Tasks 2, 4 |
| AC-4 `MultiVectorSearchable`/`GraphSearchable` extracted; stores extend | Task 1 |
| AC-5 `filter_strategy` dense→PREFILTER_IDS, multi→RERANK_ONLY | Task 4 |
| AC-6 `write_uow_children` composes; shape-dispatch unchanged; no dense child if `.dense() None` | Tasks 4, 9, 10 |
| AC-7 invariant A raises at build time | Task 7 |
| AC-8 invariant C diagnostic; no new MCP param | Task 8 |
| AC-9 invariant G read None / NullVectorStore silent | Tasks 4 (`multi() None`) + existing NullVectorStore (unchanged) |
| AC-10 `search_backend` YAML parses; registry; dim/bit_width from embedding | Tasks 4, 5 |
| AC-11 backend identity in pipeline_hash + bench key | Task 11 |
| AC-12 prod + bench share `build_search_backend` | Tasks 6, 9, 10 |
| AC-13 HybridSqliteTurboStore deleted; suite+ruff green | Tasks 13, 14 |
| AC-14 ES/Qdrant docs-only; no remote imports | Out of scope by construction; Task 13/14 grep confirms no remote imports |

**Type-consistency note:** accessor names (`lexical/dense/multi/hybrid/graph`), `write_uow_children`, `capabilities`, `filter_strategy`, `format_capabilities`, `build_search_backend`, `SearchBackendConfig.kind`, `compute_identity` are used identically across Tasks 2-11. `_TurboQuantReadStore` fields match `TurboQuantVectorStore`'s constructor (Task 3 ↔ survey).

**Known cross-task ordering dependency:** Task 4's `build_search_backend` reads `config.search_backend.kind` from Task 5. Land Task 5 before running Task 4's `build_search_backend` assertion (noted inline in Task 4 Step 3).
