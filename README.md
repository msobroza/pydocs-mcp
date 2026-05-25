# pydocs-mcp

**A local Python documentation MCP server that gives your AI coding agent fast,
version-matched code retrieval — keyword (BM25) and semantic (dense) search,
fused into one hybrid ranking — over the exact versions of every dependency
installed on your machine.**

Your AI assistant remembers `requests` 2.28. You have 2.31 installed. It writes
code calling a kwarg that was renamed two versions ago — and you spend twenty
minutes debugging a failing test. The fix isn't a smarter prompt; it's giving
the AI documentation that matches **your lockfile**, not the average of every
StackOverflow answer it ever read.

pydocs-mcp indexes your project source + every installed Python dependency from
your `site-packages`, on your machine, in seconds. It exposes two MCP tools
(`search`, `lookup`) — plus a CLI mirror with the same surface and scoring — and
captures a **reference graph** so `lookup` can answer *"what calls this method?"*
across your code and every dep. No required network calls, no API keys, no rate
limits.

**Install once. Index once. Then ask your AI.**

## Retrieval, at a glance

Every query runs through a composable, sklearn-shaped retrieval pipeline. Two
retrieval modes ship today, and the YAML config lets you swap, weight, or fuse
them:

- **Keyword — BM25 over SQLite FTS5** (the default). Cheap, deterministic, ideal
  for exact terms: function names, error strings, type signatures. Metadata
  filters (`package=X`, `kind=api`) push down into SQLite rather than running in
  Python.
- **Semantic — dense embeddings.** FastEmbed by default (`BAAI/bge-small-en-v1.5`,
  no API key, ONNX model cached locally), OpenAI optional. Vectors live in a
  per-project `.tq` sidecar (TurboQuant) — never in SQLite, never in your cloud.
  Recalls paraphrases that BM25 misses.
- **Hybrid.** The `chunk_search_hybrid.yaml` preset runs BM25 + dense in parallel
  and fuses them with reciprocal-rank fusion (RRF) into a single ranking.

Plus **reference-graph navigation**: `lookup(target, show=…)` answers
`callers` / `callees` / `inherits` / `tree` over `CALLS / IMPORTS / INHERITS`
edges captured at indexing time — across your project *and* every installed dep.

> Deep dives — the full retrieval pipeline, reference graph, two-level cache,
> configuration, database schema, and the complete CLI reference — live in
> **[DOCUMENTATION.md](DOCUMENTATION.md)**. The extensibility surface (storage
> backends, new pipeline steps, planned features) is in
> **[EXTENSIONS.md](EXTENSIONS.md)**.

## Quick start

### Install

```bash
pip install -e .                              # pure Python, works everywhere
# …or with Rust acceleration:
pip install maturin && maturin develop --release
```

`fastembed` and `openai` are required runtime dependencies; `pip` pulls them
automatically (≈90 MB transitively, plus a ~80 MB ONNX model FastEmbed caches on
first use). **Linux** also needs OpenBLAS (the CBLAS interface) for the
TurboQuant vector store:

```bash
sudo apt-get install -y libopenblas-pthread-dev
```

Without it, `import pydocs_mcp` fails with `undefined symbol: cblas_sgemm`.
macOS/Windows get CBLAS from the Accelerate framework / MSVC runtime. See
**[INSTALL.md](INSTALL.md)** for details and the `LD_PRELOAD` fallback.

### Index, serve, query

```bash
pydocs-mcp serve .                            # index project + deps, start MCP server (stdio)
pydocs-mcp search "batch inference"           # CLI mirror of the search tool
pydocs-mcp lookup fastapi.routing.APIRouter.include_router --show callers
```

The first run indexes your project + every installed dep; later runs skip
unchanged packages in <100 ms. Wire it into Claude Code / Cursor / Continue.dev
over stdio — integration snippets are in
[DOCUMENTATION.md](DOCUMENTATION.md#mcp-client-integration).

## Benchmark & evaluation

pydocs-mcp ships a real retrieval-quality benchmark harness (`benchmarks/`) — not
just smoke tests. It measures retrieval against public benchmarks and competing
services:

- **Datasets:** **RepoQA-SNF** (natural-language → function retrieval in real
  repos) and **DS-1000** (data-science intent → library-docs retrieval,
  CodeRAG-Bench flavor).
- **Head-to-head:** pydocs-mcp vs **Context7** vs **Neuledge** on the same
  queries and the same gold answers, through a unified relevance layer so
  heterogeneous systems share one metric suite.
- **Metrics:** `recall@k`, `precision@1`, `mrr`, `ndcg@k`, `coverage`,
  `library_resolution@1` — each with 95% bootstrap confidence intervals — plus
  JSONL- or MLflow-backed tracking and built-in plotting.
- **Comparable runs:** because all behavior lives in YAML (below), two configs
  produce two comparable runs with zero client changes — A/B a chunker, ranker,
  or embedder against a fixed dataset.

Full guide: **[benchmarks/README.md](benchmarks/README.md)**.

## Configuration — YAML, not API params

The MCP tool surface is pinned at two tools (`search` + `lookup`) so MCP clients
(Claude Code, Cursor, IDE extensions) stay stable across deployments. Every other
knob — ranking weights, fusion, embedder identity, reference-graph capture,
chunking, output limits — lives in `AppConfig` (pydantic-settings) with layered
defaults: shipped `default_config.yaml` → pipeline blueprints → your overlay →
env vars (`PYDOCS_*`). Details and the full tunable list:
[DOCUMENTATION.md](DOCUMENTATION.md#configuration).

## How it compares

pydocs-mcp is **local, offline, version-matched to your install, Python-focused,
and reference-graph-aware**. Context7 is a hosted, multi-language doc service;
Neuledge is a local-first multi-language registry. The three aren't exclusive — a
coding agent can mount all three and route by intent (pydocs-mcp for "what calls
this method?", the others for "show me Next.js middleware patterns"). Full
side-by-side table:
[DOCUMENTATION.md](DOCUMENTATION.md#how-it-compares-context7--neuledge).

## Roadmap

Planned extensions — weighted-score fusion alongside RRF, LLM-driven tree
reasoning over `DocumentNode` trees (PageIndex-style) for long/structural
queries, and additional vector-store backends (Qdrant, Chroma, `sqlite-vec`, …)
— are catalogued in **[EXTENSIONS.md](EXTENSIONS.md)**, each scoped as a focused
follow-up PR. They are not yet shipped.

## Learn more

- **[DOCUMENTATION.md](DOCUMENTATION.md)** — retrieval pipeline, reference graph,
  cache, configuration, database schema, architecture, and the full CLI reference.
- **[EXTENSIONS.md](EXTENSIONS.md)** — the extensibility surface and planned work.
- **[benchmarks/README.md](benchmarks/README.md)** — the evaluation harness.
- **[INSTALL.md](INSTALL.md)** — installation and troubleshooting.
- **[CLAUDE.md](CLAUDE.md)** — contributor / architecture guide.

License: MIT.
