# pydocs-mcp — Project Specification

> A local Python documentation MCP server. Offline, no Node.js, indexes your project + installed dependencies automatically.

## What it is

An MCP server that indexes your Python project source code and installed dependencies into a local SQLite database with full-text search and a reference graph (CALLS / IMPORTS / INHERITS edges). Your AI coding assistant gets accurate, version-specific documentation, API signatures, and call/inheritance context without network access.

## Problem

AI coding assistants hallucinate outdated APIs because their training data is months behind. Existing solutions (Context7, Docfork) require Node.js, cloud access, or manual per-library setup. None of them index your own project code alongside dependencies, and none of them expose a reference graph for "who calls this function" queries.

## Solution

Point pydocs-mcp at your project directory. It reads `pyproject.toml` (or `requirements.txt`), finds every installed dependency, and indexes everything into a single SQLite file with FTS5 full-text search plus a reference-graph table. Your project code, dependency docs, and the cross-cutting call graph become queryable through one unified MCP interface — two tools, fixed surface.

## Key differentiators

1. **Zero config** — pass a directory, it discovers everything automatically.
2. **Project + deps together** — search your code AND dependency docs in one query.
3. **Reference graph** — `lookup(target=X, show="callers")` traverses CALLS/IMPORTS/INHERITS edges captured at index time.
4. **Fully offline** — no network, no API keys, no Node.js.
5. **Smart cache** — hash-based skip on re-runs, per-project `.db` isolation.
6. **Dual indexing mode** — `inspect` (richer, default) or `--no-inspect` / static (faster, no side effects).
7. **Rust-accelerated** — optional native extension for ~4× I/O speedup.
8. **Portable** — copy the `.db` file to another machine, it just works.
9. **Stable MCP surface** — 2 tools (`search`, `lookup`); all tuning happens via YAML config so clients don't version-bump on server-side changes.
10. **sklearn-style pipeline composition** — every retrieval step is a `RetrieverStep` subclass; pipelines are named, addressable, swappable.

## Architecture

```
pydocs-mcp/
├── Cargo.toml                  # Rust dependencies
├── pyproject.toml              # Python package config (maturin mixed layout)
├── src/
│   └── lib.rs                  # Rust: walker, hasher, chunker, parser (6 PyO3 functions)
└── python/
    └── pydocs_mcp/
        ├── __init__.py         # Package version
        ├── __main__.py         # CLI entry (serve / index / search / lookup)
        ├── _fast.py            # Try Rust, fall back to Python (substitution boundary)
        ├── _fallback.py        # Pure Python equivalents of all Rust functions
        ├── db.py               # SQLite schema (v4) + cache lifecycle + FTS rebuild
        ├── deps.py             # Dependency resolution (pyproject.toml, requirements.txt)
        ├── models.py           # Domain dataclasses (Chunk, ModuleMember, Package, …)
        ├── extraction/         # Write-side: chunkers, member extractors, ingestion pipeline
        │   ├── strategies/     #   chunkers, members, discovery, dependencies
        │   ├── pipeline/       #   IngestionPipeline, IngestionStage, PipelineChunkExtractor
        │   └── model/          #   DocumentNode, NodeKind, tree helpers
        ├── application/        # Use-case services
        │   #   IndexingService, ProjectIndexer (writes)
        │   #   DocsSearch, ApiSearch, PackageLookup, ModuleInspector (reads)
        │   #   formatting.py (single source of truth for rendering)
        ├── storage/            # Persistence — Protocols + Sqlite* concrete adapters
        │   #   SqlitePackageRepository / SqliteChunkRepository / …
        │   #   UnitOfWork, ConnectionProvider, Filter tree + FilterAdapter
        ├── retrieval/          # Read-side pipeline machinery (sklearn-style)
        │   ├── pipeline/       #   RetrieverStep ABC, RetrieverPipeline, RetrieverState, ConnectionProvider
        │   └── steps/          #   One file per step (chunk_fetcher, bm25_scorer, top_k_filter, …)
        ├── defaults/           # Shipped default_config.yaml (lowest-priority AppConfig layer)
        ├── pipelines/          # Built-in pipeline YAML blueprints (chunk_search, member_search, ingestion)
        └── server.py           # MCP server (2 tools: search + lookup)
```

For the detailed architecture rules (SOLID, async patterns, MCP API surface vs YAML configuration, single-source-of-truth defaults), see [CLAUDE.md](CLAUDE.md). For the extensibility surface (how to add new storage backends, filter formats, retrieval steps, formatters), see [EXTENSIONS.md](EXTENSIONS.md).

## Indexing pipeline

The ingestion pipeline is composed of named stages (`IngestionStage` Protocol) and is itself YAML-configurable via `python/pydocs_mcp/pipelines/ingestion.yaml`.

### Step 1 — Dependency resolution

Read dependencies from the project directory. Priority order:

1. `pyproject.toml` → `[project] dependencies`
2. `requirements.txt`
3. `requirements/base.txt` or `requirements/prod.txt`

Normalize package names: lowercase, replace hyphens with underscores, strip version specifiers.

### Step 2 — Project source indexing

1. Walk all `.py` and `.md` files using Rust `walkdir` (or Python `os.walk`), skipping `.venv`, `__pycache__`, `node_modules`, `build`, `dist`, etc.
2. Hash file paths + modification times with xxh3 (or md5 fallback).
3. Compare against stored hash. Skip entirely if unchanged.
4. For each `.py` file: extract docstrings + chunks (AST or regex), member definitions (functions/classes/methods), and references (CALLS / IMPORTS / INHERITS edges).
5. For each `.md` file: chunk at heading boundaries.
6. Batch insert chunks, module members, and references; capture into a `DocumentNode` tree for `lookup(..., show="tree")` queries.

### Step 3 — Dependency indexing

Two modes, selectable at runtime:

**Inspect mode** (default):
1. Import each module via `importlib.import_module`.
2. Use `inspect.getmembers` to extract functions, classes, signatures, type hints, default values, and docstrings (including inherited ones).
3. Recurse into submodules up to `--depth` levels.
4. Runs in parallel threads; CPU-bound `inspect` work is GIL-limited but still fast.

**Static mode** (`--no-inspect`):
1. Find all `.py` files installed by each distribution via `dist.files`.
2. Read and parse them with the same AST/regex-based parser used for the project.
3. Fully parallelizable (Rayon in Rust, threads in Python).
4. No side effects, no imports, works without an activated virtualenv.

Both modes also collect:
- Package metadata (version, summary, homepage, dependencies) from `importlib.metadata`.
- Long description / README from the package metadata payload.
- Doc files (`.md`, `.rst`, `.txt`) shipped in site-packages.

Each dependency is hashed as `name:version`. Unchanged packages are skipped.

### Step 4 — Reference resolution

After capture, the `ReferenceResolver` walks every `node_references` row whose `to_node_id` is `NULL` and tries to resolve it against the qname universe of the indexed corpus + the bundled stdlib qnames + per-package aliases. Resolution uses 5 rules (A: explicit alias, B: exact qname match, C: strict suffix within `from_package`, D: ambiguous-suffix → None, E: no match), plus the F20 fast-path and the self-attribute short-circuit.

### Step 5 — FTS5 index rebuild

After all inserts, rebuild the FTS5 index once for optimal query performance.

## Database schema (v4)

```sql
-- Package metadata
CREATE TABLE packages (
    name TEXT PRIMARY KEY,
    version TEXT,
    summary TEXT,
    homepage TEXT,
    dependencies TEXT,        -- JSON array of dependency names
    content_hash TEXT,        -- Cache hash for skip detection
    origin TEXT,              -- 'pypi' | '__project__' | ...
    local_path TEXT           -- For project source / editable installs
);

-- Text chunks (docs, READMEs, source code, docstrings)
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    package TEXT,             -- Package name or '__project__'
    module TEXT DEFAULT '',   -- Dotted module path
    title TEXT,               -- Section heading or symbol name
    text TEXT,                -- Chunk content
    origin TEXT,              -- 'readme', 'doc', 'docstring', 'source', …
    content_hash TEXT
);

-- FTS5 virtual table for BM25-ranked search
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    title, text, package,
    content=chunks, content_rowid=id,
    tokenize='porter unicode61'
);

-- API members (functions, classes, methods)
CREATE TABLE module_members (
    id INTEGER PRIMARY KEY,
    package TEXT,
    module TEXT,              -- Dotted module path
    name TEXT,                -- Symbol short name
    kind TEXT,                -- 'function', 'class', 'method', ...
    signature TEXT,           -- Parameter list
    return_annotation TEXT,
    parameters TEXT,          -- JSON array of {name, annotation, default}
    docstring TEXT
);

-- DocumentNode tree for lookup(..., show="tree")
CREATE TABLE document_trees (
    package TEXT NOT NULL,
    module TEXT NOT NULL,
    tree_json TEXT NOT NULL,
    content_hash TEXT,
    updated_at REAL,
    PRIMARY KEY (package, module)
);

-- Reference graph (CALLS / IMPORTS / INHERITS / MENTIONS edges)
CREATE TABLE node_references (
    from_package TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_name      TEXT NOT NULL,
    to_node_id   TEXT,
    kind         TEXT NOT NULL,    -- 'calls' | 'imports' | 'inherits' | 'mentions'
    PRIMARY KEY (from_package, from_node_id, to_name, kind)
);

-- Indexes
CREATE INDEX ix_chunks_package         ON chunks(package);
CREATE INDEX ix_chunks_module          ON chunks(module);
CREATE INDEX ix_module_members_package ON module_members(package);
CREATE INDEX ix_module_members_name    ON module_members(name);
CREATE INDEX idx_trees_package         ON document_trees(package);
CREATE INDEX ix_refs_from              ON node_references(from_package, from_node_id);
CREATE INDEX ix_refs_to_name           ON node_references(to_name);
CREATE INDEX ix_refs_to_node           ON node_references(to_node_id);
```

The schema is versioned via `PRAGMA user_version`. Opening a DB whose version doesn't match drops all tables and re-indexes from scratch; idempotent additive migrations (v2→v3, v3→v4) run when possible.

## Cache strategy

Each project gets its own database file:

```
~/.pydocs-mcp/
├── my-rasa-bot_a3f2c1b0e9.db
├── llm-optimizer_7d4e8f1a23.db
└── another-proj_c9b2d4e6f1.db
```

The filename is `{directory_name}_{md5(absolute_path)[:10]}.db`.

Cache invalidation:

| Component | Hash input | Re-indexes when... |
|---|---|---|
| `__project__` | .py file paths + mtimes | Any file created, modified, or deleted |
| Dependencies | `name:version` | Package version changed (pip upgrade) |

Second run with no changes completes in <100ms (hash comparison only).

## Rust native extension

Optional. The package works without Rust — `_fast.py` tries to import `_native` and falls back to `_fallback.py`.

### Functions exposed to Python

| Function | Purpose | Speedup |
|---|---|---|
| `walk_py_files(root)` | Find .py files, skip excluded dirs | ~10× |
| `hash_files(paths)` | xxh3 hash of paths + mtimes | ~3× |
| `chunk_text(text, max_chars)` | Split at heading boundaries | ~10× |
| `parse_py_file(source)` | Extract functions/classes via regex | ~5× |
| `extract_module_doc(source)` | Get module-level docstring | ~5× |
| `read_file(path)` | Read one file | ~2× |
| `read_files_parallel(paths)` | Read multiple files with Rayon | ~8× |

### Dependencies

- `pyo3` — Python ↔ Rust bindings
- `walkdir` — fast recursive directory walker
- `xxhash-rust` — xxh3 hashing (faster than md5)
- `regex` — compiled regex engine
- `rayon` — data-parallel file reading

### Build

```bash
pip install maturin
maturin develop --release    # Development
maturin build --release      # Wheel for distribution
```

## MCP surface — 2 tools

The MCP surface is intentionally minimal. Every behavioral knob lives in YAML, not in tool parameters (see CLAUDE.md §"MCP API surface vs YAML configuration").

### `search(query, kind, package, scope, limit)`

BM25-ranked full-text search across indexed chunks (project + deps).

- `query` — search terms
- `kind` ∈ `{"docs", "api", "any"}` — narrow to documentation, API surface, or both
- `package` — optional package filter; `"__project__"` searches your code only
- `scope` ∈ `{"project", "deps", "all"}` — corpus scope
- `limit` — max results (default `10`, validated 1..1000)

Returns a token-budgeted composite chunk by default. A `chunk_search_ranked.yaml` preset (out-of-scope for this spec; planned follow-up) returns top-K separate items for benchmarking.

### `lookup(target, show)`

Navigate the indexed corpus by qualified name.

- `target` — dotted path like `"fastapi.routing.APIRouter"`. Empty target lists indexed packages.
- `show` ∈ `{"default", "tree", "callers", "callees", "inherits"}` — what to render.

`show="tree"` returns the `DocumentNode` tree (table of contents). `show="callers"` / `"callees"` / `"inherits"` traverse the reference graph (CALLS / INHERITS edges).

## CLI

```bash
# Index project + deps, start MCP server
pydocs-mcp serve /path/to/project
pydocs-mcp serve .

# Static mode (no imports, faster, safer)
pydocs-mcp serve . --no-inspect

# More options
pydocs-mcp serve . --depth 2 --workers 8 --no-inspect --config ./my-pydocs.yaml

# Index only, no server
pydocs-mcp index .
pydocs-mcp index . --force              # clear cache, re-index all
pydocs-mcp index . --skip-project       # only index deps

# Search from CLI (mirrors the MCP `search` tool)
pydocs-mcp search "batch inference"
pydocs-mcp search "middleware" -p fastapi
pydocs-mcp search "handle request" -p __project__
pydocs-mcp search "predict" --kind api -p vllm

# Lookup from CLI (mirrors the MCP `lookup` tool)
pydocs-mcp lookup                                            # list packages
pydocs-mcp lookup fastapi.routing.APIRouter                  # class overview
pydocs-mcp lookup fastapi.routing.APIRouter --show tree
pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers
pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits

# Verbose logging
pydocs-mcp -v serve .
```

## Integration

### Claude Code

```json
{
  "mcpServers": {
    "pydocs": {
      "command": "pydocs-mcp",
      "args": ["serve", "/path/to/your/project"]
    }
  }
}
```

Place at `~/.config/claude-code/mcp_servers.json` or workspace `.claude/mcp_servers.json`.

### Cursor

```yaml
mcpServers:
  - name: pydocs
    command: pydocs-mcp
    args: ["serve", "/path/to/your/project"]
```

Place at `~/.cursor/mcp.json` or `.cursor/mcp.json`.

### Continue.dev

```json
{
  "mcpServers": [
    {
      "name": "pydocs",
      "command": "pydocs-mcp",
      "args": ["serve", "/path/to/your/project"]
    }
  ]
}
```

Place at `~/.continue/config.json`.

### Claude Desktop

```json
{
  "mcpServers": {
    "pydocs": {
      "command": "pydocs-mcp",
      "args": ["serve", "/path/to/your/project"]
    }
  }
}
```

### VS Code (`mcp.json`)

```json
{
  "servers": {
    "pydocs": {
      "command": "pydocs-mcp",
      "args": ["serve", "${workspaceFolder}"]
    }
  }
}
```

### SSE transport (via `mcp-proxy`)

```bash
pip install mcp-proxy
mcp-proxy --port 8080 -- pydocs-mcp serve /path/to/project
```

Then connect via `http://localhost:8080/sse`.

## Comparison

### vs Context7

| | pydocs-mcp | Context7 |
|---|---|---|
| Network | never | always (cloud) |
| Node.js | no | yes |
| Indexes your project | yes | no |
| Auto-discovers deps | yes (pyproject.toml) | no |
| Reference graph (callers/callees/inherits) | yes | no |
| Docstrings + signatures | yes | no (docs only) |
| Type hints | yes | no |
| Cost | free | 1000 req/month free, $10/month |
| Fully offline | yes | no |

### vs Neuledge

| | pydocs-mcp | Neuledge |
|---|---|---|
| Node.js required | no | yes |
| Indexes your project | yes | manual |
| Auto-discovers deps | yes | manual per library |
| Python-specific features | yes (inspect, types, reference graph) | no (generic docs) |
| Deps from site-packages | yes | no |

### vs filesystem-operations-mcp

| | pydocs-mcp | filesystem-operations-mcp |
|---|---|---|
| Search method | FTS5 BM25 + reference graph | ripgrep text match |
| Understands Python | yes (functions, classes, callers/callees/inherits) | no (plain text) |
| Installed deps | yes | no (local files only) |
| Smart cache | yes (hash-based, per-project SQLite) | no |

## Performance

### Indexing (typical project with ~30 deps)

| Phase | Python fallback | With Rust |
|---|---|---|
| File walk (1000 .py) | ~200ms | ~20ms |
| File hashing | ~150ms | ~30ms |
| Text chunking | ~100ms | ~10ms |
| Source parsing | ~300ms | ~50ms |
| `inspect.getmembers` | ~1.5s | ~1.5s (Python-only) |
| Reference capture + resolution | ~400ms | ~400ms |
| SQLite writes | ~200ms | ~200ms |
| **Total (first run)** | **~3s** | **~2.2s** |
| **Total (--no-inspect)** | **~1.5s** | **~0.7s** |
| **Re-run (no changes)** | **<100ms** | **<50ms** |

### Search

- FTS5 chunk query: <10ms
- Member LIKE search: <5ms
- Reference-graph traversal: <5ms
- All queries are local SQLite — no network latency

## Installation

```bash
# Pure Python (works everywhere)
pip install -e .

# With Rust acceleration (requires Rust toolchain)
pip install maturin
maturin develop --release

# Required runtime dependency: mcp >= 1.0 + pydantic-settings >= 2.0 + pyyaml >= 6.0
# Optional: Rust toolchain (for the _native extension)
```

## License

MIT
