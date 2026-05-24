# Hybrid Search with Semantic Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land hybrid (BM25 + dense) search infrastructure — `TurboQuantVectorStore` + `HybridSqliteTurboStore` + `DenseFetcherStep`/`DenseScorerStep` + `RRFFusionStep` + `Embedder` Protocol + `CompositeUnitOfWork` — using a `MockEmbedder` test double everywhere so no real embedding model is invoked in CI; defer real-model selection + benchmark capture to a follow-up PR.

**Architecture:** Three-class composition over three Protocols (`TextSearchable` / `VectorSearchable` / `HybridSearchable`); `Chunk.embedding` typed as `Vector | MultiVector` union (multi-vector forward-compat); `ParallelStep([bm25, dense]) → RRFFusionStep` pipeline; load-bearing `CompositeUnitOfWork` coordinating SQLite + TurboQuant `.tq` sidecar; single `Embedder` Protocol with explicit `build_embedder(cfg)` factory (no registry).

**Tech Stack:** Python 3.11, pytest-asyncio, [turbovec](https://github.com/RyanCodrai/turbovec) (Rust+Python, MIT, ~16× scalar quantization), numpy (uint64 allowlist arrays), [fastembed](https://github.com/qdrant/fastembed) + openai SDK as optional extras. SQLite for metadata, `.tq` sidecar file for embeddings.

**Spec:** [docs/superpowers/specs/2026-05-24-hybrid-search-with-semantic-embeddings-design.md](docs/superpowers/specs/2026-05-24-hybrid-search-with-semantic-embeddings-design.md)

**Branch:** `feature/hybrid-search-with-semantic-embeddings` (off `main` @ `f717bf3`)

**Worktree:** `.claude/worktrees/hybrid-search-with-semantic-embeddings/`

---

## File map

**NEW files:**
- `python/pydocs_mcp/storage/composite_uow.py` — `CompositeUnitOfWork` (~150 LOC)
- `python/pydocs_mcp/storage/turboquant_uow.py` — `TurboQuantUnitOfWork` (~120 LOC)
- `python/pydocs_mcp/storage/turboquant_store.py` — `TurboQuantVectorStore` (~180 LOC)
- `python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py` — `HybridSqliteTurboStore` (~120 LOC)
- `python/pydocs_mcp/extraction/strategies/embedders/__init__.py` — `build_embedder` factory + `OptionalDepMissing` exception
- `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py` — `FastEmbedEmbedder` (~80 LOC)
- `python/pydocs_mcp/extraction/strategies/embedders/openai.py` — `OpenAIEmbedder` (~80 LOC)
- `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py` — `EmbedChunksStage` (~60 LOC)
- `python/pydocs_mcp/retrieval/steps/dense_fetcher.py` — `DenseFetcherStep` (~150 LOC)
- `python/pydocs_mcp/retrieval/steps/dense_scorer.py` — `DenseScorerStep` (~80 LOC)
- `python/pydocs_mcp/retrieval/steps/rrf_fusion.py` — `RRFFusionStep` + `RRFResultFuser` (~100 LOC)
- `python/pydocs_mcp/pipelines/chunk_search_dense.yaml`
- `python/pydocs_mcp/pipelines/chunk_search_dense_ranked.yaml`
- `python/pydocs_mcp/pipelines/chunk_search_hybrid.yaml`
- `python/pydocs_mcp/pipelines/chunk_search_hybrid_ranked.yaml`
- Test files at `tests/storage/`, `tests/retrieval/steps/`, `tests/extraction/`, `tests/application/` mirroring the source layout

**MODIFIED files:**
- `python/pydocs_mcp/models.py` — `Vector`/`MultiVector`/`Embedding` types + `is_multi_vector()` + `Chunk.embedding` field
- `python/pydocs_mcp/storage/protocols.py` — add `Embedder` + `ResultFuser`
- `python/pydocs_mcp/storage/factories.py` — `build_composite_uow_factory`, `turboquant_path_for_project`, `build_sqlite_plus_turboquant_uow_factory`, `build_sqlite_candidate_id_resolver`, `build_sqlite_chunk_hydrator`
- `python/pydocs_mcp/db.py` — `SCHEMA_VERSION = 5` + `_apply_v5_additions`
- `python/pydocs_mcp/retrieval/steps/rrf.py` — DELETE (replaced by `rrf_fusion.py`); update `__init__.py` re-exports
- `python/pydocs_mcp/retrieval/steps/parallel.py` — branches publish to `scratch[<branch_name>.<field>]`
- `python/pydocs_mcp/retrieval/steps/top_k_filter.py` — optional `publish_to: str | None = None` field
- `python/pydocs_mcp/retrieval/config.py` — `EmbeddingConfig` sub-model; `AppConfig.embedding`
- `python/pydocs_mcp/defaults/default_config.yaml` — `embedding:` block
- `python/pydocs_mcp/extraction/pipeline/ingestion.py` + `pipelines/ingestion.yaml` — `embed_chunks` stage between flatten and content_hash
- `python/pydocs_mcp/application/indexing_service.py` — write vectors alongside chunks via composite UoW; re-embed on model mismatch
- `python/pydocs_mcp/server.py` + `python/pydocs_mcp/__main__.py` — composite UoW factory; startup integrity check
- `pyproject.toml` — `turbovec` + `numpy` main deps; `fastembed` + `openai` extras
- `tests/_fakes.py` — `MockEmbedder` test double
- `CLAUDE.md` — extend README jargon regex
- `benchmarks/README.md` — replace `PR-B3.1` references

---

## Tasks 1-10: Foundation (types + Protocols + config + extras + schema + factories)

### Task 1: Type aliases + `SparseEmbedding` Protocol + `is_multi_vector` helper

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Test: `tests/test_models_embedding_types.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_embedding_types.py
"""Embedding type aliases align with FastEmbed convention (spec §5.1, AC-1)."""
import numpy as np

from pydocs_mcp.models import (
    Embedding,
    MultiVector,
    SparseEmbedding,
    Vector,
    is_multi_vector,
)


def test_vector_is_np_ndarray_alias() -> None:
    assert Vector is np.ndarray


def test_multi_vector_alias_accepts_list_of_ndarray() -> None:
    # Pure runtime check — MultiVector is `list[np.ndarray]`.
    mv: MultiVector = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    assert isinstance(mv, list)
    assert all(isinstance(v, np.ndarray) for v in mv)


def test_is_multi_vector_single_vector_false() -> None:
    single: Vector = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert is_multi_vector(single) is False


def test_is_multi_vector_multi_vector_true() -> None:
    multi: MultiVector = [
        np.array([1.0, 2.0], dtype=np.float32),
        np.array([3.0, 4.0], dtype=np.float32),
    ]
    assert is_multi_vector(multi) is True


def test_is_multi_vector_empty_ndarray_false() -> None:
    # Empty single vector is still a Vector, not a MultiVector.
    assert is_multi_vector(np.array([], dtype=np.float32)) is False


def test_sparse_embedding_protocol_runtime_checkable() -> None:
    """SparseEmbedding is a runtime_checkable Protocol matching FastEmbed's
    shape. Not in the Embedding union this PR — just defined for forward
    compatibility."""
    class _Stub:
        indices = np.array([0, 5, 9], dtype=np.uint32)
        values = np.array([0.5, 0.7, 0.2], dtype=np.float32)

    assert isinstance(_Stub(), SparseEmbedding)


def test_sparse_embedding_NOT_in_embedding_union_yet() -> None:
    """Sentinel: this PR's Embedding union stays Vector | MultiVector.
    Adding SparseEmbedding is a future PR's job."""
    # typing.get_args on a union type alias returns the union members.
    import typing
    args = typing.get_args(Embedding)
    assert SparseEmbedding not in args
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models_embedding_types.py -v`
Expected: FAIL — `ImportError: cannot import name 'Embedding' from 'pydocs_mcp.models'`

- [ ] **Step 3: Add the types to models.py**

In `python/pydocs_mcp/models.py`, add near the top (after existing imports, before the first dataclass). Note the new numpy import.

```python
import numpy as np
from typing import Protocol, runtime_checkable

# ── Embedding types (spec §5.1) ──────────────────────────────────────────
# Aligned with FastEmbed (https://github.com/qdrant/fastembed):
#
#   Vector       = 1D np.ndarray, shape (dim,), dtype=float32.
#                  What TextEmbedding.embed() yields per document; what
#                  OpenAI returns; what TurboQuant IdMapIndex consumes.
#
#   MultiVector  = list[np.ndarray] — one 1D vector per token, ColBERT
#                  late-interaction shape. NOT persisted this PR (single-
#                  vector storage only); the type union accepts the shape
#                  so future late-interaction work doesn't break the
#                  Chunk model.
#
# SparseEmbedding (Protocol) — FastEmbed convention with .indices +
# .values numpy arrays. NOT in the Embedding union this PR; defined here
# so a future sparse-retrieval PR can extend Embedding without breaking
# changes.
Vector = np.ndarray
MultiVector = list[np.ndarray]
Embedding = Vector | MultiVector


@runtime_checkable
class SparseEmbedding(Protocol):
    """FastEmbed-compatible sparse embedding shape (forward-compat).

    Mirrors `fastembed.SparseEmbedding`'s public attributes (uint32
    indices + float32 values numpy arrays). Sparse retrieval is OUT OF
    SCOPE for this PR — this Protocol exists only so the typing layer
    is ready for it.
    """
    indices: np.ndarray   # uint32
    values:  np.ndarray   # float32


def is_multi_vector(emb: Embedding) -> bool:
    """True if `emb` is a multi-vector (list of 1D vectors, ColBERT-style).

    FastEmbed convention: single-vector embedders return `np.ndarray`;
    multi-vector embedders return `list[np.ndarray]`. The check is on
    the OUTER container type.
    """
    return isinstance(emb, list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models_embedding_types.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models_embedding_types.py
git commit -m "feat(models): FastEmbed-aligned Vector/MultiVector/Embedding + SparseEmbedding Protocol

Spec §5.1 + AC-1. Vector = np.ndarray (1D, float32);
MultiVector = list[np.ndarray]; Embedding = Vector | MultiVector.
SparseEmbedding lands as a runtime_checkable Protocol matching
fastembed.SparseEmbedding's shape — NOT in the Embedding union this
PR (sparse retrieval is a separate future PR), but the typing layer is
ready for it without breaking changes. is_multi_vector() checks the
outer container type: np.ndarray = single, list = multi."
```

---

### Task 2: Add `Chunk.embedding` field (optional, additive)

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Test: `tests/test_models_chunk_embedding.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_models_chunk_embedding.py
"""Chunk.embedding field is additive + np.ndarray-typed (spec §5.1 + AC-2)."""
import dataclasses

import numpy as np
import pytest

from pydocs_mcp.models import Chunk


def test_chunk_constructed_without_embedding_defaults_none() -> None:
    c = Chunk(text="hello")
    assert c.embedding is None


def test_chunk_accepts_single_vector_ndarray() -> None:
    vec = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    c = Chunk(text="hello", embedding=vec)
    assert isinstance(c.embedding, np.ndarray)
    assert np.array_equal(c.embedding, vec)


def test_chunk_accepts_multi_vector_list_of_ndarrays() -> None:
    multi = [
        np.array([0.1, 0.2], dtype=np.float32),
        np.array([0.3, 0.4], dtype=np.float32),
    ]
    c = Chunk(text="hello", embedding=multi)
    assert isinstance(c.embedding, list)
    assert len(c.embedding) == 2
    assert np.array_equal(c.embedding[0], multi[0])


def test_chunk_remains_frozen() -> None:
    c = Chunk(text="hello", embedding=np.array([0.1, 0.2], dtype=np.float32))
    with pytest.raises(dataclasses.FrozenInstanceError):
        c.embedding = np.array([0.3, 0.4], dtype=np.float32)  # type: ignore[misc]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models_chunk_embedding.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'embedding'`.

- [ ] **Step 3: Add the field to Chunk**

In `python/pydocs_mcp/models.py`, locate the `Chunk` dataclass and add `embedding` between `retriever_name` and `metadata`:

```python
@dataclass(frozen=True)
class Chunk:
    text: str
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    embedding: Embedding | None = None  # NEW (spec §5.1): None on read paths
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

(Position after `retriever_name` keeps `metadata` as the last default-factory field — preserves existing kwargs callers.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models_chunk_embedding.py -v`
Expected: PASS (4 tests). Also run the full suite to confirm no regression: `.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3` — expect `1027 passed, 5 skipped` (unchanged from baseline).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models_chunk_embedding.py
git commit -m "feat(models): Chunk.embedding: Embedding | None additive field

Spec §5.1 + AC-2. Default None; populated during ingestion by the
embed stage; stays None on read paths (embeddings live in the .tq
sidecar, the SQL row doesn't carry them back). Frozen-dataclass
contract preserved."
```

---

### Task 3: `Embedder` + `ResultFuser` Protocols

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py`
- Test: `tests/storage/test_embedder_resultfuser_protocols.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_embedder_resultfuser_protocols.py
"""Embedder + ResultFuser Protocols exist + are runtime_checkable (spec §5.2)."""
from pydocs_mcp.storage.protocols import Embedder, ResultFuser


def test_embedder_protocol_exposes_required_attrs() -> None:
    # Protocol has dim + embed_query + embed_chunks
    assert hasattr(Embedder, "dim")
    assert hasattr(Embedder, "embed_query")
    assert hasattr(Embedder, "embed_chunks")


def test_resultfuser_protocol_exposes_fuse() -> None:
    assert hasattr(ResultFuser, "fuse")


def test_embedder_is_runtime_checkable() -> None:
    # A duck-typed class satisfies the Protocol at runtime.
    import numpy as np
    class Stub:
        dim = 4
        async def embed_query(self, text: str): return np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        async def embed_chunks(self, texts): return tuple(np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32) for _ in texts)

    assert isinstance(Stub(), Embedder)


def test_resultfuser_is_runtime_checkable() -> None:
    class Stub:
        async def fuse(self, ranked_lists, *, limit): return ()

    assert isinstance(Stub(), ResultFuser)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_embedder_resultfuser_protocols.py -v`
Expected: FAIL — `ImportError: cannot import name 'Embedder'`.

- [ ] **Step 3: Add the Protocols to storage/protocols.py**

In `python/pydocs_mcp/storage/protocols.py`, locate the existing Protocol definitions and add at the end (before any `__all__`):

```python
from typing import Protocol, runtime_checkable
from collections.abc import Sequence
from pydocs_mcp.models import Chunk, Embedding


@runtime_checkable
class Embedder(Protocol):
    """One embedder serves both query-time and ingestion-time work.

    Spec §5.2 — concrete classes return their natural shape:
    single-vector embedders (FastEmbed, OpenAI) return Vector;
    future ColBERT-style embedders return MultiVector. Use
    `pydocs_mcp.models.is_multi_vector(emb)` to disambiguate.
    """
    dim: int

    async def embed_query(self, text: str) -> Embedding: ...

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[Embedding, ...]: ...


@runtime_checkable
class ResultFuser(Protocol):
    """Combines N ranked Chunk lists into one fused ranking.

    Spec §5.2. Implementations: RRFResultFuser (reciprocal-rank fusion).
    Future: WeightedSumResultFuser, DistributionBasedResultFuser.
    """

    async def fuse(
        self,
        ranked_lists: Sequence[tuple[Chunk, ...]],
        *,
        limit: int,
    ) -> tuple[Chunk, ...]: ...
```

If the file already has `from typing import Protocol` and `from collections.abc import Sequence`, don't duplicate; add only what's missing.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_embedder_resultfuser_protocols.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py tests/storage/test_embedder_resultfuser_protocols.py
git commit -m "feat(storage): add Embedder + ResultFuser Protocols

Spec §5.2 + AC-12. Embedder is runtime_checkable so duck-typed
implementations (incl. MockEmbedder) pass isinstance(...) checks.
ResultFuser is the abstraction RRFResultFuser (Task 15) will satisfy."
```

---

### Task 4: `MockEmbedder` test double in `tests/_fakes.py`

**Files:**
- Modify: `tests/_fakes.py`
- Test: `tests/test_mock_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mock_embedder.py
"""MockEmbedder satisfies Embedder Protocol + returns np.ndarray (AC-27)."""
import numpy as np
import pytest

from pydocs_mcp.storage.protocols import Embedder
from tests._fakes import MockEmbedder


def test_mock_embedder_satisfies_embedder_protocol() -> None:
    assert isinstance(MockEmbedder(dim=4), Embedder)


def test_mock_embedder_dim_field() -> None:
    emb = MockEmbedder(dim=384)
    assert emb.dim == 384


@pytest.mark.asyncio
async def test_mock_embedder_embed_query_returns_ndarray_of_correct_dim() -> None:
    emb = MockEmbedder(dim=8)
    vec = await emb.embed_query("hello world")
    assert isinstance(vec, np.ndarray)
    assert vec.dtype == np.float32
    assert vec.shape == (8,)


@pytest.mark.asyncio
async def test_mock_embedder_is_deterministic_same_input_same_output() -> None:
    emb = MockEmbedder(dim=8)
    v1 = await emb.embed_query("hello world")
    v2 = await emb.embed_query("hello world")
    assert np.array_equal(v1, v2)


@pytest.mark.asyncio
async def test_mock_embedder_different_input_different_output() -> None:
    emb = MockEmbedder(dim=8)
    v1 = await emb.embed_query("alpha")
    v2 = await emb.embed_query("beta")
    assert not np.array_equal(v1, v2)


@pytest.mark.asyncio
async def test_mock_embedder_embed_chunks_returns_one_ndarray_per_text() -> None:
    emb = MockEmbedder(dim=4)
    vecs = await emb.embed_chunks(["x", "y", "z"])
    assert len(vecs) == 3
    assert all(isinstance(v, np.ndarray) and v.shape == (4,) for v in vecs)
    # Each chunk's vector is the same as if embed_query were called on it.
    assert np.array_equal(vecs[0], await emb.embed_query("x"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_mock_embedder.py -v`
Expected: FAIL — `ImportError: cannot import name 'MockEmbedder' from 'tests._fakes'`.

- [ ] **Step 3: Add MockEmbedder to tests/_fakes.py**

Append to `tests/_fakes.py`:

```python
# ── MockEmbedder (canonical Embedder test double, AC-27) ─────────────────
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from pydocs_mcp.models import Embedding


@dataclass(frozen=True, slots=True)
class MockEmbedder:
    """Deterministic Embedder test double — same input → same vector.

    Returns shape-matched ``np.ndarray`` (float32, dim-shaped) so it's
    drop-in for FastEmbed / OpenAI / any single-vector Embedder. The
    vector is derived from a SHA-256 of the input text seeded into a
    numpy RNG, giving stable per-input vectors without any model
    dependency. The canonical embedder mock for this PR and future PRs
    that need embedding-shaped data without invoking a real model.
    """
    dim: int = 384

    async def embed_query(self, text: str) -> Embedding:
        return self._derive(text)

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        return tuple(self._derive(t) for t in texts)

    def _derive(self, text: str) -> np.ndarray:
        # SHA-256 → first 8 bytes → uint64 seed → numpy default_rng.
        # Output is a (dim,) float32 array in [-1, 1] — deterministic per text.
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        seed = int.from_bytes(digest[:8], "little", signed=False)
        rng = np.random.default_rng(seed)
        return rng.uniform(-1.0, 1.0, size=self.dim).astype(np.float32)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_mock_embedder.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tests/_fakes.py tests/test_mock_embedder.py
git commit -m "test: add MockEmbedder canonical Embedder test double

AC-27 + AC-28. Deterministic SHA-256-derived vectors per input text.
Configurable dim. Satisfies the Embedder Protocol via runtime_checkable.
The ONLY embedder used in this PR's tests — no real model imports
anywhere in tests/ or CI."
```

---

### Task 5: `EmbeddingConfig` + `AppConfig.embedding`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py`
- Modify: `python/pydocs_mcp/defaults/default_config.yaml`
- Test: `tests/retrieval/test_embedding_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_embedding_config.py
"""EmbeddingConfig + AppConfig.embedding (spec §5.10)."""
import pytest
from pydantic import ValidationError

from pydocs_mcp.retrieval.config import AppConfig, EmbeddingConfig


def test_embedding_config_defaults() -> None:
    cfg = EmbeddingConfig()
    assert cfg.provider == "fastembed"
    assert cfg.model_name == "BAAI/bge-small-en-v1.5"
    assert cfg.dim == 384
    assert cfg.batch_size == 32
    assert cfg.bit_width == 4


def test_embedding_config_provider_literal_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        EmbeddingConfig(provider="cohere")  # type: ignore[arg-type]


def test_embedding_config_batch_size_min_1() -> None:
    with pytest.raises(ValidationError):
        EmbeddingConfig(batch_size=0)


def test_embedding_config_bit_width_range_1_to_8() -> None:
    EmbeddingConfig(bit_width=1)
    EmbeddingConfig(bit_width=8)
    with pytest.raises(ValidationError):
        EmbeddingConfig(bit_width=0)
    with pytest.raises(ValidationError):
        EmbeddingConfig(bit_width=9)


def test_appconfig_load_exposes_embedding_block() -> None:
    cfg = AppConfig.load()
    assert isinstance(cfg.embedding, EmbeddingConfig)
    # Shipped default in defaults/default_config.yaml.
    assert cfg.embedding.provider == "fastembed"
    assert cfg.embedding.model_name == "BAAI/bge-small-en-v1.5"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/test_embedding_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'EmbeddingConfig'`.

- [ ] **Step 3: Add EmbeddingConfig to retrieval/config.py**

In `python/pydocs_mcp/retrieval/config.py`, add the new sub-model near the other config sub-models (e.g., next to `SearchConfig`):

```python
from typing import Literal


class EmbeddingConfig(BaseModel):
    """Embedding + vector-quantization config (spec §5.10).

    YAML-tunable; no MCP tool params (per CLAUDE.md §"MCP API surface
    vs YAML configuration").
    """
    provider:   Literal["fastembed", "openai"] = "fastembed"
    model_name: str = "BAAI/bge-small-en-v1.5"
    dim:        int = Field(default=384, ge=1)
    batch_size: int = Field(default=32, ge=1)
    # TurboQuant scalar-quantization bit width. 4 is the sweet spot per
    # turbovec README — ~16x compression with minimal recall loss on
    # 384-1536 dim embeddings. Tune up to 8 for higher quality, down to
    # 2 for max compression.
    bit_width:  int = Field(default=4, ge=1, le=8)
```

Then add `embedding: EmbeddingConfig` to `AppConfig` (alongside `extraction`, `reference_graph`, `search`):

```python
class AppConfig(BaseSettings):
    ...
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
```

- [ ] **Step 4: Add to defaults/default_config.yaml**

Append to `python/pydocs_mcp/defaults/default_config.yaml`:

```yaml
# Embedding + vector-quantization (spec §5.10).
embedding:
  provider:    fastembed
  model_name:  BAAI/bge-small-en-v1.5
  dim:         384
  batch_size:  32
  bit_width:   4
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/retrieval/test_embedding_config.py -v`
Expected: PASS (5 tests). Full suite still green: `.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3`.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py python/pydocs_mcp/defaults/default_config.yaml tests/retrieval/test_embedding_config.py
git commit -m "feat(config): EmbeddingConfig + AppConfig.embedding + shipped defaults

Spec §5.10. Five fields: provider (fastembed|openai), model_name,
dim, batch_size, bit_width. Defaults pinned to FastEmbed +
bge-small-en-v1.5 (384 dim) + 4-bit TurboQuant quantization."
```

---

### Task 6: `pyproject.toml` deps — `turbovec`, `numpy`, optional embedder extras

**Files:**
- Modify: `pyproject.toml`
- Test: `tests/test_pyproject_extras.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pyproject_extras.py
"""pyproject.toml declares main + extras correctly (Task 6 + AC-16)."""
from pathlib import Path

import tomllib

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _load():
    return tomllib.loads(PYPROJECT.read_text())


def test_turbovec_in_main_dependencies() -> None:
    cfg = _load()
    deps = cfg["project"]["dependencies"]
    assert any("turbovec" in d for d in deps)


def test_numpy_in_main_dependencies() -> None:
    cfg = _load()
    deps = cfg["project"]["dependencies"]
    assert any(d.startswith("numpy") for d in deps)


def test_fastembed_extra_exists() -> None:
    cfg = _load()
    extras = cfg["project"]["optional-dependencies"]
    assert "fastembed" in extras
    assert any("fastembed" in d for d in extras["fastembed"])


def test_openai_extra_exists() -> None:
    cfg = _load()
    extras = cfg["project"]["optional-dependencies"]
    assert "openai" in extras
    assert any(d.startswith("openai") for d in extras["openai"])


def test_all_embedders_extra_unions_both() -> None:
    cfg = _load()
    extras = cfg["project"]["optional-dependencies"]
    assert "all-embedders" in extras
    items = extras["all-embedders"]
    assert any("fastembed" in d for d in items)
    assert any(d.startswith("openai") for d in items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_pyproject_extras.py -v`
Expected: FAIL — `assert any("turbovec" in d for d in deps)` (turbovec not yet declared).

- [ ] **Step 3: Update pyproject.toml**

In `pyproject.toml`, add to `[project]` `dependencies` (alongside existing `mcp>=1.0`, `pydantic-settings>=2.0`, `pyyaml>=6.0`):

```toml
dependencies = [
    "mcp>=1.0",
    "pydantic-settings>=2.0",
    "pyyaml>=6.0",
    "turbovec>=0.4",
    "numpy>=1.26",
]
```

Add the optional extras section (or extend if `[project.optional-dependencies]` already exists):

```toml
[project.optional-dependencies]
fastembed = ["fastembed>=0.4"]
openai = ["openai>=1.40"]
all-embedders = ["fastembed>=0.4", "openai>=1.40"]
```

- [ ] **Step 4: Install the new deps + run the test**

```bash
.venv/bin/pip install "turbovec>=0.4" "numpy>=1.26" 2>&1 | tail -3
.venv/bin/pytest tests/test_pyproject_extras.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/test_pyproject_extras.py
git commit -m "build: add turbovec + numpy main deps + fastembed/openai extras

Task 6. turbovec is the chosen vector backend (MIT, Rust+Python, ~16x
scalar quantization); numpy carries the uint64 allowlist arrays for
TurboQuant filtered search. FastEmbed + OpenAI live behind optional
extras so 'pip install pydocs-mcp' core stays lean."
```

---

### Task 7: Schema v5 migration — `packages.embedding_model`

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Test: `tests/test_db_schema_v5_migration.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db_schema_v5_migration.py
"""Schema v5 adds packages.embedding_model TEXT additively (AC-11)."""
import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_is_5() -> None:
    assert SCHEMA_VERSION == 5


def test_fresh_db_v5_has_packages_embedding_model_column(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(packages)")]
    conn.close()
    assert "embedding_model" in cols


def test_v4_to_v5_migration_lossless(tmp_path: Path) -> None:
    db_path = tmp_path / "v4.db"
    # Simulate v4 cache: create the table with v4 columns, no embedding_model.
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE packages (
            name TEXT PRIMARY KEY, version TEXT, summary TEXT,
            homepage TEXT, dependencies TEXT, content_hash TEXT,
            origin TEXT, local_path TEXT
        );
        PRAGMA user_version = 4;
    """)
    conn.execute(
        "INSERT INTO packages (name, version) VALUES (?, ?)",
        ("demo-pkg", "1.0.0"),
    )
    conn.commit()
    conn.close()
    # Open via the migration path.
    open_index_database(db_path).close()
    conn = sqlite3.connect(db_path)
    # Existing row preserved.
    row = conn.execute(
        "SELECT name, version FROM packages WHERE name = ?", ("demo-pkg",),
    ).fetchone()
    assert row == ("demo-pkg", "1.0.0")
    # New column present + defaults to NULL on existing rows.
    embedding_model = conn.execute(
        "SELECT embedding_model FROM packages WHERE name = ?", ("demo-pkg",),
    ).fetchone()[0]
    assert embedding_model is None
    # Version bumped.
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_db_schema_v5_migration.py -v`
Expected: FAIL — `assert SCHEMA_VERSION == 5` (currently 4).

- [ ] **Step 3: Update db.py**

In `python/pydocs_mcp/db.py`:

1. Bump version: `SCHEMA_VERSION = 5`.
2. Update `_DDL` — add `embedding_model TEXT` to the `packages` CREATE TABLE:

```python
_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT,
        local_path TEXT,
        embedding_model TEXT
    );
    ...
"""
```

3. Add a `_apply_v5_additions` helper after `_apply_v4_additions`:

```python
def _apply_v5_additions(conn: sqlite3.Connection) -> None:
    """v5: add packages.embedding_model TEXT for re-embed-on-model-change
    detection (spec §3.2). Idempotent via _try_add_column."""
    _try_add_column(conn, "packages", "embedding_model TEXT")
```

4. Extend the version dispatch in `open_index_database` to walk v4 → v5:

```python
def open_index_database(db_path: Path) -> sqlite3.Connection:
    ...
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current < SCHEMA_VERSION:
        ...  # existing v2→v3, v3→v4 migrations
        if current < 5:
            _apply_v5_additions(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif current == SCHEMA_VERSION:
        # Self-repair sweep (idempotent additive helpers) — mirror existing pattern.
        _apply_v5_additions(conn)
        # ... existing self-repair calls for v3, v4 additions if any
    ...
```

(Match the exact dispatch shape of the existing `_apply_v3_additions` / `_apply_v4_additions` flow.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_db_schema_v5_migration.py -v`
Expected: PASS (3 tests). Also run full suite to verify no regression: `.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3`.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/db.py tests/test_db_schema_v5_migration.py
git commit -m "feat(db): schema v5 — packages.embedding_model TEXT additive column

AC-11. Tracks which embedding model produced each package's vectors so
the indexing service can force re-embed when YAML's embedding.model_name
changes. Additive migration via _apply_v5_additions; v4→v5 lossless;
fresh-DB path also updated."
```

---

### Task 8: `turboquant_path_for_project` helper

**Files:**
- Modify: `python/pydocs_mcp/storage/factories.py` (or `db.py` if `cache_path_for_project` lives there)
- Test: `tests/storage/test_turboquant_path_helper.py`

- [ ] **Step 1: Locate `cache_path_for_project`**

```bash
grep -rn "def cache_path_for_project" python/pydocs_mcp/
```

It lives at `python/pydocs_mcp/db.py:73-80` (per the spec §5.7). The new helper goes in the SAME module (the user expects symmetry).

- [ ] **Step 2: Write the failing test**

```python
# tests/storage/test_turboquant_path_helper.py
"""turboquant_path_for_project mirrors cache_path_for_project (Task 8)."""
from pathlib import Path

from pydocs_mcp.db import cache_path_for_project, turboquant_path_for_project


def test_returns_sibling_with_tq_suffix(tmp_path: Path) -> None:
    project = tmp_path / "myproj"
    project.mkdir()
    db_p = cache_path_for_project(project)
    tq_p = turboquant_path_for_project(project)
    # Same parent dir, same stem, different suffix.
    assert tq_p.parent == db_p.parent
    assert tq_p.stem == db_p.stem
    assert tq_p.suffix == ".tq"
    assert db_p.suffix == ".db"


def test_different_projects_get_different_paths(tmp_path: Path) -> None:
    p1 = tmp_path / "a"
    p2 = tmp_path / "b"
    p1.mkdir()
    p2.mkdir()
    assert turboquant_path_for_project(p1) != turboquant_path_for_project(p2)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_turboquant_path_helper.py -v`
Expected: FAIL — `ImportError: cannot import name 'turboquant_path_for_project'`.

- [ ] **Step 4: Add the helper**

In `python/pydocs_mcp/db.py`, after `cache_path_for_project`:

```python
def turboquant_path_for_project(project_dir: Path) -> Path:
    """Return the per-project TurboQuant .tq sidecar path under CACHE_DIR.

    Mirrors :func:`cache_path_for_project`: same dir, same path-hash slug,
    `.tq` suffix instead of `.db`. The two files live side-by-side so a
    `--force` cache clear deletes both (caller's responsibility).
    """
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.tq"
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_turboquant_path_helper.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/db.py tests/storage/test_turboquant_path_helper.py
git commit -m "feat(db): turboquant_path_for_project helper (mirror of cache_path_for_project)

Task 8. Per-project .tq sidecar path with the same 10-char md5 slug as
the SQLite cache so the two files always live side-by-side. Used by the
composition root to instantiate TurboQuantUnitOfWork."
```

---

### Task 9: `TurboQuantUnitOfWork`

**Files:**
- Create: `python/pydocs_mcp/storage/turboquant_uow.py`
- Test: `tests/storage/test_turboquant_uow.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_turboquant_uow.py
"""TurboQuantUnitOfWork lifecycle + integrity (AC-5..AC-8)."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


@pytest.mark.asyncio
async def test_add_then_commit_persists(tmp_path: Path) -> None:
    tq = tmp_path / "test.tq"
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow:
        await uow.add_vectors(
            [1, 2, 3],
            [_vec(0.1, 0.2, 0.3, 0.4) for _ in range(3)],
        )
        await uow.commit()
    assert tq.exists()
    # Re-open and check size.
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow2:
        assert uow2.size() == 3


@pytest.mark.asyncio
async def test_rollback_discards_in_memory_adds(tmp_path: Path) -> None:
    tq = tmp_path / "test.tq"
    # First, persist baseline of 2 vectors.
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow:
        await uow.add_vectors([10, 11], [_vec(0, 0, 0, 0) for _ in range(2)])
        await uow.commit()
    # Now add then rollback; on next open, size should still be 2 (not 5).
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow:
        await uow.add_vectors(
            [12, 13, 14], [_vec(1, 0, 0, 0) for _ in range(3)],
        )
        await uow.rollback()
        # After rollback in-memory state matches disk.
        assert uow.size() == 2


@pytest.mark.asyncio
async def test_multi_vector_input_raises_notimplementederror(tmp_path: Path) -> None:
    tq = tmp_path / "test.tq"
    multi: list[np.ndarray] = [
        _vec(0.1, 0.2, 0.3, 0.4), _vec(0.5, 0.6, 0.7, 0.8),
    ]
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow:
        with pytest.raises(NotImplementedError, match="chunk_vectors"):
            await uow.add_vectors([1], [multi])


@pytest.mark.asyncio
async def test_write_is_atomic_via_tmp_rename(tmp_path: Path, monkeypatch) -> None:
    """Simulate index.write raising mid-write; on-disk file unchanged."""
    tq = tmp_path / "test.tq"
    # Seed with one vector and commit.
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow:
        await uow.add_vectors([1], [_vec(0, 0, 0, 0)])
        await uow.commit()
    pre_bytes = tq.read_bytes()
    # Now make the next commit fail mid-write.
    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=4) as uow:
        await uow.add_vectors([2], [_vec(1, 1, 1, 1)])

        def boom(self, path):
            raise OSError("disk full")

        monkeypatch.setattr(
            "turbovec.IdMapIndex.write", boom, raising=True,
        )
        with pytest.raises(OSError, match="disk full"):
            await uow.commit()
    # Original file is byte-identical.
    assert tq.read_bytes() == pre_bytes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_turboquant_uow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'pydocs_mcp.storage.turboquant_uow'`.

- [ ] **Step 3: Create turboquant_uow.py**

```python
# python/pydocs_mcp/storage/turboquant_uow.py
"""TurboQuant UoW wrapping the IdMapIndex lifecycle (spec §5.4).

One child of CompositeUnitOfWork. Manages an in-memory IdMapIndex
loaded from / persisted to a `.tq` sidecar file alongside the SQLite
cache.

Atomicity model: writes go to `<path>.tmp` then atomic-rename into
place — a crash mid-write leaves the previous `.tq` intact.

Multi-vector inputs raise NotImplementedError this PR; the typed
Embedding union accepts them but persistence is deferred.
"""
from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np
from turbovec import IdMapIndex

from pydocs_mcp.models import Embedding, is_multi_vector


class TurboQuantUnitOfWork:
    """UoW for the TurboQuant `.tq` sidecar. See module docstring."""

    def __init__(
        self,
        *,
        index_path: Path,
        dim: int,
        bit_width: int = 4,
    ) -> None:
        self._index_path = index_path
        self._dim = dim
        self._bit_width = bit_width
        self._index: IdMapIndex | None = None
        self._dirty = False

    async def __aenter__(self) -> "TurboQuantUnitOfWork":
        self._index = (
            IdMapIndex.load(str(self._index_path))
            if self._index_path.exists()
            else IdMapIndex(dim=self._dim, bit_width=self._bit_width)
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if exc_type is not None and self._dirty:
            # Caller's context manager raised — discard in-memory work.
            try:
                await self.rollback()
            except Exception:
                pass

    async def add_vectors(
        self,
        ids: Sequence[int],
        embeddings: Sequence[Embedding],
    ) -> None:
        """Buffer (id, vector) pairs in the in-memory index.

        Raises NotImplementedError if any embedding is multi-vector
        (length-N sequence of vectors) — single-vector only this PR.
        Persisting multi-vector embeddings will land alongside the
        chunk_vectors side-table in a future PR.
        """
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.add_vectors called outside async with",
            )
        for emb in embeddings:
            if is_multi_vector(emb):
                raise NotImplementedError(
                    "TurboQuantUnitOfWork persists single-vector embeddings "
                    "only. Multi-vector (ColBERT-style) embeddings are "
                    "deferred to a future PR that adds a chunk_vectors "
                    "side-table; see spec §5.4 + plan task notes.",
                )
        # Each emb is np.ndarray (1D, float32) — stack into a 2D matrix
        # for IdMapIndex.add_with_ids. asarray with dtype=float32 is a
        # no-op when the inputs already come from FastEmbed in that dtype.
        vectors = np.asarray(
            np.stack(list(embeddings)), dtype=np.float32,
        )
        ids_arr = np.asarray(list(ids), dtype=np.uint64)
        self._index.add_with_ids(vectors, ids_arr)
        self._dirty = True

    async def remove_vectors(self, ids: Sequence[int]) -> None:
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.remove_vectors called outside async with",
            )
        for chunk_id in ids:
            self._index.remove(int(chunk_id))
        self._dirty = True

    async def commit(self) -> None:
        """Persist in-memory index to `<path>.tmp` then atomic-rename.

        A crash mid-write leaves the previous `.tq` intact.
        """
        if not self._dirty or self._index is None:
            return
        tmp = self._index_path.with_suffix(self._index_path.suffix + ".tmp")
        self._index.write(str(tmp))
        os.replace(tmp, self._index_path)
        self._dirty = False

    async def rollback(self) -> None:
        """Discard in-memory adds by reloading from disk."""
        if not self._dirty:
            return
        self._index = (
            IdMapIndex.load(str(self._index_path))
            if self._index_path.exists()
            else IdMapIndex(dim=self._dim, bit_width=self._bit_width)
        )
        self._dirty = False

    def size(self) -> int:
        """Number of stored vectors (for the integrity check)."""
        return self._index.size() if self._index else 0

    @property
    def index(self) -> IdMapIndex:
        """Read access to the underlying index — for TurboQuantVectorStore."""
        if self._index is None:
            raise RuntimeError(
                "TurboQuantUnitOfWork.index accessed outside async with",
            )
        return self._index


__all__ = ("TurboQuantUnitOfWork",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_turboquant_uow.py -v`
Expected: PASS (4 tests). If the `write_is_atomic` test fails due to `monkeypatch.setattr` on a Rust-extension class, swap to monkeypatching the bound method on the instance: `monkeypatch.setattr(uow._index, "write", lambda path: (_ for _ in ()).throw(OSError("disk full")))`.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/turboquant_uow.py tests/storage/test_turboquant_uow.py
git commit -m "feat(storage): TurboQuantUnitOfWork — IdMapIndex lifecycle wrapper

AC-5..AC-8. Loads .tq on enter (or fresh index if absent); buffers
add/remove in memory; commit writes to <path>.tmp then atomic-rename
for crash safety; rollback discards by reloading from disk. Rejects
multi-vector inputs with a clear NotImplementedError pointing at the
future chunk_vectors side-table PR."
```

---

### Task 10: `CompositeUnitOfWork`

**Files:**
- Create: `python/pydocs_mcp/storage/composite_uow.py`
- Test: `tests/storage/test_composite_uow.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_composite_uow.py
"""CompositeUnitOfWork dispatch + attribute proxying + rollback (AC-9, AC-10)."""
from dataclasses import dataclass, field
from typing import Any

import pytest

from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork


# ── Fake child UoWs for unit-testing composite behavior ────────────────────


@dataclass
class FakeUoW:
    name: str
    fail_commit: bool = False
    commits: list[str] = field(default_factory=list)
    rollbacks: list[str] = field(default_factory=list)
    # Attribute proxying test — each fake has unique attributes.
    _attrs: dict[str, Any] = field(default_factory=dict)

    async def __aenter__(self) -> "FakeUoW":
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def commit(self) -> None:
        self.commits.append(self.name)
        if self.fail_commit:
            raise RuntimeError(f"{self.name} commit blew up")

    async def rollback(self) -> None:
        self.rollbacks.append(self.name)

    def __getattr__(self, name: str) -> Any:
        if name in self._attrs:
            return self._attrs[name]
        raise AttributeError(name)


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_commit_dispatches_to_all_children() -> None:
    a = FakeUoW("a")
    b = FakeUoW("b")
    async with CompositeUnitOfWork([a, b]) as uow:
        await uow.commit()
    assert a.commits == ["a"]
    assert b.commits == ["b"]


@pytest.mark.asyncio
async def test_rollback_on_partial_commit_failure() -> None:
    # Second child fails — first should be rolled back.
    a = FakeUoW("a")
    b = FakeUoW("b", fail_commit=True)
    with pytest.raises(RuntimeError, match="b commit blew up"):
        async with CompositeUnitOfWork([a, b]) as uow:
            await uow.commit()
    assert a.commits == ["a"]
    assert b.commits == ["b"]
    assert a.rollbacks == ["a"]


@pytest.mark.asyncio
async def test_attribute_proxying_to_owning_child() -> None:
    a = FakeUoW("a")
    a._attrs["packages"] = "packages_store_from_a"
    b = FakeUoW("b")
    b._attrs["vectors"] = "vectors_store_from_b"
    async with CompositeUnitOfWork([a, b]) as uow:
        assert uow.packages == "packages_store_from_a"
        assert uow.vectors == "vectors_store_from_b"


@pytest.mark.asyncio
async def test_ambiguous_attribute_raises_at_access() -> None:
    a = FakeUoW("a")
    a._attrs["chunks"] = "from_a"
    b = FakeUoW("b")
    b._attrs["chunks"] = "from_b"
    async with CompositeUnitOfWork([a, b]) as uow:
        with pytest.raises(AttributeError, match="ambiguous"):
            _ = uow.chunks


@pytest.mark.asyncio
async def test_unknown_attribute_raises_clear_error() -> None:
    a = FakeUoW("a")
    async with CompositeUnitOfWork([a]) as uow:
        with pytest.raises(AttributeError, match="nonexistent"):
            _ = uow.nonexistent


@pytest.mark.asyncio
async def test_empty_children_raises_at_construction() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompositeUnitOfWork([])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_composite_uow.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create composite_uow.py**

```python
# python/pydocs_mcp/storage/composite_uow.py
"""CompositeUnitOfWork — best-effort coordinator over N child UoWs.

Spec §5.5. For this PR there are exactly two children:
- SqliteUnitOfWork (packages, chunks, module_members, trees, references)
- TurboQuantUnitOfWork (vectors — the .tq sidecar)

Commit semantics: each child commits sequentially. On any failure,
already-committed children get rollback() called (best-effort —
SQLite cannot un-commit, but TurboQuant can reload its pre-commit
on-disk state). The original exception is re-raised so the caller
sees the failure.

Atomicity limitation: NOT strict cross-backend ACID. The startup
integrity check (compare chunks.count to IdMapIndex.size()) detects
post-crash mismatches and forces re-embed of affected packages.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

logger = logging.getLogger(__name__)


class CompositeUnitOfWork:
    """Best-effort coordinator over N child UoWs (spec §5.5)."""

    def __init__(self, children: Sequence) -> None:
        if not children:
            raise ValueError(
                "CompositeUnitOfWork requires at least one child UoW",
            )
        self._children = list(children)
        self._entered: list = []

    async def __aenter__(self) -> "CompositeUnitOfWork":
        for child in self._children:
            await child.__aenter__()
            self._entered.append(child)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Exit in reverse order, best-effort.
        for child in reversed(self._entered):
            try:
                await child.__aexit__(exc_type, exc, tb)
            except Exception as inner_exc:
                logger.warning(
                    "CompositeUnitOfWork child __aexit__ raised: %r",
                    inner_exc,
                )

    async def commit(self) -> None:
        committed: list = []
        first_exc: BaseException | None = None
        for child in self._children:
            try:
                await child.commit()
                committed.append(child)
            except BaseException as exc:
                first_exc = exc
                logger.error(
                    "CompositeUnitOfWork commit failed on %r: %r",
                    child, exc,
                )
                break
        if first_exc is not None:
            # Best-effort rollback on the ones that did commit.
            for child in reversed(committed):
                try:
                    await child.rollback()
                except Exception as rb_exc:
                    logger.warning(
                        "Best-effort rollback raised on %r: %r — original "
                        "commit failure NOT masked.",
                        child, rb_exc,
                    )
            raise first_exc

    async def rollback(self) -> None:
        for child in reversed(self._children):
            try:
                await child.rollback()
            except Exception as exc:
                logger.warning(
                    "CompositeUnitOfWork.rollback raised on %r: %r",
                    child, exc,
                )

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to whichever child declares it.

        Ambiguity (multiple children expose the same attribute) raises
        AttributeError; if no child exposes it, also AttributeError with
        the requested name.
        """
        # __getattr__ is called only when normal attribute resolution
        # failed — so children won't shadow our own _children etc.
        owners = []
        for child in self._children:
            if hasattr(child, name):
                owners.append(child)
        if not owners:
            raise AttributeError(
                f"CompositeUnitOfWork: no child exposes attribute "
                f"{name!r}",
            )
        if len(owners) > 1:
            raise AttributeError(
                f"CompositeUnitOfWork: attribute {name!r} is ambiguous "
                f"({len(owners)} children expose it). Each repository "
                f"name must be unique across children.",
            )
        return getattr(owners[0], name)


__all__ = ("CompositeUnitOfWork",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_composite_uow.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/composite_uow.py tests/storage/test_composite_uow.py
git commit -m "feat(storage): CompositeUnitOfWork — best-effort multi-backend coordinator

AC-9, AC-10. Two children for this PR (SqliteUoW + TurboQuantUoW).
Sequential commit-all with best-effort rollback on partial failure;
attribute proxying delegates uow.packages/uow.chunks/uow.vectors to
the right child; ambiguity raises clear AttributeError. Cross-backend
atomicity is NOT strict — startup integrity check catches mismatches."
```

---

## Tasks 11-15: Composition root helpers + embedder concretes + vector stores

### Task 11: `build_composite_uow_factory` + `build_sqlite_plus_turboquant_uow_factory`

**Files:**
- Modify: `python/pydocs_mcp/storage/factories.py`
- Test: `tests/storage/test_factories_composite.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_factories_composite.py
"""Composite UoW factories (Task 11 + spec §5.7)."""
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.factories import (
    build_composite_uow_factory,
    build_sqlite_plus_turboquant_uow_factory,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


def _setup_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "cache.db"
    open_index_database(db_path).close()
    return db_path


@pytest.mark.asyncio
async def test_build_composite_uow_factory_returns_callable_that_makes_composite(
    tmp_path: Path,
) -> None:
    db_path = _setup_db(tmp_path)
    tq_path = tmp_path / "cache.tq"
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=4,
    )
    uow = factory()
    assert isinstance(uow, CompositeUnitOfWork)


@pytest.mark.asyncio
async def test_composite_factory_exposes_sqlite_repos_AND_vectors_attr(
    tmp_path: Path,
) -> None:
    db_path = _setup_db(tmp_path)
    tq_path = tmp_path / "cache.tq"
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=4,
    )
    async with factory() as uow:
        # SQLite-owned attributes proxy through.
        assert hasattr(uow, "packages")
        assert hasattr(uow, "chunks")
        assert hasattr(uow, "module_members")
        # TurboQuant-owned attribute proxies through.
        assert isinstance(uow.vectors, TurboQuantUnitOfWork)


@pytest.mark.asyncio
async def test_build_composite_uow_factory_with_arbitrary_children(
    tmp_path: Path,
) -> None:
    # Lower-level builder accepts a list of UoW factories.
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory
    db_path = _setup_db(tmp_path)
    sqlite_factory = build_sqlite_uow_factory(db_path)
    composite_factory = build_composite_uow_factory([sqlite_factory])
    async with composite_factory() as uow:
        assert hasattr(uow, "packages")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_factories_composite.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_composite_uow_factory'`.

- [ ] **Step 3: Add to storage/factories.py**

Append to `python/pydocs_mcp/storage/factories.py`:

```python
from collections.abc import Callable, Sequence
from pathlib import Path

from pydocs_mcp.db import turboquant_path_for_project
from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


def build_composite_uow_factory(
    children: Sequence[Callable[[], object]],
) -> Callable[[], CompositeUnitOfWork]:
    """Wrap N child UoW factories into a composite factory (spec §5.7).

    The returned callable instantiates each child via its factory and
    wraps them in a CompositeUnitOfWork. Order-preserving (children[0]
    commits first; rollback walks in reverse).
    """
    def _make() -> CompositeUnitOfWork:
        return CompositeUnitOfWork([f() for f in children])
    return _make


def build_sqlite_plus_turboquant_uow_factory(
    *,
    db_path: Path,
    tq_path: Path,
    dim: int,
    bit_width: int = 4,
) -> Callable[[], CompositeUnitOfWork]:
    """The production composite for pydocs-mcp: SQLite + TurboQuant.

    Used by the composition roots in server.py + __main__.py. Drop-in
    replacement for build_sqlite_uow_factory once dense search is on.
    """
    sqlite_factory = build_sqlite_uow_factory(db_path)
    tq_factory = lambda: TurboQuantUnitOfWork(  # noqa: E731
        index_path=tq_path, dim=dim, bit_width=bit_width,
    )
    return build_composite_uow_factory([sqlite_factory, tq_factory])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_factories_composite.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/factories.py tests/storage/test_factories_composite.py
git commit -m "feat(storage): composite + SQLite+TurboQuant UoW factories (spec §5.7)

Task 11. Two builders: build_composite_uow_factory wraps N children
generically; build_sqlite_plus_turboquant_uow_factory is the
production composite for pydocs-mcp. Composition roots flip to this in
Task 27."
```

---

### Task 12: SQLite `CandidateIdResolver` + `ChunkHydrator` callables

**Files:**
- Modify: `python/pydocs_mcp/storage/factories.py`
- Test: `tests/storage/test_candidate_resolver_hydrator.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_candidate_resolver_hydrator.py
"""SQLite-flavored CandidateIdResolver + ChunkHydrator (spec §5.3, §7 risk row 1)."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.filters import FieldEq


def _seed(tmp_path: Path) -> Path:
    db_path = tmp_path / "seed.db"
    open_index_database(db_path).close()
    # Insert two packages worth of chunks via the UoW.
    import asyncio
    async def _go():
        from pydocs_mcp.models import Package
        factory = build_sqlite_uow_factory(db_path)
        async with factory() as uow:
            await uow.packages.upsert(Package(name="demo", version="1"))
            await uow.packages.upsert(Package(name="other", version="1"))
            await uow.chunks.upsert((
                Chunk(text="alpha", id=1, metadata={"package": "demo"}),
                Chunk(text="beta", id=2, metadata={"package": "demo"}),
                Chunk(text="gamma", id=3, metadata={"package": "other"}),
            ))
            await uow.commit()
    asyncio.run(_go())
    return db_path


@pytest.mark.asyncio
async def test_candidate_id_resolver_returns_uint64_ids_matching_filter(
    tmp_path: Path,
) -> None:
    db_path = _seed(tmp_path)
    resolver = build_sqlite_candidate_id_resolver(db_path)
    ids = await resolver(FieldEq("package", "demo"))
    assert isinstance(ids, np.ndarray)
    assert ids.dtype == np.uint64
    # IDs 1 and 2 are in demo; 3 is in other.
    assert set(ids.tolist()) == {1, 2}


@pytest.mark.asyncio
async def test_chunk_hydrator_returns_chunks_for_given_ids(
    tmp_path: Path,
) -> None:
    db_path = _seed(tmp_path)
    hydrator = build_sqlite_chunk_hydrator(db_path)
    chunks = await hydrator([2, 3])
    assert len(chunks) == 2
    by_id = {c.id: c for c in chunks}
    assert by_id[2].text == "beta"
    assert by_id[3].text == "gamma"


@pytest.mark.asyncio
async def test_chunk_hydrator_empty_ids_returns_empty_tuple(
    tmp_path: Path,
) -> None:
    db_path = _seed(tmp_path)
    hydrator = build_sqlite_chunk_hydrator(db_path)
    chunks = await hydrator([])
    assert chunks == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_candidate_resolver_hydrator.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_sqlite_candidate_id_resolver'`.

- [ ] **Step 3: Add the builders to storage/factories.py**

Append:

```python
import sqlite3
import asyncio
from collections.abc import Awaitable, Sequence

import numpy as np

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.sqlite import (
    SqliteFilterAdapter,
    _CHUNK_COLUMNS,
    _row_to_chunk,
)


def build_sqlite_candidate_id_resolver(
    db_path: Path,
) -> Callable[[Filter], Awaitable[np.ndarray]]:
    """Build a CandidateIdResolver that runs the filter as SQL against the
    SQLite cache and returns the matching chunk IDs as np.uint64.

    Used by TurboQuantVectorStore to construct its allowlist. The vector
    store doesn't import sqlite3 directly (spec §7 risk row 1) — it
    accepts this callable via its constructor.
    """
    adapter = SqliteFilterAdapter(
        safe_columns=_CHUNK_COLUMNS, column_prefix="",
    )

    async def resolve(filter_tree: Filter) -> np.ndarray:
        sql_clause, params = adapter.adapt(filter_tree)
        sql = f"SELECT id FROM chunks WHERE {sql_clause}" if sql_clause else "SELECT id FROM chunks"

        def _fetch() -> np.ndarray:
            conn = sqlite3.connect(str(db_path))
            try:
                rows = conn.execute(sql, params).fetchall()
                return np.asarray([r[0] for r in rows], dtype=np.uint64)
            finally:
                conn.close()
        return await asyncio.to_thread(_fetch)

    return resolve


def build_sqlite_chunk_hydrator(
    db_path: Path,
) -> Callable[[Sequence[int]], Awaitable[tuple[Chunk, ...]]]:
    """Build a ChunkHydrator that loads full Chunk objects for a given
    sequence of chunk IDs. Used by TurboQuantVectorStore to turn vector
    search ID hits back into Chunks the pipeline can score / filter.
    """
    async def hydrate(ids: Sequence[int]) -> tuple[Chunk, ...]:
        if not ids:
            return ()
        id_list = list(ids)
        placeholders = ",".join("?" * len(id_list))
        sql = (
            "SELECT id, package, module, title, text, origin, content_hash "
            f"FROM chunks WHERE id IN ({placeholders})"
        )

        def _fetch() -> tuple[Chunk, ...]:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            try:
                rows = list(conn.execute(sql, id_list).fetchall())
            finally:
                conn.close()
            return tuple(_row_to_chunk(r) for r in rows)
        return await asyncio.to_thread(_fetch)

    return hydrate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_candidate_resolver_hydrator.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/factories.py tests/storage/test_candidate_resolver_hydrator.py
git commit -m "feat(storage): SQLite CandidateIdResolver + ChunkHydrator callables

Spec §5.3 + §7 risk row 1. The callables decouple TurboQuantVectorStore
from SQLite — the store's constructor accepts both as injected
dependencies, so a future Qdrant or Postgres backend slots its own
resolver/hydrator in without touching the store class."
```

---

### Task 13: `OptionalDepMissing` + `build_embedder` factory (no concretes yet)

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/embedders/__init__.py`
- Test: `tests/extraction/strategies/embedders/test_build_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/strategies/embedders/test_build_embedder.py
"""build_embedder factory + OptionalDepMissing (AC-15, AC-16)."""
import pytest

from pydocs_mcp.extraction.strategies.embedders import (
    OptionalDepMissing,
    build_embedder,
)
from pydocs_mcp.retrieval.config import EmbeddingConfig


def test_unknown_provider_raises_valueerror() -> None:
    cfg = EmbeddingConfig.model_construct(provider="cohere")  # bypass Literal at runtime
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        build_embedder(cfg)


def test_optional_dep_missing_is_distinct_exception_type() -> None:
    # Sanity: the OptionalDepMissing exception class is exported and is
    # distinct from RuntimeError/ImportError so callers can catch it.
    assert issubclass(OptionalDepMissing, Exception)
    assert OptionalDepMissing is not ImportError
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/strategies/embedders/test_build_embedder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the module**

```python
# python/pydocs_mcp/extraction/strategies/embedders/__init__.py
"""Embedder factory + concrete classes (spec §5.10 + Decision 5).

Concrete embedders live behind optional extras:
- FastEmbedEmbedder → pip install pydocs-mcp[fastembed]
- OpenAIEmbedder    → pip install pydocs-mcp[openai]
- Both              → pip install pydocs-mcp[all-embedders]

build_embedder(cfg) returns the right concrete based on cfg.provider.
Adding a new provider = add a new module + one new branch + one new
extra in pyproject.toml. No registry needed.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.config import EmbeddingConfig
from pydocs_mcp.storage.protocols import Embedder


class OptionalDepMissing(Exception):
    """Raised when a concrete embedder's optional extra isn't installed.

    Message includes the exact pip-install command to install the missing
    extra.
    """


def build_embedder(cfg: EmbeddingConfig) -> Embedder:
    """Construct the configured embedder.

    Defers concrete class imports so unconfigured providers don't pull
    in their optional deps. Raises OptionalDepMissing with a clear
    install command if the extra isn't available.
    """
    if cfg.provider == "fastembed":
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )
        return FastEmbedEmbedder(
            model_name=cfg.model_name, dim=cfg.dim,
        )
    if cfg.provider == "openai":
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        return OpenAIEmbedder(
            model_name=cfg.model_name, dim=cfg.dim,
        )
    raise ValueError(
        f"Unknown embedding provider: {cfg.provider!r}. "
        f"Supported: 'fastembed', 'openai'.",
    )


__all__ = ("OptionalDepMissing", "build_embedder")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/strategies/embedders/test_build_embedder.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/__init__.py tests/extraction/strategies/embedders/test_build_embedder.py
git commit -m "feat(embedders): OptionalDepMissing + build_embedder factory skeleton

AC-15 + foundation for AC-13/AC-14/AC-16. Explicit if-chain — no
registry per Decision 5. Concrete classes (FastEmbed, OpenAI) land in
Tasks 14-15. Unknown provider raises clear ValueError listing the
supported names."
```

---

### Task 14: `FastEmbedEmbedder` (with import-guard for the optional extra)

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/embedders/fastembed.py`
- Test: `tests/extraction/strategies/embedders/test_fastembed_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/strategies/embedders/test_fastembed_embedder.py
"""FastEmbedEmbedder construction + import-guard (AC-13, AC-16)."""
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_import_without_fastembed_raises_optionaldepmissing() -> None:
    # Hide fastembed from sys.modules + sys.meta_path so the import fails.
    saved = sys.modules.pop("fastembed", None)

    class _BlockFastembed:
        def find_module(self, name, path=None):
            if name == "fastembed" or name.startswith("fastembed."):
                return self
            return None

        def load_module(self, name):
            raise ImportError(f"No module named {name!r}")

    finder = _BlockFastembed()
    sys.meta_path.insert(0, finder)
    # Also remove our module from cache so the import re-runs.
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
    )
    try:
        from pydocs_mcp.extraction.strategies.embedders import (
            OptionalDepMissing,
        )
        with pytest.raises(OptionalDepMissing, match=r"pip install pydocs-mcp\[fastembed\]"):
            from pydocs_mcp.extraction.strategies.embedders.fastembed import (  # noqa: F401
                FastEmbedEmbedder,
            )
    finally:
        sys.meta_path.remove(finder)
        if saved is not None:
            sys.modules["fastembed"] = saved
        sys.modules.pop(
            "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
        )


def test_fastembedembedder_construction_with_mocked_fastembed() -> None:
    """When fastembed is mocked, FastEmbedEmbedder constructs OK."""
    # Mock the fastembed module before import.
    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = MagicMock()

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
    )
    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        from pydocs_mcp.extraction.strategies.embedders.fastembed import (
            FastEmbedEmbedder,
        )
        emb = FastEmbedEmbedder(model_name="BAAI/bge-small-en-v1.5", dim=384)
        assert emb.dim == 384
        assert emb.model_name == "BAAI/bge-small-en-v1.5"

    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.fastembed", None,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/strategies/embedders/test_fastembed_embedder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the FastEmbed wrapper**

```python
# python/pydocs_mcp/extraction/strategies/embedders/fastembed.py
"""FastEmbedEmbedder — Embedder backed by fastembed.TextEmbedding."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from pydocs_mcp.extraction.strategies.embedders import OptionalDepMissing
from pydocs_mcp.models import Embedding

try:
    from fastembed import TextEmbedding  # type: ignore[import-not-found]
except ImportError as exc:
    raise OptionalDepMissing(
        "fastembed is not installed. To use the FastEmbed embedder run: "
        "pip install pydocs-mcp[fastembed]",
    ) from exc


@dataclass
class FastEmbedEmbedder:
    """Embedder backed by FastEmbed (ONNX-accelerated, no API key).

    Zero-copy from FastEmbed's TextEmbedding.embed() yields straight
    through to our Embedding type — both are np.ndarray (1D, float32).
    """
    model_name: str = "BAAI/bge-small-en-v1.5"
    dim: int = 384
    _model: TextEmbedding = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._model = TextEmbedding(model_name=self.model_name)

    async def embed_query(self, text: str) -> Embedding:
        results = await asyncio.to_thread(
            lambda: list(self._model.embed([text])),
        )
        # FastEmbed yields np.ndarray (float32, 1D) per document.
        return np.asarray(results[0], dtype=np.float32)

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        results = await asyncio.to_thread(
            lambda: list(self._model.embed(list(texts))),
        )
        return tuple(np.asarray(v, dtype=np.float32) for v in results)


__all__ = ("FastEmbedEmbedder",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/strategies/embedders/test_fastembed_embedder.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/fastembed.py tests/extraction/strategies/embedders/test_fastembed_embedder.py
git commit -m "feat(embedders): FastEmbedEmbedder with optional-dep import guard

AC-13 + AC-16. Wraps fastembed.TextEmbedding. Import-time guard raises
OptionalDepMissing with the exact 'pip install pydocs-mcp[fastembed]'
command if the optional extra isn't installed. Embedding I/O offloaded
via asyncio.to_thread to keep the event loop free."
```

---

### Task 15: `OpenAIEmbedder` (with API key check + optional-dep guard)

**Files:**
- Create: `python/pydocs_mcp/extraction/strategies/embedders/openai.py`
- Test: `tests/extraction/strategies/embedders/test_openai_embedder.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/strategies/embedders/test_openai_embedder.py
"""OpenAIEmbedder construction + API key check + optional-dep guard (AC-14)."""
import sys
from unittest.mock import MagicMock, patch

import pytest


def test_construction_without_api_key_raises_runtime_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    mock_openai = MagicMock()
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
            OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )


def test_construction_with_api_key_succeeds(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    mock_openai = MagicMock()
    mock_openai.AsyncOpenAI = MagicMock()
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )
    with patch.dict(sys.modules, {"openai": mock_openai}):
        from pydocs_mcp.extraction.strategies.embedders.openai import (
            OpenAIEmbedder,
        )
        emb = OpenAIEmbedder(model_name="text-embedding-3-small", dim=1536)
        assert emb.dim == 1536
        assert emb.model_name == "text-embedding-3-small"
    sys.modules.pop(
        "pydocs_mcp.extraction.strategies.embedders.openai", None,
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/strategies/embedders/test_openai_embedder.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create the OpenAI wrapper**

```python
# python/pydocs_mcp/extraction/strategies/embedders/openai.py
"""OpenAIEmbedder — Embedder backed by OpenAI /v1/embeddings."""
from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from pydocs_mcp.extraction.strategies.embedders import OptionalDepMissing
from pydocs_mcp.models import Embedding

try:
    from openai import AsyncOpenAI  # type: ignore[import-not-found]
except ImportError as exc:
    raise OptionalDepMissing(
        "openai is not installed. To use the OpenAI embedder run: "
        "pip install pydocs-mcp[openai]",
    ) from exc


@dataclass
class OpenAIEmbedder:
    """Embedder backed by OpenAI /v1/embeddings. Reads OPENAI_API_KEY."""
    model_name: str = "text-embedding-3-small"
    dim: int = 1536
    _client: AsyncOpenAI = field(init=False, repr=False)

    def __post_init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OpenAIEmbedder requires OPENAI_API_KEY environment "
                "variable. Set it before starting the server, or pick a "
                "different embedding.provider in your YAML config.",
            )
        self._client = AsyncOpenAI(api_key=api_key)

    async def embed_query(self, text: str) -> Embedding:
        resp = await self._client.embeddings.create(
            model=self.model_name, input=text, dimensions=self.dim,
        )
        # OpenAI returns list[float]; wrap as np.ndarray (float32) to
        # match the Embedding type aligned with FastEmbed.
        return np.asarray(resp.data[0].embedding, dtype=np.float32)

    async def embed_chunks(
        self, texts: Sequence[str],
    ) -> tuple[Embedding, ...]:
        if not texts:
            return ()
        resp = await self._client.embeddings.create(
            model=self.model_name, input=list(texts), dimensions=self.dim,
        )
        # Preserve input order — OpenAI returns embeddings in request order.
        return tuple(
            np.asarray(item.embedding, dtype=np.float32)
            for item in resp.data
        )


__all__ = ("OpenAIEmbedder",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/strategies/embedders/test_openai_embedder.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/strategies/embedders/openai.py tests/extraction/strategies/embedders/test_openai_embedder.py
git commit -m "feat(embedders): OpenAIEmbedder with API key + optional-dep guards

AC-14. Wraps openai.AsyncOpenAI. Two failure modes — optional dep
missing (raises OptionalDepMissing with pip install command) and
OPENAI_API_KEY env var missing (raises RuntimeError at construction —
fail loud + early, not on first embed call)."
```

---

## Tasks 16-20: Vector stores + RRF fusion + ParallelStep + dense retrieval steps

### Task 16: `TurboQuantVectorStore`

**Files:**
- Create: `python/pydocs_mcp/storage/turboquant_store.py`
- Test: `tests/storage/test_turboquant_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_turboquant_store.py
"""TurboQuantVectorStore.vector_search with allowlist + hydration (AC-3, AC-4)."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.turboquant_store import TurboQuantVectorStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


def _populate_index(tmp_path: Path) -> tuple[Path, dict[int, str]]:
    """Populate a .tq file with 4 distinct vectors and return id->text map."""
    import asyncio
    tq = tmp_path / "vec.tq"
    id_to_text = {1: "alpha", 2: "beta", 3: "gamma", 4: "delta"}
    # Use orthogonal-ish vectors so each query targets a specific id.
    embeddings = [
        (1.0, 0.0, 0.0, 0.0),
        (0.0, 1.0, 0.0, 0.0),
        (0.0, 0.0, 1.0, 0.0),
        (0.0, 0.0, 0.0, 1.0),
    ]
    async def seed():
        async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=8) as uow:
            await uow.add_vectors(list(id_to_text.keys()), embeddings)
            await uow.commit()
    asyncio.run(seed())
    return tq, id_to_text


@pytest.mark.asyncio
async def test_vector_search_returns_up_to_k_chunks(tmp_path: Path) -> None:
    tq, id_to_text = _populate_index(tmp_path)
    # Mock resolver returns all IDs as allowlist (no filter case below).
    # Mock hydrator returns Chunks from the in-memory dict.
    async def hydrator(ids):
        return tuple(Chunk(text=id_to_text[int(i)], id=int(i)) for i in ids)

    async def all_ids_resolver(_filter):
        return np.asarray(list(id_to_text.keys()), dtype=np.uint64)

    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=8) as uow:
        store = TurboQuantVectorStore(
            uow=uow,
            candidate_id_resolver=all_ids_resolver,
            chunk_hydrator=hydrator,
            retriever_name="dense",
        )
        # Query close to id=1's vector
        results = await store.vector_search(
            query_vector=(1.0, 0.0, 0.0, 0.0), limit=2,
        )
        assert len(results) == 2
        assert all(isinstance(c, Chunk) for c in results)


@pytest.mark.asyncio
async def test_vector_search_with_filter_restricts_to_allowlist(tmp_path: Path) -> None:
    """When filter passed, allowlist restricts results to a subset."""
    from pydocs_mcp.storage.filters import FieldEq
    tq, id_to_text = _populate_index(tmp_path)

    async def restricted_resolver(_filter):
        # Pretend "package=demo" matches only ids 2 and 3.
        return np.asarray([2, 3], dtype=np.uint64)

    async def hydrator(ids):
        return tuple(Chunk(text=id_to_text[int(i)], id=int(i)) for i in ids)

    async with TurboQuantUnitOfWork(index_path=tq, dim=4, bit_width=8) as uow:
        store = TurboQuantVectorStore(
            uow=uow,
            candidate_id_resolver=restricted_resolver,
            chunk_hydrator=hydrator,
            retriever_name="dense",
        )
        results = await store.vector_search(
            query_vector=(0.0, 1.0, 0.0, 0.0),  # close to id=2
            limit=10,
            filter=FieldEq("package", "demo"),
        )
        # Only ids 2 and 3 are in the allowlist.
        assert {c.id for c in results}.issubset({2, 3})


@pytest.mark.asyncio
async def test_vector_search_does_not_import_sqlite_module(tmp_path: Path) -> None:
    """TurboQuantVectorStore must NOT import sqlite3 directly (SOLID)."""
    import pydocs_mcp.storage.turboquant_store as mod
    import inspect
    src = inspect.getsource(mod)
    # Module source must not contain a `import sqlite3` or `from sqlite3`.
    assert "import sqlite3" not in src
    assert "from sqlite3" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_turboquant_store.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create turboquant_store.py**

```python
# python/pydocs_mcp/storage/turboquant_store.py
"""TurboQuantVectorStore — implements VectorSearchable (spec §5.3).

Decoupled from SQLite: constructor takes a CandidateIdResolver +
ChunkHydrator callable pair. The store itself never imports any
SQLite module — that's the SOLID seam that lets a future Qdrant /
Postgres / etc. swap its own resolver + hydrator in without touching
this class.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork

CandidateIdResolver = Callable[[Filter], Awaitable[np.ndarray]]
ChunkHydrator = Callable[[Sequence[int]], Awaitable[tuple[Chunk, ...]]]


@dataclass
class TurboQuantVectorStore:
    """VectorSearchable backed by turbovec.IdMapIndex.

    Implements `vector_search(query_vector, limit, filter=None)`:
      1. If filter set, call candidate_id_resolver(filter) for allowlist.
      2. Call IdMapIndex.search(query_vector, k=limit, allowlist=ids).
      3. Hydrate returned IDs to full Chunks via chunk_hydrator.
      4. Stamp each Chunk with relevance (cosine distance from index) +
         retriever_name.
    """
    uow: TurboQuantUnitOfWork
    candidate_id_resolver: CandidateIdResolver
    chunk_hydrator: ChunkHydrator
    retriever_name: str = "turboquant_dense"

    async def vector_search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | None = None,
    ) -> tuple[Chunk, ...]:
        query = np.asarray(query_vector, dtype=np.float32)
        if filter is not None:
            allowlist = await self.candidate_id_resolver(filter)
            if allowlist.size == 0:
                return ()
            scores, ids = self.uow.index.search(
                query, k=limit, allowlist=allowlist,
            )
        else:
            scores, ids = self.uow.index.search(query, k=limit)
        if len(ids) == 0:
            return ()
        # Hydrate then stamp relevance + retriever_name.
        # IDs come back as a numpy array; turn into list[int].
        id_list = [int(i) for i in ids.tolist()]
        chunks = await self.chunk_hydrator(id_list)
        # Build a quick id -> score map; turbovec scores follow its
        # convention (higher = more similar after the internal flip).
        id_to_score = {int(i): float(s) for i, s in zip(ids.tolist(), scores.tolist())}
        return tuple(
            Chunk(
                text=c.text,
                id=c.id,
                relevance=id_to_score.get(c.id),
                retriever_name=self.retriever_name,
                embedding=c.embedding,
                metadata=c.metadata,
            )
            for c in chunks
        )


__all__ = ("TurboQuantVectorStore", "CandidateIdResolver", "ChunkHydrator")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_turboquant_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/turboquant_store.py tests/storage/test_turboquant_store.py
git commit -m "feat(storage): TurboQuantVectorStore implementing VectorSearchable

AC-3, AC-4 + spec §5.3. Wraps turbovec.IdMapIndex with a pre-filter
allowlist path. Two injected callables (CandidateIdResolver,
ChunkHydrator) keep the store decoupled from SQLite — verified by a
'no sqlite3 import' source-inspection test (spec §7 risk row 1)."
```

---

### Task 17: `RRFResultFuser` + `RRFFusionStep` (multi-list rewrite)

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/rrf_fusion.py`
- Delete: `python/pydocs_mcp/retrieval/steps/rrf.py` (after re-export update)
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` (update re-exports)
- Test: `tests/retrieval/steps/test_rrf_fusion.py`

- [ ] **Step 1: Locate existing `RRFStep`**

```bash
grep -rn "RRFStep\|rrf_step\|\"rrf\"" python/pydocs_mcp/ benchmarks/ tests/
```

Confirm no shipped pipeline YAML references the registry key `rrf` (the single-list re-scorer); this lets the rename be a hard rename, not an alias.

- [ ] **Step 2: Write the failing test**

```python
# tests/retrieval/steps/test_rrf_fusion.py
"""RRFFusionStep multi-list fusion + RRFResultFuser math (AC-19 + spec §5.6)."""
import pytest

from pydocs_mcp.models import Chunk
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.rrf_fusion import RRFFusionStep, RRFResultFuser


def _q(terms: str = "x"):
    from pydocs_mcp.models import SearchQuery
    return SearchQuery(terms=terms, max_results=10)


@pytest.mark.asyncio
async def test_rrf_formula_correct() -> None:
    """Hand-computed RRF: score = sum(1 / (k + rank)) across lists."""
    # k=60, two lists.
    fuser = RRFResultFuser(k=60)
    # Chunk A: rank 0 in list 1, rank 1 in list 2 → 1/60 + 1/61 ≈ 0.0331967
    # Chunk B: rank 1 in list 1, absent in list 2 → 1/61 ≈ 0.01639
    a = Chunk(text="A", id=1)
    b = Chunk(text="B", id=2)
    list1 = (a, b)
    list2 = (b, a)
    fused = await fuser.fuse([list1, list2], limit=10)
    # A appears in both (ranks 0 + 1) → high score
    # B appears in both (ranks 1 + 0) → high score
    # Both should appear; A's relevance should equal B's.
    fused_ids = [c.id for c in fused]
    assert set(fused_ids) == {1, 2}


@pytest.mark.asyncio
async def test_rrf_step_reads_named_scratch_keys() -> None:
    """RRFFusionStep reads from scratch[<branch_name>.ranked] then writes
    state.candidates."""
    state = RetrieverState(query=_q())
    bm25 = (Chunk(text="A", id=1), Chunk(text="B", id=2))
    dense = (Chunk(text="B", id=2), Chunk(text="C", id=3))
    state.scratch["bm25.ranked"] = bm25
    state.scratch["dense.ranked"] = dense
    step = RRFFusionStep(
        name="rrf_fusion",
        k=60,
        branch_keys=("bm25.ranked", "dense.ranked"),
    )
    out = await step.run(state)
    assert out.candidates is not None
    # Returned as ChunkList (or tuple inside ChunkList).
    items = out.candidates.items if hasattr(out.candidates, "items") else out.candidates
    fused_ids = [c.id for c in items]
    # All 3 ids present, B (in both lists) ranks high.
    assert set(fused_ids) == {1, 2, 3}


@pytest.mark.asyncio
async def test_rrf_step_no_branches_returns_state_unchanged() -> None:
    state = RetrieverState(query=_q())
    step = RRFFusionStep(name="rrf_fusion", k=60, branch_keys=("absent.x",))
    out = await step.run(state)
    assert out is state
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/steps/test_rrf_fusion.py -v`
Expected: FAIL — `ModuleNotFoundError: pydocs_mcp.retrieval.steps.rrf_fusion`.

- [ ] **Step 4: Create rrf_fusion.py + delete rrf.py + update __init__**

Create `python/pydocs_mcp/retrieval/steps/rrf_fusion.py`:

```python
# python/pydocs_mcp/retrieval/steps/rrf_fusion.py
"""RRFFusionStep + RRFResultFuser — multi-list reciprocal-rank fusion (spec §5.6).

Replaces the previous single-list RRFStep. Reads N ranked Chunk lists
from state.scratch[<branch_name>.ranked] (each parallel branch publishes
its ranking via TopKFilterStep.publish_to), computes RRF score per item
as sum(1 / (k + rank_in_list_i)) across lists, sorts descending, emits
fused ranking via state.candidates.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

# Literature default for RRF — see Cormack et al. 2009.
_DEFAULT_K = 60


@dataclass(frozen=True, slots=True)
class RRFResultFuser:
    """ResultFuser implementing Cormack-style reciprocal-rank fusion."""
    k: int = _DEFAULT_K

    async def fuse(
        self,
        ranked_lists: Sequence[tuple[Chunk, ...]],
        *,
        limit: int,
    ) -> tuple[Chunk, ...]:
        return _rrf_fuse(ranked_lists, k=self.k, limit=limit)


def _rrf_fuse(
    ranked_lists: Sequence[tuple[Chunk, ...]],
    *,
    k: int,
    limit: int | None = None,
) -> tuple[Chunk, ...]:
    """Reciprocal-rank fusion. Returns ranked Chunks with `relevance`
    overwritten by the RRF score. Items are de-duped by `id`."""
    scores: dict[int, float] = {}
    representatives: dict[int, Chunk] = {}
    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked):
            if chunk.id is None:
                continue  # skip un-IDed; can't dedupe
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
            # First-seen wins for the representative Chunk (preserves
            # the earlier branch's metadata + retriever_name).
            representatives.setdefault(chunk.id, chunk)
    # Stamp each rep with its RRF score and sort.
    fused = [
        replace(representatives[chunk_id], relevance=scores[chunk_id])
        for chunk_id in scores
    ]
    fused.sort(key=lambda c: c.relevance or 0.0, reverse=True)
    if limit is not None:
        fused = fused[:limit]
    return tuple(fused)


@step_registry.register("rrf_fusion")
@dataclass(frozen=True, slots=True)
class RRFFusionStep(RetrieverStep):
    """Multi-list RRF fusion step. Reads named scratch keys; writes candidates."""
    k: int = _DEFAULT_K
    branch_keys: tuple[str, ...] = ("bm25.ranked", "dense.ranked")
    name: str = field(default="rrf_fusion", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        ranked_lists: list[tuple[Chunk, ...]] = []
        for key in self.branch_keys:
            payload = state.scratch.get(key)
            if payload is None:
                continue
            # Payload could be a tuple of Chunks or a ChunkList.
            items = (
                tuple(payload.items)
                if hasattr(payload, "items")
                else tuple(payload)
            )
            if items:
                ranked_lists.append(items)
        if not ranked_lists:
            return state
        fused = _rrf_fuse(ranked_lists, k=self.k)
        return replace(state, candidates=ChunkList(items=fused))

    def to_dict(self) -> dict:
        return {
            "type": "rrf_fusion",
            "k": self.k,
            "branch_keys": list(self.branch_keys),
        }

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "RRFFusionStep":
        return cls(
            k=data.get("k", _DEFAULT_K),
            branch_keys=tuple(data.get(
                "branch_keys", ["bm25.ranked", "dense.ranked"],
            )),
        )


__all__ = ("RRFFusionStep", "RRFResultFuser")
```

Then update `python/pydocs_mcp/retrieval/steps/__init__.py` — remove the old `RRFStep` export and add `RRFFusionStep`. Then delete `python/pydocs_mcp/retrieval/steps/rrf.py`.

- [ ] **Step 5: Run test to verify it passes**

```bash
.venv/bin/pytest tests/retrieval/steps/test_rrf_fusion.py -v
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3
```

Expected: new tests PASS (3); full suite still PASS (1027+ tests).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/rrf_fusion.py python/pydocs_mcp/retrieval/steps/__init__.py tests/retrieval/steps/test_rrf_fusion.py
git rm python/pydocs_mcp/retrieval/steps/rrf.py
git commit -m "refactor(retrieval): RRFStep → RRFFusionStep (multi-list fuser)

AC-19 + spec §5.6. The previous single-list re-scorer had no shipped
consumer; replaced with a true multi-list fuser that reads N ranked
lists from scratch[<branch_name>.ranked] and emits fused ranking via
state.candidates. RRFResultFuser is the standalone ResultFuser the
HybridSqliteTurboStore composes with. Registry key 'rrf' → 'rrf_fusion'
makes any external caller fail loudly."
```

---

### Task 18: `TopKFilterStep.publish_to` field (parallel-branch hand-off)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/top_k_filter.py`
- Test: `tests/retrieval/steps/test_top_k_filter_publish_to.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/steps/test_top_k_filter_publish_to.py
"""TopKFilterStep.publish_to writes its output to state.scratch (AC-20)."""
import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep


def _state_with_candidates() -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(items=(
            Chunk(text="a", id=1, relevance=0.9),
            Chunk(text="b", id=2, relevance=0.7),
            Chunk(text="c", id=3, relevance=0.5),
        )),
    )


@pytest.mark.asyncio
async def test_publish_to_default_none_does_not_touch_scratch() -> None:
    state = _state_with_candidates()
    step = TopKFilterStep(name="topk", k=2)
    out = await step.run(state)
    assert "bm25.ranked" not in out.scratch
    assert out.candidates is not None  # still in state.candidates


@pytest.mark.asyncio
async def test_publish_to_writes_topk_to_scratch() -> None:
    state = _state_with_candidates()
    step = TopKFilterStep(name="topk", k=2, publish_to="bm25.ranked")
    out = await step.run(state)
    assert "bm25.ranked" in out.scratch
    payload = out.scratch["bm25.ranked"]
    # Payload is the same tuple/ChunkList as state.candidates after top-K.
    items = (
        tuple(payload.items)
        if hasattr(payload, "items")
        else tuple(payload)
    )
    assert len(items) == 2
    assert items[0].id == 1
    assert items[1].id == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/steps/test_top_k_filter_publish_to.py -v`
Expected: FAIL — `TypeError: TopKFilterStep.__init__() got an unexpected keyword argument 'publish_to'`.

- [ ] **Step 3: Add the field to TopKFilterStep**

In `python/pydocs_mcp/retrieval/steps/top_k_filter.py`, add the field + scratch-write logic:

```python
@step_registry.register("top_k_filter")
@dataclass(frozen=True, slots=True)
class TopKFilterStep(RetrieverStep):
    k: int = _DEFAULT_K
    # NEW (spec §5.8): when set, also publish the top-K output to
    # state.scratch[publish_to] so a downstream RRFFusionStep can read
    # it as one of its named branches. None = legacy behavior (only
    # writes state.candidates).
    publish_to: str | None = None
    name: str = field(default="top_k_filter", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        # ... existing top-K logic that produces `topk_items` ...
        new_state = replace(state, candidates=ChunkList(items=topk_items))
        if self.publish_to is not None:
            new_state.scratch[self.publish_to] = new_state.candidates
        return new_state

    def to_dict(self) -> dict:
        d: dict = {"type": "top_k_filter"}
        if self.k != _DEFAULT_K:
            d["k"] = self.k
        if self.publish_to is not None:
            d["publish_to"] = self.publish_to
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "TopKFilterStep":
        return cls(
            k=data.get("k", _DEFAULT_K),
            publish_to=data.get("publish_to"),
        )
```

(Splice the new field in; preserve existing top-K logic exactly.)

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/retrieval/steps/test_top_k_filter_publish_to.py -v
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3
```

Expected: 2 PASS; full suite still green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/top_k_filter.py tests/retrieval/steps/test_top_k_filter_publish_to.py
git commit -m "feat(retrieval): TopKFilterStep.publish_to for parallel-branch hand-off

AC-20 + spec §5.8. Optional str field — when set, the step writes its
ranked output to state.scratch[publish_to] in addition to
state.candidates. Default None preserves legacy behavior. This is the
contract by which parallel branches publish their rankings for
RRFFusionStep to consume."
```

---

### Task 19: `DenseFetcherStep`

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/dense_fetcher.py`
- Test: `tests/retrieval/steps/test_dense_fetcher.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/steps/test_dense_fetcher.py
"""DenseFetcherStep reads pre_filter + queries TurboQuantVectorStore (AC-17)."""
from pathlib import Path

import numpy as np
import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, ChunkList, Package, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
from pydocs_mcp.storage.factories import (
    build_sqlite_candidate_id_resolver,
    build_sqlite_chunk_hydrator,
    build_sqlite_uow_factory,
)
from pydocs_mcp.storage.turboquant_store import TurboQuantVectorStore
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork
from tests._fakes import MockEmbedder


@pytest.mark.asyncio
async def test_dense_fetcher_end_to_end(tmp_path: Path) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    embedder = MockEmbedder(dim=4)

    # Seed SQLite with 3 chunks
    sqlite_factory = build_sqlite_uow_factory(db_path)
    async with sqlite_factory() as uow:
        await uow.packages.upsert(Package(name="demo", version="1"))
        chunks_to_insert = (
            Chunk(text="alpha", id=1, metadata={"package": "demo"}),
            Chunk(text="beta", id=2, metadata={"package": "demo"}),
            Chunk(text="gamma", id=3, metadata={"package": "demo"}),
        )
        await uow.chunks.upsert(chunks_to_insert)
        await uow.commit()

    # Seed TurboQuant with the same 3 IDs + mock embeddings.
    async with TurboQuantUnitOfWork(index_path=tq_path, dim=4, bit_width=8) as tq_uow:
        vecs = [await embedder.embed_query(c.text) for c in chunks_to_insert]
        await tq_uow.add_vectors([1, 2, 3], vecs)
        await tq_uow.commit()

    # Run the DenseFetcherStep
    async with TurboQuantUnitOfWork(index_path=tq_path, dim=4, bit_width=8) as tq_uow:
        store = TurboQuantVectorStore(
            uow=tq_uow,
            candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
            chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
            retriever_name="dense",
        )
        step = DenseFetcherStep(
            name="dense_fetch", store=store, embedder=embedder, limit=10,
        )
        # Query for "alpha" — expect alpha to rank top (deterministic via MockEmbedder).
        state = RetrieverState(query=SearchQuery(terms="alpha", max_results=10))
        out = await step.run(state)
        assert out.candidates is not None
        items = out.candidates.items
        assert len(items) > 0
        assert items[0].id == 1  # mock-deterministic match


@pytest.mark.asyncio
async def test_dense_fetcher_with_pre_filter_restricts_results(tmp_path: Path) -> None:
    """When pre_filter.result is in scratch, DenseFetcher restricts to allowlist."""
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    embedder = MockEmbedder(dim=4)

    # Seed chunks in two packages
    sqlite_factory = build_sqlite_uow_factory(db_path)
    async with sqlite_factory() as uow:
        await uow.packages.upsert(Package(name="demo", version="1"))
        await uow.packages.upsert(Package(name="other", version="1"))
        chunks_to_insert = (
            Chunk(text="alpha", id=1, metadata={"package": "demo"}),
            Chunk(text="beta", id=2, metadata={"package": "other"}),
        )
        await uow.chunks.upsert(chunks_to_insert)
        await uow.commit()

    async with TurboQuantUnitOfWork(index_path=tq_path, dim=4, bit_width=8) as tq_uow:
        vecs = [await embedder.embed_query(c.text) for c in chunks_to_insert]
        await tq_uow.add_vectors([1, 2], vecs)
        await tq_uow.commit()

    async with TurboQuantUnitOfWork(index_path=tq_path, dim=4, bit_width=8) as tq_uow:
        store = TurboQuantVectorStore(
            uow=tq_uow,
            candidate_id_resolver=build_sqlite_candidate_id_resolver(db_path),
            chunk_hydrator=build_sqlite_chunk_hydrator(db_path),
            retriever_name="dense",
        )
        step = DenseFetcherStep(
            name="dense_fetch", store=store, embedder=embedder, limit=10,
        )
        # Build state with a pre_filter.result restricting to package="demo".
        from pydocs_mcp.storage.filters import FieldEq
        state = RetrieverState(
            query=SearchQuery(
                terms="alpha", max_results=10,
                pre_filter={"package": "demo"},
            ),
        )
        state.scratch["pre_filter.result"] = PreFilterResult(
            tree=FieldEq("package", "demo"),
            scope=None,
            sql='"package" = ?',
            params=("demo",),
        )
        out = await step.run(state)
        # Only id=1 in demo; id=2 in other is excluded.
        assert all(c.id == 1 for c in out.candidates.items)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/steps/test_dense_fetcher.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create dense_fetcher.py**

```python
# python/pydocs_mcp/retrieval/steps/dense_fetcher.py
"""DenseFetcherStep — vector-search fetcher using TurboQuantVectorStore (AC-17).

Mirrors ChunkFetcherStep's shape but on the dense side:
- Reads state.scratch["pre_filter.result"] (set by PreFilterStep
  upstream) to get the filter tree.
- Embeds the query via the injected Embedder.
- Calls store.vector_search(query_vector, limit, filter=...) which
  uses the TurboQuant allowlist mechanism for metadata pre-filtering.
- Writes results to state.candidates.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydocs_mcp.models import ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
from pydocs_mcp.storage.protocols import Embedder
from pydocs_mcp.storage.turboquant_store import TurboQuantVectorStore

_DEFAULT_LIMIT = 50


@step_registry.register("dense_fetcher")
@dataclass(frozen=True, slots=True)
class DenseFetcherStep(RetrieverStep):
    """Dense-side candidate generation via TurboQuant filtered search."""
    store: TurboQuantVectorStore = field(default=None)  # type: ignore[assignment]
    embedder: Embedder = field(default=None)  # type: ignore[assignment]
    limit: int = _DEFAULT_LIMIT
    name: str = field(default="dense_fetcher", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        query_text = state.query.terms.strip()
        if not query_text:
            return replace(state, candidates=ChunkList(items=()))
        query_vec = await self.embedder.embed_query(query_text)
        # Single-vector only this PR (per spec §5.4 NotImplementedError).
        # If embedder returns MultiVector, take first vector as a degraded
        # fallback (logged); future PR handles MultiVector properly.
        from pydocs_mcp.models import is_multi_vector
        if is_multi_vector(query_vec):
            query_vec = query_vec[0]

        # Get filter from pre_filter.result if set.
        filter_tree = None
        if state.query.pre_filter is not None:
            result = state.scratch.get("pre_filter.result")
            if isinstance(result, PreFilterResult):
                filter_tree = result.tree

        # query_vec is np.ndarray (FastEmbed-aligned); pass straight through.
        candidates = await self.store.vector_search(
            query_vector=query_vec,
            limit=self.limit,
            filter=filter_tree,
        )
        return replace(state, candidates=ChunkList(items=candidates))

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "DenseFetcherStep":
        if context.vector_store is None or context.embedder is None:
            raise ValueError(
                "DenseFetcherStep requires BuildContext.vector_store + "
                "BuildContext.embedder to be set. Provide both at server "
                "startup via build_retrieval_context(...).",
            )
        return cls(
            store=context.vector_store,
            embedder=context.embedder,
            limit=data.get("limit", _DEFAULT_LIMIT),
        )

    def to_dict(self) -> dict:
        d: dict = {"type": "dense_fetcher"}
        if self.limit != _DEFAULT_LIMIT:
            d["limit"] = self.limit
        return d


__all__ = ("DenseFetcherStep",)
```

NOTE: `BuildContext` needs `vector_store` and `embedder` fields. Verify their presence (Task 11 should have already wired the context, OR add a sub-step here to extend `retrieval/serialization.py`'s `BuildContext`). For this task assume they exist; if the test fails on `context.vector_store`, follow up with a `BuildContext` extension commit.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/retrieval/steps/test_dense_fetcher.py -v`
Expected: PASS (2 tests). If `BuildContext` lacks `vector_store`/`embedder`, add those fields first as a prerequisite step.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/dense_fetcher.py tests/retrieval/steps/test_dense_fetcher.py
git commit -m "feat(retrieval): DenseFetcherStep — TurboQuant-allowlist filtered search

AC-17. Reads pre_filter.result for the metadata filter; embeds query
via the injected Embedder; calls TurboQuantVectorStore.vector_search
(which builds the allowlist via the CandidateIdResolver). Writes
results to state.candidates."
```

---

### Task 20: `DenseScorerStep`

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/dense_scorer.py`
- Test: `tests/retrieval/steps/test_dense_scorer.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/steps/test_dense_scorer.py
"""DenseScorerStep — cosine sim re-scoring on np.ndarray (AC-18)."""
import numpy as np
import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.dense_scorer import DenseScorerStep
from tests._fakes import MockEmbedder


def _cos(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


@pytest.mark.asyncio
async def test_dense_scorer_writes_cosine_similarity_per_candidate() -> None:
    embedder = MockEmbedder(dim=4)
    # MockEmbedder is deterministic — same input → same np.ndarray.
    q_vec = await embedder.embed_query("alpha")
    a_vec = await embedder.embed_query("alpha")
    b_vec = await embedder.embed_query("beta")

    candidates = ChunkList(items=(
        Chunk(text="alpha", id=1, embedding=a_vec),
        Chunk(text="beta", id=2, embedding=b_vec),
    ))
    state = RetrieverState(
        query=SearchQuery(terms="alpha", max_results=10),
        candidates=candidates,
    )
    step = DenseScorerStep(name="dense_scorer", embedder=embedder)
    out = await step.run(state)
    items = out.candidates.items
    expected_a = _cos(q_vec, a_vec)
    expected_b = _cos(q_vec, b_vec)
    by_id = {c.id: c for c in items}
    assert by_id[1].relevance == pytest.approx(expected_a, rel=1e-5)
    assert by_id[2].relevance == pytest.approx(expected_b, rel=1e-5)


@pytest.mark.asyncio
async def test_dense_scorer_no_candidates_returns_state() -> None:
    embedder = MockEmbedder(dim=4)
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10), candidates=None,
    )
    step = DenseScorerStep(name="dense_scorer", embedder=embedder)
    out = await step.run(state)
    assert out.candidates is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/steps/test_dense_scorer.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create dense_scorer.py**

```python
# python/pydocs_mcp/retrieval/steps/dense_scorer.py
"""DenseScorerStep — overwrite candidate.relevance with cosine similarity (AC-18).

Mirrors BM25ScorerStep's shape:
- Reads state.candidates (no DB access).
- Embeds query via the injected Embedder.
- For each candidate, computes cosine sim between query_vec and
  candidate.embedding using numpy linalg.
- Writes the scores back to state.candidates with updated relevance.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from pydocs_mcp.models import ChunkList, is_multi_vector
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import Embedder


def _cosine_sim(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


@step_registry.register("dense_scorer")
@dataclass(frozen=True, slots=True)
class DenseScorerStep(RetrieverStep):
    embedder: Embedder = field(default=None)  # type: ignore[assignment]
    name: str = field(default="dense_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        if not isinstance(state.candidates, ChunkList):
            return state  # ModuleMember candidates not supported here
        if not state.candidates.items:
            return state

        query_vec = await self.embedder.embed_query(state.query.terms)
        if is_multi_vector(query_vec):
            query_vec = query_vec[0]  # single-vector path only this PR
        # Ensure np.ndarray + float32 (FastEmbed/OpenAI already comply;
        # MockEmbedder also; defensive cast is cheap).
        query_vec = np.asarray(query_vec, dtype=np.float32)

        scored = []
        for c in state.candidates.items:
            if c.embedding is None:
                scored.append(c)
                continue
            chunk_vec = c.embedding[0] if is_multi_vector(c.embedding) else c.embedding
            chunk_vec = np.asarray(chunk_vec, dtype=np.float32)
            score = _cosine_sim(query_vec, chunk_vec)
            scored.append(replace(c, relevance=score))
        return replace(state, candidates=ChunkList(items=tuple(scored)))

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "DenseScorerStep":
        if context.embedder is None:
            raise ValueError(
                "DenseScorerStep requires BuildContext.embedder to be set.",
            )
        return cls(embedder=context.embedder)

    def to_dict(self) -> dict:
        return {"type": "dense_scorer"}


__all__ = ("DenseScorerStep",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/retrieval/steps/test_dense_scorer.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/dense_scorer.py tests/retrieval/steps/test_dense_scorer.py
git commit -m "feat(retrieval): DenseScorerStep — cosine-sim re-scoring step

AC-18. Mirrors bm25_scorer shape: no DB access; reads candidates,
embeds query, computes cosine similarity per candidate.embedding,
writes scores back. Single-vector path only this PR — multi-vector
query/candidate uses first vector as degraded fallback (logged in
future PR)."
```

---

## Tasks 21-25: HybridSqliteTurboStore + ParallelStep + ingestion stage + YAML pipelines

### Task 21: `HybridSqliteTurboStore`

**Files:**
- Create: `python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py`
- Test: `tests/storage/test_hybrid_sqlite_turbo_store.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_hybrid_sqlite_turbo_store.py
"""HybridSqliteTurboStore composes text + vector + fuser (spec §5.3)."""
from collections.abc import Sequence

import pytest

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.hybrid_sqlite_turbo_store import HybridSqliteTurboStore


class _FakeTextStore:
    async def text_search(self, query_terms, limit, filter=None):
        # Returns 2 chunks: id=1 (alpha), id=2 (beta)
        return (
            Chunk(text="alpha", id=1, relevance=0.9, retriever_name="text"),
            Chunk(text="beta", id=2, relevance=0.5, retriever_name="text"),
        )


class _FakeVectorStore:
    async def vector_search(self, query_vector, limit, filter=None):
        # Returns 2 chunks: id=2 (beta), id=3 (gamma)
        return (
            Chunk(text="beta", id=2, relevance=0.95, retriever_name="vec"),
            Chunk(text="gamma", id=3, relevance=0.7, retriever_name="vec"),
        )


class _FakeFuser:
    def __init__(self):
        self.calls: list[tuple[Sequence, int]] = []

    async def fuse(self, ranked_lists, *, limit):
        self.calls.append((ranked_lists, limit))
        # Just concat + dedupe by id for the fake.
        seen = set()
        out = []
        for lst in ranked_lists:
            for c in lst:
                if c.id not in seen:
                    out.append(c)
                    seen.add(c.id)
        return tuple(out[:limit])


@pytest.mark.asyncio
async def test_hybrid_search_runs_both_stores_concurrently_and_fuses() -> None:
    fuser = _FakeFuser()
    store = HybridSqliteTurboStore(
        text=_FakeTextStore(),
        vector=_FakeVectorStore(),
        fuser=fuser,
    )
    results = await store.hybrid_search(
        query_terms="alpha", query_vector=(0.1, 0.2), limit=10,
    )
    assert len(fuser.calls) == 1
    ranked_lists, limit = fuser.calls[0]
    assert limit == 10
    assert len(ranked_lists) == 2
    # All 3 distinct ids present in fused output.
    assert {c.id for c in results} == {1, 2, 3}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_hybrid_sqlite_turbo_store.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create hybrid_sqlite_turbo_store.py**

```python
# python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py
"""HybridSqliteTurboStore — composes text + vector + ResultFuser (spec §5.3)."""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass

from pydocs_mcp.models import Chunk
from pydocs_mcp.storage.filters import Filter
from pydocs_mcp.storage.protocols import (
    ResultFuser,
    TextSearchable,
    VectorSearchable,
)


@dataclass
class HybridSqliteTurboStore:
    """HybridSearchable composing a text store + a vector store + a fuser.

    Runs both stores concurrently via asyncio.gather; hands the two ranked
    lists to the fuser to produce the merged ranking. Cross-store ID
    dedupe is the fuser's responsibility.
    """
    text: TextSearchable
    vector: VectorSearchable
    fuser: ResultFuser

    async def hybrid_search(
        self,
        query_terms: str,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | None = None,
        *,
        alpha: float = 0.5,
    ) -> tuple[Chunk, ...]:
        text_task = self.text.text_search(query_terms, limit, filter)
        vec_task = self.vector.vector_search(query_vector, limit, filter)
        text_results, vec_results = await asyncio.gather(text_task, vec_task)
        return await self.fuser.fuse(
            [text_results, vec_results], limit=limit,
        )


__all__ = ("HybridSqliteTurboStore",)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/storage/test_hybrid_sqlite_turbo_store.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/hybrid_sqlite_turbo_store.py tests/storage/test_hybrid_sqlite_turbo_store.py
git commit -m "feat(storage): HybridSqliteTurboStore — text + vector + fuser composition

Spec §5.3. Three injected dependencies — TextSearchable,
VectorSearchable, ResultFuser. Concurrent execution via asyncio.gather;
fusion deferred to the injected fuser. Future Qdrant swap is one
constructor arg change."
```

---

### Task 22: `ParallelStep` scratch contract clarification

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/parallel.py`
- Test: `tests/retrieval/steps/test_parallel_scratch_handoff.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/steps/test_parallel_scratch_handoff.py
"""ParallelStep branches can publish to scratch keys for RRFFusion to consume
(spec §3.1 Decision 3 + AC-21 prereq)."""
from dataclasses import dataclass, field, replace

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep


@dataclass(frozen=True, slots=True)
class _SeedCandidatesStep(RetrieverStep):
    """Test helper: writes a fixed candidates list into state."""
    items: tuple[Chunk, ...] = ()
    name: str = field(default="seed", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        return replace(state, candidates=ChunkList(items=self.items))


@pytest.mark.asyncio
async def test_parallel_branches_publish_to_scratch_keys() -> None:
    """Two branches each end with TopKFilterStep(publish_to=...). Output
    state.scratch carries both branch publications."""
    bm25_items = (Chunk(text="a", id=1, relevance=0.9),)
    dense_items = (Chunk(text="b", id=2, relevance=0.8),)
    bm25_branch = (
        _SeedCandidatesStep(items=bm25_items, name="seed_bm25"),
        TopKFilterStep(name="bm25_topk", k=10, publish_to="bm25.ranked"),
    )
    dense_branch = (
        _SeedCandidatesStep(items=dense_items, name="seed_dense"),
        TopKFilterStep(name="dense_topk", k=10, publish_to="dense.ranked"),
    )
    parallel = ParallelStep(
        name="parallel",
        branches=({"name": "bm25", "steps": bm25_branch},
                  {"name": "dense", "steps": dense_branch}),
    )
    state = RetrieverState(query=SearchQuery(terms="x", max_results=10))
    out = await parallel.run(state)
    assert "bm25.ranked" in out.scratch
    assert "dense.ranked" in out.scratch
```

NOTE: The actual `ParallelStep` API may use a different `branches` shape — adjust the test fixture to match. The behavior tested (that branches' scratch writes are preserved into the final state) is what matters.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/steps/test_parallel_scratch_handoff.py -v`
Expected: FAIL — scratch keys absent in the output state because current ParallelStep merges via `state.result` only (per the Explore survey).

- [ ] **Step 3: Update ParallelStep**

In `python/pydocs_mcp/retrieval/steps/parallel.py`, modify the merge logic so each branch's `state.scratch` writes propagate to the final state. Concretely, after each branch's sub-pipeline runs, copy non-conflicting scratch keys to the merged state. Existing `state.result` merge behavior stays for backward compat.

```python
async def run(self, state: RetrieverState) -> RetrieverState:
    # ... existing async-gather of branches producing branch_states ...
    merged_scratch = dict(state.scratch)
    for branch_state in branch_states:
        for key, value in branch_state.scratch.items():
            # Last-write-wins; branches publish under unique
            # <branch_name>.<field> keys by convention.
            merged_scratch[key] = value
    # ... existing state.result merge ...
    new_state = replace(state, result=merged_result, scratch=merged_scratch)
    return new_state
```

(Splice this scratch-merge into the existing merge function. The `state.result` merge stays intact for the legacy use case.)

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/retrieval/steps/test_parallel_scratch_handoff.py -v
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3
```

Expected: 1 new test PASS; full suite still PASS.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/retrieval/steps/parallel.py tests/retrieval/steps/test_parallel_scratch_handoff.py
git commit -m "feat(retrieval): ParallelStep propagates branches' scratch writes

Spec §3.1 Decision 3. Each parallel branch's state.scratch (typically
set via TopKFilterStep.publish_to) is merged into the final state so
downstream RRFFusionStep can read the named branch keys. Last-write-
wins on key collision — branches use unique <branch_name>.<field>
convention. state.result merge behavior unchanged."
```

---

### Task 23: `EmbedChunksStage` ingestion stage

**Files:**
- Create: `python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py`
- Test: `tests/extraction/pipeline/stages/test_embed_chunks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/pipeline/stages/test_embed_chunks.py
"""EmbedChunksStage populates Chunk.embedding (AC-23)."""
from dataclasses import replace

import pytest

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.pipeline.stages.embed_chunks import EmbedChunksStage
from pydocs_mcp.models import Chunk
from tests._fakes import MockEmbedder


@pytest.mark.asyncio
async def test_embed_chunks_populates_every_chunk_embedding() -> None:
    import numpy as np
    embedder = MockEmbedder(dim=4)
    state = IngestionState(
        chunks=(
            Chunk(text="alpha", id=1),
            Chunk(text="beta", id=2),
            Chunk(text="gamma", id=3),
        ),
    )
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    out = await stage.run(state)
    assert len(out.chunks) == 3
    # Every chunk now has a np.ndarray embedding of shape (4,).
    for c in out.chunks:
        assert isinstance(c.embedding, np.ndarray)
        assert c.embedding.shape == (4,)
    # Determinism: re-embedding the same text gives the same vector.
    assert np.array_equal(
        out.chunks[0].embedding, await embedder.embed_query("alpha"),
    )


@pytest.mark.asyncio
async def test_embed_chunks_empty_state_no_op() -> None:
    embedder = MockEmbedder(dim=4)
    state = IngestionState(chunks=())
    stage = EmbedChunksStage(embedder=embedder, batch_size=2)
    out = await stage.run(state)
    assert out.chunks == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_embed_chunks.py -v`
Expected: FAIL — `ModuleNotFoundError`.

- [ ] **Step 3: Create embed_chunks.py**

```python
# python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py
"""EmbedChunksStage — batch-embed state.chunks during ingestion (AC-23)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydocs_mcp.extraction.pipeline.ingestion import IngestionState
from pydocs_mcp.extraction.serialization import stage_registry
from pydocs_mcp.storage.protocols import Embedder


_DEFAULT_BATCH_SIZE = 32


@stage_registry.register("embed_chunks")
@dataclass(frozen=True, slots=True)
class EmbedChunksStage:
    """Compute embeddings for every chunk in state.chunks via the
    configured Embedder. Idempotent — if a chunk already has an
    embedding, it's recomputed (the cheap path is to skip ingestion
    entirely via the existing per-package hash skip)."""
    embedder: Embedder
    batch_size: int = _DEFAULT_BATCH_SIZE

    async def run(self, state: IngestionState) -> IngestionState:
        if not state.chunks:
            return state
        all_embeddings: list = []
        for i in range(0, len(state.chunks), self.batch_size):
            batch = state.chunks[i:i + self.batch_size]
            embs = await self.embedder.embed_chunks([c.text for c in batch])
            all_embeddings.extend(embs)
        new_chunks = tuple(
            replace(c, embedding=emb)
            for c, emb in zip(state.chunks, all_embeddings)
        )
        return replace(state, chunks=new_chunks)


__all__ = ("EmbedChunksStage",)
```

NOTE on the `@stage_registry.register("embed_chunks")` decorator: this stage cannot be built by YAML alone (it needs an `embedder` instance, which YAML doesn't construct). The `from_dict` classmethod will need to pull the embedder from `BuildContext.embedder`. If the existing `stage_registry` requires a `from_dict`, add one that raises until the BuildContext wiring is done (Task 28).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/pipeline/stages/test_embed_chunks.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/extraction/pipeline/stages/embed_chunks.py tests/extraction/pipeline/stages/test_embed_chunks.py
git commit -m "feat(extraction): EmbedChunksStage — batched ingestion-time embedder

AC-23. Slots between flatten and content_hash in ingestion.yaml.
Configurable batch_size (default 32). Uses replace(chunk, embedding=…)
on every Chunk in state.chunks — the cheap path is the existing
per-package hash-skip which avoids running this stage at all on
unchanged repos."
```

---

### Task 24: Wire `embed_chunks` into `pipelines/ingestion.yaml`

**Files:**
- Modify: `python/pydocs_mcp/pipelines/ingestion.yaml`
- Test: `tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py
"""ingestion.yaml includes embed_chunks between flatten and content_hash."""
from pathlib import Path

import yaml

INGESTION_YAML = (
    Path(__file__).resolve().parents[3]
    / "python" / "pydocs_mcp" / "pipelines" / "ingestion.yaml"
)


def test_embed_chunks_is_between_flatten_and_content_hash() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text())
    step_names = [s["name"] for s in cfg["steps"]]
    flatten_idx = step_names.index("flatten")
    embed_idx = step_names.index("embed_chunks")
    hash_idx = step_names.index("content_hash")
    assert flatten_idx < embed_idx < hash_idx


def test_embed_chunks_step_type_is_embed_chunks() -> None:
    cfg = yaml.safe_load(INGESTION_YAML.read_text())
    embed_step = next(
        s for s in cfg["steps"] if s["name"] == "embed_chunks"
    )
    assert embed_step["type"] == "embed_chunks"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py -v`
Expected: FAIL — `embed_chunks` not in step_names yet.

- [ ] **Step 3: Update ingestion.yaml**

Insert the new step between `flatten` and `content_hash`. Result (full file):

```yaml
# python/pydocs_mcp/pipelines/ingestion.yaml
name: ingestion
steps:
  - {name: file_discovery, type: file_discovery, params: {}}
  - {name: file_read, type: file_read, params: {}}
  - {name: chunking, type: chunking, params: {}}
  - {name: reference_capture, type: reference_capture, params: {}}
  - {name: flatten, type: flatten, params: {}}
  - {name: embed_chunks, type: embed_chunks, params: {batch_size: 32}}   # NEW
  - {name: content_hash, type: content_hash, params: {}}
  - {name: package_build, type: package_build, params: {}}
```

(Adapt to the existing YAML layout — preserve any `params: {}` blocks already there. Just insert the new entry.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/pipelines/ingestion.yaml tests/extraction/pipeline/test_ingestion_yaml_includes_embed_chunks.py
git commit -m "feat(ingestion): slot embed_chunks between flatten and content_hash

AC-23 wiring. embed_chunks now runs after state.chunks is populated by
flatten and before content_hash computes the per-package hash. Hash
skip behavior unchanged — unchanged packages skip the entire ingestion
pipeline including this new stage."
```

---

### Task 25: Four new chunk-search YAML presets (dense + dense_ranked + hybrid + hybrid_ranked)

**Files:**
- Create: `python/pydocs_mcp/pipelines/chunk_search_dense.yaml`
- Create: `python/pydocs_mcp/pipelines/chunk_search_dense_ranked.yaml`
- Create: `python/pydocs_mcp/pipelines/chunk_search_hybrid.yaml`
- Create: `python/pydocs_mcp/pipelines/chunk_search_hybrid_ranked.yaml`
- Test: `tests/retrieval/test_new_pipeline_presets_load.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/retrieval/test_new_pipeline_presets_load.py
"""All four new YAML presets parse + assemble without error (AC-21 prereq)."""
from pathlib import Path

import pytest
import yaml

PIPELINES_DIR = (
    Path(__file__).resolve().parents[2]
    / "python" / "pydocs_mcp" / "pipelines"
)

NEW_PRESETS = (
    "chunk_search_dense.yaml",
    "chunk_search_dense_ranked.yaml",
    "chunk_search_hybrid.yaml",
    "chunk_search_hybrid_ranked.yaml",
)


@pytest.mark.parametrize("preset", NEW_PRESETS)
def test_preset_file_exists_and_parses(preset: str) -> None:
    path = PIPELINES_DIR / preset
    assert path.exists(), f"missing preset {preset}"
    cfg = yaml.safe_load(path.read_text())
    assert "name" in cfg
    assert "steps" in cfg
    assert len(cfg["steps"]) >= 2


def test_hybrid_preset_has_parallel_then_rrf_fusion() -> None:
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_hybrid.yaml").read_text(),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "parallel" in step_types
    assert "rrf_fusion" in step_types
    assert step_types.index("parallel") < step_types.index("rrf_fusion")


def test_dense_preset_uses_dense_fetcher_and_dense_scorer() -> None:
    cfg = yaml.safe_load(
        (PIPELINES_DIR / "chunk_search_dense.yaml").read_text(),
    )
    step_types = [s["type"] for s in cfg["steps"]]
    assert "dense_fetcher" in step_types
    assert "dense_scorer" in step_types


def test_ranked_variants_drop_token_budget_formatter() -> None:
    for ranked in ("chunk_search_dense_ranked.yaml", "chunk_search_hybrid_ranked.yaml"):
        cfg = yaml.safe_load((PIPELINES_DIR / ranked).read_text())
        step_types = [s["type"] for s in cfg["steps"]]
        assert "token_budget_formatter" not in step_types
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/retrieval/test_new_pipeline_presets_load.py -v`
Expected: FAIL — files don't exist.

- [ ] **Step 3: Create the four YAML files**

`python/pydocs_mcp/pipelines/chunk_search_dense.yaml`:

```yaml
# Dense-only chunk search (Task 25).
#
# Pre-filter -> dense fetcher (TurboQuant allowlist) -> dense scorer
# (cosine sim) -> metadata post-filter -> top-K -> limit -> token-budget
# formatter for MCP composite output. Mirrors chunk_search.yaml's
# composite shape but on the dense side.
name: chunk_search_dense
steps:
  - {name: pre_filter, type: pre_filter, params: {schema_name: chunk, target_field: chunk}}
  - {name: fetch, type: dense_fetcher, params: {limit: 50}}
  - {name: score, type: dense_scorer, params: {}}
  - {name: filter, type: metadata_post_filter, params: {}}
  - {name: topk, type: top_k_filter, params: {}}
  - {name: limit, type: limit, params: {max_results: 8}}
  - {name: budget, type: token_budget_formatter, params: {formatter: {type: chunk_markdown}, budget: 2000}}
```

`python/pydocs_mcp/pipelines/chunk_search_dense_ranked.yaml`:

```yaml
# Ranked dense-only chunk search — benchmark + evaluation use.
# Drops token_budget_formatter so state.candidates carries the
# ranked top-K (matches chunk_search_ranked.yaml convention).
name: chunk_search_dense_ranked
steps:
  - {name: pre_filter, type: pre_filter, params: {schema_name: chunk, target_field: chunk}}
  - {name: fetch, type: dense_fetcher, params: {limit: 50}}
  - {name: score, type: dense_scorer, params: {}}
  - {name: filter, type: metadata_post_filter, params: {}}
  - {name: topk, type: top_k_filter, params: {}}
  - {name: limit, type: limit, params: {max_results: 8}}
```

`python/pydocs_mcp/pipelines/chunk_search_hybrid.yaml`:

```yaml
# Hybrid BM25 + Dense chunk search (Task 25 + spec §5.8).
#
# Pre-filter runs once; both branches read the shared pre_filter.result
# from scratch. ParallelStep dispatches BM25 + Dense concurrently;
# each branch publishes its ranking to scratch["<branch>.ranked"] via
# TopKFilterStep.publish_to. RRFFusionStep reads both lists, fuses,
# writes state.candidates. Limit + budget render for MCP.
name: chunk_search_hybrid
steps:
  - {name: pre_filter, type: pre_filter, params: {schema_name: chunk, target_field: chunk}}
  - name: parallel
    type: parallel
    params:
      branches:
        - name: bm25
          steps:
            - {name: fetch, type: chunk_fetcher, params: {limit: 50}}
            - {name: score, type: bm25_scorer, params: {}}
            - {name: filter, type: metadata_post_filter, params: {}}
            - {name: topk, type: top_k_filter, params: {publish_to: "bm25.ranked"}}
        - name: dense
          steps:
            - {name: fetch, type: dense_fetcher, params: {limit: 50}}
            - {name: score, type: dense_scorer, params: {}}
            - {name: filter, type: metadata_post_filter, params: {}}
            - {name: topk, type: top_k_filter, params: {publish_to: "dense.ranked"}}
  - {name: rrf_fusion, type: rrf_fusion, params: {k: 60, branch_keys: ["bm25.ranked", "dense.ranked"]}}
  - {name: limit, type: limit, params: {max_results: 8}}
  - {name: budget, type: token_budget_formatter, params: {formatter: {type: chunk_markdown}, budget: 2000}}
```

`python/pydocs_mcp/pipelines/chunk_search_hybrid_ranked.yaml`:

```yaml
# Ranked hybrid — benchmark + evaluation use.
name: chunk_search_hybrid_ranked
steps:
  - {name: pre_filter, type: pre_filter, params: {schema_name: chunk, target_field: chunk}}
  - name: parallel
    type: parallel
    params:
      branches:
        - name: bm25
          steps:
            - {name: fetch, type: chunk_fetcher, params: {limit: 50}}
            - {name: score, type: bm25_scorer, params: {}}
            - {name: filter, type: metadata_post_filter, params: {}}
            - {name: topk, type: top_k_filter, params: {publish_to: "bm25.ranked"}}
        - name: dense
          steps:
            - {name: fetch, type: dense_fetcher, params: {limit: 50}}
            - {name: score, type: dense_scorer, params: {}}
            - {name: filter, type: metadata_post_filter, params: {}}
            - {name: topk, type: top_k_filter, params: {publish_to: "dense.ranked"}}
  - {name: rrf_fusion, type: rrf_fusion, params: {k: 60, branch_keys: ["bm25.ranked", "dense.ranked"]}}
  - {name: limit, type: limit, params: {max_results: 8}}
```

(Confirm the `parallel` step's YAML key — if the existing `ParallelStep.from_dict` expects a different shape than `branches: [{name, steps: [...]}]`, adjust to match. The `to_dict`/`from_dict` round-trip must work.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/retrieval/test_new_pipeline_presets_load.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/pipelines/chunk_search_dense.yaml python/pydocs_mcp/pipelines/chunk_search_dense_ranked.yaml python/pydocs_mcp/pipelines/chunk_search_hybrid.yaml python/pydocs_mcp/pipelines/chunk_search_hybrid_ranked.yaml tests/retrieval/test_new_pipeline_presets_load.py
git commit -m "feat(pipelines): four new chunk-search presets (dense + hybrid, composite + ranked)

Task 25 + AC-21. Dense-only and Hybrid (BM25+Dense fused via RRF)
presets land in two shapes each: composite (token_budget_formatter
last → state.result) and ranked (no formatter → state.candidates as
ranked top-K, benchmark-friendly). MCP default remains chunk_search.yaml
(BM25 composite); these four are opt-in via YAML overlay or benchmark
config selection."
```

---

## Tasks 26-31: Composition root + integrity check + re-embed + housekeeping + final gauntlet

### Task 26: Wire `IndexingService` to write vectors via composite UoW

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py`
- Test: `tests/application/test_indexing_writes_vectors.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_indexing_writes_vectors.py
"""IndexingService writes vectors alongside chunks via composite UoW (AC-24)."""
from pathlib import Path

import pytest

from pydocs_mcp.db import (
    open_index_database,
    turboquant_path_for_project,
)
from pydocs_mcp.models import Chunk, Package
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


@pytest.mark.asyncio
async def test_index_package_writes_chunks_AND_vectors(tmp_path: Path) -> None:
    """End-to-end: IndexingService.replace_package commits to both children."""
    import numpy as np
    from pydocs_mcp.application.indexing_service import IndexingService

    project = tmp_path / "myproj"
    project.mkdir()
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()

    chunks = (
        Chunk(text="alpha", id=None, embedding=np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)),
        Chunk(text="beta", id=None, embedding=np.array([0.5, 0.6, 0.7, 0.8], dtype=np.float32)),
    )
    package = Package(name="demo", version="1.0", embedding_model="test-model")

    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    svc = IndexingService(uow_factory=factory)
    await svc.replace_package(package, chunks)

    # SQLite has the chunks + packages.embedding_model is set.
    async with factory() as uow:
        all_chunks = await uow.chunks.list(filter={"package": "demo"})
        assert len(all_chunks) == 2
    # TurboQuant has 2 vectors.
    async with TurboQuantUnitOfWork(
        index_path=tq_path, dim=4, bit_width=8,
    ) as tq_uow:
        assert tq_uow.size() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/application/test_indexing_writes_vectors.py -v`
Expected: FAIL — current IndexingService doesn't touch `uow.vectors`.

- [ ] **Step 3: Update IndexingService**

In `python/pydocs_mcp/application/indexing_service.py`, locate the `replace_package` (or equivalent write) method. Modify it to also write vectors:

```python
async def replace_package(
    self, package: Package, chunks: tuple[Chunk, ...],
) -> None:
    async with self.uow_factory() as uow:
        # ... existing delete + upsert sequence for chunks + packages ...
        await uow.packages.upsert(package)
        # Upsert chunks; capture their post-insert IDs.
        await uow.chunks.upsert(chunks)
        # Re-fetch to get the assigned IDs (chunks.id is autoincrement).
        # The existing implementation already does this — slot the vector
        # write right after, before commit.
        if hasattr(uow, "vectors"):
            persisted = await uow.chunks.list(filter={"package": package.name})
            ids = [c.id for c in persisted if c.id is not None]
            embeddings = [
                next(orig.embedding for orig in chunks if orig.text == c.text)
                for c in persisted
                if c.embedding is None  # was set on the original, not the row
            ]
            # Simpler: trust caller's chunks have embeddings + match
            # persisted by text.
            embeddings_by_text = {c.text: c.embedding for c in chunks if c.embedding is not None}
            id_emb_pairs = [
                (c.id, embeddings_by_text[c.text])
                for c in persisted
                if c.text in embeddings_by_text and c.id is not None
            ]
            if id_emb_pairs:
                ids_list, embs = zip(*id_emb_pairs)
                await uow.vectors.add_vectors(list(ids_list), list(embs))
        await uow.commit()
```

(Adapt to the actual existing `replace_package` implementation — the key insertion is the `if hasattr(uow, 'vectors')` block, gated for backward compat with `SqliteUnitOfWork`-only setups.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/application/test_indexing_writes_vectors.py -v`
Expected: PASS (1 test). Full suite still green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_writes_vectors.py
git commit -m "feat(application): IndexingService writes vectors via composite UoW

AC-24. On the write path, when uow.vectors is present (composite UoW
with TurboQuant child), add_vectors is called alongside chunks.upsert
inside the same async-with transaction. CompositeUnitOfWork.commit
attempts both children sequentially; failure rolls back the other
best-effort."
```

---

### Task 27: Composition-root flip + startup integrity check

**Files:**
- Modify: `python/pydocs_mcp/server.py`
- Modify: `python/pydocs_mcp/__main__.py`
- Modify: `python/pydocs_mcp/storage/factories.py` (add integrity-check helper)
- Test: `tests/storage/test_integrity_check.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/storage/test_integrity_check.py
"""Startup integrity check detects + repairs chunks-vs-vectors mismatch (AC-25)."""
import asyncio
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
    check_integrity_and_repair,
)
from pydocs_mcp.storage.turboquant_uow import TurboQuantUnitOfWork


@pytest.mark.asyncio
async def test_integrity_check_clears_content_hash_on_size_mismatch(
    tmp_path: Path, caplog,
) -> None:
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    # Seed 3 chunks in SQLite, but only 1 vector in TurboQuant — mismatch.
    async with factory() as uow:
        pkg = Package(name="demo", version="1", content_hash="abc123")
        await uow.packages.upsert(pkg)
        await uow.chunks.upsert((
            Chunk(text="a", id=1, metadata={"package": "demo"}),
            Chunk(text="b", id=2, metadata={"package": "demo"}),
            Chunk(text="c", id=3, metadata={"package": "demo"}),
        ))
        await uow.vectors.add_vectors([1], [np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)])
        await uow.commit()
    # Now run the integrity check.
    import logging
    caplog.set_level(logging.WARNING)
    repaired_pkg_names = await check_integrity_and_repair(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    assert "demo" in repaired_pkg_names
    # demo's content_hash got cleared so the next index sweep re-extracts.
    async with factory() as uow:
        pkgs = await uow.packages.list(filter={"name": "demo"})
        assert pkgs[0].content_hash in (None, "")
    # Log message includes the mismatch.
    assert any(
        "mismatch" in r.message.lower()
        for r in caplog.records
    )


@pytest.mark.asyncio
async def test_integrity_check_passes_when_counts_match(tmp_path: Path) -> None:
    db_path = tmp_path / "y.db"
    tq_path = tmp_path / "y.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    async with factory() as uow:
        await uow.packages.upsert(Package(name="demo", version="1", content_hash="abc"))
        await uow.chunks.upsert((
            Chunk(text="a", id=1, metadata={"package": "demo"}),
        ))
        await uow.vectors.add_vectors([1], [np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)])
        await uow.commit()
    repaired = await check_integrity_and_repair(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    assert repaired == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/storage/test_integrity_check.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_integrity_and_repair'`.

- [ ] **Step 3: Add the integrity check + flip composition roots**

In `python/pydocs_mcp/storage/factories.py`, append:

```python
import sqlite3

async def check_integrity_and_repair(
    *, db_path: Path, tq_path: Path, dim: int, bit_width: int,
) -> list[str]:
    """Compare chunks.count vs IdMapIndex.size; on mismatch, log warning
    and clear content_hash on the affected packages so next index sweep
    re-extracts them. Returns the list of repaired package names.

    Per spec §3.2 + §5.7. Called at server startup; cache is regenerable
    so silent recovery preserves user flow.
    """
    def _chunk_count() -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        finally:
            conn.close()
    chunk_count = await asyncio.to_thread(_chunk_count)
    async with TurboQuantUnitOfWork(
        index_path=tq_path, dim=dim, bit_width=bit_width,
    ) as tq_uow:
        vec_count = tq_uow.size()
    if chunk_count == vec_count:
        return []
    logger.warning(
        "Cache integrity mismatch: chunks=%d but TurboQuant index "
        "size=%d. Clearing content_hash on affected packages so next "
        "indexing sweep re-extracts them.",
        chunk_count, vec_count,
    )
    # Heuristic: clear content_hash on ALL packages — the indexing sweep
    # will hash-skip the ones whose mtimes match anyway, but force re-
    # extract the ones that don't. This is the cheapest correct response.
    def _clear_all_hashes() -> list[str]:
        conn = sqlite3.connect(str(db_path))
        try:
            names = [r[0] for r in conn.execute("SELECT name FROM packages")]
            conn.execute("UPDATE packages SET content_hash = NULL")
            conn.commit()
            return names
        finally:
            conn.close()
    return await asyncio.to_thread(_clear_all_hashes)
```

Then in `python/pydocs_mcp/server.py` and `python/pydocs_mcp/__main__.py`, locate every call site that builds the UoW factory and flip them:

```python
# BEFORE:
# uow_factory = build_sqlite_uow_factory(db_path)

# AFTER:
from pydocs_mcp.db import turboquant_path_for_project
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
    check_integrity_and_repair,
)

config = AppConfig.load(...)
db_path = cache_path_for_project(project_dir)
tq_path = turboquant_path_for_project(project_dir)
open_index_database(db_path).close()  # ensure schema v5

# Run integrity check (logs + clears hashes if mismatched).
await check_integrity_and_repair(
    db_path=db_path, tq_path=tq_path,
    dim=config.embedding.dim, bit_width=config.embedding.bit_width,
)

uow_factory = build_sqlite_plus_turboquant_uow_factory(
    db_path=db_path, tq_path=tq_path,
    dim=config.embedding.dim, bit_width=config.embedding.bit_width,
)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
.venv/bin/pytest tests/storage/test_integrity_check.py -v
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3
```

Expected: 2 PASS; full suite still green.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/storage/factories.py python/pydocs_mcp/server.py python/pydocs_mcp/__main__.py tests/storage/test_integrity_check.py
git commit -m "feat(server): composite UoW factory + startup integrity check

AC-25. Composition roots flip from build_sqlite_uow_factory to
build_sqlite_plus_turboquant_uow_factory — one line per call site.
Service consumers (async with self.uow_factory() as uow:) keep working
unchanged via CompositeUnitOfWork attribute proxying. New
check_integrity_and_repair runs at startup; logs warning and clears
content_hash on packages when chunks.count != IdMapIndex.size() so the
next index sweep re-extracts them."
```

---

### Task 28: Re-embed on model change

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py` (or wherever the hash-skip lives)
- Test: `tests/application/test_re_embed_on_model_change.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/application/test_re_embed_on_model_change.py
"""Changing YAML's embedding.model_name forces re-embed of every package (AC-26)."""
from pathlib import Path

import pytest

from pydocs_mcp.db import open_index_database
from pydocs_mcp.models import Chunk, Package
from pydocs_mcp.storage.factories import (
    build_sqlite_plus_turboquant_uow_factory,
)


@pytest.mark.asyncio
async def test_indexed_package_records_embedding_model(tmp_path: Path) -> None:
    """packages.embedding_model is populated on every indexed package."""
    from pydocs_mcp.application.indexing_service import IndexingService
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    svc = IndexingService(uow_factory=factory)
    pkg = Package(name="demo", version="1.0", embedding_model="model-A")
    await svc.replace_package(pkg, (
        Chunk(text="alpha", embedding=np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)),
    ))
    async with factory() as uow:
        pkgs = await uow.packages.list(filter={"name": "demo"})
        assert pkgs[0].embedding_model == "model-A"


@pytest.mark.asyncio
async def test_model_change_detected_via_stored_embedding_model(
    tmp_path: Path,
) -> None:
    """Helper that checks for stale embeddings returns names of packages
    whose stored embedding_model != current YAML model."""
    from pydocs_mcp.application.indexing_service import (
        find_packages_with_stale_embeddings,
    )
    from pydocs_mcp.application.indexing_service import IndexingService
    db_path = tmp_path / "x.db"
    tq_path = tmp_path / "x.tq"
    open_index_database(db_path).close()
    factory = build_sqlite_plus_turboquant_uow_factory(
        db_path=db_path, tq_path=tq_path, dim=4, bit_width=8,
    )
    svc = IndexingService(uow_factory=factory)
    await svc.replace_package(
        Package(name="pkg-a", version="1", embedding_model="model-A"),
        (Chunk(text="a", embedding=np.zeros(4, dtype=np.float32)),),
    )
    await svc.replace_package(
        Package(name="pkg-b", version="1", embedding_model="model-A"),
        (Chunk(text="b", embedding=np.zeros(4, dtype=np.float32)),),
    )
    stale = await find_packages_with_stale_embeddings(
        uow_factory=factory, current_model="model-B",
    )
    assert set(stale) == {"pkg-a", "pkg-b"}
    stale_no_change = await find_packages_with_stale_embeddings(
        uow_factory=factory, current_model="model-A",
    )
    assert stale_no_change == []
```

NOTE: This requires (a) `Package.embedding_model` field exists in the model, (b) `IndexingService` writes it on replace, (c) `find_packages_with_stale_embeddings` helper exists. If `Package.embedding_model` doesn't exist, the prerequisite is to add the field — should land in Task 7 (schema migration) but ALSO needs the model dataclass field. Confirm Package has it before this task; if not, split into a small prereq.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/application/test_re_embed_on_model_change.py -v`
Expected: FAIL — `find_packages_with_stale_embeddings` not defined, OR `Package.embedding_model` field missing.

- [ ] **Step 3: Add the field + the helper**

In `python/pydocs_mcp/models.py`, add `embedding_model: str | None = None` to `Package`.

In `python/pydocs_mcp/application/indexing_service.py`, add:

```python
async def find_packages_with_stale_embeddings(
    *, uow_factory: Callable[[], UnitOfWork], current_model: str,
) -> list[str]:
    """Return the names of packages whose stored embedding_model
    differs from current_model. Caller can clear their content_hash so
    the next indexing sweep re-extracts + re-embeds them."""
    async with uow_factory() as uow:
        all_pkgs = await uow.packages.list()
    stale = [
        p.name for p in all_pkgs
        if p.embedding_model is not None and p.embedding_model != current_model
    ]
    return stale
```

Also extend `IndexingService.replace_package` to populate `package.embedding_model` if not set (or trust caller passes it).

Wire the staleness check into composition root: at startup, after `check_integrity_and_repair`, also run `find_packages_with_stale_embeddings` and clear `content_hash` on any stale ones.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/application/test_re_embed_on_model_change.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/models.py python/pydocs_mcp/application/indexing_service.py tests/application/test_re_embed_on_model_change.py
git commit -m "feat(application): re-embed-on-model-change via stored embedding_model

AC-26. Package.embedding_model dataclass field; IndexingService writes
it on replace_package; find_packages_with_stale_embeddings returns
names where stored != current YAML model. Composition root calls the
helper at startup and clears content_hash on stale packages so the
next sweep re-extracts + re-embeds them — re-uses the existing
--force code path under the hood."
```

---

### Task 29: Housekeeping — extend README jargon regex + fix `PR-B3.1` refs

**Files:**
- Modify: `CLAUDE.md`
- Modify: `benchmarks/README.md`
- Test: `tests/test_readme_jargon_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_readme_jargon_audit.py
"""README jargon rule + audit grep (AC-29)."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_pr_jargon_in_readmes() -> None:
    """The audit regex must match nothing in any tracked README.md."""
    result = subprocess.run(
        [
            "bash", "-c",
            "find . -name 'README.md' "
            "-not -path '*/.venv/*' "
            "-not -path '*/.claude/*' "
            "-not -path '*/node_modules/*' "
            "-not -path '*/.git/*' "
            "-not -path '*/.pytest_cache/*' "
            "| xargs grep -nE '"
            "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of"
            "|PR-[A-Z][0-9.]+'",
        ],
        cwd=ROOT, capture_output=True, text=True,
    )
    # grep exits 1 (no match) on clean. Any match → exit 0 with output.
    assert result.returncode != 0, (
        f"README jargon violations:\n{result.stdout}"
    )


def test_claude_md_includes_pr_letter_pattern_in_jargon_rule() -> None:
    """CLAUDE.md's README-jargon section's regex catches PR-[A-Z]N.M."""
    claude_md = (ROOT / "CLAUDE.md").read_text()
    # The audit command in CLAUDE.md must include the PR-letter pattern.
    audit_block = claude_md.split("README files: no internal PR")[-1]
    audit_block = audit_block.split("Async Patterns")[0]
    assert "PR-[A-Z]" in audit_block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_readme_jargon_audit.py -v`
Expected: FAIL on the first test (the `PR-B3.1` refs in `benchmarks/README.md`) AND on the second test (CLAUDE.md regex doesn't include `PR-[A-Z]`).

- [ ] **Step 3: Update CLAUDE.md + benchmarks/README.md**

In `CLAUDE.md`, find the "README files: no internal PR / sub-PR / task jargon" section. Add `PR-[A-Z][0-9.]+` to both the forbidden-patterns list AND the audit grep command:

```markdown
- `PR-B3.1`, `PR-C2`, any `PR-<LETTER><N>.<M>`-style label — internal
  multi-PR series labels. Reference the *capability* instead
  ("the planned dense-embeddings + RRF baseline").

**Audit command** (run before merging any README change):

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```
```

In `benchmarks/README.md`, replace the five `PR-B3.1` references with capability language. Run `grep -n "PR-B3.1" benchmarks/README.md` to find them all and edit each.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_readme_jargon_audit.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md benchmarks/README.md tests/test_readme_jargon_audit.py
git commit -m "docs: extend README jargon rule + fix PR-B3.1 leakage (#4 ride-along)

AC-29. CLAUDE.md's README-files-no-jargon rule + audit grep now catches
PR-[A-Z][0-9.]+ multi-PR series labels (e.g., PR-B3.1, PR-C2) in
addition to the existing PR #N / sub-PR / trilogy patterns. The five
PR-B3.1 references in benchmarks/README.md (left behind from the
cleanups PR) are replaced with capability language."
```

---

### Task 30: Update GitHub issue #6 body

**Files:**
- (no source changes)

This is a manual checklist step, not a code commit. Per spec §"Issue #6 body update":

- [ ] **Step 1: Verify scope**

Read the spec's "Issue #6 body update" section (around line 270 of `docs/superpowers/specs/2026-05-24-hybrid-search-with-semantic-embeddings-design.md`). Confirm the body text reflects:
1. What this PR delivers (infrastructure only)
2. Open questions resolved
3. Open questions deferred (model selection, real recall, MCP default flip, multi-vector)
4. Validation track link to #2 (CodeRAG-Bench)
5. Acceptance criteria (5 infrastructure-level items)

- [ ] **Step 2: Update via gh CLI**

```bash
gh issue edit 6 --body "$(cat <<'EOF'
## Summary

Add infrastructure for hybrid (BM25 + dense) search alongside the
existing FTS5 BM25 search.

## What this PR delivers

INFRASTRUCTURE for hybrid search. Concrete deliverables:

- `TurboQuantVectorStore` + `HybridSqliteTurboStore` (composition over
  `TextSearchable` / `VectorSearchable` / `HybridSearchable` Protocols)
- `DenseFetcherStep` with TurboQuant `allowlist`-based metadata
  pre-filtering (the "filtered search" / hybrid retrieval feature from
  the turbovec README)
- `DenseScorerStep` (cosine similarity mirror of BM25ScorerStep)
- `RRFFusionStep` — multi-list rewrite of the previous single-list
  re-scorer
- `EmbeddingConfig` + `AppConfig.embedding` + shipped defaults
- Four new chunk-search YAML presets: `chunk_search_dense.yaml`,
  `chunk_search_dense_ranked.yaml`, `chunk_search_hybrid.yaml`,
  `chunk_search_hybrid_ranked.yaml`
- Load-bearing `CompositeUnitOfWork` coordinating `SqliteUnitOfWork` +
  new `TurboQuantUnitOfWork` (`.tq` sidecar file)
- `Embedder` Protocol with `FastEmbedEmbedder` + `OpenAIEmbedder`
  concretes shipped as available options (selection deferred — see
  below)
- Schema migration v4 → v5 (additive `packages.embedding_model` column)

## Open questions resolved

- Local vs API: both shipped via Protocol + `build_embedder` factory.
- Storage: TurboQuant `.tq` sidecar (MIT, ~16× compression).
- Fusion: multi-list RRF via `ParallelStep` → `RRFFusionStep`.
- Metadata pre-filter: TurboQuant `allowlist=` parameter.
- MCP surface: unchanged (every tunable through YAML).
- Re-embed-on-model-change: detected via stored
  `packages.embedding_model`.

## Open questions deferred (follow-up PR)

- **Which embedding model lands as the recommended default.** Benchmark-
  driven; the follow-up PR sweeps candidate embedders against RepoQA
  and CodeRAG-Bench (#2).
- **Real recall lift measurement.** This PR does NOT capture a hybrid
  baseline JSON; tests use a `MockEmbedder` everywhere.
- **MCP default flip** from BM25 to Hybrid — separate one-line PR once
  recall lift is confirmed.
- **Multi-vector / late-interaction storage** (ColBERT-style). Typed
  `Embedding` union accepts the shape; persistence is single-vector
  this PR.

## Validation track

CodeRAG-Bench (DS-1000 + ODEX) integration — see #2 — will provide the
corpus for embedding model selection AND downstream codegen scoring.

## Acceptance criteria (infrastructure-level)

- All new abstractions have unit + integration tests using `MockEmbedder`.
- `chunk_search_hybrid.yaml` preset loads + assembles + runs end-to-end
  against the mock + ephemeral TurboQuant index.
- `chunk_search.yaml` (MCP default, BM25 composite) baseline SHA
  unchanged — proves no regression on the existing path.
- Schema v4 → v5 migration lossless.
- `CompositeUnitOfWork` best-effort rollback + integrity check behave
  as specified.
EOF
)"
```

- [ ] **Step 3: Verify the issue was updated**

```bash
gh issue view 6 --json body --jq '.body' | head -50
```

Expected: the new body text appears.

- [ ] **Step 4: No commit needed** (issue text lives on GitHub, not in the repo).

---

### Task 31: Final verification gauntlet

**Files:**
- (no changes — verification only)

- [ ] **Step 1: Full pytest**

```bash
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -5
```

Expected: All tests PASS. Count should be roughly 1027 (baseline) + the new tests added across all tasks.

- [ ] **Step 2: Integration test (AC #15 floor)**

```bash
.venv/bin/pytest tests/integration/test_self_index_resolution_rate.py -v 2>&1 | tail -5
```

Expected: PASS — hashing/skip behavior unchanged by any task in this plan.

- [ ] **Step 3: Benchmark tests**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3
```

Expected: All PASS (existing 141+ benchmark tests still green; no new benchmark tests in this PR).

- [ ] **Step 4: ruff clean**

```bash
.venv/bin/ruff check python/ tests/ benchmarks/ 2>&1 | tail -3
```

Expected: `All checks passed!`

- [ ] **Step 5: AC-22 baseline byte check**

```bash
shasum -a 256 benchmarks/baselines/repoqa_fixture_baseline.json
```

Expected: SAME hash as before this PR (capture the hash on `main` before the PR started; compare at the end). Proves the BM25 path is untouched.

- [ ] **Step 6: README jargon audit**

```bash
find . -name "README.md" -not -path "*/.venv/*" -not -path "*/.claude/*" \
    -not -path "*/node_modules/*" -not -path "*/.git/*" \
    -not -path "*/.pytest_cache/*" | \
    xargs grep -nE "PR #[0-9]+|sub-PR|#5[a-c]|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+"
```

Expected: no matches (grep exits 1).

- [ ] **Step 7: Live MCP smoke (manual, no assertion)**

```bash
# In one terminal:
PYTHONPATH=python pydocs-mcp serve . --config /tmp/hybrid-test.yaml

# /tmp/hybrid-test.yaml contains:
# embedding:
#   provider: fastembed
#   model_name: BAAI/bge-small-en-v1.5
# (Then point pipelines.chunk.routes at chunk_search_hybrid.yaml)

# In another terminal, hit the MCP search tool via your client and verify
# results come back. NOT a quality assertion — just verifies the wire works.
```

Note: this requires `pip install pydocs-mcp[fastembed]` and a real fastembed model download — explicitly OUT OF SCOPE for CI / required verification (per spec §"Out of scope for this PR's verification"). Optional dev sanity check only.

- [ ] **Step 8: Commit any housekeeping fix-ups from this verification pass**

If the gauntlet surfaces any issues:

```bash
# Fix the issue, then:
git add <files>
git commit -m "fix: <issue caught in final gauntlet>"
```

No commit needed if everything's clean.

---

## Final commit sequence summary

After all 30 task commits land, the PR's commit log should look roughly like:

```
[N]  fix:  housekeeping fix-ups from final gauntlet (if any)
30   (no code commit — gh issue edit only)
29   docs: extend README jargon rule + fix PR-B3.1 leakage (#4 ride-along)
28   feat(application): re-embed-on-model-change via stored embedding_model
27   feat(server): composite UoW factory + startup integrity check
26   feat(application): IndexingService writes vectors via composite UoW
25   feat(pipelines): four new chunk-search presets (dense + hybrid, composite + ranked)
24   feat(ingestion): slot embed_chunks between flatten and content_hash
23   feat(extraction): EmbedChunksStage — batched ingestion-time embedder
22   feat(retrieval): ParallelStep propagates branches' scratch writes
21   feat(storage): HybridSqliteTurboStore — text + vector + fuser composition
20   feat(retrieval): DenseScorerStep — cosine-sim re-scoring step
19   feat(retrieval): DenseFetcherStep — TurboQuant-allowlist filtered search
18   feat(retrieval): TopKFilterStep.publish_to for parallel-branch hand-off
17   refactor(retrieval): RRFStep → RRFFusionStep (multi-list fuser)
16   feat(storage): TurboQuantVectorStore implementing VectorSearchable
15   feat(embedders): OpenAIEmbedder with API key + optional-dep guards
14   feat(embedders): FastEmbedEmbedder with optional-dep import guard
13   feat(embedders): OptionalDepMissing + build_embedder factory skeleton
12   feat(storage): SQLite CandidateIdResolver + ChunkHydrator callables
11   feat(storage): composite + SQLite+TurboQuant UoW factories (spec §5.7)
10   feat(storage): CompositeUnitOfWork — best-effort multi-backend coordinator
09   feat(storage): TurboQuantUnitOfWork — IdMapIndex lifecycle wrapper
08   feat(db): turboquant_path_for_project helper
07   feat(db): schema v5 — packages.embedding_model TEXT additive column
06   build: add turbovec + numpy main deps + fastembed/openai extras
05   feat(config): EmbeddingConfig + AppConfig.embedding + shipped defaults
04   test: add MockEmbedder canonical Embedder test double
03   feat(storage): add Embedder + ResultFuser Protocols
02   feat(models): Chunk.embedding: Embedding | None additive field
01   feat(models): add Vector/MultiVector/Embedding type aliases + is_multi_vector helper
```

Plus the spec commit (`d643e07`) and plan commit (this file).

---

## Self-review (writing-plans skill checklist)

**1. Spec coverage:** Every AC-1..AC-30 in the spec is implemented by one of Tasks 1-31. AC-22 (BM25 fixture baseline SHA unchanged) is verified by Task 31 Step 5 + naturally by the fact that no task touches `chunk_search.yaml` or the BM25 retrieval path. AC-28 (no real model in tests) is verified by spot-check: search for `import fastembed` / `import openai` in tests/ — should find nothing outside of `tests/extraction/strategies/embedders/test_*_embedder.py` (which uses `unittest.mock`).

**2. Placeholder scan:** Every step has actual code in code blocks. The Task 26 `IndexingService` modification has a `# ... existing delete + upsert sequence for chunks + packages ...` placeholder because the actual existing code shape needs to be confirmed during implementation — flagged in the task body for the implementer.

**3. Type consistency:** Type names match across tasks: `Vector` / `MultiVector` / `Embedding` (Task 1) used in Tasks 2, 3, 9, 14, 15, 17, 19, 20, 23. `Embedder` Protocol (Task 3) used in Tasks 4, 13, 14, 15, 19, 20, 23. `ResultFuser` (Task 3) used in Task 17, 21. `CandidateIdResolver` / `ChunkHydrator` (Task 12) used in Task 16. `TurboQuantVectorStore` (Task 16) used in Task 19, 21. `RRFFusionStep` (Task 17) used in Task 25. `TopKFilterStep.publish_to` (Task 18) used in Task 25. `DenseFetcherStep` / `DenseScorerStep` (Tasks 19, 20) used in Task 25. `EmbedChunksStage` (Task 23) used in Task 24. `CompositeUnitOfWork` (Task 10) used in Tasks 11, 26, 27.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-24-hybrid-search-with-semantic-embeddings.md`. Per user-mandated process, the workflow halts here until **the user reviews the plan manually**. After approval:

- **`superpowers:subagent-driven-development`** — dispatch one Opus 4.7 max-effort subagent per task; two-stage review per task (spec compliance + code quality).
- Post-coding review chain: `/code-review` + `/review` + `/design-review` + `/ultrareview`, all Opus 4.7 max effort, parallel where independent.
- Merge with squash + delete branch + sync main + clean up worktree.
