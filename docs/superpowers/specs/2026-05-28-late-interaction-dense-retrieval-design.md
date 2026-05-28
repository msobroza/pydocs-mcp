# Late-interaction dense retrieval (ColBERT / PyLate) — design

**Status:** spec — ready for implementation planning
**Tracks:** retrieval pipeline extension + storage extension
**Related work:** hybrid BM25 + dense (single-vector) + RRF (shipped),
chunk-level cache + atomic vector cleanup (shipped), LLM tree reasoning +
weighted fusion (shipped), the `MultiVector` type union + `is_multi_vector`
helper already present in `python/pydocs_mcp/models.py`, and the explicit
"deferred to a future PR that adds a `chunk_vectors` side-table" note in
`python/pydocs_mcp/storage/turboquant_uow.py:107-109`.

---

## 1. Goal

Add **late-interaction (multi-vector / ColBERT-style) dense retrieval** to
the existing sklearn-shaped `RetrieverPipeline`, served via the **PyLate**
library (github.com/lightonai/pylate, arXiv:2508.03555) so models such as
`lightonai/LateOn-Code` (Hugging Face) become usable as an **opt-in**
retrieval backend.

Three new primitives compose with the existing pipeline so a late-interaction
preset can ship without touching any shipped default:

1. `MultiVectorEmbedder` adapter (behind a `MultiVectorEmbedder` Protocol) —
   wraps PyLate's `models.ColBERT` to emit **one vector per token** for both
   queries and documents. Distinct from the single-vector `Embedder` Protocol
   (which returns a 1-D `np.ndarray`); a late-interaction embedder returns a
   2-D `np.ndarray` of shape `(n_tokens, dim)` (the `MultiVector` arm of the
   existing `Embedding` union).

2. Multi-vector persistence — a new storage backend that stores N vectors per
   chunk plus a `chunk → vector-id range` mapping, so a chunk's full token
   matrix can be reconstructed at query time. Wired through the same
   `CompositeUnitOfWork` / `uow.vectors` seam the single-vector path uses.

3. `LateInteractionScorerStep` (registry key `late_interaction_scorer`) — a
   **MaxSim** re-ranking step registered via `@step_registry.register`. It
   takes the candidate chunks produced by a cheap first-stage retriever
   (BM25 and/or single-vector dense), reconstructs each candidate's token
   matrix from the multi-vector store, embeds the query into its token
   matrix via the `MultiVectorEmbedder`, computes the ColBERT MaxSim score,
   and overwrites `relevance`. It fuses into the existing pipeline via the
   shipped `rrf_fusion` / `weighted_score_interpolation` steps.

A new typed `AppConfig` sub-model (`LateInteractionConfig`, an architectural
twin of `EmbeddingConfig`) configures the provider / model / dim / token-cap,
and one new preset YAML (`chunk_search_late_interaction.yaml`) wires the
retrieve-then-MaxSim-rerank shape. Everything is opt-in: the default
`chunk_search.yaml` and the default `embedding:` config are untouched.

## 2. Context

The hybrid-search work shipped `Chunk.embedding`, the single-vector
`Embedder` Protocol with `FastEmbedEmbedder` + `OpenAIEmbedder` concretes,
`TurboQuantVectorStore` + `HybridSqliteTurboStore` + `TurboQuantUnitOfWork`,
`DenseFetcherStep` + `DenseScorerStep` + `RRFFusionStep`, and `ParallelStep`
with named branches publishing to `state.scratch[<branch>.ranked]`. The
chunk-cache work added `pipeline_hash` (invalidates chunk hashes on embedder /
ingestion-YAML change) and `BuildContext.uow_factory` threaded through every
retrieval step that needs UoW access. The LLM-tree-reasoning work added the
`LlmClient` Protocol + `OpenAiLlmClient` + `build_llm_client(cfg)` factory +
`WeightedScoreInterpolationStep`, and is the freshest precedent for "new
provider behind a Protocol + typed config sub-model + opt-in preset YAML".

**The codebase already anticipates this feature.** Four load-bearing seams
exist today specifically so multi-vector retrieval can land without churning
the domain model:

- `python/pydocs_mcp/models.py:56-80` — `MultiVector = list[np.ndarray]`,
  `Embedding = np.ndarray | MultiVector`, and `is_multi_vector(emb)`. The
  `Chunk.embedding` field already accepts the `MultiVector` arm.
- `python/pydocs_mcp/storage/protocols.py:336-358` — the `Embedder` Protocol
  docstring already says "future ColBERT-style embedders return `MultiVector`
  (list of 1D `np.ndarray`s); use `is_multi_vector(emb)` to disambiguate."
- `python/pydocs_mcp/retrieval/steps/dense_fetcher.py:67-73` +
  `dense_scorer.py:59-63` — both collapse a multi-vector query to `query_vec[0]`
  with a comment: "The check exists so a future PR that adds multi-vector
  persistence can flip the behaviour without changing the contract."
- `python/pydocs_mcp/storage/turboquant_uow.py:104-110` —
  `TurboQuantUnitOfWork.add_vectors` raises `NotImplementedError` for
  multi-vector input, explicitly naming "a future PR that adds a
  `chunk_vectors` side-table."

This spec is that future PR. The only genuinely new machinery is (a) the
PyLate-backed `MultiVectorEmbedder`, (b) the multi-vector store + its UoW, and
(c) the MaxSim scoring step. Everything else reuses the shipped fusion,
config-layering, and composition-root patterns.

**Why this is NOT a config swap of the existing `embedding:` section.**
`lightonai/LateOn-Code` is a late-interaction model: it does not produce a
single pooled sentence vector. It emits a contextualized vector per token and
defers the similarity computation to query time via MaxSim:

```
score(q, d) = Σ_{i ∈ query tokens}  max_{j ∈ doc tokens}  cos(q_i, d_j)
```

(arXiv:2004.12832 ColBERT; arXiv:2508.03555 PyLate.) Pointing
`embedding.model_name` at `lightonai/LateOn-Code` cannot work because:

1. **Output shape.** `FastEmbedEmbedder.embed_query` returns a 1-D
   `np.ndarray`; PyLate returns `(n_tokens, dim)`. `EmbeddingConfig.dim`'s
   `dim % 8 == 0` validator and the `_KNOWN_MODEL_DIMS` cross-check assume one
   vector of `dim` per text.
2. **Storage shape.** `TurboQuantUnitOfWork.add_vectors` stacks one vector per
   id (`np.stack(embeddings)` → `IdMapIndex.add_with_ids`). A chunk with 180
   token-vectors has no single id-to-vector row to write — it needs a
   one-to-many mapping, which the `.tq` `IdMapIndex` does not model.
3. **Scoring shape.** `DenseScorerStep` computes one cosine between the query
   vector and the chunk vector. MaxSim is a per-token max-then-sum over two
   matrices — a fundamentally different operation that needs its own step.

So late interaction is a new feature spanning the embedder, the storage layer,
and the retrieval pipeline — not a tuning knob. It lands behind the existing
2-tool MCP surface (`search` + `lookup`), enabled entirely via YAML.

## 3. Locked-in decisions

These were settled before brainstorming and constrain every choice below.
Every decision uses the spec-format contract: **Question → Pros/Cons table →
Recommended → Code example** (Decision D below pins this requirement, mirroring
the LLM-tree-reasoning spec §3 Decision D).

### Decision A — `MultiVectorEmbedder` Protocol + `PyLateEmbedder` concrete, SOLID-extensible

**Question:** What abstraction does the late-interaction embedder live behind,
and which concrete ships first?

| Option | Pros | Cons |
|---|---|---|
| Reuse the existing `Embedder` Protocol; return a `MultiVector` from the same `embed_query` / `embed_chunks` | Zero new Protocol; `is_multi_vector` already disambiguates | Conflates two contracts on one type — every `Embedder` caller (single-vector `DenseScorerStep`, `EmbedChunksStage`) must branch on `is_multi_vector` at runtime; the dim semantics differ (per-text dim vs per-token dim); violates Interface Segregation |
| New `MultiVectorEmbedder` Protocol + `PyLateEmbedder` concrete | Mirrors `Embedder` → FastEmbed/OpenAI and `LlmClient` → OpenAiLlmClient; SOLID open/closed for future providers (a local `sentence-transformers` ColBERT, a Jina ColBERT, etc.); `from_dict` gates the dependency cleanly | Modest LOC for the Protocol + factory + config wiring |
| Direct PyLate calls inside the scorer step | No abstraction overhead | Couples retrieval to one library; impossible to unit-test MaxSim without loading a real model; no test oracle |

**Recommended:** Option 2 — a dedicated `MultiVectorEmbedder` Protocol (added
to `storage/protocols.py` next to `Embedder`) + a `PyLateEmbedder` concrete
under `extraction/strategies/embedders/pylate.py`, dispatched by a
`build_multi_vector_embedder(cfg)` factory. This keeps single-vector and
multi-vector embedding as two segregated contracts: the single-vector
`Embedder` callers never grow an `is_multi_vector` branch, and a future
multi-vector provider is a one-file addition. The Protocol returning the
`MultiVector` arm of the existing `Embedding` union means no new domain type.

**Code example (Protocol + first concrete):**

```python
# storage/protocols.py — additive, Embedder unchanged
@runtime_checkable
class MultiVectorEmbedder(Protocol):
    """Late-interaction (ColBERT-style) embedder: one vector PER TOKEN.

    Distinct from :class:`Embedder` (single pooled vector per text).
    ``embed_query`` / ``embed_documents`` each return a 2-D float32
    ``np.ndarray`` of shape ``(n_tokens, dim)`` — the ``MultiVector``
    arm of the ``Embedding`` union. Query and document encoders may
    differ (ColBERT prepends a [Q]/[D] marker token), hence the two
    method names rather than reusing ``embed_query`` / ``embed_chunks``.
    """
    dim: int
    model_name: str

    async def embed_query(self, text: str) -> MultiVector: ...

    async def embed_documents(
        self, texts: Sequence[str],
    ) -> tuple[MultiVector, ...]: ...
```

```python
# extraction/strategies/embedders/pylate.py — first concrete
from pylate import models  # optional dependency, imported lazily by the factory


@dataclass
class PyLateEmbedder:
    model_name: str = "lightonai/LateOn-Code"
    dim: int = 128                       # ColBERT default projection dim
    document_length: int = 256           # token cap per document (storage lever)
    query_length: int = 32               # token cap per query
    _model: "models.ColBERT" = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._model = models.ColBERT(
            model_name_or_path=self.model_name,
            document_length=self.document_length,
            query_length=self.query_length,
        )

    async def embed_query(self, text: str) -> MultiVector:
        mat = await asyncio.to_thread(
            lambda: self._model.encode(
                [text], is_query=True, convert_to_numpy=True,
            )[0],
        )
        return np.asarray(mat, dtype=np.float32)         # (n_q_tokens, dim)

    async def embed_documents(self, texts):
        if not texts:
            return ()
        mats = await asyncio.to_thread(
            lambda: self._model.encode(
                list(texts), is_query=False, convert_to_numpy=True,
            ),
        )
        return tuple(np.asarray(m, dtype=np.float32) for m in mats)
```

```python
# extraction/strategies/embedders/__init__.py — SOLID factory (sibling of build_embedder)
def build_multi_vector_embedder(cfg: LateInteractionConfig) -> MultiVectorEmbedder:
    if cfg.provider == "pylate":
        from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder
        return PyLateEmbedder(
            model_name=cfg.model_name, dim=cfg.dim,
            document_length=cfg.document_length, query_length=cfg.query_length,
        )
    raise ValueError(f"Unknown late-interaction provider: {cfg.provider!r}")
```

### Decision B — multi-vector storage: dedicated SQLite-backed store, NOT the `.tq` `IdMapIndex`

**Question:** Where do the N-per-chunk token vectors live, and how is the
`chunk → vectors` mapping persisted?

| Option | Pros | Cons |
|---|---|---|
| **(B1)** Extend the TurboQuant `.tq` `IdMapIndex`: assign each token-vector a synthetic `uint64` id, store a `token_id → chunk_id` map in a new SQLite `chunk_vectors` table, run ANN over all token-vectors, then group hits back to chunks for MaxSim | Reuses the shipped quantized ANN index; token-level ANN can serve as a first-stage retriever too | Blows up the index N× (a 50-token-avg corpus is a 50× larger `IdMapIndex`); MaxSim needs the FULL token matrix per candidate, not just the ANN-hit tokens, so we still need a side-store of complete matrices; quantization (4-bit scalar) degrades MaxSim precision more than single-vector cosine; the `uint64` id space now means two things (chunk id and token id) — a leaky abstraction across the whole codebase |
| **(B2)** New dedicated `MultiVectorStore` backend: a `chunk_vectors` SQLite table storing each chunk's token matrix as a single BLOB (`np.save` bytes) keyed by `chunk_id`, loaded + MaxSim-scored in-memory at query time over a candidate set produced by a cheap first stage | One row per chunk (no N× id explosion); stores the FULL float32 matrix (no quantization loss in MaxSim); the `chunk_id` space stays single-meaning; atomic with SQLite via the existing UoW; trivially correct test oracle (numpy MaxSim) | Linear MaxSim over candidates (acceptable because the candidate set is bounded to ~50-200 by the first stage); float32 BLOBs are larger on disk than 4-bit quantized vectors |
| **(B3)** External multi-vector engine (PLAID / a vector DB with native ColBERT support) | Production-grade ANN over token-vectors at scale | New heavy service dependency; breaks the "per-project sidecar files, no server" deployment model; massive YAGNI for a local docs index |

**Recommended:** Option **B2** — a dedicated `MultiVectorStore` backed by a new
`chunk_vectors` SQLite table (one BLOB row per chunk: the full
`(n_tokens, dim)` float32 matrix), with MaxSim computed in-memory at query time
over a candidate set produced by a cheap first stage (BM25 and/or single-vector
dense). This is the re-rank shape PyLate itself recommends for small/medium
corpora and matches our scale: a per-project docs index where the candidate set
is already capped at ~50-200 by an upstream `top_k_filter`. It keeps the
`chunk_id` space single-meaning, avoids the N× id explosion of B1, preserves
full-precision MaxSim (no quantization loss), and stays inside the existing
SQLite sidecar deployment model (no new service). The `.tq` `IdMapIndex` is
left exactly as-is for the single-vector path — this is purely additive.

The new table lands as a SQLite schema bump (`SCHEMA_VERSION = 5 → 6`) so it
coheres with the existing wipe-and-recreate-on-mismatch migration in
`db.py`. Because the dense float32 matrices are bulky, the BLOB store is its
own table (not a column on `chunks`) so the FTS rebuild and the common
chunk-list read path never pay for it.

**Code example (the new table + UoW seam):**

```python
# db.py — additive DDL, SCHEMA_VERSION bumped 5 -> 6
CREATE TABLE chunk_vectors (
    chunk_id   INTEGER PRIMARY KEY,   -- FK to chunks.id (one matrix per chunk)
    n_tokens   INTEGER NOT NULL,
    dim        INTEGER NOT NULL,
    vectors    BLOB    NOT NULL,      -- float32 (n_tokens, dim), C-contiguous
    model_name TEXT    NOT NULL       -- guards a model swap from mixing matrices
);
```

```python
# storage/multi_vector_store.py — the dedicated backend (sketch)
@dataclass(frozen=True, slots=True)
class SqliteMultiVectorUnitOfWork:
    """UoW child for the chunk_vectors table — mirrors TurboQuantUnitOfWork's
    add_vectors / remove_vectors / clear_all surface so CompositeUnitOfWork
    dispatches uow.vectors to it the same way. Stores ONE token matrix per
    chunk id. Multi-vector inputs are the expected shape here (the inverse of
    TurboQuantUnitOfWork, which raises NotImplementedError on them)."""

    async def add_vectors(
        self, ids: Sequence[int], embeddings: Sequence[Embedding],
    ) -> None:
        # Each emb is a (n_tokens, dim) float32 ndarray -> one BLOB row.
        ...

    async def load_matrices(
        self, ids: Sequence[int],
    ) -> dict[int, np.ndarray]:
        # chunk_id -> (n_tokens, dim) float32 — consumed by the MaxSim step.
        ...
```

Note the `uow.vectors` seam is unchanged: `IndexingService._maybe_write_vectors`
already calls `await uow.vectors.add_vectors(ids, embeddings)` with whatever
`Embedding` shape the configured embedder produced. Swapping the composite's
vector child from `TurboQuantUnitOfWork` to `SqliteMultiVectorUnitOfWork` (a
composition-root choice driven by config) is the only wiring change on the
write path.

### Decision C — `LateInteractionScorerStep` is a re-ranker, not a first-stage fetcher

**Question:** Does late interaction generate candidates itself, or re-rank an
upstream candidate set?

| Option | Pros | Cons |
|---|---|---|
| Late-interaction **fetcher** (token-ANN generates candidates from scratch) | One-stage retrieval | Requires the B1 token-ANN index we rejected; unbounded MaxSim cost; duplicates BM25/dense candidate generation |
| Late-interaction **re-ranker** over an upstream candidate set | MaxSim cost bounded by `top_k`; reuses the shipped BM25 / dense fetchers as the recall stage; matches PyLate's documented "retrieve then rerank" pattern; composes as a plain `RetrieverStep` reading `state.candidates` | Recall ceiling is the first stage's (a relevant doc the first stage misses can't be recovered) — acceptable; the standard ColBERT re-rank trade-off |

**Recommended:** Option 2 — a re-ranker. `LateInteractionScorerStep` reads
`state.candidates` (the chunk list produced by an upstream `chunk_fetcher`
and/or `dense_fetcher`), loads each candidate's token matrix from the
`MultiVectorStore` via `uow.vectors`, embeds the query into its token matrix,
computes MaxSim, overwrites `relevance`, and writes the re-ranked list back.
It is the dense analogue of `DenseScorerStep` (read candidates → score →
write) — same shape, different math. Bounding MaxSim to the candidate set
makes the cost predictable and lets the step publish to a scratch branch for
fusion exactly like the existing scorers.

**Code example (the MaxSim core + step body):**

```python
def _maxsim(query_mat: np.ndarray, doc_mat: np.ndarray) -> float:
    """ColBERT MaxSim: sum over query tokens of the max cosine to any doc token.

    Both inputs are L2-normalized per row first so the dot product IS the
    cosine. ``query_mat`` is (nq, dim); ``doc_mat`` is (nd, dim). The
    (nq, nd) similarity matrix's row-maxes are summed.
    """
    q = query_mat / (np.linalg.norm(query_mat, axis=1, keepdims=True) + 1e-12)
    d = doc_mat   / (np.linalg.norm(doc_mat,   axis=1, keepdims=True) + 1e-12)
    sim = q @ d.T                       # (nq, nd)
    return float(sim.max(axis=1).sum())


@step_registry.register("late_interaction_scorer")
@dataclass(frozen=True, slots=True)
class LateInteractionScorerStep(RetrieverStep):
    embedder: MultiVectorEmbedder
    uow_factory: "Callable[[], UnitOfWork]"
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default="late_interaction_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if not isinstance(state.candidates, ChunkList) or not state.candidates.items:
            return state
        ids = [c.id for c in state.candidates.items if c.id is not None]
        if not ids:
            return state
        query_mat = await self.embedder.embed_query(state.query.terms)
        async with self.uow_factory() as uow:
            matrices = await uow.vectors.load_matrices(ids)   # chunk_id -> (nd, dim)
        scored = [
            replace(c, relevance=_maxsim(query_mat, matrices[c.id]),
                    retriever_name="late_interaction")
            if c.id in matrices else c
            for c in state.candidates.items
        ]
        scored.sort(key=lambda c: c.relevance or 0.0, reverse=True)
        ranked = ChunkList(items=tuple(scored))
        new_scratch = dict(state.scratch)
        if self.publish_to is not None:
            new_scratch[self.publish_to] = ranked
        return replace(state, candidates=ranked, scratch=new_scratch)
```

### Decision D — Spec format requirement

Every non-trivial design decision uses **Question → Pros/Cons table →
Recommended → Code example.** This document follows that contract; the
implementation plan inherits it. (Carried verbatim from the LLM-tree-reasoning
spec §3 Decision D so the two specs read the same.)

## 4. Brainstormed decisions

### Decision E — `pylate` is an optional extra, imported lazily

**Question:** Is PyLate (+ sentence-transformers + torch) a hard runtime
dependency, or an optional extra?

| Option | Pros | Cons |
|---|---|---|
| Hard `dependencies` entry | One install path; always available | PyLate pulls `sentence-transformers` → `torch` → CUDA wheels (hundreds of MB to >1 GB); breaks the "~90 MB transitive" install promise in CLAUDE.md §Key Technical Details; forces every `pip install pydocs-mcp` user to download torch for a feature most won't use; first-run `serve` would import torch |
| `[project.optional-dependencies]` extra (`late-interaction`) imported lazily by the factory | Default install stays light; only users who opt in pay the weight; mirrors the `watch = ["watchdog..."]` extra precedent already in `pyproject.toml`; the `build_multi_vector_embedder` factory defers the `import pylate` until first use, so a missing extra surfaces a clear `ImportError` only when the late-interaction preset is actually loaded | Two install paths to document; users must `pip install pydocs-mcp[late-interaction]` |

**Recommended:** Option 2 — a `late-interaction` optional extra. PyLate and
sentence-transformers/torch are far heavier than the entire current dependency
set, so making them mandatory is a non-starter for a "local docs index" tool.
The factory's lazy import (the same pattern `build_embedder` /
`build_llm_client` already use) means the cost is paid only when the
late-interaction preset is selected, and a missing extra produces an
actionable error at preset-load time, not a cryptic failure deep in a query.

**Code example (`pyproject.toml` + the actionable error):**

```toml
[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff", "pytest-asyncio>=0.23"]
watch = ["watchdog>=4.0,<6.0"]
# Heavy (sentence-transformers + torch). Opt-in; default install stays ~90 MB.
late-interaction = ["pylate>=1.1,<2.0"]
```

```python
# extraction/strategies/embedders/pylate.py — top of module
try:
    from pylate import models
except ImportError as e:  # pragma: no cover - exercised via a monkeypatched import test
    raise ImportError(
        "Late-interaction retrieval requires the 'late-interaction' extra. "
        "Install it with:  pip install 'pydocs-mcp[late-interaction]'  "
        "(pulls pylate + sentence-transformers + torch)."
    ) from e
```

### Decision F — `LateInteractionConfig` is a sibling sub-model, not new keys on `EmbeddingConfig`

**Question:** Where does the late-interaction provider / model config live?

| Option | Pros | Cons |
|---|---|---|
| Add `late_interaction_*` keys onto `EmbeddingConfig` | One config object | Conflates two embedder identities in one model; the `dim % 8 == 0` validator + `_KNOWN_MODEL_DIMS` cross-check are single-vector-specific and would misfire; `compute_pipeline_hash` would hash irrelevant fields |
| New `LateInteractionConfig` sub-model + `AppConfig.late_interaction` field | Mirrors `EmbeddingConfig` / `LlmConfig` exactly; own validators + own `compute_pipeline_hash`; YAML namespacing gives future late-interaction tunables an obvious home; `provider` is its own `Literal` so an unknown value fails at load | One more sub-model (cheap) |

**Recommended:** Option 2 — a dedicated `LateInteractionConfig` in
`retrieval/config.py`, an architectural twin of `EmbeddingConfig` and
`LlmConfig`, exposed as `AppConfig.late_interaction`. It owns
`provider: Literal["pylate"]`, `model_name`, `dim`, `document_length`,
`query_length`, `candidate_limit`, and its own `compute_pipeline_hash()`. Per
CLAUDE.md §"MCP API surface vs YAML configuration", this is a pipeline-tuning
knob, never an MCP param. Per §"Default values: single source of truth", the
token-cap and dim defaults live once as the pydantic `Field(default=...)`.

**Code example (`LateInteractionConfig`):**

```python
# retrieval/config.py — sibling of EmbeddingConfig / LlmConfig
class LateInteractionConfig(BaseModel):
    """Late-interaction (ColBERT / PyLate) embedder config.

    Twin of :class:`EmbeddingConfig`; consumed by
    ``build_multi_vector_embedder(cfg)``. NO dim%8 / known-model checks —
    ColBERT projection dims (often 96/128) are model-specific and the
    multi-vector store is dim-agnostic (it stores raw float32 matrices).
    """
    model_config = ConfigDict(extra="forbid")

    provider:        Literal["pylate"] = "pylate"
    model_name:      str = "lightonai/LateOn-Code"
    dim:             int = Field(default=128, ge=1)
    # Token caps double as the storage / latency lever (Decision B / risks).
    document_length: int = Field(default=256, ge=8)
    query_length:    int = Field(default=32, ge=4)
    # Re-rank candidate ceiling — how many first-stage hits MaxSim scores.
    candidate_limit: int = Field(default=100, ge=1)

    def compute_pipeline_hash(self) -> str:
        identity = "|".join([
            self.provider, self.model_name, str(self.dim),
            str(self.document_length), str(self.query_length),
        ])
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()
```

### Decision G — opt-in via a new preset; default config + default `embedding:` untouched

**Question:** Does any shipped default change?

| Option | Pros | Cons |
|---|---|---|
| Opt-in only via a new preset + explicit `late_interaction:` config | Zero risk to existing users; default install stays light (no torch); easy A/B against the hybrid baseline; first-run `serve` needs no model download | Feature ships dormant; benchmarks must explicitly select it |
| Replace the default `chunk_search.yaml` with the late-interaction preset | Every user benefits | First-run `pip install + serve` downloads torch + a ColBERT model; breaks the zero-config promise; huge cold-start |
| Make late interaction the default `embedding.provider` | One config surface | Same cold-start problem + conflates the two embedder contracts (Decision A/F) |

**Recommended:** Option 1 — opt-in. One new preset
(`chunk_search_late_interaction.yaml`) ships under
`python/pydocs_mcp/pipelines/`; the default `chunk_search.yaml`, the default
`embedding:` config, and the default ingestion pipeline are all unchanged. A
user enables late interaction by (a) installing the extra, (b) adding a
`late_interaction:` block to their YAML, (c) pointing `--config` at the preset
(or pointing the ingestion pipeline at the multi-vector embed stage). This is
the same opt-in posture the LLM-tree-reasoning presets took.

**Code example (preset usage):**

```bash
# Default behavior — unchanged, light install, no torch
pydocs-mcp serve .

# Opt into late-interaction reranking
pip install 'pydocs-mcp[late-interaction]'
pydocs-mcp index .  --config python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml
pydocs-mcp serve .  --config python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml
```

### Decision H — fuse via the shipped `rrf_fusion` / `weighted_score_interpolation`, no new fuser

**Question:** Does late interaction need a bespoke fusion step to combine its
MaxSim ranking with the BM25 / dense rankings?

| Option | Pros | Cons |
|---|---|---|
| New `late_interaction_fusion` step | Could special-case MaxSim score scale | Duplicates the shipped fusion math; YAGNI — MaxSim scores publish to a scratch branch like any other ranking |
| Reuse `rrf_fusion` (rank-based, scale-free) | Already shipped + tested; rank-based fusion is robust to MaxSim's unbounded score scale; the `publish_to` → `branch_keys` convention is identical to the dense branch | None for the default fuse |
| Reuse `weighted_score_interpolation` (min-max normalized blend) | Already shipped; lets a power user weight MaxSim higher than BM25 | Needs all branches present (raises on missing) — fine for a fixed preset |

**Recommended:** reuse both — `rrf_fusion` as the default in the shipped
preset (rank-based fusion sidesteps MaxSim's unbounded magnitude), with
`weighted_score_interpolation` available as a one-line YAML swap for users who
want to weight the late-interaction branch. `LateInteractionScorerStep`
publishes its ranking to `state.scratch[<branch>.ranked]` via `publish_to`,
exactly like `TopKFilterStep` does for the BM25 / dense branches, so the
existing fusers consume it with zero new code. No new fuser ships.

**Code example (preset fusion shape — retrieve, then MaxSim rerank, then fuse):**

```yaml
# chunk_search_late_interaction.yaml (excerpt)
- name: parallel
  type: parallel_retrieval
  params:
    branches:
      - name: bm25                     # cheap recall stage
        steps:
          - { name: fetch, type: chunk_fetcher, params: { schema_name: chunk } }
          - { name: score, type: bm25_scorer,   params: {} }
          - { name: topk,  type: top_k_filter,  params: { k: 100, publish_to: bm25.ranked } }
      - name: late                     # MaxSim rerank of the same recall set
        steps:
          - { name: fetch,  type: chunk_fetcher,            params: { schema_name: chunk } }
          - { name: topk,   type: top_k_filter,             params: { k: 100 } }
          - { name: maxsim, type: late_interaction_scorer,  params: { publish_to: late.ranked } }
- name: fuse
  type: rrf_fusion
  params: { branch_keys: [bm25.ranked, late.ranked] }
```

### Decision I — multi-vector ingestion is a NEW stage, not a flag on `embed_chunks`

**Question:** How do token matrices get written at index time?

| Option | Pros | Cons |
|---|---|---|
| Add a `multi_vector: bool` flag to `EmbedChunksStage` | One stage | The stage takes an `Embedder` (single-vector contract); branching it on a second embedder type violates SRP; its `strict=True` 1:1 zip and `Chunk.embedding` splice assume one vector per chunk |
| New `EmbedChunksMultiVectorStage` registered via `@stage_registry.register("embed_chunks_multi_vector")` | SRP — one stage per embedder contract; takes a `MultiVectorEmbedder`; splices the `(n_tokens, dim)` matrix onto `Chunk.embedding` (the `MultiVector` arm); a new `ingestion_late_interaction.yaml` swaps `embed_chunks` for it | One more stage (cheap; matches "one stage per file" convention) |

**Recommended:** Option 2 — a new `EmbedChunksMultiVectorStage`. It mirrors
`EmbedChunksStage` (same content-hash skip set, same batch loop, same
`pipeline_hash` interaction) but calls `MultiVectorEmbedder.embed_documents`
and stores the matrix into `Chunk.embedding`. A new ingestion preset
(`ingestion_late_interaction.yaml`) wires it in place of `embed_chunks`. The
existing `IndexingService._maybe_write_vectors` → `uow.vectors.add_vectors`
path then forwards each `(n_tokens, dim)` matrix to the
`SqliteMultiVectorUnitOfWork`, which stores it as one BLOB row — no change to
the service, only to which vector UoW the composite holds.

**Code example (stage skeleton — only the embedder call + splice differ):**

```python
@stage_registry.register("embed_chunks_multi_vector")
@dataclass(frozen=True, slots=True)
class EmbedChunksMultiVectorStage:
    embedder: MultiVectorEmbedder
    batch_size: int = _DEFAULT_BATCH_SIZE
    name: str = "embed_chunks_multi_vector"

    async def run(self, state: IngestionState) -> IngestionState:
        # ... identical skip-set + batch loop as EmbedChunksStage ...
        embs = await self.embedder.embed_documents(tuple(c.text for c in batch))
        # embs[i] is a (n_tokens, dim) float32 ndarray -> Chunk.embedding (MultiVector arm)
        ...
```

### Decision J — `pipeline_hash` already invalidates on a multi-vector embedder swap (one-line wiring)

**Question:** How does the chunk-level cache know to re-embed when the
late-interaction model changes?

| Option | Pros | Cons |
|---|---|---|
| New bespoke invalidation path for multi-vector | Explicit | Duplicates the shipped `pipeline_hash` mechanism |
| Fold `late_interaction.compute_pipeline_hash()` into `AppConfig.ingestion_pipeline_hash` when the multi-vector ingestion pipeline is active | Reuses the shipped cache-invalidation seam verbatim; a model swap (or a token-cap change) flips every chunk's `content_hash` → diff-merge re-embeds via the existing path; no "force re-embed" code | Must include the multi-vector embedder identity in the hash input |

**Recommended:** Option 2 — reuse `pipeline_hash`. `AppConfig.ingestion_pipeline_hash`
already hashes `embedding.compute_pipeline_hash() + ingestion-YAML bytes`.
When the active ingestion pipeline is the multi-vector one, the hash input
additionally folds in `late_interaction.compute_pipeline_hash()`, so changing
`late_interaction.model_name` (or `document_length`, which changes the stored
matrices) invalidates every chunk hash and the existing diff-merge re-embeds.
The `chunk_vectors.model_name` column (Decision B) is the defense-in-depth
guard: a stale matrix from a different model is never MaxSim-scored against a
new query encoder. No new invalidation code path.

**Code example (the hash fold):**

```python
# retrieval/config.py — AppConfig.ingestion_pipeline_hash (additive)
identity = self.embedding.compute_pipeline_hash()
if _ingestion_uses_multi_vector(ingestion_path):      # cheap YAML scan, cached
    identity += "|" + self.late_interaction.compute_pipeline_hash()
return hashlib.sha256(identity.encode("utf-8") + b"|" + ingestion_path.read_bytes()).hexdigest()
```

## 5. Architecture

### `LateInteractionScorerStep` data flow

```
state.candidates  (ChunkList from an upstream chunk_fetcher / dense_fetcher, top-K capped)
       │
       ▼
1. ids = [c.id for c in candidates if c.id is not None]
   (empty -> return state unchanged: no-op, mirrors DenseScorerStep)
       │
       ▼
2. query_mat = await embedder.embed_query(state.query.terms)   # (nq, dim) float32
   (PyLate prepends the [Q] marker; asyncio.to_thread wraps the torch call)
       │
       ▼
3. async with uow_factory() as uow:
       matrices = await uow.vectors.load_matrices(ids)   # chunk_id -> (nd, dim) float32
   (SqliteMultiVectorUnitOfWork reads the BLOB rows, np.frombuffer -> reshape)
       │
       ▼
4. for each candidate with a matrix:
       relevance = _maxsim(query_mat, matrices[c.id])
       retriever_name = "late_interaction"
   (candidates without a stored matrix pass through unchanged)
       │
       ▼
5. sort by relevance desc -> ChunkList
       │
       ▼
6. if publish_to: state.scratch[publish_to] = ranked
   state.candidates = ranked
   (downstream rrf_fusion / weighted_score_interpolation fuses the branch)
```

### Step contracts (input/output table — the late-interaction additions)

`RetrieverState` is the contract object: `query: SearchQuery` (immutable),
`candidates: ChunkList | None` (current stream), `result: ResultPayload | None`
(final render), `scratch: dict[str, Any]` (side-channel). The new step slots in
alongside the shipped ones:

| Step | Reads | Writes | Scratch side-effects |
|---|---|---|---|
| `chunk_fetcher` | `state.query` | `state.candidates` (FTS5 hits) | — |
| `dense_fetcher` | `state.query`, single-vector `Embedder` | `state.candidates` (`.tq` ANN hits) | — |
| `dense_scorer` | `state.candidates` + single query vector | `state.candidates` w/ cosine | — |
| **`late_interaction_scorer`** | `state.candidates`, `MultiVectorEmbedder.embed_query`, `uow.vectors.load_matrices` | `state.candidates` re-ranked by MaxSim | If `publish_to` set: writes the ranking |
| `top_k_filter` | `state.candidates` | truncated | If `publish_to` set: writes scratch |
| `rrf_fusion` | `state.scratch[branch_keys[i]]` | `state.candidates` fused | If `publish_to` set: writes scratch |
| `weighted_score_interpolation` | `state.scratch[branch_keys[i]]` | `state.candidates` fused (normalized blend) | If `publish_to` set: writes scratch |

### `BuildContext` extension

`BuildContext` (in `retrieval/serialization.py`) gains one new field, mirroring
the existing `embedder` / `llm_client` fields:

```python
@dataclass(frozen=True, slots=True)
class BuildContext:
    # ... existing: connection_provider, ..., embedder, uow_factory,
    # llm_client, pipeline_hash, filter_adapter ...
    multi_vector_embedder: "MultiVectorEmbedder | None" = None
```

`LateInteractionScorerStep.from_dict` raises `ValueError` if
`context.multi_vector_embedder is None` OR `context.uow_factory is None` (the
same strict-gate pattern `DenseFetcherStep` / `EmbedChunksStage` /
`LlmTreeReasoningStep` use). The composition roots (`retrieval/factories.py`'s
`build_retrieval_context`, `server.py`, `__main__.py`) build the embedder once
via `build_multi_vector_embedder(config.late_interaction)` — but ONLY when the
active config actually references late interaction, so a default install never
imports torch.

### Write-path wiring (composition root only)

The single-vector composite is built by
`build_sqlite_plus_turboquant_uow_factory(...)` in `storage/factories.py`. A
parallel `build_sqlite_plus_multi_vector_uow_factory(...)` wraps
`SqliteUnitOfWork` + `SqliteMultiVectorUnitOfWork` (instead of
`TurboQuantUnitOfWork`). The composition root selects which composite to build
based on whether the active ingestion pipeline uses the multi-vector stage.
`IndexingService._maybe_write_vectors` is unchanged — it forwards whatever
`Chunk.embedding` shape the stage produced to `uow.vectors.add_vectors`, and
the multi-vector UoW stores it as a BLOB.

### New preset YAMLs

`python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml` — the
retrieve-then-MaxSim-rerank-then-fuse shape sketched in Decision H. A
`_ranked` benchmark variant (no `token_budget_formatter`, matching the
existing `chunk_search_hybrid_ranked.yaml` convention) ships alongside so
recall@k / mrr can be measured.

`python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml` — a copy of
`ingestion.yaml` with `embed_chunks` swapped for `embed_chunks_multi_vector`.

## 6. Scope

### In-scope

- `MultiVectorEmbedder` Protocol (added to `storage/protocols.py` next to
  `Embedder`) + `PyLateEmbedder` concrete +
  `build_multi_vector_embedder(cfg)` factory branch.
- `LateInteractionConfig` sub-model + `AppConfig.late_interaction` field +
  `compute_pipeline_hash`.
- `chunk_vectors` SQLite table (`SCHEMA_VERSION` 5 → 6) +
  `SqliteMultiVectorUnitOfWork` (the dedicated multi-vector store with
  `add_vectors` / `remove_vectors` / `clear_all` / `load_matrices`).
- `EmbedChunksMultiVectorStage` ingestion stage +
  `ingestion_late_interaction.yaml` preset.
- `LateInteractionScorerStep` (`@step_registry.register("late_interaction_scorer")`)
  + the `_maxsim` core + tests + YAML round-trip.
- `BuildContext.multi_vector_embedder` field + composition-root wiring
  (`retrieval/factories.py`, `storage/factories.py`, `server.py`,
  `__main__.py`) — gated so a default install never imports PyLate.
- `chunk_search_late_interaction.yaml` + `chunk_search_late_interaction_ranked.yaml`
  presets.
- `pylate` optional extra in `pyproject.toml` (`[late-interaction]`); lazy
  import with an actionable `ImportError`.
- `FakeMultiVectorEmbedder` in `tests/_fakes.py` (returns deterministic
  random-but-seeded matrices) + a `make_fake_uow_factory(..., vectors=...)`
  extension so MaxSim tests run offline without torch.
- One new benchmark system variant
  (`PydocsLateInteractionSystem` in `benchmarks/src/benchmarks/eval/systems/pydocs.py`).
- Docs: `CLAUDE.md` retrieval-steps enumeration gains
  `late_interaction_scorer`; the embedders / storage notes mention the
  multi-vector path; `default_config.yaml` documents a commented-out
  `late_interaction:` block.

### Out-of-scope (YAGNI)

- Token-level ANN over a quantized index (Decision B1 — rejected).
- An external PLAID / vector-DB engine (Decision B3 — rejected).
- A second multi-vector provider (Jina ColBERT, a local sentence-transformers
  ColBERT) — SOLID-extensible later via one factory branch.
- Quantizing the stored token matrices (4/8-bit) — a follow-up storage
  optimization once on-disk size is measured against real corpora.
- A late-interaction **first-stage fetcher** (Decision C — re-ranker only).
- Default config / default `embedding:` changes (all opt-in).
- Changing the MCP surface — `search` + `lookup` stay fixed; no new params.
- Multi-vector support for the `member_search` pipeline (chunk-search path
  only this PR).
- GPU / device-placement config knobs for PyLate (sentence-transformers picks
  the device; a `device:` knob is a follow-up if needed).

## 7. Files touched

### Create

- `python/pydocs_mcp/extraction/strategies/embedders/pylate.py`
  (`PyLateEmbedder`)
- `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks_multi_vector.py`
  (`EmbedChunksMultiVectorStage`)
- `python/pydocs_mcp/storage/multi_vector_store.py`
  (`SqliteMultiVectorUnitOfWork`)
- `python/pydocs_mcp/retrieval/steps/late_interaction_scorer.py`
  (`LateInteractionScorerStep` + `_maxsim`)
- `python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml`
- `python/pydocs_mcp/pipelines/chunk_search_late_interaction_ranked.yaml`
- `python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml`
- `tests/retrieval/steps/test_late_interaction_scorer.py`
- `tests/storage/test_multi_vector_store.py`
- `tests/extraction/test_embed_chunks_multi_vector.py`
- `tests/extraction/strategies/embedders/test_pylate_embedder.py` (monkeypatch
  the `pylate.models.ColBERT` import; assert lazy-import error path + shape
  contract)
- `tests/integration/test_late_interaction_end_to_end.py` (skip-unless the
  `late-interaction` extra is importable)

### Modify

- `python/pydocs_mcp/storage/protocols.py` — add `MultiVectorEmbedder`
  Protocol.
- `python/pydocs_mcp/retrieval/config.py` — add `LateInteractionConfig` +
  `AppConfig.late_interaction`; fold its `compute_pipeline_hash()` into
  `ingestion_pipeline_hash` when the multi-vector ingestion pipeline is active.
- `python/pydocs_mcp/db.py` — `chunk_vectors` DDL; bump `SCHEMA_VERSION`
  5 → 6; add `chunk_vectors` to `_KNOWN_TABLES`.
- `python/pydocs_mcp/retrieval/serialization.py` — add
  `BuildContext.multi_vector_embedder` field.
- `python/pydocs_mcp/retrieval/steps/__init__.py` — re-export
  `LateInteractionScorerStep`.
- `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` — add
  `build_multi_vector_embedder(cfg)`.
- `python/pydocs_mcp/storage/factories.py` —
  `build_sqlite_plus_multi_vector_uow_factory(...)`.
- `python/pydocs_mcp/retrieval/factories.py` — build the multi-vector embedder
  (gated) and thread it into `BuildContext.multi_vector_embedder`.
- `python/pydocs_mcp/server.py` + `python/pydocs_mcp/__main__.py` — select the
  composite UoW factory + thread the multi-vector embedder when configured.
- `tests/_fakes.py` — `FakeMultiVectorEmbedder` + `make_fake_uow_factory`
  `vectors=` support.
- `benchmarks/src/benchmarks/eval/systems/pydocs.py` —
  `PydocsLateInteractionSystem`.
- `pyproject.toml` — `[project.optional-dependencies] late-interaction`;
  `[tool.maturin] include` already covers `pipelines/*.yaml`.
- `python/pydocs_mcp/defaults/default_config.yaml` — commented-out
  `late_interaction:` block.
- `CLAUDE.md` — retrieval-steps enumeration + a one-line note on the
  multi-vector store + the `late-interaction` extra.

## 8. Acceptance criteria

1. **AC-1 — `MultiVectorEmbedder` Protocol.** `PyLateEmbedder` passes
   `isinstance(obj, MultiVectorEmbedder)` at runtime. `embed_query` returns a
   2-D `np.ndarray` `(nq, dim)`; `embed_documents` returns a tuple of 2-D
   arrays. Verified with `FakeMultiVectorEmbedder` (no torch needed).
2. **AC-2 — `LateInteractionConfig`.** `AppConfig.late_interaction` is a
   `LateInteractionConfig` with `provider: Literal["pylate"]`, `model_name`,
   `dim`, `document_length`, `query_length`, `candidate_limit` fields; YAML
   overlay loading + env overrides (`PYDOCS_LATE_INTERACTION__MODEL_NAME`)
   work; `extra="forbid"` rejects unknown keys.
3. **AC-3 — `build_multi_vector_embedder`.** Returns a `PyLateEmbedder` when
   `provider == "pylate"`; raises `ValueError` for unknown providers; the
   `pylate` import is lazy (a monkeypatched-absent `pylate` raises the
   actionable `ImportError` only when the factory is called, not at module
   import).
4. **AC-4 — `chunk_vectors` schema + migration.** A fresh DB has the
   `chunk_vectors` table; opening a v5 DB triggers the wipe-and-recreate path
   to v6; `chunk_vectors` is in `_KNOWN_TABLES`.
5. **AC-5 — `SqliteMultiVectorUnitOfWork` round-trip.** `add_vectors([id],
   [(nd, dim) matrix])` then `load_matrices([id])` returns the byte-identical
   matrix (`np.array_equal`); `remove_vectors([id])` deletes the row;
   `clear_all()` empties the table; all atomic within the surrounding UoW
   (`commit` persists, exception rolls back).
6. **AC-6 — `_maxsim` correctness.** `_maxsim(q, d)` equals the reference
   `sum(max over doc tokens of cosine)` for hand-computed small matrices;
   identical query and doc matrices yield `nq` (every query token's max cosine
   to itself is 1.0); orthogonal matrices yield ~0.
7. **AC-7 — `LateInteractionScorerStep` happy path.** Given a `ChunkList` of
   candidates with ids, a `FakeMultiVectorEmbedder`, and a fake `uow.vectors`
   returning matrices, the step overwrites `relevance` with the MaxSim score,
   sets `retriever_name="late_interaction"`, and sorts descending. Candidates
   without a stored matrix pass through unchanged. Round-trips through
   `to_dict` / `from_dict`.
8. **AC-8 — `LateInteractionScorerStep` `publish_to`.** With `publish_to` set,
   the re-ranked list is written to `state.scratch[<key>]` AND
   `state.candidates`; without it, only `state.candidates`.
9. **AC-9 — strict gate.** `LateInteractionScorerStep.from_dict` raises
   `ValueError` (pointing at the composition root) when
   `context.multi_vector_embedder is None` OR `context.uow_factory is None`.
10. **AC-10 — `EmbedChunksMultiVectorStage`.** Given chunks and a
    `FakeMultiVectorEmbedder`, the stage splices a `(nd, dim)` matrix onto each
    `Chunk.embedding`; honors the `existing_chunk_hashes` skip set; stamps the
    package `embedding_model`; round-trips `to_dict` / `from_dict`; `from_dict`
    raises without `context.multi_vector_embedder`.
11. **AC-11 — `pipeline_hash` invalidation.** With the multi-vector ingestion
    pipeline active, changing `late_interaction.model_name` OR
    `late_interaction.document_length` changes `AppConfig.ingestion_pipeline_hash`
    (so the diff-merge re-embeds). With the default single-vector pipeline,
    `late_interaction` changes do NOT affect the hash.
12. **AC-12 — preset YAMLs round-trip.** `chunk_search_late_interaction.yaml`,
    `chunk_search_late_interaction_ranked.yaml`, and
    `ingestion_late_interaction.yaml` each load via the existing factories
    (with a `FakeMultiVectorEmbedder` + fake multi-vector UoW in the
    `BuildContext`), execute against a seeded SQLite + `chunk_vectors` fixture,
    and produce non-empty output.
13. **AC-13 — opt-in: defaults untouched.** `chunk_search.yaml`, the default
    `embedding:` config, and `ingestion.yaml` are byte-unchanged; a default
    `pip install pydocs-mcp` (no extra) imports the package, runs the default
    chunk-search pipeline, and never imports `pylate` / `torch` (asserted via a
    `sys.modules` check after a default `serve` composition).
14. **AC-14 — lazy extra.** With `pylate` not installed, loading the
    late-interaction preset raises the actionable `ImportError` naming
    `pip install 'pydocs-mcp[late-interaction]'`; the rest of the test suite is
    unaffected.
15. **AC-15 — benchmark variant.** `pydocs_late_interaction` is runnable via the
    benchmark CLI and produces mrr / recall@k comparable to `pydocs_hybrid`;
    the RepoQA smoke test skips cleanly when the extra is absent.
16. **AC-16 — full suite green.** `pytest -q`: at least 1199 + (one per AC)
    tests pass. `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`: at
    least 141 pass. `ruff check python/ tests/ benchmarks/`: clean.
    `cargo fmt --check` + `cargo clippy -- -D warnings` + `cargo test`: clean
    (no Rust changes this PR, but the gate stays green).
17. **AC-17 — authorship.** Every commit on the PR branch authored solely by
    `Max Raphael Sobroza Marques <max.raphael@gmail.com>`; zero
    `Co-authored-by:` trailers. Verified via
    `git log main..HEAD --pretty=full | grep -i 'co-authored-by'` returning
    empty.
18. **AC-18 — docs + README jargon audit.** `CLAUDE.md` retrieval-steps list
    enumerates `late_interaction_scorer`; `default_config.yaml` documents the
    commented-out `late_interaction:` block. The README jargon audit-grep
    (CLAUDE.md §"README files") returns zero matches.

## 9. Risks

| Risk | Mitigation |
|---|---|
| Model size / cold-start latency (PyLate loads a transformer + tokenizer) | Opt-in extra (Decision E) — default users never load it; the model loads once at server/CLI startup, not per query; `asyncio.to_thread` keeps the torch call off the event loop |
| Storage blow-up from N vectors per chunk | Decision B2 stores one BLOB row per chunk (no id explosion); `document_length` caps tokens per chunk (default 256); a follow-up can quantize the matrices (out-of-scope). Risk is bounded + measurable: bytes ≈ `n_chunks × document_length × dim × 4`. For a typical project (~10k chunks, 256 tokens, dim 128) that is ~1.3 GB worst-case if every chunk hits the cap — flagged in docs; real chunks are far shorter, and the cap is a tunable lever |
| PyLate / sentence-transformers / torch dependency weight (>1 GB) | Optional extra, lazy import, actionable `ImportError`; never in the default dependency closure (AC-13) |
| Indexing-time cost (encoding every chunk through a transformer) | Reuses the per-package content-hash skip (unchanged packages never re-enter the pipeline) + the chunk-level `pipeline_hash` skip; first index is slow (documented), reindex is incremental |
| MaxSim float32 BLOB load cost per query | Bounded by `candidate_limit` (default 100) — the re-rank set, not the whole corpus; matrices loaded in one `load_matrices(ids)` batch query; numpy MaxSim over ~100 small matrices is sub-millisecond |
| Recall ceiling: a relevant doc the first stage misses can't be recovered by MaxSim | Inherent ColBERT re-rank trade-off (Decision C); mitigated by a generous first-stage `top_k` (100) and by fusing BM25 + late-interaction branches via RRF so a doc found by either survives |
| `chunk_vectors` matrices outlive a model swap and get MaxSim-scored against a mismatched query encoder | `pipeline_hash` re-embeds on model change (Decision J) AND the `chunk_vectors.model_name` column is a defense-in-depth guard the loader can assert against |
| Late-interaction numbers regress vs hybrid on RepoQA | That is what the benchmark is for; a regression is data, not a bug — documented as a follow-up if confirmed (mirrors the LLM-tree-reasoning spec §9 stance) |

## 10. Open items

1. **PyLate encode API surface.** The exact `models.ColBERT(...)` constructor
   kwargs (`document_length` / `query_length` names) and `.encode(...,
   is_query=...)` flag should be pinned against the installed PyLate version
   during planning — the public API is stable per arXiv:2508.03555 but the
   kwarg names are confirmed at implementation time, not in this spec.
2. **`lightonai/LateOn-Code` projection dim.** The `dim` default (128) is the
   ColBERT convention; the actual `LateOn-Code` projection dim is read from the
   model card at implementation time and the `Field(default=...)` updated to
   match (single source of truth).
3. **Token-matrix quantization.** Whether to ship 8-bit quantization of the
   stored matrices in v1 or defer is left open pending a real on-disk size
   measurement against this repo's own index (the `bytes ≈ ...` estimate in
   §9). Default: defer (YAGNI) unless the measured size is prohibitive.

## 11. Verification

End-to-end smoke after implementation:

```bash
# Unit + benchmark tests (default install — no torch)
pytest -q                                              # expect 1199 + new
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q  # expect 141 + new

# Lint
ruff check python/ tests/ benchmarks/
cargo fmt --check && cargo clippy -- -D warnings

# Opt-in late-interaction smoke (requires the extra + a model download)
pip install 'pydocs-mcp[late-interaction]'
# Add to ./pydocs-mcp.yaml:
#   late_interaction:
#     provider: pylate
#     model_name: lightonai/LateOn-Code
pydocs-mcp index . --config python/pydocs_mcp/pipelines/ingestion_late_interaction.yaml
pydocs-mcp serve . --config python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml
# In an MCP client:
#   search("how does the diff-merge skip unchanged chunks")
# Expect: BM25 recalls candidates; late_interaction_scorer re-ranks via MaxSim;
# the AssignChunkContentHashStage / diff-merge chunk surfaces at the top.

# Confirm the default path stays light (no torch import)
python -c "import pydocs_mcp.server; import sys; assert 'torch' not in sys.modules"
```

## 12. Implementation handoff

After spec approval:

1. Invoke `superpowers:writing-plans` against this spec to produce a
   bite-sized TDD plan under
   `docs/superpowers/plans/2026-05-28-late-interaction-dense-retrieval.md`.
2. Execute via `superpowers:subagent-driven-development`.
3. `/code-review` + `/review` gates after every task commit.
4. Final code-reviewer subagent over the full PR diff.
5. Merge after CI green + final review approval.

Suggested commit shape (smallest-first, each lands green):

```
feat: MultiVectorEmbedder Protocol + LateInteractionConfig
feat: chunk_vectors table + SqliteMultiVectorUnitOfWork (schema v6)
feat: PyLateEmbedder + build_multi_vector_embedder (lazy extra)
feat: EmbedChunksMultiVectorStage + ingestion_late_interaction.yaml
feat: LateInteractionScorerStep (MaxSim) + BuildContext wiring
feat(pipelines): chunk_search_late_interaction{,_ranked}.yaml + benchmark variant
docs: CLAUDE.md steps list + default_config.yaml late_interaction block
```
