# Retrieval Pipeline Refactor — sklearn-style Stage / Pipeline

**Status:** Spec, awaiting plan.

**Driver:** PR #27 shipped a real-data RepoQA baseline (0% recall on 100 needles). A targeted 5-needle investigation revealed two failure modes: (1) `TokenBudgetStage` collapses ranked retrieval output into a single composite chunk, breaking `recall@k` by construction — even when upstream BM25 finds the gold, only one composite is returned; (2) description ↔ code vocabulary gap — RepoQA queries are structured English essays ("1. **Purpose**: To retrieve..."), not symbol names, so BM25 can't bridge to code identifiers.

Before fixing either, the retrieval layer's two parallel hierarchies need to be unified. Today:
- `Retriever` Protocol with `async retrieve(query) → result`
- `PipelineStage` Protocol with `async run(state) → state`
- `*RetrievalStage` adapter classes wrapping retrievers as stages
- `PipelineChunkRetriever` — a Retriever that internally runs a Pipeline (reverse-Inception)

Adding `DenseRetrievalStage` and `RerankStage` for B3 on top of this layout will compound an already confused abstraction. Clean it up first, then build on it.

---

## 1. Goal

One unified abstraction (`Stage` ABC) + one composable pipeline class (`Pipeline`) where every step is a named, addressable, swappable Stage. Pipelines themselves are Stages, so they compose recursively. Drop the `Retriever` Protocol entirely; the `*RetrievalStage` adapter classes go with it. YAML pipeline blueprints gain a `name:` per step.

This is **internal-API only** — no MCP tool signatures change; no observable behavior change. Pre-existing tests should pass identically after the refactor, modulo updated import paths.

---

## 2. Architecture

### 2.1 Core abstraction (`Stage` ABC)

```python
# python/pydocs_mcp/retrieval/pipeline/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline.state import PipelineState


@dataclass(frozen=True, slots=True)
class Stage(ABC):
    """A single pipeline step. Pure: take a state, return a NEW state.

    Subclasses MUST set ``name: str`` (used for addressing + debug logs)
    and implement ``async def run(self, state) -> state``.
    """
    name: str

    @abstractmethod
    async def run(self, state: PipelineState) -> PipelineState: ...
```

**Why `@dataclass(frozen=True) + ABC`** — the user picked "cleaner immutability". `frozen=True` means stages are hashable, comparable, and impossible to mutate at runtime (state mutation must produce a new state, not patch the stage). Subclasses extend via dataclass fields, not via `__init__` overrides. The `dataclass + ABC` combo is well-supported in Python 3.11+.

**Why ABC over Protocol** — addresses the user's complaint about SOLID + abstract classes. Concrete benefits over the current Protocol approach:
- Explicit inheritance (`class Bm25ChunkSearch(Stage)`) is greppable.
- `isinstance(stage, Stage)` is nominal, not structural.
- Default methods possible (e.g., `Stage.describe()` for debug rendering — out of scope here, but the door is open).
- Method signatures are enforced at class definition time, not at first call.

### 2.2 Pipeline = a named tuple of Stages

```python
@dataclass(frozen=True, slots=True)
class Pipeline(Stage):
    """A Pipeline IS a Stage — they compose recursively.

    Construction (sklearn-shaped):

        chunk_pipeline = Pipeline(
            name="chunk_search",
            steps=(
                ("bm25", Bm25ChunkSearchStage(name="bm25", limit=50)),
                ("rrf", RRFStage(name="rrf")),
                ("token_budget", TokenBudgetStage(name="token_budget", max_tokens=2000)),
            ),
        )

    Addressing:

        chunk_pipeline["bm25"]  # → Bm25ChunkSearchStage instance
    """
    steps: tuple[tuple[str, Stage], ...]

    def __post_init__(self) -> None:
        names = [n for n, _ in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(f"duplicate step names in {self.name!r}: {names}")
        if not names:
            raise ValueError(f"pipeline {self.name!r} has no steps")

    def __getitem__(self, name: str) -> Stage:
        for n, stage in self.steps:
            if n == name:
                return stage
        raise KeyError(f"pipeline {self.name!r} has no step {name!r}")

    @property
    def step_names(self) -> tuple[str, ...]:
        return tuple(n for n, _ in self.steps)

    async def run(self, state: PipelineState) -> PipelineState:
        for _, stage in self.steps:
            state = await stage.run(state)
        return state
```

**Why `tuple[tuple[str, Stage], ...]`** — matches sklearn's `Pipeline(steps=[('name', step), ...])` API. Tuple (not list/dict) preserves order AND immutability AND hashability. `__getitem__` makes `pipeline["bm25"]` work like sklearn's `pipeline.named_steps`.

**Why "Pipeline IS a Stage"** — sub-pipelines, branching, conditional dispatch all become trivial. `RouteStage` holds a tuple of `(predicate, Pipeline)` pairs; `SubPipelineStage` becomes unnecessary (just nest a Pipeline directly as a step). No special-case classes.

### 2.3 PipelineState — typed dataclass, expanded

The current shape:

```python
@dataclass(frozen=True, slots=True)
class PipelineState:
    query: SearchQuery
    result: PipelineResultItem | None = None
    duration_ms: float = 0.0
```

The new shape adds explicit fields for intermediate-stage outputs so that, e.g., BM25 produces `candidates`, RRF reads `candidates` and produces `fused_candidates`, dense retrieval (future B3.1) produces `dense_candidates`, etc. Without explicit fields, every stage would need to round-trip via the `result` field with downcasts.

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
class PipelineState:
    """Immutable state threaded through a Pipeline's stages.

    Stages are pure: each stage takes a state and returns a NEW state
    (typically via ``dataclasses.replace``), never mutates in place.

    The ``result`` field is the canonical output read by callers
    (DocsSearch, ApiSearch). Intermediate fields (``candidates``,
    ``dense_candidates`` once B3.1 lands) are scratch space for stages
    that produce/consume them.
    """
    query: "SearchQuery"
    # Intermediate scratch space — stages produce / consume these.
    # ``ChunkList`` for chunk pipelines, ``ModuleMemberList`` for member
    # pipelines. Type-narrowed by route.
    candidates: "ChunkList | ModuleMemberList | None" = None
    # Final output read by callers. Set by the LAST stage in a pipeline
    # (typically a renderer / budget stage). Distinct from ``candidates``
    # because not every pipeline produces both (e.g., a debug pipeline
    # might leave ``candidates`` set and ``result`` None).
    result: "PipelineResultItem | None" = None
    # Observability — populated by stages that care to measure.
    duration_ms: float = 0.0
    # Free-form scratch for stages that need to pass non-canonical data
    # downstream (RRF fusion intermediate scores, debug breadcrumbs).
    # Per-stage convention: keys are ``<stage_name>.<field>``.
    scratch: dict[str, object] = field(default_factory=dict)
```

**`scratch: dict[str, object]`** is the escape hatch for stage-specific metadata that doesn't belong in a typed field (e.g., RRF needs to remember per-candidate fused scores; a debug stage might log intermediate ranks). Convention: keys are `<stage_name>.<field>` so collisions are detectable. Frozen-dataclass + dict-field works because `frozen=True` only forbids reassignment of the field itself, not mutation of the dict (intentional; the dict is mutable scratch).

---

## 3. Directory layout

### Before

```
retrieval/
├── __init__.py
├── config.py                      # 287 LOC — AppConfig + pydantic models
├── factories.py                   # builds pipeline+context from config
├── formatters.py                  # markdown formatters for Chunk/Member
├── pipeline.py                    # CodeRetrieverPipeline + PipelineState + PerCallConnectionProvider
├── protocols.py                   # Retriever, ChunkRetriever, ModuleMemberRetriever, PipelineStage, ConnectionProvider, ResultFormatter
├── route_predicates.py
├── serialization.py               # BuildContext, registries (stage / retriever / formatter), YAML loading
├── retrievers/                    # ← parallel hierarchy
│   ├── __init__.py
│   ├── _shared.py
│   ├── base_retriever.py          # re-exports Retriever protocols (16 LOC, basically empty)
│   ├── bm25_chunk.py              # Bm25ChunkRetriever
│   ├── like_member.py             # LikeModuleMemberRetriever
│   ├── pipeline_chunk.py          # PipelineChunkRetriever (a Retriever that runs a Pipeline (!))
│   └── pipeline_member.py         # PipelineMemberRetriever (same)
└── stages/                        # ← parallel hierarchy
    ├── __init__.py
    ├── base_stage.py              # re-exports PipelineStage Protocol (12 LOC)
    ├── chunk_retrieval.py         # ChunkRetrievalStage (adapter: wraps a ChunkRetriever)
    ├── conditional.py             # ConditionalStage
    ├── limit.py                   # LimitStage
    ├── metadata_post_filter.py    # MetadataPostFilterStage
    ├── module_member_retrieval.py # ModuleMemberRetrievalStage (adapter)
    ├── parallel_retrieval.py      # ParallelRetrievalStage
    ├── reciprocal_rank_fusion.py  # ReciprocalRankFusionStage
    ├── route.py                   # RouteStage (predicate-routed pipelines)
    ├── sub_pipeline.py            # SubPipelineStage (runs a nested pipeline)
    └── token_budget.py            # TokenBudgetStage
```

### After

```
retrieval/
├── __init__.py
├── config.py                      # unchanged
├── factories.py                   # updated — builds Pipelines with named steps
├── formatters.py                  # unchanged
├── route_predicates.py            # unchanged
├── serialization.py               # updated — YAML loader reads `name:` per step
├── protocols.py                   # SLIMMED — only ConnectionProvider + ResultFormatter
├── pipeline/                      # ← NEW directory
│   ├── __init__.py                # re-exports Stage, Pipeline, PipelineState
│   ├── base.py                    # Stage ABC + Pipeline class
│   └── state.py                   # PipelineState (expanded — see §2.3)
└── stages/                        # retrievers folded in here; retrievers/ DELETED
    ├── __init__.py                # stage_registry registration (one place)
    ├── bm25_chunk_search.py       # ← was retrievers/bm25_chunk.py (renamed for verb-action clarity)
    ├── like_member_search.py      # ← was retrievers/like_member.py
    ├── rrf.py                     # ← was stages/reciprocal_rank_fusion.py (renamed shorter)
    ├── token_budget.py
    ├── route.py                   # uses Pipeline directly now (predicate, Pipeline) pairs
    ├── conditional.py
    ├── limit.py
    ├── metadata_post_filter.py
    └── parallel.py                # ← was stages/parallel_retrieval.py (renamed shorter)
```

### Files deleted (net ~600 LOC removed)

- `retrievers/` — entire directory (6 files, ~378 LOC). Functionality folds into `stages/`.
- `retrieval/protocols.py::Retriever` / `ChunkRetriever` / `ModuleMemberRetriever` / `PipelineStage` Protocols — replaced by `Stage` ABC in `pipeline/base.py`.
- `stages/base_stage.py` — was just a re-export of the deleted Protocol.
- `stages/chunk_retrieval.py::ChunkRetrievalStage` — adapter no longer needed (retrievers ARE stages now).
- `stages/module_member_retrieval.py::ModuleMemberRetrievalStage` — same.
- `stages/sub_pipeline.py::SubPipelineStage` — replaced by direct `Pipeline` composition (Pipeline IS a Stage).
- `retrievers/pipeline_chunk.py::PipelineChunkRetriever` / `pipeline_member.py::PipelineMemberRetriever` — the reverse-Inception adapters (a Retriever that runs a Pipeline). Services call pipelines directly.

### Files renamed (for verb-action clarity)

- `retrievers/bm25_chunk.py` → `stages/bm25_chunk_search.py` (`Bm25ChunkRetriever` → `Bm25ChunkSearchStage`)
- `retrievers/like_member.py` → `stages/like_member_search.py` (`LikeModuleMemberRetriever` → `LikeMemberSearchStage`)
- `stages/reciprocal_rank_fusion.py` → `stages/rrf.py` (`ReciprocalRankFusionStage` → `RRFStage`)
- `stages/parallel_retrieval.py` → `stages/parallel.py` (`ParallelRetrievalStage` → `ParallelStage`)

---

## 4. YAML schema (no backward compat — hard flip)

User chose "no backward compatibility". The loader does NOT accept the old `stages: [...]` shape; it raises a clear error pointing at the migration.

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
# pipelines/chunk_search.yaml (new shape)
name: chunk_search
steps:
  - name: bm25
    type: bm25_chunk_search
    params:
      limit: 50
  - name: token_budget
    type: token_budget
    params:
      max_tokens: 2000
```

Key changes:
- `stages:` → `steps:` (matches the Pipeline dataclass field name).
- Every step has a `name:` (no more positional addressing).
- `chunk_retrieval` adapter wrapping a `bm25_chunk` retriever collapses into a single `bm25_chunk_search` step — the retriever IS the stage now.

### Loader error contract

If a YAML uses the old `stages:` key, the loader raises:

```
PipelineLoadError: 'stages:' key is no longer accepted (PR-0 refactor).
Use 'steps:' with a 'name:' per step. See pipelines/chunk_search.yaml
for the canonical shape.
```

No silent fallback. Users see one clear error.

### Files to update

All YAML pipeline blueprints + any benchmark configs that override them:

- `python/pydocs_mcp/pipelines/chunk_search.yaml`
- `python/pydocs_mcp/pipelines/member_search.yaml`
- `python/pydocs_mcp/pipelines/ingestion.yaml`
- `python/pydocs_mcp/defaults/default_config.yaml` (only if it inlines pipeline definitions)
- `benchmarks/configs/baseline.yaml` (and any other benchmark overlay)
- `benchmarks/configs/strict_suffix_off.yaml` — doesn't define pipelines, just overrides resolver, no change needed.

---

## 5. Service migration (DocsSearch, ApiSearch, IndexingService)

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
    chunk_pipeline: Pipeline

    async def search(self, query: SearchQuery) -> ChunkList:
        state = await self.chunk_pipeline.run(PipelineState(query=query))
        # type-narrow: chunk pipelines always populate result with a ChunkList
        # (set by the final renderer/budget stage)
        return state.result if isinstance(state.result, ChunkList) else ChunkList(items=())
```

Same data flow, one less abstraction layer. `ApiSearch` mirrors `DocsSearch`. `IndexingService` doesn't use the retrieval pipeline — unchanged.

Composition roots (`server.py`, `__main__.py`, `storage/factories.py`) build the `Pipeline` once at startup from `AppConfig` and inject it. No change to the `uow_factory` pattern from CLAUDE.md §"Creating new application services".

---

## 6. Acceptance criteria

| # | Criterion |
|---|---|
| AC1 | `python/pydocs_mcp/retrieval/pipeline/base.py` defines `Stage(ABC)` and `Pipeline(Stage)` per §2.1 and §2.2. |
| AC2 | `Pipeline.__post_init__` rejects duplicate step names and zero steps with clear `ValueError`. |
| AC3 | `pipeline["name"]` raises `KeyError(f"pipeline {name!r} has no step {key!r}")` on miss. |
| AC4 | `PipelineState` (§2.3) has fields: `query`, `candidates`, `result`, `duration_ms`, `scratch`. |
| AC5 | `retrieval/protocols.py` no longer exports `Retriever`, `ChunkRetriever`, `ModuleMemberRetriever`, `PipelineStage`. |
| AC6 | `retrieval/retrievers/` directory is deleted. |
| AC7 | Every stage in `retrieval/stages/` subclasses `Stage`. |
| AC8 | `SubPipelineStage` is deleted; `RouteStage` holds `tuple[tuple[Predicate, Pipeline], ...]`. |
| AC9 | YAML loader accepts `steps:` with `name:` per step; rejects old `stages:` with the §4 error message. |
| AC10 | All shipped YAML blueprints (`pipelines/*.yaml`, benchmark configs) use the new shape. |
| AC11 | `DocsSearch` / `ApiSearch` take `chunk_pipeline: Pipeline` / `api_pipeline: Pipeline`, not retrievers. |
| AC12 | `pytest -q` shows no regressions vs the pre-refactor baseline (same passing count modulo intentional test additions for new behavior). |
| AC13 | `ruff check python/ benchmarks/ tests/` is clean. |
| AC14 | `RepoQA` real baseline at `benchmarks/baselines/repoqa_snf.json` is unchanged (no observable retrieval behavior change). |
| AC15 | A new test pins `Pipeline.__getitem__` addressing + composition (Pipeline-in-Pipeline). |
| AC16 | A new test pins the "old `stages:` YAML rejection" error path. |
| AC17 | CLAUDE.md architecture section updated to reflect the new layout. |

---

## 7. Commit sequence

Bisect-friendly. Each commit passes `pytest -q` on its own.

| # | Commit | Notes |
|---|---|---|
| 1 | `feat(retrieval): add Stage ABC + Pipeline class + PipelineState v2` | New files only. Nothing wired up. Old `CodeRetrieverPipeline` still in use. |
| 2 | `refactor(retrieval): Bm25ChunkRetriever → Bm25ChunkSearchStage` | One stage at a time. ChunkRetrievalStage adapter kept temporarily. |
| 3 | `refactor(retrieval): LikeMemberRetriever → LikeMemberSearchStage` | Same pattern. |
| 4 | `refactor(retrieval): RRF / TokenBudget / Limit / MetadataPostFilter / Conditional / Parallel become Stage subclasses` | All non-retrieval stages flip to the new ABC in one commit (mechanical). |
| 5 | `refactor(retrieval): Route + nested Pipelines replace SubPipelineStage` | Predicate-routed Pipelines compose directly; `SubPipelineStage` deleted. |
| 6 | `refactor(retrieval): services consume Pipeline directly, drop retriever adapters` | `DocsSearch` / `ApiSearch` take `Pipeline`. `PipelineChunkRetriever` / `PipelineMemberRetriever` deleted. `ChunkRetrievalStage` / `ModuleMemberRetrievalStage` deleted. |
| 7 | `refactor(retrieval): YAML loader reads `steps:` with `name:`, rejects `stages:`` | Loader + all shipped `pipelines/*.yaml` files + benchmark configs migrated together. |
| 8 | `chore(retrieval): delete retrievers/ + slim protocols.py` | Final cleanup commit. `Retriever` Protocols gone. |
| 9 | `docs: update CLAUDE.md architecture section to reflect Stage / Pipeline` | Reflects new directory layout, deletes references to retrievers/ and Retriever Protocol. |

Total: ~9 commits, ~4.5 days of focused work.

---

## 8. Risks + mitigations

| Risk | Mitigation |
|---|---|
| Breaking changes in `retrieval/` ripple to every test that constructs a pipeline | Tests use shared fakes (`tests/_fakes.py`). Update fakes once; downstream tests inherit the new shape for free. |
| Existing YAML blueprints in `pipelines/*.yaml` need lockstep migration | Done in commit 7 alongside the loader change — single bisect point if anything regresses. |
| `dataclass(frozen=True) + ABC` quirks in Python 3.11 (e.g., subclass `__init__` field ordering) | Field ordering rule: positional fields first, then keyword-only after `*`. Each subclass adds fields with defaults so ordering stays consistent. Verified at `Stage` ABC definition. |
| `PipelineState.scratch: dict[str, object]` is mutable inside a frozen dataclass | Documented convention; scratch is intentional escape hatch. Frozen-ness of state still holds for the canonical fields. |
| User-authored YAML overlays (outside the repo) break on the schema flip | No backward compat by user choice. The error message (§4) tells them exactly what to change. Documented in PR description + CHANGELOG entry. |
| The refactor delays the real product question (RepoQA 0% retrieval) by ~1 week | Acceptable per user decision — clean architecture first, then B3 dense embeddings on top of it. Each subsequent PR (A, B3.1, B3.2) becomes a smaller, focused change. |

---

## 9. Out of scope (separate specs to follow)

Each of these gets its own brainstorm → spec → plan cycle after PR-0 lands:

- **PR-A** (small, ~1-2 days) — `chunk_search_ranked.yaml` preset that drops `TokenBudgetStage` for benchmark consumers. Re-measure RepoQA real baseline with ranked output. Expected: `recall@k > 0` on at least the symbol-style needles.
- **PR-B3.1** (medium, ~3-5 days) — `EmbeddingProvider` Protocol + registry (`openai_api`, `fastembed_local`). New `DenseRetrievalStage` + `chunks.embedding` schema column. CI defaults to FastEmbed (offline). `CodeRankEmbed` as the default FastEmbed model (code-tuned).
- **PR-B3.2** (medium, ~3-5 days) — `RerankerProvider` Protocol + registry (`cohere_rerank`). New `RerankStage` operating on top-N candidates from B3.1. API key via env var (`COHERE_API_KEY`); CI either has the secret or skips rerank.

---

## 10. Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/pipeline-refactor/`
Branch: `feature/pipeline-refactor` off `main` at `a8de6de`.
