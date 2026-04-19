# Extension Points

**Context:** this document describes the extensibility surface of pydocs-mcp **after sub-PRs #1, #2, and #3 land** (see `docs/superpowers/specs/`). Until then, some extension hooks listed below exist only as Protocol declarations; the complete surface activates once the three planned PRs ship.

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
│    IndexingService (sub-PR #3)                                   │
│    SearchDocsService, PackageLookupService, ... (sub-PR #4+)     │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  retrieval/ — Pipeline + stages + retrievers + formatters        │
│    CodeRetrieverPipeline, PipelineStage, RouteStage, ...         │
│    ChunkRetriever / ModuleMemberRetriever (Retriever hierarchy)  │
│    ResultFormatter, PredicateRegistry                            │
│    AppConfig (pydantic-settings + YAML)                          │
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│  storage/ — Ports (Protocols) + Adapters (concrete)              │
│    ChunkStore, PackageStore, ModuleMemberStore                   │
│    TextSearchable, VectorSearchable, HybridSearchable            │
│    UnitOfWork, ConnectionProvider                                │
│    Filter tree + FilterFormat + FilterAdapter + MetadataSchema   │
│    Sqlite* concrete implementations                              │
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
| `SqliteVecVectorStore` | Extends `SqliteVectorStore` with `VectorSearchable` via `sqlite-vec` | ~150 LOC |
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

Every primitive uses **registry + decorator**. One class + one `@register("name")` line + optional YAML preset.

| Extension | Register via |
|---|---|
| New pipeline stage (`TimedStage`, `CachingStage`, `ExplainStage`, `DedupeStage`, `LoopStage`, ...) | `@stage_registry.register("name")` |
| New retriever strategy (`DenseChunkRetriever`, `NativeHybridChunkRetriever`, `HyDERetriever`, `SemanticRouterRetriever`, `KeywordBoostRetriever`, ...) | `@retriever_registry.register("name")` |
| New formatter (`JsonFormatter`, `CompactMarkdownFormatter`, `CitationFormatter`, `XmlFormatter`, ...) | `@formatter_registry.register("name")` |
| New conditional predicate (`is_code_like_query`, `has_high_relevance`, `feature_flag_enabled`, ...) | `@predicate("name")` decorator |
| New fusion algorithm (`WeightedSumFusionStage`, `DistributionBasedScoreFusionStage`, `BordaCountFusionStage`) | `@stage_registry.register("name")` |
| New pipeline preset | YAML file under `python/pydocs_mcp/presets/` or a user path; referenced from `pydocs-mcp.yaml` |

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
| New pipeline stage | ~30 | 1 new class | None |
| New predicate | ~3 | Existing `predicates.py` | None |
| New formatter | ~30 | 1 new class | None |
| New retriever | ~60 | 1 new class | None |
| New pipeline preset (YAML) | ~20 | 1 new YAML file | None |
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
| Schema DDL | Lives in `db.py`; version bumps trigger full rebuild | Sub-PR #5 may extract a `SchemaManager` if per-repo DDL becomes worth it |
| Rust-side `ModuleMember` shape | Defined in `src/lib.rs` as `#[pyclass]` | Only parser output; `ApiDefinition`-style richer types could live purely Python-side |
| `SqliteFilterAdapter.safe_columns` is per-repo | Cross-table JOIN filters would need a `plan()` method | Declared as future extension in sub-PR #3 §13 |
| Cross-backend atomicity | Physics — no single backend supports all stores' native transactions | `CompositeUnitOfWork` with best-effort rollback, when needed |
| `SearchQuery` is single-query | Multi-query (find A and B simultaneously) requires a different model | Additive: add a `SearchQuery.sub_queries` if ever needed |
| Three entity types (`Chunk`, `ModuleMember`, `Package`) | Adding a fundamentally new entity requires a new Protocol + repository + schema | Standard extension work, just scope |

---

## Suggested test extensions — picks for a future sub-PR

Each is small enough to land in one focused PR and exercises the abstractions end-to-end. Ordered by complexity.

### Tier 1 — tiny PRs (~50–150 LOC)

1. **Add `CompactMarkdownFormatter`** — a `ResultFormatter` that renders matches more densely for token-constrained MCP responses. Exercises the `formatter_registry` + YAML preset path.

2. **Add `is_code_like_query` predicate** — heuristic that detects `self.`, `.__`, parentheses in the query terms. Exercises the `@predicate` decorator + `RouteStage` composition.

3. **Add `TimedStage(inner)`** — a decorator stage that logs duration. Exercises the uniform `PipelineStage` protocol + the "compound stage" pattern (wraps another stage).

4. **Add `JsonResultFormatter`** — output as JSON for tooling that wants structured results. Pair with a `json_chunk` YAML preset.

### Tier 2 — small PRs (~200–400 LOC)

5. **Add `FilterTreeFormat`** — full dict-form filter with `$and` / `$or` / `$not`. Lights up `Any_` and `Not` in the `SqliteFilterAdapter`. First non-multifield format; validates the two-format architecture.

6. **Add `TryStage(stage, on_error=None)`** — the first error-tolerance primitive (originally planned for sub-PR #7). Exercises stage-wrapper composition + the "stages propagate exceptions" contract.

7. **Add `FieldRange(field, lo, hi)` filter node** — validates that the Filter tree extends cleanly; `SqliteFilterAdapter` gains a `BETWEEN` translation. Would support queries like "chunks indexed in the last 7 days" once a timestamp field lands.

8. **Add `CachingStage(inner, cache)` with a simple LRU** — decorator that memoizes `retrieve()` results on query hash. Validates the compound-stage pattern under real load.

### Tier 3 — medium PRs (~400–800 LOC)

9. **Add `SqliteVecVectorStore`** — wire `sqlite-vec` to get `VectorSearchable` on the existing SQLite backend. Requires adding `Chunk.embedding` field (and its migration) + an embedder. First real dense-retrieval path; tests the whole vector-search pipeline.

10. **Add `QdrantVectorStore`** with the full `ChunkStore + TextSearchable + VectorSearchable + HybridSearchable` stack. First full backend swap — validates that nothing above the storage layer changes.

11. **Add `ChromaVectorStore` with `ChunkStore + VectorSearchable` only** — tests that Option C (split Protocols) handles the "not all backends support every capability" case cleanly. Demonstrates the type-checker-level protection against wiring a BM25 retriever to a Chroma store.

12. **Add `LlmRerankStage` with an OpenAI/Anthropic client** — validates that an I/O-bound stage composes with the pipeline. Tests predicate-guarded execution (skip rerank on short queries).

### Tier 4 — larger PRs (~800+ LOC)

13. **Add `CompositeUnitOfWork`** — coordinates multiple backend UoWs with best-effort rollback. Enables heterogeneous setups (e.g., Qdrant chunks + SQLite packages). Tests the heterogeneous-backends story.

14. **Add `HyDERetriever`** (Hypothetical Document Embeddings) — asks an LLM to draft a hypothetical answer, embeds it, then does vector search. Exercises retriever composition (LLM client + embedder + vector store).

15. **Add config-driven pipeline reloading** — watch the YAML file; rebuild pipelines on change without restart. Tests the serialization boundary (dict round-trip).

---

## How to propose a test-extension sub-PR

1. Pick one extension from the list above (or a combination).
2. Brainstorm the spec in a session following the same pattern used for sub-PRs #1–#3: clarifying questions → design sections → spec file under `docs/superpowers/specs/YYYY-MM-DD-<topic>.md`.
3. The spec template (see existing specs for reference) covers: Goal, Decisions, Scope, Domain components, Files touched, Risks, Acceptance criteria, Open items.
4. Acceptance criteria should include at least one test that verifies the extension integrates at a seam (e.g., "a pipeline YAML referencing the new stage round-trips through `to_dict` / `from_dict` and executes end-to-end against a fixture").
