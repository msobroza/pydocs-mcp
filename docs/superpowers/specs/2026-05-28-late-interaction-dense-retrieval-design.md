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
   `MultiVector = list[np.ndarray]` of length `n_tokens` where each element
   is a 1-D float32 vector of length `dim` (the `MultiVector` arm of the
   existing `Embedding` union — `is_multi_vector` checks for
   `isinstance(emb, list)`, so the outer container MUST be a Python `list`,
   not a stacked 2-D array).

2. Multi-vector persistence — a new storage backend that stores one token
   matrix per chunk as a SQLite BLOB, so the full matrix can be reconstructed
   at query time for MaxSim. Wired through the same `CompositeUnitOfWork` /
   `uow.vectors` seam the single-vector path uses.

3. `LateInteractionScorerStep` (registry key `late_interaction_scorer`) — a
   **MaxSim** re-ranking step registered via `@step_registry.register`. It
   takes the candidate chunks produced by a cheap first-stage retriever
   (BM25 and/or single-vector dense), reconstructs each candidate's token
   matrix from the multi-vector store, embeds the query into its token
   matrix via the `MultiVectorEmbedder`, computes the ColBERT MaxSim score,
   and overwrites `relevance`. It fuses into the existing pipeline via the
   shipped `rrf_fusion` / `weighted_score_interpolation` steps.

A new typed `AppConfig` sub-model (`LateInteractionConfig`, an architectural
twin of `EmbeddingConfig`) configures the provider / model / projection dim /
token caps / pool-factor, and one new preset YAML
(`chunk_search_late_interaction.yaml`) wires the retrieve-then-MaxSim-rerank
shape. Everything is opt-in: the default `chunk_search.yaml` and the default
`embedding:` config are untouched.

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
- `python/pydocs_mcp/storage/protocols.py:350-375` — the `Embedder` Protocol
  docstring already names ColBERT and points at `is_multi_vector` for
  disambiguation, so adding a sibling `MultiVectorEmbedder` Protocol is
  the planned extension shape, not a surprise.
- `python/pydocs_mcp/retrieval/steps/dense_fetcher.py:67-74` +
  `dense_scorer.py:59-64` — both detect a multi-vector query via
  `is_multi_vector` and collapse it to `query_vec[0]` (the first
  token-vector) as a transitional fallback until the multi-vector
  persistence layer lands. `dense_scorer.py:61-63` calls this out as a
  "degraded single-vector fallback" and notes "A future PR adding
  multi-vector persistence can flip this without changing the contract"
  — matching this spec.
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
   `np.ndarray`; a `MultiVectorEmbedder` returns a `MultiVector =
   list[np.ndarray]` of length `n_tokens` (each element a 1-D float32
   vector of length `dim`). `EmbeddingConfig.dim`'s `dim % 8 == 0`
   validator and the `_KNOWN_MODEL_DIMS` cross-check assume one vector
   of `dim` per text.
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
| **(A1)** Reuse the existing `Embedder` Protocol; return a `MultiVector` from the same `embed_query` / `embed_chunks` | Zero new Protocol; `is_multi_vector` already disambiguates | Conflates two contracts on one type — every `Embedder` caller (single-vector `DenseScorerStep`, `EmbedChunksStage`, `TurboQuantUnitOfWork.add_vectors`) grows a runtime branch on `is_multi_vector`; `dim` semantics differ (per-text dim vs per-token projection dim); the shipped `_KNOWN_MODEL_DIMS` + `dim % 8` validators would need bypass flags; ISP violation |
| **(A2)** Add a `multi_vector: bool` capability flag to `Embedder` + the same `embed_query` returning either shape | One Protocol, explicit flag instead of a runtime `isinstance` | Still conflates two contracts; flag would be checked at every call site (same pattern A1 has, just paid via a different attribute); a future provider that does both single + multi vectors per the same model class (e.g. ColBERT-with-a-CLS-pooling-head) needs *two* instances anyway |
| **(A3)** New `MultiVectorEmbedder` Protocol + `PyLateEmbedder` concrete | Mirrors the shipped `Embedder` → FastEmbed/OpenAI and `LlmClient` → OpenAiLlmClient asymmetry; SOLID open/closed for future providers (a local `sentence-transformers` ColBERT, a Jina ColBERT, etc.); call sites can be typed precisely (`MultiVectorEmbedder`, not `Embedder | None` with a runtime test); `from_dict` gates the optional PyLate dependency cleanly | Modest LOC for the Protocol + factory + config wiring |
| **(A4)** Direct PyLate calls inside the scorer step | No abstraction overhead | Couples retrieval to one library; impossible to unit-test MaxSim without loading a real model; no `FakeMultiVectorEmbedder` test oracle |

**Recommended:** Option **A3** — a dedicated `MultiVectorEmbedder` Protocol
(added to `storage/protocols.py` next to `Embedder`) + a `PyLateEmbedder`
concrete under `extraction/strategies/embedders/pylate.py`, dispatched by a
`build_multi_vector_embedder(cfg)` factory. This keeps single-vector and
multi-vector embedding as two segregated contracts: existing `Embedder`
call sites never grow an `is_multi_vector` branch (the `is_multi_vector`
guards in `dense_fetcher.py:68` / `dense_scorer.py:60` stay as backwards-
compat fallbacks for the union type, not as the primary multi-vector
entry point), and a future multi-vector provider is a one-file addition.
The Protocol returns the `MultiVector` arm of the existing `Embedding`
union (`list[np.ndarray]` of length `n_tokens`, each element a 1-D
float32 vector of length `dim`) so the domain model gains no new type
and `is_multi_vector(emb)` already disambiguates downstream.

**Method-name rationale:** PyLate distinguishes encoding via an
`is_query: bool` flag on a single `encode()`, but the codebase's
established convention (FastEmbed and OpenAI Embedders both expose
`embed_query` / `embed_chunks`) is two methods. We follow the codebase
convention and translate it to PyLate inside the adapter — keeping the
naming aligned with `Embedder` so a `build_*_embedder(cfg)` reader sees
one mental model across providers.

**Code example (Protocol + first concrete):**

```python
# storage/protocols.py — additive, Embedder unchanged
@runtime_checkable
class MultiVectorEmbedder(Protocol):
    """Late-interaction (ColBERT-style) embedder: one vector PER TOKEN.

    Distinct from :class:`Embedder` (single pooled vector per text).
    ``embed_query`` / ``embed_chunks`` each return a
    ``MultiVector = list[np.ndarray]`` of length ``n_tokens`` — every
    element is a 1-D float32 ``np.ndarray`` of length ``dim``. The
    outer container is a Python ``list`` (NOT a stacked 2-D array)
    because :func:`pydocs_mcp.models.is_multi_vector` disambiguates the
    ``Embedding`` union via ``isinstance(emb, list)``.

    Implementations MUST L2-normalize each token-vector before
    returning so MaxSim's downstream dot-product IS the cosine — no
    per-query renormalization in :func:`_maxsim` (Decision C).
    """
    dim: int                  # projection dim (ColBERT default 128)
    model_name: str

    async def embed_query(self, text: str) -> MultiVector: ...

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[MultiVector, ...]: ...
```

```python
# extraction/strategies/embedders/pylate.py — first concrete
# Lazy import: PyLate is an OPTIONAL extra (Decision E). Failing fast here
# with an actionable message is the contract — the factory imports this
# module only when the late-interaction preset asks for it.
try:
    from pylate import models
except ImportError as e:  # pragma: no cover - exercised via a monkeypatched import test
    raise ImportError(
        "Late-interaction retrieval requires the 'late-interaction' extra. "
        "Install it with:  pip install 'pydocs-mcp[late-interaction]'  "
        "(pulls pylate + sentence-transformers + torch + transformers; "
        "expect ~1-5 GB depending on CUDA wheel selection)."
    ) from e


# Field defaults intentionally omitted — the canonical defaults live on
# ``LateInteractionConfig`` (Field(default=...)), and the factory always
# constructs us via ``cfg.model_name`` / ``cfg.dim`` / ``cfg.document_length`` /
# ``cfg.query_length`` / ``cfg.pool_factor`` (Decision F + CLAUDE.md
# §"Default values: single source of truth").
@dataclass(frozen=True, slots=True)
class PyLateEmbedder:
    model_name: str
    dim: int                             # ColBERT projection dim (LateOn-Code: 128)
    document_length: int                 # PyLate's own default 180 (NOT 256 / 512)
    query_length: int                    # PyLate's own default 32
    pool_factor: int                     # 1 = no token pooling; >1 trades recall for storage
    # Constructed in __post_init__. Declared as a slot field with
    # default=None so ``frozen=True`` + ``slots=True`` admits
    # ``object.__setattr__`` for the real assignment. The Optional in
    # the annotation is structural (covers the brief pre-__post_init__
    # window inside the constructor); call sites can assume non-None.
    _model: "models.ColBERT | None" = field(init=False, repr=False, default=None, compare=False)

    def __post_init__(self) -> None:
        # ``embedding_size`` is the PyLate kwarg for the projection dim
        # (NOT ``dim``); ``document_length`` / ``query_length`` are
        # PyLate's existing kwarg names. ``pool_factor`` is a
        # ``models.ColBERT(...)`` constructor kwarg (NOT a ``.encode()``
        # kwarg). ``similarity_fn_name`` defaults to ``MaxSim`` inside
        # ColBERT — fine for us, we never call ``model.similarity``.
        # ``frozen=True`` forbids field assignment, so the constructed
        # model goes in via ``object.__setattr__`` (CLAUDE.md
        # §"Code Conventions").
        object.__setattr__(self, "_model", models.ColBERT(
            model_name_or_path=self.model_name,
            embedding_size=self.dim,
            document_length=self.document_length,
            query_length=self.query_length,
            pool_factor=self.pool_factor,
        ))

    async def embed_query(self, text: str) -> MultiVector:
        # ``normalize_embeddings=True`` so downstream MaxSim dot-product
        # equals cosine without a re-normalization pass per query.
        mat = await asyncio.to_thread(
            lambda: self._model.encode(
                [text], is_query=True,
                convert_to_numpy=True, normalize_embeddings=True,
            )[0],
        )
        # Return as ``list[np.ndarray]`` (one 1-D float32 vector per token)
        # so ``is_multi_vector(emb)`` — which tests ``isinstance(emb, list)``
        # — disambiguates correctly. A 2-D stacked array would silently
        # fail that check.
        return [np.asarray(row, dtype=np.float32) for row in mat]

    async def embed_chunks(self, texts):
        if not texts:
            return ()
        mats = await asyncio.to_thread(
            lambda: self._model.encode(
                list(texts), is_query=False,
                convert_to_numpy=True, normalize_embeddings=True,
            ),
        )
        # Each ``mat`` is shape ``(n_tokens, dim)``; unpack the rows into
        # the ``MultiVector = list[np.ndarray]`` arm of ``Embedding``.
        return tuple(
            [np.asarray(row, dtype=np.float32) for row in mat]
            for mat in mats
        )
```

```python
# extraction/strategies/embedders/__init__.py — SOLID factory (sibling of build_embedder)
def build_multi_vector_embedder(cfg: LateInteractionConfig) -> MultiVectorEmbedder:
    if cfg.provider == "pylate":
        from pydocs_mcp.extraction.strategies.embedders.pylate import PyLateEmbedder
        return PyLateEmbedder(
            model_name=cfg.model_name, dim=cfg.dim,
            document_length=cfg.document_length, query_length=cfg.query_length,
            pool_factor=cfg.pool_factor,
        )
    raise ValueError(f"Unknown late-interaction provider: {cfg.provider!r}")
```

### Decision B — multi-vector storage: **fast-plaid** as UoW backend + SQLite **FilterAdapter** for subset filtering (REVISED)

> **2026-05-28 update — design pivot.** The originally-recommended option
> (**B2**, SQLite-BLOB store) is **superseded** by **B5 + FilterAdapter**.
> The trade-off table and reasoning below are preserved for historical
> context; the implementation contract is the **REVISED Recommendation**
> immediately below. The downstream Architecture (§5), Acceptance
> Criteria (§8), and Files-touched (§7) sections of this spec read in
> light of the revised contract — concretely, every reference to
> `chunk_vectors` SQLite table / `SqliteMultiVectorUnitOfWork` /
> `load_matrices(ids)` is replaced by `chunk_multi_vector_ids` SQLite
> table / `FastPlaidUnitOfWork` / `score(query, subset, top_k)`.

**REVISED Recommendation:** Option **B5 with SQLite FilterAdapter coupling**.
The multi-vector index is owned by `fast-plaid` (the Rust-backed PLAID
engine that is already a transitive PyLate dep — choosing it here turns a
transitive dep into a first-class one, no new top-level dep). SQLite owns
the metadata: the existing `chunks` table plus a new
`chunk_multi_vector_ids(chunk_id PK, plaid_doc_id INT UNIQUE, package,
pipeline_hash)` id-mapping table. The hexagonal seam between the two is
the existing `FilterAdapter` Protocol (CLAUDE.md §"`FilterAdapter`
Protocol contract"): retrieval translates the `Filter` tree through
`BuildContext.filter_adapter.adapt(tree, target_field="chunk")` → SQL
WHERE → executes against SQLite to obtain the candidate `chunk_id` list,
then joins through `chunk_multi_vector_ids` to obtain `plaid_doc_id`s,
and finally calls `fast_plaid.search(queries, subset=plaid_doc_ids,
top_k=K)` to score MaxSim within that subset. The subset narrows
fast-plaid's PLAID scan to only the chunks SQLite says are eligible —
both engines do what they do best, and the FilterAdapter contract that
already keeps retrieval steps backend-neutral is the same contract that
gates fast-plaid's `subset` input.

Why this beats the originally-recommended **B2** (SQLite BLOB):

1. **MaxSim engine quality.** fast-plaid implements full PLAID — IVF
   centroids over token-vectors, residual decompression, GPU-friendly
   MaxSim — written in Rust. The B2 numpy MaxSim re-rank works for
   ~100-candidate sets but degrades fast above that; fast-plaid's
   subset-filtered search keeps the recall-vs-cost knob in our hands
   without us reimplementing PLAID.
2. **fast-plaid IS already a transitive dep.** Choosing PyLate as the
   embedder (Decision A) pulls `fast-plaid` in via `pylate` already.
   Promoting it to a direct dep adds zero install footprint and removes
   the lurking version-skew risk of depending on a transitive's API.
3. **The "subset filtering from SQLite via FilterAdapter" pattern is
   exactly the seam this codebase has already invested in.** The
   `FilterAdapter` Protocol exists to let retrieval steps push
   backend-neutral filter trees to whichever store can execute them.
   SQLite executes the metadata side (package / kind / module
   filters); fast-plaid executes the dense MaxSim side. Coupling
   them through `subset=[ids]` is one new join + one new method
   call, not a new architecture.
4. **The B2 storage-blow-up risk is dodged.** B2's worst-case
   `~920 MB/10k chunks` (Decision F's `pool_factor` lever
   notwithstanding) was a real risk for laptop deployments.
   fast-plaid quantizes residuals natively (IVF + 2-bit residuals are
   the PLAID defaults), bringing the on-disk index well under that
   even before `pool_factor`.
5. **Atomicity is eventually-consistent, the SAME shape as
   `TurboQuantUnitOfWork`.** SQLite is canonical: the
   `chunk_multi_vector_ids` table is the source of truth for "which
   chunks have a multi-vector vector." `FastPlaidUnitOfWork` mutates
   the index directory in `commit()` only; a crash between SQLite
   commit and fast-plaid commit leaves orphans, recoverable on next
   reindex via `pipeline_hash` invalidation. This is the same
   trade-off the shipped `TurboQuantUnitOfWork` accepts.

What the B5 rejection in the original table called out:

- *"New heavy service / index dependency"* — moot: fast-plaid is a
  transitive PyLate dep, not a new top-level dep, and is a Rust crate
  loaded into our process (no separate service to run).
- *"Breaks the per-project sidecar files, no server deployment
  model"* — moot: fast-plaid persists to a directory sidecar
  (`~/.pydocs-mcp/{slug}.plaid/`) alongside the existing
  `~/.pydocs-mcp/{slug}.db` SQLite file. Same deployment shape; one
  more sidecar.
- *"Heavy YAGNI for a local docs index"* — re-evaluated: the
  re-ranker-only B2 design has a sub-millisecond MaxSim path for
  ~100 candidates, but late-interaction's promise (better recall via
  token-level matching) wants candidate sets larger than ~100 to be
  worth the cost. B5 makes larger candidate sets affordable without
  reimplementing PLAID.

### Decision B (original, superseded) — multi-vector storage trade-off table for historical context

**Question:** Where do the N-per-chunk token vectors live, and how is the
`chunk → vectors` mapping persisted?

Per-1k-chunks rough sizes assume a representative late-interaction profile
(LateOn-Code projection dim 128, mean 80 stored tokens / chunk after
`pool_factor=2` pooling, fp32 → `80 × 128 × 4 = 40 KB / chunk`; cap is
`document_length × dim × 4 = 180 × 128 × 4 = ~92 KB / chunk`). Compare with
single-vector dense at TurboQuant 4-bit: `~0.18 KB / chunk` —
**multi-vector storage is two orders of magnitude heavier**, which is the
core trade this decision must respect.

| Option | Storage / 1k chunks | Pros | Cons |
|---|---|---|---|
| **(B1)** Token-ANN via TurboQuant: assign each token-vector a synthetic `uint64` id, store `token_id → chunk_id` in SQLite, run ANN over all token-vectors, group hits back to chunks for MaxSim | ~10-20 MB (4-bit quantized × 80k token rows) | Reuses the shipped quantized ANN; token-level ANN could *also* serve as a first-stage retriever, eliminating the recall ceiling | MaxSim needs the FULL token matrix per candidate, not just the ANN-hit tokens — so we ALSO need a side-store of complete matrices anyway; 4-bit quantization measurably degrades MaxSim precision (4-bit was tuned for L2-cosine on pooled vectors, not for token-level dot-products); the `uint64` id space now means two things (chunk id and token id) — a leaky abstraction across the whole codebase including FilterAdapter |
| **(B2)** `chunk_vectors` SQLite BLOB table: one float32 `(n_tokens, dim)` matrix per chunk row | ~40 MB | One row per chunk (no N× id explosion); FULL float32 (no quantization loss in MaxSim); `chunk_id` space stays single-meaning; atomic with SQLite via the existing UoW; trivially correct test oracle (numpy MaxSim); zero new file format | Linear MaxSim scan over the candidate set (bounded ~100 by the first stage — acceptable); float32 BLOBs are bulky on disk |
| **(B3)** NPY / parquet sidecar files (one file per chunk, OR one parquet per package with a row group per chunk) | ~40 MB | Same fp32 fidelity as B2; native numpy / parquet tooling for offline inspection | Two file formats (SQLite + parquet) to keep coherent on every reindex; parquet column-store has zero advantage when reads are always whole-row (per-chunk matrix); per-chunk files explode inode count; UoW atomicity now spans two backends with two different commit shapes |
| **(B4)** FAISS index per package (one FAISS `IndexHNSWFlat` per chunk, OR one global index with per-chunk slicing) | ~40-60 MB (with HNSW graph overhead) | Production-grade ANN | Per-chunk FAISS index is degenerate (N too small); global index re-introduces the B1 leaky-id problem; adds a `faiss-cpu` runtime dep (~70 MB) entirely for storage we already get from SQLite |
| **(B5)** External multi-vector engine (PLAID via `fast-plaid` — already a transitive PyLate dep — or a vector DB with native ColBERT support) | varies | Production-grade ANN over token-vectors at scale | New heavy service / index dependency; breaks the "per-project sidecar files, no server" deployment model; the `fast-plaid` Rust extension would link from our wheel — heavy YAGNI for a local docs index |

**Recommended:** Option **B2** — a dedicated `MultiVectorStore` backed by a new
`chunk_vectors` SQLite table (one BLOB row per chunk: the full
`(n_tokens, dim)` float32 matrix), with MaxSim computed in-memory at query time
over a candidate set produced by a cheap first stage (BM25 and/or single-vector
dense). This is the re-rank shape PyLate itself recommends for small/medium
corpora and matches our scale: a per-project docs index where the candidate set
is already capped at ~100 by an upstream `top_k_filter`.

The numbers above also justify why we DON'T need ANN over token-vectors today:
a worst-case 100-candidate × 80-token × 32-query-token MaxSim is two matmuls
of `(80,128) @ (32,128).T` then a row-max-then-sum — sub-millisecond on cpu
numpy. ANN earns its keep when the alternative is millions of cosines; we
have ~3,200 cosines worst-case per query.

Storage-pressure escape hatch: `pool_factor` (Decision F) is the
first-line lever — `pool_factor=2` halves on-disk size, `pool_factor=3`
keeps 33%, all without a new quantization codec. Float32→int8 quantization
of the BLOB stays out-of-scope unless a real on-disk measurement exceeds
the documented budget.

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
# storage/protocols.py — additive Protocol mirroring the shipped VectorStore
@runtime_checkable
class MultiVectorStore(Protocol):
    """Typed contract for the multi-vector backend (chunk -> token matrix).

    Mirrors the shipped single-vector ``VectorStore`` surface
    (``add_vectors`` / ``remove_vectors`` / ``clear_all``) plus the new
    ``load_matrices(ids)`` reader consumed by
    :class:`LateInteractionScorerStep`. Wiring a typed Protocol here lets
    :class:`BuildContext` and :class:`UnitOfWork` consumers narrow the
    ``uow.vectors: object`` slot to a real type when the late-interaction
    deployment is active, instead of relying on duck typing.
    """

    async def add_vectors(
        self, ids: Sequence[int], embeddings: Sequence[Embedding],
    ) -> None: ...

    async def remove_vectors(self, ids: Sequence[int]) -> None: ...

    async def clear_all(self) -> None: ...

    async def load_matrices(
        self, ids: Sequence[int],
    ) -> dict[int, np.ndarray]: ...
```

```python
# storage/multi_vector_store.py — the dedicated backend (sketch)
@dataclass(frozen=True, slots=True)
class SqliteMultiVectorUnitOfWork:
    """UoW child for chunk_vectors — mirrors TurboQuantUnitOfWork's surface so
    CompositeUnitOfWork dispatches uow.vectors to it the same way. Stores ONE
    token matrix per chunk id. Multi-vector inputs are the expected shape
    here (the inverse of TurboQuantUnitOfWork). Satisfies the
    :class:`MultiVectorStore` Protocol above."""

    async def add_vectors(
        self, ids: Sequence[int], embeddings: Sequence[Embedding],
    ) -> None:
        # Each emb is a MultiVector = list[np.ndarray] of length n_tokens;
        # stacked into a single (n_tokens, dim) float32 ndarray -> one BLOB row.
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
| **(C1)** Late-interaction **fetcher** (token-ANN generates candidates from scratch via the B1 token-ANN we rejected) | One-stage retrieval; no recall ceiling | Requires the B1 token-ANN we rejected for fidelity reasons; unbounded MaxSim cost; duplicates BM25 / dense candidate generation; would require a separate token-id space |
| **(C2)** Late-interaction **re-ranker** over an upstream candidate set | MaxSim cost bounded by the upstream `top_k_filter.k` knob in the preset YAML; reuses the shipped BM25 / dense fetchers as the recall stage; matches PyLate's documented "retrieve then rerank" pattern AND the production ColBERT-v2 / PLAID deployment shape; composes as a plain `RetrieverStep` reading `state.candidates` | Recall ceiling is the first stage's (a relevant doc the first stage misses can't be recovered) — mitigated by (a) widening first-stage `top_k` and (b) fusing BM25+dense+late branches via RRF so any branch finding a doc surfaces it |

**Recommended:** Option **C2** — a re-ranker. `LateInteractionScorerStep` reads
`state.candidates` (the chunk list produced by an upstream `chunk_fetcher`
and/or `dense_fetcher`), loads each candidate's token matrix from the
`MultiVectorStore` via `uow.vectors`, embeds the query into its token matrix,
computes MaxSim, overwrites `relevance`, and writes the re-ranked list back.
It is the dense analogue of `DenseScorerStep` (read candidates → score →
write) — same shape, different math. Bounding MaxSim to the candidate set
makes the cost predictable and lets the step publish to a scratch branch for
fusion exactly like the existing scorers.

**Mitigating the recall ceiling.** The standard ColBERT-rerank trade-off
in the literature is mitigated three ways, all of which the YAML preset
must use: (1) generous first-stage `top_k` (preset ships `k=100`, well
above the typical `k=10-20` for direct return); (2) parallel BM25 + dense
recall so the union covers more of the corpus than either alone; (3) RRF
over the late-interaction branch alongside the recall branches, so a
top-ranked chunk in *any* branch survives. The combination keeps the
effective recall ceiling at roughly `recall(BM25 ∪ dense) ≥ 90%` on
typical code-search corpora — well above the gain MaxSim re-rank
contributes on top.

**Code example (the MaxSim core + step body):**

```python
def _maxsim(query_mat: np.ndarray, doc_mat: np.ndarray) -> float:
    """ColBERT MaxSim: sum over query tokens of the max cosine to any doc token.

    Inputs MUST be L2-normalized per row — the ``PyLateEmbedder`` does this
    at encode time via ``normalize_embeddings=True``. Skipping the
    per-query renormalization here saves an O(n_q * dim) op on every
    candidate scored.
    """
    # (nq, dim) @ (nd, dim).T -> (nq, nd) similarity matrix; row-max -> sum.
    return float((query_mat @ doc_mat.T).max(axis=1).sum())


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
        # ``embed_query`` returns ``MultiVector = list[np.ndarray]`` per the
        # Protocol; stack into a single ``(nq, dim)`` matrix once for the
        # vectorized MaxSim dot-product across every candidate.
        query_tokens = await self.embedder.embed_query(state.query.terms)
        query_mat = np.stack(query_tokens, axis=0) if query_tokens else np.empty((0, 0), dtype=np.float32)
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
        # Branch-safe: build a NEW scratch dict (CLAUDE.md §scratch mutation rule).
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

**Question:** Is PyLate (+ its transitive closure) a hard runtime dependency,
or an optional extra?

PyLate 3.x's direct dependencies (confirmed from upstream `pyproject.toml`)
are: `sentence-transformers==5.3.0`, `transformers>=4.41`, `accelerate>=0.31`,
`datasets>=2.20`, `pandas>=2.2`, `fast-plaid>=1.4.6.270` (compiled Rust
extension), `fastkmeans==0.5.0`, `ujson`, `ninja`. `sentence-transformers`
pulls in `torch` (+ optionally CUDA wheels), `huggingface-hub`, `scikit-learn`,
`pillow`. Floor wheel size on Linux x86_64 CPU-only: ~800 MB - 1.5 GB; with
CUDA wheels (default on Linux): 2-5 GB.

This is **>15x our current ~90 MB transitive footprint** and dwarfs the
entire single-vector + LLM-reasoning dependency surface. Mandatory inclusion
is a non-starter.

| Option | Pros | Cons |
|---|---|---|
| **(E1)** Hard `dependencies` entry | One install path; always available | Default install becomes 1-5 GB; first-run `serve` would import torch; breaks the "~90 MB transitive" install promise in CLAUDE.md §Key Technical Details |
| **(E2)** `[project.optional-dependencies]` extra (`late-interaction`) imported lazily by the factory | Default install stays light; only users who opt in pay the weight; mirrors the `watch = ["watchdog..."]` extra precedent already in `pyproject.toml`; the `build_multi_vector_embedder` factory defers the `import pylate` until first use, so a missing extra surfaces a clear `ImportError` only when the late-interaction preset is actually loaded | Two install paths to document; users must `pip install pydocs-mcp[late-interaction]` |

**Recommended:** Option **E2** — a `late-interaction` optional extra. The
factory's lazy import (the same pattern `build_embedder` / `build_llm_client`
already use) means the cost is paid only when the late-interaction preset is
selected, and a missing extra produces an actionable error at preset-load
time, not a cryptic failure deep in a query.

**Code example (`pyproject.toml` + the actionable error):**

```toml
[project.optional-dependencies]
watch            = ["watchdog>=4.0,<6.0"]
# Heavy (~1-5 GB with torch). Opt-in; default install stays ~90 MB.
late-interaction = ["pylate>=3.0,<4.0"]
```

```python
# extraction/strategies/embedders/pylate.py — top of module
try:
    from pylate import models
except ImportError as e:  # pragma: no cover - exercised via a monkeypatched import test
    raise ImportError(
        "Late-interaction retrieval requires the 'late-interaction' extra. "
        "Install it with:  pip install 'pydocs-mcp[late-interaction]'  "
        "(pulls pylate + sentence-transformers + torch + transformers; "
        "expect ~1-5 GB depending on CUDA wheel selection)."
    ) from e
```

### Decision F — `LateInteractionConfig` is a sibling sub-model with `pool_factor` as a first-class lever

**Question:** Where does the late-interaction provider / model config live,
and which knobs are first-class?

| Option | Pros | Cons |
|---|---|---|
| **(F1)** Add `late_interaction_*` keys onto `EmbeddingConfig` | One config object | Conflates two embedder identities in one model; the `dim % 8 == 0` validator + `_KNOWN_MODEL_DIMS` cross-check are single-vector-specific and would misfire (ColBERT projection dim 128 IS a multiple of 8 by coincidence, but a 96-dim Jina variant isn't); `compute_pipeline_hash` would hash irrelevant fields |
| **(F2)** New `LateInteractionConfig` sub-model + `AppConfig.late_interaction` field | Mirrors `EmbeddingConfig` / `LlmConfig` exactly; own validators + own `compute_pipeline_hash`; YAML namespacing gives future late-interaction tunables an obvious home; `provider` is its own `Literal` so an unknown value fails at load | One more sub-model (cheap) |

**Recommended:** Option **F2** — a dedicated `LateInteractionConfig` in
`retrieval/config.py`, an architectural twin of `EmbeddingConfig` and
`LlmConfig`, exposed as `AppConfig.late_interaction`. Per CLAUDE.md §"MCP API
surface vs YAML configuration", every knob below is YAML-only — never an MCP
param. Per §"Default values: single source of truth", all defaults live once
as `Field(default=...)`.

One notable knob makes it onto the config (beyond the standard embedder kwargs):

- **`pool_factor: int = 1`** — PyLate's built-in token-pooling lever
  (described in PyLate docs as "1/pool_factor of the original tokens";
  default 1 = no pooling). This is the principled lossy-storage knob that
  replaces the rejected "8-bit BLOB quantization" follow-up: a `pool_factor=2`
  halves on-disk size at small recall cost, `pool_factor=3` keeps a third,
  etc. Surfacing it in YAML makes the storage / recall trade-off a one-line
  edit instead of a code change.

The re-rank candidate ceiling is NOT a config field. The shipped preset
YAMLs already pin it via `top_k_filter.k: 100` (Decision H) — the
user-visible knob lives in the preset YAML alongside every other shipped
pipeline's per-stage settings (consistent with how `chunk_search.yaml` and
`chunk_search_hybrid.yaml` expose their `k` values). Adding a separate
`candidate_limit` config field would duplicate the YAML default and
create a SSOT conflict (CLAUDE.md §"Default values: single source of
truth").

**Code example (`LateInteractionConfig`):**

```python
# retrieval/config.py — sibling of EmbeddingConfig / LlmConfig
class LateInteractionConfig(BaseModel):
    """Late-interaction (ColBERT / PyLate) embedder config.

    Twin of :class:`EmbeddingConfig`; consumed by
    ``build_multi_vector_embedder(cfg)``. NO dim%8 / known-model checks —
    ColBERT projection dims (96/128 typical) are model-specific and the
    multi-vector store is dim-agnostic (it stores raw float32 matrices).
    """
    model_config = ConfigDict(extra="forbid")

    provider:        Literal["pylate"] = "pylate"
    model_name:      str = "lightonai/LateOn-Code"
    dim:             int = Field(default=128, ge=1)   # ColBERT projection dim
    # Token caps — PyLate's own defaults (NOT 256 / 512). Override only
    # when a model card explicitly documents different values.
    document_length: int = Field(default=180, ge=8)
    query_length:    int = Field(default=32,  ge=4)
    # Token-pooling knob: 1 keeps all tokens; >1 keeps 1/pool_factor.
    # The principled storage / recall lever — replaces the rejected
    # "BLOB int8-quantize" follow-up.
    pool_factor:     int = Field(default=1, ge=1)
    # NOTE: no ``candidate_limit`` field. The re-rank ceiling lives in
    # the preset YAML (``top_k_filter.k: 100``) as the single source of
    # truth — see Decision F prose above.

    def compute_pipeline_hash(self) -> str:
        identity = "|".join([
            self.provider, self.model_name, str(self.dim),
            str(self.document_length), str(self.query_length),
            str(self.pool_factor),
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
| Reuse `rrf_fusion` (rank-based, scale-free) | Already shipped + tested; rank-based fusion is robust to MaxSim's unbounded score scale (a MaxSim score is `Σ` of `n_query_tokens` cosines, range `[0, n_q]` — depends on query length); the `publish_to` → `branch_keys` convention is identical to the dense branch | None for the default fuse |
| Reuse `weighted_score_interpolation` (min-max normalized blend) | Already shipped; lets a power user weight MaxSim higher than BM25 | Needs all branches present (raises on missing) — fine for a fixed preset |

**Recommended:** reuse both — `rrf_fusion` as the default in the shipped
preset (rank-based fusion sidesteps MaxSim's query-length-dependent
magnitude), with `weighted_score_interpolation` available as a one-line
YAML swap for users who want to weight the late-interaction branch.
`LateInteractionScorerStep` publishes its ranking to
`state.scratch[<branch>.ranked]` via `publish_to`, exactly like
`TopKFilterStep` does for the BM25 / dense branches, so the existing
fusers consume it with zero new code. No new fuser ships.

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
| New `EmbedChunksMultiVectorStage` registered via `@stage_registry.register("embed_chunks_multi_vector")` | SRP — one stage per embedder contract; takes a `MultiVectorEmbedder`; splices the `MultiVector = list[np.ndarray]` (length `n_tokens`) onto `Chunk.embedding` (the `MultiVector` arm of the `Embedding` union); a new `ingestion_late_interaction.yaml` swaps `embed_chunks` for it | One more stage (cheap; matches "one stage per file" convention) |

**Recommended:** Option 2 — a new `EmbedChunksMultiVectorStage`. It mirrors
`EmbedChunksStage` (same content-hash skip set, same batch loop, same
`pipeline_hash` interaction) but calls `MultiVectorEmbedder.embed_chunks`
and stores the `MultiVector` into `Chunk.embedding`. A new ingestion preset
(`ingestion_late_interaction.yaml`) wires it in place of `embed_chunks`. The
existing `IndexingService._maybe_write_vectors` → `uow.vectors.add_vectors`
path then forwards each `MultiVector` (a `list[np.ndarray]` of length
`n_tokens`) to the `SqliteMultiVectorUnitOfWork`, which stacks it into a
single `(n_tokens, dim)` float32 BLOB row — no change to the service,
only to which vector UoW the composite holds.

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
        embs = await self.embedder.embed_chunks(tuple(c.text for c in batch))
        # embs[i] is a MultiVector = list[np.ndarray] of length n_tokens
        # (each element a 1-D float32 vector of length dim) -> Chunk.embedding
        # (the MultiVector arm of the Embedding union).
        ...
```

### Decision J — `pipeline_hash` already invalidates on a multi-vector embedder swap (one-line wiring)

**Question:** How does the chunk-level cache know to re-embed when the
late-interaction model changes?

| Option | Pros | Cons |
|---|---|---|
| New bespoke invalidation path for multi-vector | Explicit | Duplicates the shipped `pipeline_hash` mechanism |
| Fold `late_interaction.compute_pipeline_hash()` into `AppConfig.ingestion_pipeline_hash` when the multi-vector ingestion pipeline is active | Reuses the shipped cache-invalidation seam verbatim; a model swap (or a token-cap / pool-factor change) flips every chunk's `content_hash` → diff-merge re-embeds via the existing path; no "force re-embed" code | Must include the multi-vector embedder identity in the hash input |

**Recommended:** Option 2 — reuse `pipeline_hash`. `AppConfig.ingestion_pipeline_hash`
already hashes `embedding.compute_pipeline_hash() + ingestion-YAML bytes`.
When the active ingestion pipeline is the multi-vector one, the hash input
additionally folds in `late_interaction.compute_pipeline_hash()`, so changing
`late_interaction.model_name` (or `document_length` or `pool_factor`, which
change the stored matrices) invalidates every chunk hash and the existing
diff-merge re-embeds. The `chunk_vectors.model_name` column (Decision B) is
the defense-in-depth guard: a stale matrix from a different model is never
MaxSim-scored against a new query encoder. No new invalidation code path.

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
2. query_tokens = await embedder.embed_query(state.query.terms)   # MultiVector = list[np.ndarray]
   query_mat    = np.stack(query_tokens, axis=0)                  # (nq, dim) float32, L2-normalized
   (PyLate prepends [Q] + pads to query_length with [MASK] for query expansion;
    asyncio.to_thread wraps the torch call; stacking the per-token list into
    a single 2-D matrix once lets _maxsim use one vectorized matmul per candidate)
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
6. if publish_to: new_scratch[publish_to] = ranked
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
| **`late_interaction_scorer`** | `state.candidates`, `MultiVectorEmbedder.embed_query`, `uow.vectors.load_matrices` | `state.candidates` re-ranked by MaxSim | If `publish_to` set: writes the ranking to a fresh scratch dict |
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

The composite UoW is built by ONE `build_uow_factory(config)` entry point
in `storage/factories.py`. A single dispatch helper
`build_vectors_uow_child(config)` picks the vector child —
`TurboQuantUnitOfWork` by default, `SqliteMultiVectorUnitOfWork` when the
active ingestion pipeline references `embed_chunks_multi_vector` (detected
via the shared `_pipeline_uses_step_type` helper). The three composition
roots (`storage/factories.py`, `server.py`, `__main__.py`) all call the
same `build_uow_factory(...)` — no per-site `if late_interaction:`
branching. `IndexingService._maybe_write_vectors` is unchanged — it
forwards whatever `Chunk.embedding` shape the stage produced to
`uow.vectors.add_vectors`, and the multi-vector UoW stores it as a BLOB.

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
- Sidecar parquet / NPY storage (Decision B3 — rejected).
- FAISS per-package indices (Decision B4 — rejected).
- An external PLAID / vector-DB engine (Decision B5 — rejected).
- A second multi-vector provider (Jina ColBERT, a local sentence-transformers
  ColBERT) — SOLID-extensible later via one factory branch.
- BLOB-level fp32→int8 quantization — `pool_factor` (Decision F) is the
  shipped storage / recall lever; quantization stays a follow-up unless
  real on-disk measurements exceed the documented budget.
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
- `python/pydocs_mcp/storage/factories.py` — introduce single-dispatch
  `build_vectors_uow_child(config)` + `build_uow_factory(config)`; pick
  `SqliteMultiVectorUnitOfWork` over `TurboQuantUnitOfWork` when the active
  ingestion pipeline references `embed_chunks_multi_vector`.
- `python/pydocs_mcp/retrieval/factories.py` — build the multi-vector embedder
  (gated) and thread it into `BuildContext.multi_vector_embedder`.
- `python/pydocs_mcp/server.py` + `python/pydocs_mcp/__main__.py` — call
  the single `build_uow_factory(config)` entry point; the multi-vector
  embedder threads through `build_retrieval_context` when configured.
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
   `isinstance(obj, MultiVectorEmbedder)` at runtime. `embed_query` returns
   a `MultiVector = list[np.ndarray]` of length `n_q_tokens` (each element a
   1-D float32 vector of length `dim`); `embed_chunks` returns a tuple of
   such `MultiVector` lists; every token-vector is L2-normalized
   (`np.allclose(np.linalg.norm(v), 1.0)` for each `v` in each list).
   Verified with `FakeMultiVectorEmbedder` (no torch needed).
2. **AC-2 — `LateInteractionConfig`.** `AppConfig.late_interaction` is a
   `LateInteractionConfig` with `provider: Literal["pylate"]`, `model_name`,
   `dim`, `document_length`, `query_length`, `pool_factor` fields (no
   `candidate_limit` — that lives in the preset YAML's `top_k_filter.k`
   per Decision F's SSOT rationale); YAML overlay loading + env overrides
   (`PYDOCS_LATE_INTERACTION__MODEL_NAME`) work; `extra="forbid"` rejects
   unknown keys.
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
   `sum(max over doc tokens of dot)` for hand-computed L2-normalized small
   matrices; identical query and doc matrices yield `nq` (every query token's
   max dot to itself is 1.0); orthogonal matrices yield ~0; assumes
   L2-normalized inputs (documented in the docstring).
7. **AC-7 — `LateInteractionScorerStep` happy path.** Given a `ChunkList` of
   candidates with ids, a `FakeMultiVectorEmbedder`, and a fake `uow.vectors`
   returning matrices, the step overwrites `relevance` with the MaxSim score,
   sets `retriever_name="late_interaction"`, and sorts descending. Candidates
   without a stored matrix pass through unchanged. Round-trips through
   `to_dict` / `from_dict`.
8. **AC-8 — `LateInteractionScorerStep` `publish_to` + scratch hygiene.** With
   `publish_to` set, the re-ranked list is written to `state.scratch[<key>]`
   AND `state.candidates`; without it, only `state.candidates`. The step
   builds a fresh scratch dict (does NOT mutate `state.scratch` in place) so
   it is safe inside a `ParallelStep` branch (per CLAUDE.md §"RetrieverState
   scratch mutation discipline").
9. **AC-9 — strict gate.** `LateInteractionScorerStep.from_dict` raises
   `ValueError` (pointing at the composition root) when
   `context.multi_vector_embedder is None` OR `context.uow_factory is None`.
10. **AC-10 — `EmbedChunksMultiVectorStage`.** Given chunks and a
    `FakeMultiVectorEmbedder`, the stage splices a `MultiVector =
    list[np.ndarray]` (length `n_tokens`, each element a 1-D float32 vector
    of length `dim`) onto each `Chunk.embedding`; honors the
    `existing_chunk_hashes` skip set; stamps the package `embedding_model`;
    round-trips `to_dict` / `from_dict`; `from_dict` raises without
    `context.multi_vector_embedder`.
11. **AC-11 — `pipeline_hash` invalidation.** With the multi-vector ingestion
    pipeline active, changing `late_interaction.model_name` OR
    `late_interaction.document_length` OR `late_interaction.pool_factor`
    changes `AppConfig.ingestion_pipeline_hash` (so the diff-merge re-embeds).
    With the default single-vector pipeline, `late_interaction` changes do NOT
    affect the hash.
12. **AC-12 — preset YAMLs round-trip.** `chunk_search_late_interaction.yaml`,
    `chunk_search_late_interaction_ranked.yaml`, and
    `ingestion_late_interaction.yaml` each load via the existing factories
    (with a `FakeMultiVectorEmbedder` + fake multi-vector UoW in the
    `BuildContext`), execute against a seeded SQLite + `chunk_vectors` fixture,
    and produce non-empty output.
13. **AC-13 — opt-in: defaults untouched.** `chunk_search.yaml`, the default
    `embedding:` config, and `ingestion.yaml` are byte-unchanged; a default
    `pip install pydocs-mcp` (no extra) imports the package, runs the default
    chunk-search pipeline, and never imports `pylate` / `torch` /
    `sentence_transformers` (asserted via a `sys.modules` check after a
    default `serve` composition).
14. **AC-14 — lazy extra.** With `pylate` not installed, loading the
    late-interaction preset raises the actionable `ImportError` naming
    `pip install 'pydocs-mcp[late-interaction]'`; the rest of the test suite is
    unaffected.
15. **AC-15 — benchmark variant.** `pydocs_late_interaction` is runnable via the
    benchmark CLI and produces mrr / recall@k comparable to `pydocs_hybrid`;
    the RepoQA smoke test skips cleanly when the extra is absent.
16. **AC-16 — full suite green.** `pytest -q`: at least 1367 + (one per AC)
    tests pass. `PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q`: at
    least 283 pass. `ruff check python/ tests/ benchmarks/`: clean.
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
| Model size / cold-start latency (PyLate loads a transformer + tokenizer + projection layer) | Opt-in extra (Decision E) — default users never load it; the model loads once at server/CLI startup, not per query; `asyncio.to_thread` keeps the torch call off the event loop |
| Storage blow-up from N vectors per chunk | Decision B2 stores one BLOB row per chunk (no id explosion); `document_length` caps tokens per chunk (PyLate default 180); `pool_factor` (Decision F) reduces stored tokens proportionally. Risk is bounded + measurable: bytes ≈ `n_chunks × (document_length / pool_factor) × dim × 4`. For a typical project (~10k chunks, document_length=180, pool_factor=1, dim=128) that is ~920 MB worst-case if every chunk hits the cap — flagged in docs; real chunks are far shorter, and `pool_factor=2` halves it |
| PyLate / sentence-transformers / torch / transformers dependency weight (>1 GB CPU-only, 2-5 GB with CUDA wheels) | Optional extra, lazy import, actionable `ImportError`; never in the default dependency closure (AC-13) |
| Indexing-time cost (encoding every chunk through a transformer) | Reuses the per-package content-hash skip (unchanged packages never re-enter the pipeline) + the chunk-level `pipeline_hash` skip; first index is slow (documented), reindex is incremental |
| MaxSim float32 BLOB load cost per query | Bounded by the preset YAML's `top_k_filter.k` (shipped at 100) — the re-rank set, not the whole corpus; matrices loaded in one `load_matrices(ids)` batch query; numpy MaxSim over ~100 small matrices is sub-millisecond |
| Recall ceiling: a relevant doc the first stage misses can't be recovered by MaxSim | Inherent ColBERT re-rank trade-off (Decision C); mitigated by a generous first-stage `top_k` (100), parallel BM25 + dense recall branches, and RRF fusion over (BM25, dense, late) so any branch finding a doc surfaces it |
| `chunk_vectors` matrices outlive a model swap and get MaxSim-scored against a mismatched query encoder | `pipeline_hash` re-embeds on model change (Decision J) AND the `chunk_vectors.model_name` column is a defense-in-depth guard the loader can assert against |
| `[Q]` / `[D]` marker tokens are model-card metadata: a model card that omits or renames them silently shifts the query/doc encoder behavior | PyLate's `ColBERT.__init__` falls back to `"[Q] "` / `"[D] "` defaults when the model card omits the metadata; loud-fail isn't possible without parsing every model card. Mitigation: pin `model_name` in the recommended preset (`lightonai/LateOn-Code`); add a startup log line stating which prefixes resolved (`query_prefix=...`, `document_prefix=...`); document the constraint in the YAML preset comment |
| `do_query_expansion=True` (PyLate default for queries) pads every query to `query_length` with `[MASK]` tokens — a 6-token query becomes a 32-row matrix | Intentional ColBERT-v1 behavior; lets MaxSim attend to "what tokens *could* have been there". Tunable via the underlying PyLate `do_query_expansion` flag if a future config knob is needed; not surfaced in `LateInteractionConfig` v1 (YAGNI) |
| Late-interaction numbers regress vs hybrid on RepoQA | That is what the benchmark is for; a regression is data, not a bug — documented as a follow-up if confirmed (mirrors the LLM-tree-reasoning spec §9 stance) |

## 10. Open items

1. **`lightonai/LateOn-Code` projection dim — TENTATIVE 128, confirm at
   implementation.** ColBERT-v2 and the LateOn-Code paper (arXiv:2508.03555)
   use 128 as the projection-layer output dim, matching PyLate's default
   `embedding_size=128`. Reading the model card's `1_Dense/config.json`
   `out_features` at implementation time confirms (or surfaces) the real
   value; update the `Field(default=...)` to match (single source of truth).
2. **Token-matrix quantization — defer.** With `pool_factor` (Decision F) as
   the shipped lossy-storage lever, BLOB-level fp32→int8 quantization is no
   longer a near-term need. Re-open if real on-disk measurements against
   this repo's own index exceed the budget projected in §9.

(The PyLate kwarg-name uncertainty from the prior draft is now closed:
upstream `pylate/models/colbert.py` confirms `embedding_size` /
`query_length` / `document_length` / `is_query` and PyLate's own defaults
of `embedding_size=128`, `query_length=32`, `document_length=180` — those
are the values inlined in Decisions A and F. The
`do_query_expansion=True` query-padding behavior is documented as a
risk-section caveat rather than an open question.)

## 11. Verification

End-to-end smoke after implementation:

```bash
# Unit + benchmark tests (default install — no torch)
pytest -q                                              # expect 1367 + new
PYTHONPATH=benchmarks/src pytest benchmarks/tests/ -q  # expect 283 + new

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
python -c "import pydocs_mcp.server; import sys; \
           assert 'torch' not in sys.modules and 'sentence_transformers' not in sys.modules"
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
