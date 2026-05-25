# Extension Points

**Context:** this document describes the extensibility surface of pydocs-mcp. The MCP surface (2 tools), the storage Protocol layer, the sklearn-style retrieval pipeline, the reference graph, and the hybrid BM25 + dense retrieval stack have all shipped — every extension hook listed below is live in the current codebase.

**Purpose:** a reference menu for picking "test-the-architecture" work. Use it to propose small follow-up PRs that exercise one extension point end-to-end and validate that the abstractions hold up in practice.

---

## Architecture snapshot

```
┌──────────────────────────────────────────────────────────────────┐
│  MCP handlers (server.py) + CLI (__main__.py)                    │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  application/ — Application Services                             │
│    IndexingService, ProjectIndexer                               │
│    DocsSearch, ApiSearch, PackageLookup, ModuleInspector         │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  retrieval/ — sklearn-style Pipeline + Step ABC + formatters     │
│    RetrieverStep (ABC), RetrieverPipeline, RetrieverState        │
│    ChunkFetcherStep, BM25ScorerStep, MemberFetcherStep,          │
│    DenseFetcherStep, DenseScorerStep, PreFilterStep,             │
│    TopKFilterStep, MetadataPostFilterStep, LimitStep,            │
│    TokenBudgetStep, RouteStep, ConditionalStep, ParallelStep,    │
│    RRFFusionStep                                                 │
│    ResultFormatter, PredicateRegistry                            │
│    AppConfig (pydantic-settings + YAML)                          │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  storage/ — Ports (Protocols) + Adapters (concrete)              │
│    ChunkStore, PackageStore, ModuleMemberStore,                  │
│    DocumentTreeStore, ReferenceStore, Embedder, ResultFuser      │
│    TextSearchable, VectorSearchable, HybridSearchable            │
│    UnitOfWork, ConnectionProvider                                │
│    Filter tree + FilterFormat + FilterAdapter + MetadataSchema   │
│    Sqlite* + TurboQuantStore + HybridSqliteTurboStore adapters   │
│    SqliteUnitOfWork + TurboQuantUnitOfWork + CompositeUnitOfWork │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  db.py + src/lib.rs                                              │
└──────────────────────────────────────────────────────────────────┘
```

Every layer boundary is a Protocol; every swappable component has a registry.

---

## A. Storage backends

Each new backend implements the Protocol combinations it supports. **Zero modification of existing code.**

| Extension | What to implement | Estimated size |
|---|---|---|
| `QdrantVectorStore` | `ChunkStore + TextSearchable + VectorSearchable + HybridSearchable` + `QdrantFilterAdapter` + optional `QdrantUnitOfWork` | ~400 LOC |
| `ChromaVectorStore` | `ChunkStore + VectorSearchable` only (Chroma lacks first-class BM25) + `ChromaFilterAdapter` | ~300 LOC |
| `WeaviateVectorStore` | `ChunkStore + TextSearchable + VectorSearchable + HybridSearchable` + `WeaviateFilterAdapter` | ~400 LOC |
| `ElasticsearchVectorStore` | `ChunkStore + TextSearchable + VectorSearchable + HybridSearchable` + `ElasticsearchFilterAdapter` | ~400 LOC |
| `PineconeVectorStore` | `ChunkStore + VectorSearchable` + `PineconeFilterAdapter` | ~300 LOC |
| `RedisVectorStore` (RediSearch) | `ChunkStore + TextSearchable + VectorSearchable + HybridSearchable` | ~400 LOC |
| `SqliteVecVectorStore` | Alternative dense backend keeping vectors in SQLite (via `sqlite-vec`) instead of the shipped TurboQuant `.tq` sidecar — `ChunkStore + VectorSearchable` + reuses the existing `Embedder` + `DenseScorerStep` | ~150 LOC |
| `MilvusVectorStore` | `ChunkStore + VectorSearchable` + adapter | ~300 LOC |

Wiring: instantiate in `server.py` startup; reference by config path.

## B. Filter system

| Extension | What to implement |
|---|---|
| New filter operator in `MultiFieldFormat` (e.g., `regex`, `gte`, `startswith`) | Extend `MultiFieldFormat._parse_field`; add matching `FilterAdapter` cases |
| New filter tree node type (e.g., `FieldRange(field, lo, hi)`, `FieldExists(field)`, `GeoWithin(field, bbox)`) | Frozen dataclass + `FilterAdapter` case in each backend |
| New user-facing filter format (e.g., `ChromaFormat`, `QdrantFormat`, `ElasticsearchFormat`, `FilterTreeFormat`) | Class implementing `FilterFormat` Protocol + registry entry + enum value in `MetadataFilterFormat` |
| New backend filter adapter | Class implementing `FilterAdapter` Protocol — translates `Filter` tree to backend native |

## C. Retrieval pipeline

Every primitive subclasses `RetrieverStep` (ABC) and uses **registry + decorator**. One class + one `@step_registry.register("name")` line + optional YAML preset. (`step_registry` holds `RetrieverStep` subclasses keyed by their YAML `type:` string. The `extraction/` pipeline has its own separate `stage_registry` — see CLAUDE.md §"Naming: retrieval vs ingestion pipelines".)

| Extension | Register via |
|---|---|
| New pipeline step (`TimedStep`, `CachingStep`, `ExplainStep`, `DedupeStep`, `LoopStep`, ...) | `@step_registry.register("name")` |
| New fetcher / scorer step (`DenseScorerStep`, `HyDEFetcherStep`, `SemanticRouterStep`, `KeywordBoostScorerStep`, ...) — replaces the old `Retriever` Protocol hierarchy with sklearn-shaped composition | `@step_registry.register("name")` |
| New formatter (`JsonFormatter`, `CompactMarkdownFormatter`, `CitationFormatter`, `XmlFormatter`, ...) | `@formatter_registry.register("name")` |
| New conditional predicate (`is_code_like_query`, `has_high_relevance`, `feature_flag_enabled`, ...) | `@predicate("name")` decorator |
| New fusion algorithm (`DistributionBasedScoreFusionStep`, `BordaCountFusionStep`) | `@step_registry.register("name")` |
| New pipeline blueprint | YAML file under `python/pydocs_mcp/pipelines/` or a user path; referenced from `pydocs-mcp.yaml`. Schema: top-level `name:` + `steps:` list, each entry has `name:` + `type:` + `params:` |

## D. Configuration

| Extension | Where |
|---|---|
| New `AppConfig` field | `retrieval/config.py` — pydantic-settings handles YAML + env-var overrides automatically |
| New metadata schema (retriever-category field allowlist) | `AppConfig.metadata_schemas[<name>]` — zero code change, YAML edit only |
| New pipeline route | Add entry under `pipelines.<handler>` in `pydocs-mcp.yaml` |
| New env-var override | Already wired: `PYDOCS_<FIELD>` via pydantic-settings |

## E. Domain model

| Extension | Impact |
|---|---|
| Add a field to `Chunk` / `ModuleMember` / `Package` (e.g., `Chunk.embedding`) | Additive — old callers keep working; schema DDL in `db.py` gains a column; `PRAGMA user_version` bump triggers auto-rebuild |
| New `SearchScope` / `ChunkOrigin` / `MemberKind` enum value | Add to enum; `StrEnum` values are plain strings; zero changes elsewhere |
| New domain entity (e.g., `Embedding`, `Annotation`, `Conversation`) | New Protocol + concrete repository + new DDL in `db.py`, following the sub-PR #3 pattern |
| New operator in `MultiFieldFormat` | Parser branch + `FilterAdapter` case(s) |

## F. MCP / CLI surface

| Extension | Where |
|---|---|
| New MCP tool | `server.py` — a new `@mcp.tool()` handler; thin wrapper over a use case from `application/` |
| New CLI subcommand | `__main__.py` — argparse subcommand + use case call |

---

## Cost reference (LOC to add each extension)

| Extension | LOC | Files touched | Existing code changes |
|---|:---:|:---:|---|
| New pipeline step | ~30 | 1 new class subclassing `RetrieverStep` | None |
| New predicate | ~3 | Existing `route_predicates.py` | None |
| New formatter | ~30 | 1 new class | None |
| New fetcher / scorer step | ~60 | 1 new class subclassing `RetrieverStep` (replaces the old "new retriever" pattern) | None |
| New pipeline preset (YAML) | ~20 | 1 new YAML file (`steps:` schema with `name:` per step) | None |
| New filter operator in `MultiFieldFormat` | ~10 | `filters.py` + each `FilterAdapter` | Backends that don't support it keep raising `NotImplementedError` |
| New filter tree node type | ~10 | `filters.py` + each `FilterAdapter` | Every `FilterAdapter` gains one case |
| New filter format (`ChromaFormat`, etc.) | ~80 | 1 new class + registry entry | None |
| New vector-store backend | ~400 | 1 new file in `storage/` + `FilterAdapter` | `server.py` startup wires it |
| New `UnitOfWork` | ~80 | 1 new file | `server.py` startup |
| New `AppConfig` field | ~3 | `retrieval/config.py` | None |
| New MCP tool | ~40 | `server.py` + use case in `application/` | None |

---

## Known coupling limits (deliberate)

These are the walls — the current architecture accepts them as trade-offs rather than walling off.

| Thing | Why it's coupled | When to revisit |
|---|---|---|
| Schema DDL | Lives in `db.py`; version bumps trigger full rebuild | A future PR may extract a `SchemaManager` if per-repo DDL becomes worth it |
| Rust-side `ModuleMember` shape | Defined in `src/lib.rs` as `#[pyclass]` | Only parser output; `ApiDefinition`-style richer types could live purely Python-side |
| `SqliteFilterAdapter.safe_columns` is per-repo | Cross-table JOIN filters would need a `plan()` method | Tracked as a future extension; see the filter-adapter docstring |
| Cross-backend atomicity | Physics — no single backend supports all stores' native transactions | `CompositeUnitOfWork` with best-effort rollback, when needed |
| `SearchQuery` is single-query | Multi-query (find A and B simultaneously) requires a different model | Additive: add a `SearchQuery.sub_queries` if ever needed |
| Three entity types (`Chunk`, `ModuleMember`, `Package`) | Adding a fundamentally new entity requires a new Protocol + repository + schema | Standard extension work, just scope |

---

## Suggested test extensions — picks for a future sub-PR

Each is small enough to land in one focused PR and exercises the abstractions end-to-end. Ordered by complexity.

### Tier 1 — tiny PRs (~50–150 LOC)

1. **Add `CompactMarkdownFormatter`** — a `ResultFormatter` that renders matches more densely for token-constrained MCP responses. Exercises the `formatter_registry` + YAML preset path.

2. **Add `is_code_like_query` predicate** — heuristic that detects `self.`, `.__`, parentheses in the query terms. Exercises the `@predicate` decorator + `RouteStep` composition.

3. **Add `TimedStep(inner)`** — a decorator step that logs duration. Exercises the uniform `RetrieverStep` ABC + the "compound step" pattern (wraps another step). Pipeline-IS-a-Step composition means it can wrap a whole sub-pipeline.

4. **Add `JsonResultFormatter`** — output as JSON for tooling that wants structured results. Pair with a `json_chunk` YAML preset.

5. **Add `WeightedScoreInterpolationStep`** — alternative fusion to the shipped `RRFFusionStep`. Normalizes each branch's scores to `[0, 1]` (min-max), then blends via `α·norm(bm25) + (1-α)·norm(dense)`. RRF discards score magnitude; this preserves it, which sometimes wins when one retriever is dramatically stronger than the other on a given query. Reads from the same `state.scratch[<branch>.ranked]` keys RRF uses, so it drops in as a YAML swap. Pairs naturally with `ConditionalStep` for per-query-type routing (e.g., RRF for short queries, weighted interpolation for long).

### Tier 2 — small PRs (~200–400 LOC)

5. **Add `FilterTreeFormat`** — full dict-form filter with `$and` / `$or` / `$not`. Lights up `Any_` and `Not` in the `SqliteFilterAdapter`. First non-multifield format; validates the two-format architecture.

6. **Add `TryStep(inner, on_error=None)`** — the first error-tolerance primitive (originally planned for sub-PR #7). Exercises step-wrapper composition + the "steps propagate exceptions" contract.

7. **Add `FieldRange(field, lo, hi)` filter node** — validates that the Filter tree extends cleanly; `SqliteFilterAdapter` gains a `BETWEEN` translation. Would support queries like "chunks indexed in the last 7 days" once a timestamp field lands.

8. **Add `CachingStep(inner, cache)` with a simple LRU** — decorator that memoizes step output on query hash. Validates the compound-step pattern under real load.

### Tier 3 — medium PRs (~400–800 LOC)

N. **Add capability-aware ingestion (`REQUIRES` declarations + auto-derivation)** — couple the ingestion pipeline to retrieval needs without coupling code. Each `RetrieverStep` gains a class-level `REQUIRES: ClassVar[frozenset[str]]` declaring what storage shapes it reads at query time (e.g., `BM25ScorerStep` → `{"chunks", "chunks_fts"}`; `DenseScorerStep` → `{"chunks", "chunk_embeddings"}`; `LlmTreeReasoningStep` → `{"chunks", "document_trees"}`). A new `derive_ingestion_capabilities(retrieval_yaml_path)` walks the active retrieval YAML, unions every step's `REQUIRES`, and `build_ingestion_pipeline()` conditionally assembles stages based on the result — `FlattenStage` runs only when `"chunks"` is needed, `EmbedChunksStage` only when `"chunk_embeddings"` is needed, etc.

    Wins:

    - **Zero-mismatch by construction** — ingestion automatically produces exactly the storage shapes the active retrieval pipeline will read. Switching retrieval YAMLs triggers a re-index only when the new YAML's `REQUIRES` is a strict superset. No more "I set `tree_only.yaml` and BM25 returns nothing" support burden.
    - **Tree-only deployments save real money** — skipping `EmbedChunksStage` zeros out the FastEmbed ONNX inference (or OpenAI embedding spend) at index time. On a 100-dep project this is the difference between "indexes in 30s" and "indexes in 5 minutes + costs ~$2 on OpenAI" — a meaningful unlock for users who go all-in on LLM tree reasoning.
    - **Self-documenting** — reading any step's `REQUIRES = frozenset({...})` line tells you exactly which storage shapes it consumes. New contributors get capability wiring right without reading the ingestion pipeline.
    - **Composes with `pipeline_hash`** (shipped in the chunk-cache work) — extend the hash input to include the capability set so switching retrieval profiles auto-invalidates the index when the new profile needs strictly more storage. No manual `--force` required after a profile flip.
    - **Forces honest step design** — a step that secretly reads from `uow.chunks` but doesn't declare it in `REQUIRES` becomes a code-review issue. Encourages single-responsibility.

    Implementation notes:

    - Add `REQUIRES: ClassVar[frozenset[str]] = frozenset()` to the `RetrieverStep` ABC (default empty → backward-compatible).
    - Override `REQUIRES` on every shipped step (~15 step classes, ~1 line each).
    - Add `derive_ingestion_capabilities(yaml_path)` in `extraction/factories.py` (~50 LOC + tests).
    - Refactor `build_ingestion_pipeline` to conditionally assemble stages from the capability set (~50 LOC).
    - Extend `compute_ingestion_pipeline_hash` to include `sorted(capabilities)` in the hash input (~10 LOC).
    - Lint rule (or test) that fails when a step's `run()` reads `state.scratch` / `uow.X` without declaring the matching capability (catches regressions).

    Pairs well with the `LlmTreeReasoningStep` PR — once that lands, this is the natural follow-up that makes `tree_only.yaml` deployments stop paying for embeddings they never use.

9. **Add `SqliteVecVectorStore`** — alternative dense backend that keeps vectors in SQLite (via `sqlite-vec`) instead of the shipped TurboQuant `.tq` sidecar. The dense plumbing (`Chunk.embedding`, `Embedder` Protocol, `FastEmbedEmbedder`, `OpenAIEmbedder`, `DenseScorerStep`, `DenseFetcherStep`, `HybridSqliteTurboStore`) already exists; this swaps the storage layer to make `.db` self-contained. Useful for deployments that prefer a single-file index.

10. **Add `QdrantVectorStore`** with the full `ChunkStore + TextSearchable + VectorSearchable + HybridSearchable` stack. First full backend swap — validates that nothing above the storage layer changes.

11. **Add `ChromaVectorStore` with `ChunkStore + VectorSearchable` only** — tests that Option C (split Protocols) handles the "not all backends support every capability" case cleanly. Demonstrates the type-checker-level protection against wiring a BM25 fetcher step to a Chroma store.

12. **Add `LlmRerankStep` with an OpenAI/Anthropic/Cohere client** — validates that an I/O-bound step composes with the pipeline. Tests predicate-guarded execution (skip rerank on short queries). The planned LLM-rerank step listed in §C "Retrieval pipeline".

13. **Add `LlmTreeReasoningStep` (vectorless RAG, PageIndex-style)** — a `RetrieverStep` that uses an LLM to navigate the existing `DocumentNode` trees and pick the nodes most likely to contain the answer, without embedding. Single-shot prompt: serialize the tree via `DocumentNode.to_pageindex_json()` (strip body text, keep titles + summaries + node_ids), send `(query, tree_json)` to a configured LLM, parse `{"thinking", "node_list": [node_id, ...]}` from the response, then fetch the corresponding chunks via `uow.chunks` and emit them as the step's result.

    Composes with the existing pipeline machinery:

    - **In parallel with hybrid** — `ParallelStep` branches: one runs BM25 + dense + RRF, the other runs tree reasoning; downstream `RRFFusionStep` (or the planned `WeightedScoreInterpolationStep`) fuses by `branch_keys=("hybrid.ranked", "tree.ranked")`.
    - **After hybrid** — `ConditionalStep` triggers tree reasoning only on long or structural queries (`is_long_query` predicate), using the hybrid result as a candidate filter.
    - **Standalone** — a YAML preset that skips dense entirely; useful for long-doc corpora where TOC carries signal (PageIndex cites 98.7% on FinanceBench).

    Reuses the storage layer already in place: `BuildContext.uow_factory` threads through, the step opens `async with uow_factory()` and reads `uow.trees` (same pattern as `LoadExistingChunkHashesStage` from the chunk-cache work).

    New abstractions added by this PR:

    - `LlmClient` Protocol in `storage/protocols.py` mirroring `Embedder` but exposing BOTH `async chat()` and `chat_sync()` — LLM calls surface in more contexts than embedding calls. First concrete: `OpenAiLlmClient` using `openai>=1.40` (already a required dep, no new dependencies). New providers (Anthropic, Gemini, LiteLLM) land as one-file additions per SOLID open/closed.
    - `LlmConfig` sub-model in `retrieval/config.py` mirroring `EmbeddingConfig` (`provider: Literal["openai", ...]`, `model_name`, `temperature`, `max_tokens`).
    - Two Jinja2 prompt templates under `python/pydocs_mcp/retrieval/prompts/`: `tree_reasoning_pageindex_v1.j2` (verbatim PageIndex baseline) and `tree_reasoning_pydocs_v1.j2` (adapted for code-doc queries). Prompts are versioned (`_vN` suffix); selected at runtime via a `prompt_template` dataclass field on the step. Versioned prompts make A/B comparison and rollback a YAML edit instead of a code change.

    Inspired by [VectifyAI/PageIndex](https://github.com/VectifyAI/PageIndex) (MIT). **Re-implemented locally — no `pageindex` package dependency.** The single-shot algorithm is small (one Jinja2 prompt + one `json.loads` + one chunk fetch through the existing `uow.chunks`); vendoring the logic ≈30 LOC, avoids growing the install surface (already +90 MB after the FastEmbed/OpenAI promotion), and keeps prompt versioning + reproducibility in our repo instead of someone else's release schedule. PageIndex isn't on PyPI under `pageindex` anyway (`pageindex-rs` is an unrelated Rust port).

### Tier 4 — larger PRs (~800+ LOC)

14. **Add `CompositeUnitOfWork`** — coordinates multiple backend UoWs with best-effort rollback. Enables heterogeneous setups (e.g., Qdrant chunks + SQLite packages). Tests the heterogeneous-backends story.

15. **Add `HyDERetriever`** (Hypothetical Document Embeddings) — asks an LLM to draft a hypothetical answer, embeds it, then does vector search. Exercises retriever composition (LLM client + embedder + vector store).

16. **Add config-driven pipeline reloading** — watch the YAML file; rebuild pipelines on change without restart. Tests the serialization boundary (dict round-trip).

---

## How to propose a test-extension PR

1. Pick one extension from the list above (or a combination).
2. Brainstorm the spec in a session: clarifying questions → design sections → spec file under `docs/superpowers/specs/YYYY-MM-DD-<topic>.md`. Match the shape of the existing specs in that directory.
3. The spec template (see existing specs for reference) covers: Goal, Decisions, Scope, Domain components, Files touched, Risks, Acceptance criteria, Open items.
4. Acceptance criteria should include at least one test that verifies the extension integrates at a seam (e.g., "a pipeline YAML referencing the new stage round-trips through `to_dict` / `from_dict` and executes end-to-end against a fixture").
