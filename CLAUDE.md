# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Model

Always use **Claude Opus 4.7** (`claude-opus-4-7`) for all tasks in this repository.

## Project Overview

**pydocs-mcp** — A local Python documentation indexing and search MCP server with optional Rust acceleration. Indexes project source code + all installed dependencies into a searchable SQLite FTS5 database for AI coding assistants.

**Post-trilogy state (sub-PRs #5a / #5a-2 / #5b / #5c, complete):** the reference graph (CALLS / IMPORTS / INHERITS / MENTIONS edges) ships in the indexer and is queried via the existing MCP surface as `lookup(target=X, show="callers" | "callees" | "inherits")`. Capture is on by default, tunable via `reference_graph.capture.{enabled,kinds}` in YAML; output bounds via `reference_graph.output.{default_limit,max_limit}`. The MCP surface remains the fixed 2-tool `search` + `lookup` (see §"MCP API surface vs YAML configuration").

## Build & Run Commands

```bash
# Pure Python install (no Rust needed)
pip install -e .

# With Rust acceleration (requires Rust 1.70+)
pip install maturin
maturin develop --release

# Run MCP server
pydocs-mcp serve /path/to/project
pydocs-mcp serve . --no-inspect --depth 2 --workers 8

# Index only (no server)
pydocs-mcp index .
pydocs-mcp index . --force        # Clear cache and re-index
pydocs-mcp index . --skip-project # Dependencies only

# Search/debug from CLI (mirrors MCP surface: `search` + `lookup`)
pydocs-mcp search "batch inference"
pydocs-mcp search "predict" --kind api -p vllm
pydocs-mcp lookup fastapi.routing.APIRouter
pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers

# Verbose logging
pydocs-mcp -v serve .
```

No test suite or linting configuration exists yet.

```bash
# Rust checks
cargo fmt --check          # Format check
cargo clippy               # Lint Rust code
cargo test                 # Run Rust unit tests
```

## Architecture

```
python/pydocs_mcp/
├── __main__.py    # CLI entry — thin _cmd_* wrappers over ProjectIndexer + DocsSearch + ApiSearch
├── _fast.py       # Imports Rust native module or falls back to Python
├── _fallback.py   # Pure Python implementations of all Rust functions
├── db.py          # SQLite schema + cache lifecycle + FTS rebuild (no row mappers)
├── deps.py        # Dependency resolution (pyproject.toml, requirements.txt)
├── extraction/    # Strategy-based extraction (subdivided):
│   ├── strategies/  #   chunkers, members, discovery, dependencies
│   ├── pipeline/    #   IngestionPipeline, stages, PipelineChunkExtractor
│   └── model/       #   DocumentNode, NodeKind, tree helpers
├── application/   # Use-case services — IndexingService + ProjectIndexer + PackageLookup + DocsSearch + ApiSearch + ModuleInspector + shared formatting helpers
├── storage/       # Filter tree, Protocols, SQLite repositories + VectorStore + UnitOfWork
├── retrieval/     # sklearn-style RetrieverPipeline + RetrieverStep ABC; one file per step under steps/; pipeline/ holds Step/Pipeline base + RetrieverState + ConnectionProvider; YAML config
│   ├── pipeline/    #   RetrieverStep ABC, RetrieverPipeline, RetrieverState, PerCallConnectionProvider, CodeRetrieverPipeline (legacy entry-point shim)
│   └── steps/       #   One file per step: chunk_fetcher, bm25_scorer, member_fetcher, top_k_filter, metadata_post_filter, limit, token_budget, route, conditional, parallel, sub_pipeline (YAML decoder shim), rrf
├── defaults/      # Shipped default_config.yaml (lowest-priority AppConfig layer)
├── pipelines/     # Built-in pipeline YAML blueprints (chunk_search, member_search, ingestion)
└── server.py      # MCP handlers over services
src/lib.rs         # Rust acceleration: 6 PyO3 functions (walk, hash, parse, module-doc, read, read-parallel)
```

**Naming: retrieval vs ingestion pipelines** — `retrieval/` uses `RetrieverStep` (ABC) and `RetrieverPipeline`. `extraction/pipeline/` uses `IngestionStage` (Protocol) and `IngestionPipeline`. Two different pipelines, two different abstractions; don't confuse them. A future PR may rename `IngestionStage` → `IngestionStep` for symmetry.

**Data flow:** CLI / MCP server → services (`PackageLookup` / `DocsSearch` / `ApiSearch` / `ModuleInspector` for queries; `ProjectIndexer.index_project` for writes) → `application.IndexingService` writes through `storage.SqlitePackageRepository` / `SqliteChunkRepository` / `SqliteModuleMemberRepository` under a `SqliteUnitOfWork` → `extraction.PipelineChunkExtractor` + `AstMemberExtractor` / `InspectMemberExtractor` feed write-side extraction; retrieval/ runs an async `RetrieverPipeline` whose steps fetch chunks (BM25 via FTS5 + pre-filter pushdown) or members (LIKE) → score → filter → top-K → render → server.py / __main__.py via `application/formatting` helpers → client.

**Rust/Python duality:** `_fast.py` tries `from ._native import *`; on ImportError falls back to `_fallback.py`. All Rust functions have pure Python equivalents — the package works without Rust compiled.

**Two indexing modes:**
- **Inspect mode** (default): Imports modules via Python's `inspect`, gets full type hints and signatures. Riskier (side effects from imports).
- **Static mode** (`--no-inspect`): Reads `.py` files from site-packages with regex parsing. Faster, safer, no side effects.

**Cache:** Per-project SQLite at `~/.pydocs-mcp/{dirname}_{path_hash}.db`. Hash-based invalidation — subsequent runs with no changes skip indexing (<100ms).

## Key Technical Details

- Python 3.11+ required, single runtime dependency: `mcp>=1.0`
- `pydantic-settings>=2.0` and `pyyaml>=6.0` runtime deps (YAML-driven pipeline config, added sub-PR #2)
- `retrieval/` uses a uniform `RetrieverStep` ABC + composable `RetrieverPipeline` (Pipeline IS a Step, so sub-pipelines compose directly without a SubPipelineStep adapter — named, addressable steps a la sklearn's `Pipeline([(name, step), ...])`)
- Build system: maturin (PEP 517) bridges Python packaging with Rust cdylib
- Rust module name: `pydocs_mcp._native` (configured in pyproject.toml `tool.maturin`)
- Entry point: `pydocs-mcp = "pydocs_mcp.__main__:main"`
- DB has three main tables: `packages`, `chunks` (with FTS5 virtual table), `module_members`
- FTS5 uses porter stemming + unicode61 tokenizer
- The project code itself is indexed under the special package name `__project__`

## Rust Guidelines (src/lib.rs)

**PyO3 boundary pattern:** Keep the Rust/Python boundary thin. Do type conversion at the boundary, keep core logic in pure Rust. Minimize round-trips between Python and Rust types. Map Rust errors to Python exceptions via `PyResult`.

**Coding standards:**
- Run `cargo fmt` and `cargo clippy` before committing Rust changes
- Prefer `&[T]` / `&str` over owned types in function parameters
- Use `?` operator for error propagation, avoid `unwrap()` in production
- Keep functions small and focused on a single responsibility
- Use iterators over explicit loops for better performance and readability

**Ownership and borrowing:**
- Prefer borrowing (`&`) over cloning for performance
- Keep mutable borrow scopes as small as possible
- Use `Arc<Mutex<T>>` sparingly — only when truly needed for shared ownership

**Concurrency (rayon):** The project uses rayon for parallel file reading. Avoid blocking operations in parallel iterators. Use `par_iter()` for CPU-bound batch work.

**ABI3:** Consider enabling `pyo3/abi3-py311` feature in Cargo.toml to produce a single wheel per platform that works across Python 3.11+.

## Packaging & Distribution

**Maturin mixed layout:** Python sources live in `python/` directory, Rust in `src/`. Maturin places the compiled `.so`/`.pyd` alongside the Python files. The `module-name` in `[tool.maturin]` controls where the native module lands.

**Fallback contract:** Every Rust function in `src/lib.rs` must have a matching pure Python implementation in `_fallback.py` with the same signature and behavior. The Python version serves as the reference implementation and test oracle.

**Building wheels for distribution:**
- Publish both wheels (per-platform) and sdist — users without Rust get the pure Python fallback from sdist
- For Linux: build inside manylinux2014+ container or use `maturin build --zig` for cross-compilation
- Use `PyO3/maturin-action` in GitHub Actions for CI wheel building across platforms

## SOLID Principles

**Single Responsibility:** Each module has one concern — `db.py` owns the schema, `storage/` owns persistence (repositories, filter adapter, UoW, VectorStore), `application/` owns both write-side (`IndexingService`, `ProjectIndexer`) AND read-side (`PackageLookup`, `DocsSearch`, `ApiSearch`, `ModuleInspector`) use-case services, `retrieval/` owns the pipeline machinery (`RetrieverStep` ABC + `RetrieverPipeline` + concrete steps under `steps/`), `extraction/` owns the write-side ingestion pipeline, chunkers, member extractors, and DocumentNode trees. `application/formatting.py` is the single source of truth for rendering — stages, MCP handlers, and CLI all delegate to it. New features should follow this pattern. If a module gains a second reason to change, split it.

**Open/Closed:** Extend behavior through new `kind` values in chunks/module_members tables rather than modifying existing indexing logic. New search strategies should be added as new retrievers/stages registered in `retrieval/`, not by modifying existing ones.

**Liskov Substitution:** The Rust/Python fallback is a substitution boundary — `_fallback.py` functions must be drop-in replacements for `_native` functions. Same inputs must produce same outputs. Never strengthen preconditions or weaken postconditions in either implementation.

**Interface Segregation:** MCP tools in `server.py` each expose a focused interface. Keep tool parameters minimal and client-specific. Don't add parameters "just in case."

**Dependency Inversion:** Core logic (`extraction/`, `retrieval/`, `application/`) depends on abstractions — `_fast.py` hides the Rust/Python choice and `storage/protocols.py` + `application/protocols.py` + `retrieval/protocols.py` define the backend contracts. Services depend only on Protocols from those three modules, never on concrete `Sqlite*` types. `ProjectIndexer` composes with the strategy classes from `extraction/` (`PipelineChunkExtractor` / `AstMemberExtractor` / `InspectMemberExtractor` / `StaticDependencyResolver`) — the service knows only the Protocol shape, so swapping chunkers, member extractors, or the resolver is a pure strategy change. Swapping SQLite for Postgres/DuckDB later is a pure adapter change. Never import `_native` or `_fallback` directly from other modules.

## Creating new application services

**Rule:** New `application/` services that touch any persisted entity (`Package`, `Chunk`, `ModuleMember`, `DocumentNode`, `Reference`, …) MUST depend on a single `uow_factory: Callable[[], UnitOfWork]` constructor parameter. **Do NOT take individual `PackageStore` / `ChunkStore` / `ModuleMemberStore` / `DocumentTreeStore` / `ReferenceStore` references**, do NOT take a `unit_of_work: UnitOfWork | None` field, and do NOT reach into `*Repository` types directly.

**Pattern** (mandatory for every new service):

```python
from collections.abc import Callable
from dataclasses import dataclass
from pydocs_mcp.storage.protocols import UnitOfWork

@dataclass(frozen=True, slots=True)
class MyNewService:
    uow_factory: Callable[[], UnitOfWork]

    # READ path — no commit needed.
    async def get_something(self, name: str) -> SomeResult:
        async with self.uow_factory() as uow:
            packages = await uow.packages.list(filter={"name": name})
            chunks = await uow.chunks.list(filter={...}, limit=10)
        return _render(packages, chunks)

    # WRITE path — commit explicitly. The whole sequence is atomic.
    async def replace_something(self, package: Package, chunks: tuple[Chunk, ...]) -> None:
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter={"package": package.name})
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            await uow.commit()  # success signal — flushes the SQLite transaction
```

**Atomicity model** (the boundary that makes writes safe):

- `async with self.uow_factory() as uow:` opens a single SQLite connection and stores it on the `_sqlite_transaction` ContextVar. Every `uow.<repo>.<method>()` call inside the block routes through that one connection (no per-call connection acquisition).
- SQLite's implicit transaction wraps every statement on that connection until you call `commit()` or `rollback()`. So the whole `delete → delete → upsert → upsert` sequence above either fully lands or fully rolls back — no partial writes survive a crash mid-sequence.
- `await uow.commit()` is the explicit success signal. Calling it flushes the transaction (`SqliteUnitOfWork.commit` → `await asyncio.to_thread(self._held_conn.commit)`).
- `__aexit__` is the safety net: if an exception escapes the `async with` body OR if the body completes without ever calling `commit()`, it calls `rollback()` automatically. Silently failing to commit cannot accidentally persist half a transaction.

Other rules:

- One UoW per public method call. Short transactions = low contention.
- Inside `async with self.uow_factory() as uow:` you reach every repository via `uow.packages` / `uow.chunks` / `uow.module_members` / `uow.trees` / `uow.references`. The `UnitOfWork` Protocol guarantees they're present — no `if uow.X is not None:` guards.
- Reads are wrapped too — even if a method touches only `uow.packages`, going through the UoW keeps the connection-acquisition path uniform and lets a single transaction span multiple reads with consistent isolation. Reads do **not** need `await uow.commit()`; the `__aexit__` safety-net rollback is a no-op for read-only paths.
- **The only exception:** retrieval/search pipelines that consume `SqliteVectorStore` directly via `ConnectionProvider` (`DocsSearch`, `ApiSearch`). They are query-only against a single FTS table and don't need cross-store consistency. Anything that touches more than one store, or any write path, uses `uow_factory`.

**Tests** use `make_fake_uow_factory(packages=..., chunks=..., module_members=..., trees=..., references=...)` from `tests/_fakes.py`. Never construct services with direct `package_store=` / `chunk_store=` / `module_member_store=` / `tree_store=` / `unit_of_work=` kwargs in tests — those constructor shapes are obsolete and the helper enforces the new contract.

**Composition roots** (`server.py`, `__main__.py`, `storage/factories.py`) build a `uow_factory = lambda: SqliteUnitOfWork(provider=provider, lock=lock, ...)` closure once and thread it to every service. No inline `SqliteDocumentTreeStore(...)` / `SqliteChunkRepository(...)` constructors in service-wiring code.

## MCP API surface vs YAML configuration

**Rule:** Pipeline / feature / behavior settings — capture toggles, resolver thresholds, retrieval limits-on-defaults, ranking weights, embedding model choices, indexing depth, kinds-to-emit, reference-graph capture on/off, etc. — MUST be configured via YAML (loaded through `AppConfig.load(...)` at server / CLI startup), NEVER exposed as new MCP tool parameters.

**The MCP tool surface is FIXED at two tools:** `search(query, kind, ...)` and `lookup(target, show, ...)`. Their signatures are pinned by sub-PRs #4 / #6. New features land **behind** the existing surface — via YAML config + internal service composition — never as a new MCP param.

**Why:**

1. **MCP stability.** The MCP client surface is consumed by external tools (Claude Code, Cursor, IDE extensions). Every new MCP param is a versioning event for those clients. YAML edits are deployment changes — they don't ripple to clients.
2. **Experiment tracking + benchmark evaluation.** A/B testing different resolver thresholds, capture strategies, or ranking weights happens server-side via swappable YAML. The benchmark harness can iterate over `configs/*.yaml` and produce comparable measurements **without rebuilding clients or churning the API**. Conflating "what the API offers" with "how the server is currently tuned" makes evaluation impossible.
3. **Per-deployment tuning.** Different MCP server deployments (per-project, per-developer, per-environment) carry different configs. The API stays the same; the behavior varies.

**The one allowed exception** is *input-shape* validators on the MCP tool models (e.g., `LookupInput.limit: int = Field(50, ge=1, le=1000)`) — these constrain a single client request's bounds and are client-driven, not feature toggles. If you find yourself adding a parameter like `lookup(kinds=[...])` or `search(min_score=0.5)`, **stop**: that's a pipeline setting, it belongs in YAML, and the MCP input should expose nothing.

**Where YAML config lives:**

- `python/pydocs_mcp/defaults/default_config.yaml` — shipped lowest-priority defaults, the canonical reference of every tunable.
- `python/pydocs_mcp/pipelines/*.yaml` — built-in pipeline blueprints (ingestion, chunk search, member search).
- User overlays loaded via `AppConfig.load(explicit_path=...)` at MCP server / CLI startup.

**`AppConfig` is the single source of truth** — it's a `pydantic-settings` model that layers (1) defaults → (2) shipped pipeline YAML → (3) explicit overlay path → (4) env vars. New tunables go in the typed config sub-model that owns the relevant pipeline; never as a CLI flag *and* an MCP param.

**When in doubt:** if a new behavior could be A/B tested against a benchmark to measure quality, it belongs in YAML.

## Default values: single source of truth

**Rule:** Every default value lives in exactly ONE place. Code that needs to reference that default MUST refer to the canonical source — NEVER repeat the literal.

Acceptable canonical sources, in order of preference:

1. **Module-level `_DEFAULT_X = value` constant** — best for dataclass field defaults that also appear in `to_dict` comparisons or `from_dict` fallbacks. Matches the existing `_MAX_PIPELINE_DEPTH = 32` precedent in `retrieval/pipeline/code_pipeline.py`.
2. **pydantic `Field(default=N)`** in `AppConfig` sub-models — the canonical source for YAML-tunable settings. Application code reads these via `AppConfig.load(...)`, never re-encoding the literal elsewhere.
3. **Dataclass field default** — fine when the default is referenced only at construction (no `to_dict` / `from_dict` round-trip).

**Anti-pattern** (do NOT do this):

```python
@dataclass(frozen=True, slots=True)
class RRFStep(RetrieverStep):
    k: int = 60                                          # canonical

    def to_dict(self) -> dict:
        if self.k != 60:                                 # ← BAD: literal repeated
            ...

    @classmethod
    def from_dict(cls, data, context):
        return cls(k=data.get("k", 60))                  # ← BAD: literal repeated
```

**Correct pattern:**

```python
_DEFAULT_K = 60                                          # single source of truth

@dataclass(frozen=True, slots=True)
class RRFStep(RetrieverStep):
    k: int = _DEFAULT_K

    def to_dict(self) -> dict:
        if self.k != _DEFAULT_K:
            ...

    @classmethod
    def from_dict(cls, data, context):
        return cls(k=data.get("k", _DEFAULT_K))
```

**Why:**

1. **One place to change.** Bumping the default touches the `_DEFAULT_K` line, not three sites that drift.
2. **No silent regressions.** A diff that changes the constant is obvious in code review; a diff that changes one of three hardcoded `60`s is easy to miss.
3. **YAML / Python parity.** When the same default appears in YAML, it's intentional duplication for config-file clarity (the YAML is the user-visible knob). Inside Python, duplicating a literal is just churn.

**YAML files are exempt** — `pipelines/*.yaml` and `default_config.yaml` explicitly state values for user-facing clarity, even when the Python field default would be the same.

## Code Comments

- **Explain WHY, not WHAT** — the code should be self-documenting for the "what"
- Use `# WORKAROUND:` for temporary fixes with context on when to remove
- Use `# Performance:` to explain non-obvious optimizations (e.g., why batch inserts, why FTS rebuild is deferred)
- Use `# TODO:` with context for planned work
- Write Python docstrings (`"""..."""`) for public functions — especially MCP tool handlers in `server.py` since these become user-facing tool descriptions
- Write Rust doc comments (`///`) for `#[pyfunction]` exports since they document the API contract with Python
- Don't comment obvious code; don't leave commented-out code without a reason

## Async Patterns

The MCP server (`server.py`) uses FastMCP which is async. Follow these patterns:

- Use `async def` for all MCP tool handlers and server lifecycle functions
- Use `await` consistently — never call async functions without awaiting
- Use `asyncio.to_thread()` to offload blocking operations (SQLite queries, file I/O, indexing) from the async event loop
- Never use `time.sleep()` in async context — use `asyncio.sleep()` instead
- Keep async functions focused: do the I/O, return the result. Put business logic in sync helpers
- For concurrent indexing tasks, prefer `asyncio.gather()` over sequential awaits
- Handle timeouts with `asyncio.wait_for()` for operations that could hang (e.g., inspect-mode imports)
- In Rust: the PyO3 functions are sync and CPU-bound — they should be called via `asyncio.to_thread()` from async Python code to avoid blocking the event loop
