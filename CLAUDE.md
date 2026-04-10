# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## Architecture

```
python/pydocs_mcp/
├── __main__.py    # CLI entry (argparse), orchestrates pipeline
├── _fast.py       # Imports Rust native module or falls back to Python
├── _fallback.py   # Pure Python implementations of all Rust functions
├── db.py          # SQLite schema, cache lifecycle, FTS rebuild
├── deps.py        # Dependency resolution (pyproject.toml, requirements.txt)
├── indexer.py     # Core indexing: inspect mode (live import) vs static mode (file read)
├── search.py      # FTS5 BM25 search + symbol LIKE queries
└── server.py      # FastMCP server exposing 5 tools to clients
src/lib.rs         # Rust acceleration: 7 PyO3 functions (walk, hash, chunk, parse, read)
```

**Data flow:** CLI → deps.py resolves packages → indexer.py extracts docs/symbols → db.py stores in SQLite → search.py queries via FTS5 → server.py exposes via MCP.

**Rust/Python duality:** `_fast.py` tries `from ._native import *`; on ImportError falls back to `_fallback.py`. All Rust functions have pure Python equivalents — the package works without Rust compiled.

**Two indexing modes:**
- **Inspect mode** (default): Imports modules via Python's `inspect`, gets full type hints and signatures. Riskier (side effects from imports).
- **Static mode** (`--no-inspect`): Reads `.py` files from site-packages with regex parsing. Faster, safer, no side effects.

**Cache:** Per-project SQLite at `~/.pydocs-mcp/{dirname}_{path_hash}.db`. Hash-based invalidation — subsequent runs with no changes skip indexing (<100ms).

## Key Technical Details

- Python 3.10+ required, single runtime dependency: `mcp>=1.0`
- Build system: maturin (PEP 517) bridges Python packaging with Rust cdylib
- Rust module name: `pydocs_mcp._native` (configured in pyproject.toml `tool.maturin`)
- Entry point: `pydocs-mcp = "pydocs_mcp.__main__:main"`
- DB has three main tables: `packages`, `chunks` (with FTS5 virtual table), `symbols`
- FTS5 uses porter stemming + unicode61 tokenizer
- The project code itself is indexed under the special package name `__project__`
