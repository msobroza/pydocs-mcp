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

# More options
pydocs-mcp serve . --depth 2 --workers 8

# Index only (no server)
pydocs-mcp index .

# Search from CLI
pydocs-mcp query "batch inference"
pydocs-mcp api predict -p vllm
pydocs-mcp query "handle request" -p __project__

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
        ├── __main__.py         # CLI entry point
        ├── _fallback.py        # Pure Python versions of Rust functions
        ├── _fast.py            # Import Rust or fallback
        ├── db.py               # SQLite management
        ├── deps.py             # Dependency resolution
        ├── indexer.py          # Index project + deps
        ├── search.py           # FTS5 + symbol search
        └── server.py           # MCP server (5 tools)
```

## MCP Tools

| Tool | Description |
|---|---|
| `list_packages` | List all indexed packages |
| `get_package_doc` | Full docs for one package |
| `search_docs` | BM25 search across all docs |
| `search_api` | Search functions/classes by name |
| `inspect_module` | Live-import and show current API |

Use `__project__` as the package name to search your own code.

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
