# Retrieval Pipeline Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the retrieval layer's two parallel hierarchies (`Retriever` Protocol + `PipelineStage` Protocol with `*RetrievalStage` adapters between them) with a single sklearn-style abstraction — `RetrieverStep` ABC + `RetrieverPipeline` with named, addressable steps. Decompose the monolithic `Bm25ChunkRetriever` into three single-responsibility steps (`ChunkFetcherStep` + `BM25ScorerStep` + `TopKFilterStep`) so future B3.1 dense retrieval composes cleanly alongside BM25.

**Architecture (from [spec](../specs/2026-05-21-retrieval-pipeline-refactor-design.md)):**
1. `RetrieverStep(ABC)` with `name: str` + `async run(state) -> state`.
2. `RetrieverPipeline(RetrieverStep)` holding `tuple[tuple[str, RetrieverStep], ...]` — Pipeline IS a Step.
3. `RetrieverState` typed dataclass with `query`, `candidates`, `result`, `duration_ms`, `scratch`.
4. Directory: `stages/` → `steps/`; `retrievers/` folds in; new `pipeline/` for the base classes.
5. YAML: `stages:` → `steps:` with `name:` per step. No backward compat (clean reject of the old key).

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, ruff. Existing pydantic-settings + pyyaml + asyncio.

**Reviewer strategy** (per `superpowers:subagent-driven-development`): each task gets two-stage review (spec-compliance + code-quality), with reviewer prompts customized per task category.

| Task category | Spec reviewer focus | Code reviewer focus |
|---|---|---|
| Foundations (Task 1) | ABC contract matches §2.1; dataclass(frozen=True) + ABC works in Py 3.11 | No defensive `if x is None` guards; method signatures match spec |
| Mass rename (Task 2) | All `*Stage` class names + `stages/` dir + imports updated; no orphans | Diff is mechanical; no unrelated changes slip in |
| Subclass refactor (Tasks 3–6) | Each concrete step inherits `RetrieverStep`; old `PipelineStage` import gone from each file | Each step has ONE responsibility per §2.4; no resurrected adapter logic |
| YAML loader (Task 8) | Loader rejects old `stages:` with exact §4 error message; all shipped YAML files migrated | No silent fallback; error message text is verbatim |
| Service migration (Task 7) | `DocsSearch`/`ApiSearch` take `RetrieverPipeline` directly; no `Retriever` Protocol callers left | Type-narrow defensively for ChunkList result; no behavior drift |
| Final cleanup (Tasks 9, 10) | `retrievers/` deleted; protocols.py slim; CLAUDE.md updated | No remaining `*Stage` / `Retriever` exports |

---

## Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/pipeline-refactor/`
Branch: `feature/pipeline-refactor` at `e96de42` (post-spec, no implementation yet).
Spec: [docs/superpowers/specs/2026-05-21-retrieval-pipeline-refactor-design.md](../specs/2026-05-21-retrieval-pipeline-refactor-design.md)

---

## Task 0: Baseline + venv setup

**Files:** none modified. This task just captures the pre-refactor baseline so Tasks 1–10 can verify "no regressions".

- [ ] **Step 1: Verify worktree state**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/pipeline-refactor
git status
git log --oneline -3
```

Expected: clean worktree, HEAD at `e96de42 spec(rev): consistency pass — add naming-conventions callout, tighten Driver`.

- [ ] **Step 2: Set up venv (if not already done)**

```bash
python3.11 -m venv .venv
.venv/bin/pip install --quiet -e . -e benchmarks/
.venv/bin/python -c "from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState; print('imports OK')"
```

Expected: `imports OK`.

- [ ] **Step 3: Capture pre-refactor test count**

```bash
.venv/bin/pytest -q 2>&1 | tail -3 > /tmp/pre_refactor_pytest.txt
cat /tmp/pre_refactor_pytest.txt
```

Expected: a line ending in `XXX passed in YYs`. Record this number — Tasks 1–10 must end with the same count (or higher if a task intentionally adds tests).

- [ ] **Step 4: Capture pre-refactor benchmark count**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3 > /tmp/pre_refactor_benchmarks.txt
cat /tmp/pre_refactor_benchmarks.txt
```

Expected: `XXX passed`. Record this number.

- [ ] **Step 5: Capture pre-refactor RepoQA real baseline (for AC17)**

```bash
sha256sum benchmarks/baselines/repoqa_snf.json > /tmp/pre_refactor_baseline.sha256
cat /tmp/pre_refactor_baseline.sha256
```

The refactor must NOT change this file (AC17 — no observable retrieval behavior change).

- [ ] **Step 6: No commit for Task 0**

Task 0 is verification only — nothing changes on disk.

---

## Task 1: Add `RetrieverStep` ABC + `RetrieverPipeline` + `RetrieverState` (foundations)

**Goal:** ship the new abstractions as standalone files. Nothing wired up yet — old `CodeRetrieverPipeline` remains in use.

**Files:**
- Create: `python/pydocs_mcp/retrieval/pipeline/__init__.py`
- Create: `python/pydocs_mcp/retrieval/pipeline/base.py`
- Create: `python/pydocs_mcp/retrieval/pipeline/state.py`
- Create: `tests/retrieval/pipeline/__init__.py`
- Create: `tests/retrieval/pipeline/test_pipeline_base.py`

**Note:** `python/pydocs_mcp/retrieval/pipeline.py` (the module file) currently holds `CodeRetrieverPipeline` + `PipelineState`. We can't create a `pipeline/` directory at the same path while the `pipeline.py` file exists. Resolution: in Step 1 below, rename `pipeline.py` → `pipeline_legacy.py` first (one commit), then introduce the new `pipeline/` package. Step 1's rename is mechanical.

- [ ] **Step 1: Rename `pipeline.py` → `pipeline_legacy.py` to free the namespace**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/pipeline-refactor
git mv python/pydocs_mcp/retrieval/pipeline.py python/pydocs_mcp/retrieval/pipeline_legacy.py
grep -rln "from pydocs_mcp.retrieval.pipeline import\|from pydocs_mcp.retrieval import pipeline" python/ tests/ benchmarks/src/ 2>/dev/null
```

The grep lists every import we need to update. For each file in the output, change:
- `from pydocs_mcp.retrieval.pipeline import X` → `from pydocs_mcp.retrieval.pipeline_legacy import X`
- `from pydocs_mcp.retrieval import pipeline as P` → `from pydocs_mcp.retrieval import pipeline_legacy as P`

Verify with: `grep -rn "from pydocs_mcp.retrieval.pipeline " python/ tests/ benchmarks/src/ 2>/dev/null` — should return only `pipeline_legacy` hits.

Run pytest to confirm everything still imports:

```bash
.venv/bin/pytest tests/ -q 2>&1 | tail -3
```

Expected: same passing count as baseline. No new failures.

- [ ] **Step 2: Write failing tests for the new abstractions**

Create `tests/retrieval/pipeline/__init__.py` as empty file:

```python
```

Create `tests/retrieval/pipeline/test_pipeline_base.py`:

```python
"""Tests for the new RetrieverStep ABC + RetrieverPipeline + RetrieverState."""
from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import (
    RetrieverPipeline,
    RetrieverState,
    RetrieverStep,
)


@dataclass(frozen=True, slots=True)
class _BumpStep(RetrieverStep):
    """Test fixture: bumps duration_ms by 1."""

    async def run(self, state: RetrieverState) -> RetrieverState:
        return replace(state, duration_ms=state.duration_ms + 1.0)


def _query() -> SearchQuery:
    return SearchQuery(terms="anything", max_results=10)


@pytest.mark.asyncio
async def test_pipeline_runs_steps_in_order() -> None:
    pipeline = RetrieverPipeline(
        name="p",
        steps=(
            ("a", _BumpStep(name="a")),
            ("b", _BumpStep(name="b")),
            ("c", _BumpStep(name="c")),
        ),
    )
    out = await pipeline.run(RetrieverState(query=_query()))
    assert out.duration_ms == 3.0


def test_pipeline_addresses_steps_by_name() -> None:
    a = _BumpStep(name="a")
    b = _BumpStep(name="b")
    pipeline = RetrieverPipeline(name="p", steps=(("a", a), ("b", b)))
    assert pipeline["a"] is a
    assert pipeline["b"] is b


def test_pipeline_step_names() -> None:
    pipeline = RetrieverPipeline(
        name="p",
        steps=(
            ("fetch", _BumpStep(name="fetch")),
            ("score", _BumpStep(name="score")),
        ),
    )
    assert pipeline.step_names == ("fetch", "score")


def test_pipeline_rejects_duplicate_step_names() -> None:
    with pytest.raises(ValueError, match="duplicate step names"):
        RetrieverPipeline(
            name="p",
            steps=(("a", _BumpStep(name="a")), ("a", _BumpStep(name="a"))),
        )


def test_pipeline_rejects_zero_steps() -> None:
    with pytest.raises(ValueError, match="has no steps"):
        RetrieverPipeline(name="p", steps=())


def test_pipeline_keyerror_on_unknown_step() -> None:
    pipeline = RetrieverPipeline(name="p", steps=(("a", _BumpStep(name="a")),))
    with pytest.raises(KeyError, match="has no step 'b'"):
        _ = pipeline["b"]


@pytest.mark.asyncio
async def test_pipeline_is_a_step_composes_recursively() -> None:
    """Pipeline IS a RetrieverStep — they nest."""
    inner = RetrieverPipeline(
        name="inner",
        steps=(("a", _BumpStep(name="a")), ("b", _BumpStep(name="b"))),
    )
    outer = RetrieverPipeline(
        name="outer",
        steps=(("inner", inner), ("c", _BumpStep(name="c"))),
    )
    assert isinstance(inner, RetrieverStep)
    out = await outer.run(RetrieverState(query=_query()))
    assert out.duration_ms == 3.0  # 2 from inner + 1 from c


def test_retriever_step_is_abstract() -> None:
    """Can't instantiate the ABC directly."""
    with pytest.raises(TypeError, match="abstract"):
        RetrieverStep(name="bare")  # type: ignore[abstract]


def test_state_is_frozen() -> None:
    state = RetrieverState(query=_query())
    with pytest.raises(AttributeError):
        state.duration_ms = 5.0  # type: ignore[misc]


def test_state_scratch_is_mutable() -> None:
    """Scratch dict can be mutated even though the dataclass is frozen."""
    state = RetrieverState(query=_query())
    state.scratch["bm25.weights"] = [0.5, 0.3]
    assert state.scratch["bm25.weights"] == [0.5, 0.3]


@pytest.mark.asyncio
async def test_state_replace_for_pure_updates() -> None:
    """Stages produce a new state via dataclasses.replace, not mutation."""
    initial = RetrieverState(query=_query())
    bumped = replace(initial, duration_ms=42.0)
    assert initial.duration_ms == 0.0
    assert bumped.duration_ms == 42.0
```

- [ ] **Step 3: Run tests to verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/pipeline/test_pipeline_base.py -v 2>&1 | tail -15
```

Expected: `ImportError: cannot import name 'RetrieverPipeline' from 'pydocs_mcp.retrieval.pipeline'` (or similar — `pydocs_mcp.retrieval.pipeline` is now empty or doesn't exist).

- [ ] **Step 4: Create `python/pydocs_mcp/retrieval/pipeline/state.py`**

```python
"""RetrieverState — immutable typed state threaded through a RetrieverPipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.models import (
        ChunkList,
        ModuleMemberList,
        PipelineResultItem,
        SearchQuery,
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
    """
    query: "SearchQuery"
    candidates: "ChunkList | ModuleMemberList | None" = None
    result: "PipelineResultItem | None" = None
    duration_ms: float = 0.0
    # WHY: free-form per-step scratch. The dict is mutable even inside a
    # frozen dataclass (frozen=True forbids field reassignment, not deep
    # mutation). Convention: keys are ``<step_name>.<field>`` so collisions
    # are detectable. Intentional escape hatch for cross-step coordination
    # that doesn't merit a typed field (RRF intermediate scores, debug
    # breadcrumbs).
    scratch: dict[str, object] = field(default_factory=dict)
```

- [ ] **Step 5: Create `python/pydocs_mcp/retrieval/pipeline/base.py`**

```python
"""RetrieverStep ABC + RetrieverPipeline class.

The retrieval-pipeline contract. Every step in a retrieval pipeline
subclasses ``RetrieverStep`` and implements ``async def run(state)``.
A ``RetrieverPipeline`` is itself a ``RetrieverStep`` — they compose
recursively.

Naming: ``RetrieverStep`` (not ``Stage``) differentiates this contract
from the extraction-side ``IngestionStage`` Protocol at
``pydocs_mcp/extraction/pipeline/ingestion.py``. Different pipelines,
different state shapes, different contracts.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline.state import RetrieverState


@dataclass(frozen=True, slots=True)
class RetrieverStep(ABC):
    """A single retrieval-pipeline step. Pure: take a state, return a NEW state.

    Subclasses set ``name: str`` (used for addressing + debug logs) and
    implement ``async def run(self, state) -> state``.
    """
    name: str

    @abstractmethod
    async def run(self, state: RetrieverState) -> RetrieverState: ...


@dataclass(frozen=True, slots=True)
class RetrieverPipeline(RetrieverStep):
    """An ordered tuple of named ``RetrieverStep``s. A Pipeline IS a Step.

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

        chunk_pipeline["fetch"]  # → ChunkFetcherStep
        chunk_pipeline.step_names  # → ("fetch", "score", "topk", "budget")
    """
    steps: tuple[tuple[str, RetrieverStep], ...]

    def __post_init__(self) -> None:
        names = [n for n, _ in self.steps]
        if len(names) != len(set(names)):
            raise ValueError(
                f"duplicate step names in {self.name!r}: {names}",
            )
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

- [ ] **Step 6: Create `python/pydocs_mcp/retrieval/pipeline/__init__.py`**

```python
"""Retrieval-pipeline abstractions: RetrieverStep ABC + RetrieverPipeline + RetrieverState."""
from pydocs_mcp.retrieval.pipeline.base import RetrieverPipeline, RetrieverStep
from pydocs_mcp.retrieval.pipeline.state import RetrieverState

__all__ = ("RetrieverPipeline", "RetrieverState", "RetrieverStep")
```

- [ ] **Step 7: Run tests to verify PASS**

```bash
.venv/bin/pytest tests/retrieval/pipeline/test_pipeline_base.py -v 2>&1 | tail -20
```

Expected: 11 tests passing.

- [ ] **Step 8: Run full suite, expect no regressions**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: same passing count as Task 0 baseline + 11 new tests.

- [ ] **Step 9: Ruff clean**

```bash
.venv/bin/ruff check python/pydocs_mcp/retrieval/pipeline/ tests/retrieval/pipeline/
```

Expected: `All checks passed!`

- [ ] **Step 10: Commit**

```bash
git add python/pydocs_mcp/retrieval/pipeline/ \
        python/pydocs_mcp/retrieval/pipeline_legacy.py \
        tests/retrieval/pipeline/
# Plus any files modified in Step 1 for the import rename:
git add -u

git commit -m "feat(retrieval): add RetrieverStep ABC + RetrieverPipeline + RetrieverState

Foundations for the retrieval-pipeline refactor. New files only:
- retrieval/pipeline/base.py — RetrieverStep ABC + RetrieverPipeline
- retrieval/pipeline/state.py — RetrieverState dataclass
- retrieval/pipeline/__init__.py — re-exports

Renamed legacy:
- retrieval/pipeline.py → retrieval/pipeline_legacy.py (freed the namespace).
  All imports updated to the legacy name. CodeRetrieverPipeline +
  PipelineState still in use by everything; they get removed in Task 9.

Naming: RetrieverStep (not Stage) differentiates from extraction-side
IngestionStage Protocol. Spec §2.1 / §2.2 / §2.3."
```

---

## Task 2: Rename `stages/` → `steps/`, `*Stage` classes → `*Step`

**Goal:** mechanical, sed-style rename. No logic change. After this task, every existing stage subclasses the OLD `PipelineStage` Protocol but lives in `steps/` under the new `*Step` name.

**Files:**
- Rename: `python/pydocs_mcp/retrieval/stages/` → `python/pydocs_mcp/retrieval/steps/`
- Modify: every file in the renamed directory (class name change)
- Modify: every import site across `python/`, `tests/`, `benchmarks/`

- [ ] **Step 1: List all files to rename + class names to update**

```bash
ls python/pydocs_mcp/retrieval/stages/
```

Expected list:
```
__init__.py
base_stage.py
chunk_retrieval.py
conditional.py
limit.py
metadata_post_filter.py
module_member_retrieval.py
parallel_retrieval.py
reciprocal_rank_fusion.py
route.py
sub_pipeline.py
token_budget.py
```

Class renames:
- `ChunkRetrievalStage` → `ChunkRetrievalStep`
- `ConditionalStage` → `ConditionalStep`
- `LimitStage` → `LimitStep`
- `MetadataPostFilterStage` → `MetadataPostFilterStep`
- `ModuleMemberRetrievalStage` → `ModuleMemberRetrievalStep`
- `ParallelRetrievalStage` → `ParallelStep`  (also file rename: `parallel_retrieval.py` → `parallel.py`)
- `ReciprocalRankFusionStage` → `RRFStep`  (file rename: `reciprocal_rank_fusion.py` → `rrf.py`)
- `RouteStage` → `RouteStep`
- `SubPipelineStage` → `SubPipelineStep`  (renamed but deleted in Task 6 — keeping the *Step suffix for one intermediate commit)
- `TokenBudgetStage` → `TokenBudgetStep`

- [ ] **Step 2: Rename the directory**

```bash
git mv python/pydocs_mcp/retrieval/stages python/pydocs_mcp/retrieval/steps
```

- [ ] **Step 3: Rename two files for verb-action clarity**

```bash
git mv python/pydocs_mcp/retrieval/steps/parallel_retrieval.py python/pydocs_mcp/retrieval/steps/parallel.py
git mv python/pydocs_mcp/retrieval/steps/reciprocal_rank_fusion.py python/pydocs_mcp/retrieval/steps/rrf.py
```

- [ ] **Step 4: Update class names inside each file**

For each renamed class above, edit the file. Use `sed` for each file or `Edit` tool. Pattern: `class <OldName>(...)` → `class <NewName>(...)`. Also update internal references (e.g., `__all__`, docstrings).

Example for `chunk_retrieval.py`:

```bash
sed -i.bak 's/ChunkRetrievalStage/ChunkRetrievalStep/g' python/pydocs_mcp/retrieval/steps/chunk_retrieval.py
rm python/pydocs_mcp/retrieval/steps/chunk_retrieval.py.bak
```

Repeat for every file. The `__init__.py` exports need updating too.

- [ ] **Step 5: Update `steps/__init__.py` exports**

```python
"""Retrieval-pipeline steps — one file per step."""
from pydocs_mcp.retrieval.steps.base_stage import PipelineStage  # still re-exported until Task 9
from pydocs_mcp.retrieval.steps.chunk_retrieval import ChunkRetrievalStep
from pydocs_mcp.retrieval.steps.conditional import ConditionalStep
from pydocs_mcp.retrieval.steps.limit import LimitStep
from pydocs_mcp.retrieval.steps.metadata_post_filter import MetadataPostFilterStep
from pydocs_mcp.retrieval.steps.module_member_retrieval import ModuleMemberRetrievalStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep
from pydocs_mcp.retrieval.steps.route import RouteCase, RouteStep
from pydocs_mcp.retrieval.steps.rrf import RRFStep
from pydocs_mcp.retrieval.steps.sub_pipeline import SubPipelineStep
from pydocs_mcp.retrieval.steps.token_budget import TokenBudgetStep

__all__ = (
    "ChunkRetrievalStep",
    "ConditionalStep",
    "LimitStep",
    "MetadataPostFilterStep",
    "ModuleMemberRetrievalStep",
    "ParallelStep",
    "PipelineStage",  # legacy — removed in Task 9
    "RRFStep",
    "RouteCase",
    "RouteStep",
    "SubPipelineStep",
    "TokenBudgetStep",
)
```

- [ ] **Step 6: Update every import site across the codebase**

```bash
grep -rln "from pydocs_mcp.retrieval.stages\|retrieval.stages" python/ tests/ benchmarks/src/ 2>/dev/null
```

For each file: replace `retrieval.stages` → `retrieval.steps`.

Then for class-name updates:

```bash
grep -rln "RouteStage\|SubPipelineStage\|TokenBudgetStage\|ChunkRetrievalStage\|ModuleMemberRetrievalStage\|ConditionalStage\|LimitStage\|ParallelRetrievalStage\|ReciprocalRankFusionStage\|MetadataPostFilterStage" python/ tests/ benchmarks/src/ 2>/dev/null
```

For each file in the output, run the appropriate sed substitution. Be careful to skip:
- `IngestionStage` (ingestion-side, untouched)
- `PipelineStage` (still alive until Task 9)
- `*RetrievalStage` mentions in code that's GOING to be deleted (e.g., adapter stages) — these are renamed to `*Step` here, deleted in Task 7.

- [ ] **Step 7: Update `python/pydocs_mcp/retrieval/config.py` import line**

```python
# was:
from pydocs_mcp.retrieval.stages import RouteCase, RouteStage, SubPipelineStage
# now:
from pydocs_mcp.retrieval.steps import RouteCase, RouteStep, SubPipelineStep
```

And update `SubPipelineStage(pipeline=...)` → `SubPipelineStep(pipeline=...)` at the call site (around line 474).

- [ ] **Step 8: Update `pipeline_legacy.py` import**

```python
# was:
from pydocs_mcp.retrieval.protocols import PipelineStage
# stays the same — PipelineStage Protocol lives in protocols.py until Task 9
```

But the comment on line 29 mentions `SubPipelineStage`:

```python
# WHY: ... SubPipelineStage chains nest deeply. ...
```

Update to: `# WHY: ... SubPipelineStep chains nest deeply. ...`

- [ ] **Step 9: Update `stage_registry` references** (still spelled "stage_registry" — Task 8 will rename)

Check `python/pydocs_mcp/retrieval/serialization.py` for `stage_registry` references. Don't rename it in this task — that's Task 8 (YAML loader work).

- [ ] **Step 10: Update `tests/_retriever_helpers.py`**

```bash
grep -n "Stage" tests/_retriever_helpers.py
```

For each `*Stage` hit, rename to `*Step` per the mapping in Step 1.

- [ ] **Step 11: Run pytest**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: same passing count as Task 1's commit. No new failures, no new passes (no tests added).

- [ ] **Step 12: Ruff clean**

```bash
.venv/bin/ruff check python/ tests/ benchmarks/
```

Expected: `All checks passed!`

- [ ] **Step 13: Verify no `*Stage` references remain (except legitimate ones)**

```bash
grep -rn "ChunkRetrievalStage\|ConditionalStage\|LimitStage\|MetadataPostFilterStage\|ModuleMemberRetrievalStage\|ParallelRetrievalStage\|ReciprocalRankFusionStage\|RouteStage\|SubPipelineStage\|TokenBudgetStage" python/ tests/ benchmarks/src/ 2>/dev/null | grep -v __pycache__
```

Expected: empty output. (Legitimate `IngestionStage` and `PipelineStage` are not in this grep list — they stay.)

- [ ] **Step 14: Commit**

```bash
git add -A
git commit -m "refactor(retrieval): rename stages/ → steps/, *Stage → *Step (mechanical)

Pure sed-style rename. No behavior change. Each step still subclasses
the legacy PipelineStage Protocol (replaced in Task 3) but the class
name now ends in *Step instead of *Stage, and the directory is steps/
instead of stages/.

File renames:
- stages/parallel_retrieval.py → steps/parallel.py
- stages/reciprocal_rank_fusion.py → steps/rrf.py
- (all others keep their filename, only the directory and class name change)

Spec §3 directory layout 'After' section."
```

---

## Task 3: Concrete steps subclass `RetrieverStep` ABC instead of `PipelineStage` Protocol

**Goal:** flip every non-retrieval step from `PipelineStage` Protocol-conformance to explicit `RetrieverStep(ABC)` subclass. Retrieval-related steps (`ChunkRetrievalStep`, `ModuleMemberRetrievalStep`, `Bm25ChunkRetriever`, `LikeModuleMemberRetriever`) are NOT touched here — Tasks 4–7 handle them.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/conditional.py`
- Modify: `python/pydocs_mcp/retrieval/steps/limit.py`
- Modify: `python/pydocs_mcp/retrieval/steps/metadata_post_filter.py`
- Modify: `python/pydocs_mcp/retrieval/steps/parallel.py`
- Modify: `python/pydocs_mcp/retrieval/steps/rrf.py`
- Modify: `python/pydocs_mcp/retrieval/steps/route.py`
- Modify: `python/pydocs_mcp/retrieval/steps/sub_pipeline.py`
- Modify: `python/pydocs_mcp/retrieval/steps/token_budget.py`

For each file, the pattern is:

```python
# BEFORE:
from pydocs_mcp.retrieval.steps.base_stage import PipelineStage
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class TokenBudgetStep:
    name: str = "token_budget"
    # ... fields ...

    async def run(self, state):
        # ... existing impl ...

# AFTER:
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class TokenBudgetStep(RetrieverStep):
    # ``name`` inherited from RetrieverStep
    # ... existing fields ...

    async def run(self, state: RetrieverState) -> RetrieverState:
        # ... existing impl ...
```

Important: `RetrieverStep` declares `name: str` (no default). Subclasses with `frozen=True` must keep field-ordering rules: positional fields first, then fields with defaults. If a step's current `name` has a default (e.g., `name: str = "token_budget"`), keeping that default is fine — subclasses inherit the field and can override the default.

- [ ] **Step 1: Survey each file's current pattern**

```bash
for f in python/pydocs_mcp/retrieval/steps/*.py; do
    echo "=== $f ==="
    head -25 "$f"
done
```

Note for each file: where does `name` come from, what dataclass fields exist, what's the `async def run` signature, what imports does it need.

- [ ] **Step 2: Refactor `token_budget.py` first as a template**

Edit `python/pydocs_mcp/retrieval/steps/token_budget.py`. Replace the import of `PipelineStage` with the new ABC:

```python
# at the top of the file
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
```

Drop the old `PipelineStage` import. Change `class TokenBudgetStep:` → `class TokenBudgetStep(RetrieverStep):`. The `name` field is inherited; remove it from the subclass body if it's declared explicitly (or keep with a default value as override).

- [ ] **Step 3: Refactor each remaining step file with the same pattern**

Files in this order (smallest first for confidence):
1. `limit.py`
2. `conditional.py`
3. `metadata_post_filter.py`
4. `rrf.py`
5. `parallel.py`
6. `route.py`
7. `sub_pipeline.py` (will be deleted in Task 6 — but flip the ABC for consistency)

- [ ] **Step 4: Run pytest**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: same passing count as Task 2's commit.

- [ ] **Step 5: Run the new pipeline base tests**

```bash
.venv/bin/pytest tests/retrieval/pipeline/ -v 2>&1 | tail -15
```

Expected: 11 passing.

- [ ] **Step 6: Run an `isinstance` smoke check**

```bash
.venv/bin/python -c "
from pydocs_mcp.retrieval.pipeline import RetrieverStep
from pydocs_mcp.retrieval.steps import (
    ConditionalStep, LimitStep, MetadataPostFilterStep,
    ParallelStep, RRFStep, RouteStep, SubPipelineStep, TokenBudgetStep,
)
for klass in (ConditionalStep, LimitStep, MetadataPostFilterStep,
              ParallelStep, RRFStep, RouteStep, SubPipelineStep, TokenBudgetStep):
    assert issubclass(klass, RetrieverStep), f'{klass.__name__} not a RetrieverStep'
print('all non-retrieval steps subclass RetrieverStep ✓')
"
```

Expected: `all non-retrieval steps subclass RetrieverStep ✓`.

- [ ] **Step 7: Ruff clean**

```bash
.venv/bin/ruff check python/pydocs_mcp/retrieval/
```

Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "refactor(retrieval): non-retrieval steps subclass RetrieverStep ABC

Flips ConditionalStep, LimitStep, MetadataPostFilterStep, ParallelStep,
RRFStep, RouteStep, SubPipelineStep, TokenBudgetStep from the legacy
PipelineStage Protocol to explicit RetrieverStep(ABC) subclasses.

ChunkRetrievalStep + ModuleMemberRetrievalStep (the retriever adapter
stages) and Bm25ChunkRetriever / LikeModuleMemberRetriever (the
retrievers themselves) are NOT touched — Tasks 4–7 handle them.

Spec §2.4."
```

---

## Task 4: Split `Bm25ChunkRetriever` into `ChunkFetcherStep` + `BM25ScorerStep` + `TopKFilterStep`

**Goal:** decompose the monolithic BM25 chunk retriever into three single-responsibility steps. After this task, the chunk pipeline still produces identical output but the work is distributed across composable steps.

**Files:**
- Read: `python/pydocs_mcp/retrieval/retrievers/bm25_chunk.py` (the monolith)
- Create: `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py`
- Create: `python/pydocs_mcp/retrieval/steps/bm25_scorer.py`
- Create: `python/pydocs_mcp/retrieval/steps/top_k_filter.py`
- Create: `tests/retrieval/steps/test_chunk_fetcher.py`
- Create: `tests/retrieval/steps/test_bm25_scorer.py`
- Create: `tests/retrieval/steps/test_top_k_filter.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` (export new steps)
- Modify: `python/pydocs_mcp/retrieval/steps/chunk_retrieval.py` (adapter — wire to use the new steps internally, removed in Task 7)

**Survey before implementing:** read `bm25_chunk.py` end-to-end. Note the SQL query shape, parameter list, how it converts rows to `Chunk` objects, where `relevance` gets set.

- [ ] **Step 1: Write failing tests for `ChunkFetcherStep`**

Create `tests/retrieval/steps/__init__.py` if missing (empty file).

Create `tests/retrieval/steps/test_chunk_fetcher.py`:

```python
"""ChunkFetcherStep tests — issues FTS5 MATCH query, returns candidates with raw FTS5 ranks."""
from __future__ import annotations

import sqlite3
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.models import Chunk, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.pipeline_legacy import PerCallConnectionProvider
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """A tiny SQLite with chunks_fts populated so FTS5 MATCH works."""
    from pydocs_mcp.db import open_index_database
    db_path = tmp_path / "fixtures.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO packages (name) VALUES ('demo')")
    conn.execute(
        "INSERT INTO chunks (package, kind, title, text, source_path, module) "
        "VALUES ('demo', 'api', 'add', 'def add(a, b): return a + b', 'demo/m.py', 'demo.m')"
    )
    conn.execute(
        "INSERT INTO chunks (package, kind, title, text, source_path, module) "
        "VALUES ('demo', 'api', 'sub', 'def sub(a, b): return a - b', 'demo/m.py', 'demo.m')"
    )
    conn.commit()
    # Trigger FTS rebuild (the project's stored procedure)
    from pydocs_mcp.storage.sqlite import SqliteChunkRepository
    from pydocs_mcp.db import build_connection_provider
    repo = SqliteChunkRepository(provider=build_connection_provider(db_path))
    import asyncio
    asyncio.run(repo.rebuild_index())
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_fetcher_returns_candidates_for_matching_query(populated_db: Path) -> None:
    """FTS5 MATCH 'add' returns the chunk whose text contains 'add'."""
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert out.candidates is not None
    assert len(out.candidates.items) >= 1
    assert any("def add" in c.text for c in out.candidates.items)


@pytest.mark.asyncio
async def test_fetcher_respects_limit(populated_db: Path) -> None:
    """limit caps the returned candidate count."""
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=1)
    state = RetrieverState(query=SearchQuery(terms="a", max_results=10))
    out = await step.run(state)
    assert out.candidates is not None
    assert len(out.candidates.items) <= 1


@pytest.mark.asyncio
async def test_fetcher_captures_fts5_rank(populated_db: Path) -> None:
    """Candidates carry FTS5's BM25 rank as ``relevance`` (negative score:
    lower = better, per FTS5 convention)."""
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = ChunkFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert out.candidates is not None
    assert all(c.relevance is not None for c in out.candidates.items)
```

- [ ] **Step 2: Run tests to verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_chunk_fetcher.py -v 2>&1 | tail -10
```

Expected: `ImportError: cannot import name 'ChunkFetcherStep'`.

- [ ] **Step 3: Implement `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py`**

Read `python/pydocs_mcp/retrieval/retrievers/bm25_chunk.py` for the canonical SQL query and row-to-Chunk conversion. Then write:

```python
"""ChunkFetcherStep — candidate generation via SQLite FTS5 MATCH.

Single responsibility: take a query, return up to N candidate chunks
with FTS5's BM25 rank captured as ``relevance``. No score normalization,
no top-K cutoff, no rendering.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider

# Mirrors the SQL in retrievers/bm25_chunk.py — single source of truth
# moves here as part of the decomposition.
_FETCH_SQL = """
    SELECT
        c.id, c.package, c.kind, c.title, c.text,
        c.source_path, c.module, c.metadata_json,
        chunks_fts.rank
    FROM chunks_fts
    JOIN chunks c ON c.id = chunks_fts.rowid
    WHERE chunks_fts MATCH ?
    ORDER BY chunks_fts.rank
    LIMIT ?
"""


@dataclass(frozen=True, slots=True)
class ChunkFetcherStep(RetrieverStep):
    """Candidate generation step for chunk pipelines.

    Reads ``state.query.terms``. Writes ``state.candidates`` as a
    ``ChunkList`` with each chunk's ``relevance`` set to FTS5's
    negative BM25 rank (lower is better; sign-flipped by BM25ScorerStep
    downstream).
    """
    provider: ConnectionProvider
    limit: int = 50

    async def run(self, state: RetrieverState) -> RetrieverState:
        rows = await asyncio.to_thread(self._fetch_sync, state.query.terms)
        chunks = tuple(_row_to_chunk(row) for row in rows)
        return replace(state, candidates=ChunkList(items=chunks))

    def _fetch_sync(self, terms: str) -> list[tuple]:
        # WHY: ConnectionProvider.acquire is async; we're inside to_thread
        # already so we use the provider's sync-friendly path. The existing
        # PerCallConnectionProvider builds a fresh connection synchronously
        # inside its async context manager body.
        import sqlite3
        conn = sqlite3.connect(str(self.provider.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(_FETCH_SQL, (terms, self.limit)).fetchall())
        finally:
            conn.close()


def _row_to_chunk(row) -> Chunk:
    """Row → Chunk conversion. Mirror of retrievers/bm25_chunk.py logic."""
    import json
    metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
    return Chunk(
        package=row["package"],
        kind=row["kind"],
        title=row["title"],
        text=row["text"],
        source_path=row["source_path"],
        module=row["module"],
        metadata=metadata,
        # WHY: FTS5's ``rank`` is a negative BM25 score (lower is better).
        # Surface it raw here; BM25ScorerStep normalizes the sign in the
        # next step.
        relevance=float(row["rank"]),
    )
```

- [ ] **Step 4: Run tests to verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_chunk_fetcher.py -v 2>&1 | tail -10
```

Expected: 3 tests passing.

- [ ] **Step 5: Write failing tests for `BM25ScorerStep`**

Create `tests/retrieval/steps/test_bm25_scorer.py`:

```python
"""BM25ScorerStep tests — normalizes FTS5 rank into positive relevance scores."""
from __future__ import annotations

from dataclasses import replace

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.bm25_scorer import BM25ScorerStep


def _state_with_candidates(*relevances: float) -> RetrieverState:
    chunks = tuple(
        Chunk(
            package="demo", kind="api", title=f"t{i}", text=f"def f{i}()",
            source_path="m.py", module="demo.m", metadata={}, relevance=r,
        )
        for i, r in enumerate(relevances)
    )
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(items=chunks),
    )


@pytest.mark.asyncio
async def test_scorer_flips_sign_of_fts5_rank() -> None:
    """FTS5 ranks are negative (lower = better). Scorer flips sign so
    higher = better (the convention every other step assumes)."""
    state = _state_with_candidates(-2.5, -1.0, -0.5)
    step = BM25ScorerStep(name="score")
    out = await step.run(state)
    assert out.candidates is not None
    scores = [c.relevance for c in out.candidates.items]
    assert scores == [2.5, 1.0, 0.5]


@pytest.mark.asyncio
async def test_scorer_no_op_on_empty_candidates() -> None:
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(items=()),
    )
    step = BM25ScorerStep(name="score")
    out = await step.run(state)
    assert out.candidates is not None
    assert len(out.candidates.items) == 0


@pytest.mark.asyncio
async def test_scorer_skips_when_candidates_is_none() -> None:
    """No candidates → no work. State pass-through unchanged."""
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=None,
    )
    step = BM25ScorerStep(name="score")
    out = await step.run(state)
    assert out.candidates is None


@pytest.mark.asyncio
async def test_scorer_skips_member_candidates() -> None:
    """If candidates is a ModuleMemberList (wrong pipeline), no-op pass-through."""
    from pydocs_mcp.models import ModuleMember, ModuleMemberList
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ModuleMemberList(items=(
            ModuleMember(package="demo", module="m", name="f", kind="function",
                         signature="f()", docstring=None, metadata={}, relevance=None),
        )),
    )
    step = BM25ScorerStep(name="score")
    out = await step.run(state)
    # Members pass through unchanged.
    assert isinstance(out.candidates, ModuleMemberList)
```

- [ ] **Step 6: Run tests to verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_bm25_scorer.py -v 2>&1 | tail -10
```

Expected: `ImportError`.

- [ ] **Step 7: Implement `python/pydocs_mcp/retrieval/steps/bm25_scorer.py`**

```python
"""BM25ScorerStep — normalize FTS5 rank into positive relevance scores.

Single responsibility: flip the sign of FTS5's negative BM25 rank so
``relevance`` is a positive "higher is better" score that the rest of
the pipeline (TopKFilterStep, etc.) can sort on.

Future B3.1 ``DenseScorerStep`` will operate in the same shape: read
candidates, assign normalized scores, write candidates back.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.models import Chunk, ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep


@dataclass(frozen=True, slots=True)
class BM25ScorerStep(RetrieverStep):
    """Score normalization step for chunk pipelines."""

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        # Member candidates aren't BM25-scorable (LIKE doesn't produce ranks).
        if isinstance(state.candidates, ModuleMemberList):
            return state
        # ChunkList: flip the sign of FTS5's negative BM25 rank.
        new_items = tuple(
            Chunk(
                package=c.package,
                kind=c.kind,
                title=c.title,
                text=c.text,
                source_path=c.source_path,
                module=c.module,
                metadata=c.metadata,
                relevance=(-c.relevance) if c.relevance is not None else None,
            )
            for c in state.candidates.items
        )
        return replace(state, candidates=ChunkList(items=new_items))
```

- [ ] **Step 8: Run tests to verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_bm25_scorer.py -v 2>&1 | tail -10
```

Expected: 4 tests passing.

- [ ] **Step 9: Write failing tests for `TopKFilterStep`**

Create `tests/retrieval/steps/test_top_k_filter.py`:

```python
"""TopKFilterStep tests — uniform top-K cutoff for chunks and members."""
from __future__ import annotations

import pytest

from pydocs_mcp.models import (
    Chunk, ChunkList, ModuleMember, ModuleMemberList, SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep


def _chunks_with_relevance(*rels: float) -> ChunkList:
    return ChunkList(items=tuple(
        Chunk(package="d", kind="api", title=f"t{i}", text=f"f{i}",
              source_path="m.py", module="d.m", metadata={}, relevance=r)
        for i, r in enumerate(rels)
    ))


def _state(candidates) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=candidates,
    )


@pytest.mark.asyncio
async def test_topk_sorts_chunks_by_relevance_desc() -> None:
    step = TopKFilterStep(name="topk", k=3)
    out = await step.run(_state(_chunks_with_relevance(0.5, 1.5, 0.8, 2.0)))
    assert out.candidates is not None
    rels = [c.relevance for c in out.candidates.items]
    assert rels == [2.0, 1.5, 0.8]


@pytest.mark.asyncio
async def test_topk_caps_to_k() -> None:
    step = TopKFilterStep(name="topk", k=2)
    out = await step.run(_state(_chunks_with_relevance(1.0, 0.5, 0.2)))
    assert out.candidates is not None
    assert len(out.candidates.items) == 2


@pytest.mark.asyncio
async def test_topk_fallback_to_source_order_when_no_relevance() -> None:
    """If candidates have no relevance set, keep source order, take first K."""
    chunks = ChunkList(items=tuple(
        Chunk(package="d", kind="api", title=f"t{i}", text=f"f{i}",
              source_path="m.py", module="d.m", metadata={}, relevance=None)
        for i in range(5)
    ))
    step = TopKFilterStep(name="topk", k=3)
    out = await step.run(_state(chunks))
    assert out.candidates is not None
    titles = [c.title for c in out.candidates.items]
    assert titles == ["t0", "t1", "t2"]


@pytest.mark.asyncio
async def test_topk_works_on_members() -> None:
    members = ModuleMemberList(items=tuple(
        ModuleMember(
            package="d", module="m", name=f"f{i}", kind="function",
            signature=f"f{i}()", docstring=None, metadata={}, relevance=None,
        )
        for i in range(5)
    ))
    step = TopKFilterStep(name="topk", k=2)
    out = await step.run(_state(members))
    assert isinstance(out.candidates, ModuleMemberList)
    assert len(out.candidates.items) == 2


@pytest.mark.asyncio
async def test_topk_no_op_on_none_candidates() -> None:
    step = TopKFilterStep(name="topk", k=10)
    out = await step.run(_state(None))
    assert out.candidates is None
```

- [ ] **Step 10: Run tests to verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_top_k_filter.py -v 2>&1 | tail -10
```

Expected: `ImportError`.

- [ ] **Step 11: Implement `python/pydocs_mcp/retrieval/steps/top_k_filter.py`**

```python
"""TopKFilterStep — uniform top-K cutoff for chunk and member pipelines.

Single responsibility: keep the top K candidates by ``relevance``
descending. If no candidate has a relevance set (e.g., no scorer ran
upstream), falls back to source order.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep


@dataclass(frozen=True, slots=True)
class TopKFilterStep(RetrieverStep):
    """Top-K cutoff step. Works uniformly for chunks and members."""
    k: int = 50

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        items = state.candidates.items
        if not items:
            return state
        # Sort by relevance desc if any candidate has it set; else source order.
        has_relevance = any(getattr(c, "relevance", None) is not None for c in items)
        if has_relevance:
            sorted_items = tuple(
                sorted(items, key=lambda c: c.relevance or 0.0, reverse=True)
            )
        else:
            sorted_items = tuple(items)
        new_items = sorted_items[: self.k]
        if isinstance(state.candidates, ChunkList):
            return replace(state, candidates=ChunkList(items=new_items))
        if isinstance(state.candidates, ModuleMemberList):
            return replace(state, candidates=ModuleMemberList(items=new_items))
        return state
```

- [ ] **Step 12: Run tests to verify PASS**

```bash
.venv/bin/pytest tests/retrieval/steps/test_top_k_filter.py -v 2>&1 | tail -10
```

Expected: 5 tests passing.

- [ ] **Step 13: Wire ChunkRetrievalStep (the adapter) to use the new three-step chain internally**

`ChunkRetrievalStep` currently wraps a `Bm25ChunkRetriever`. We can't delete it yet (Task 7 does that — services still call it). But we can rewrite its `async run` body to delegate to the new three steps:

Edit `python/pydocs_mcp/retrieval/steps/chunk_retrieval.py`:

```python
"""ChunkRetrievalStep — TEMPORARY adapter wiring the legacy retriever
interface to the new decomposed steps. Removed in Task 7."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.bm25_scorer import BM25ScorerStep
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep
from pydocs_mcp.retrieval.protocols import ConnectionProvider


@dataclass(frozen=True, slots=True)
class ChunkRetrievalStep(RetrieverStep):
    """Temporary adapter: composes the three-step chain so existing
    YAML/pipelines keep working while Tasks 5–8 migrate consumers.
    Removed in Task 7."""
    provider: ConnectionProvider
    limit: int = 50

    async def run(self, state: RetrieverState) -> RetrieverState:
        fetch = ChunkFetcherStep(name="_fetch_inner", provider=self.provider, limit=self.limit)
        score = BM25ScorerStep(name="_score_inner")
        topk = TopKFilterStep(name="_topk_inner", k=self.limit)
        state = await fetch.run(state)
        state = await score.run(state)
        state = await topk.run(state)
        return state
```

- [ ] **Step 14: Update `steps/__init__.py` exports**

Add the three new steps:

```python
from pydocs_mcp.retrieval.steps.bm25_scorer import BM25ScorerStep
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep
```

Add them to `__all__`.

- [ ] **Step 15: Run full pytest**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: pre-refactor count + 12 new tests (3 fetcher + 4 scorer + 5 topk). All passing.

- [ ] **Step 16: Verify RepoQA real baseline unchanged (AC17 spot-check)**

```bash
sha256sum benchmarks/baselines/repoqa_snf.json
diff <(cat /tmp/pre_refactor_baseline.sha256) <(sha256sum benchmarks/baselines/repoqa_snf.json)
```

Expected: empty diff — baseline unchanged.

- [ ] **Step 17: Ruff clean**

```bash
.venv/bin/ruff check python/ tests/
```

Expected: `All checks passed!`

- [ ] **Step 18: Commit**

```bash
git add -A
git commit -m "feat(retrieval): split Bm25ChunkRetriever into ChunkFetcherStep + BM25ScorerStep + TopKFilterStep

Decomposes the monolithic Bm25ChunkRetriever (which did fetch + score
+ cutoff in one class) into three single-responsibility steps:

- ChunkFetcherStep — issues SQLite FTS5 MATCH, returns N candidates
  with raw FTS5 rank captured as relevance.
- BM25ScorerStep — flips the sign of FTS5's negative rank so
  'higher = better' (the convention TopKFilter assumes). Future
  B3.1 DenseScorerStep composes in the same shape.
- TopKFilterStep — keeps top K by relevance descending; falls back
  to source order if no scorer ran upstream. Works for chunks AND
  members uniformly.

ChunkRetrievalStep is rewired as a temporary adapter composing the
three-step chain so YAML pipelines continue working unchanged until
Task 7 migrates services to consume RetrieverPipeline directly.

Bm25ChunkRetriever itself stays alive in retrievers/ — it's still
imported by ChunkRetrievalStep until Task 9's final cleanup.

Spec §2.4."
```

---

## Task 5: `LikeModuleMemberRetriever` → `MemberFetcherStep`

**Goal:** mirror Task 4's decomposition for the member side, except there's no scoring step (LIKE doesn't produce ranks).

**Files:**
- Read: `python/pydocs_mcp/retrieval/retrievers/like_member.py`
- Create: `python/pydocs_mcp/retrieval/steps/member_fetcher.py`
- Create: `tests/retrieval/steps/test_member_fetcher.py`
- Modify: `python/pydocs_mcp/retrieval/steps/module_member_retrieval.py` (rewire as temp adapter)
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py`

- [ ] **Step 1: Write failing tests for `MemberFetcherStep`**

Create `tests/retrieval/steps/test_member_fetcher.py`:

```python
"""MemberFetcherStep tests — LIKE-based member candidate generation."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.pipeline_legacy import PerCallConnectionProvider
from pydocs_mcp.retrieval.steps.member_fetcher import MemberFetcherStep


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    from pydocs_mcp.db import open_index_database
    db_path = tmp_path / "fixtures.db"
    open_index_database(db_path).close()
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO packages (name) VALUES ('demo')")
    conn.execute(
        "INSERT INTO module_members "
        "(package, module, name, kind, signature, docstring, metadata_json) "
        "VALUES ('demo', 'demo.m', 'add_one', 'function', 'add_one(n: int) -> int', "
        "'Add one to n.', '{}')"
    )
    conn.execute(
        "INSERT INTO module_members "
        "(package, module, name, kind, signature, docstring, metadata_json) "
        "VALUES ('demo', 'demo.m', 'subtract', 'function', 'subtract(a, b)', "
        "'Subtract b from a.', '{}')"
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.mark.asyncio
async def test_member_fetcher_returns_matching_members(populated_db: Path) -> None:
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert out.candidates is not None
    assert len(out.candidates.items) >= 1
    assert any("add" in m.name for m in out.candidates.items)


@pytest.mark.asyncio
async def test_member_fetcher_respects_limit(populated_db: Path) -> None:
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=1)
    state = RetrieverState(query=SearchQuery(terms="a", max_results=10))
    out = await step.run(state)
    assert out.candidates is not None
    assert len(out.candidates.items) <= 1
```

- [ ] **Step 2: Run tests to verify FAIL**

```bash
.venv/bin/pytest tests/retrieval/steps/test_member_fetcher.py -v 2>&1 | tail -10
```

- [ ] **Step 3: Implement `member_fetcher.py`**

Read `python/pydocs_mcp/retrieval/retrievers/like_member.py` for the canonical LIKE SQL, then write `python/pydocs_mcp/retrieval/steps/member_fetcher.py` mirroring `ChunkFetcherStep`'s shape but with LIKE instead of FTS5 MATCH. Returns `ModuleMemberList` candidates, no relevance set (LIKE doesn't score).

(Full code mirrors `chunk_fetcher.py` structure; substitute the SQL query for the LIKE-based one from `retrievers/like_member.py`.)

- [ ] **Step 4: Verify PASS + rewire adapter**

Run tests. Then rewire `module_member_retrieval.py` as a temporary adapter (mirror Task 4 Step 13).

- [ ] **Step 5: Update `steps/__init__.py` + run full pytest + commit**

Commit message:

```
refactor(retrieval): LikeModuleMemberRetriever → MemberFetcherStep

Mirrors Task 4's decomposition for members. No scorer step — LIKE
doesn't produce relevance ranks. ModuleMemberRetrievalStep is rewired
as a temp adapter; deleted in Task 7.

Spec §2.4.
```

---

## Task 6: `RouteStep` + nested `RetrieverPipeline`s replace `SubPipelineStep`

**Goal:** delete `SubPipelineStep` (the `*Step`-renamed legacy). Since `RetrieverPipeline` IS a `RetrieverStep`, nested pipelines can be used directly inside `RouteStep` or as steps in a parent pipeline.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/steps/route.py` (refactor RouteStep to hold `tuple[tuple[Predicate, RetrieverPipeline], ...]`)
- Modify: `python/pydocs_mcp/retrieval/config.py` (replace `SubPipelineStep(pipeline=sub)` construction with direct nested pipeline usage)
- Delete: `python/pydocs_mcp/retrieval/steps/sub_pipeline.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` (drop `SubPipelineStep` export)
- Modify: existing tests that reference `SubPipelineStep`

- [ ] **Step 1: Survey current `RouteStep` shape**

```bash
cat python/pydocs_mcp/retrieval/steps/route.py
```

Note current `RouteCase` shape — likely already holds a sub-pipeline reference.

- [ ] **Step 2: Write failing test pinning new behavior**

Add to `tests/retrieval/pipeline/test_pipeline_base.py`:

```python
@pytest.mark.asyncio
async def test_nested_pipeline_as_step() -> None:
    """A RetrieverPipeline can be used directly as a step in a parent
    pipeline — no SubPipelineStep adapter needed."""
    inner = RetrieverPipeline(
        name="inner",
        steps=(("a", _BumpStep(name="a")),),
    )
    # Use inner DIRECTLY as a step (not wrapped in SubPipelineStep)
    outer = RetrieverPipeline(
        name="outer",
        steps=(
            ("nested", inner),
            ("b", _BumpStep(name="b")),
        ),
    )
    out = await outer.run(RetrieverState(query=_query()))
    assert out.duration_ms == 2.0  # a (1) + b (1)
```

- [ ] **Step 3: Verify test passes (Pipeline IS a Step — already works from Task 1)**

Run; should already be passing.

- [ ] **Step 4: Refactor `config.py`**

Find the `SubPipelineStep(pipeline=...)` construction (around line 474 of `config.py`). Replace with direct nested-pipeline usage.

- [ ] **Step 5: Delete `sub_pipeline.py`**

```bash
git rm python/pydocs_mcp/retrieval/steps/sub_pipeline.py
```

- [ ] **Step 6: Update `steps/__init__.py`**

Remove `from pydocs_mcp.retrieval.steps.sub_pipeline import SubPipelineStep` and drop it from `__all__`.

- [ ] **Step 7: Find and fix remaining `SubPipelineStep` references**

```bash
grep -rln "SubPipelineStep" python/ tests/ benchmarks/src/ 2>/dev/null
```

For each hit, replace with direct nested-pipeline usage.

- [ ] **Step 8: Run pytest + ruff + commit**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
.venv/bin/ruff check python/ tests/
git add -A
git commit -m "refactor(retrieval): RouteStep + nested RetrieverPipelines replace SubPipelineStep

RetrieverPipeline IS a RetrieverStep (Pipeline-is-a-Step composition),
so nested pipelines can be used directly inside RouteStep cases or as
steps in a parent pipeline. SubPipelineStep adapter deleted.

Spec §2.2."
```

---

## Task 7: Services consume `RetrieverPipeline` directly, drop retriever adapters

**Goal:** `DocsSearch` / `ApiSearch` services take a `RetrieverPipeline` directly. The reverse-Inception `PipelineChunkRetriever` / `PipelineMemberRetriever` adapters are deleted, along with `ChunkRetrievalStep` / `ModuleMemberRetrievalStep` adapter steps.

**Files:**
- Modify: `python/pydocs_mcp/application/docs_search.py` (or wherever DocsSearch lives)
- Modify: `python/pydocs_mcp/application/api_search.py` (or equivalent)
- Modify: `python/pydocs_mcp/storage/factories.py` (composition root — builds RetrieverPipeline)
- Delete: `python/pydocs_mcp/retrieval/retrievers/pipeline_chunk.py`
- Delete: `python/pydocs_mcp/retrieval/retrievers/pipeline_member.py`
- Delete: `python/pydocs_mcp/retrieval/steps/chunk_retrieval.py`
- Delete: `python/pydocs_mcp/retrieval/steps/module_member_retrieval.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py`
- Modify: `python/pydocs_mcp/retrieval/retrievers/__init__.py`
- Update: existing tests for DocsSearch / ApiSearch

- [ ] **Step 1: Survey DocsSearch's current shape**

```bash
grep -rn "class DocsSearch\|class ApiSearch" python/pydocs_mcp/application/
```

Read each file. Note: which retriever they take, how `search()` is implemented.

- [ ] **Step 2: Survey composition roots**

```bash
grep -rn "PipelineChunkRetriever\|PipelineMemberRetriever" python/ 2>/dev/null | grep -v __pycache__
```

Note every construction site.

- [ ] **Step 3: Rewrite DocsSearch**

```python
# python/pydocs_mcp/application/docs_search.py
from dataclasses import dataclass

from pydocs_mcp.models import ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverPipeline, RetrieverState


@dataclass(frozen=True, slots=True)
class DocsSearch:
    chunk_pipeline: RetrieverPipeline

    async def search(self, query: SearchQuery) -> ChunkList:
        state = await self.chunk_pipeline.run(RetrieverState(query=query))
        if isinstance(state.result, ChunkList):
            return state.result
        if isinstance(state.candidates, ChunkList):
            return state.candidates
        return ChunkList(items=())
```

Mirror for `ApiSearch` with `MemberList`/`ModuleMemberList`.

- [ ] **Step 4: Update composition roots**

`storage/factories.py`'s `build_sqlite_lookup_service` / equivalent — find where `PipelineChunkRetriever` is constructed; replace with direct `RetrieverPipeline` construction from `AppConfig`. The YAML loader is updated in Task 8; for now, factories can build the pipeline from config the same way the legacy retriever did internally.

- [ ] **Step 5: Delete files**

```bash
git rm python/pydocs_mcp/retrieval/retrievers/pipeline_chunk.py
git rm python/pydocs_mcp/retrieval/retrievers/pipeline_member.py
git rm python/pydocs_mcp/retrieval/steps/chunk_retrieval.py
git rm python/pydocs_mcp/retrieval/steps/module_member_retrieval.py
```

- [ ] **Step 6: Update exports**

Remove `ChunkRetrievalStep` + `ModuleMemberRetrievalStep` from `steps/__init__.py`. Remove `PipelineChunkRetriever` + `PipelineMemberRetriever` from `retrievers/__init__.py`.

- [ ] **Step 7: Update tests**

Tests that construct `DocsSearch(chunk_retriever=...)` need updating to `DocsSearch(chunk_pipeline=...)`. Find them:

```bash
grep -rln "DocsSearch\|ApiSearch" tests/ 2>/dev/null | head
```

For each test, replace the retriever construction with a `RetrieverPipeline`.

- [ ] **Step 8: Run full pytest**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
```

Expected: passing count adjusted for deleted tests + still-passing. No new failures.

- [ ] **Step 9: Verify RepoQA real baseline unchanged**

```bash
diff <(cat /tmp/pre_refactor_baseline.sha256) <(sha256sum benchmarks/baselines/repoqa_snf.json)
```

- [ ] **Step 10: Ruff + commit**

```bash
.venv/bin/ruff check python/ tests/
git add -A
git commit -m "refactor(retrieval): services consume RetrieverPipeline directly, drop retriever adapters

DocsSearch / ApiSearch take chunk_pipeline / api_pipeline (RetrieverPipeline)
instead of a Retriever Protocol. Same data flow, one less abstraction
layer.

Deleted:
- retrievers/pipeline_chunk.py (PipelineChunkRetriever)
- retrievers/pipeline_member.py (PipelineMemberRetriever)
- steps/chunk_retrieval.py (ChunkRetrievalStep adapter)
- steps/module_member_retrieval.py (ModuleMemberRetrievalStep adapter)

Composition roots updated to build RetrieverPipelines directly.

Spec §5."
```

---

## Task 8: YAML loader — `steps:` with `name:` per step; reject `stages:`

**Goal:** flip the YAML loader to the new shape. Old `stages:` key returns a clear `PipelineLoadError`. All shipped YAML files migrated.

**Files:**
- Modify: `python/pydocs_mcp/retrieval/serialization.py` (loader + registry)
- Modify: `python/pydocs_mcp/pipelines/chunk_search.yaml`
- Modify: `python/pydocs_mcp/pipelines/member_search.yaml`
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (only if it inlines retrieval pipelines)
- Modify: `benchmarks/configs/baseline.yaml` (if it overrides chunk/member pipelines)
- Create: `tests/retrieval/test_serialization_step_schema.py`

- [ ] **Step 1: Write failing test for old `stages:` rejection**

Create `tests/retrieval/test_serialization_step_schema.py`:

```python
"""Pipeline YAML loader: new `steps:` schema; reject old `stages:`."""
from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.serialization import PipelineLoadError, load_pipeline_yaml


def test_old_stages_key_rejected_with_clear_message() -> None:
    yaml_str = """
name: legacy_pipeline
stages:
  - type: token_budget
    params: {max_tokens: 100}
"""
    with pytest.raises(PipelineLoadError, match="'stages:' key is no longer accepted"):
        load_pipeline_yaml(yaml_str)


def test_new_steps_key_loads_cleanly() -> None:
    yaml_str = """
name: new_pipeline
steps:
  - name: budget
    type: token_budget
    params: {max_tokens: 100}
"""
    pipeline = load_pipeline_yaml(yaml_str)
    assert pipeline.name == "new_pipeline"
    assert pipeline.step_names == ("budget",)


def test_step_missing_name_field_raises() -> None:
    yaml_str = """
name: bad
steps:
  - type: token_budget
    params: {max_tokens: 100}
"""
    with pytest.raises(PipelineLoadError, match="missing 'name'"):
        load_pipeline_yaml(yaml_str)
```

- [ ] **Step 2: Run tests to verify FAIL**

Expected: ImportError (or similar — `load_pipeline_yaml` and `PipelineLoadError` may not exist yet).

- [ ] **Step 3: Refactor `serialization.py`**

Add `PipelineLoadError`. Add the new loader entry point. Old loader path either renamed or removed. Key changes:
- YAML reads `steps:` (not `stages:`)
- Each step entry has `name:` + `type:` + `params:`
- Loader rejects `stages:` with the spec §4 error message

- [ ] **Step 4: Migrate `pipelines/chunk_search.yaml`**

```yaml
name: chunk_search
steps:
  - name: fetch
    type: chunk_fetcher
    params:
      limit: 200
  - name: score
    type: bm25_scorer
    params: {}
  - name: topk
    type: top_k_filter
    params:
      k: 50
  - name: budget
    type: token_budget
    params:
      max_tokens: 2000
```

- [ ] **Step 5: Migrate `pipelines/member_search.yaml`**

```yaml
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

- [ ] **Step 6: Audit + migrate any benchmark configs**

```bash
grep -rn "stages:" benchmarks/configs/ python/pydocs_mcp/defaults/ 2>/dev/null
```

Migrate each to `steps:` with `name:`.

- [ ] **Step 7: Run pytest + verify baseline + commit**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
diff <(cat /tmp/pre_refactor_baseline.sha256) <(sha256sum benchmarks/baselines/repoqa_snf.json)
```

Commit:

```
refactor(retrieval): YAML loader reads `steps:` with `name:`, rejects `stages:`

New canonical YAML shape:

    name: chunk_search
    steps:
      - name: fetch
        type: chunk_fetcher
        params: {limit: 200}
      - name: score
        type: bm25_scorer
        params: {}
      ...

Loader rejects old `stages:` key with:
'stages:' key is no longer accepted (retrieval-pipeline-refactor).
Use 'steps:' with a 'name:' per step.

All shipped pipelines/*.yaml + benchmark configs migrated.
No backward compat by spec design decision (clean reject).

Spec §4.
```

---

## Task 9: Final cleanup — delete `retrievers/`, slim `protocols.py`, delete `pipeline_legacy.py`

**Goal:** remove the corpse of the old abstractions now that nothing depends on them.

**Files:**
- Delete: `python/pydocs_mcp/retrieval/retrievers/` (entire directory)
- Delete: `python/pydocs_mcp/retrieval/pipeline_legacy.py`
- Modify: `python/pydocs_mcp/retrieval/protocols.py` (remove `Retriever`, `ChunkRetriever`, `ModuleMemberRetriever`, `PipelineStage`)
- Delete: `python/pydocs_mcp/retrieval/steps/base_stage.py`
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` (drop `PipelineStage` re-export)

- [ ] **Step 1: Verify nothing imports the doomed names**

```bash
grep -rn "from pydocs_mcp.retrieval.retrievers\|from pydocs_mcp.retrieval.pipeline_legacy\|PipelineStage\|class Retriever\|class ChunkRetriever\|class ModuleMemberRetriever" python/ tests/ benchmarks/src/ 2>/dev/null | grep -v __pycache__
```

Expected: only legitimate `IngestionStage` hits (extraction-side) — no `PipelineStage` / `Retriever*` hits remain anywhere.

If any hits remain, investigate and either migrate or document why before continuing.

- [ ] **Step 2: Delete the files**

```bash
git rm -r python/pydocs_mcp/retrieval/retrievers/
git rm python/pydocs_mcp/retrieval/pipeline_legacy.py
git rm python/pydocs_mcp/retrieval/steps/base_stage.py
```

- [ ] **Step 3: Slim `protocols.py`**

```python
"""Retrieval-pipeline protocols — only the cross-cutting structural types remain."""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from pydocs_mcp.models import Chunk, ModuleMember


@runtime_checkable
class ConnectionProvider(Protocol):
    """Yields a SQLite connection scoped to a single operation."""
    def acquire(self) -> AsyncIterator[sqlite3.Connection]: ...


@runtime_checkable
class ResultFormatter(Protocol):
    """Renders one result (Chunk or ModuleMember) as a string payload."""
    def format(self, result: Chunk | ModuleMember) -> str: ...
```

- [ ] **Step 4: Update `steps/__init__.py`**

Remove `from pydocs_mcp.retrieval.steps.base_stage import PipelineStage` and drop `PipelineStage` from `__all__`.

- [ ] **Step 5: Run pytest + ruff + commit**

```bash
.venv/bin/pytest -q 2>&1 | tail -3
.venv/bin/ruff check python/ tests/ benchmarks/
```

Commit:

```
chore(retrieval): delete retrievers/ + pipeline_legacy.py + slim protocols.py

Final corpse-removal. After Tasks 1–8, nothing depends on:
- retrievers/* (entire directory)
- pipeline_legacy.py (CodeRetrieverPipeline + old PipelineState)
- protocols.py::Retriever / ChunkRetriever / ModuleMemberRetriever / PipelineStage
- steps/base_stage.py (was just a PipelineStage re-export)

protocols.py now holds only ConnectionProvider + ResultFormatter
(cross-cutting structural types still in use by steps and storage).

Spec §3 'Files deleted'.
```

---

## Task 10: Update `CLAUDE.md` architecture section

**Goal:** docs reflect the new layout. New devs landing on CLAUDE.md see `RetrieverStep` / `RetrieverPipeline` as the canonical abstractions.

**Files:**
- Modify: `CLAUDE.md` (architecture diagram + retrieval-related prose)

- [ ] **Step 1: Update the architecture diagram**

Find the `retrieval/` line in the architecture ASCII tree (around line 70 of `CLAUDE.md`):

```
├── retrieval/     # Async pipelines, retrievers, stages, registries, YAML config
```

Replace with:

```
├── retrieval/     # Async RetrieverPipeline + RetrieverStep ABC; one file per step; YAML config
```

- [ ] **Step 2: Update the §"Single Responsibility" paragraph**

Find the paragraph mentioning `retrieval/` (under SOLID Principles). Update phrasing:

```
... retrieval/ owns the pipeline machinery (RetrieverStep + RetrieverPipeline + concrete steps under steps/), ...
```

- [ ] **Step 3: Add a callout about the naming convention vs IngestionStage**

After the architecture diagram, add a short note:

```
**Naming: retrieval vs ingestion pipelines** — `retrieval/` uses `RetrieverStep` (ABC) and `RetrieverPipeline`. `extraction/` uses `IngestionStage` (Protocol) and `IngestionPipeline`. Two different pipelines, two different abstractions; don't confuse them. A future PR may rename `IngestionStage` → `IngestionStep` for symmetry.
```

- [ ] **Step 4: Update §"Key Technical Details"**

The line:

```
- `retrieval/` uses a uniform `PipelineStage` protocol + compound stages (`RouteStage`, `SubPipelineStage`) for composition
```

Replace with:

```
- `retrieval/` uses a uniform `RetrieverStep` ABC + composable `RetrieverPipeline` (Pipeline IS a Step, so sub-pipelines compose directly without a SubPipelineStep adapter)
```

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md architecture for RetrieverStep / RetrieverPipeline

Reflects the post-refactor retrieval layer:
- RetrieverStep ABC replaces PipelineStage Protocol
- RetrieverPipeline replaces CodeRetrieverPipeline
- retrieval/steps/ replaces retrieval/stages/ + retrieval/retrievers/
- Pipeline IS a Step composition replaces SubPipelineStage adapter

Adds a callout about retrieval-vs-ingestion pipeline naming so a new
contributor doesn't confuse RetrieverStep with IngestionStage.

Spec §3 + §9 'Future: ingestion-side rename'."
```

---

## Final verification gauntlet

After Task 10 lands:

- [ ] **Step 1: Full pytest suite**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/pipeline-refactor
.venv/bin/pytest -q 2>&1 | tail -3
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3
```

Expected: pre-refactor count + ~30 new tests (11 from Task 1 pipeline-base + 12 from Task 4 step tests + 3 member tests + 4 YAML tests). No regressions.

- [ ] **Step 2: Ruff clean**

```bash
.venv/bin/ruff check python/ benchmarks/ tests/
```

- [ ] **Step 3: AC17 — RepoQA baseline unchanged**

```bash
diff <(cat /tmp/pre_refactor_baseline.sha256) <(sha256sum benchmarks/baselines/repoqa_snf.json)
```

Expected: empty (no behavior change).

- [ ] **Step 4: Stale-name grep — all return empty**

```bash
grep -RIn "class PipelineStage\b" python/ tests/ benchmarks/src/ --include="*.py"
grep -RIn "class Retriever\b\|class ChunkRetriever\b\|class ModuleMemberRetriever\b" python/ tests/ benchmarks/src/ --include="*.py"
grep -RIn "class .*Stage\b" python/pydocs_mcp/retrieval/ --include="*.py"  # IngestionStage not in this path
grep -RIn "from pydocs_mcp.retrieval.retrievers\|from pydocs_mcp.retrieval.pipeline_legacy\|from pydocs_mcp.retrieval.stages" python/ tests/ benchmarks/src/ --include="*.py"
```

All four greps must return empty.

- [ ] **Step 5: Smoke-import the new public API**

```bash
.venv/bin/python -c "
from pydocs_mcp.retrieval.pipeline import RetrieverPipeline, RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps import (
    BM25ScorerStep, ChunkFetcherStep, MemberFetcherStep, TopKFilterStep,
    ConditionalStep, LimitStep, MetadataPostFilterStep, ParallelStep,
    RRFStep, RouteCase, RouteStep, TokenBudgetStep,
)
print('all new symbols importable')
"
```

Expected: `all new symbols importable`.

- [ ] **Step 6: Push + open PR**

```bash
git push -u origin feature/pipeline-refactor
gh pr create --draft --title "Retrieval pipeline refactor — RetrieverStep / RetrieverPipeline" --body "$(cat <<'EOF'
## Summary

Replaces the retrieval layer's two parallel hierarchies (`Retriever` Protocol + `PipelineStage` Protocol with adapter stages between them) with a single sklearn-style abstraction: `RetrieverStep` ABC + `RetrieverPipeline` with named, addressable steps.

Decomposes the monolithic `Bm25ChunkRetriever` into three single-responsibility steps:
- `ChunkFetcherStep` — candidate generation
- `BM25ScorerStep` — score normalization
- `TopKFilterStep` — top-K cutoff (shared with member pipelines)

Naming: `RetrieverStep` (not `Stage`) differentiates from extraction-side `IngestionStage`. Future PR may symmetric-rename.

Spec: docs/superpowers/specs/2026-05-21-retrieval-pipeline-refactor-design.md

## Test plan

- [ ] All pre-refactor tests pass
- [ ] ~30 new tests pass (pipeline base + decomposed steps + YAML schema)
- [ ] RepoQA real baseline at benchmarks/baselines/repoqa_snf.json unchanged
- [ ] CI green on python 3.11/3.12/3.13 + rust + benchmark-repoqa

## Out of scope (separate specs)

- PR-A: chunk_search_ranked.yaml preset (drop TokenBudgetStep for benchmarks)
- PR-B3.1: pluggable embeddings (OpenAI + FastEmbed) + DenseScorerStep
- PR-B3.2: pluggable reranker (Cohere) + RerankerStep
EOF
)"
```

- [ ] **Step 7: Watch CI**

```bash
gh pr checks --watch
```

Expected: all CI jobs green.

---

## Reused helpers / patterns

- `dataclasses.replace` for immutable state updates — every step uses this.
- `ConnectionProvider` Protocol stays in `retrieval/protocols.py` — unchanged by this refactor.
- `tests/_fakes.py` shared fakes — services that take a `RetrieverPipeline` will accept a small test-only `_RecordingPipeline` for service tests.
- `git mv` preserves rename history for `stages/` → `steps/` and individual file renames.

## Verification gates (post-task summary)

Every task ends with `pytest -q` showing the expected passing count. The PR is reviewable in three independent slices:

1. **Foundations** (Tasks 1–3): new ABC + rename + ABC subclassing. Pure plumbing; no behavior change.
2. **Decomposition** (Tasks 4–6): split BM25 monolith; replace SubPipelineStep with nested Pipelines. Internal-only.
3. **Surface migration** (Tasks 7–9): services consume Pipelines; YAML schema flip; corpse removal.

After all 10 tasks land: dispatch the final code-reviewer subagent across the entire diff, surface findings for user approval, then take PR out of draft.
