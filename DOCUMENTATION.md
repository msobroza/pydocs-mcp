# pydocs-mcp — Documentation

The deep-dive companion to the [README](README.md). The README is the
30-second overview; this file covers the retrieval pipeline, reference graph,
two-level cache, configuration, database schema, the full CLI reference, and MCP
client integration. For the *extensibility* surface (storage backends, new
pipeline steps, planned features), see [EXTENSIONS.md](EXTENSIONS.md); for
contributor architecture rules, see [CLAUDE.md](CLAUDE.md).

---

## Retrieval pipeline

Every query runs through a **`RetrieverPipeline`** — an sklearn-shaped chain of
**named, addressable steps** (`Pipeline([(name, step), …])`). A `RetrieverPipeline`
*is* a `RetrieverStep`, so pipelines compose recursively: nest one as a step
inside another for sub-routing, and address any step by name
(`pipeline["fetch"]`) for introspection or testing.

### The default chunk-search pipeline (dense + graph expansion)

The shipped default (`python/pydocs_mcp/pipelines/chunk_search_graph.yaml`) is an
eight-step dense + reference-graph chain:

1. `pre_filter` — parse + validate + scope-split; writes a typed result to
   `state.scratch` for the fetcher.
2. `dense_fetcher` — embed the query, ANN-search the `.tq` sidecar; writes the
   candidate set.
3. `dense_scorer` — cosine similarity → relevance.
4. `metadata_post_filter` — apply any remaining `SearchQuery.post_filter`
   in-memory.
5. `graph_expand` — seed from the top dense hits, add their 1-hop caller/callee
   neighbours (the structurally-adjacent code an embedding alone misses).
6. `top_k_filter` — sort by relevance, keep top K.
7. `limit` — cap the final item count.
8. `token_budget_formatter` — render the composite chunk for MCP output.

The former BM25-only chain (`chunk_search.yaml`) remains a shipped preset. On the
RepoQA benchmark, this dense + graph default lifts recall@10 from 0.40 (BM25) to
0.77 on standard queries and to 1.00 on structurally-reachable answers.

### Dense, hybrid, late-interaction, and tree-reasoning retrieval

Several more retrieval modes ship as opt-in pipeline presets:

- **Dense** (`chunk_search_dense.yaml`, `chunk_search_dense_ranked.yaml`) — a
  `DenseFetcherStep` + `DenseScorerStep` query the TurboQuant vector store using
  embeddings from the configured `Embedder` (FastEmbed `BAAI/bge-small-en-v1.5`
  by default; OpenAI and the on-device `sentence_transformers` provider —
  `Qwen/Qwen3-Embedding-0.6B`, `Alibaba-NLP/gte-modernbert-base`, or the
  code-strong `codefuse-ai/F2LLM-v2-0.6B` — optional).
- **Hybrid** (`chunk_search_hybrid.yaml`, `chunk_search_hybrid_ranked.yaml`) — a
  `ParallelStep` runs the BM25 and dense branches concurrently, then an
  `RRFFusionStep` merges them with reciprocal-rank fusion into one ranking.
- **Graph (dense + reference-graph expansion)** (`chunk_search_graph.yaml`,
  `chunk_search_graph_ranked.yaml`) — a `GraphExpandStep` seeds from the top
  dense hits and pulls in their 1-hop reference-graph neighbours (callers /
  callees / overriding subclass methods), merging them into the dense ranking by
  `max(dense_sim, seed_sim · decay)` — embedding-centric (no RRF/BM25). Recovers
  the structurally-adjacent answer a dense embedder misses; degrades to
  dense-only when the index has no reference graph. See the
  [structural-recall benchmark](benchmarks/README.md#structural-recall-graph-expansion).
- **Late-interaction** — opt-in multi-vector (ColBERT / PyLate via fast-plaid)
  MaxSim scoring; enable with the `[late-interaction]` extra and
  `late_interaction.enabled: true`.
- **LLM tree-reasoning** (`tree_only.yaml`,
  `chunk_search_with_tree_reasoning_parallel.yaml`,
  `chunk_search_with_tree_reasoning_after.yaml`) — vectorless RAG: an
  `LlmTreeReasoningStep` walks the project's `DocumentNode` tree (each node
  enriched with its real signature, decorators, and a docstring excerpt) with an
  LLM and fetches the nodes it selects — no embeddings required. Opt-in via the
  `llm:` config section; can run standalone, in a branch parallel to hybrid, or
  as a two-stage reranker over a BM25/dense candidate set (`rerank_candidates`).

Select a preset by pointing the chunk pipeline at it in your config overlay (see
[Configuration](#configuration)); the default is dense + graph expansion
(`chunk_search_graph.yaml`).

#### Graph analytics (opt-in)

Two further reference-graph signals are computed at index time when enabled
(both off by default; PageRank/community detection needs the `[graph]` extra —
`pip install 'pydocs-mcp[graph]'`):

- **Node scores** (`reference_graph.node_scores.enabled: true`) — a single
  post-index pass computes in-degree, PageRank, and Louvain community per symbol
  into a `node_scores` table. Two rerank steps consume it: `centrality_prior`
  (boost structurally central "god-node" APIs by a normalised PageRank/in-degree
  prior — rerank-only, can't hurt recall) and `community_diversity` (greedy
  MMR-by-community so the top-k spans subsystems instead of near-duplicates from
  one module). Add either step to a chunk pipeline YAML.
- **Similar edges** (`reference_graph.similar_edges.enabled: true`, `top_m: N`) —
  the `synthesize_similar_edges` ingestion stage adds `kind='similar'`
  embedding-kNN edges between each symbol and its nearest neighbours, densifying
  the AST graph so `graph_expand` (with `kinds: [calls, inherits, similar]`) can
  reach semantically-related code that has no call/inherit edge. `similar` edges
  are excluded from node-score centrality (which stays structural). Note: the
  kNN runs over chunks embedded in the current index pass, so a *complete*
  similar-edge graph requires a full `index --force`; an incremental reindex of
  a touched package recomputes them from its re-embedded chunks only.

Embedder inference runs on CPU by default. Pass `--gpu` to `serve` / `index` to
move it onto CUDA — same vectors, same cache, lower latency (see
[GPU inference](#gpu-inference---gpu)).

### Routing

`ConditionalStep` and `RouteStep` route per query type — e.g. send long or
structural queries down a different branch than short keyword lookups — without
modifying the branches themselves.

### Building pipelines in Python

For tests, benchmarks, or embedded usage, build an `IngestionPipeline` and a
`RetrieverPipeline` programmatically, no YAML required:

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

    # 2. RetrieverPipeline composed from named, addressable steps
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

---

## CLI reference

The CLI mirrors the MCP tools one-to-one — same pipelines, same scoring, same
rendering.

```bash
# Serve as an MCP server (the most common entry point)
pydocs-mcp serve /path/to/project
pydocs-mcp serve . --no-inspect --depth 2 --workers 8 --config ./my-pydocs.yaml
pydocs-mcp serve . --gpu            # run embedder inference on CUDA (see "GPU inference" below)

# Index only (no server) — useful for one-shot benchmark setups
pydocs-mcp index .
pydocs-mcp index . --force          # clear cache + re-index
pydocs-mcp index . --skip-project   # only index deps, not the project
pydocs-mcp index . --skip-deps      # only index the project, not its deps
pydocs-mcp index . --gpu            # index with CUDA-accelerated embeddings

# Search (mirrors the MCP `search_codebase` tool)
pydocs-mcp search "batch inference"
pydocs-mcp search "predict" --kind api -p vllm
pydocs-mcp search "handle request" -p __project__

# Navigate to a specific target (mirrors the other five MCP tools)
pydocs-mcp overview                                    # list indexed packages (get_overview)
pydocs-mcp symbol fastapi.routing.APIRouter            # class overview (get_symbol)
pydocs-mcp symbol fastapi.routing.APIRouter --depth tree
pydocs-mcp refs fastapi.routing.APIRouter.include_router --direction callers
pydocs-mcp refs requests.auth.HTTPBasicAuth --direction inherits
pydocs-mcp context fastapi.routing.APIRouter fastapi.applications.FastAPI
pydocs-mcp why "how does routing work"                 # recorded design rationale (get_why)
```

### GPU inference (`--gpu`)

`serve`, `index`, and `watch` accept `--gpu` to run **embedder inference on
CUDA** — it covers all embedders: FastEmbed and the `sentence_transformers`
provider (single-vector dense) and PyLate (late-interaction / multi-vector). It needs no YAML change and
applies to both index-time and query-time embedding.

```bash
pydocs-mcp index . --gpu     # CUDA-accelerated indexing
pydocs-mcp serve . --gpu     # CUDA for both the initial index and query-time embedding
```

`--gpu` is a **runtime latency knob only**: it does not change retrieval results
and does not trigger a re-index — the execution device is excluded from the
index-cache key, so the same `.tq` / fast-plaid index is shared across CPU and
GPU runs. It requires the matching GPU runtime for whichever embedder you use
(`onnxruntime-gpu`, `fastembed-gpu`, or a CUDA build of torch for PyLate); see
[INSTALL.md](INSTALL.md#gpu-inference-optional). With the default CPU runtimes
installed, FastEmbed/ONNX fall back to CPU and only the PyLate path requires real
CUDA. The benchmark runner takes the same `--gpu` flag.

### `search` flags

```bash
# --kind docs  → markdown / docstring chunks only
# --kind api   → ModuleMember rows (functions, classes, signatures)
# --kind any   → both, merged + scored together (default)
pydocs-mcp search "predict" --kind api
pydocs-mcp search "router include" --kind any --limit 20

# Restrict to one package. PyPI names are normalized to the DB form
# (e.g. "Flask-Login" → "flask_login"), so either spelling works.
pydocs-mcp search "auth" -p Flask-Login

# Search only YOUR project source via the __project__ sentinel.
pydocs-mcp search "handle request" -p __project__

# Restrict by SCOPE: project | deps | all (default all).
pydocs-mcp search "retry" --scope project
pydocs-mcp search "retry" --scope deps

# Cap client-visible results (default 10; top-K is also configurable in YAML).
pydocs-mcp search "logging" --limit 5

# Point at a different project (default is cwd).
pydocs-mcp search "celery beat" --project-dir /path/to/other/project

# Force the pure-Python fallback (debug the Rust substitution boundary).
pydocs-mcp search "tokenizer" --no-rust
```

`search` finds candidates by relevance; `symbol` / `refs` / `context` jump to a
specific known name. `symbol`'s `--depth` accepts `{summary, tree, source}`;
`refs`' `--direction` accepts `{callers, callees, inherits, impact}`. The
deprecated `lookup` command still works as an alias but warns — use the
task-shaped commands directly.

### MCP tool reference

The surface is **intentionally fixed at six task-shaped tools** — they cover
every workflow, and pinning them keeps MCP clients stable across server retunes
(see [Configuration](#configuration)).

| Tool | Signature | Purpose |
|---|---|---|
| `get_overview` | `get_overview(package, project)` | Orient yourself: what is indexed and what shape a repo/package has. Empty `package` covers the whole workspace. |
| `search_codebase` | `search_codebase(query, kind, package, scope, limit, project)` | Full-text / hybrid search across indexed docs + code. `kind` ∈ `{docs, api, any, decision}`. `package` / `scope` / `project` are corpus-scope filters (`project` selects one loaded repo in a multi-repo server). |
| `get_symbol` | `get_symbol(target, depth, project)` | Navigate to a known dotted path. `depth` ∈ `{summary, tree, source}`. `project` resolves the target inside one loaded repo. |
| `get_context` | `get_context(targets, project)` | Everything needed to understand one or more symbols, packed in a single call. |
| `get_references` | `get_references(target, direction, limit, project)` | Traverse the reference graph. `direction` ∈ `{callers, callees, inherits, impact, governed_by}`. |
| `get_why` | `get_why(query, targets, project)` | Recorded architectural decisions and rationale for a topic or target. |

---

## Multi-repo serving

One MCP server (or CLI query) can host several already-indexed repos. Each indexed
project is a portable `{name}_{hash}.db` + `.tq` bundle (relative source paths and
logical identifiers only — no absolute paths), stamped at index time with an
`index_metadata` row: project name/root, the embedder identity, and `indexed_at`.

- **Load** — `serve --workspace <dir>` loads every `.db` bundle in a directory;
  `serve --db <file>` loads specific bundles (repeatable). Both are **read-only**:
  the real source may be absent, so there is no reindex/watch. `--cache-dir` still
  controls where `index` writes bundles (default `~/.pydocs-mcp`); the
  `{name}_{hash}` filename is unchanged.
- **Scope** — the per-query `project` filter (MCP tool param / CLI `--project`)
  restricts to one loaded repo by name; omitted, a query **unions** across all
  loaded repos.
- **Dedup** (union only) — when the same symbol appears in several repos, a
  root-project copy (`__project__`) beats a dependency copy; among duplicate
  dependencies the most-recently-indexed (`indexed_at`) wins. Cross-repo scores are
  comparable because…
- **Embedder guard** — every loaded db must match the configured embedder
  (`model` + `dim`, from `index_metadata`); a read-only load that can't re-embed
  fails fast with a clear error rather than a dim-mismatch panic at query time.
  Note: the guard compares model + dim only — a `backend`/quantization
  difference between bundles (same model, qint8 vs full precision) is not
  caught here; single-db deployments catch it via the pipeline hash.

`get_symbol(target)` without `project` resolves across loaded repos most-recent-first
and returns the first repo that has the target (its reference-graph traversal stays
within that repo).

---

## Live re-indexing

The file-system watcher re-triggers indexing on edits so subsequent
queries see fresh data. Two modes are available, each tuned for a
different workflow:

```bash
pydocs-mcp serve . --watch   # MCP server + watcher (for AI clients connected over stdio)
pydocs-mcp watch .            # watcher only (no MCP server; index stays fresh for CLI `search` / `symbol` / `refs`)
```

Use `serve --watch` when an AI client (Claude Code, Cursor, Continue.dev)
is connected over stdio and you want the index to refresh as you edit.
Use `watch` when you don't need an MCP server running — for example, you
prefer the CLI `search` / `symbol` / `refs` commands, or you want to keep the
index fresh from an IDE-driven workflow without leaving an idle FastMCP
stdio process. Both modes share the same YAML knobs under `serve.watch.*`.

Without either mode, the server (or `pydocs-mcp index`) indexes once and
exits — today's behavior, unchanged.

### Install

The watcher uses `watchdog`, which ships as an optional extra:

```bash
pip install pydocs-mcp[watch]
pydocs-mcp serve . --watch    # or:
pydocs-mcp watch .
```

Without the `[watch]` extras, both `pydocs-mcp serve --watch` and
`pydocs-mcp watch` exit with an actionable install hint. Default
`pydocs-mcp serve` (no `--watch`) does not require `watchdog`.

### How it works

1. The watcher monitors the project root (NOT `site-packages/`, which is under
   the ignored `.venv`). It fires on edits to source files (`extensions`) **and**
   to dependency manifests (`pyproject.toml` / `requirements*.txt`) — so adding a
   package (e.g. `uv add X`, which edits `pyproject.toml`) reindexes and picks up
   the new dependency once it's installed.
2. File-system events for paths matching `extensions` — or a dependency manifest
   (`pyproject.toml` / `requirements*.txt`, always watched regardless of
   `extensions`) — AND not matching any `ignore_globs` pattern are queued.
3. Events are **debounced** by `debounce_ms` — N edits within the
   window collapse into a single reindex. Editor atomic-save sequences
   (temp create → delete → rename) naturally fall under the same
   trigger.
4. Edits arriving during an in-flight reindex schedule **exactly one**
   follow-up reindex (no thundering herd from `git checkout` /
   `git rebase` rewrites).
5. The two-level cache (`packages.content_hash` + `chunks.content_hash`)
   makes the no-change case <100 ms; only modified packages are
   re-extracted, only added/changed chunks are re-embedded.

### YAML knobs (`serve.watch.*`)

All tunables live in YAML — no MCP tool params change (the MCP
surface stays fixed at the six task-shaped tools). The CLI
`--watch` flag overrides `enabled` at runtime.

```yaml
# pydocs-mcp.yaml
serve:
  watch:
    enabled: false              # CLI --watch overrides this at runtime
    debounce_ms: 500            # 1 .. 60_000 ms; 500ms is editor-safe
    extensions: [".py", ".md", ".ipynb"]
    ignore_globs:
      - "**/__pycache__/**"
      - "**/.git/**"
      - "**/.venv/**"
      - "**/node_modules/**"
      - "**/.pytest_cache/**"
      - "**/*.pyc"
```

### Trade-offs

- **Memory + one OS event handle** for the watcher process — small,
  but headless / CI deployments that never edit code should leave
  `--watch` off.
- **Brief query-latency hit during reindex** — SQLite WAL mode allows
  concurrent readers, so MCP queries continue serving stale-but-correct
  data while the reindex transaction commits.
- **Reindex failures are logged but do not crash the MCP server** —
  the server keeps serving the previous index. Check the logs if
  results look stale.

---

## Reference graph

At indexing time the AST walker captures **`CALLS` / `IMPORTS` / `INHERITS`**
edges (and optionally **`MENTIONS`** in markdown, via a YAML toggle) into the
`node_references` SQLite table. `get_references(target, direction=…)` answers the
graph shapes, and `get_symbol(target, depth="tree")` the structural one:

- `direction="callers"` — every site that calls this method, project-wide (your
  code + every dep).
- `direction="callees"` — every method this one calls.
- `direction="inherits"` — the inheritance graph above this class.
- `get_symbol(…, depth="tree")` — the structural `DocumentNode` tree for the
  module, the same shape used for structural rendering.

Cross-package edges resolve through a rule-based resolver (see
`python/pydocs_mcp/application/reference_service.py` and the resolver it
composes): it matches an edge's target name against indexed qualified names,
preferring exact matches and falling back to suffix matches under strictness
controls, with guards to avoid combinatorial blow-ups on deeply nested
attribute chains. Unresolved targets are still stored (with a null
`to_node_id`) so a later index pass can resolve them.

Capture is on by default and tuned via YAML
(`reference_graph.capture.{enabled,kinds}`); `MENTIONS` is opt-in.

---

## Embedding backends (sentence_transformers)

The `sentence_transformers` provider can run its model through three runtimes,
selected by `embedding.backend`: `torch` (default), `onnx`, or `openvino`.
`embedding.model_file_name` optionally picks a specific exported weight file in
the HF repo — e.g. `openvino/openvino_model_qint8_quantized.xml` or
`onnx/model_qint8_avx512.onnx` — for quantized CPU inference (typically 2–4×
faster than torch-CPU at a small recall cost).

- Non-torch backends need the matching sentence-transformers extra:
  `pip install 'pydocs-mcp[openvino]'` (or `pip install
  'sentence-transformers[onnx]'` for the ONNX backend).
- `backend: openvino` is CPU/iGPU-only — combining it with `device: cuda`
  (`--gpu`) fails at config load with an actionable error.
- Both keys fold into the pipeline hash **only when set**, so enabling them
  re-embeds on the next index (quantized vectors differ from full precision),
  while default configs keep their existing index hashes byte-identical.
- Both keys are inert for the `fastembed` / `openai` providers.

## Selective dependency embedding

Everything discovered is chunked and FTS/BM25-indexed, but **dense vectors are
written per package tier** (`EmbedPolicy` — `extraction/embed_policy.py`):

- **`full`** — every chunk embedded. Applies to the project itself, to any
  dependency matching `embedding.full_index_dependencies` (exact names or
  fnmatch globs; the CLI `--full-dep NAME` flag merges into the list), and
  globally when `embedding.dependency_policy: full`.
- **`doc_pages`** (default for dependencies) — only documentation chunks are
  embedded: the per-module docstring pages emitted by the
  `dependency_doc_pages` ingestion stage (module docstring + public top-level
  signatures/docstrings, code bodies excluded), plus markdown sections and
  READMEs. One embedding per module instead of one per def.
- **`none`** — dependencies get no vectors at all (BM25-only).

Mechanics worth knowing:

- The package's tier is folded into every chunk's `content_hash`
  (`AssignChunkContentHashStage` appends `|tier:<tier>`), so promoting or
  demoting one dependency re-embeds (or drops vectors for) **exactly that
  package** on the next index — no global re-embed.
- `chunks.embedded` (schema v12) records which chunks actually carry a `.tq`
  vector; the startup integrity check compares vectors against this flag, so
  deliberately-unembedded chunks are a steady state, never treated as drift.
- Dense search over a partially-embedded corpus returns the embedded subset
  (the allowlist is intersected with the index's ids first).
- `scope=deps` queries route to `pipelines/chunk_search_deps.yaml`
  (BM25 over all dep chunks ∥ dense over their doc pages, RRF-fused) via the
  `scope_is_dependencies_only` route predicate; other scopes stay on the
  dense+graph default, which also reaches the embedded dep doc pages.
- A package whose tier yields no embeddable chunks keeps
  `packages.embedding_model` NULL and is never re-embedded on model changes.

## Two-level cache

Each project gets a `.db` (SQLite — chunks + metadata + reference graph) plus a
matching `.tq` (TurboQuant — dense vectors) under `~/.pydocs-mcp/`. The SQLite
filename is `{dirname}_{path_hash}.db`, where `path_hash` is a 10-char slug of
the absolute project path, so two projects in different directories never share
state.

### Skip when nothing changed

Subsequent indexing runs do a quick metadata scan and skip when nothing changed
(typically <100 ms):

- For every package (your project + each dep), the indexer collects
  `(file_path, mtime)` pairs, joins them into one buffer, and hashes it with
  **xxh3-64** → stored in `packages.content_hash`.
- Before re-indexing a package, it recomputes the hash and compares. **Match →
  skip the whole package** (no parsing, no chunking, no embedding, no writes).
  Mismatch → re-extract that package only.
- `mtime` + path (not file contents) is the signal: cheap to read in bulk, and
  the file tree is the source-of-truth. Reading contents would defeat the speed
  goal.

### Chunk-level diff-merge

When a package *is* re-extracted, work happens at chunk granularity. Each chunk
carries a `content_hash`:

```
content_hash = SHA-256( package \0 module \0 title \0 text \0 pipeline_hash )
```

`IndexingService.reindex_package` diffs incoming chunks against the persisted set
by this hash: unchanged chunks keep their row **and** their vector, removed
chunks are wiped atomically from **both** stores via the `CompositeUnitOfWork`,
and only added chunks are re-embedded.

The `pipeline_hash` slot is what makes model swaps automatic:

```
pipeline_hash = SHA-256( embedder identity (provider, model_name, dim, bit_width)
                         |  ingestion.yaml raw bytes )
```

Any change to the embedder config *or* any edit to `ingestion.yaml` (even
whitespace — raw bytes are hashed, deliberately conservative) changes
`pipeline_hash`, which changes every chunk's `content_hash`. The diff-merge then
sees all chunks as "added" and re-embeds through the normal add path — no
separate "force re-embed" code path, no manual cache wipe.

### Clearing

`pydocs-mcp index . --force` calls `IndexingService.clear_all`, which wipes
SQLite + TurboQuant atomically through the same `CompositeUnitOfWork` — no
half-deleted state if a crash lands mid-clear. Both files are always rebuildable
from source, so deleting `~/.pydocs-mcp/*.db` and the matching `.tq` is always
safe.

---

## Configuration

The MCP tool surface is pinned at the six task-shaped tools. Every other knob — ranking
weights, fusion algorithm, embedder identity, reference-graph capture toggles,
chunking strategies, output limits, formatter choice — lives in `AppConfig`
(pydantic-settings) with **layered defaults**:

```
shipped defaults/default_config.yaml   (lowest priority)
  → shipped pipeline blueprints (pipelines/*.yaml)
  → your overlay (--config ./my-pydocs.yaml or PYDOCS_CONFIG_PATH)
  → env vars (PYDOCS_*)                (highest priority)
```

This keeps MCP clients (Claude Code, Cursor, IDE extensions) stable across
deployments while giving you per-project experiment tracking: two YAMLs produce
two comparable retrieval runs with nothing rebuilt client-side. (This is exactly
what the [benchmark harness](benchmarks/README.md) exploits.)

### Shipped blueprints

- `pipelines/chunk_search_graph.yaml` / `…_graph_ranked.yaml` — **default** chunk
  search: dense retrieval + `graph_expand` reference-graph expansion.
- `pipelines/chunk_search.yaml` — BM25 chunk search (the former default).
- `pipelines/chunk_search_ranked.yaml` — BM25, ranked top-K (no composite
  collapse).
- `pipelines/chunk_search_dense.yaml` / `…_dense_ranked.yaml` — dense retrieval.
- `pipelines/chunk_search_hybrid.yaml` / `…_hybrid_ranked.yaml` — BM25 + dense
  fused via RRF.
- `pipelines/tree_only.yaml` — LLM tree-reasoning only (vectorless).
- `pipelines/chunk_search_with_tree_reasoning_parallel.yaml` — hybrid + LLM
  tree-reasoning in a parallel branch, fused downstream.
- `pipelines/chunk_search_with_tree_reasoning_after.yaml` — hybrid first, then
  LLM tree-reasoning as a downstream reranker.
- `pipelines/member_search.yaml` — default member search.
- `pipelines/ingestion.yaml` — default ingestion (discovery → read → chunk →
  reference capture → flatten → content-hash → embed → package build).

### Pipeline schema

```yaml
name: chunk_search
steps:
  - name: fetch                 # addressable + greppable
    type: chunk_fetcher         # a registered step type
    params: { schema_name: chunk }
  - name: score
    type: bm25_scorer
    params: {}
  # …
```

Each step entry needs a `name:`, a registered `type:`, and `params:` matching the
step's dataclass fields.

### Example overlay

```yaml
# my-pydocs.yaml
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

```bash
pydocs-mcp serve . --config ./my-pydocs.yaml
# or: PYDOCS_CONFIG_PATH=./my-pydocs.yaml pydocs-mcp serve .
```

Every tunable is listed in `python/pydocs_mcp/defaults/default_config.yaml` —
read it as the canonical reference.

---

## Database schema (simplified)

The SQLite file holds six tables. The schema is versioned via
`PRAGMA user_version`; a mismatch on open drops the known tables and re-indexes
from scratch. Dense vectors are **not** in SQLite — they live in the per-project
`.tq` TurboQuant sidecar.

```mermaid
erDiagram
    packages {
        TEXT name PK
        TEXT version
        TEXT content_hash "xxh3 of (path,mtime) pairs"
        TEXT origin "site-packages | __project__"
        TEXT local_path
    }
    chunks {
        INTEGER id PK
        TEXT package FK
        TEXT module
        TEXT title
        TEXT text "indexed by chunks_fts (FTS5)"
        TEXT content_hash
    }
    chunks_fts {
        FTS5 virtual "porter + unicode61 tokenizer"
    }
    module_members {
        INTEGER id PK
        TEXT package FK
        TEXT module
        TEXT name "function/class/method"
        TEXT kind
        TEXT signature
        TEXT docstring
    }
    document_trees {
        TEXT package PK
        TEXT module PK
        TEXT tree_json "DocumentNode tree"
        TEXT content_hash
    }
    node_references {
        TEXT from_package PK
        TEXT from_node_id PK
        TEXT to_name PK
        TEXT kind PK "CALLS | IMPORTS | INHERITS | MENTIONS"
        TEXT to_node_id "null if unresolved"
    }
    packages ||--o{ chunks                : has
    packages ||--o{ module_members        : has
    packages ||--o{ document_trees        : has
    packages ||--o{ node_references       : owns
    chunks   ||--|| chunks_fts            : indexed_by
```

| Table | What it stores | Where it's read |
|---|---|---|
| `packages` | One row per indexed package + the cache-skip `content_hash`. Project source is stored under the sentinel `name = "__project__"`. | Indexing skip-check, `get_overview` (list packages) |
| `chunks` | Documentation + source chunks (markdown sections, docstrings, code blocks). `content_hash` powers the chunk-level reindex-skip; the dense vector for each chunk lives in the `.tq` sidecar, not in this row. | `search_codebase(query, kind="docs")` via FTS5 + dense scoring |
| `chunks_fts` | FTS5 virtual table mirroring `chunks.title` + `chunks.text` + `chunks.package`, with Porter stemming + unicode61. | `search_codebase` BM25 ranking (fused with dense via RRF) |
| `module_members` | Functions, classes, methods, attributes — name + signature + docstring + kind. | `search_codebase(query, kind="api")`, `get_symbol(target)` |
| `document_trees` | The hierarchical `DocumentNode` tree per module. | `get_symbol(…, depth="tree")` |
| `node_references` | The reference graph: one row per (`from_node`, `to_name`, `kind`) edge. | `get_references(…, direction="callers"\|"callees"\|"inherits")` |

The schema is documented in [python/pydocs_mcp/db.py](python/pydocs_mcp/db.py).

---

## Architecture

Hexagonal layout. `application/` services
(`IndexingService`, `ProjectIndexer`, `DocsSearch`, `ApiSearch`, `PackageLookup`,
`ModuleInspector`, `LookupService`, `ReferenceService`, `TreeService`) depend
only on **Protocols** defined in `storage/protocols.py` — `ChunkStore`,
`PackageStore`, `ModuleMemberStore`, `DocumentTreeStore`, `ReferenceStore`,
`Embedder`, `UnitOfWork`, `TextSearchable`, `VectorSearchable`,
`HybridSearchable`, `ResultFuser`, `FilterAdapter`. Concrete adapters
(`Sqlite*` repositories, `TurboQuantStore` / `TurboQuantUnitOfWork`,
the `SearchBackend` / `SqliteCompositeBackend` capability factory,
`CompositeUnitOfWork`) live behind them.

The composition root (`server.py` + `__main__.py` + `storage/factories.py`)
builds one `uow_factory` closure and threads it through every service. Rust
acceleration sits behind a substitution boundary: `_fast.py` resolves to
`_native` (compiled) or `_fallback.py` (pure Python) with identical signatures on
both sides, so the package works with or without the compiled extension.

```
pydocs-mcp/
├── Cargo.toml                  # Rust dependencies
├── pyproject.toml              # Python package config (maturin mixed layout)
├── src/lib.rs                  # Rust: walker, hasher, chunker, parser (PyO3)
└── python/pydocs_mcp/
    ├── __main__.py             # CLI entry (serve / index / search / symbol / refs / …)
    ├── _fast.py / _fallback.py # Rust-or-pure-Python substitution boundary
    ├── db.py                   # SQLite schema + cache lifecycle + FTS rebuild
    ├── deps.py                 # Dependency resolution
    ├── extraction/             # Chunkers, member extractors, ingestion pipeline, embedders
    ├── application/            # Use-case services (indexing, search, lookup, references, trees)
    ├── storage/                # SQLite + TurboQuant adapters, Protocols, UnitOfWork
    ├── retrieval/              # RetrieverStep ABC + RetrieverPipeline + steps/
    ├── defaults/               # Shipped default_config.yaml
    ├── pipelines/              # Built-in pipeline YAML blueprints
    └── server.py               # MCP server (six task-shaped tools)
```

Every layer boundary is a Protocol and every swappable component has a registry,
so new backends and steps land as one file each with no modification to existing
code. The full extension menu — vector-store backends, filter formats, pipeline
steps, fusion algorithms, planned features — is catalogued in
[EXTENSIONS.md](EXTENSIONS.md); the contributor-facing rules (SOLID, async
patterns, MCP API rules, single-source-of-truth defaults) are in
[CLAUDE.md](CLAUDE.md).

---

## Design patterns

Beyond the hexagonal layout above, pydocs-mcp leans on a small set of
named patterns that together explain *why* the codebase looks the way
it does. Each one resolves a specific tradeoff and lives behind a
recognizable file shape — once you spot the pattern, adding a new
backend / step / service usually reduces to copying one of these.

### Architectural patterns

| Pattern | Where in the code | What it buys |
|---|---|---|
| **Hexagonal / Ports & Adapters** | `storage/protocols.py`, `application/protocols.py`, `retrieval/protocols.py` define the ports; `Sqlite*` / `TurboQuant*` / `FastPlaidUnitOfWork` are the adapters, fronted by the `SearchBackend` / `SqliteCompositeBackend` capability factory (`storage/search_backend.py`). | Application code never imports a concrete `Sqlite*` type — swapping SQLite for Postgres / DuckDB / a hosted vector store is a pure adapter change, not a service rewrite. |
| **Repository pattern** | One class per persisted entity: `SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository`, `SqliteDocumentTreeStore`, `SqliteReferenceRepository`. | Each entity's SQL lives in exactly one place. New columns mean editing one file. |
| **Unit of Work + Composite UoW** | `SqliteUnitOfWork`, `TurboQuantUnitOfWork`, `FastPlaidUnitOfWork`, plus `CompositeUnitOfWork` (`storage/composite_uow.py`) that fans out to children. | Multi-store writes (chunks → vectors → multi-vectors → mapping table) commit or roll back atomically. Application services depend on `uow_factory: Callable[[], UnitOfWork]` and don't know which backends are wired. |
| **Pipeline pattern (sklearn-shaped)** | `RetrieverPipeline = [(name, RetrieverStep), …]` for reads; `IngestionPipeline` + `IngestionStage` for writes. `Pipeline` IS a `Step`, so sub-pipelines nest without an adapter. | YAML presets compose by name. Parallel branches, fusion, re-rankers all land as new steps without touching existing ones. |
| **Strategy pattern** | Chunkers (`AstPythonChunker`, etc.), member extractors (`AstMemberExtractor`, `InspectMemberExtractor`), dependency resolvers, single-vector embedders (`FastEmbedEmbedder`, `OpenAIEmbedder`), multi-vector embedders (`PyLateEmbedder`). | Swap behavior at the boundary that needs it. Each strategy is one file. |
| **Composition root** | `server.py`, `__main__.py`, `storage/factories.py`. Everywhere else takes a closure. | Wiring decisions live in exactly three files. Every other module is testable in isolation. |
| **Registry + decorator** | `@step_registry.register("name")`, `@stage_registry.register("name")`, `@predicate("name")`, `@formatter_registry.register("name")`. | Extensions become YAML-addressable with one decorator. The shipped `EXTENSIONS.md` menu IS the registry. |
| **Substitution boundary** | `_fast.py` resolves to either the compiled Rust `_native` extension or the pure-Python `_fallback.py`. Identical signatures both sides. | The package works with or without the Rust extension; tests don't have to fork by build mode. |
| **Null Object pattern** | `NullTreeService`, `NullReferenceService` (read-loud), `NullVectorStore`, `NullMultiVectorStore` (write-silent). Wired by the composition root when a backend is disabled. | `if x is not None:` guards disappear from every consumer. The Protocol field is *always* present; behavior just becomes a no-op or an actionable error. |
| **Filter tree → Adapter (hexagonal seam)** | Retrieval emits backend-neutral `Filter` trees (`storage/filters.py`); the `FilterAdapter` Protocol translates to SQL at the storage boundary. The same tree feeds fast-plaid via the `subset=` argument for late-interaction. | No retrieval step ever imports `SqliteFilterAdapter` at runtime. Same tree, two backends, zero leakage. |

### Code-level idioms

- **Frozen + slots dataclasses for value objects and pipeline steps.**
  `@dataclass(frozen=True, slots=True)` is the default; mutation
  happens via `dataclasses.replace`, never in-place. This makes parallel
  pipeline branches safe by construction — see `retrieval/steps/parallel.py`.
- **Scratch hygiene under parallelism.** `RetrieverState.scratch` is the
  documented escape hatch for per-step coordination. Steps that may run
  inside a `ParallelStep` branch (today: `TopKFilterStep`, `PreFilterStep`,
  `LateInteractionScorerStep`) build a fresh `dict(state.scratch)` and
  return via `replace(state, scratch=new_scratch)` — never mutate the
  input's scratch in place. The rule keeps branches from leaking into
  each other.
- **Single source of truth for defaults.** A module-level
  `_DEFAULT_X = value` (e.g. `_DEFAULT_TOP_K = 100` in
  `late_interaction_scorer.py`) is the canonical source; field defaults,
  `to_dict` comparisons, and `from_dict` fallbacks all reference the
  constant. Bumping the default touches one line, not three.
- **Lazy imports for optional extras.** Heavy / optional deps
  (`fast_plaid`, `torch`, `pylate`) are imported strictly inside the
  methods that use them. A module-level `_FastPlaidCls` slot caches the
  resolution; tests monkeypatch it to exercise the import-missing branch
  without uninstalling the extra. Result: the default install never pays
  the import cost of code paths it doesn't use.
- **Async + `asyncio.to_thread` for CPU/I/O off the loop.** MCP handlers
  are `async def`. Blocking SQLite calls, mmap loads, and Rust PyO3
  inference all get offloaded via `await asyncio.to_thread(...)`. Never
  `time.sleep` in async code; use `asyncio.sleep`.
- **One responsibility per file.** One retrieval step per file under
  `retrieval/steps/`; one ingestion stage per file under
  `extraction/pipeline/stages/`; one repository per persisted entity.
  Files stay short enough to hold in working memory while editing.
- **Comments explain *why*, not *what*.** Code is self-documenting for
  the what; comments call out non-obvious tradeoffs, workarounds, or
  hidden invariants. References to internal task IDs / planning artifacts
  die with the code that explained them — they don't accumulate in the
  source tree.

### Why this set, in one line each

- **Hexagonal** — keep services testable; swap storage without rewriting.
- **Repository / UoW / Composite UoW** — atomic multi-store writes
  without coupling the writer to which backends are present.
- **Pipeline / Strategy / Registry** — YAML-tunable behavior without
  source edits; the same step composes across BM25, dense, late-interaction.
- **Composition root** — wiring lives in three files, everything else
  takes closures.
- **Substitution boundary** — Rust optional, never required.
- **Null Object** — Protocols stay non-optional; consumers never branch
  on whether a backend is wired.
- **Filter tree → Adapter** — late-interaction's SQLite + fast-plaid
  coupling reuses the same seam BM25 + dense already used. No new
  abstraction, just one more adapter behind a contract.

The full contributor-facing rule set — naming conventions, async
patterns, SSOT defaults, the MCP-API-vs-YAML rule, and the README
jargon audit — lives in [CLAUDE.md](CLAUDE.md).

---

## MCP client integration

Start the server over stdio, then point your client at it.

```bash
pydocs-mcp serve /path/to/project
```

**Claude Code** (`~/.config/claude-code/mcp_servers.json` or workspace
`.claude/mcp_servers.json`):

```json
{
  "mcpServers": {
    "pydocs": { "command": "pydocs-mcp", "args": ["serve", "/path/to/your/project"] }
  }
}
```

**Cursor** (`~/.cursor/mcp.json` or `.cursor/mcp.json`):

```json
{
  "mcpServers": [
    { "name": "pydocs", "command": "pydocs-mcp", "args": ["serve", "/path/to/your/project"] }
  ]
}
```

**Continue.dev** (`~/.continue/config.json`):

```json
{
  "mcpServers": [
    { "name": "pydocs", "command": "pydocs-mcp", "args": ["serve", "/path/to/your/project"] }
  ]
}
```

Example client invocations (ask your LLM to run these once connected):

```
search_codebase("batch inference vllm", kind="api", package="vllm", limit=20)
get_symbol("fastapi.routing.APIRouter")
get_references("fastapi.routing.APIRouter.include_router", direction="callers")
get_references("requests.auth.HTTPBasicAuth", direction="inherits")
```

---

## How it compares (Context7 / Neuledge)

Three open-source projects in roughly the same MCP-doc-retrieval space, each
optimizing for different things.

| Aspect | **pydocs-mcp** | **Context7** ([upstash/context7](https://github.com/upstash/context7)) | **Neuledge Context** ([neuledge/context](https://github.com/neuledge/context)) |
|---|---|---|---|
| Deployment | Local stdio MCP server | Hosted MCP at `mcp.context7.com` (or `ctx7` CLI) | Local stdio MCP server (`context serve`) |
| Doc source | **Your installed Python deps** + your project source, indexed in place | Curated community library docs hosted by Upstash | Community-driven package registry (~100+ libraries) downloaded then queried locally |
| Version match | Whatever you have in `site-packages` — automatic | Library + version selectable in the prompt | Latest from the registry |
| Languages | Python only | Multi-language | Multi-language (~100+ libraries) |
| Retrieval | BM25 + dense embeddings, fused into hybrid (RRF) | Not publicly documented | BM25 over SQLite FTS5 |
| Code-structure queries | **Reference graph** — `get_references(target, direction="callers"\|"callees"\|"inherits")` | None (doc retrieval only) | None (doc retrieval only) |
| Project source indexing | Indexes your own code under `__project__` | No (external docs only) | No (registry packages only) |
| MCP tools | Six task-shaped tools, pinned (`get_overview`, `search_codebase`, `get_symbol`, `get_context`, `get_references`, `get_why`) | `resolve-library-id`, `query-docs` | Doc-retrieval tools |
| Privacy | **Fully offline** with the default embedder — zero network calls | Queries hit Upstash; OAuth + API key | Local once packages are downloaded |
| Customization | YAML pipelines (chunkers, scorers, filters, fusion, formatters) via `AppConfig` | API key + HTTP headers | Registry-package mechanics |
| Cost | **$0** — OSS (MIT), no keys / rate limits / fees | Free tier (rate-limited, API key) + paid | **$0** — OSS (Apache-2.0), local-first |
| Vendor lock-in | None — your data is a SQLite file you can read/delete/move | Reliance on the hosted service (closed-source crawling/parsing) | None — retrieval + storage stay local |
| License | MIT | MIT | Apache-2.0 |

**Pick pydocs-mcp** for offline, version-matched-to-your-install retrieval in
Python, when you care about navigating code structure (callers / callees /
inheritance), not just reading docs. **Pick Context7** for a hosted service with
up-to-date docs across many languages. **Pick Neuledge** for local-first
multi-language coverage from a community registry. They're not exclusive — mount
all three and route by intent.
