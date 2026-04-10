# pyctx7 — Project Specification

> Context7 for Python. Offline, no Node.js, indexes your project automatically.

## What it is

An MCP server that indexes your Python project source code and installed
dependencies into a local SQLite database with full-text search. Your AI coding
assistant gets accurate, version-specific documentation and API signatures
without network access.

## Problem

AI coding assistants hallucinate outdated APIs because their training data is
months behind. Existing solutions (Context7, Docfork) require Node.js, cloud
access, or manual per-library setup. None of them index your own project code
alongside dependencies.

## Solution

Point pyctx7 at your project directory. It reads `pyproject.toml` (or
`requirements.txt`), finds every installed dependency, and indexes everything
into a single SQLite file with FTS5 full-text search. Your project code and
dependency docs become searchable through one unified MCP interface.

## Key differentiators

1. **Zero config** — pass a directory, it discovers everything automatically
2. **Project + deps together** — search your code AND dependency docs in one query
3. **Fully offline** — no network, no API keys, no Node.js
4. **Smart cache** — hash-based skip on re-runs, per-project `.db` isolation
5. **Dual indexing mode** — `inspect` (richer) or `--no-inspect` / static (faster, safer)
6. **Rust-accelerated** — optional native extension for 4x I/O speedup
7. **Portable** — copy the `.db` file to another machine, it just works

## Architecture

```
pyctx7/
├── Cargo.toml                  # Rust dependencies
├── pyproject.toml              # Python package config (maturin)
├── src/
│   └── lib.rs                  # Rust: walker, hasher, chunker, parser
└── python/
    └── pyctx7/
        ├── __init__.py         # Package version
        ├── __main__.py         # CLI entry point
        ├── _fallback.py        # Pure Python implementations
        ├── _fast.py            # Try Rust, fall back to Python
        ├── db.py               # SQLite + FTS5 management
        ├── deps.py             # Dependency resolution
        ├── indexer.py          # Project + dependency indexing
        ├── search.py           # FTS5 + symbol search
        └── server.py           # MCP server (5 tools)
```

## Indexing pipeline

### Step 1 — Dependency resolution

Read dependencies from the project directory. Priority order:

1. `pyproject.toml` → `[project] dependencies`
2. `requirements.txt`
3. `requirements/base.txt` or `requirements/prod.txt`

Normalize package names: lowercase, replace hyphens with underscores, strip
version specifiers.

### Step 2 — Project source indexing

1. Walk all `.py` files using Rust `walkdir` (or Python `os.walk`), skipping
   `.venv`, `__pycache__`, `node_modules`, `build`, `dist`, etc.
2. Hash all file paths + modification times with xxh3 (or md5 fallback).
3. Compare hash against stored value. Skip entirely if unchanged.
4. For each `.py` file:
   - Extract module-level docstring via regex.
   - Extract top-level function and class definitions (name, signature,
     docstring) using regex-based parser. No imports, no AST.
   - Split source into semantic chunks at heading boundaries (~800 tokens each).
5. Batch insert all chunks and symbols.

### Step 3 — Dependency indexing

Two modes, selectable at runtime:

**Inspect mode** (default):
1. Import each module.
2. Use `inspect.getmembers` to extract functions, classes, signatures, type
   hints, default values, and docstrings (including inherited ones).
3. Recurse into submodules up to `--depth` levels.
4. Runs in parallel threads, limited by GIL for CPU-bound inspect work.

**Static mode** (`--no-inspect`):
1. Find all `.py` files installed by each distribution via `dist.files`.
2. Read and parse them with the same regex-based parser used for the project.
3. Fully parallelizable (Rayon in Rust, threads in Python).
4. No side-effects, no imports, works without an activated virtualenv.

Both modes also collect:
- Package metadata (version, summary, homepage, dependencies) from
  `importlib.metadata`.
- Long description / README from package metadata payload.
- Doc files (`.md`, `.rst`, `.txt`) shipped in site-packages.

Each dependency is hashed as `name:version`. Unchanged packages are skipped.

### Step 4 — FTS5 index rebuild

After all inserts, rebuild the FTS5 index once for optimal query performance.

## Database schema

```sql
-- Package metadata
CREATE TABLE packages (
    name TEXT PRIMARY KEY,
    version TEXT,
    summary TEXT,
    homepage TEXT,
    requires TEXT,        -- JSON array of dependency names
    hash TEXT             -- Cache hash for skip detection
);

-- Text chunks (docs, READMEs, source code, docstrings)
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    pkg TEXT,             -- Package name or '__project__'
    heading TEXT,         -- Section heading
    body TEXT,            -- Chunk content (max ~4000 chars)
    kind TEXT             -- 'readme', 'doc', 'docstring', 'project_doc',
                          -- 'project_code', 'dep_doc', 'dep_code'
);

-- FTS5 virtual table for BM25-ranked search
CREATE VIRTUAL TABLE chunks_fts USING fts5(
    heading, body, pkg,
    content=chunks, content_rowid=id,
    tokenize='porter unicode61'
);

-- API symbols (functions, classes)
CREATE TABLE symbols (
    id INTEGER PRIMARY KEY,
    pkg TEXT,
    module TEXT,          -- e.g. 'fastapi.routing'
    name TEXT,            -- e.g. 'APIRouter'
    kind TEXT,            -- 'def', 'async def', 'class', 'function'
    signature TEXT,       -- e.g. '(prefix: str, tags: list = None)'
    returns TEXT,         -- Return type annotation
    params TEXT,          -- JSON array of {name, type?, default?}
    doc TEXT              -- Docstring (truncated to 3000-5000 chars)
);
```

## Cache strategy

Each project gets its own database file:

```
~/.pyctx7/
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

Optional. The package works without Rust — `_fast.py` tries to import
`_native` and falls back to `_fallback.py`.

### Functions exposed to Python

| Function | Purpose | Speedup |
|---|---|---|
| `walk_py_files(root)` | Find .py files, skip excluded dirs | ~10x |
| `hash_files(paths)` | xxh3 hash of paths + mtimes | ~3x |
| `chunk_text(text, max_chars)` | Split at heading boundaries | ~10x |
| `parse_py_file(source)` | Extract functions/classes via regex | ~5x |
| `extract_module_doc(source)` | Get module-level docstring | ~5x |
| `read_file(path)` | Read one file | ~2x |
| `read_files_parallel(paths)` | Read multiple files with Rayon | ~8x |

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

## MCP tools

The server exposes 5 tools via the Model Context Protocol:

### `list_packages`
List all indexed packages with version and summary.
Use `__project__` to refer to the project's own source code.

### `get_package_doc(package)`
Full documentation for a single package: metadata, README chunks, API summary.
Accepts `__project__` as a special value.

### `search_docs(query, package?)`
BM25-ranked full-text search across all chunks (docs, source code, docstrings).
Optional package filter. Returns top 8 results with source kind and heading.

### `search_api(query, package?)`
Search symbols (functions, classes) by name or docstring content.
Returns signatures, parameter types, defaults, and docstrings.

### `inspect_module(package, submodule?)`
Live-import a module at runtime and show its current public API.
Useful for checking the actual state vs. indexed state.

## CLI

```bash
# Index project + deps, start MCP server
pyctx7 serve /path/to/project
pyctx7 serve .

# Static mode (no imports, faster)
pyctx7 serve . --no-inspect

# More options
pyctx7 serve . --depth 2 --workers 8 --no-inspect

# Index only, no server
pyctx7 index .
pyctx7 index . --force              # Clear cache, re-index all
pyctx7 index . --skip-project       # Only index deps

# Search from CLI (for testing)
pyctx7 query "batch inference"
pyctx7 query "middleware" -p fastapi
pyctx7 query "handle request" -p __project__
pyctx7 api predict -p vllm
pyctx7 api Router

# Verbose logging
pyctx7 -v serve .
```

## Integration

### Continue

```yaml
mcpServers:
  - name: pyctx7
    command: pyctx7
    args: ["serve", "/path/to/project", "--no-inspect"]
```

### VS Code (mcp.json)

```json
{
  "servers": {
    "pyctx7": {
      "command": "pyctx7",
      "args": ["serve", "${workspaceFolder}"]
    }
  }
}
```

### Cursor (.cursor/mcp.json)

```json
{
  "mcpServers": {
    "pyctx7": {
      "command": "pyctx7",
      "args": ["serve", "."]
    }
  }
}
```

### Claude Desktop

```json
{
  "mcpServers": {
    "pyctx7": {
      "command": "pyctx7",
      "args": ["serve", "/path/to/project"]
    }
  }
}
```

### SSE transport (via mcp-proxy)

```bash
pip install mcp-proxy
mcp-proxy --port 8080 -- pyctx7 serve /path/to/project
```

Then connect via `http://localhost:8080/sse`.

## Comparison

### vs Context7

| | pyctx7 | Context7 |
|---|---|---|
| Network | never | always (cloud) |
| Node.js | no | yes |
| Indexes your project | yes | no |
| Auto-discovers deps | yes (pyproject.toml) | no |
| Docstrings + signatures | yes | no (docs only) |
| Type hints | yes | no |
| Cost | free | 1000 req/month free, $10/month |
| Fully offline | yes | no |

### vs Neuledge Context

| | pyctx7 | Neuledge |
|---|---|---|
| Node.js required | no | yes |
| Indexes your project | yes | manual |
| Auto-discovers deps | yes | manual per library |
| Python-specific features | yes (inspect, types) | no (generic docs) |
| Deps from site-packages | yes | no |

### vs filesystem-operations-mcp

| | pyctx7 | filesystem-operations-mcp |
|---|---|---|
| Search method | FTS5 BM25 semantic | ripgrep text match |
| Understands Python | yes (functions, classes) | no (plain text) |
| Installed deps | yes | no (local files only) |
| Smart cache | yes (hash-based) | no |

## Performance

### Indexing (typical project with ~30 deps)

| Phase | Python fallback | With Rust |
|---|---|---|
| File walk (1000 .py) | ~200ms | ~20ms |
| File hashing | ~150ms | ~30ms |
| Text chunking | ~100ms | ~10ms |
| Source parsing | ~300ms | ~50ms |
| inspect.getmembers | ~1.5s | ~1.5s (Python-only) |
| SQLite writes | ~200ms | ~200ms |
| **Total (first run)** | **~2.5s** | **~1.8s** |
| **Total (--no-inspect)** | **~1s** | **~0.3s** |
| **Re-run (no changes)** | **<100ms** | **<50ms** |

### Search

- FTS5 query: <10ms
- Symbol search: <5ms
- All queries are local SQLite, no network latency

## Installation

```bash
# From PyPI (pure Python, works everywhere)
pip install pyctx7

# With Rust acceleration (requires Rust toolchain)
pip install pyctx7[rust]
# or from source:
pip install maturin
git clone https://github.com/<user>/pyctx7
cd pyctx7 && maturin develop --release

# Dependencies
# Required: mcp>=1.0
# Optional: Rust toolchain (for native extension)
```

## License

MIT
