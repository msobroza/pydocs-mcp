# Sub-PR #4 — Query-side + write-side Application Services; thin MCP handlers

**Status:** Approved (2026-04-19) — ready for implementation planning in a later session
**Date:** 2026-04-19
**Depends on:** sub-PR #1 (approved — **canonical data model in §5**), sub-PR #2 (approved — `retrieval/` package), sub-PR #3 (approved — `storage/` package + `IndexingService`).
**Follows-on:** sub-PR #5 (indexer strategy split + chunking strategies — consumes the `DependencyResolver` / `ChunkExtractor` / `MemberExtractor` Protocols introduced here), sub-PR #6 (query parsing + Pydantic at MCP boundary), sub-PR #7 (error-tolerance primitives — `TryStage`, `RetryStage`, etc.).

**⚠️ Canonical data model:** this spec uses the canonical data model defined in **sub-PR #1 §5**. Any reader confusion about `Chunk`, `ModuleMember`, `ChunkList`, `ModuleMemberList`, `PipelineResultItem`, `SearchResponse`, `ChunkFilterField`, `ModuleMemberFilterField`, `ChunkOrigin`, `MetadataFilterFormat` should be resolved by consulting sub-PR #1 §5 first.

---

## 1. Goal

Extract the read-side logic currently sitting inside `server.py` MCP handlers into dedicated Application Services, mirroring sub-PR #3's `IndexingService` pattern. Add `IndexProjectService` as a write-side orchestrator that wraps the existing indexer bootstrap flow. After this PR:

- `server.py` handlers become thin wrappers over services.
- `__main__.py` CLI subcommands become thin wrappers over the same services (with different pipeline variants).
- Every use case is represented by one Application Service with Protocol-typed dependencies.
- MCP tool signatures and observable outputs remain byte-identical to `main`.

---

## 2. Decisions locked in during brainstorming

| Topic | Decision |
|---|---|
| Service granularity | **Four query-side services + one write-side** — `PackageLookupService` (covers `list_packages` + `get_package_doc`), `SearchDocsService` (`search_docs`), `SearchApiService` (`search_api`), `ModuleIntrospectionService` (`inspect_module`), `IndexProjectService` (write-side bootstrap orchestration). |
| Return types | Services return **`SearchResponse`** or typed domain objects (`Package`, `Package \| None`, `str`) — **never pre-formatted strings** except for `ModuleIntrospectionService` which returns a string today. Presentation happens at the caller (MCP handler / CLI handler). |
| Formatter stage behavior | `TokenBudgetFormatterStage` produces a **composite `Chunk`** with `metadata["origin"] == ChunkOrigin.COMPOSITE_OUTPUT.value`, wrapped in a 1-element `ChunkList`. No new type added. |
| Canonical data model | Defined in sub-PR #1 §5. `Chunk` has `text` + `metadata` dict; `ModuleMember` is fully generic; `ChunkList` / `ModuleMemberList` list wrappers; filter field names live in `ChunkFilterField` / `ModuleMemberFilterField` StrEnums. |
| Pipeline ownership | Services hold a pipeline as a **field**, injected at construction. MCP and CLI each construct their own service instances with appropriate pipeline variants (with / without `TokenBudgetFormatterStage`). |
| Dependencies on services | **Protocol-typed** — `PackageStore`, `ChunkStore`, `ModuleMemberStore`, `CodeRetrieverPipeline`, `DependencyResolver`, `ChunkExtractor`, `MemberExtractor`. No concrete SQLite / filesystem types in service constructors. |
| `IndexProjectService` | Ships in sub-PR #4 (not deferred) with supporting Protocols. Bootstrap logic from `__main__.py` + `indexer.py` moves behind the service; `indexer.py` module functions become the concrete `ChunkExtractor` / `MemberExtractor` implementations. |
| Error handling | Services **propagate** exceptions. Blanket catch only at MCP handlers in `server.py` (returns tool-specific fallback string) and CLI top-level in `__main__.py` (prints error, exits non-zero). |
| Testing strategy | **Mixed by layer + one end-to-end smoke test.** Services use in-memory Protocol fakes. Server + CLI handlers use fake services. One E2E test wires the full real stack on a tmp SQLite. |
| MCP surface stability | MCP tool signatures byte-identical to `main` — same tool names, parameter names, type annotations, docstrings, return-string shapes. |
| Serialization | Services do NOT implement `to_dict` / `from_dict`. They are application-layer wiring, not user-configurable components. |
| Existing tests | **No existing test deleted.** Mechanical updates only — renamed imports, new service-typed dependencies in mocks. |

---

## 3. Architecture overview

```
┌──────────────────────────────────────────────────────────────────────┐
│   server.py (MCP async tools) + __main__.py (CLI subcommands)        │
│   Each handler:                                                      │
│     1. Translates legacy params (internal, package, topic) → pre_filter dict │
│     2. Builds SearchQuery (sub-PR #3 pydantic dataclass)             │
│     3. Calls a service                                               │
│     4. Dispatches on response type (composite Chunk vs ChunkList etc)│
│     5. Formats for its surface (markdown for MCP, stdout lines for CLI)│
│     6. Blanket catches → tool-specific fallback string / CLI error   │
└────────────────────────────┬─────────────────────────────────────────┘
                             │ one service per use case
                             ▼
┌──────────────────────────────────────────────────────────────────────┐
│              application/ — Application Services                     │
│                                                                      │
│  IndexingService            (sub-PR #3 — write side, per-package)    │
│  IndexProjectService        (sub-PR #4 — write side, whole project)  │
│  PackageLookupService       (sub-PR #4 — reads package metadata)     │
│  SearchDocsService          (sub-PR #4 — chunk retrieval pipeline)   │
│  SearchApiService           (sub-PR #4 — member retrieval pipeline)  │
│  ModuleIntrospectionService (sub-PR #4 — live importlib + inspect)   │
│                                                                      │
│  All services depend only on Protocols.                              │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
        ┌────────────────────┴────────────────────┐
        ▼                                         ▼
┌──────────────────────────┐      ┌──────────────────────────────────┐
│ retrieval/               │      │ storage/ (sub-PR #3)             │
│  CodeRetrieverPipeline   │      │  PackageStore, ChunkStore,       │
│  (sub-PR #2)             │      │  ModuleMemberStore Protocols     │
│                          │      │  SqliteVectorStore (TextSearchable)│
└──────────────────────────┘      └──────────────────────────────────┘
```

---

## 4. Scope

### In scope

- New `python/pydocs_mcp/application/` files (adds 5 new service files alongside `indexing_service.py` from sub-PR #3):
  - `package_lookup_service.py`
  - `search_docs_service.py`
  - `search_api_service.py`
  - `module_introspection_service.py`
  - `index_project_service.py`
- New Protocols in `application/protocols.py` (or in individual service files):
  - `DependencyResolver` — wraps `deps.resolve()` logic.
  - `ChunkExtractor` — wraps current `indexer.py` chunk-extraction functions.
  - `MemberExtractor` — wraps current `indexer.py` member-extraction functions.
  - Concrete adapters over the existing `deps.py` / `indexer.py` module functions ship alongside — minimal wrappers (~30 LOC each).
- Value object `IndexingStats` (in `models.py` or `application/`) — tracks per-project indexing results (`indexed`, `cached`, `failed`, `project_indexed: bool`).
- `server.py` rewrite — all 5 MCP handlers become thin wrappers over services. Each handler ~10–20 LOC.
- `__main__.py` rewrite — `query` / `api` CLI subcommands wrap `SearchDocsService` / `SearchApiService` with CLI-flavored pipeline variants. `index` / `serve` subcommands call `IndexProjectService`.
- Formatting helpers (`format_chunks_markdown_within_budget`, `format_members_markdown_within_budget`) in `application/formatting.py` — shared between MCP fallback path and CLI formatter.
- Test subtree `tests/application/` covering all five new services + handlers + end-to-end smoke.

### Out of scope (deferred to later sub-PRs)

- **Sub-PR #5** — replacing the `ChunkExtractor` / `MemberExtractor` / `DependencyResolver` adapters with strategy-based implementations (`HeadingChunker`, `AstPythonChunker`, etc.). Sub-PR #4 ships thin adapters wrapping today's `indexer.py` / `deps.py` functions.
- **Sub-PR #6** — Pydantic input validation on MCP tool arguments.
- **Sub-PR #7** — error-tolerance primitives (`TryStage`, `RetryStage`).
- CLI output format redesign — stays byte-identical.
- Service-level logging / metrics — handlers log on fallback; services propagate.

---

## 5. Domain components

### 5.1 Application Services

All `@dataclass(frozen=True, slots=True)` with Protocol-typed dependencies injected at construction.

#### `PackageLookupService`

```python
@dataclass(frozen=True, slots=True)
class PackageLookupService:
    package_store: PackageStore
    chunk_store: ChunkStore                   # used for get_package_doc chunk retrieval
    module_member_store: ModuleMemberStore    # used for get_package_doc API listing

    async def list_packages(self) -> tuple[Package, ...]:
        return tuple(await self.package_store.list())

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        pkg = await self.package_store.get(package_name)
        if pkg is None:
            return None
        chunks = await self.chunk_store.list(
            filter={ChunkFilterField.PACKAGE.value: package_name},
            limit=10,
        )
        members = await self.module_member_store.list(
            filter={ModuleMemberFilterField.PACKAGE.value: package_name},
            limit=30,
        )
        return PackageDoc(package=pkg, chunks=chunks, members=members)
```

Returns a `PackageDoc` value object (new in this PR — groups the three query results for one handler's convenience):

```python
@dataclass(frozen=True, slots=True)
class PackageDoc:
    package: Package
    chunks: tuple[Chunk, ...]
    members: tuple[ModuleMember, ...]
```

#### `SearchDocsService`

```python
@dataclass(frozen=True, slots=True)
class SearchDocsService:
    chunk_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.chunk_pipeline.run(query)
        return SearchResponse(
            result=state.result or ChunkList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
        )
```

#### `SearchApiService`

```python
@dataclass(frozen=True, slots=True)
class SearchApiService:
    member_pipeline: CodeRetrieverPipeline

    async def search(self, query: SearchQuery) -> SearchResponse:
        state = await self.member_pipeline.run(query)
        return SearchResponse(
            result=state.result or ModuleMemberList(items=()),
            query=state.query,
            duration_ms=state.duration_ms,
        )
```

#### `ModuleIntrospectionService`

Live-import-based; not pipeline-backed. Uses `PackageStore` only to verify the package is indexed.

```python
@dataclass(frozen=True, slots=True)
class ModuleIntrospectionService:
    package_store: PackageStore

    async def inspect(self, package_name: str, submodule: str = "") -> str:
        # Verify package exists (indexed)
        if await self.package_store.get(package_name) is None:
            return f"'{package_name}' is not indexed. Use list_packages() to see available packages."
        # Run importlib + inspect in a thread (sync operations)
        target = package_name + (f".{submodule}" if submodule else "")
        return await asyncio.to_thread(self._inspect_target, target)

    def _inspect_target(self, target: str) -> str:
        # Same logic as today's server.py inspect_module handler, extracted here.
        ...
```

Returns a `str` (not `SearchResponse`) — the operation is fundamentally presentational, and the return value is a formatted module summary already.

#### `IndexProjectService` (write-side)

```python
@dataclass(frozen=True, slots=True)
class IndexProjectService:
    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor

    async def index_project(
        self, project_dir: Path, *, force: bool = False,
        include_project_source: bool = True,
    ) -> IndexingStats:
        stats = IndexingStats()
        if force:
            await self.indexing_service.clear_all()
        if include_project_source:
            pkg, chunks, members = await self._extract_project(project_dir)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.project_indexed = True
        for dep_name in await self.dependency_resolver.resolve(project_dir):
            await self._index_one_dependency(dep_name, stats)
        return stats

    async def _extract_project(self, project_dir: Path):
        # Build Package, Chunks (with metadata populated), ModuleMembers (generic)
        ...

    async def _index_one_dependency(self, dep_name: str, stats: IndexingStats) -> None:
        try:
            pkg, chunks, members = await self._extract_dependency(dep_name)
            await self.indexing_service.reindex_package(pkg, chunks, members)
            stats.indexed += 1
        except Exception as e:
            log.warning("  fail %s: %s", dep_name, e)
            stats.failed += 1
```

### 5.2 Supporting Protocols

```python
# application/protocols.py

class DependencyResolver(Protocol):
    async def resolve(self, project_dir: Path) -> tuple[str, ...]: ...

class ChunkExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[tuple[Chunk, ...], Package]: ...
    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[tuple[Chunk, ...], Package]: ...

class MemberExtractor(Protocol):
    async def extract_from_project(
        self, project_dir: Path,
    ) -> tuple[ModuleMember, ...]: ...
    async def extract_from_dependency(
        self, dep_name: str,
    ) -> tuple[ModuleMember, ...]: ...
```

Sub-PR #4 ships thin adapters (~30 LOC each) wrapping existing `deps.py` / `indexer.py` module functions. Sub-PR #5 replaces them with strategy-based implementations (HeadingChunker etc.).

### 5.3 `IndexingStats` value object

```python
@dataclass(slots=True)
class IndexingStats:
    """Mutable stats; accumulated as index_project iterates dependencies."""
    project_indexed: bool = False
    indexed: int = 0
    cached: int = 0
    failed: int = 0
```

Mutable (`slots=True` but not `frozen=True`) because it's a transient accumulator inside `IndexProjectService.index_project`. Not used across other services.

### 5.4 Formatting helpers (`application/formatting.py`)

Extracted from `TokenBudgetFormatterStage` implementation for use by MCP-handler fallback paths and CLI stdout rendering:

```python
def format_chunks_markdown_within_budget(
    chunks: tuple[Chunk, ...],
    budget_tokens: int,
    formatter: ChunkMarkdownFormatter,
) -> str:
    """Format a list of chunks as markdown within a token budget.
    Used by:
      - TokenBudgetFormatterStage (wraps result as composite Chunk)
      - MCP handler fallback (when pipeline didn't include the formatter stage)
      - CLI handler (for non-composite output formatting)
    """
    ...

def format_members_markdown_within_budget(...) -> str: ...
def format_chunks_cli_stdout(chunks: tuple[Chunk, ...]) -> str: ...
def format_members_cli_stdout(members: tuple[ModuleMember, ...]) -> str: ...
```

Single source of truth for rendering. Stage + handler both call these helpers.

### 5.5 MCP handlers (rewritten to be thin)

```python
# server.py

@mcp.tool()
async def list_packages() -> str:
    """List indexed packages. '__project__' = your source code."""
    try:
        packages = await package_lookup_service.list_packages()
        return "\n".join(f"- {p.name} {p.version} — {p.summary}" for p in packages)
    except Exception as e:
        log.warning("list_packages failed: %s", e)
        return "Failed to list packages."


@mcp.tool()
async def get_package_doc(package: str) -> str:
    """Full docs for a package. Use '__project__' for your own code."""
    try:
        normalized = normalize_package_name(package) if package != "__project__" else package
        doc = await package_lookup_service.get_package_doc(normalized)
        if doc is None:
            return f"'{package}' not found."
        return _render_package_doc(doc)
    except Exception as e:
        log.warning("get_package_doc failed: %s", e)
        return f"'{package}' not found."


@mcp.tool()
async def search_docs(
    query: str, package: str = "", internal: bool | None = None, topic: str = "",
) -> str:
    try:
        pre_filter = _build_chunk_pre_filter(package, internal, topic)
        sq = SearchQuery(terms=query, pre_filter=pre_filter or None)
        response = await search_docs_service.search(sq)
        return _render_search_response_chunks(response)
    except Exception as e:
        log.warning("search_docs failed: %s", e)
        return "No matches found."


@mcp.tool()
async def search_api(
    query: str, package: str = "", internal: bool | None = None,
) -> str:
    try:
        pre_filter = _build_member_pre_filter(package, internal)
        sq = SearchQuery(terms=query, pre_filter=pre_filter or None)
        response = await search_api_service.search(sq)
        return _render_search_response_members(response)
    except Exception as e:
        log.warning("search_api failed: %s", e)
        return "No symbols found."


@mcp.tool()
async def inspect_module(package: str, submodule: str = "") -> str:
    try:
        return await module_introspection_service.inspect(package, submodule)
    except Exception as e:
        log.warning("inspect_module failed: %s", e)
        return f"Cannot import '{package}.{submodule}'."


def _render_search_response_chunks(response: SearchResponse) -> str:
    """MCP path: pipeline includes TokenBudgetFormatterStage → result is 1-element
    ChunkList whose only Chunk has metadata['origin'] == composite_output."""
    match response.result:
        case ChunkList(items=(composite,)) if composite.metadata.get(
            ChunkFilterField.ORIGIN.value
        ) == ChunkOrigin.COMPOSITE_OUTPUT.value:
            return composite.text
        case ChunkList(items=items) if items:
            # Fallback — pipeline didn't format; do it here
            return format_chunks_markdown_within_budget(
                items, budget_tokens=CONTEXT_TOKEN_BUDGET,
                formatter=ChunkMarkdownFormatter(),
            )
        case _:
            return "No matches found."
```

Each handler ~10–20 LOC. All translation + dispatch + fallback in the adapter layer. No business logic.

### 5.6 CLI handlers (thin wrappers over services)

```python
# __main__.py (excerpt)

async def _cmd_query(args, search_docs_service_cli: SearchDocsService) -> int:
    try:
        pre_filter = {ChunkFilterField.PACKAGE.value: args.package} if args.package else None
        sq = SearchQuery(terms=" ".join(args.terms), pre_filter=pre_filter)
        response = await search_docs_service_cli.search(sq)
        match response.result:
            case ChunkList(items=items) if items:
                print(format_chunks_cli_stdout(items))
                return 0
            case _:
                print("No matches found.")
                return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
```

CLI's `search_docs_service_cli` is a separate `SearchDocsService` instance constructed with a pipeline that **does NOT include** `TokenBudgetFormatterStage` — so its `response.result` is a raw `ChunkList` of N items, not a 1-element composite list.

### 5.7 Startup wiring (server.py excerpt)

```python
def run(db_path: Path) -> None:
    app_config = AppConfig.load()
    provider = db.build_connection_provider(db_path)

    # Storage layer (sub-PR #3)
    package_repo = SqlitePackageRepository(provider=provider)
    chunk_repo = SqliteChunkRepository(provider=provider)
    member_repo = SqliteModuleMemberRepository(provider=provider)
    vector_store = SqliteVectorStore(provider=provider)
    uow = SqliteUnitOfWork(provider=provider)

    # Pipelines (sub-PR #2) — MCP variants WITH TokenBudgetFormatterStage
    chunk_pipeline_mcp = _build_chunk_pipeline_with_formatter(vector_store, app_config)
    member_pipeline_mcp = _build_member_pipeline_with_formatter(member_repo, app_config)

    # Application services (sub-PR #3 + #4)
    indexing_service = IndexingService(
        package_store=package_repo,
        chunk_store=chunk_repo,
        module_member_store=member_repo,
        unit_of_work=uow,
    )
    index_project_service = IndexProjectService(
        indexing_service=indexing_service,
        dependency_resolver=DependencyResolverAdapter(),
        chunk_extractor=ChunkExtractorAdapter(),
        member_extractor=MemberExtractorAdapter(),
    )
    package_lookup_service = PackageLookupService(
        package_store=package_repo,
        chunk_store=chunk_repo,
        module_member_store=member_repo,
    )
    search_docs_service = SearchDocsService(chunk_pipeline=chunk_pipeline_mcp)
    search_api_service = SearchApiService(member_pipeline=member_pipeline_mcp)
    module_introspection_service = ModuleIntrospectionService(package_store=package_repo)

    # MCP handlers close over services (see §5.5)
    mcp.run(...)
```

~25 lines of wiring at the server boundary. All Protocol-typed.

---

## 6. Example flows

### 6.1 MCP `search_docs("router", package="fastapi", topic="APIRouter")`

```
1. Handler builds pre_filter:
     {ChunkFilterField.PACKAGE.value: "fastapi",
      ChunkFilterField.TITLE.value:   {"like": "APIRouter"}}
2. Constructs SearchQuery(terms="router", pre_filter=above)
3. Calls search_docs_service.search(sq)
4. SearchDocsService runs chunk_pipeline_mcp:
     - ChunkRetrievalStage: retriever returns ChunkList of N Chunks
     - MetadataPostFilterStage: no-op (post_filter is None)
     - LimitStage(8): truncates to 8
     - TokenBudgetFormatterStage:
         * formats each Chunk's text within budget
         * emits ChunkList(items=(composite,)) where
           composite.metadata["origin"] == "composite_output"
5. Service wraps in SearchResponse(result=ChunkList(...), query=sq, duration_ms=...)
6. Handler dispatches:
     * case ChunkList(items=(composite,)) where composite.metadata["origin"] is COMPOSITE_OUTPUT
       → return composite.text
7. Returns formatted markdown string to the MCP client.
```

### 6.2 CLI `pydocs-mcp query "router" -p fastapi`

```
1. __main__.py parses argparse.
2. Builds pre_filter: {ChunkFilterField.PACKAGE.value: "fastapi"}
3. Constructs SearchQuery(terms="router", pre_filter=above)
4. Calls search_docs_service_cli.search(sq) — CLI service has a DIFFERENT pipeline
   (no TokenBudgetFormatterStage).
5. Pipeline runs:
     - ChunkRetrievalStage
     - MetadataPostFilterStage
     - LimitStage(8)
6. Returns SearchResponse(result=ChunkList(items=(c1, c2, ...))).
7. CLI handler:
     case ChunkList(items=items) if items:
         print(format_chunks_cli_stdout(items))
```

### 6.3 `pydocs-mcp index . --force`

```
1. Build IndexProjectService with adapters.
2. Call index_project_service.index_project(project_dir, force=True).
3. Inside:
     - indexing_service.clear_all()
     - Extract project chunks + members, reindex_package()
     - For each dep: extract + reindex_package(); on error, stats.failed += 1
4. Return IndexingStats; CLI prints summary ("ok N, cached M, failed K").
```

### 6.4 MCP `list_packages()`

```
1. Handler calls package_lookup_service.list_packages()
2. Service calls package_store.list()
3. Returns tuple[Package, ...]
4. Handler formats: "\n".join(f"- {p.name} {p.version} — {p.summary}" for p in ...)
```

---

## 7. Error handling

- **Services propagate.** No blanket `try/except` inside any service method.
- **Pipelines propagate** (same as sub-PR #2).
- **Repositories propagate** (same as sub-PR #3).
- **MCP handlers** catch broadly: each has `try: ...; except Exception as e: log.warning(...); return <tool-specific-fallback-string>`. Fallback strings match today's behavior byte-for-byte.
- **CLI top-level** catches at the `_cmd_X` level: prints `f"Error: {e}"` to `stderr`, returns non-zero exit code.
- **SearchQuery pydantic validation** fails at construction — handlers treat this as "malformed query" and return the no-matches fallback.

No new exception types. All exceptions remain `Exception` subclasses flowing through the catch chain.

---

## 8. Code organization — files touched

### New `application/` files

| File | Contents | ~LOC |
|---|---|---|
| `application/__init__.py` | Re-exports all 6 services + PackageDoc + IndexingStats + Protocols | 30 |
| `application/protocols.py` | `DependencyResolver`, `ChunkExtractor`, `MemberExtractor` Protocols | 50 |
| `application/package_lookup_service.py` | `PackageLookupService` + `PackageDoc` value object | 80 |
| `application/search_docs_service.py` | `SearchDocsService` | 40 |
| `application/search_api_service.py` | `SearchApiService` | 40 |
| `application/module_introspection_service.py` | `ModuleIntrospectionService` (includes live-import logic extracted from server.py) | 80 |
| `application/index_project_service.py` | `IndexProjectService` + `IndexingStats` + extraction-adapter classes | 200 |
| `application/formatting.py` | Shared markdown/CLI formatting helpers | 120 |

**New-package subtotal: ~640 LOC.**

### Modified files

| File | Change | ~LOC diff |
|---|---|---|
| `python/pydocs_mcp/server.py` | Rewrite all 5 handlers to be thin wrappers over services. Remove raw SQL, remove live-import logic (moves to service). Startup wiring (~25 LOC) constructs all services. | ±250 |
| `python/pydocs_mcp/__main__.py` | `query`/`api` subcommands use `SearchDocsService`/`SearchApiService` with CLI-flavored pipelines. `index`/`serve` use `IndexProjectService`. | ±150 |
| `python/pydocs_mcp/indexer.py` | Remove bootstrap-flow code (moved to `IndexProjectService`). Keep extraction functions; they become the adapter implementations. | ±80 |
| `python/pydocs_mcp/models.py` | Add `PackageDoc` value object + `IndexingStats` | +25 |
| `python/pydocs_mcp/retrieval/stages.py` | `TokenBudgetFormatterStage` updated to emit composite `Chunk` (per canonical §5 of sub-PR #1) if it isn't already | ±40 |

### Tests

| File | ~LOC |
|---|---|
| `tests/application/test_package_lookup_service.py` | 120 |
| `tests/application/test_search_docs_service.py` | 80 |
| `tests/application/test_search_api_service.py` | 80 |
| `tests/application/test_module_introspection_service.py` | 100 |
| `tests/application/test_index_project_service.py` | 150 |
| `tests/application/test_server_handlers.py` | 180 |
| `tests/application/test_cli_handlers.py` | 120 |
| `tests/application/test_formatting.py` | 80 |
| `tests/application/test_end_to_end.py` | 200 |
| `tests/application/conftest.py` | In-memory Protocol fakes (`InMemoryPackageStore`, `InMemoryChunkStore`, `InMemoryModuleMemberStore`, `InMemoryPipeline`, `InMemoryDepResolver`, `InMemoryChunkExtractor`, `InMemoryMemberExtractor`) | 200 |

**Tests subtotal: ~1,310 LOC.**

### PR size rollup

| | LOC |
|---|---|
| New `application/` files | ~640 |
| Modified existing files | ~545 |
| Tests | ~1,310 |
| **Total** | **~2,495** |

Large, similar size to sub-PR #3. Natural split if needed:
- **#4a:** 4 query-side services + MCP handler rewrite + query-side tests
- **#4b:** `IndexProjectService` + extractor adapters + CLI handler rewrite + indexer.py slim-down + write-side tests

Preference is one PR — the services share wiring and test scaffolding.

---

## 9. Cross-PR commitments (ratified; additive to sub-PR #3)

| # | Commitment |
|---|---|
| 1 | Application services are **Protocol-typed** — constructors never mention concrete SQLite / filesystem / importlib types. Fully backend-agnostic by construction. |
| 2 | Services **propagate** exceptions. Only MCP handlers + CLI top-level catch. |
| 3 | Services do NOT implement `to_dict` / `from_dict` — they are wiring, not serializable components. |
| 4 | Presentation (markdown rendering, CLI stdout rendering) lives in `application/formatting.py` — single source of truth, callable from pipeline stages AND MCP / CLI handlers. |
| 5 | MCP tool surface byte-identical to `main` — signatures, docstrings, output strings. |
| 6 | CLI `query` / `api` subcommands use DIFFERENT service instances than MCP — different pipeline variants (with / without `TokenBudgetFormatterStage`). Same service class, different construction at the entry point. |
| 7 | `IndexProjectService` is the only write-side Application Service orchestrator; per-package atomicity is `IndexingService` (sub-PR #3). |
| 8 | Sub-PR #5 replaces the adapter implementations of `DependencyResolver` / `ChunkExtractor` / `MemberExtractor` with strategy-based implementations — zero changes to `IndexProjectService` or any other service. |

---

## 10. Risks and rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| MCP handler output drifts from byte-identical (e.g., a trailing newline, different error message) | Medium | Golden-fixture tests assert `handler(args) == expected_string_from_main` for every MCP tool on a deterministic fixture |
| Composite-chunk dispatch in handlers doesn't match the pipeline's formatter output | Medium | Unit test asserts `response.result` structure for both pipeline variants; end-to-end smoke asserts final string |
| `IndexProjectService` orchestration order changes observable behavior (project before vs after deps) | Low | Order kept identical to today's `__main__.py` flow; snapshot test asserts index counts match pre-PR |
| `ModuleIntrospectionService.inspect()` async → `asyncio.to_thread` wrapping changes error modes | Low | Test covers `ImportError`, `AttributeError`, success with submodule, success without submodule |
| CLI `--force` flag semantics change | Low | Test asserts `force=True` calls `indexing_service.clear_all()` before iteration; `force=False` does not |
| Extractor-adapter signatures don't match sub-PR #5's planned strategy Protocols | Medium | Sub-PR #4's `ChunkExtractor` / `MemberExtractor` Protocols are drafted with sub-PR #5 in mind — minimal method surface (`extract_from_project`, `extract_from_dependency`); sub-PR #5 can add methods additively without reopening sub-PR #4 |
| Blanket `except Exception` at handler level swallows programmer bugs (typos) | Low | The catch LOGS with `log.warning(...)` including the exception type and message; tests assert specific exceptions bubble through to the log call |
| Service-startup wiring in `server.py` fails partial-init | Low | Wiring code ordered by dependency; each construction is a single-line dataclass init; no side effects until `mcp.run()` |
| Existing tests depend on `indexer.py`'s top-level bootstrap functions | Medium | Bootstrap functions deleted only after tests are redirected to `IndexProjectService`; interim commits keep both available briefly |

**Rollback:** revert the merge commit. Services are new files; deleting `application/*service.py` and reverting `server.py` / `__main__.py` / `indexer.py` restores pre-PR state. SQLite cache + DDL unchanged → no data rebuild needed.

---

## 11. Acceptance criteria

1. `python/pydocs_mcp/application/` exists with files: `__init__.py`, `protocols.py`, `package_lookup_service.py`, `search_docs_service.py`, `search_api_service.py`, `module_introspection_service.py`, `index_project_service.py`, `formatting.py`, plus the existing `indexing_service.py` from sub-PR #3.
2. `PackageLookupService`, `SearchDocsService`, `SearchApiService`, `ModuleIntrospectionService`, `IndexProjectService` are each `@dataclass(frozen=True, slots=True)`, with constructor parameters typed by Protocols only (no concrete SQLite types, no `Path` or `sqlite3.Connection`).
3. Supporting Protocols exist: `DependencyResolver`, `ChunkExtractor`, `MemberExtractor` — all `@runtime_checkable`.
4. Concrete adapters for the Protocols live alongside (thin wrappers over existing `deps.py` / `indexer.py` module functions) — ~30 LOC each.
5. `PackageDoc` value object + `IndexingStats` accumulator added to `models.py` (or `application/`).
6. `application/formatting.py` contains `format_chunks_markdown_within_budget`, `format_members_markdown_within_budget`, `format_chunks_cli_stdout`, `format_members_cli_stdout`. `TokenBudgetFormatterStage` in `retrieval/stages.py` calls `format_chunks_markdown_within_budget` — no duplicate rendering logic.
7. `server.py` handlers are each ≤25 LOC, each with a single `try: ... except Exception:` catch, each returning a surface-appropriate fallback string.
8. MCP tool signatures byte-identical to `main`: `list_packages`, `get_package_doc`, `search_docs`, `search_api`, `inspect_module` — parameter names, type annotations, docstrings, return-string shapes unchanged.
9. CLI subcommands `query` / `api` / `index` / `serve` produce output byte-identical to `main` on the golden fixture repo. `pydocs-mcp index . --force` triggers the full re-index via `IndexProjectService`.
10. No concrete type (`Sqlite*Repository`, `sqlite3.Connection`, `Path`) appears in the type annotation of any service constructor parameter.
11. `IndexProjectService.index_project` order matches `main`: project source first (if `include_project_source=True`), then each dep in the order returned by `DependencyResolver.resolve`.
12. Services propagate exceptions. The word `legacy` does not appear. No blanket `except Exception: return []` or similar swallow exists in any service / protocol / adapter.
13. `tests/application/` contains the 10 test files listed in §8, covering every service + handler + formatter + end-to-end. Each service test uses Protocol fakes; `test_end_to_end.py` wires the full real stack against a tmp-dir SQLite fixture repo.
14. Golden-fixture test asserts MCP tool outputs byte-identical to `main` for `list_packages`, `get_package_doc("fastapi")`, `search_docs("routing", package="fastapi")`, `search_api("APIRouter", package="fastapi")`, `inspect_module("fastapi", "routing")` — all run through the service layer.
15. Composite-chunk dispatch test: given a pipeline with `TokenBudgetFormatterStage`, the handler returns `composite.text` unchanged (no double-formatting). Given a pipeline without the formatter, the handler calls `format_chunks_markdown_within_budget` — the test asserts only one formatting path per request.
16. `__main__.py` CLI handles `Exception` at the top of each subcommand; exit codes: 0 success, non-zero error.
17. Every existing test in `tests/` is still present after this PR. Updates to existing tests are mechanical (imports, service-fake wiring). No test deleted.
18. `pyproject.toml` declares no new runtime dependencies.
19. No new Pydantic usage at the MCP boundary — MCP tool arguments remain plain Python types (`str`, `bool | None`). Pydantic enters only at `SearchQuery` construction inside the handler (from sub-PR #3).
20. Behavior parity: end-to-end smoke test produces the same MCP outputs and same CLI outputs on the golden fixture as `main`.

---

## 12. Open items

None — all design decisions ratified during brainstorming.

---

## 13. Implementation notes — pitfalls for implementers without full brainstorming context

Points easy to get wrong without the backstory.

### Canonical data model is in sub-PR #1 §5
- `Chunk` has only `text` as a typed field; everything else is in `metadata` keyed by `ChunkFilterField.*.value`.
- `ModuleMember` is fully generic — ALL fields in `metadata`.
- `SearchResponse.result: PipelineResultItem` (singular), NOT a tuple of matches.
- No `SearchMatch` class. No `FormattedPassage` class.
- Composite chunks are ordinary `Chunk`s with `metadata["origin"] == ChunkOrigin.COMPOSITE_OUTPUT.value`.

### Services hold pipelines as fields, injected at construction
- Don't build pipelines on-demand inside service methods.
- Don't pass pipelines per-call.
- MCP uses one service instance with a "formatter-included" pipeline; CLI uses another instance with a "formatter-omitted" pipeline. Same class, different construction.

### Handlers dispatch on `ChunkList` structure, not on pipeline membership
- The handler doesn't know if the pipeline had `TokenBudgetFormatterStage`. It inspects `response.result.items[0].metadata["origin"]` — if `COMPOSITE_OUTPUT`, use `.text`; else, iterate and format via the helper.
- This keeps handlers stateless about the pipeline's internal composition.

### `ModuleIntrospectionService` is NOT pipeline-backed
- The other three query services run pipelines; this one runs `importlib` + `inspect` directly.
- Still structured as a service (Protocol-typed deps = just `PackageStore`) for consistency.
- `importlib.import_module` is sync; `ModuleIntrospectionService.inspect` wraps it in `asyncio.to_thread`.

### `IndexProjectService` is orchestration, not extraction
- Extraction logic stays in `indexer.py` — adapters wrap those functions into `ChunkExtractor` / `MemberExtractor` Protocols.
- The service only coordinates: dep resolution → per-package extract → per-package reindex_package → stats.
- Sub-PR #5 will replace the adapter implementations without touching `IndexProjectService`.

### Formatting lives in `application/formatting.py`, not in pipelines
- `TokenBudgetFormatterStage` calls `format_chunks_markdown_within_budget`.
- MCP-handler fallback (when pipeline omits the formatter) also calls `format_chunks_markdown_within_budget`.
- CLI handler calls `format_chunks_cli_stdout`.
- Don't duplicate the rendering logic in each call site.

### MCP surface is byte-identical
- Tool names, parameter names, type annotations, docstrings, return-string shapes UNCHANGED.
- Handler parameters still include `internal: bool | None` tri-state, `package: str = ""`, `topic: str = ""` — same as `main`.
- Handlers translate to `pre_filter` dict internally.

### `PackageLookupService` depends on three stores
- `PackageStore` for `list_packages()` / `get()`.
- `ChunkStore` for `get_package_doc`'s chunk listing.
- `ModuleMemberStore` for `get_package_doc`'s API listing.
- All three passed via Protocol types in the constructor.

### Error propagation is uniform
- Services propagate; pipelines propagate; repositories propagate.
- The blanket catch is at the OUTERMOST boundary (MCP handler per tool; CLI subcommand top-level).
- Don't add a `try/except` inside a service "defensively."

### Test fakes are dict-backed in-memory
- `InMemoryPackageStore`, `InMemoryChunkStore`, etc. live in `tests/application/conftest.py`.
- Each is ~20–30 LOC — a dict + Protocol methods.
- Service tests inject these fakes; no SQLite fixture needed for service-level unit tests.

### End-to-end test uses a small fixture project
- `tests/fixtures/minimal_pkg/` — 2–3 Python files + 1 README.
- NOT the pydocs-mcp repo itself (too slow).
- The E2E test wires the full real stack + asserts byte-identical output.

---

## 14. Usage examples and design patterns

### Usage — MCP server startup (concrete)

```python
from pydocs_mcp.application import (
    IndexingService, IndexProjectService,
    PackageLookupService, SearchDocsService, SearchApiService,
    ModuleIntrospectionService,
)

def run(db_path):
    provider = db.build_connection_provider(db_path)
    uow = SqliteUnitOfWork(provider=provider)
    package_repo = SqlitePackageRepository(provider=provider)
    chunk_repo = SqliteChunkRepository(provider=provider)
    member_repo = SqliteModuleMemberRepository(provider=provider)
    vector_store = SqliteVectorStore(provider=provider)

    # Pipelines (MCP variant — with formatter)
    chunk_pipeline = _build_mcp_chunk_pipeline(vector_store, AppConfig.load())
    member_pipeline = _build_mcp_member_pipeline(member_repo, AppConfig.load())

    # Services
    indexing = IndexingService(package_repo, chunk_repo, member_repo, uow)
    index_project = IndexProjectService(indexing, DependencyResolverAdapter(),
                                         ChunkExtractorAdapter(), MemberExtractorAdapter())
    package_lookup = PackageLookupService(package_repo, chunk_repo, member_repo)
    search_docs = SearchDocsService(chunk_pipeline=chunk_pipeline)
    search_api = SearchApiService(member_pipeline=member_pipeline)
    module_intro = ModuleIntrospectionService(package_store=package_repo)

    # MCP handlers close over services; see §5.5
    mcp.run(...)
```

### Usage — calling a service from a test

```python
async def test_search_docs_returns_composite_when_pipeline_has_formatter():
    fake_pipeline = FakeChunkPipeline(
        response_result=ChunkList(items=(
            Chunk(
                text="## Routing\n\nUse APIRouter.",
                metadata={ChunkFilterField.ORIGIN.value: ChunkOrigin.COMPOSITE_OUTPUT.value,
                          "source_count": 3, "token_estimate": 12},
            ),
        )),
    )
    service = SearchDocsService(chunk_pipeline=fake_pipeline)
    response = await service.search(SearchQuery(terms="router"))
    assert isinstance(response.result, ChunkList)
    assert len(response.result.items) == 1
    composite = response.result.items[0]
    assert composite.metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.COMPOSITE_OUTPUT.value
    assert "Routing" in composite.text
```

### Design patterns used

| Pattern | Where | Role |
|---|---|---|
| **Application Service (DDD)** | All 5 services | Use-case coordinators; orchestrate repositories + pipelines; don't hold domain logic |
| **Ports and Adapters (Hexagonal)** | Services depend on Protocols; adapters wrap concrete implementations | Services backend-agnostic; adapters map Protocols to real infra |
| **Thin-controller / thin-handler** | `server.py` + `__main__.py` handlers | Translate + catch + render; delegate all real work to services |
| **Adapter (GoF)** | `DependencyResolverAdapter`, `ChunkExtractorAdapter`, `MemberExtractorAdapter` | Wrap existing module functions into Protocol shapes; sub-PR #5 swaps for strategy-based implementations |
| **Dependency Injection (SOLID)** | All service constructors take Protocol-typed args | Services backend-agnostic; testable with in-memory fakes |
| **Value Object (DDD)** | `PackageDoc`, `IndexingStats` | Small, purpose-specific types that group related query results |
| **Strategy (GoF)** | Pipelines passed to services (MCP variant vs CLI variant) | Same service class; different pipeline composition per surface |
| **Observer-free orchestration** | `IndexProjectService` iterates deps, accumulates stats | Synchronous accumulation, not event-driven |

### Architectural choices

- **Services for the write side too, not just reads.** `IndexProjectService` matches the query-side pattern — single entry for "index this project," Protocol-typed deps, testable in isolation. Consistent write + read service architecture.
- **Thin handlers over fat services.** Handlers do MCP/CLI-specific concerns (legacy-param translation, surface-specific error strings, result-type dispatch); everything else is a service. Testing handlers = Protocol-fake services; testing services = Protocol-fake stores/pipelines.
- **Presentation is a standalone module, not a service.** `application/formatting.py` holds pure functions used by both pipeline stages AND handler fallback paths. Single rendering logic for each output format.
- **Byte-identical MCP surface.** Services + handlers refactor the internals; observable behavior unchanged. No new tool, no new parameter, no renamed docstring. Golden-fixture test guarantees.
- **Error-propagation uniformity.** Each layer propagates; the single blanket catch sits at the outermost boundary. No layered defensiveness.

---

## 15. Follow-up sub-PRs (not in scope)

Each will get its own brainstorm + spec.

- **Sub-PR #5** — Indexer strategy split + chunking strategies. Replaces the adapter implementations of `DependencyResolver` / `ChunkExtractor` / `MemberExtractor` with strategy-based implementations. Adds `HeadingChunker`, `AstPythonChunker`, `SlidingWindowChunker`, `SentenceChunker` + `ChunkerSelector`. Zero changes to `IndexProjectService`.
- **Sub-PR #6** — Pydantic at MCP boundary. `server.py` tool handlers gain Pydantic input models. Error messages become richer. `QueryParser` component for alternative query syntaxes.
- **Sub-PR #7** — Error-tolerance primitives. `TryStage`, `RetryStage`, `CircuitBreakerStage`, `TimedStage`, `CachingStage`. Pure additions; don't modify existing stages or services.

Future work unblocked by this PR:

- **New MCP tools**: add a tool by adding a new service method + new handler + new entry in the golden-fixture test. No cross-layer churn.
- **Web API endpoint** (hypothetical): add a new `api_server.py` that holds the same services; a new set of HTTP handlers formats service responses as JSON. The services are reused.
- **Service-level metrics / tracing**: wrap each service in a `TimedService` decorator that emits metrics before delegating. Protocol-typed services make this a pure addition.
