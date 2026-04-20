# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Model

Always use **Claude Opus 4.7** (`claude-opus-4-7`) for all tasks in this repository.

## Project Overview

**pydocs-mcp** — A local Python documentation indexing and search MCP server with optional Rust acceleration. Indexes project source code + all installed dependencies into a searchable SQLite FTS5 database for AI coding assistants.

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

# Search/debug from CLI
pydocs-mcp query "batch inference"
pydocs-mcp api predict -p vllm

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
├── __main__.py    # CLI entry — thin _cmd_* wrappers over IndexProjectService + SearchDocsService + SearchApiService
├── _fast.py       # Imports Rust native module or falls back to Python
├── _fallback.py   # Pure Python implementations of all Rust functions
├── db.py          # SQLite schema + cache lifecycle + FTS rebuild (no row mappers)
├── deps.py        # Dependency resolution (pyproject.toml, requirements.txt)
├── indexer.py     # Extraction only (async extract_* fns); bootstrap orchestration lives in IndexProjectService
├── application/   # Use-case services — IndexingService + IndexProjectService + PackageLookupService + SearchDocsService + SearchApiService + ModuleIntrospectionService + shared formatting helpers
├── storage/       # Filter tree, Protocols, SQLite repositories + VectorStore + UnitOfWork
├── retrieval/     # Async pipelines, retrievers, stages, registries, YAML config
├── presets/       # Built-in pipeline YAML presets (chunk_fts, member_like)
└── server.py      # 5 thin MCP handlers (≤25 LOC each) over services
src/lib.rs         # Rust acceleration: 7 PyO3 functions (walk, hash, chunk, parse, read)
```

**Data flow:** CLI / MCP server → services (`PackageLookupService` / `SearchDocsService` / `SearchApiService` / `ModuleIntrospectionService` for queries; `IndexProjectService.index_project` for writes) → `application.IndexingService` writes through `storage.SqlitePackageRepository` / `SqliteChunkRepository` / `SqliteModuleMemberRepository` under a `SqliteUnitOfWork` → retrieval/ runs the async `CodeRetrieverPipeline` (BM25 chunks via `SqliteVectorStore` + LIKE module-members via `SqliteModuleMemberRepository`) → server.py / __main__.py render via `application/formatting` helpers → client.

**Rust/Python duality:** `_fast.py` tries `from ._native import *`; on ImportError falls back to `_fallback.py`. All Rust functions have pure Python equivalents — the package works without Rust compiled.

**Two indexing modes:**
- **Inspect mode** (default): Imports modules via Python's `inspect`, gets full type hints and signatures. Riskier (side effects from imports).
- **Static mode** (`--no-inspect`): Reads `.py` files from site-packages with regex parsing. Faster, safer, no side effects.

**Cache:** Per-project SQLite at `~/.pydocs-mcp/{dirname}_{path_hash}.db`. Hash-based invalidation — subsequent runs with no changes skip indexing (<100ms).

## Key Technical Details

- Python 3.11+ required, single runtime dependency: `mcp>=1.0`
- `pydantic-settings>=2.0` and `pyyaml>=6.0` runtime deps (YAML-driven pipeline config, added sub-PR #2)
- `retrieval/` uses a uniform `PipelineStage` protocol + compound stages (`RouteStage`, `SubPipelineStage`) for composition
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

**Single Responsibility:** Each module has one concern — `db.py` owns the schema, `storage/` owns persistence (repositories, filter adapter, UoW, VectorStore), `application/` owns both write-side (`IndexingService`, `IndexProjectService`) AND read-side (`PackageLookupService`, `SearchDocsService`, `SearchApiService`, `ModuleIntrospectionService`) use-case services, `retrieval/` owns the pipeline machinery (retrievers, stages, pipelines), `indexer.py` owns extraction. `application/formatting.py` is the single source of truth for rendering — stages, MCP handlers, and CLI all delegate to it. New features should follow this pattern. If a module gains a second reason to change, split it.

**Open/Closed:** Extend behavior through new `kind` values in chunks/module_members tables rather than modifying existing indexing logic. New search strategies should be added as new retrievers/stages registered in `retrieval/`, not by modifying existing ones.

**Liskov Substitution:** The Rust/Python fallback is a substitution boundary — `_fallback.py` functions must be drop-in replacements for `_native` functions. Same inputs must produce same outputs. Never strengthen preconditions or weaken postconditions in either implementation.

**Interface Segregation:** MCP tools in `server.py` each expose a focused interface. Keep tool parameters minimal and client-specific. Don't add parameters "just in case."

**Dependency Inversion:** Core logic (`indexer.py`, `retrieval/`, `application/`) depends on abstractions — `_fast.py` hides the Rust/Python choice and `storage/protocols.py` + `application/protocols.py` + `retrieval/protocols.py` define the backend contracts. Services depend only on Protocols from those three modules, never on concrete `Sqlite*` types. Adapters in `application/index_project_service.py` (`ChunkExtractorAdapter` / `MemberExtractorAdapter` / `DependencyResolverAdapter`) wrap today's `indexer.py` extraction; sub-PR #5 can replace them with strategy-based implementations without touching the services. Swapping SQLite for Postgres/DuckDB later is a pure adapter change. Never import `_native` or `_fallback` directly from other modules.

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
