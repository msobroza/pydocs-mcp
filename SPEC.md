# pydocs-mcp — Project Specification

> A local Python documentation MCP server. Offline, no Node.js, indexes your project + installed dependencies automatically.

## What it is

An MCP server that indexes your Python project source code and installed dependencies into a local SQLite database with full-text search and a reference graph (CALLS / IMPORTS / INHERITS edges). Your AI coding assistant gets accurate, version-specific documentation, API signatures, and call/inheritance context without network access.

## Problem

AI coding assistants hallucinate outdated APIs because their training data is months behind. Existing documentation servers typically require Node.js, cloud access, or manual per-library setup — and rarely index your own project code alongside dependencies or expose a reference graph for "who calls this function" queries.

## Solution

Point pydocs-mcp at your project directory. It scans the tree for dependency manifests (`pyproject.toml`, `requirements*.txt`), finds every installed dependency, and indexes everything into a per-project hybrid index: a SQLite file with FTS5 full-text search plus a reference-graph table, and a TurboQuant `.tq` sidecar holding the dense embedding vectors. Your project code, dependency docs, and the cross-cutting call graph become queryable through one unified MCP interface — six task-shaped tools, fixed surface.

## Key differentiators

1. **Zero config** — pass a directory, it discovers everything automatically.
2. **Project + deps together** — search your code AND dependency docs in one query.
3. **Reference graph** — `get_references(target=X, direction="callers")` traverses CALLS/IMPORTS/INHERITS edges captured at index time.
4. **Fully offline** — no network, no API keys, no Node.js.
5. **Smart cache** — hash-based skip on re-runs, per-project `.db` + `.tq` isolation.
6. **Dual indexing mode** — `inspect` (richer, default) or `--no-inspect` / static (faster, no side effects).
7. **Rust-accelerated** — optional native extension for ~4× I/O speedup.
8. **Portable** — copy the `.db` + `.tq` pair to another machine, it just works (the `.tq` sidecar carries the dense vectors).
9. **Stable MCP surface** — six task-shaped tools (`get_overview`, `search_codebase`, `get_symbol`, `get_context`, `get_references`, `get_why`); all tuning happens via YAML config so clients don't version-bump on server-side changes.
10. **sklearn-style pipeline composition** — every retrieval step is a `RetrieverStep` subclass; pipelines are named, addressable, swappable.

## Architecture

```
pydocs-mcp/
├── Cargo.toml                  # Rust dependencies
├── pyproject.toml              # Python package config (maturin mixed layout)
├── src/
│   └── lib.rs                  # Rust: walker, hasher, parser, reader (6 PyO3 functions)
└── python/
    └── pydocs_mcp/
        ├── __init__.py         # Package version
        ├── __main__.py         # CLI entry (serve / index / watch / search / overview / symbol / context / refs / why / lookup)
        ├── _fast.py            # Try Rust, fall back to Python (substitution boundary)
        ├── _fallback.py        # Pure Python equivalents of all Rust functions
        ├── db.py               # SQLite schema (v14) + cache lifecycle + FTS rebuild
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
        ├── storage/            # Persistence — Protocols + Sqlite*/TurboQuant* concrete adapters
        │   #   SqlitePackageRepository / SqliteChunkRepository / …
        │   #   TurboQuantVectorStore (.tq sidecar) + SearchBackend / SqliteCompositeBackend
        │   #   SqliteUnitOfWork + TurboQuantUnitOfWork + CompositeUnitOfWork
        │   #   ConnectionProvider, Filter tree + FilterAdapter
        │   #   opt-in fast-plaid multi-vector index ([late-interaction] extra)
        ├── retrieval/          # Read-side pipeline machinery (sklearn-style)
        │   ├── pipeline/       #   RetrieverStep ABC, RetrieverPipeline, RetrieverState, ConnectionProvider
        │   └── steps/          #   One file per step (chunk_fetcher, bm25_scorer, top_k_filter, …)
        ├── defaults/           # Shipped default_config.yaml (lowest-priority AppConfig layer)
        ├── pipelines/          # Built-in pipeline YAML blueprints (chunk_search, member_search, ingestion)
        └── server.py           # MCP server (six task-shaped tools)
```

For the detailed architecture rules (SOLID, async patterns, MCP API surface vs YAML configuration, single-source-of-truth defaults), see [CLAUDE.md](CLAUDE.md). For the extensibility surface (how to add new storage backends, filter formats, retrieval steps, formatters), see [EXTENSIONS.md](EXTENSIONS.md).

## Indexing pipeline

The ingestion pipeline is composed of named stages (`IngestionStage` Protocol) and is itself YAML-configurable via `python/pydocs_mcp/pipelines/ingestion.yaml`.

### Step 1 — Dependency resolution

Recursively scan the project tree for every dependency manifest (skipping `.venv`, build artefacts, etc.) and union the results:

1. Every `pyproject.toml` — `[project] dependencies`, plus PEP 621 `optional-dependencies` extras and PEP 735 `dependency-groups`.
2. Every `requirements*.txt` anywhere in the tree (e.g. `requirements.txt`, `requirements-dev.txt`).

There is no priority order — all manifests contribute to one deduplicated set. Normalize package names: lowercase, replace hyphens with underscores, strip version specifiers.

### Step 2 — Project source indexing

1. Walk all `.py`, `.md`, and `.ipynb` files (the default `include_extensions`, YAML-tunable) using Rust `walkdir` (or Python `os.walk`), skipping `.venv`, `__pycache__`, `node_modules`, `build`, `dist`, etc.
2. Hash file paths + modification times with xxh3 (or md5 fallback).
3. Compare against stored hash. Skip entirely if unchanged.
4. For each `.py` file: extract docstrings + chunks (AST or regex), member definitions (functions/classes/methods), and references (CALLS / IMPORTS / INHERITS edges).
5. For each `.md` file: chunk at heading boundaries; each `.ipynb` notebook goes through the dedicated notebook chunker.
6. Batch insert chunks, module members, and references; capture into a `DocumentNode` tree for `get_symbol(..., depth="tree")` queries.

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
- Doc files (`.md`, `.ipynb`) shipped in site-packages — the same YAML-tunable `include_extensions` allowlist as project indexing; widen it to also pick up `.rst` / `.txt`.

Each dependency is hashed as `name:version`. Unchanged packages are skipped.

### Step 4 — Reference resolution

After capture, the `ReferenceResolver` walks every `node_references` row whose `to_node_id` is `NULL` and tries to resolve it against the qname universe of the indexed corpus + the bundled stdlib qnames + per-package aliases. Resolution uses 5 rules (A: explicit alias, B: exact qname match, C: strict suffix within `from_package`, D: ambiguous-suffix → None, E: no match), plus the F20 fast-path and the self-attribute short-circuit.

### Step 5 — FTS5 index rebuild

After all inserts, rebuild the FTS5 index once for optimal query performance.

## Database schema (v14)

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
    local_path TEXT,          -- For project source / editable installs
    embedding_model TEXT      -- Embedder identity used for this package's vectors
);

-- Text chunks (docs, READMEs, source code, docstrings)
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    package TEXT,             -- Package name or '__project__'
    module TEXT DEFAULT '',   -- Dotted module path
    title TEXT,               -- Section heading or symbol name
    text TEXT,                -- Chunk content
    origin TEXT,              -- 'readme', 'doc', 'docstring', 'source', …
    content_hash TEXT,
    qualified_name TEXT,      -- Dotted symbol path (tree-reasoning join key)
    embedded INTEGER NOT NULL DEFAULT 0,  -- 1 = dense vector written to the .tq sidecar
    decision_id INTEGER       -- Backlink to decision_records
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

-- DocumentNode tree for get_symbol(..., depth="tree")
CREATE TABLE document_trees (
    package TEXT NOT NULL,
    module TEXT NOT NULL,
    tree_json TEXT NOT NULL,
    content_hash TEXT,
    updated_at REAL,
    PRIMARY KEY (package, module)
);

-- Reference graph (CALLS / IMPORTS / INHERITS / MENTIONS / SIMILAR / GOVERNS edges)
CREATE TABLE node_references (
    from_package TEXT NOT NULL,
    from_node_id TEXT NOT NULL,
    to_name      TEXT NOT NULL,
    to_node_id   TEXT,
    kind         TEXT NOT NULL,    -- 'calls' | 'imports' | 'inherits' | 'mentions'
                                   -- | 'similar' (opt-in embedding-kNN synthetic edges)
                                   -- | 'governs' (decision→qname edges backing
                                   --    get_references(direction="governed_by"))
    PRIMARY KEY (from_package, from_node_id, to_name, kind)
);

-- chunk_id ↔ fast-plaid doc-id bridge for multi-vector retrieval
-- ([late-interaction] extra; the multi-vectors live in fast-plaid's index)
CREATE TABLE chunk_multi_vector_ids (
    chunk_id      INTEGER PRIMARY KEY,
    plaid_doc_id  INTEGER NOT NULL UNIQUE,
    package       TEXT    NOT NULL,
    pipeline_hash TEXT    NOT NULL
);

-- Graph analytics over node_references (in-degree / PageRank / community;
-- populated when the [graph] extra is installed)
CREATE TABLE node_scores (
    package        TEXT    NOT NULL,
    qualified_name TEXT    NOT NULL,
    in_degree      INTEGER NOT NULL DEFAULT 0,
    pagerank       REAL    NOT NULL DEFAULT 0.0,
    community      INTEGER NOT NULL DEFAULT -1,
    PRIMARY KEY (package, qualified_name)
);

-- Recorded architectural decisions backing get_why and kind="decision" search
CREATE TABLE decision_records (
    id              INTEGER PRIMARY KEY,
    package         TEXT NOT NULL,
    title           TEXT NOT NULL,
    status          TEXT NOT NULL,
    source          TEXT NOT NULL,
    confidence      REAL NOT NULL,
    evidence        TEXT NOT NULL,
    affected_files  TEXT NOT NULL,
    affected_qnames TEXT NOT NULL,
    staleness_score REAL NOT NULL DEFAULT 0.0,
    superseded_by   INTEGER,
    verification    TEXT NOT NULL DEFAULT 'verbatim',
    structured      TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);

-- Single-row index provenance card (embedder identity, pipeline hash, git HEAD)
CREATE TABLE index_metadata (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    project_name TEXT, project_root TEXT,
    embedding_provider TEXT, embedding_model TEXT, embedding_dim INTEGER,
    pipeline_hash TEXT, indexed_at REAL, git_head TEXT,
    activity_summary TEXT, overview_summary TEXT
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
CREATE INDEX idx_cmv_plaid_doc_id      ON chunk_multi_vector_ids(plaid_doc_id);
CREATE INDEX idx_cmv_package           ON chunk_multi_vector_ids(package);
CREATE INDEX ix_node_scores_qname      ON node_scores(qualified_name);
CREATE INDEX ix_node_scores_package    ON node_scores(package);
CREATE INDEX ix_decisions_package      ON decision_records(package);
```

The schema is versioned via `PRAGMA user_version`. Opening a DB whose version doesn't match drops all tables and re-indexes from scratch; idempotent additive migration sweeps run through the current version when possible (a DB drifted beyond what the sweeps can heal falls back to a full rebuild).

## Cache strategy

Each project gets its own database file plus a TurboQuant vector sidecar:

```
~/.pydocs-mcp/
├── my-rasa-bot_a3f2c1b0e9.db     # SQLite: chunks, members, trees, references
├── my-rasa-bot_a3f2c1b0e9.tq     # TurboQuant: dense embedding vectors
├── llm-optimizer_7d4e8f1a23.db
└── llm-optimizer_7d4e8f1a23.tq
```

The filename is `{directory_name}_{md5(absolute_path)[:10]}.db`; the `.tq` sidecar shares the same slug. The two files live side-by-side so a `--force` cache clear deletes both — and moving an index to another machine means copying the pair.

Cache invalidation:

| Component | Hash input | Re-indexes when... |
|---|---|---|
| `__project__` | .py file paths + mtimes | Any file created, modified, or deleted |
| Dependencies | `name:version` | Package version changed (pip upgrade) |

Second run with no changes completes in <100ms (hash comparison only).

## Rust native extension

Optional for `pip install` users — the prebuilt wheels (Linux x86_64/aarch64, macOS arm64, Windows amd64) already bundle the compiled `_native` module; other platforms use the pure-Python fallback. `_fast.py` tries to import `_native` and falls back to `_fallback.py`.

### Functions exposed to Python

| Function | Purpose | Speedup |
|---|---|---|
| `walk_py_files(root)` | Find .py files, skip excluded dirs | ~10× |
| `hash_files(paths)` | xxh3 hash of paths + mtimes | ~3× |
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

## MCP surface — six task-shaped tools

The MCP surface is intentionally minimal. Every behavioral knob lives in YAML, not in tool parameters (see CLAUDE.md §"MCP API surface vs YAML configuration").

### `search_codebase(query, kind, package, scope, limit, project)`

Hybrid-ranked full-text search across indexed chunks (project + deps).

- `query` — search terms
- `kind` ∈ `{"docs", "api", "any", "decision"}` — narrow to documentation, API surface, recorded decisions, or everything
- `package` — optional package filter; `"__project__"` searches your code only
- `scope` ∈ `{"project", "deps", "all"}` — corpus scope
- `limit` — max results (default `10`, validated 1..1000)
- `project` — one loaded repo in a multi-repo server; omit to union across all

Returns a token-budgeted composite chunk by default. The `chunk_search_ranked.yaml` preset returns top-K separate items for benchmarking.

### `get_overview(package, project)`

Orient yourself: what is indexed and what shape a repo/package has. Empty `package` covers the whole workspace (lists indexed packages).

### `get_symbol(target, depth, project)`

Navigate to a known qualified name — `target` is a dotted path like `"fastapi.routing.APIRouter"`. `depth` ∈ `{"summary", "tree", "source"}`: `"tree"` returns the `DocumentNode` tree (table of contents), `"source"` the full body.

### `get_context(targets, project)`

Everything needed to understand one or more symbols, packed in a single token-budgeted call.

### `get_references(target, direction, limit, project)`

`direction` ∈ `{"callers", "callees", "inherits", "impact", "governed_by"}` traverses the reference graph (CALLS / IMPORTS / INHERITS edges), including ranked transitive impact.

### `get_why(query, targets, project)`

Recorded architectural decisions and rationale for a topic or target.

## CLI

```bash
# Index project + deps, start MCP server
pydocs-mcp serve /path/to/project
pydocs-mcp serve .

# Static mode (no imports, faster, safer)
pydocs-mcp serve . --no-inspect

# More options (--config is a global flag — it goes BEFORE the subcommand)
pydocs-mcp --config ./my-pydocs.yaml serve . --depth 2 --workers 8 --no-inspect

# Watch mode — keep the index fresh on file edits (requires the [watch] extra)
pydocs-mcp serve . --watch              # MCP server + file watcher
pydocs-mcp watch .                      # watcher only, no MCP server

# Index only, no server
pydocs-mcp index .
pydocs-mcp index . --force              # clear cache, re-index all
pydocs-mcp index . --skip-project       # only index deps

# Search from CLI (mirrors the MCP `search_codebase` tool)
pydocs-mcp search "batch inference"
pydocs-mcp search "middleware" -p fastapi
pydocs-mcp search "handle request" -p __project__
pydocs-mcp search "predict" --kind api -p vllm

# Navigate from CLI (mirrors the other five MCP tools)
pydocs-mcp overview                                          # list packages (get_overview)
pydocs-mcp symbol fastapi.routing.APIRouter                  # class overview (get_symbol)
pydocs-mcp symbol fastapi.routing.APIRouter --depth tree
pydocs-mcp refs fastapi.routing.APIRouter.include_router --direction callers
pydocs-mcp refs requests.auth.HTTPBasicAuth --direction inherits
pydocs-mcp context fastapi.routing.APIRouter fastapi.applications.FastAPI
pydocs-mcp why "how does routing work"                       # design rationale (get_why)

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

Rather than proxying a cloud documentation service or grepping local files, pydocs-mcp builds a local hybrid index of your project and its installed dependencies. In practice that means:

- **Fully local and offline** — no network calls, no API keys, no usage quotas; everything runs against per-project `.db` + `.tq` files on disk.
- **Version-exact** — docs, signatures, and type hints come from the packages actually installed in your environment, not from whatever version a remote index happens to host.
- **Python-aware retrieval** — hybrid BM25 + dense search over chunks, plus a reference graph (callers / callees / inherits / impact) that plain text matching can't answer.
- **Project + dependencies together** — your own code is indexed under `__project__` next to site-packages, so one query spans both.
- **Zero per-library setup** — dependencies are discovered from the project's manifests automatically; no manual registration.

## Performance

### Indexing (typical project with ~30 deps)

| Phase | Python fallback | With Rust |
|---|---|---|
| File walk (1000 .py) | ~200ms | ~20ms |
| File hashing | ~150ms | ~30ms |
| Text chunking | ~100ms | ~100ms (Python-only) |
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
# From PyPI — prebuilt wheels bundle the Rust core (Linux x86_64/aarch64,
# macOS arm64, Windows amd64); pure-Python fallback on other platforms.
pip install pydocs-mcp
# Optional extras: 'pydocs-mcp[watch]' / '[sentence-transformers]' / '[openvino]'
# / '[late-interaction]' / '[graph]' / '[ask-your-docs]'

# …or from source for development (Rust core optional — pure Python works everywhere):
pip install -e .
pip install maturin && maturin develop --release
```

## License

MIT
