# Hybrid Search with Semantic Embeddings — Design Spec

**Status:** Draft, awaiting user review.
**Tracking issue:** [#6 — feat: hybrid search with semantic embeddings](https://github.com/msobroza/pydocs-mcp/pull/6).
**Companion plan:** to be written via `superpowers:writing-plans` after this spec is approved.
**Branch:** `feature/hybrid-search-with-semantic-embeddings` (off `main` @ `f717bf3`).

---

## 1. Goal

Land the **infrastructure** for hybrid (BM25 + dense) search in pydocs-mcp without committing to a specific embedding model or capturing a real benchmark baseline. After this PR:

- A user can opt into hybrid retrieval via YAML (`embedding.provider: fastembed|openai` + `pipelines.chunk.routes[0].pipeline_path: pipelines/chunk_search_hybrid.yaml`) and the pipeline assembles + runs end-to-end against either a real model or a mock.
- Adding a different vector backend (Qdrant, Chroma, etc.) is a pure adapter change — no plumbing churn.
- Adding a different embedder (Cohere, Voyage, ColBERT) is one new file + one branch in the `build_embedder` factory.
- The follow-up "model selection + real benchmarks" PR can sweep candidate embedders against RepoQA / CodeRAG-Bench and lock the recommended default. **Nothing in this PR prejudges that choice.**

Acceptance is infrastructure-level: every embedding-touching test uses a `MockEmbedder`; no real model is loaded in CI; the existing BM25-only baseline SHA stays unchanged.

## 2. Context

The previous PR (`Cleanups + PR-A: chunk_search_ranked.yaml preset`, merged as `f717bf3`) shipped the headline finding **recall@10 = 18.0% [11%, 26%] on real RepoQA-100 (Python subset)** for BM25-only retrieval against `chunk_search_ranked.yaml`. That number is the BM25 ceiling — every roadmap benchmark (CodeRAG-Bench, DocPrompting, SWE-bench-retrieval) assumes a hybrid retriever will move it up. The new `plot_metric_vs_latency` Pareto-scatter chart in `benchmarks/eval/plotting.py` currently shows one dot waiting for a second baseline.

This PR doesn't capture that second baseline — it ships the **infrastructure** so the follow-up model-selection PR can. The split prevents this PR from prejudging which embedding model wins (which would require running real benchmarks across candidates first).

## 3. Locked decisions

All five architectural decisions plus the eight open-item defaults were locked during the plan-mode review (see `/Users/msobroza/.claude/plans/i-would-like-to-squishy-sun.md`). Restated here for spec-internal completeness.

### 3.1 Architecture

| Decision | Choice | Rationale |
|---|---|---|
| **Vector-store composition** | Three classes implementing three Protocols: `SqliteVectorStore` (existing, `TextSearchable`), `TurboQuantVectorStore` (NEW, `VectorSearchable`), `HybridSqliteTurboStore` (NEW, `HybridSearchable`, composes the two + a `ResultFuser`) | SOLID. Adding `QdrantVectorStore` later = one new class; `HybridSqliteTurboStore(text=Sqlite…, vector=Qdrant…)` works unchanged. |
| **`Chunk.embedding` field** | Typed `embedding: Embedding \| None` where `Embedding = Vector \| MultiVector`. Single-vector embedders return `Vector`; multi-vector embedders return `MultiVector`. No forced wrapping. | Type-discoverable via `is_multi_vector(emb)` helper. Storage path single-vector only this PR; multi-vector inputs raise clear `NotImplementedError`. |
| **Pipeline integration** | `ParallelStep([bm25_chain, dense_chain]) → RRFFusionStep` — branches publish ranked lists to `scratch["<branch_name>.ranked"]`, fusion reads both. | Standard hybrid IR pattern. Requires refactoring `RRFStep` into a true multi-list fuser + a small `ParallelStep` contract clarification. |
| **`CompositeUnitOfWork`** | Full implementation now, load-bearing (two children: `SqliteUnitOfWork` + new `TurboQuantUnitOfWork`). Best-effort rollback semantics. | TurboQuant is a separate backend (`.tq` sidecar file). Single-UoW coordination is the only path that keeps `uow_factory` consumers untouched. |
| **`Embedder` Protocol** | Single Protocol exposing `embed_query(text) -> Embedding` + `embed_chunks(texts) -> tuple[Embedding, ...]`. Same instance serves retrieval + ingestion. No registry — explicit `build_embedder(cfg)` factory. | Per user revision. Concrete classes (`FastEmbedEmbedder`, `OpenAIEmbedder`) implement both methods. Adding new providers = one file + one `if` branch. |

### 3.2 Open-item defaults (user-confirmed via brainstorm)

| Item | Locked default | Notes |
|---|---|---|
| TurboQuant `bit_width` | **4** | Sweet spot per turbovec README — ~16× compression vs FP32 with minimal recall loss on standard embedding sizes (384–1536 dim). YAML-tunable. |
| FastEmbed default model | **`BAAI/bge-small-en-v1.5`** | 384 dim, ~33 MB ONNX. Canonical FastEmbed default; fast on CPU. |
| OpenAI default model | **`text-embedding-3-small`** | 1536 dim (configurable down via `dimensions` param). 5× cheaper than legacy ada-002, near-equivalent quality. |
| CompositeUoW integrity policy | **Log + auto-rebuild affected packages** | When `chunks.count != IdMapIndex.size()` at startup, log warning, mark affected packages dirty, next index run re-extracts + re-embeds them. Cache is regenerable. |
| Re-embed-on-model-change | **Auto via stored `packages.embedding_model`** | On commit, store the model identifier per-package. On next-run open, if YAML's `embedding.model_name` differs from any package's stored model, mark that package dirty + force re-embed. Re-uses the existing `--force` code path. |
| RRF `k` constant | **60** | Literature default; matches existing `_DEFAULT_K = 60` in `retrieval/steps/rrf.py`. YAML-tunable. |
| MCP default pipeline | **Stays `chunk_search.yaml` (BM25 composite)** | No flip in this PR. Flip is a separate one-line PR once benchmarks justify it. |
| `OPENAI_API_KEY` fail-fast | **At server startup** if `embedding.provider: openai` selected + key absent | Loud + early, easier to diagnose than a first-query 500. |

## 4. Scope

### 4.1 In scope

- New Protocols: `Embedder`, `ResultFuser` in `storage/protocols.py`.
- New type aliases: `Vector`, `MultiVector`, `Embedding`, and `is_multi_vector()` helper in `models.py`.
- New `Chunk.embedding: Embedding | None` field (typed, additive, default `None`).
- New concrete classes:
  - `TurboQuantVectorStore` — implements `VectorSearchable` against `turbovec.IdMapIndex`.
  - `HybridSqliteTurboStore` — implements `HybridSearchable`, composes a text store + a vector store + a `ResultFuser`.
  - `TurboQuantUnitOfWork` — wraps the `IdMapIndex` lifecycle (load on enter, accumulate adds, write on commit, reload on rollback).
  - `CompositeUnitOfWork` — best-effort coordinator over N child UoWs.
  - `FastEmbedEmbedder` — implements `Embedder` via `fastembed.TextEmbedding`. Optional `[fastembed]` extra.
  - `OpenAIEmbedder` — implements `Embedder` via OpenAI `/v1/embeddings`. Optional `[openai]` extra.
  - `RRFResultFuser` — implements `ResultFuser` using reciprocal-rank fusion with `k=60` default.
- New retrieval steps:
  - `DenseFetcherStep` — reads `scratch["pre_filter.result"]` → SQL pre-filter → candidate IDs → `IdMapIndex.search(..., allowlist=…)`.
  - `DenseScorerStep` — embeds query + computes cosine sim per candidate (mirrors `bm25_scorer.py`).
- Retrieval refactors:
  - `RRFStep` → `RRFFusionStep` — multi-list fuser (reads N ranked lists from `scratch`, writes fused to `state.candidates`).
  - `ParallelStep` contract: branches publish to `scratch[<branch_name>.<field>]`; remove the implicit `state.result` merge as the only termination.
- New ingestion stage: `embed_chunks` between `flatten` and `content_hash` in `pipelines/ingestion.yaml`.
- New factory: `build_embedder(cfg) -> Embedder` in `extraction/strategies/embedders/__init__.py`.
- New factory: `build_composite_uow_factory(children) -> Callable[[], UnitOfWork]` in `storage/factories.py`.
- Schema migration v4 → v5: add `packages.embedding_model TEXT` column. NO `chunks.embedding` column, NO `chunks_vec` virtual table.
- New YAML pipeline presets:
  - `chunk_search_dense.yaml` (composite output)
  - `chunk_search_dense_ranked.yaml` (ranked output)
  - `chunk_search_hybrid.yaml` (composite output)
  - `chunk_search_hybrid_ranked.yaml` (ranked output)
- New config: `AppConfig.embedding: EmbeddingConfig` (`provider`, `model_name`, `batch_size`, `bit_width`, `dim`).
- New dep: `turbovec` (main deps); `fastembed`, `openai` as optional extras `[fastembed]`, `[openai]`, `[all-embedders]`.
- New test double: `MockEmbedder` in `tests/_fakes.py` — deterministic numpy-RNG vectors, configurable `dim`.
- Storage layout: `~/.pydocs-mcp/{dirname}_{path_hash}.tq` sidecar alongside the existing `.db`.
- Housekeeping (the "#4" ride-along):
  - CLAUDE.md "README files: no internal PR / sub-PR / task jargon" — extend forbidden-patterns + audit regex to catch `PR-[A-Z][0-9.]+`-style labels.
  - `benchmarks/README.md` — replace the five `PR-B3.1` references with capability language.
- GitHub issue #6 body update.

### 4.2 Out of scope

- **Real embedding model invocation in tests or CI.** Every test uses `MockEmbedder`.
- **Hybrid baseline JSON capture** (`repoqa_snf_hybrid.json` etc.). Defer to follow-up model-selection PR.
- **Pareto-scatter / recall-lift acceptance criteria.** No quality assertions in this PR.
- **MCP default switch from `chunk_search.yaml` to `chunk_search_hybrid.yaml`.** Stays BM25-default.
- **Qdrant / Chroma / other vector backends.** Future PRs via the composition seam.
- **CodeRAG-Bench / DocPrompting / SWE-bench plugins.** Separate validation PRs.
- **Multi-vector storage** (ColBERT, late-interaction). Typed `Embedding` union accepts the shape; `TurboQuantUnitOfWork.add_vectors` raises `NotImplementedError` on length>1.
- **LLM-based reranking** (`LlmRerankStep`). Separate PR.
- **Indexing-throughput benchmark.** Separate PR.

## 5. Domain components

### 5.1 Types (in `pydocs_mcp/models.py`)

```python
Vector      = tuple[float, ...]
MultiVector = tuple[Vector, ...]
Embedding   = Vector | MultiVector


def is_multi_vector(emb: Embedding) -> bool:
    """True if `emb` is a sequence of vectors (ColBERT-style)."""
    return bool(emb) and isinstance(emb[0], tuple)


@dataclass(frozen=True)
class Chunk:
    text: str
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    embedding: Embedding | None = None     # NEW — additive, default None
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

`Chunk.embedding` is populated during **ingestion** (the embed stage writes it; the persistence layer hands it to `TurboQuantUnitOfWork.add_vectors`). On **read paths** it stays `None` — embeddings live in the `.tq` sidecar, the SQL row doesn't carry them back.

### 5.2 Protocols (in `pydocs_mcp/storage/protocols.py`)

`TextSearchable`, `VectorSearchable`, `HybridSearchable` already exist (see [storage/protocols.py:55,65,75](python/pydocs_mcp/storage/protocols.py:55)). Two new Protocols:

```python
class Embedder(Protocol):
    """One embedder serves both query and ingestion. Concrete classes
    return their natural shape: Vector for single-vector models;
    MultiVector for ColBERT-style. Use is_multi_vector(emb) to branch."""
    dim: int
    async def embed_query(self, text: str) -> Embedding: ...
    async def embed_chunks(self, texts: Sequence[str]) -> tuple[Embedding, ...]: ...


class ResultFuser(Protocol):
    """Combines N ranked Chunk lists into one fused ranking."""
    async def fuse(
        self,
        ranked_lists: Sequence[tuple[Chunk, ...]],
        *,
        limit: int,
    ) -> tuple[Chunk, ...]: ...
```

### 5.3 Concrete vector-store classes

- **`SqliteVectorStore`** (existing, [sqlite.py:634-703](python/pydocs_mcp/storage/sqlite.py:634)) implements `TextSearchable`. Unchanged.
- **`TurboQuantVectorStore`** (NEW) implements `VectorSearchable`. Wraps a `turbovec.IdMapIndex`. Constructor takes the index + a `CandidateIdResolver` callable + a `ChunkHydrator` callable — **no direct SQLite import** (keeps SOLID intact; see §7 risk row 1).
  - `vector_search(query_vector, limit, filter=None)`:
    1. If `filter is not None`: call `self._candidate_resolver(filter) -> np.ndarray[uint64]` to obtain the allowlist. The default resolver (constructed in `storage/factories.py`) runs `SELECT id FROM chunks WHERE <pre_filter_sql>` against the SQLite connection.
    2. Call `self._index.search(query_vector, k=limit, allowlist=allowlist)` (omit `allowlist=` when `filter is None`).
    3. Hydrate returned IDs to full `Chunk` objects via `self._chunk_hydrator(ids) -> tuple[Chunk, ...]`. The default hydrator runs `SELECT * FROM chunks WHERE id IN (...)`.
  - The two callables decouple the store from SQLite; a Qdrant or Postgres-backed candidate resolver would slot in via the same constructor.
- **`HybridSqliteTurboStore(text, vector, fuser)`** (NEW) implements `HybridSearchable`. `hybrid_search(query_terms, query_vector, limit, filter=None, *, alpha=0.5)`:
  - Concurrently call `text.text_search(query_terms, limit, filter)` + `vector.vector_search(query_vector, limit, filter)`.
  - Pass both ranked lists to `self._fuser.fuse([text_results, vec_results], limit=limit)`.
  - Note: `alpha` is accepted on the Protocol but `RRFResultFuser` ignores it (RRF is parameterless except for `k`); a future weighted-sum fuser would honor it.

### 5.4 `TurboQuantUnitOfWork`

```python
class TurboQuantUnitOfWork:
    def __init__(self, *, index_path: Path, dim: int, bit_width: int = 4):
        self._index_path = index_path
        self._dim = dim
        self._bit_width = bit_width
        self._index: IdMapIndex | None = None
        self._dirty = False

    async def __aenter__(self) -> "TurboQuantUnitOfWork":
        self._index = (
            IdMapIndex.load(self._index_path)
            if self._index_path.exists()
            else IdMapIndex(dim=self._dim, bit_width=self._bit_width)
        )
        return self

    async def add_vectors(
        self, ids: Sequence[int], embeddings: Sequence[Embedding],
    ) -> None:
        for emb in embeddings:
            if is_multi_vector(emb):
                raise NotImplementedError(
                    "Multi-vector embeddings are not yet persisted by "
                    "TurboQuantUnitOfWork. See <future PR> for the "
                    "chunk_vectors side-table design."
                )
        vectors = np.asarray(
            [emb for emb in embeddings], dtype=np.float32,
        )
        ids_arr = np.asarray(ids, dtype=np.uint64)
        self._index.add_with_ids(vectors, ids_arr)
        self._dirty = True

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        for chunk_id in ids:
            self._index.remove(chunk_id)
        self._dirty = True

    async def commit(self) -> None:
        if self._dirty:
            self._index.write(self._index_path)
            self._dirty = False

    async def rollback(self) -> None:
        # Discard in-memory adds by reloading.
        if self._dirty:
            self._index = (
                IdMapIndex.load(self._index_path)
                if self._index_path.exists()
                else IdMapIndex(dim=self._dim, bit_width=self._bit_width)
            )
            self._dirty = False

    def size(self) -> int:
        """Number of stored vectors (for the integrity check)."""
        return self._index.size() if self._index else 0
```

Exposes its `IdMapIndex` to `TurboQuantVectorStore` for read paths via a property; the store reads the index without going through UoW commit semantics (vector searches are pure reads).

### 5.5 `CompositeUnitOfWork`

```python
class CompositeUnitOfWork:
    """Best-effort coordinator over N child UoWs.

    Commit attempts each child sequentially. On any child commit failure:
    1. Log the original failure.
    2. Call rollback() on each child that already committed (best-effort —
       most backends including SQLite cannot un-commit, but TurboQuant
       can reload from its pre-commit on-disk state).
    3. Re-raise the original exception so the caller sees the failure.

    Attribute access (.packages / .chunks / .vectors / ...) delegates to
    whichever child declares that attribute. Names must be unique across
    children (e.g., SqliteUoW owns .packages; TurboQuantUoW owns .vectors).
    Ambiguity raises AttributeError at construction time.
    """

    def __init__(self, children: Sequence[UnitOfWork]): ...
    async def __aenter__(self) -> "CompositeUnitOfWork": ...
    async def __aexit__(self, *exc_info) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...
    def __getattr__(self, name: str) -> Any: ...
```

**Atomicity limitation (documented in module docstring):** if SQLite COMMIT succeeds and TurboQuant `.tq` write fails, SQLite cannot be un-committed. The startup integrity check (§5.7) detects the mismatch (`chunks.count != IdMapIndex.size()`) and triggers auto re-embed of affected packages.

### 5.6 `RRFFusionStep`

Replaces the existing single-list `RRFStep`. Reads N ranked lists from `state.scratch["<branch_name>.ranked"]` (key set by each parallel branch); computes RRF score per item as `sum(1 / (k + rank_in_list_i))` across lists; sorts descending; emits the fused ranking to `state.candidates`.

```python
@step_registry.register("rrf_fusion")
@dataclass(frozen=True, slots=True)
class RRFFusionStep(RetrieverStep):
    k: int = _DEFAULT_K                                    # 60
    branch_keys: tuple[str, ...] = ("bm25.ranked", "dense.ranked")
    name: str = field(default="rrf_fusion", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        rankings = [
            state.scratch.get(k, ())
            for k in self.branch_keys
            if k in state.scratch
        ]
        if not rankings:
            return state
        fused = _rrf_fuse(rankings, k=self.k)
        return replace(state, candidates=fused)
```

### 5.7 Composition root changes

In `storage/factories.py`:

```python
def build_composite_uow_factory(
    children: Sequence[Callable[[], UnitOfWork]],
) -> Callable[[], CompositeUnitOfWork]:
    return lambda: CompositeUnitOfWork([f() for f in children])


def turboquant_path_for_project(project_dir: Path) -> Path:
    """Mirror cache_path_for_project but with .tq suffix."""
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.tq"


def build_sqlite_plus_turboquant_uow_factory(
    db_path: Path, tq_path: Path, *, dim: int, bit_width: int,
) -> Callable[[], CompositeUnitOfWork]:
    provider = build_connection_provider(db_path)
    return build_composite_uow_factory([
        lambda: SqliteUnitOfWork(provider=provider),
        lambda: TurboQuantUnitOfWork(
            index_path=tq_path, dim=dim, bit_width=bit_width,
        ),
    ])
```

`server.py`, `__main__.py`, and the indexing-service factory swap their UoW factory builder to `build_sqlite_plus_turboquant_uow_factory(db_path, tq_path, dim=app_config.embedding.dim, bit_width=app_config.embedding.bit_width)`. Service consumers (`async with self.uow_factory() as uow:`) keep working — `uow.packages`, `uow.chunks`, `uow.vectors` all resolve through the composite's `__getattr__`.

**Startup integrity check** (runs once after `open_index_database` in `server.py` + `__main__.py` startup paths): open the composite UoW briefly; if `len(uow.chunks) != uow.vectors.size()`, log a warning + iterate the packages where the mismatch lives and clear their `content_hash` so the next index sweep re-extracts them.

### 5.8 Pipeline integration

`pipelines/chunk_search_hybrid.yaml`:

```yaml
name: chunk_search_hybrid
steps:
  - name: pre_filter
    type: pre_filter
    params:
      schema_name: chunk
      target_field: chunk
  - name: parallel
    type: parallel
    params:
      branches:
        - name: bm25
          steps:
            - {name: fetch, type: chunk_fetcher, params: {schema_name: chunk}}
            - {name: score, type: bm25_scorer, params: {}}
            - {name: filter, type: metadata_post_filter, params: {}}
            - {name: topk,   type: top_k_filter, params: {publish_to: "bm25.ranked"}}
        - name: dense
          steps:
            - {name: fetch, type: dense_fetcher, params: {schema_name: chunk}}
            - {name: score, type: dense_scorer, params: {}}
            - {name: filter, type: metadata_post_filter, params: {}}
            - {name: topk,   type: top_k_filter, params: {publish_to: "dense.ranked"}}
  - name: rrf_fusion
    type: rrf_fusion
    params: {k: 60, branch_keys: ["bm25.ranked", "dense.ranked"]}
  - name: limit
    type: limit
    params: {max_results: 8}
  - name: budget
    type: token_budget_formatter
    params: {formatter: {type: chunk_markdown}, budget: 2000}
```

The `*_ranked` variant drops `token_budget_formatter` so `state.candidates` carries the ranked list directly (matches `chunk_search_ranked.yaml` convention).

The dense-only presets (`chunk_search_dense.yaml`, `chunk_search_dense_ranked.yaml`) skip the parallel + fusion and run a linear dense chain.

**`TopKFilterStep.publish_to` is a new field** (optional, default `None`). When set, the step writes its output to `state.scratch[publish_to]` in addition to `state.candidates`. This is the contract by which parallel branches publish their ranked lists for `RRFFusionStep` to consume.

### 5.9 Ingestion stage

`extraction/pipeline/stages/embed_chunks.py`:

```python
@stage_registry.register("embed_chunks")
@dataclass(frozen=True, slots=True)
class EmbedChunksStage:
    embedder: Embedder
    batch_size: int = 32

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks:
            return state
        # Batch over state.chunks, calling embedder.embed_chunks per batch.
        all_embeddings: list[Embedding] = []
        for i in range(0, len(state.chunks), self.batch_size):
            batch = state.chunks[i:i + self.batch_size]
            embs = await self.embedder.embed_chunks([c.text for c in batch])
            all_embeddings.extend(embs)
        # Replace state.chunks with the embedded versions (Chunk is frozen).
        new_chunks = tuple(
            replace(c, embedding=emb)
            for c, emb in zip(state.chunks, all_embeddings)
        )
        return replace(state, chunks=new_chunks)
```

Slotted into `pipelines/ingestion.yaml` between `flatten` and `content_hash`. The `IndexingService` write path then hands `(chunk.id, chunk.embedding)` pairs to `uow.vectors.add_vectors(...)` alongside the `uow.chunks.upsert(...)` call.

### 5.10 Config

`pydocs_mcp/retrieval/config.py` gains:

```python
class EmbeddingConfig(BaseModel):
    provider:    Literal["fastembed", "openai"] = "fastembed"
    model_name:  str = "BAAI/bge-small-en-v1.5"
    dim:         int = 384
    batch_size:  int = Field(default=32, ge=1)
    bit_width:   int = Field(default=4, ge=1, le=8)   # TurboQuant quantization width

    @model_validator(mode="after")
    def _validate_dim(self) -> "EmbeddingConfig":
        # If a known model_name is set, validate dim matches (lookup table).
        ...
        return self


class AppConfig(BaseSettings):
    ...
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
```

`defaults/default_config.yaml`:

```yaml
embedding:
  provider:    fastembed
  model_name:  BAAI/bge-small-en-v1.5
  dim:         384
  batch_size:  32
  bit_width:   4
```

## 6. Files touched (representative paths)

See §"Files likely touched" of `/Users/msobroza/.claude/plans/i-would-like-to-squishy-sun.md`. Pattern, not enumeration. Key new files:

- `python/pydocs_mcp/storage/turboquant_store.py`
- `python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py`
- `python/pydocs_mcp/storage/turboquant_uow.py`
- `python/pydocs_mcp/storage/composite_uow.py`
- `python/pydocs_mcp/extraction/strategies/embedders/{__init__.py,fastembed.py,openai.py}`
- `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py`
- `python/pydocs_mcp/retrieval/steps/{dense_fetcher.py,dense_scorer.py}`
- `python/pydocs_mcp/pipelines/{chunk_search_dense,chunk_search_dense_ranked,chunk_search_hybrid,chunk_search_hybrid_ranked}.yaml`
- `tests/_fakes.py::MockEmbedder` (extend the existing `_fakes.py`).

Key modified files:

- `python/pydocs_mcp/models.py` — add type aliases + `is_multi_vector` + `Chunk.embedding`.
- `python/pydocs_mcp/storage/protocols.py` — add `Embedder` + `ResultFuser`.
- `python/pydocs_mcp/storage/factories.py` — composite builders + `turboquant_path_for_project`.
- `python/pydocs_mcp/db.py` — `SCHEMA_VERSION = 5` + `_apply_v5_additions`.
- `python/pydocs_mcp/retrieval/steps/rrf.py` — refactor to `RRFFusionStep`.
- `python/pydocs_mcp/retrieval/steps/parallel.py` — branch scratch contract clarification.
- `python/pydocs_mcp/retrieval/steps/top_k_filter.py` — optional `publish_to` field.
- `python/pydocs_mcp/retrieval/config.py` — `EmbeddingConfig`.
- `python/pydocs_mcp/defaults/default_config.yaml` — `embedding:` block.
- `python/pydocs_mcp/extraction/pipeline/ingestion.py` + `pipelines/ingestion.yaml` — slot the embed stage.
- `python/pydocs_mcp/application/indexing_service.py` — write vectors alongside chunks via composite UoW.
- `python/pydocs_mcp/server.py` + `__main__.py` + composition roots — flip UoW factory + run integrity check at startup.
- `pyproject.toml` — `turbovec` + `numpy` main deps; `fastembed` + `openai` extras.
- `CLAUDE.md` — extend README-jargon forbidden pattern + audit regex.
- `benchmarks/README.md` — replace `PR-B3.1` references.

## 7. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| `TurboQuantVectorStore.vector_search` needs the SQLite connection to build the allowlist + hydrate IDs to Chunks, but `VectorSearchable` is supposed to be backend-agnostic. Coupling SQLite into the vector store breaks SOLID. | Medium | **Resolved in §5.3:** constructor takes a `CandidateIdResolver(filter) -> np.ndarray` callable + a `ChunkHydrator(ids) -> tuple[Chunk, ...]` callable. SQLite-flavored implementations of both live in `storage/factories.py`. The vector-store class itself does NOT import `sqlite3` or any SQLite-specific module. |
| `ParallelStep` branches share the same `state.scratch` dict; concurrent writes to `pre_filter.result` (both branches re-run `PreFilterStep`) could race. | Low | `PreFilterStep` is idempotent and produces identical output for the same query — last-write-wins is correct. Document the invariant in `ParallelStep`. |
| TurboQuant `.tq` file write is not atomic — a crash mid-write could leave a corrupted index. | Medium | Write to `<path>.tmp`, fsync, then atomic rename (mirror the existing `_download_release_atomic` pattern in `benchmarks/eval/datasets/repoqa.py:73-87`). |
| `IdMapIndex.add_with_ids` may reject duplicate IDs. The indexing path re-runs on hash mismatch, which means existing chunks get re-upserted; do their embeddings re-write too? | Medium | Plan-phase: confirm `IdMapIndex.remove` is called before re-add, or that `add_with_ids` overwrites. Mitigation in TurboQuantUnitOfWork: always `remove` then `add` for any chunk being re-embedded. |
| Multi-vector embedders ship later than the model-selection PR but the type union allows them today — could surprise a reader. | Low | Document explicitly in `TurboQuantUnitOfWork.add_vectors` docstring + the `Embedding` type alias docstring. Test asserts the `NotImplementedError`. |
| `fastembed` package weight (ONNX runtime + tokenizers) makes the `[fastembed]` extra ~80 MB. | Low | Keep it as an optional extra; document the size in README. `pip install pydocs-mcp` core stays lean. |
| OpenAI cost — every reindex hits the API. | Low | Hash-skip already prevents re-embedding unchanged packages. Document the cost note in README. |
| `RRFStep` rewrite to `RRFFusionStep` might break consumers. | Very Low | No shipped pipeline references `rrf` as `RRFStep` today (grep confirmed during plan exploration). Renaming the registry key from `rrf` → `rrf_fusion` makes any external caller fail loudly. |

## 8. Acceptance criteria

All test-driven; CI must be green pre-merge.

### 8.1 Type + model layer

- AC-1: `Vector`, `MultiVector`, `Embedding` type aliases exist in `models.py`; `is_multi_vector(emb)` returns `True` for `((1.0,), (2.0,))` and `False` for `(1.0, 2.0)` and `False` for `()`.
- AC-2: `Chunk.embedding` defaults to `None`; existing constructors that don't pass `embedding` keep working unchanged.

### 8.2 Storage layer

- AC-3: `TurboQuantVectorStore.vector_search(query, limit=k)` against an index populated with N chunks returns at most k IDs, each present in the index.
- AC-4: `TurboQuantVectorStore.vector_search(query, limit=k, filter=…)` with a non-trivial filter returns only chunks whose metadata matches the filter (verified by checking IDs against a direct SQL query for the same filter).
- AC-5: `TurboQuantUnitOfWork`: add → commit → reload → search returns the same IDs (round-trip).
- AC-6: `TurboQuantUnitOfWork.rollback()` after `add_vectors` discards in-memory adds (size returns to pre-add value).
- AC-7: `TurboQuantUnitOfWork.add_vectors(ids, [multi_vector_with_length_2])` raises `NotImplementedError` whose message includes `"chunk_vectors"` (the future side-table marker).
- AC-8: `TurboQuantUnitOfWork` write is atomic — simulate a write failure (mock `index.write` to raise after partial completion); the existing `.tq` file is unchanged on disk.
- AC-9: `CompositeUnitOfWork` attribute proxying: `uow.packages` from `SqliteUnitOfWork`, `uow.vectors` from `TurboQuantUnitOfWork`; ambiguity raises `AttributeError`.
- AC-10: `CompositeUnitOfWork.commit()` with two children commits both; failure in second child triggers rollback on first child (best-effort); original exception re-raised.
- AC-11: Schema migration v4 → v5 adds `packages.embedding_model` column without data loss; downgrading (opening v5 with old code) is handled per the existing version-mismatch drop-and-rebuild flow.

### 8.3 Embedder layer

- AC-12: `Embedder` Protocol has `embed_query` + `embed_chunks` + `dim`; the `MockEmbedder` test double satisfies it (static type check via `isinstance` against a `runtime_checkable` Protocol).
- AC-13: `build_embedder(EmbeddingConfig(provider="fastembed", ...))` returns a `FastEmbedEmbedder` instance when the `[fastembed]` extra is installed.
- AC-14: `build_embedder(EmbeddingConfig(provider="openai", ...))` returns an `OpenAIEmbedder` instance when the `[openai]` extra is installed AND `OPENAI_API_KEY` env var is set; raises a clear `RuntimeError` at construction otherwise.
- AC-15: `build_embedder(EmbeddingConfig(provider="cohere", ...))` raises `ValueError` with message including `"Unknown embedding provider"`.
- AC-16: Importing `FastEmbedEmbedder` without the `[fastembed]` extra raises `OptionalDepMissing` (or similar) with message including `"pip install pydocs-mcp[fastembed]"`.

### 8.4 Retrieval layer

- AC-17: `DenseFetcherStep` reads `state.scratch["pre_filter.result"]`, runs the SQL → IDs, calls `IdMapIndex.search(..., allowlist=ids)`, hydrates Chunks; verified end-to-end against a mock-populated index.
- AC-18: `DenseScorerStep` reads `state.candidates`, embeds query via injected embedder, computes cosine similarity, writes scores back; verified with mock embedder.
- AC-19: `RRFFusionStep` reads two named scratch keys, fuses; expected RRF formula `sum(1 / (k + rank))`; output ordering matches hand-computed expected.
- AC-20: `TopKFilterStep.publish_to` field, when set, writes the step's output to `state.scratch[publish_to]` (verified via mock state).
- AC-21: `chunk_search_hybrid.yaml` loads + assembles + runs end-to-end against MockEmbedder + ephemeral SQLite + ephemeral TurboQuant `.tq` → `state.candidates` is non-empty for a non-trivial query.
- AC-22: The committed RepoQA fixture baseline SHA at `benchmarks/baselines/repoqa_fixture_baseline.json` (BM25-only, captured against `chunk_search_ranked.yaml`, MCP-side `chunk_search.yaml` unchanged) is **byte-identical** before and after this PR's commits. Proves the BM25 path is untouched.

### 8.5 Ingestion + composition root

- AC-23: `EmbedChunksStage` with a mock embedder of `dim=4` populates `Chunk.embedding` on every chunk in `state.chunks`.
- AC-24: End-to-end indexing test: index a small fixture project; assert `packages.embedding_model` is populated; assert the `.tq` file exists and `IdMapIndex.size() == SELECT COUNT(*) FROM chunks`.
- AC-25: Composition root integrity check: after manually corrupting the count balance (deleting some `.tq` entries via `remove`), restart; log emits a warning; affected packages get their `content_hash` cleared so the next sweep re-embeds them.
- AC-26: Re-embed on model change: index with `embedding.model_name = "A"`; change YAML to `model_name = "B"`; re-run index; all packages get re-embedded (verified via `packages.embedding_model` post-condition).

### 8.6 Test infrastructure

- AC-27: `MockEmbedder` in `tests/_fakes.py` satisfies the `Embedder` Protocol; same input → same vector (deterministic); `dim` is configurable; documented in module docstring as the canonical embedder test double.
- AC-28: No test file imports `fastembed` or `openai`. CI does not download any embedding model.

### 8.7 Housekeeping (#4 ride-along)

- AC-29: `CLAUDE.md` README-jargon section's forbidden-patterns list + audit regex include `PR-[A-Z][0-9.]+`; running the audit grep against `benchmarks/README.md` (post-fix) returns `AUDIT CLEAN`.
- AC-30: GitHub issue #6 body is updated per §3 of the plan (manual gh issue edit; not a test, but a checklist item).

## 9. Out-of-scope verification (deferred)

These are intentionally NOT acceptance criteria for this PR — they belong to the follow-up model-selection PR:

- Real embedding model invocation in tests or CI.
- `repoqa_snf_hybrid.json` / fixture baseline JSON capture.
- Quality lift: hybrid `recall@10` beats BM25 by 95% CI half-width.
- Pareto-scatter comparison BM25 vs Dense vs Hybrid on real data.
- Live MCP smoke test with a real semantic query (e.g., "asynchronous retry").

## 10. Implementation handoff

Next step per the user-mandated process:

1. **User reviews this spec** — workflow halts until user approves or requests changes.
2. After approval, invoke `superpowers:writing-plans` to produce the TDD task plan at `docs/superpowers/plans/2026-05-24-hybrid-search-with-semantic-embeddings.md`.
3. After plan approval, invoke `superpowers:subagent-driven-development` with one Opus 4.7 max-effort subagent per task.
4. Post-coding review chain: `/code-review`, `/review`, `/design-review`, `/ultrareview`.
