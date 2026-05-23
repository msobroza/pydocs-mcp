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

## Quick start

```bash
# Index your project + its installed deps and start the MCP server
pydocs-mcp serve .

# Sample CLI queries (same surface the MCP tools expose)
pydocs-mcp search "batch inference"
pydocs-mcp lookup fastapi.routing.APIRouter --show callers
```

That's it for the 30-second path. Read on for the three ways to actually integrate pydocs-mcp into your workflow.

---

## Usage patterns

pydocs-mcp is designed to work three ways. Pick the one that matches your setup.

### A. Run as an MCP server (most common)

For Claude Code, Cursor, or any MCP-compatible client.

**Start the server** (stdio transport):

```bash
pydocs-mcp serve /path/to/project
```

The server indexes the project + installed deps on startup (or uses the cached `.db` if unchanged) and exposes two tools over MCP stdio.

**Claude Code integration** (`~/.config/claude-code/mcp_servers.json` or workspace `.claude/mcp_servers.json`):

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

**Cursor integration** (`~/.cursor/mcp.json` or `.cursor/mcp.json`):

```yaml
mcpServers:
  - name: pydocs
    command: pydocs-mcp
    args: ["serve", "/path/to/your/project"]
```

**Continue.dev integration** (`~/.continue/config.json`):

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

**Example client invocations** (ask your LLM to run these after the MCP server is connected):

```
search("batch inference vllm", kind="api", package="vllm", limit=20)
lookup("fastapi.routing.APIRouter")
lookup("fastapi.routing.APIRouter.include_router", show="callers")
lookup("requests.auth.HTTPBasicAuth", show="inherits")
```

The MCP surface is **fixed at two tools by design** (see CLAUDE.md §"MCP API surface vs YAML configuration"). All pipeline tuning happens server-side via YAML — clients don't need rebuilds when you adjust ranking weights or chunk strategies.

### B. Configure via YAML

Production deployments + benchmark sweeps. Both the MCP server and the CLI load pipelines from YAML at startup.

**Shipped blueprints** (copy + modify as needed):

- `python/pydocs_mcp/pipelines/chunk_search.yaml` — default chunk-search pipeline (BM25 fetch → score → metadata filter → top-K → limit → token-budget renderer)
- `python/pydocs_mcp/pipelines/member_search.yaml` — default member-search pipeline (LIKE fetch → metadata filter → top-K → limit → budget)
- `python/pydocs_mcp/pipelines/ingestion.yaml` — default ingestion pipeline (discovery → read → chunk → flatten → hash → package → reference capture)

**Pipeline schema** (`steps:` with `name:` per step):

```yaml
name: chunk_search
steps:
  - name: fetch
    type: chunk_fetcher
    params: { limit: 200 }
  - name: score
    type: bm25_scorer
    params: {}
  - name: post_filter
    type: metadata_post_filter
    params: {}
  - name: topk
    type: top_k_filter
    params: { k: 50 }
  - name: limit
    type: limit
    params: { max_results: 8 }
  - name: budget
    type: token_budget_formatter
    params: { budget: 2000 }
```

Each step entry needs a `name:` (addressable + greppable), a registered `type:`, and `params:` matching the step's dataclass fields.

**Override with a user overlay** — a single file that layers on top of the shipped defaults:

```yaml
# my-pydocs.yaml — sits next to your project, or anywhere
extraction:
  chunking:
    markdown:
      max_heading_level: 4         # default: 3
search:
  output:
    default_limit: 20              # default: 10
reference_graph:
  capture:
    enabled: true
    kinds: [calls, imports, inherits, mentions]   # opt into MENTIONS
```

Load it explicitly:

```bash
pydocs-mcp serve . --config ./my-pydocs.yaml
```

Or set `PYDOCS_CONFIG_PATH=./my-pydocs.yaml` in the environment. The config layer is: shipped `defaults/default_config.yaml` → shipped pipeline YAML → user overlay → env vars (`PYDOCS_*`, highest priority).

All tunables are listed in `python/pydocs_mcp/defaults/default_config.yaml` — read it as the canonical reference.

### C. Build pipelines in Python code

For tests, benchmarks, or embedded usage. Build an `IngestionPipeline` and a `RetrieverPipeline` programmatically, no YAML required.

```python
import asyncio
import tempfile
from pathlib import Path

from pydocs_mcp.application import ProjectIndexer
from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.extraction import (
    AstMemberExtractor,
    PipelineChunkExtractor,
    StaticDependencyResolver,
    build_ingestion_pipeline,
)
from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.pipeline import (
    PerCallConnectionProvider,
    RetrieverPipeline,
    RetrieverState,
)
from pydocs_mcp.retrieval.steps import (
    BM25ScorerStep,
    ChunkFetcherStep,
    LimitStep,
    MetadataPostFilterStep,
    TokenBudgetStep,
    TopKFilterStep,
)
from pydocs_mcp.storage.factories import (
    build_sqlite_indexing_service,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.sqlite import SqliteChunkRepository


async def main() -> None:
    # 1. Fresh SQLite + ingestion pipeline from default AppConfig
    db_path = Path(tempfile.mkstemp(suffix=".sqlite")[1])
    open_index_database(db_path).close()
    config = AppConfig.load()

    indexer = ProjectIndexer(
        indexing_service=build_sqlite_indexing_service(db_path),
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=PipelineChunkExtractor(pipeline=build_ingestion_pipeline(config)),
        member_extractor=AstMemberExtractor(),
        uow_factory=build_sqlite_uow_factory(db_path),
    )
    await indexer.index_project(Path("/path/to/your/project"))
    await SqliteChunkRepository(provider=build_connection_provider(db_path)).rebuild_index()

    # 2. RetrieverPipeline composed from steps — sklearn-shaped, named + addressable
    provider = PerCallConnectionProvider(cache_path=db_path)
    pipeline = RetrieverPipeline(
        name="chunk_search",
        steps=(
            ("fetch", ChunkFetcherStep(provider=provider)),
            ("score", BM25ScorerStep(name="bm25_scorer")),
            ("post_filter", MetadataPostFilterStep(name="metadata_post_filter")),
            ("topk", TopKFilterStep(name="top_k_filter")),
            ("limit", LimitStep(name="limit")),
            ("budget", TokenBudgetStep(name="token_budget_formatter")),
        ),
    )

    # 3. Run a search
    state = await pipeline.run(RetrieverState(query=SearchQuery(terms="async retry")))
    if state.result is not None:
        print(state.result.items[0].text[:500])


asyncio.run(main())
```

`RetrieverPipeline` IS a `RetrieverStep`, so pipelines compose recursively — nest one as a step inside another for sub-routing. Address steps by name (`pipeline["fetch"]`) for introspection or testing.

---

## CLI reference

```bash
# Serve as an MCP server (the most common entry point)
pydocs-mcp serve /path/to/project
pydocs-mcp serve . --no-inspect --depth 2 --workers 8 --config ./my-pydocs.yaml

# Index only (no server) — useful for one-shot benchmark setups
pydocs-mcp index .
pydocs-mcp index . --force          # clear cache + re-index
pydocs-mcp index . --skip-project   # only index deps, not the project

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
```

Use `__project__` as the package name to scope a search to your own code.

---

## How it works

1. Reads `pyproject.toml` (priority) or `requirements.txt` to discover dependencies.
2. Indexes project source via AST/regex (no imports needed).
3. Indexes installed deps via `inspect` (parallel, optional — skip with `--no-inspect` for static-only).
4. Stores everything in SQLite with FTS5 full-text search.
5. Captures the reference graph (CALLS / IMPORTS / INHERITS edges) for `lookup(..., show="callers")` etc.
6. Cache: per-project `.db`, hash-based skip on re-runs (<100ms).

---

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
        ├── __main__.py         # CLI entry point (serve / index / search / lookup)
        ├── _fallback.py        # Pure Python versions of Rust functions
        ├── _fast.py            # Import Rust or fallback (substitution boundary)
        ├── db.py               # SQLite schema + cache lifecycle + FTS rebuild
        ├── deps.py             # Dependency resolution (pyproject.toml, requirements.txt)
        ├── extraction/         # Chunkers, member extractors, ingestion pipeline
        │   ├── strategies/     #   chunkers, members, discovery, dependencies
        │   ├── pipeline/       #   IngestionPipeline, stages, PipelineChunkExtractor
        │   └── model/          #   DocumentNode, NodeKind, tree helpers
        ├── application/        # Use-case services (indexing, search, lookup, formatting)
        ├── storage/            # SQLite repositories, UnitOfWork, VectorStore
        ├── retrieval/          # sklearn-style RetrieverStep ABC + RetrieverPipeline
        │   ├── pipeline/       #   RetrieverStep ABC, RetrieverPipeline, RetrieverState
        │   └── steps/          #   one file per step (chunk_fetcher, bm25_scorer, …)
        ├── defaults/           # Shipped default_config.yaml
        ├── pipelines/          # Built-in pipeline YAML blueprints
        └── server.py           # MCP server (2 tools: search + lookup)
```

For the detailed architecture (data flow, SOLID principles, async patterns, MCP API rules, single-source-of-truth defaults), see [CLAUDE.md](CLAUDE.md).

---

## MCP tool reference

The MCP surface is intentionally minimal — two tools cover every workflow.

| Tool | Signature | Purpose |
|---|---|---|
| `search` | `search(query, kind, package, scope, limit)` | BM25 full-text search across indexed docs/code. `kind` ∈ `{docs, api, any}`. |
| `lookup` | `lookup(target, show)` | Navigate to a specific named target. `show` ∈ `{default, tree, callers, callees, inherits}`. Empty target lists indexed packages. |

`lookup(target=X, show="callers")`, `show="callees"`, and `show="inherits"` query the **reference graph** (CALLS, IMPORTS, INHERITS edges) — captured at indexing time from AST analysis and stored alongside the chunks. Capture is on by default and tuned via YAML (`reference_graph.capture.{enabled,kinds}`); MENTIONS edges are opt-in.

---

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

Each project gets its own SQLite database at `~/.pydocs-mcp/{dirname}_{path_hash}.db`. The schema is versioned via `PRAGMA user_version`; opening a DB whose version doesn't match drops all tables and re-indexes from scratch. The cache is always rebuildable from source, so it's safe to delete at any time.

**Downgrading:** if you install an older version of `pydocs-mcp` that uses a pre-v2 schema, delete `~/.pydocs-mcp/*.db` first — otherwise the older code will fail with "no such column: pkg" against the newer schema.
