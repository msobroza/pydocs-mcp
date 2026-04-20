# Sub-PR #3 — Storage layer (repositories, vector store, unit of work, filters)

**Status:** Approved (2026-04-19) — ready for implementation planning in a later session
**⚠️ Data-model drift notice (added 2026-04-19 after sub-PR #4 brainstorming):** the canonical data model is defined in **sub-PR #1 §5** (single source of truth). Implementers MUST consult that section for types. This spec's body contains references to shapes that no longer exist.

**Known drift in this spec — do NOT implement these literal forms:**
- `TextSearchable.text_search`, `VectorSearchable.vector_search`, `HybridSearchable.hybrid_search` Protocol signatures returning `tuple[SearchMatch, ...]` — canonical returns `ChunkList` (or analogous list wrapper).
- `SqliteVectorStore.text_search` pseudocode mapping rows to `SearchMatch` — should build `Chunk(text=..., id=..., relevance=..., retriever_name="bm25", metadata={ChunkFilterField.PACKAGE.value: row["package"], ...})` and wrap into `ChunkList`.
- `Bm25ChunkRetriever.retrieve` annotated as returning `tuple[SearchMatch, ...]` — returns `ChunkList`.
- Hardcoded `_CHUNK_COLUMNS` / `_PACKAGE_COLUMNS` / `_MEMBER_COLUMNS` frozensets of literal strings — canonical derives them from `ChunkFilterField` / `ModuleMemberFilterField` StrEnums (minus `SCOPE`, which is derived).
- Raw-string filter keys (`{"package": ...}`, `{"name": ...}`, `{"scope": ...}`, `{"title": {"like": ...}}`) in `IndexingService`, MCP handlers, usage examples — canonical uses `ChunkFilterField.*.value` / `ModuleMemberFilterField.*.value` (StrEnum values are plain strings, so the dict key shape is unchanged; the literal must be the enum member's `.value` for grep-ability and validation).
- `_row_to_chunk` / `_row_to_module_member` building typed-field Chunks / ModuleMembers — canonical wraps ALL non-`text` / non-`id` columns into `metadata`.
- AC items echoing Protocol return types of `tuple[SearchMatch, ...]`.
- Typed-field access `chunk.package`, `chunk.origin` etc. — should be `chunk.metadata[ChunkFilterField.*.value]`.
- §12 "Open items: None" — outdated; there is known drift listed here.

**Missing from this spec (should be added during implementation):** cross-reference to `ChunkFilterField` / `ModuleMemberFilterField` StrEnums defined in sub-PR #1 §5; `AppConfig` model validator cross-checking `metadata_schemas` entries against those StrEnums.

Follow this spec for **storage architecture, filter format infrastructure, `SqliteUnitOfWork`, repository split, row-mapping location, YAML config layering, testing strategy**. Follow sub-PR #1 §5 for **types, field names, enums, `ChunkFilterField`, `ModuleMemberFilterField`, `PipelineResultItem`, canonical `Chunk` / `ModuleMember` shape**.
**Date:** 2026-04-19
**Depends on:** sub-PR #1 (approved) — domain models, Python 3.11+, SQLite schema v2. Sub-PR #2 (approved) — `retrieval/` package, `ConnectionProvider`, `CodeRetrieverPipeline`, `PipelineStage`, `AppConfig` (pydantic-settings + pyyaml).
**Follows-on:** sub-PR #4 (query-side application services + `PackageLookupService`), sub-PR #5 (indexer strategy split + chunking strategies), sub-PR #6 (query parsing + Pydantic at MCP boundary), sub-PR #7 (error-tolerance primitives — `TryStage` etc.).

**Scope additions vs. earlier plan:**
- Filter expression infrastructure (`Filter` tree + `MultiFieldFormat` + `MetadataSchema` + `SqliteFilterAdapter`) ships here, enabling generic filter-driven CRUD on every repository and future swap-in of Qdrant / Chroma / Elasticsearch backends.
- `SearchQuery` is **refactored** (breaking change to sub-PR #1): structured fields `package_filter` / `scope` / `title_filter` move into a generic `pre_filter` + `post_filter` dict pair with `metadata_filter_format` selection.
- `pre_filter` / `post_filter` pipeline stages replace sub-PR #2's `PackageFilterStage` / `ScopeFilterStage` / `TitleFilterStage`.
- `application/` package introduced for `IndexingService` (first DDD Application Service).

---

## 1. Goal

Extract all CRUD SQL from `server.py` and `indexer.py` into a layered storage architecture:

- **Ports (Protocols):** `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`, `UnitOfWork`, `FilterAdapter`.
- **Adapters (concrete):** `SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository`, `SqliteVectorStore`, `SqliteUnitOfWork`, `SqliteFilterAdapter`.
- **Application Service:** `IndexingService` coordinating cross-repository writes with transactional atomicity.
- **Filter infrastructure:** canonical `Filter` tree (Specification pattern) + pluggable input formats (`MultiFieldFormat` ships; `FilterTreeFormat` / `ChromaFormat` / `QdrantFormat` / `ElasticsearchFormat` declared for future) + backend-specific adapters (Adapter pattern).
- **No user-visible behavior change.** MCP tool signatures and output strings byte-identical to sub-PR #2's state when no `pydocs-mcp.yaml` is present.

The work prepares the codebase for heterogeneous backends (Qdrant, Chroma, Elasticsearch, Weaviate) without requiring changes to `IndexingService`, retrievers, or pipeline stages.

---

## 2. Decisions locked in during brainstorming

| Topic | Decision |
|---|---|
| DDL ownership | **Stays in `db.py` for this PR.** `PRAGMA user_version` + schema DDL from sub-PR #1 unchanged. |
| Repository granularity | **One repository per table** (`SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository`). |
| Filter organization | **Abstract `Filter` tree (Specification)** + **`FilterFormat` Protocol** for user-facing formats + **`FilterAdapter` Protocol** for backend translation (Adapter pattern). |
| Formats shipped this PR | **`MultiFieldFormat`** (flat dict, implicit AND, no boolean ops) only. `FilterTreeFormat` / `ChromaFormat` / `QdrantFormat` / `ElasticsearchFormat` declared in the `MetadataFilterFormat` enum, not implemented. |
| Filter tree classes shipped | `FieldEq`, `FieldIn`, `FieldLike`, `All`. `Any_` + `Not` declared but unused in `MultiFieldFormat` — ready for future formats. |
| Backend filter adapter | **`SqliteFilterAdapter`** only. Other backends' adapters ship with their respective backend PRs. |
| `SearchQuery` shape | **Breaking change to sub-PR #1.** Remove `package_filter` / `scope` / `title_filter`. Add `pre_filter: Mapping \| None`, `post_filter: Mapping \| None`, `pre_filter_format: MetadataFilterFormat`, `post_filter_format: MetadataFilterFormat`. Convert `SearchQuery` to `pydantic.dataclasses.dataclass` with `@field_validator` + `@model_validator`. |
| Metadata schema location | **YAML-only.** `AppConfig.metadata_schemas: Mapping[str, tuple[str, ...]]` has **no Python-level default** — the baseline ships in `python/pydocs_mcp/presets/default_config.yaml` and is loaded as the first layer; the user's `pydocs-mcp.yaml` overlays it. Consistent with how `pipelines` defaults work. Validated at retriever `retrieve()` time. |
| Sub-PR #2 filter stages | **Remove** `PackageFilterStage` / `ScopeFilterStage` / `TitleFilterStage`. Replace with `MetadataPostFilterStage` that reads `query.post_filter`. |
| Store Protocol capabilities | **`ChunkStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`** — all `@runtime_checkable`. Concrete classes declare capabilities via multi-Protocol inheritance. |
| `SqliteVectorStore` shape | **Implements `TextSearchable` only** (retrieval-only service). CRUD is handled directly by `SqliteChunkRepository` (which implements `ChunkStore`). |
| Transaction model | **UnitOfWork pattern (DDD).** `UnitOfWork` Protocol + `SqliteUnitOfWork` concrete. Transactions are ambient via `contextvars`; Protocol methods have no `connection` parameter. |
| Application Service | **`IndexingService`** as the first Application Service. Depends only on `PackageStore`, `ChunkStore`, `ModuleMemberStore`, and optional `UnitOfWork`. Backend-agnostic by construction. |
| Application Service placement | **`application/indexing_service.py`** — new `application/` package in this PR (per Option C). Sub-PR #4 adds query-side services there. |
| Indexer coupling | Indexer takes one dependency: `IndexingService`. |
| Testing strategy | **Mixed by layer + one end-to-end smoke test.** Pure unit tests for filter infrastructure; integration tests (real SQLite tmp file) for repositories / vector store / UoW; Protocol-fake unit tests for `IndexingService` and retrievers; one E2E smoke test exercises the full stack. |
| Existing tests | **No existing test is deleted.** Mechanical updates for renamed imports, new SearchQuery shape, and removed/replaced filter stages. |

---

## 3. Architecture overview

```
┌────────────────────────────────────────────────────────────────────┐
│          server.py (MCP async tools) + __main__.py (CLI)           │
│  Translates legacy MCP params (internal, package, topic) to        │
│  SearchQuery.pre_filter dict                                       │
└──────────────────────────┬─────────────────────────────────────────┘
                           │
          reads via                      writes via
          pipelines (sub-PR #2)          IndexingService
                           │                         │
                           ▼                         ▼
     ┌──────────────────────────────┐  ┌────────────────────────────┐
     │  CodeRetrieverPipeline       │  │  IndexingService           │
     │  (from sub-PR #2)            │  │  (application/ — new)      │
     │                              │  │                            │
     │  ChunkRetrievalStage ───▶    │  │  reindex_package()         │
     │    retriever: TextSearchable │  │  remove_package()          │
     │                              │  │  clear_all()               │
     │  MetadataPostFilterStage ◀── │  │                            │
     │    reads query.post_filter   │  │  Depends on Protocols only │
     └──────────────────────────────┘  └────────────────────────────┘
                           │                         │
                           ▼                         ▼
     ┌──────────────────────────────────────────────────────────────┐
     │     Ports (storage/protocols.py — all @runtime_checkable)    │
     │                                                              │
     │  ChunkStore │ PackageStore │ ModuleMemberStore                │
     │  TextSearchable │ VectorSearchable* │ HybridSearchable*       │
     │  UnitOfWork │ ConnectionProvider (from sub-PR #2)             │
     │  FilterAdapter │ FilterFormat (filters.py)                    │
     │                                                              │
     │           * declared, no implementation this PR              │
     └──────────────────────────────────────────────────────────────┘
                           │
                           ▼
     ┌──────────────────────────────────────────────────────────────┐
     │                  Adapters (storage/sqlite.py)                │
     │                                                              │
     │  SqlitePackageRepository(PackageStore)                       │
     │  SqliteChunkRepository(ChunkStore)                           │
     │  SqliteModuleMemberRepository(ModuleMemberStore)             │
     │  SqliteVectorStore(TextSearchable) — composes ChunkRepo      │
     │  SqliteUnitOfWork(UnitOfWork) — contextvar-based transaction │
     │  SqliteFilterAdapter(FilterAdapter)                          │
     └──────────────────────────────────────────────────────────────┘
                           │
                           ▼
     ┌──────────────────────────────────────────────────────────────┐
     │  db.py — schema, PRAGMA user_version, row↔model mapping      │
     │  (from sub-PR #1; unchanged)                                 │
     │                           SQLite (FTS5)                      │
     └──────────────────────────────────────────────────────────────┘
```

Perpendicular: `storage/filters.py` — canonical `Filter` tree, `MetadataFilterFormat` enum, `FilterFormat` Protocol, `MultiFieldFormat`, `MetadataSchema`, `format_registry`.

---

## 4. Scope

### In scope

- New package `python/pydocs_mcp/storage/` with:
  - `storage/protocols.py` — 8 Protocols: `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`, `UnitOfWork`, `FilterAdapter`.
  - `storage/filters.py` — `Filter` tree (4+2 classes), `MetadataFilterFormat` enum, `MultiFieldFormat`, `MetadataSchema`, `FieldSpec`, `format_registry`, filter-tree walk helpers, `_evaluate` for post-filter.
  - `storage/sqlite.py` — 3 repositories, `SqliteVectorStore`, `SqliteUnitOfWork`, `SqliteFilterAdapter`, per-table safe-column constants, contextvar `_sqlite_transaction`, row↔model mapping helpers.
- New package `python/pydocs_mcp/application/` with:
  - `application/indexing_service.py` — `IndexingService` Application Service depending only on Protocols.
- Modifications to sub-PR #1's `models.py`:
  - `SearchQuery` becomes a `pydantic.dataclasses.dataclass`.
  - Removes `package_filter`, `scope`, `title_filter`.
  - Adds `pre_filter`, `post_filter`, `pre_filter_format`, `post_filter_format`.
  - Adds `@field_validator` and `@model_validator` enforcing construction-time invariants and format syntax.
- Modifications to sub-PR #2's `retrieval/` package:
  - `retrieval/retrievers.py`: `Bm25ChunkRetriever` consumes `TextSearchable` (not `ConnectionProvider`); `LikeMemberRetriever` consumes a `ModuleMemberStore` + `TextSearchable`-like abstraction (see §5.7).
  - `retrieval/stages.py`: remove `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`. Add `MetadataPostFilterStage`.
  - `retrieval/config.py`: extend `AppConfig` with `metadata_schemas: Mapping[str, tuple[str, ...]]` field (no Python default), and `settings_customise_sources` layering to load the shipped `presets/default_config.yaml` as the baseline source.
  - New file `python/pydocs_mcp/presets/default_config.yaml` — ships with the package; holds every default value (metadata_schemas, pipeline routes, cache_dir, log_level).
- Modifications to `server.py`:
  - MCP handlers translate legacy params (`internal`, `package`, `topic`) into `SearchQuery.pre_filter` dicts.
  - `list_packages`, `get_package_doc`, `inspect_module` migrate to use repositories via `SqlitePackageRepository` / other stores directly.
- Modifications to `__main__.py` — CLI subcommands continue to work (same translation at CLI level).
- Modifications to `indexer.py` — replaces direct SQL writes with `IndexingService.reindex_package` calls.
- Default YAML presets updated to drop the 3 removed filter stages and add `MetadataPostFilterStage` where applicable.
- New test subtrees `tests/storage/`, `tests/application/` covering every new component.
- Existing `tests/retrieval/` updated mechanically for the `SearchQuery` shape change.

### Out of scope (deferred)

- **Sub-PR #4 — query-side application services:** `SearchDocsService`, `SearchApiService`, `PackageLookupService`, `ModuleIntrospectionService`, `IndexProjectUseCase`. MCP handlers become thin wrappers over these.
- **Sub-PR #5 — indexer strategy split + chunking strategies** (placeholder in sub-PR #2's §13).
- **Sub-PR #6 — query parsing + Pydantic at MCP boundary.**
- **Sub-PR #7 — error-tolerance primitives** (`TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`).
- **Other filter formats:** `FilterTreeFormat`, `ChromaFormat`, `QdrantFormat`, `ElasticsearchFormat`. Declared in `MetadataFilterFormat` enum, not implemented.
- **Other backend adapters:** `QdrantFilterAdapter`, `ChromaFilterAdapter`, `ElasticsearchFilterAdapter`.
- **Other vector stores:** `QdrantVectorStore`, `ChromaVectorStore`, `ElasticsearchVectorStore`, `SqliteWithVecStore`.
- **Other unit-of-work implementations:** `QdrantUnitOfWork`, `CompositeUnitOfWork`.
- **Concrete embedders / dense retrievers / LLM rerank.**
- **Cross-table JOIN filter planning in `SqliteFilterAdapter`** — the adapter stays chunk-table-only (plus separate safe_column sets for each repo).

---

## 5. Domain components

### 5.1 Filter tree + format infrastructure (`storage/filters.py`)

**Filter tree** — canonical representation. 4 implemented classes + 2 declared:

```python
class Filter(Protocol): ...

@dataclass(frozen=True, slots=True)
class FieldEq:   field: str; value: Any
@dataclass(frozen=True, slots=True)
class FieldIn:   field: str; values: tuple[Any, ...]
@dataclass(frozen=True, slots=True)
class FieldLike: field: str; substring: str
@dataclass(frozen=True, slots=True)
class All:       clauses: tuple[Filter, ...]

# Declared but unused in MultiFieldFormat — for future FilterTreeFormat:
@dataclass(frozen=True, slots=True)
class Any_:      clauses: tuple[Filter, ...]
@dataclass(frozen=True, slots=True)
class Not:       clause: Filter
```

**Format enum:**

```python
class MetadataFilterFormat(StrEnum):
    MULTIFIELD    = "multifield"       # shipped
    FILTER_TREE   = "filter_tree"      # future
    CHROMADB      = "chromadb"         # future
    ELASTICSEARCH = "elasticsearch"    # future
    QDRANT        = "qdrant"           # future
```

**`FilterFormat` Protocol** — user-facing format with `validate` + `parse`:

```python
class FilterFormat(Protocol):
    format: MetadataFilterFormat
    def validate(self, native: Any) -> None: ...
    def parse(self, native: Any) -> Filter: ...
```

**`MultiFieldFormat`** — flat dict, implicit AND, no boolean ops. Values may be bare (eq) or `{eq|in|like: value}`. Raises if `$and` / `$or` / `$not` appear (direct user to `filter_tree` format).

**`MetadataSchema`** — declarative allowlist of field names per retriever:

```python
@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    operators: frozenset[str] = frozenset({"eq"})

@dataclass(frozen=True, slots=True)
class MetadataSchema:
    fields: tuple[FieldSpec, ...]
    def field_names(self) -> frozenset[str]: ...
    def validate(self, filter: Filter) -> None:
        unknown = _walk_fields(filter) - self.field_names()
        if unknown:
            raise ValueError(...)
```

**`format_registry`** — shared lookup table keyed by `MetadataFilterFormat`; `MULTIFIELD` registered at import.

### 5.2 Storage Protocols (`storage/protocols.py`)

```python
@runtime_checkable
class PackageStore(Protocol):
    async def upsert(self, package: Package) -> None: ...
    async def get(self, name: str) -> Package | None: ...
    async def list(self, filter: Filter | Mapping | None = None, limit: int | None = None) -> list[Package]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...

@runtime_checkable
class ChunkStore(Protocol):
    async def upsert(self, chunks: Iterable[Chunk]) -> None: ...
    async def list(self, filter=None, limit=None) -> list[Chunk]: ...
    async def delete(self, filter) -> int: ...
    async def count(self, filter=None) -> int: ...
    async def rebuild_index(self) -> None: ...

@runtime_checkable
class ModuleMemberStore(Protocol):
    async def upsert_many(self, members: Iterable[ModuleMember]) -> None: ...
    async def list(self, filter=None, limit=None) -> list[ModuleMember]: ...
    async def delete(self, filter) -> int: ...
    async def count(self, filter=None) -> int: ...

@runtime_checkable
class TextSearchable(Protocol):
    async def text_search(
        self, query_terms: str, limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[SearchMatch, ...]: ...

@runtime_checkable
class VectorSearchable(Protocol):           # declared, no impl this PR
    async def vector_search(
        self, query_vector: Sequence[float], limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[SearchMatch, ...]: ...

@runtime_checkable
class HybridSearchable(Protocol):           # declared, no impl this PR
    async def hybrid_search(
        self, query_terms: str, query_vector: Sequence[float], limit: int,
        filter: Filter | Mapping | None = None,
        *, alpha: float = 0.5,
    ) -> tuple[SearchMatch, ...]: ...

@runtime_checkable
class FilterAdapter(Protocol):
    def adapt(self, filter: Filter) -> Any: ...

class UnitOfWork(Protocol):
    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]: ...
```

Concrete classes explicitly inherit from the Protocols they implement, enabling type-checker verification and runtime `isinstance` dispatch.

### 5.3 Concrete SQLite adapters (`storage/sqlite.py`)

**Ambient transaction via contextvar:**

```python
_sqlite_transaction: ContextVar[sqlite3.Connection | None] = ContextVar(
    "_sqlite_transaction", default=None,
)

@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction connection if set; otherwise acquire fresh."""
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        yield ambient
    else:
        async with provider.acquire() as conn:
            yield conn
```

**`SqliteUnitOfWork`:**

```python
@dataclass(frozen=True, slots=True)
class SqliteUnitOfWork:
    provider: ConnectionProvider

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        async with self.provider.acquire() as conn:
            await asyncio.to_thread(conn.execute, "BEGIN")
            token = _sqlite_transaction.set(conn)
            try:
                yield
            except Exception:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)
            finally:
                _sqlite_transaction.reset(token)
```

**Repositories** — each implements its `Store` Protocol; reads accept `Filter | Mapping | None`; writes use `_maybe_acquire`:

```python
_CHUNK_COLUMNS   = frozenset({"package", "module", "origin", "title"})
_PACKAGE_COLUMNS = frozenset({"name", "version", "origin"})
_MEMBER_COLUMNS  = frozenset({"package", "module", "name", "kind"})


@dataclass(frozen=True, slots=True)
class SqliteChunkRepository(ChunkStore):
    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_CHUNK_COLUMNS)
    )

    async def upsert(self, chunks: Iterable[Chunk]) -> None: ...
    async def list(self, filter=None, limit=None) -> list[Chunk]: ...
    async def delete(self, filter) -> int: ...
    async def count(self, filter=None) -> int: ...
    async def rebuild_index(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SqlitePackageRepository(PackageStore):
    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_PACKAGE_COLUMNS)
    )

    async def upsert(self, package: Package) -> None: ...
    async def get(self, name: str) -> Package | None: ...
    async def list(self, filter=None, limit=None) -> list[Package]: ...
    async def delete(self, filter) -> int: ...
    async def count(self, filter=None) -> int: ...


@dataclass(frozen=True, slots=True)
class SqliteModuleMemberRepository(ModuleMemberStore):
    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_MEMBER_COLUMNS)
    )

    async def upsert_many(self, members: Iterable[ModuleMember]) -> None: ...
    async def list(self, filter=None, limit=None) -> list[ModuleMember]: ...
    async def delete(self, filter) -> int: ...
    async def count(self, filter=None) -> int: ...
```

**`SqliteVectorStore`** — retrieval-only service implementing `TextSearchable`:

```python
@dataclass(frozen=True, slots=True)
class SqliteVectorStore(TextSearchable):
    """SQLite-backed retrieval service. Performs FTS5 MATCH + filter push-down.
    Pure CRUD happens via SqliteChunkRepository; this class is retrieval only."""
    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_CHUNK_COLUMNS)
    )

    async def text_search(
        self, query_terms: str, limit: int, filter: Filter | Mapping | None = None,
    ) -> tuple[SearchMatch, ...]:
        tree = _resolve_filter(filter)
        where_fragment, params = ("", [])
        if tree is not None:
            where_fragment, params = self.filter_adapter.adapt(tree)
        # Build FTS5 SQL with MATCH + where_fragment; execute; map rows → SearchMatch
        ...
```

**`SqliteFilterAdapter`** — translates `Filter` tree → `(where_fragment: str, params: list)`. Handles `FieldEq`, `FieldIn`, `FieldLike`, `All`. Uses per-repository `safe_columns` whitelist for SQL injection safety; raises `NotImplementedError` on `Any_` / `Not` in this PR.

### 5.4 Row ↔ model mapping

Moves from sub-PR #1's `db.py` into `storage/sqlite.py` alongside the repositories that use it:

- `_chunk_to_row`, `_row_to_chunk`
- `_package_to_row`, `_row_to_package`
- `_module_member_to_row`, `_row_to_module_member`

`db.py` keeps only schema + `PRAGMA user_version` + `build_connection_provider`.

### 5.5 `SearchQuery` — breaking change (`models.py`)

```python
from pydantic.dataclasses import dataclass as pyd_dataclass
from pydantic import field_validator, model_validator

@pyd_dataclass(frozen=True, slots=True)
class SearchQuery:
    terms: str
    max_results: int = 8
    pre_filter: Mapping[str, Any] | None = None
    post_filter: Mapping[str, Any] | None = None
    pre_filter_format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD
    post_filter_format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD

    @field_validator("terms")
    @classmethod
    def _terms_non_empty(cls, v):
        if not v.strip(): raise ValueError("terms must be non-empty")
        return v

    @field_validator("max_results")
    @classmethod
    def _positive_limit(cls, v):
        if v <= 0: raise ValueError("max_results must be positive")
        return v

    @model_validator(mode="after")
    def _validate_filter_syntax(self):
        from pydocs_mcp.storage.filters import format_registry
        for f, fmt in ((self.pre_filter, self.pre_filter_format),
                       (self.post_filter, self.post_filter_format)):
            if f is not None:
                format_registry[fmt].validate(f)
        return self
```

Removed: `package_filter`, `scope`, `title_filter`. Callers build equivalent `pre_filter` dicts.

### 5.6 Application Service (`application/indexing_service.py`)

```python
@dataclass(frozen=True, slots=True)
class IndexingService:
    """First Application Service. Coordinates write-side indexing across
    three entity stores. Depends only on Protocols — backend-agnostic."""
    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore
    unit_of_work: UnitOfWork | None = None

    async def reindex_package(
        self, package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
    ) -> None:
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                await self._do_reindex(package, chunks, module_members)
        else:
            await self._do_reindex(package, chunks, module_members)

    async def remove_package(self, name: str) -> None:
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                await self._do_remove(name)
        else:
            await self._do_remove(name)

    async def clear_all(self) -> None:
        # Used by `--force` CLI flag; drops all rows across all three stores.
        ...

    async def _do_reindex(self, package, chunks, module_members) -> None:
        await self.chunk_store.delete(filter={"package": package.name})
        await self.module_member_store.delete(filter={"package": package.name})
        await self.package_store.delete(filter={"name": package.name})
        await self.package_store.upsert(package)
        await self.chunk_store.upsert(chunks)
        await self.module_member_store.upsert_many(module_members)

    async def _do_remove(self, name) -> None: ...
```

Zero concrete types in signatures. Tested with in-memory Protocol fakes; wired with SQLite adapters in production.

### 5.7 Retrievers (modified from sub-PR #2)

```python
# retrieval/retrievers.py

@dataclass(frozen=True, slots=True)
class Bm25ChunkRetriever:
    store: TextSearchable
    allowed_fields: frozenset[str]
    name: str = "bm25_chunk"

    async def retrieve(self, query: SearchQuery) -> tuple[SearchMatch, ...]:
        tree = None
        if query.pre_filter is not None:
            tree = format_registry[query.pre_filter_format].parse(query.pre_filter)
            _check_allowlist(tree, self.allowed_fields)
        return await self.store.text_search(
            query_terms=query.terms, limit=query.max_results, filter=tree,
        )

    def to_dict(self) -> dict:
        return {"type": "bm25_chunk", "schema_name": "chunk"}

    @classmethod
    def from_dict(cls, data, context):
        schema_name = data.get("schema_name", "chunk")
        allowed = frozenset(context.app_config.metadata_schemas[schema_name])
        return cls(store=context.vector_store, allowed_fields=allowed)
```

`LikeMemberRetriever` mirrors this pattern but consumes a store implementing a text-like contract over `ModuleMember` rows. For sub-PR #3, we don't introduce a separate `ModuleMemberTextSearchable` Protocol — the `LikeMemberRetriever` takes a `SqliteModuleMemberRepository` directly (which implements `ModuleMemberStore`) and performs its LIKE via `repository.list(filter=...)`. If a second backend wants to serve module-member text search later, a `ModuleMemberTextSearchable` Protocol can be extracted at that point.

### 5.8 Stages (modified from sub-PR #2)

**Removed:** `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`.

**Added:** `MetadataPostFilterStage`:

```python
@dataclass(frozen=True, slots=True)
class MetadataPostFilterStage:
    name: str = "metadata_post_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.query.post_filter is None:
            return state
        tree = format_registry[state.query.post_filter_format].parse(state.query.post_filter)
        kept = tuple(m for m in state.matches if _evaluate(tree, m.result))
        return replace(state, matches=kept)
```

`_evaluate(filter, obj)` walks the tree, checking each clause against attributes of the target domain object.

### 5.9 `AppConfig` extension (modified from sub-PR #2) — YAML-only defaults

All configurable defaults live in a **shipped** YAML file (`python/pydocs_mcp/presets/default_config.yaml`) loaded as the baseline layer. User's `pydocs-mcp.yaml` overlays it; env vars overlay both; CLI flags overlay everything. No Python-level defaults on fields that ship via YAML — single source of truth.

#### Shipped baseline (`python/pydocs_mcp/presets/default_config.yaml`)

```yaml
# Ships inside the installed package. All defaults live here.
cache_dir: ~/.pydocs-mcp
log_level: info

metadata_schemas:
  chunk:  [package, scope, origin, title, module]
  member: [package, module, name, kind]

pipelines:
  chunk:
    - default: true
      pipeline_path: presets/chunk_fts.yaml
  member:
    - default: true
      pipeline_path: presets/member_like.yaml
```

#### `AppConfig` class — no Python defaults on YAML-backed fields

```python
# retrieval/config.py

from importlib.resources import files
from pathlib import Path
from collections.abc import Mapping

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import YamlConfigSettingsSource


class AppConfig(BaseSettings):
    cache_dir: Path
    log_level: str
    metadata_schemas: Mapping[str, tuple[str, ...]]
    pipelines: Mapping[str, HandlerConfig]

    model_config = SettingsConfigDict(
        env_prefix="PYDOCS_",
    )

    # ------------------------------------------------------------------
    # Source layering: shipped baseline → user file → env vars → init
    # ------------------------------------------------------------------
    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings,
    ):
        shipped_source = YamlConfigSettingsSource(
            settings_cls,
            yaml_file=_shipped_default_config_path(),
        )
        user_source = YamlConfigSettingsSource(
            settings_cls,
            yaml_file=_resolved_user_config_path(),
        )
        # Highest priority first:
        return (init_settings, env_settings, user_source, shipped_source)

    # ------------------------------------------------------------------
    # Entry point used by server.py / __main__.py at startup
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, explicit_path: Path | None = None) -> "AppConfig":
        """Resolve the user's config path (explicit → env → cwd → XDG home),
        then construct AppConfig with the full source layering."""
        global _USER_CONFIG_PATH_OVERRIDE
        _USER_CONFIG_PATH_OVERRIDE = explicit_path
        return cls()


def _shipped_default_config_path() -> Path:
    """Package-relative path via importlib.resources — works in wheel / sdist / editable."""
    return Path(str(files("pydocs_mcp.presets") / "default_config.yaml"))


def _resolved_user_config_path() -> Path | None:
    """Resolution order:
    1. explicit --config path (set via AppConfig.load)
    2. PYDOCS_CONFIG_PATH env var
    3. ./pydocs-mcp.yaml
    4. ~/.config/pydocs-mcp/config.yaml
    5. None (no user file — shipped baseline is sufficient)"""
    ...
```

#### Example — user overriding a single value

User's `pydocs-mcp.yaml` (project-local or home):

```yaml
# Only what the user wants to change; everything else stays at shipped defaults.
metadata_schemas:
  chunk: [package, scope, origin, title, module, language]   # added 'language'
```

After `AppConfig.load()`, the effective config is:

```python
AppConfig(
    cache_dir=Path("~/.pydocs-mcp"),                                       # shipped
    log_level="info",                                                      # shipped
    metadata_schemas={
        "chunk":  ("package", "scope", "origin", "title", "module", "language"),  # overridden
        "member": ("package", "module", "name", "kind"),                   # shipped
    },
    pipelines={
        "chunk":  HandlerConfig(routes=(PipelineRouteEntry(default=True, pipeline_path=Path("presets/chunk_fts.yaml")),)),   # shipped
        "member": HandlerConfig(routes=(PipelineRouteEntry(default=True, pipeline_path=Path("presets/member_like.yaml")),)), # shipped
    },
)
```

#### Environment-variable override example

```bash
PYDOCS_LOG_LEVEL=debug pydocs-mcp serve .
```

Effective `log_level` becomes `"debug"`; everything else loads normally through the layering.

#### Example — using the schema inside a retriever

```python
@classmethod
def from_dict(cls, data, context):
    schema_name = data.get("schema_name", "chunk")
    allowed = frozenset(context.app_config.metadata_schemas[schema_name])
    return cls(store=context.vector_store, allowed_fields=allowed)
```

Retrievers reference a schema by name via `schema_name` in their dict form — defaults to `"chunk"` for chunk retrievers and `"member"` for member retrievers.

#### Why no Python defaults?

| Pro | Detail |
|---|---|
| Single source of truth | All defaults live in one YAML file; no drift between the Python class and docs |
| Consistent with pipelines | `pipelines` already falls back to shipped YAML presets — `metadata_schemas` now follows the same pattern |
| Discoverable | Users reading `default_config.yaml` see every default in one place; no hunting in Python |
| Testable | One snapshot test asserts the shipped YAML parses into the expected `AppConfig`; no need to mirror defaults in test fixtures |
| Overridable at any layer | YAML file → env var → CLI flag, standard pydantic-settings chain |

#### Why not use pydantic defaults instead?

Rejected alternative — keeping Python defaults on fields — meant **two sources of truth** (Python class + documented YAML example). Any drift between them produced silent confusion: "the YAML says X but the code says Y; which wins?" YAML-only eliminates the ambiguity.

---

## 6. Example flows

### 6.1 MCP handler — legacy params → `pre_filter` → pipeline

```python
@mcp.tool()
async def search_docs(query: str, package: str = "", internal: bool | None = None, topic: str = "") -> str:
    pre_filter: dict[str, Any] = {}
    if package:
        pre_filter["package"] = package
    if internal is True:
        pre_filter["scope"] = SearchScope.PROJECT_ONLY.value
    elif internal is False:
        pre_filter["scope"] = SearchScope.DEPENDENCIES_ONLY.value
    if topic:
        pre_filter["title"] = {"like": topic}

    sq = SearchQuery(terms=query, pre_filter=pre_filter or None)
    state = await chunk_pipeline.run(sq)
    return state.output or "No matches found."
```

### 6.2 MCP handler — `list_packages` uses `SqlitePackageRepository`

```python
@mcp.tool()
async def list_packages() -> str:
    packages = await package_repository.list()
    return "\n".join(f"- {p.name} {p.version} — {p.summary}" for p in packages)
```

### 6.3 `__main__.py` CLI — `pydocs-mcp index . --force`

```python
if args.force:
    await indexing_service.clear_all()
for pkg, chunks, members in _extract_from_source(project):
    await indexing_service.reindex_package(pkg, chunks, members)
```

### 6.4 Indexer — atomic per-package reindex via `UnitOfWork`

Inside `IndexingService.reindex_package`, the six ops (three deletes + three upserts) share one SQLite transaction via the ambient contextvar; on any failure, all are rolled back.

### 6.5 Heterogeneous backend (illustrative — future)

```python
indexing_service = IndexingService(
    package_store=SqlitePackageRepository(provider=sqlite_provider),
    chunk_store=QdrantVectorStore(client=qdrant_client),            # different backend!
    module_member_store=SqliteModuleMemberRepository(provider=sqlite_provider),
    unit_of_work=None,  # no cross-backend atomicity — best effort
)
```

`IndexingService` unchanged. This is the payoff.

### 6.6 YAML config — retriever schema + config-driven pipelines (extends sub-PR #2)

```yaml
# pydocs-mcp.yaml
cache_dir: ~/.pydocs-mcp
log_level: info

metadata_schemas:
  chunk:  [package, scope, origin, title, module]
  member: [package, module, name, kind]

pipelines:
  chunk:
    - default: true
      pipeline_path: presets/chunk_fts.yaml
  member:
    - default: true
      pipeline_path: presets/member_like.yaml
```

```yaml
# presets/chunk_fts.yaml
name: fts_chunk
stages:
  - type: chunk_retrieval
    retriever:
      type: bm25_chunk
      schema_name: chunk
  - type: metadata_post_filter    # no-op when query.post_filter is None
  - type: limit
    max_results: 8
  - type: token_budget_formatter
    formatter: {type: chunk_markdown}
    budget: 2000
```

---

## 7. Error handling

- Repositories and stores **propagate** exceptions. No blanket swallow. (Same as sub-PR #2.)
- `IndexingService` propagates; `UnitOfWork.begin()` rolls back on exception before re-raising.
- `format.validate()` errors surface at `SearchQuery` construction with clear messages (field, expected shape).
- `MetadataSchema.validate()` errors surface at retriever `retrieve()` time, listing the unknown fields + the allowlist.
- `SqliteFilterAdapter.adapt()` raises `ValueError` on unwhitelisted columns and `NotImplementedError` on unsupported filter node types.
- `ComponentRegistry.build()` + `format_registry` lookups raise `KeyError` listing known names on miss.
- `server.py` handlers and `__main__.py` CLI catch broadly at the outermost boundary and produce the "No matches found." / CLI-friendly error messages users see today. No new user-visible error paths.

---

## 8. Code organization — files touched

### New: `storage/`

| File | Contents | ~LOC |
|---|---|---|
| `python/pydocs_mcp/storage/__init__.py` | Re-exports public surface (Protocols, SqliteVectorStore, repositories, SqliteUnitOfWork, MultiFieldFormat, format_registry) | 25 |
| `python/pydocs_mcp/storage/protocols.py` | 8 Protocols (`PackageStore`, `ChunkStore`, `ModuleMemberStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`, `UnitOfWork`, `FilterAdapter`) | 120 |
| `python/pydocs_mcp/storage/filters.py` | Filter tree (6 classes), MetadataFilterFormat enum, FilterFormat Protocol, MultiFieldFormat, format_registry, FieldSpec, MetadataSchema, filter-tree walk + evaluate helpers | 250 |
| `python/pydocs_mcp/storage/sqlite.py` | Contextvar, SqliteUnitOfWork, SqliteFilterAdapter, 3 repositories, SqliteVectorStore, per-table safe-column constants, row↔model mapping helpers moved from db.py | 500 |

**Subtotal new: ~895 LOC.**

### New: `application/`

| File | Contents | ~LOC |
|---|---|---|
| `python/pydocs_mcp/application/__init__.py` | Re-exports `IndexingService` | 10 |
| `python/pydocs_mcp/application/indexing_service.py` | `IndexingService` depending only on Protocols | 80 |

**Subtotal new: ~90 LOC.**

### Modified

| File | Change | ~LOC diff |
|---|---|---|
| `python/pydocs_mcp/models.py` | `SearchQuery` → `pydantic.dataclasses.dataclass`; removes 3 fields, adds 4 fields + 3 validators | ±60 |
| `python/pydocs_mcp/db.py` | Removes row↔model mapping helpers (moved to `storage/sqlite.py`); keeps schema DDL + `build_connection_provider` + `PRAGMA user_version` logic | −70 |
| `python/pydocs_mcp/retrieval/retrievers.py` | `Bm25ChunkRetriever` consumes `TextSearchable`; `LikeMemberRetriever` consumes `SqliteModuleMemberRepository`; allowlist validation via `AppConfig.metadata_schemas` | ±80 |
| `python/pydocs_mcp/retrieval/stages.py` | Removes `PackageFilterStage` / `ScopeFilterStage` / `TitleFilterStage`; adds `MetadataPostFilterStage` | −120 / +50 |
| `python/pydocs_mcp/retrieval/config.py` | Adds `metadata_schemas` field (no Python default), `settings_customise_sources` layering with shipped-default YAML source, `_shipped_default_config_path` helper. Existing fields from sub-PR #2 lose their Python defaults — values now come from the shipped YAML exclusively. | +60 |
| `python/pydocs_mcp/presets/default_config.yaml` | **New file.** Shipped baseline for every configurable default (`cache_dir`, `log_level`, `metadata_schemas`, `pipelines`). Loaded as the lowest-priority layer by `AppConfig`. | +20 |
| `python/pydocs_mcp/retrieval/serialization.py` | `BuildContext` gains `app_config: AppConfig`; existing fields unchanged | +15 |
| `python/pydocs_mcp/server.py` | Handlers translate legacy MCP params to `pre_filter` dict; `list_packages` / `get_package_doc` / `inspect_module` use repositories | ±100 |
| `python/pydocs_mcp/__main__.py` | CLI uses `IndexingService.clear_all` for `--force`; `query` / `api` subcommands build `SearchQuery` with `pre_filter` | ±40 |
| `python/pydocs_mcp/indexer.py` | Writes go through `IndexingService.reindex_package`; removes direct SQL | ±100 |
| `python/pydocs_mcp/presets/chunk_fts.yaml` | Remove 3 filter stages; add `metadata_post_filter` | ±10 |
| `python/pydocs_mcp/presets/member_like.yaml` | Same | ±10 |

**Subtotal modified: ~555 LOC diff.**

### Tests

| File | Contents | ~LOC |
|---|---|---|
| `tests/storage/__init__.py` | | 1 |
| `tests/storage/conftest.py` | `tmp_sqlite_provider`, `sqlite_transaction_reset`, in-memory Protocol fakes (`InMemoryPackageStore`, `InMemoryChunkStore`, `InMemoryModuleMemberStore`, `InMemoryUnitOfWork`) | 150 |
| `tests/storage/test_filters.py` | Filter tree constructors, MultiFieldFormat.validate/parse, MetadataSchema.validate, format_registry | 120 |
| `tests/storage/test_sqlite_filter_adapter.py` | SqliteFilterAdapter.adapt on all 4 implemented Filter node types, safe-column whitelist, NotImplementedError on Any_/Not | 80 |
| `tests/storage/test_sqlite_repositories.py` | Real tmp-file SQLite; upsert/list/delete/count per repository; filter-based reads; ambient transaction detection | 200 |
| `tests/storage/test_sqlite_vector_store.py` | Real tmp-file SQLite; FTS5 MATCH; filter push-down; `text_search` returns expected SearchMatches | 100 |
| `tests/storage/test_sqlite_unit_of_work.py` | Real tmp-file SQLite; BEGIN/COMMIT on success; ROLLBACK on exception; nested begin raises; contextvar reset | 100 |
| `tests/storage/test_end_to_end.py` | **Smoke test.** Build real stack (provider + repos + vector store + UoW + IndexingService + a minimal chunk pipeline). Index a small Python fixture. Call `search_docs` through the pipeline. Assert output is non-empty and contains expected snippets. | 150 |
| `tests/application/__init__.py` | | 1 |
| `tests/application/test_indexing_service.py` | `IndexingService.reindex_package` / `remove_package` / `clear_all` with in-memory Protocol fakes; exception propagation through UoW; without UoW (best-effort) | 150 |
| Existing `tests/retrieval/*` | Mechanical updates: new `SearchQuery` shape, removed filter stages replaced with `MetadataPostFilterStage`, retrievers consume `TextSearchable` | ±200 |
| Existing `tests/test_server.py` | Handler tests update to new `SearchQuery` shape; MCP surface unchanged | +50 |
| Existing `tests/test_db.py` | Trim tests for moved mapping helpers; keep schema + user_version tests | ±20 |
| Existing `tests/test_indexer.py` | Replace direct-SQL assertions with `IndexingService` mock interactions | ±50 |

**Tests subtotal: ~1,370 LOC (new + modified).**

### PR size rollup

| | LOC |
|---|---|
| New `storage/` package | ~895 |
| New `application/` package | ~90 |
| Modified Python files | ~555 |
| Tests | ~1,370 |
| **Total** | **~2,910** |

Large but self-contained. Natural split if needed:
- **#3a** — filter infrastructure (`storage/filters.py`) + `SearchQuery` breaking change + test_filters.
- **#3b** — repositories + `SqliteVectorStore` + `SqliteUnitOfWork` + tests.
- **#3c** — `IndexingService` + indexer/server wiring + end-to-end smoke test.

Preference is one PR for coherent review; split only if review bandwidth demands it.

---

## 9. Cross-PR commitments (ratified; updated from sub-PR #2)

| # | Commitment |
|---|---|
| 1 | Retrievers/stores own retrieval SQL (FTS5 MATCH). Repositories own CRUD SQL. No overlap. ✓ |
| 2 | Single `ConnectionProvider` protocol. No read-only / read-write type split. ✓ |
| 3 | Transactions are caller-driven via `UnitOfWork` Protocol. Concrete `SqliteUnitOfWork` uses contextvars; repositories detect ambient transaction automatically. **Promoted from aspirational to shipped.** |
| 4 | DDL stays in `db.py` (unchanged from sub-PR #2's decision). Schema version still in `db.py`. |
| 5 | One `ConnectionProvider` + one `UnitOfWork` + one of each repository + one `SqliteVectorStore` + one `IndexingService` built at startup. |
| 6 | Default `PerCallConnectionProvider`. Future pooled provider is a drop-in. |
| 7 | All 5 MCP handlers async; all use the repository layer (not raw SQL). |
| 8 | Only retrieval stages touch `TextSearchable`. Filter / limit / formatter stages are connection-free. |
| 9 | `storage/` and `application/` packages flat (3 Python files + `__init__.py` in storage; 1 Python file + `__init__.py` in application). |
| 10 | Testing seam: `ConnectionProvider` + Protocol fakes. No monkeypatching `sqlite3`. |
| 11 | `SearchQuery.pre_filter` is the single source of truth for filters; structured fields removed. |
| 12 | Filter formats are extensible via `format_registry`; backend adapters are extensible via multi-Protocol inheritance. Both the `MetadataFilterFormat` enum and the 4 storage capability Protocols are **open for extension, closed for modification**. |
| 13 | `IndexingService` depends only on Protocols + optional `UnitOfWork`. Backend-agnostic by construction — future heterogeneous setups (e.g., Qdrant chunks + SQLite packages) wire in without modifying the service. |

---

## 10. Risks and rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| Breaking `SearchQuery` change lands silently and sub-PR #2's existing tests break on import | High (expected) | All retrieval tests are updated in this PR (see §8). A prominent CHANGELOG entry + sub-PR #1 spec gets a "superseded by sub-PR #3" note on `SearchQuery` fields. |
| Pydantic dataclass validators fail on constructs that old dataclass accepted | Medium | Test `SearchQuery` construction with every MCP-handler input combination; tests in `tests/application/test_indexing_service.py` and `tests/storage/test_filters.py` cover the round-trips |
| Ambient `contextvars` leak across async tasks or tests, causing flaky cross-talk | Medium | `sqlite_transaction_reset` conftest fixture clears the contextvar in `setup/teardown` every test. Integration tests verify the reset behavior after both success and exception paths |
| Multi-Protocol inheritance surfaces mypy strictness issues (diamond inheritance, conflicting method defaults) | Low | Protocols in this PR don't share method names; each concrete class implements disjoint method sets. Mypy strict mode passes in CI |
| `SqliteFilterAdapter` SQL injection via attacker-supplied filter field | Low | Per-repository `safe_columns` whitelist; unknown column raises `ValueError` before any SQL is constructed. Adapters also escape `LIKE` wildcards |
| `IndexingService.reindex_package` atomic boundary dropped when `unit_of_work=None` (best-effort path) | Medium | Production always passes a UoW; best-effort is reserved for future heterogeneous setups. Documented in docstring. |
| Retriever tries to apply a filter field not in the `allowed_fields` allowlist | Medium | `MetadataSchema.validate` raises with the unknown field(s) + the allowlist. Test coverage exercises both accept + reject paths |
| Existing test suite breaks en masse because imports / symbols / `SearchQuery` fields shifted | Medium | Automated rename / import fixup as part of the PR, committed alongside the new files. CI catches anything missed |
| Row-mapping helpers moved out of `db.py` break any external consumer (e.g., a downstream script) | Low | The helpers were private (`_chunk_to_row` etc.). Re-exported from `storage/sqlite.py` for anyone importing by absolute path. CHANGELOG notes the move |
| `pydantic-settings` / `pyyaml` deps added in sub-PR #2 — no new deps in this PR | Low | Confirm no new `pyproject.toml` entries beyond sub-PR #2. `pydantic.dataclasses` is already bundled with `pydantic` which sub-PR #2 pulls in transitively |

**Rollback:** revert the merge commit. `~/.pydocs-mcp/*.db` stays compatible (schema unchanged from sub-PR #1). Sub-PR #2's retrieval layer comes back with its three filter stages. `SearchQuery`'s original fields come back. No user data loss.

---

## 11. Acceptance criteria

1. `python/pydocs_mcp/storage/` exists with files: `__init__.py`, `protocols.py`, `filters.py`, `sqlite.py`.
2. `python/pydocs_mcp/application/` exists with files: `__init__.py`, `indexing_service.py`.
3. `storage/protocols.py` declares 8 Protocols: `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`, `UnitOfWork`, `FilterAdapter`. All are `@runtime_checkable`. `VectorSearchable` and `HybridSearchable` have no concrete implementations in this PR. (`FilterFormat` Protocol lives in `storage/filters.py` alongside the formats it represents.)
4. `storage/filters.py` declares the `Filter` tree classes (`FieldEq`, `FieldIn`, `FieldLike`, `All`, `Any_`, `Not`), `MetadataFilterFormat` enum with all 5 values, `FilterFormat` Protocol, `MultiFieldFormat` (parse + validate), `FieldSpec`, `MetadataSchema`, `format_registry` with `MULTIFIELD` registered at import.
5. `MultiFieldFormat.validate` rejects dicts with `$and` / `$or` / `$not` keys, unknown operators, non-mapping input. Each rejection includes a descriptive error message.
6. `MetadataSchema.validate(filter)` rejects filters referencing fields not in `field_names()`, listing unknown fields + the allowlist.
7. `storage/sqlite.py` defines: `_sqlite_transaction` contextvar, `_maybe_acquire`, `SqliteUnitOfWork`, `SqliteFilterAdapter` (4 Filter cases + 2 raise-cases), `SqlitePackageRepository : PackageStore`, `SqliteChunkRepository : ChunkStore`, `SqliteModuleMemberRepository : ModuleMemberStore`, `SqliteVectorStore : TextSearchable`, per-table `_*_COLUMNS` constants, row↔model mapping helpers.
8. Every repository method (`upsert`, `list`, `get`, `delete`, `count`, `rebuild_index` where applicable) uses `_maybe_acquire` so it honors ambient transactions set by `SqliteUnitOfWork`.
9. `SqliteVectorStore.text_search` composes FTS5 MATCH with `SqliteFilterAdapter`-generated WHERE fragments; pushes down `pre_filter` fields that map to `_CHUNK_COLUMNS`.
10. `application/indexing_service.py` defines `IndexingService` depending only on `PackageStore`, `ChunkStore`, `ModuleMemberStore`, and optional `UnitOfWork`. No concrete types appear in its type annotations.
11. `IndexingService.reindex_package` / `remove_package` / `clear_all` use `unit_of_work.begin()` when supplied; run without transaction otherwise.
12. `models.py` `SearchQuery` is a `pydantic.dataclasses.dataclass(frozen=True, slots=True)`. Fields: `terms`, `max_results`, `pre_filter`, `post_filter`, `pre_filter_format`, `post_filter_format`. Removed fields: `package_filter`, `scope`, `title_filter`. Constructor rejects empty `terms`, non-positive `max_results`, and malformed `pre_filter` / `post_filter` dicts.
13. `retrieval/stages.py` removes `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`. Adds `MetadataPostFilterStage` that applies `state.query.post_filter` when non-None; no-op otherwise.
14. `retrieval/config.py`'s `AppConfig` gains `metadata_schemas: Mapping[str, tuple[str, ...]]` with **no Python-level default**. The field's value loads from `python/pydocs_mcp/presets/default_config.yaml` (shipped with the package) via `settings_customise_sources` layering. User's `pydocs-mcp.yaml` overrides per schema name; env vars override both. A snapshot test asserts the shipped YAML parses into an `AppConfig` whose `metadata_schemas` matches the expected baseline (`chunk: [package, scope, origin, title, module]`, `member: [package, module, name, kind]`). No other field on `AppConfig` declares a Python-level default for values that ship via `presets/default_config.yaml`.
15. `retrieval/retrievers.py` `Bm25ChunkRetriever` takes `store: TextSearchable`; applies `allowed_fields` validation via `MetadataSchema`. `LikeMemberRetriever` takes a `SqliteModuleMemberRepository` and filters via the repository's `list` method.
16. `server.py` MCP tool signatures (`search_docs`, `search_api`, `list_packages`, `get_package_doc`, `inspect_module`) remain byte-identical to sub-PR #2. Handlers translate legacy params (`internal`, `package`, `topic`) to `SearchQuery.pre_filter`.
17. `server.py` `list_packages` / `get_package_doc` / `inspect_module` use the repository layer (no direct SQL). Output strings remain byte-identical to sub-PR #2.
18. `__main__.py` CLI subcommands use `IndexingService` for writes (`pydocs-mcp index . --force` calls `clear_all` then `reindex_package` per package) and `SearchQuery` with `pre_filter` for reads.
19. `indexer.py` writes go through `IndexingService.reindex_package`. No direct SQL remains in `indexer.py`.
20. Default YAML presets (`presets/chunk_fts.yaml`, `presets/member_like.yaml`) updated to use `metadata_post_filter` stage; no reference to the removed filter stages.
21. Every test existing on `main` is still present in the PR branch after mechanical updates. Zero tests deleted. The full suite passes.
22. `tests/storage/` contains the 7 test files listed in §8 with at least the coverage described. `tests/application/test_indexing_service.py` uses only Protocol-fake stores.
23. `tests/storage/test_end_to_end.py` runs the full stack on a tmp-file SQLite: indexes a small Python fixture via `IndexingService.reindex_package`, invokes `search_docs` through a real `CodeRetrieverPipeline`, asserts the output string contains expected snippets. Completes in under 2 seconds on a dev laptop.
24. The word `legacy` does not appear in any source file added or modified by this PR.
25. **No blanket-swallow contract (explicitly enforced; inherits + strengthens sub-PR #2 AC #26):** No `try/except Exception: return ...`-style catch lives in any repository, store, application service, retriever, stage, or `CodeRetrieverPipeline.run()`. Blanket catches remain confined to MCP handlers in `server.py` and CLI top-level in `__main__.py`. **Specific narrowing rules:**
    - Concrete SQLite repositories and stores (`SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository`, `SqliteVectorStore`, and any future `*Store`/`*Repository` that talks to SQLite) MUST narrow their `except` to `sqlite3.DatabaseError` (which covers `OperationalError`, `IntegrityError`, `DataError`, `NotSupportedError`, `InterfaceError`, and `ProgrammingError`). Programming errors (`TypeError`, `AttributeError`, `KeyError` in internal logic) MUST propagate. `SystemExit` / `KeyboardInterrupt` MUST NOT be intercepted.
    - Retrievers (`Bm25ChunkRetriever`, `LikeMemberRetriever`, `PipelineChunkRetriever`, `PipelineModuleMemberRetriever`, and any future `*Retriever`) MUST narrow to `sqlite3.DatabaseError` (if they talk to SQLite directly) or propagate entirely (if they delegate to a store).
    - Stages (all 12 concrete stages plus any future `TryStage` / `RetryStage` / `CircuitBreakerStage`) MUST propagate exceptions unchanged. Future `TryStage` (sub-PR #7) is the opt-in mechanism for per-stage error tolerance.
    - `CodeRetrieverPipeline.run()` MUST NOT wrap the inner stage-iteration loop in any `try/except`. Full propagation to the caller (MCP handler or CLI `asyncio.run`) is required.
    - `UnitOfWork.begin()` MAY `rollback` on exception, but MUST re-raise — never swallow.
    - Grep invariant for CI: `rg -n 'except Exception' python/pydocs_mcp/retrieval/ python/pydocs_mcp/storage/ python/pydocs_mcp/application/` MUST return zero matches in any file under those trees EXCEPT when the except body is bare `raise`, `raise ...`, or the caught exception is logged-then-re-raised unchanged.
26. `pyproject.toml` declares no new runtime dependencies beyond sub-PR #2 (`pydantic`, `pydantic-settings`, `pyyaml`, `mcp`). `requires-python = ">=3.11"` retained.
27. **Behavior parity (inherits + strengthens sub-PR #2 AC #21, #29):** For the golden fixture repo indexed under sub-PR #1, every MCP tool and every CLI subcommand produces byte-identical output before and after sub-PR #3 when no `pydocs-mcp.yaml` is present. Regression test locked in `tests/retrieval/test_parity_golden.py` (added in sub-PR #2); it MUST still pass after sub-PR #3's storage-layer migration. **Byte-parity contract (explicitly inherited from sub-PR #2; MUST NOT regress):**
    - `ChunkMarkdownFormatter.format(chunk)` MUST emit `## {title}\n{body}` — exactly one `\n` between heading and body, NEVER `\n\n`. Any future formatter change that alters whitespace is a breaking change and MUST bump the preset YAML format version.
    - `TokenBudgetFormatterStage.run(state)` MUST NOT call `.rstrip()` (or any equivalent trailing-whitespace strip) on the composite output text. The trailing `\n` after the last joined piece is load-bearing for downstream consumers comparing bytes or running line-anchored regex.
    - Post-filter substitution (removing `PackageFilterStage` / `ScopeFilterStage` / `TitleFilterStage` in favor of `MetadataPostFilterStage` + SQL pushdown per §5.8) MUST NOT alter the ordering, field set, or rendered string of results for the default preset. The golden test covers this.
28. **`ParallelRetrievalStage` content-keyed dedup (inherits sub-PR #2 AC #32; MUST NOT regress):** `ParallelRetrievalStage.run()` MUST dedup branch outputs by content key, NEVER by positional slice. The positional form (`branch_state.result.items[len(initial_items):]`) is BANNED — any branch that filters, reorders, or partially drops items would silently lose legitimate outputs with that approach. **Required implementation:**
    - Compute a key per item: `item.id` if it is not `None`, else `id(item)` (Python identity fallback).
    - Maintain a `seen_keys: set` while iterating the initial items (if any) followed by each branch's full `result.items`. An item is appended to the accumulator only when its key is not yet in `seen_keys`. Keys that have been appended are added to `seen_keys`.
    - First-seen wins: if a key appears in both the initial state and a branch, only the initial copy is kept. Between branches, the first-branch contribution wins. This preserves stable ordering across reruns with the same input.
    - Result type dispatch: the accumulator is wrapped in `ChunkList` if the first non-None result was a `ChunkList`, else `ModuleMemberList`. Mixed result types across branches are NOT supported.
    - The regression test `test_parallel_retrieval_stage_preserves_filtered_branches` (added in sub-PR #2 `tests/retrieval/test_stages.py`) MUST still pass after any sub-PR #3 stage refactoring. If `ParallelRetrievalStage` moves or is re-factored, the test moves with it.
29. **Retriever-provider decoupling (new invariant discovered during sub-PR #2 review):** Concrete retrievers MUST NOT reach into a `ConnectionProvider`'s internal attributes (e.g., `.cache_path`) to open connections. They MUST acquire connections via the provider's documented interface (`async with provider.acquire()` for async contexts, or via a new sync helper `provider.open_sync()` added in sub-PR #3 if `asyncio.to_thread`-friendly access is required). This preserves the dependency-inversion seam between retrievers (concrete) and connection lifecycle (abstract). Sub-PR #3 adds the sync helper to `ConnectionProvider` Protocol and migrates `Bm25ChunkRetriever._retrieve_sync` / `LikeMemberRetriever._retrieve_sync` to use it. A contract test asserts that any `ConnectionProvider` without a `.cache_path` attribute (e.g., a pool-backed provider, a mock) works correctly with both retrievers.
30. **`retrieval/__init__.py` registry-population contract:** Importing `pydocs_mcp.retrieval` alone MUST populate all component registries (`stage_registry`, `retriever_registry`, `formatter_registry`, `default_predicate_registry`). This is verified by a test that performs only `import pydocs_mcp.retrieval` then asserts `len(stage_registry.names()) >= 12`, `len(retriever_registry.names()) >= 4`, `len(formatter_registry.names()) >= 2`, `len(default_predicate_registry.names()) >= 4`. Implementation: `retrieval/__init__.py` MUST eagerly import `stages`, `retrievers`, `formatters`, `predicates` (with `# noqa: F401` if needed) so decorator side-effects fire on package import, independent of whoever imports `retrieval.config` first.
31. **`from_dict` recursion depth guard:** `CodeRetrieverPipeline.from_dict` and `SubPipelineStage.from_dict` MUST accept a `_depth: int = 0` keyword argument (or equivalent mechanism) and raise a descriptive `ValueError("pipeline nesting exceeds max depth of N")` when depth exceeds a configurable limit (default `32`). This prevents hostile or corrupted YAML with deeply-nested `sub_pipeline` entries from triggering Python's `RecursionError` at config-load time and crashing the MCP server. Test: a YAML file with 33+ levels of nested `sub_pipeline` MUST raise `ValueError` at load, not `RecursionError`.
32. **`PipelineRouteEntry` mutual-exclusion validator:** `PipelineRouteEntry` MUST declare a pydantic `@model_validator(mode='after')` that raises `ValueError("route entry must set exactly one of predicate or default")` when both `predicate` is set AND `default=True`, or when neither is set. The silent "default wins" fallback observed in sub-PR #2's `_build_handler_pipeline` is forbidden. Test covers all 4 cases: predicate-only (valid), default-only (valid), both-set (raises), neither-set (raises).
33. **`ReciprocalRankFusionStage` first-seen merge (strengthens sub-PR #2 AC #32 posture):** `ReciprocalRankFusionStage.run()` MUST use `setdefault`-semantics when deduplicating items by `id` key — the FIRST-seen copy's `retriever_name` / `relevance` / `metadata` is preserved, NOT the last. When two retrievers return the same chunk, the output reflects the higher-ranking source, not whichever happens to hash later. Test: construct two branches that each return a chunk with `id=1` but different `retriever_name` values; assert the merged output carries the first branch's `retriever_name`.
34. **Composite chunk title sentinel:** Composite chunks produced by `TokenBudgetFormatterStage` MUST set `metadata[ChunkFilterField.TITLE.value] = "_composite"` (or equivalent sentinel) so that downstream `TitleFilterStage` / `MetadataPostFilterStage` instances configured with a title filter do NOT drop composite outputs. Documented invariant: composite chunks are terminal — no filter stage should run after `TokenBudgetFormatterStage`. If a filter stage is nonetheless present, it MUST treat `_composite` as a pass-through.
35. **Retrieval-layer storage boundary:** `retrieval/retrievers.py` MUST NOT import from `pydocs_mcp.db` (which may be renamed or relocated in sub-PR #3). Row-deserialization helpers MUST live in `storage/sqlite.py` or `retrieval/row_mappers.py`, not in `db.py`. This closes the layering inversion flagged during sub-PR #2's review (`retrievers.py` reaching into `db._row_to_*`).

---

## 12. Open items

None — all design decisions ratified during brainstorming. If implementation uncovers a decision that doesn't survive reality, I'll surface it before writing code.

---

## 13. Implementation notes — pitfalls for implementers without full brainstorming context

Points that are easy to get wrong if you only have the spec and not the brainstorming history.

### Ambient transaction via `contextvars` — most critical
- `_sqlite_transaction: ContextVar[sqlite3.Connection | None]` is module-level. Every repository method calls `_maybe_acquire(self.provider)` to get its connection. Do NOT call `self.provider.acquire()` directly in a repository method — that bypasses the ambient transaction and silently breaks `IndexingService.reindex_package`'s atomicity.
- `SqliteUnitOfWork.begin()` MUST reset the contextvar token in `finally`. A leaked token cross-contaminates subsequent tasks/tests. Test for this explicitly.
- ContextVars are `asyncio.Task`-local. Do NOT substitute `threading.local` — wrong scope; wrong primitive. Pure `asyncio` with no thread offload works correctly; `asyncio.to_thread(...)` calls (used heavily here for blocking SQLite work) also preserve contextvars by default on Python 3.11+.

### `_maybe_acquire` is load-bearing
- Every new repository method — even a one-line helper — uses `_maybe_acquire`. It's not cosmetic. Enforce in code review: grep for `self.provider.acquire()` inside `SqliteXRepository` classes; zero matches expected. Only `SqliteUnitOfWork.begin` and `SqliteVectorStore.text_search` may acquire directly.

### `MultiFieldFormat` is FLAT; no boolean ops
- No `$and` / `$or` / `$not` keys in a multifield dict. `MultiFieldFormat.validate` rejects them explicitly, redirecting the user to the (future) `FilterTreeFormat`. Don't add boolean support to `MultiFieldFormat` "for convenience" — it breaks the clean split between the two formats.
- Filter tree classes `Any_` and `Not` exist in `filters.py` but are NEVER instantiated by `MultiFieldFormat`. They're ready for `FilterTreeFormat` in a later PR. `SqliteFilterAdapter` raises `NotImplementedError` for them in this PR — don't preemptively add cases.

### `SqliteVectorStore` ≠ `ChunkStore`
- `SqliteVectorStore` implements `TextSearchable` ONLY. CRUD on chunks goes through `SqliteChunkRepository` (which implements `ChunkStore`). `IndexingService` talks to `SqliteChunkRepository`, not to `SqliteVectorStore`. Retrievers talk to `SqliteVectorStore` (as `TextSearchable`).
- Do not add `upsert` / `list` / `delete` methods to `SqliteVectorStore`. If you feel the urge, the symptom is that the caller's dependency annotation is too wide.

### `LikeMemberRetriever` takes `SqliteModuleMemberRepository` directly
- Intentional YAGNI. Module-member text search is SQL-LIKE-only; no other backend currently wants to serve it. If a second backend appears later, extract a `ModuleMemberTextSearchable` Protocol at that point. Don't pre-extract one now.

### `SearchQuery` is `pydantic.dataclasses.dataclass` (not stdlib)
- Import: `from pydantic.dataclasses import dataclass as pyd_dataclass`. Validators via `@field_validator` and `@model_validator`. A stdlib `@dataclass(frozen=True, slots=True)` compiles but silently loses the validation.
- Construction failure raises `ValidationError`, not `ValueError`. Update any `except ValueError:` in the codebase accordingly.

### Fields removed from `SearchQuery`
- `package_filter`, `scope`, `title_filter` are GONE. Every reference across Python code, tests, YAML presets, and docstrings must be updated. Run `grep -rn 'package_filter\|\.scope\s\|title_filter' python/ tests/` after implementation; expected zero matches (the `scope` regex has a space to avoid matching the `SearchScope` enum import).

### `AppConfig` has NO Python-level defaults on YAML-backed fields
- Fields load from `python/pydocs_mcp/presets/default_config.yaml` via `settings_customise_sources`. Adding a Python default "for convenience" reintroduces the two-sources-of-truth bug the YAML-only design eliminated.
- Adding a new configurable field = edit the shipped YAML (required) + declare the field on `AppConfig` (no `=` default). The acceptance criteria include a snapshot test that asserts the shipped YAML parses cleanly; update that test when adding fields.

### Per-repo `safe_columns` and DDL are paired
- Adding a column to a table requires updating BOTH the DDL in `db.py` AND the matching `_PACKAGE_COLUMNS` / `_CHUNK_COLUMNS` / `_MEMBER_COLUMNS` frozenset in `storage/sqlite.py`. Missing either half either blocks safe filter queries or admits SQL injection. Test both paths.

### `IndexingService` depends on Protocols only
- `__init__` annotations are `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `UnitOfWork | None`. Do NOT annotate with concrete SQLite types in `IndexingService` — that re-couples. The service's own tests use in-memory Protocol fakes.
- `unit_of_work: UnitOfWork | None = None` — when `None`, the service runs each op without a transaction (best-effort). SQLite setups always pass a `SqliteUnitOfWork`. Future heterogeneous setups (e.g., Qdrant + SQLite) may pass `None` or a `CompositeUnitOfWork`.

### Breaking change rollout
- The `SearchQuery` shape change is the largest blast radius in this PR. Update tests first, confirm compile-time failures are all legitimate (not semantic regressions), then update production code. CI's existing test suite is the safety net.

### End-to-end smoke test fixture
- The E2E test (AC #23) uses a small fixture repo — 2–3 Python files + 1 README. Not the pydocs-mcp codebase itself (too slow). Create `tests/fixtures/minimal_pkg/` with intentionally simple code so the test is deterministic.

### Renaming / no aliasing / no "legacy"
- Carryover from sub-PRs #1 and #2. Zero occurrences of the word `legacy` in any added or modified source. No `OldName = NewName` compat aliases. After implementation, `grep -rn 'legacy\|package_filter' python/ tests/` should return zero.

---

## 14. Usage examples and design patterns

### How consumers interact with this PR

**1. Wire up the full stack at startup (what `server.py` does):**

```python
from pydocs_mcp import db
from pydocs_mcp.storage.sqlite import (
    SqlitePackageRepository, SqliteChunkRepository,
    SqliteModuleMemberRepository, SqliteVectorStore, SqliteUnitOfWork,
)
from pydocs_mcp.application.indexing_service import IndexingService

provider = db.build_connection_provider(cache_path)

package_repo = SqlitePackageRepository(provider=provider)
chunk_repo = SqliteChunkRepository(provider=provider)
member_repo = SqliteModuleMemberRepository(provider=provider)
vector_store = SqliteVectorStore(provider=provider)
uow = SqliteUnitOfWork(provider=provider)

indexing_service = IndexingService(
    package_store=package_repo,
    chunk_store=chunk_repo,
    module_member_store=member_repo,
    unit_of_work=uow,
)
```

**2. Reindex a package atomically:**

```python
await indexing_service.reindex_package(pkg, chunks, members)
# Six SQL operations share one BEGIN/COMMIT via the UoW contextvar.
```

**3. Read with dynamic filters (MultiFieldFormat dict):**

```python
# Simple equality
fastapi_chunks = await chunk_repo.list(filter={"package": "fastapi"}, limit=50)

# Operator form
project_only = await member_repo.list(
    filter={"package": "fastapi", "kind": {"in": ["class", "method"]}},
    limit=20,
)

# Title substring
routing_chunks = await chunk_repo.list(
    filter={"package": "fastapi", "title": {"like": "router"}},
    limit=10,
)
```

**4. Run a retrieval through the vector store:**

```python
matches = await vector_store.text_search(
    query_terms="router prefix",
    limit=8,
    filter={"package": "fastapi", "scope": "dependencies_only"},
)
```

**5. Use `SearchQuery` with `pre_filter` / `post_filter`:**

```python
sq = SearchQuery(
    terms="router prefix",
    max_results=8,
    pre_filter={"package": "fastapi"},              # pushed down to the store
    post_filter={"title": {"like": "APIRouter"}},   # applied after retrieval
    pre_filter_format=MetadataFilterFormat.MULTIFIELD,
    post_filter_format=MetadataFilterFormat.MULTIFIELD,
)
state = await chunk_pipeline.run(sq)
print(state.output)
```

**6. Swap to a different vector-store backend (future):**

```python
# No change to IndexingService, retrievers, pipelines, or MCP handlers.
chunk_store = QdrantVectorStore(client=qdrant_client, collection="pydocs")
indexing_service = IndexingService(
    package_store=package_repo,          # still SQLite
    chunk_store=chunk_store,             # now Qdrant
    module_member_store=member_repo,     # still SQLite
    unit_of_work=None,                    # no cross-backend transaction; best effort
)
```

### Design patterns used

| Pattern | Where | Role |
|---|---|---|
| **Repository (DDD)** | `SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository` | Collection-like access to each aggregate; CRUD + filtered reads. |
| **Unit of Work (DDD)** | `UnitOfWork` Protocol + `SqliteUnitOfWork` | Transactional boundary; atomic multi-repo writes. |
| **Application Service (DDD)** | `IndexingService` | Orchestrates repositories + UoW for the indexing use case; first of the family (sub-PR #4 adds query-side services). |
| **Hexagonal Architecture (Ports & Adapters)** | `storage/protocols.py` (ports) + `storage/sqlite.py` (adapters) | Backend-agnostic core; concrete SQLite (today) and future Qdrant / Chroma / Elasticsearch (future) are interchangeable adapters. |
| **Adapter (GoF)** | `SqliteFilterAdapter`, `QdrantFilterAdapter` (future), … | Each translates the canonical Filter tree into a backend-native query shape. |
| **Specification (DDD)** | Filter tree — `FieldEq`, `FieldIn`, `FieldLike`, `All` (+ declared `Any_`, `Not`) | Composable boolean expressions on metadata; backend-independent intent. |
| **Composite (GoF)** | `All` (+ future `Any_`, `Not`) | Filter nodes contain other Filter nodes; arbitrary-depth boolean trees. |
| **Plugin Registry** | `format_registry` | Multiple user-facing filter formats (MultiField today; ChromaDB/Qdrant/ES in future) mapped by name. |
| **Strategy (GoF)** | Per-backend `FilterAdapter` implementations | Each backend picks its translation strategy. |
| **Ambient context pattern** | `_sqlite_transaction: ContextVar` | Asyncio-task-local transaction handle; hides `connection` kwargs from Protocol signatures, keeping ports backend-agnostic. |
| **Dependency Inversion (SOLID)** | `IndexingService` ctor signatures | Depends on `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `UnitOfWork` Protocols only. No concrete type leakage. |
| **Interface Segregation (SOLID)** | Four storage capability Protocols (`ChunkStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`) | A backend implements only what it supports. Retrievers depend on the narrowest Protocol they need. |
| **Pydantic dataclass + validators** | `SearchQuery` | Construction-time validation (terms non-empty, format syntax check via `model_validator`). |
| **Layered configuration (12-factor)** | `AppConfig` via `settings_customise_sources` | Shipped `default_config.yaml` → user YAML → env vars → CLI flags. No Python defaults on YAML-backed fields. |

### Architectural choices

- **Four capability Protocols over one `VectorStore`.** A backend like Chroma (vector-only; no real BM25) legitimately can't implement `TextSearchable`. Splitting capabilities at the Protocol level lets the type checker catch wiring errors (trying to pass a Chroma store to a `Bm25ChunkRetriever`) at edit time rather than runtime.
- **Filter tree as lingua franca; two adapter kinds around it.** `FilterFormat` adapters sit on the user side (dict/native → tree); `FilterAdapter` adapters sit on the backend side (tree → native query). The tree is the canonical representation in between — exactly one target for new formats AND exactly one source for new backends.
- **Ambient transactions via `ContextVar` beat per-method `connection` kwargs.** Threading `connection` through every repository method would either leak SQLite-specific types into the port or force a generic `Any` parameter that defeats type-checking. `ContextVar` keeps the port surface clean.
- **`SqliteVectorStore` is retrieval-only.** Separating retrieval (FTS5 MATCH) from CRUD (`SqliteChunkRepository`) matches SRP: each class has one reason to change. Retrievers depend on `TextSearchable`; `IndexingService` depends on `ChunkStore`. No caller sees the whole surface.
- **Application service, not Domain service.** `IndexingService` coordinates across aggregates, owns the transaction boundary, and handles a use case. Domain logic (what constitutes a valid package, etc.) lives on value objects, not the service.
- **`LikeMemberRetriever` takes the repository directly (YAGNI).** Module-member text search is SQL-LIKE only today; abstracting to a `ModuleMemberTextSearchable` Protocol now would be empty generality. Extract later if a second backend joins.
- **Drop-and-recreate schema migration.** The cache is rebuildable; `PRAGMA user_version` mismatch triggers drop + recreate. Simpler than ALTER TABLE paths, zero data-loss risk (no user-authored data in the cache).
- **YAML as single source of truth for defaults.** Shipped `default_config.yaml` holds every default; `AppConfig` fields declare no Python defaults. Eliminates the "code says X, doc says Y" drift bug.

---

## 15. Follow-up sub-PRs (not in scope)

Each will get its own brainstorm + spec.

- **Sub-PR #4** — Query-side Application Services: `SearchDocsService`, `SearchApiService`, `PackageLookupService`, `ModuleIntrospectionService`, `IndexProjectUseCase`. `server.py` handlers become thin wrappers over these services. Matches the `IndexingService` pattern introduced here.
- **Sub-PR #5** — Indexer strategy split (inspect / static as `Extractor` strategies) + **pluggable chunking strategies** (new `chunking/` subpackage — see sub-PR #2 §13 placeholder).
- **Sub-PR #6** — Query parsing component + Pydantic at MCP boundary.
- **Sub-PR #7** — Error-tolerance primitives: `TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`.

Future additive work this PR unblocks — each drops in without modifying `IndexingService`, retrievers, pipeline stages, or the filter tree:

- **`FilterTreeFormat`** — dict form with `$and` / `$or` / `$not`. ~50 LOC. Register with `format_registry`.
- **`ChromaFormat` / `QdrantFormat` / `ElasticsearchFormat`** — native-format input adapters. ~50 LOC each.
- **Backend adapters (`QdrantFilterAdapter`, `ChromaFilterAdapter`, `ElasticsearchFilterAdapter`)** — tree → native backend query. ~60–100 LOC each. Ship alongside their backend's `VectorStore`.
- **`QdrantVectorStore(ChunkStore, TextSearchable, VectorSearchable, HybridSearchable)`** — native-hybrid backend. Takes an `AsyncQdrantClient`. No changes to retrievers or `IndexingService`.
- **`ChromaVectorStore(ChunkStore, VectorSearchable)`** — dense-only backend; type checker prevents wiring it to `Bm25ChunkRetriever`.
- **`ElasticsearchVectorStore(ChunkStore, TextSearchable, VectorSearchable, HybridSearchable)`** — all-capability backend.
- **`SqliteWithVecStore`** — adds `VectorSearchable` + `HybridSearchable` to the current SQLite setup via `sqlite-vec`.
- **Dense retriever / LLM rerank stage / native-hybrid retriever** — each ships as a `PipelineStage` or `Retriever` implementing the already-declared capability Protocols.
- **`CompositeUnitOfWork`** — coordinates multiple backend UoWs for heterogeneous indexing with best-effort rollback.
- **Cross-table JOIN support in `SqliteFilterAdapter`** — grows the adapter into a `plan()` that decides tables + joins based on the filter's field namespace; `SqliteVectorStore` composes additional repositories as needed.
