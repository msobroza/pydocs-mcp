# Retrieval Pipeline Refactor — sklearn-style RetrieverStep / RetrieverPipeline

**Status:** Spec, awaiting plan.

**Driver:** PR #27 shipped a real-data RepoQA baseline (0% recall on 100 needles). A targeted 5-needle investigation revealed two failure modes: (1) `TokenBudgetStage` collapses ranked retrieval output into a single composite chunk, breaking `recall@k` by construction — even when upstream BM25 finds the gold, only one composite is returned; (2) description ↔ code vocabulary gap — RepoQA queries are structured English essays ("1. **Purpose**: To retrieve..."), not symbol names, so BM25 can't bridge to code identifiers.

Before fixing either, the retrieval layer's two parallel hierarchies need to be unified, and individual stages need to be decomposed into single-responsibility steps so future B3 work (dense embeddings, Cohere reranking) can be composed cleanly. Today:

- `Retriever` Protocol with `async retrieve(query) → result`
- `PipelineStage` Protocol with `async run(state) → state`
- `*RetrievalStage` adapter classes wrapping retrievers as stages
- `PipelineChunkRetriever` — a Retriever that internally runs a Pipeline (reverse-Inception)
- `Bm25ChunkRetriever` does THREE things at once: fetches candidates from FTS5, scores by BM25, applies cutoff — none of which are individually addressable or swappable

Adding `DenseScorerStep` and `RerankStep` for B3 on top of this layout will compound an already confused abstraction. Clean it up first, then build on it.

---

## 1. Goal

One unified abstraction (`RetrieverStep` ABC) + one composable pipeline class (`RetrieverPipeline`) where every step is a named, addressable, swappable, **single-responsibility** step. Pipelines themselves are steps, so they compose recursively. Drop the `Retriever` Protocol entirely.

Decompose the monolithic `Bm25ChunkRetriever` into three composable steps:

- `ChunkFetcherStep` — produces candidates from SQLite/FTS5 for a query
- `BM25ScorerStep` — assigns BM25 relevance scores
- `TopKFilterStep` — cuts candidate list to top-K by score

This decomposition is the load-bearing change. It lets B3.1 add a `DenseScorerStep` alongside `BM25ScorerStep`, then RRF-fuse them, without rewriting the BM25 path. It also gives benchmark configs the ability to drop `TokenBudgetStep` for ranked output without coupling that decision to scoring.

**Naming convention** — to differentiate from the extraction layer's `IngestionStage` (Protocol at [extraction/pipeline/ingestion.py:75](python/pydocs_mcp/extraction/pipeline/ingestion.py:75)):

- Retrieval side (this PR): `RetrieverStep` + `RetrieverPipeline` + `RetrieverState`
- Ingestion side (this PR, **unchanged**): `IngestionStage` Protocol stays as-is. A future PR can mirror this refactor on the ingestion side and rename to `IngestionStep` for symmetry — out of scope here.

This is **internal-API only** — no MCP tool signatures change; no observable behavior change at the system boundary. Pre-existing tests should pass identically after the refactor, modulo updated import paths.

---

## 2. Architecture

### 2.1 Core abstraction (`RetrieverStep` ABC)

```python
# python/pydocs_mcp/retrieval/pipeline/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline.state import RetrieverState


@dataclass(frozen=True, slots=True)
class RetrieverStep(ABC):
    """A single retrieval-pipeline step. Pure: take a state, return a NEW state.

    Subclasses MUST set ``name: str`` (used for addressing + debug logs)
    and implement ``async def run(self, state) -> state``.

    Naming: ``RetrieverStep`` (not ``Stage``) to differentiate from
    ``extraction/pipeline/ingestion.py::IngestionStage``. Different
    pipelines, different abstractions, different state types.
    """
    name: str

    @abstractmethod
    async def run(self, state: RetrieverState) -> RetrieverState: ...
```

**Why `@dataclass(frozen=True) + ABC`** — user chose "cleaner immutability". `frozen=True` means steps are hashable, comparable, and impossible to mutate at runtime (state mutation must produce a new state, not patch the step). Subclasses extend via dataclass fields, not via `__init__` overrides.

**Why ABC over Protocol** — addresses the user's complaint about SOLID + abstract classes. Concrete benefits:
- Explicit inheritance (`class ChunkFetcherStep(RetrieverStep)`) is greppable.
- `isinstance(step, RetrieverStep)` is nominal, not structural.
- Default methods possible (e.g., `RetrieverStep.describe()` for debug — out of scope here, but the door is open).
- Method signatures enforced at class definition time, not at first call.

### 2.2 RetrieverPipeline — named tuple of steps

```python
@dataclass(frozen=True, slots=True)
class RetrieverPipeline(RetrieverStep):
    """A RetrieverPipeline IS a RetrieverStep — they compose recursively.

    Construction (sklearn-shaped):

        chunk_pipeline = RetrieverPipeline(
            name="chunk_search",
            steps=(
                ("fetch", ChunkFetcherStep(name="fetch", limit=200)),
                ("score", BM25ScorerStep(name="score")),
                ("topk", TopKFilterStep(name="topk", k=50)),
                ("budget", TokenBudgetStep(name="budget", max_tokens=2000)),
            ),
        )

    Addressing:

        chunk_pipeline["fetch"]  # → ChunkFetcherStep instance
        chunk_pipeline.step_names  # → ("fetch", "score", "topk", "budget")
    """
    steps: tuple[tuple[str, RetrieverStep], ...]

    def __post_init__(self) -> None:
        names = [n for n, _ in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate step names in {self.name!r}: {names}")
        if not names:
            raise ValueError(f"pipeline {self.name!r} has no steps")

    def __getitem__(self, name: str) -> RetrieverStep:
        for n, step in self.steps:
            if n == name:
                return step
        raise KeyError(f"pipeline {self.name!r} has no step {name!r}")

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(n for n, _ in self.steps)

    async def run(self, state: RetrieverState) -> RetrieverState:
        for _, step in self.steps:
            state = await step.run(state)
        return state
```

**Why `tuple[tuple[str, RetrieverStep], ...]`** — matches sklearn's `Pipeline(steps=[('name', step), ...])` API. Tuple (not list/dict) preserves order AND immutability AND hashability. `__getitem__` makes `pipeline["fetch"]` work like sklearn's `pipeline.named_steps`.

**Why "Pipeline IS a Step"** — sub-pipelines, branching, conditional dispatch all become trivial. `RouteStep` holds a tuple of `(predicate, RetrieverPipeline)` pairs; `SubPipelineStage` becomes unnecessary (just nest a `RetrieverPipeline` directly as a step). No special-case classes.

### 2.3 RetrieverState — typed dataclass with scratch escape hatch

The current shape is minimal:

```python
@dataclass(frozen=True, slots=True)
class PipelineState:
    query: SearchQuery
    result: PipelineResultItem | None = None
    duration_ms: float = 0.0
```

The new shape adds explicit fields so each step has clear input/output contracts:

```python
# python/pydocs_mcp/retrieval/pipeline/state.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.models import (
        ChunkList, ModuleMemberList, PipelineResultItem, SearchQuery,
    )


@dataclass(frozen=True, slots=True)
class RetrieverState:
    """Immutable state threaded through a RetrieverPipeline's steps.

    Steps are pure: each step takes a state and returns a NEW state
    (typically via ``dataclasses.replace``), never mutates in place.

    Step input/output contracts:
    - Fetcher steps (``ChunkFetcherStep``, ``MemberFetcherStep``):
      read ``query``, write ``candidates``.
    - Scorer steps (``BM25ScorerStep``, future ``DenseScorerStep``):
      read+write ``candidates`` (assign / update ``relevance`` per item).
    - Filter steps (``TopKFilterStep``, ``MetadataPostFilterStep``):
      read+write ``candidates`` (trim / reorder).
    - Renderer steps (``TokenBudgetStep``):
      read ``candidates``, write ``result``.

    The ``result`` field is the canonical output read by callers
    (DocsSearch, ApiSearch). Intermediate ``candidates`` is scratch
    space for the steps that produce / consume them.
    """
    query: "SearchQuery"
    # Intermediate scratch space — fetcher writes, scorer updates,
    # filter trims, renderer consumes. ``ChunkList`` for chunk pipelines,
    # ``ModuleMemberList`` for member pipelines.
    candidates: "ChunkList | ModuleMemberList | None" = None
    # Final output read by callers. Set by the LAST step in a pipeline
    # (typically a renderer / budget step). Distinct from ``candidates``
    # because not every pipeline produces both (e.g., a debug pipeline
    # might leave ``candidates`` set and ``result`` None).
    result: "PipelineResultItem | None" = None
    # Observability.
    duration_ms: float = 0.0
    # Free-form per-step scratch for cross-step coordination that doesn't
    # belong in a typed field (RRF intermediate scores, debug breadcrumbs).
    # Per-step convention: keys are ``<step_name>.<field>`` so collisions
    # are detectable.
    scratch: dict[str, object] = field(default_factory=dict)
```

**`scratch: dict[str, object]`** is the escape hatch for step-specific metadata that doesn't merit a typed field. Frozen-dataclass + dict-field works because `frozen=True` only forbids reassignment of the field itself, not mutation of the dict contents (intentional; the dict is mutable scratch).

### 2.4 Step inventory after refactor

| Step | Responsibility | Reads | Writes | Decomposed from |
|---|---|---|---|---|
| `ChunkFetcherStep` | Candidate generation — query SQLite FTS5 (`MATCH ? ORDER BY bm25() LIMIT N`). Returns N chunks with raw FTS5 ranks captured. | `query.terms` | `candidates` | was `Bm25ChunkRetriever` (whole) |
| `BM25ScorerStep` | Score normalization — extracts FTS5's BM25 rank into each chunk's `relevance` field. Today: assigns FTS5 ranks directly. Future: optional pure-Python BM25 over arbitrary candidates. | `candidates` | `candidates` (with relevance set) | was inline in `Bm25ChunkRetriever` |
| `TopKFilterStep` | Cutoff — keeps top K candidates by `relevance` descending. If relevance is unset (no scorer ran), falls back to source order. Works for chunks and members uniformly. | `candidates`, `params.k` | `candidates` | NEW |
| `MemberFetcherStep` | Candidate generation — LIKE query against `module_members`. Returns matching members in SQL row order (no BM25 — LIKE doesn't score). | `query.terms` | `candidates` | was `LikeModuleMemberRetriever` |
| `TokenBudgetStep` | Composite renderer — collapses ranked output into a single composite chunk for MCP-server consumption. Sets `result`, leaves `candidates` alone. | `candidates` | `result` | renamed from `TokenBudgetStage` |
| `RRFStep` | Reciprocal-rank fusion — fuses multiple ranked candidate lists into one (used by B3.2 hybrid scoring). | `scratch["<step>.candidates"]`, `candidates` | `candidates` | renamed from `ReciprocalRankFusionStage` |
| `MetadataPostFilterStep` | Filter candidates by package / scope / kind metadata. | `candidates`, params | `candidates` | renamed from `MetadataPostFilterStage` |
| `RouteStep` | Predicate-routed dispatch — picks one of multiple `RetrieverPipeline`s. | `state` (full) | `state` (after sub-pipeline) | renamed from `RouteStage` |
| `ConditionalStep` | Run inner step only if predicate matches. | `state` | `state` | renamed from `ConditionalStage` |
| `ParallelStep` | Run multiple sub-pipelines in parallel, merge candidates. | `state` | `state` | renamed from `ParallelRetrievalStage` |
| `LimitStep` | Simple max-count cap (no scoring). | `candidates`, params | `candidates` | renamed from `LimitStage` |

**Steps deleted:**
- `Bm25ChunkRetriever` — split into `ChunkFetcherStep` + `BM25ScorerStep` + `TopKFilterStep`.
- `LikeModuleMemberRetriever` — folded into `MemberFetcherStep`.
- `PipelineChunkRetriever`, `PipelineMemberRetriever` (reverse-Inception adapters) — services call pipelines directly.
- `ChunkRetrievalStage`, `ModuleMemberRetrievalStage` (adapter stages) — no longer needed.
- `SubPipelineStage` — `RetrieverPipeline` IS a `RetrieverStep`; nest directly as a step.

---

## 3. Directory layout

### Before

```
retrieval/
├── __init__.py
├── config.py                      # 287 LOC — AppConfig + pydantic models
├── factories.py
├── formatters.py
├── pipeline.py                    # CodeRetrieverPipeline + PipelineState + PerCallConnectionProvider
├── protocols.py                   # Retriever, ChunkRetriever, ModuleMemberRetriever, PipelineStage, ConnectionProvider, ResultFormatter
├── route_predicates.py
├── serialization.py
├── retrievers/                    # ← parallel hierarchy
│   ├── __init__.py
│   ├── _shared.py
│   ├── base_retriever.py
│   ├── bm25_chunk.py              # MONOLITHIC — fetch + score + cutoff
│   ├── like_member.py
│   ├── pipeline_chunk.py          # reverse-Inception
│   └── pipeline_member.py         # reverse-Inception
└── stages/                        # ← parallel hierarchy
    ├── __init__.py
    ├── base_stage.py
    ├── chunk_retrieval.py         # adapter
    ├── conditional.py
    ├── limit.py
    ├── metadata_post_filter.py
    ├── module_member_retrieval.py # adapter
    ├── parallel_retrieval.py
    ├── reciprocal_rank_fusion.py
    ├── route.py
    ├── sub_pipeline.py
    └── token_budget.py
```

### After

```
retrieval/
├── __init__.py
├── config.py                      # unchanged
├── factories.py                   # updated — builds RetrieverPipelines with named steps
├── formatters.py                  # unchanged
├── route_predicates.py            # unchanged
├── serialization.py               # updated — YAML loader reads `name:` per step, rejects old `stages:` key
├── protocols.py                   # SLIMMED — only ConnectionProvider + ResultFormatter
├── pipeline/                      # ← NEW directory
│   ├── __init__.py                # re-exports RetrieverStep, RetrieverPipeline, RetrieverState
│   ├── base.py                    # RetrieverStep ABC + RetrieverPipeline class
│   └── state.py                   # RetrieverState dataclass
└── steps/                         # ← renamed from stages/, retrievers/ folded in here
    ├── __init__.py                # step_registry registration (one place)
    ├── chunk_fetcher.py           # ChunkFetcherStep (NEW — extracted from Bm25ChunkRetriever)
    ├── bm25_scorer.py             # BM25ScorerStep (NEW — extracted from Bm25ChunkRetriever)
    ├── member_fetcher.py          # MemberFetcherStep (was retrievers/like_member.py)
    ├── top_k_filter.py            # TopKFilterStep (NEW — uniform cutoff)
    ├── token_budget.py            # TokenBudgetStep (renamed Stage → Step)
    ├── rrf.py                     # RRFStep (renamed reciprocal_rank_fusion.py → rrf.py)
    ├── route.py                   # RouteStep (uses RetrieverPipeline directly now)
    ├── conditional.py             # ConditionalStep
    ├── parallel.py                # ParallelStep (renamed parallel_retrieval.py → parallel.py)
    ├── metadata_post_filter.py    # MetadataPostFilterStep
    └── limit.py                   # LimitStep
```

### Files deleted (net ~600 LOC removed)

- `retrievers/` entire directory (6 files, ~378 LOC). Functionality folds into `steps/`.
- `retrieval/protocols.py::Retriever`, `ChunkRetriever`, `ModuleMemberRetriever`, `PipelineStage` Protocols — replaced by `RetrieverStep` ABC.
- `stages/base_stage.py` — was just a re-export.
- `stages/chunk_retrieval.py::ChunkRetrievalStage` — adapter no longer needed.
- `stages/module_member_retrieval.py::ModuleMemberRetrievalStage` — same.
- `stages/sub_pipeline.py::SubPipelineStage` — replaced by direct `RetrieverPipeline` composition.
- `retrievers/pipeline_chunk.py::PipelineChunkRetriever`, `retrievers/pipeline_member.py::PipelineMemberRetriever` — services call pipelines directly.

### Files renamed (for verb-action clarity + Step suffix)

- Directory: `stages/` → `steps/`
- `retrievers/bm25_chunk.py` → split into `steps/chunk_fetcher.py` + `steps/bm25_scorer.py` (decomposition, not a simple rename)
- `retrievers/like_member.py` → `steps/member_fetcher.py`
- `stages/reciprocal_rank_fusion.py` → `steps/rrf.py`
- `stages/parallel_retrieval.py` → `steps/parallel.py`
- All `*Stage` class names → `*Step` (e.g., `TokenBudgetStage` → `TokenBudgetStep`, `RouteStage` → `RouteStep`)

---

## 4. YAML schema (no backward compat — hard flip)

User chose "no backward compatibility". The loader does NOT accept the old `stages:` shape; it raises a clear error.

### Before

```yaml
# pipelines/chunk_search.yaml (current shape)
name: chunk_search
stages:
  - type: chunk_retrieval
    params:
      retriever:
        type: bm25_chunk
        params: { limit: 50 }
  - type: token_budget
    params: { max_tokens: 2000 }
```

### After

```yaml
# pipelines/chunk_search.yaml (new shape — decomposed)
name: chunk_search
steps:
  - name: fetch
    type: chunk_fetcher
    params:
      limit: 200          # fetch a broad candidate set
  - name: score
    type: bm25_scorer
    params: {}            # uses FTS5's bm25() output set by fetch
  - name: topk
    type: top_k_filter
    params:
      k: 50               # cutoff after scoring
  - name: budget
    type: token_budget
    params:
      max_tokens: 2000
```

Key changes:
- `stages:` → `steps:` (matches the `RetrieverPipeline.steps` field name).
- Every step has a `name:` (no more positional addressing).
- The single `chunk_retrieval`-wrapping-`bm25_chunk` step decomposes into THREE addressable steps (`fetch` / `score` / `topk`). Now `chunk_search_ranked.yaml` (PR-A) is just this file with the `budget` step removed.

### Member-search pipeline

```yaml
# pipelines/member_search.yaml (new shape)
name: member_search
steps:
  - name: fetch
    type: member_fetcher
    params:
      limit: 200
  - name: topk
    type: top_k_filter
    params:
      k: 50
  - name: budget
    type: token_budget
    params:
      max_tokens: 2000
```

Member retrieval has no scorer step today (LIKE doesn't produce a meaningful score). A future ranker can be inserted between `fetch` and `topk` without touching the others.

### Loader error contract

If a YAML uses the old `stages:` key, the loader raises:

```
PipelineLoadError: 'stages:' key is no longer accepted (retrieval-pipeline-refactor).
Use 'steps:' with a 'name:' per step. See pipelines/chunk_search.yaml
for the canonical shape.
```

No silent fallback. Users see one clear error.

### Files to update

- `python/pydocs_mcp/pipelines/chunk_search.yaml`
- `python/pydocs_mcp/pipelines/member_search.yaml`
- `python/pydocs_mcp/pipelines/ingestion.yaml` — **out of scope**; that's an ingestion pipeline (separate abstraction), and the user's decision was to leave ingestion-side renaming for a future PR. The retrieval YAML loader only handles `pipelines/chunk_search.yaml` + `pipelines/member_search.yaml`.
- `python/pydocs_mcp/defaults/default_config.yaml` — only if it inlines retrieval-pipeline definitions.
- `benchmarks/configs/baseline.yaml` — update if it overrides chunk/member pipelines.
- `benchmarks/configs/strict_suffix_off.yaml` — overrides resolver only, no change needed.

---

## 5. Service migration (DocsSearch, ApiSearch)

### Before

```python
@dataclass
class DocsSearch:
    chunk_retriever: ChunkRetriever  # = PipelineChunkRetriever (reverse-Inception)

    async def search(self, query: SearchQuery) -> ChunkList:
        return await self.chunk_retriever.retrieve(query)
```

### After

```python
@dataclass
class DocsSearch:
    chunk_pipeline: RetrieverPipeline

    async def search(self, query: SearchQuery) -> ChunkList:
        state = await self.chunk_pipeline.run(RetrieverState(query=query))
        # The chunk_search pipeline ends with TokenBudgetStep which sets
        # state.result. Type-narrow defensively in case a future config
        # routes through a different pipeline shape.
        if isinstance(state.result, ChunkList):
            return state.result
        if isinstance(state.candidates, ChunkList):
            return state.candidates  # ranked output, no budget step
        return ChunkList(items=())
```

Same data flow, one less abstraction layer. `ApiSearch` mirrors `DocsSearch`. `IndexingService` doesn't use the retrieval pipeline — unchanged.

Composition roots (`server.py`, `__main__.py`, `storage/factories.py`) build the `RetrieverPipeline` once at startup from `AppConfig` and inject it. No change to the `uow_factory` pattern from CLAUDE.md §"Creating new application services".

---

## 6. Acceptance criteria

| # | Criterion |
|---|---|
| AC1 | `python/pydocs_mcp/retrieval/pipeline/base.py` defines `RetrieverStep(ABC)` and `RetrieverPipeline(RetrieverStep)` per §2.1 and §2.2. |
| AC2 | `RetrieverPipeline.__post_init__` rejects duplicate step names and zero steps with clear `ValueError`. |
| AC3 | `pipeline["name"]` raises `KeyError(f"pipeline {name!r} has no step {key!r}")` on miss. |
| AC4 | `RetrieverState` (§2.3) has fields: `query`, `candidates`, `result`, `duration_ms`, `scratch`. |
| AC5 | `retrieval/protocols.py` no longer exports `Retriever`, `ChunkRetriever`, `ModuleMemberRetriever`, `PipelineStage`. |
| AC6 | `retrieval/retrievers/` directory is deleted. |
| AC7 | `retrieval/stages/` directory is renamed to `retrieval/steps/`. Every step in it subclasses `RetrieverStep`. |
| AC8 | `Bm25ChunkRetriever` is split into three steps: `ChunkFetcherStep`, `BM25ScorerStep`, `TopKFilterStep`. Each has one responsibility per §2.4. |
| AC9 | `LikeModuleMemberRetriever` is renamed to `MemberFetcherStep`. |
| AC10 | `TopKFilterStep` works uniformly for `ChunkList` and `ModuleMemberList` candidates. Sorts by `relevance` desc if any candidate has it set; falls back to source order otherwise. |
| AC11 | `SubPipelineStage` is deleted. `RouteStep` holds `tuple[tuple[Predicate, RetrieverPipeline], ...]`. Nested-Pipeline composition replaces SubPipelineStage. |
| AC12 | YAML loader accepts `steps:` with `name:` per step; rejects old `stages:` with the §4 error message. |
| AC13 | All shipped retrieval YAML blueprints (`pipelines/chunk_search.yaml`, `pipelines/member_search.yaml`) use the new decomposed shape. |
| AC14 | `DocsSearch` / `ApiSearch` take `chunk_pipeline: RetrieverPipeline` / `api_pipeline: RetrieverPipeline`, not retrievers. |
| AC15 | `pytest -q` shows no regressions vs the pre-refactor baseline (same passing count modulo intentional test additions). |
| AC16 | `ruff check python/ benchmarks/ tests/` is clean. |
| AC17 | RepoQA real baseline at `benchmarks/baselines/repoqa_snf.json` is unchanged (no observable retrieval behavior change). |
| AC18 | A new test pins `RetrieverPipeline.__getitem__` addressing + Pipeline-in-Pipeline composition. |
| AC19 | A new test pins the "old `stages:` YAML rejection" error path. |
| AC20 | A new test pins the chunk pipeline's decomposed step contract: `fetch` populates `candidates` with raw FTS5 output, `score` sets `relevance` on each, `topk` trims to k. |
| AC21 | CLAUDE.md architecture section updated to reflect `RetrieverStep` / `RetrieverPipeline` (and notes that `IngestionStage` is parallel ingestion-side abstraction left for future symmetry). |

---

## 7. Commit sequence

Bisect-friendly. Each commit passes `pytest -q` on its own.

| # | Commit | Notes |
|---|---|---|
| 1 | `feat(retrieval): add RetrieverStep ABC + RetrieverPipeline + RetrieverState` | New files only (`retrieval/pipeline/base.py`, `state.py`). Nothing wired up. Old `CodeRetrieverPipeline` still in use. |
| 2 | `refactor(retrieval): rename stages/ → steps/, *Stage classes → *Step (sed-style)` | Pure rename. No functional change. All call sites updated. |
| 3 | `refactor(retrieval): non-retrieval steps subclass RetrieverStep` | `TokenBudgetStep`, `RRFStep`, `MetadataPostFilterStep`, `ConditionalStep`, `LimitStep`, `ParallelStep` flip from PipelineStage Protocol to RetrieverStep ABC. |
| 4 | `feat(retrieval): split Bm25ChunkRetriever into ChunkFetcherStep + BM25ScorerStep + TopKFilterStep` | Decomposition. The new steps + `ChunkRetrievalStage` adapter wired to use them while the old retriever path stays alive for one commit. |
| 5 | `refactor(retrieval): LikeModuleMemberRetriever → MemberFetcherStep` | Same pattern as #4 but no scorer (LIKE doesn't score). |
| 6 | `refactor(retrieval): RouteStep + nested RetrieverPipelines replace SubPipelineStage` | Pipeline composition. `SubPipelineStage` deleted. |
| 7 | `refactor(retrieval): services consume RetrieverPipeline directly, drop retriever adapters` | `DocsSearch` / `ApiSearch` take RetrieverPipeline. `PipelineChunkRetriever`, `PipelineMemberRetriever`, `ChunkRetrievalStage`, `ModuleMemberRetrievalStage` deleted. |
| 8 | `refactor(retrieval): YAML loader reads steps: with name:, rejects stages:` | Loader + all shipped `pipelines/*.yaml` + benchmark configs migrated together. |
| 9 | `chore(retrieval): delete retrievers/ + slim protocols.py` | Final cleanup. `Retriever` / `ChunkRetriever` / `ModuleMemberRetriever` / `PipelineStage` Protocols gone. |
| 10 | `docs: update CLAUDE.md architecture for RetrieverStep / RetrieverPipeline` | Reflects new layout. Notes the ingestion-side `IngestionStage` parallel naming for future symmetry. |

Total: ~10 commits, ~4.5–5 days of focused work.

---

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Breaking changes in `retrieval/` ripple to every test that constructs a pipeline | Tests use shared fakes (`tests/_fakes.py`). Update fakes once; downstream tests inherit the new shape. |
| Existing YAML blueprints in `pipelines/*.yaml` need lockstep migration | Done in commit 8 alongside the loader change — single bisect point if anything regresses. |
| `dataclass(frozen=True) + ABC` quirks in Python 3.11 (e.g., subclass `__init__` field ordering) | Field-ordering rule: positional fields first, then keyword-only after `*`. Each subclass adds fields with defaults so ordering stays consistent. Verified at `RetrieverStep` ABC definition. |
| `RetrieverState.scratch: dict[str, object]` is mutable inside a frozen dataclass | Documented convention; scratch is intentional escape hatch. Frozen-ness of state still holds for the canonical fields. |
| User-authored YAML overlays (outside the repo) break on the schema flip | No backward compat by user choice. The error message (§4) tells them exactly what to change. Documented in PR description + CHANGELOG entry. |
| Decomposing `Bm25ChunkRetriever` into three steps adds 2 extra Python function calls per query (fetch, score, topk vs the old monolith) | Negligible — each step is microseconds; the SQL query dominates. Benchmark verification: `search_seconds` p50 should stay within ±5% of pre-refactor (covered by AC17). |
| `TopKFilterStep` falling back to source order when no scorer ran is silent semantics | Documented in §2.4. A future debug step or assertion could pin it; out of scope here. |
| The refactor delays the real product question (RepoQA 0% retrieval) by ~1 week | Acceptable per user decision — clean architecture first, then PR-A (ranked pipeline) + PR-B3 dense embeddings on top. Each subsequent PR becomes a smaller, focused change. |

---

## 9. Out of scope (separate specs to follow)

Each of these gets its own brainstorm → spec → plan cycle after PR-0 lands:

- **PR-A** (small, ~1-2 days) — `chunk_search_ranked.yaml` preset that drops `TokenBudgetStep` for benchmark consumers. Same fetch + score + topk steps, no budget. Re-measure RepoQA real baseline with ranked output. Expected: `recall@k > 0` on at least the symbol-style needles.
- **PR-B3.1** (medium, ~3-5 days) — `EmbeddingProvider` Protocol + registry (`openai_api`, `fastembed_local`). New `DenseScorerStep` runs alongside `BM25ScorerStep` (both assign scores to candidates); RRF step fuses. CI defaults to FastEmbed (offline). `CodeRankEmbed` as default FastEmbed model.
- **PR-B3.2** (medium, ~3-5 days) — `RerankerProvider` Protocol + registry (`cohere_rerank`). New `RerankerStep` operating on top-N candidates after RRF. API key via env var (`COHERE_API_KEY`); CI either has the secret or skips rerank.
- **Future: ingestion-side rename** — Mirror the `Step` naming on the ingestion side (`IngestionStage` Protocol → `IngestionStep` ABC) for symmetry. Not bundled here because (a) the ingestion pipeline shape is different (different state, different stages), (b) the user's directive was to focus on retrieval first, (c) bundling would inflate PR-0 by ~30%.

---

## 10. Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/pipeline-refactor/`
Branch: `feature/pipeline-refactor` off `main` at `a8de6de`.
