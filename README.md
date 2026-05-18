# pydocs-mcp

Local Python docs MCP server — indexes your project + installed deps.
Zero network. SQLite FTS5 + BM25. Optionally accelerated with Rust.

## Install

```bash
# With Rust acceleration (requires Rust toolchain)
pip install maturin
cd pydocs-mcp && maturin develop --release

# Without Rust (pure Python, works everywhere)
pip install -e .
```

## Usage

```bash
# Index project + deps, start MCP server
pydocs-mcp serve /path/to/project

# More options (--no-inspect uses static parsing only; --workers parallelism)
pydocs-mcp serve . --no-inspect --depth 2 --workers 8

# Index only (no server)
pydocs-mcp index .

# Search from CLI (mirrors the MCP `search` tool)
pydocs-mcp search "batch inference"
pydocs-mcp search "predict" --kind api -p vllm
pydocs-mcp search "handle request" -p __project__

# Navigate to a specific target (mirrors the MCP `lookup` tool)
pydocs-mcp lookup                                      # list packages
pydocs-mcp lookup fastapi.routing.APIRouter            # class overview
pydocs-mcp lookup fastapi.routing.APIRouter --show tree
pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers
pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits

# Force re-index
pydocs-mcp index . --force
```

## Continue config

```yaml
mcpServers:
  - name: pydocs
    command: pydocs-mcp
    args: ["serve", "/path/to/project"]
```

## How it works

1. Reads `pyproject.toml` (priority) or `requirements.txt`
2. Indexes project source via AST/regex (no imports needed)
3. Indexes installed deps via `inspect` (parallel)
4. Stores everything in SQLite with FTS5 full-text search
5. Cache: per-project `.db`, hash-based skip on re-runs

## Project structure

```
pydocs-mcp/
├── Cargo.toml                  # Rust dependencies
├── pyproject.toml              # Python package config (maturin)
├── src/
│   └── lib.rs                  # Rust: walker, hasher, chunker, parser
└── python/
    └── pydocs_mcp/
        ├── __init__.py         # Package version
        ├── __main__.py         # CLI entry point (serve / index / search / lookup)
        ├── _fallback.py        # Pure Python versions of Rust functions
        ├── _fast.py            # Import Rust or fallback
        ├── db.py               # SQLite schema + cache lifecycle
        ├── deps.py             # Dependency resolution
        ├── extraction/         # Chunkers, member extractors, ingestion pipeline
        ├── application/        # Use-case services (indexing, search, lookup)
        ├── storage/            # Repositories, UnitOfWork, VectorStore
        ├── retrieval/          # Async retrieval pipelines + YAML config
        ├── defaults/           # Shipped default_config.yaml
        ├── pipelines/          # Built-in pipeline YAML blueprints
        └── server.py           # MCP server (2 tools: search + lookup)
```

## MCP Tools

The MCP surface is intentionally minimal — two tools cover every workflow:

| Tool | Description |
|---|---|
| `search(query, kind, package, scope, limit)` | BM25 full-text search across indexed docs/code. `kind` ∈ `{docs, api, any}`. |
| `lookup(target, show)` | Navigate to a specific named target. `show` ∈ `{default, tree, callers, callees, inherits}`. Empty target lists indexed packages. |

`lookup(target=X, show="callers")`, `show="callees"`, and `show="inherits"`
query the **reference graph** (CALLS, IMPORTS, INHERITS edges) — captured
at indexing time from AST analysis and stored alongside the chunks.
Capture is on by default and tuned via YAML
(`reference_graph.capture.{enabled,kinds}`); MENTIONS is opt-in.

Use `__project__` as the package name to search your own code.

## Configuration

Behavior knobs — capture toggles, retrieval limits, ranking weights,
indexing depth — are driven by YAML, not MCP tool params. The MCP API
stays fixed at the two tools above so external clients (Claude Code,
Cursor, IDE extensions) never have to version-bump on a tuning change.

Layers (highest to lowest priority): env vars (`PYDOCS_*`) → user
`./pydocs-mcp.yaml` or `~/.config/pydocs-mcp/config.yaml` → explicit
`--config <path>` → shipped `defaults/default_config.yaml`.

Example user overlay:

```yaml
# pydocs-mcp.yaml — sits next to your project
reference_graph:
  capture:
    enabled: true
    kinds: [calls, imports, inherits, mentions]   # opt into MENTIONS
  output:
    default_limit: 25       # lookup(..., show="callers") default
    max_limit: 500

search:
  output:
    default_limit: 10       # search(..., limit=...) default
    max_limit: 1000
```

## Performance

| Operation | Python | Rust |
|---|---|---|
| File walk (1000 .py) | ~200ms | ~20ms |
| File hashing | ~150ms | ~30ms |
| Text chunking | ~100ms | ~10ms |
| Source parsing | ~300ms | ~50ms |
| **Total indexing** | **~2s** | **~0.5s** |

Re-runs with no changes: <100ms (hash check only).

## Cache

Each project gets its own SQLite database at `~/.pydocs-mcp/{dirname}_{path_hash}.db`.
The schema is versioned via `PRAGMA user_version`; opening a DB whose version doesn't
match drops all tables and re-indexes from scratch. The cache is always rebuildable
from source, so it's safe to delete at any time.

**Downgrading:** if you install an older version of `pydocs-mcp` that uses a
pre-v2 schema, delete `~/.pydocs-mcp/*.db` first — otherwise the older code
will fail with "no such column: pkg" against the newer schema.
