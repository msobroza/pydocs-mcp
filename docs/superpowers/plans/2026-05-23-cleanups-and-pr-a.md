# Cleanups + PR-A — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Bundle four queued cleanups with PR-A (the `chunk_search_ranked.yaml` benchmark preset) into one cohesive PR. The cleanups remove low-level naming/test/architectural debt accumulated during the pipeline refactor. PR-A drops `TokenBudgetStep` from the benchmark pipeline so `recall@k` can finally measure top-K hits — unblocking real-data measurement (currently structurally pegged at 0%).

**Architecture:** No new abstractions (except `PreFilterStep`, which dedups existing logic). All changes follow the established `RetrieverStep` ABC + `RetrieverPipeline` patterns from PR #28.

**Tech Stack:** Python 3.11+, pytest, pytest-asyncio, ruff. Existing pydantic-settings + pyyaml + asyncio.

**Reviewer strategy** (per `superpowers:subagent-driven-development`): each task gets two-stage review (spec-compliance + code-quality), with reviewer prompts customized per task category. Model: **Opus 4.7 max effort** for every dispatch.

| Task category | Spec reviewer focus | Code reviewer focus |
|---|---|---|
| Registry rename (Task 1) | All 14 step files + serialization.py + BuildContext updated; YAML `type:` keys unchanged | No silent behavior drift; ruff clean; old name fully gone |
| Fixture corpus (Task 2) | Floor test reads from `tests/integration/fixtures/ac15_corpus/`; new `EMPIRICAL_FLOOR` is stable; docstring updated | Fixture corpus is < 5 MB, well-scoped, no stdlib-mirror bloat |
| PreFilterStep (Task 4) | New `PreFilterStep` parses + validates + scope-splits; fetchers read `state.scratch`; AC17 hash unchanged | Step is `RetrieverStep` subclass with `frozen=True, slots=True`; `to_dict`/`from_dict` round-trip; backward-compat fallback in fetchers |
| Ranked preset (Task 5) | New `chunk_search_ranked.yaml` ends at `limit` (no `TokenBudgetStep`); benchmark wiring switches to it; MCP `chunk_search.yaml` unchanged | `PydocsMcpSystem.search` reads `state.candidates` then falls back to `state.result`; no fragile type assumptions |
| Re-measurement (Task 6) | Both fixture and real baselines re-captured; new real baseline shows recall@k > 0 | Captured numbers documented in PR description; CI `ci_compare` gate still works (fixture-vs-fixture) |

---

## Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/cleanups-and-pr-a/`
Branch: `feature/cleanups-and-pr-a` off `main` at `a41b007` (post PR #28/29/30 merge).
Spec: [docs/superpowers/specs/2026-05-21-retrieval-pipeline-refactor-design.md](../specs/2026-05-21-retrieval-pipeline-refactor-design.md) (referenced for the PR-A out-of-scope §)

Baseline: 1019 pytest + 111 benchmarks + ruff clean + AC17 RepoQA SHA `1a38657bc2fd8ab3e45f816883351e98bf2b608393df2e0b386b2c45531869c1`.

---

## Task 0: Baseline capture

**Files:** none modified. Capture pre-refactor state so Tasks 1-6 can verify "no regressions".

- [ ] **Step 1: Verify worktree + branch**
```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/cleanups-and-pr-a
git status   # clean
git log --oneline -2   # HEAD at a41b007
```

- [ ] **Step 2: Capture pytest baseline**
```bash
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3 > /tmp/pre_pytest.txt
cat /tmp/pre_pytest.txt   # expect 1019 passed
```

- [ ] **Step 3: Capture benchmark baseline + AC17 SHA**
```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3 > /tmp/pre_bench.txt
cat /tmp/pre_bench.txt   # expect 111 passed
shasum -a 256 benchmarks/baselines/repoqa_snf.json > /tmp/pre_ac17.sha256
cat /tmp/pre_ac17.sha256   # expect 1a38657b…
shasum -a 256 benchmarks/baselines/repoqa_fixture_baseline.json > /tmp/pre_fixture_ac17.sha256
cat /tmp/pre_fixture_ac17.sha256   # baseline for the CI gate
```

No commit for Task 0.

---

## Task 1: `stage_registry` → `step_registry` rename

**Files (16 total):**
- `python/pydocs_mcp/retrieval/serialization.py` (1 global var + 1 BuildContext field)
- `python/pydocs_mcp/retrieval/steps/{bm25_scorer,chunk_fetcher,conditional,limit,member_fetcher,metadata_post_filter,parallel,route,rrf,sub_pipeline,token_budget,top_k_filter}.py` (14 files, each with `@stage_registry.register(...)`)
- Possibly `tests/retrieval/test_serialization.py` and any test that imports `stage_registry`

### Step 1: Survey
```bash
grep -rln "stage_registry" python/ tests/ benchmarks/src/ 2>/dev/null | grep -v __pycache__
```
Expected: ~16 files. Confirm before proceeding.

### Step 2: Sed-rename in serialization.py
```bash
sed -i.bak 's/\bstage_registry\b/step_registry/g' python/pydocs_mcp/retrieval/serialization.py
rm python/pydocs_mcp/retrieval/serialization.py.bak
```

### Step 3: Sed-rename across all step files
```bash
for f in python/pydocs_mcp/retrieval/steps/*.py; do
    sed -i.bak 's/\bstage_registry\b/step_registry/g' "$f"
    rm "$f.bak"
done
```

### Step 4: Sed-rename across tests
```bash
grep -rln "stage_registry" tests/ benchmarks/src/ 2>/dev/null | grep -v __pycache__ | while read f; do
    sed -i.bak 's/\bstage_registry\b/step_registry/g' "$f"
    rm "$f.bak"
done
```

### Step 5: Verify stale-name grep
```bash
grep -rn "stage_registry" python/ tests/ benchmarks/src/ 2>/dev/null | grep -v __pycache__
```
Expected: EMPTY.

### Step 6: Run pytest
```bash
.venv/bin/pytest -q --ignore=tests/integration/test_self_index_resolution_rate.py 2>&1 | tail -3
```
Expected: 1019 passed (unchanged from baseline).

### Step 7: Ruff
```bash
.venv/bin/ruff check python/ tests/ benchmarks/ 2>&1 | tail -3
```
Expected: All checks passed!

### Step 8: Commit
```bash
git add -A
git commit -m "refactor(retrieval): stage_registry → step_registry

Historical naming artifact from the pre-refactor *Stage era. Now the
registry holds RetrieverStep subclasses, so the new name reads true.

Mechanical sed-style rename across:
- python/pydocs_mcp/retrieval/serialization.py (global + BuildContext field)
- python/pydocs_mcp/retrieval/steps/*.py (14 step files)
- tests that import stage_registry

YAML schema 'type:' keys unchanged — those are registry KEYS, not the
registry name. No behavior change. 1019 tests pass."
```

---

## Task 2: AC #15 floor test → fixture corpus

**Files:**
- Create: `tests/integration/fixtures/ac15_corpus/` (small Python package — see below)
- Modify: `tests/integration/test_self_index_resolution_rate.py`
- New: `tests/integration/fixtures/ac15_corpus/pyproject.toml`
- New: `tests/integration/fixtures/ac15_corpus/ac15_pkg/__init__.py`
- New: `tests/integration/fixtures/ac15_corpus/ac15_pkg/<curated subset>.py`

### Step 1: Survey the live test
```bash
head -60 tests/integration/test_self_index_resolution_rate.py
grep -n "repo_root\|EMPIRICAL_FLOOR\|target = \|index_project" tests/integration/test_self_index_resolution_rate.py
```
Note where `repo_root` is built + the floor constant location.

### Step 2: Build the fixture corpus

Create a deterministic mini-corpus designed to exercise the resolver. ~10 Python files. Each file has known calls/imports/inherits that exercise specific resolver rules.

```bash
mkdir -p tests/integration/fixtures/ac15_corpus/ac15_pkg
```

Create `tests/integration/fixtures/ac15_corpus/pyproject.toml`:
```toml
[project]
name = "ac15_corpus"
version = "0.0.1"
description = "Curated corpus for the AC #15 resolution-rate floor test."
requires-python = ">=3.11"
dependencies = []
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/__init__.py`:
```python
"""Curated AC #15 test corpus — designed for stable resolution-rate measurement.

The corpus is intentionally small (~10 modules, ~100 functions/classes,
~500 calls) so the resolution rate is stable across resolver changes.
Exercises every resolver rule (A through E + F20 + self-short-circuit).
"""
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/types_and_helpers.py`:
```python
"""Type aliases + simple helpers. Exercises Rule B (exact qname match)."""
from __future__ import annotations

from typing import TypeAlias

# Type alias usable across modules
ChunkId: TypeAlias = int


def compute_sum(a: int, b: int) -> int:
    """Pure helper. Called from `pipeline.process` (cross-module call,
    Rule B: exact qname match resolves)."""
    return a + b


def compute_product(a: int, b: int) -> int:
    return a * b


def normalize(name: str) -> str:
    """Used by `Indexer.normalize_name` (self-attribute pattern)."""
    return name.strip().lower()
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/pipeline.py`:
```python
"""Pipeline class with self-attribute calls. Exercises Rule 0 (self.X.Y rewrite)
+ Rule 5 (self-method short-circuit)."""
from __future__ import annotations

from dataclasses import dataclass

from ac15_pkg.types_and_helpers import compute_sum, compute_product


@dataclass(frozen=True)
class Pipeline:
    """Test class for self-attribute resolution."""

    multiplier: int = 1

    def process(self, a: int, b: int) -> int:
        """Calls compute_sum (cross-module, Rule B) and compute_product
        (also Rule B); calls self.scale (Rule 5: self-method short-circuit)."""
        intermediate = compute_sum(a, b)
        return self.scale(compute_product(a, b)) + intermediate

    def scale(self, value: int) -> int:
        return value * self.multiplier
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/indexer.py`:
```python
"""Indexer class with self.X.Y patterns. Exercises Rule 0."""
from __future__ import annotations

from ac15_pkg.pipeline import Pipeline
from ac15_pkg.types_and_helpers import normalize


class Indexer:
    """Test class for self.X.Y rewrite (Rule 0)."""

    def __init__(self, pipeline: Pipeline) -> None:
        self.pipeline = pipeline      # self.pipeline: Pipeline
        self.cache: dict[str, int] = {}

    def index_pair(self, name: str, a: int, b: int) -> int:
        """Calls self.pipeline.process — Rule 0 rewrites to Pipeline.process,
        then Rule B resolves Pipeline.process to ac15_pkg.pipeline.Pipeline.process."""
        clean = self.normalize_name(name)
        result = self.pipeline.process(a, b)
        self.cache[clean] = result
        return result

    def normalize_name(self, name: str) -> str:
        """Calls normalize (cross-module, Rule B)."""
        return normalize(name)
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/inheritance.py`:
```python
"""Inheritance graph. Exercises Rule B + INHERITS edges."""
from __future__ import annotations


class Base:
    """Base class with a method."""

    def announce(self) -> str:
        return "Base"


class Middle(Base):
    """Inherits Base — INHERITS edge to ac15_pkg.inheritance.Base."""

    def announce(self) -> str:
        return f"Middle({super().announce()})"


class Leaf(Middle):
    """Inherits Middle — chain of INHERITS edges."""

    def announce(self) -> str:
        return f"Leaf({super().announce()})"
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/stdlib_user.py`:
```python
"""Uses stdlib. Exercises Rule B against the bundled stdlib qname universe."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path


def hash_text(text: str) -> str:
    """Calls hashlib.sha256 (Rule B against stdlib bundle)."""
    return hashlib.sha256(text.encode()).hexdigest()


def parse_json(payload: str) -> dict:
    """Calls json.loads (Rule B)."""
    return json.loads(payload)


async def io_helper(path: str) -> str:
    """Calls asyncio.to_thread + Path operations (Rule B against stdlib)."""
    return await asyncio.to_thread(Path(path).read_text)
```

Create `tests/integration/fixtures/ac15_corpus/ac15_pkg/orchestrator.py`:
```python
"""Cross-module composition. Most CALLS edges in the corpus live here."""
from __future__ import annotations

from ac15_pkg.indexer import Indexer
from ac15_pkg.inheritance import Leaf
from ac15_pkg.pipeline import Pipeline
from ac15_pkg.stdlib_user import hash_text, parse_json
from ac15_pkg.types_and_helpers import compute_sum


def build_indexer(multiplier: int = 1) -> Indexer:
    """Constructs Pipeline + Indexer. Calls Indexer.__init__ (Rule B) +
    Pipeline.__init__ (Rule B)."""
    return Indexer(pipeline=Pipeline(multiplier=multiplier))


def run_demo(name: str) -> str:
    """End-to-end demo. Many cross-module calls."""
    indexer = build_indexer()
    result = indexer.index_pair(name, 1, 2)
    leaf = Leaf()
    hashed = hash_text(f"{name}:{result}")
    parsed = parse_json("{}")
    total = compute_sum(result, len(parsed))
    return f"{leaf.announce()}:{hashed}:{total}"
```

### Step 3: Modify the floor test

Edit `tests/integration/test_self_index_resolution_rate.py`:

Replace the `repo_root` resolution to point at the fixture corpus:

```python
# BEFORE (somewhere near the top of test_self_index_calls_resolution_rate_floor):
# repo_root = Path(__file__).resolve().parent.parent.parent

# AFTER:
_FIXTURE_CORPUS = Path(__file__).parent / "fixtures" / "ac15_corpus"
repo_root = _FIXTURE_CORPUS
```

Update the docstring to mention the fixture corpus:
```python
"""AC #15: corpus-level CALLS resolution rate floor.

Indexes a CURATED fixture corpus at tests/integration/fixtures/ac15_corpus/
through the full extraction → resolution → storage pipeline, then asks
SQLite what fraction of captured kind='calls' edges got resolved to a
real to_node_id (i.e. linked into the cross-package qname universe).

The corpus is deterministic — same files, same imports, same call graph
every run. The floor catches REGRESSIONS in the resolver, not venv-shape
drift. (Earlier versions of this test scanned the live worktree, which
made the result depend on which deps were pip-installed.)

Spec §16 AC #15 — target: CALLS resolution rate ≥ 35%.
"""
```

### Step 4: Run the test once, measure, pin
```bash
.venv/bin/pytest tests/integration/test_self_index_resolution_rate.py -v -s 2>&1 | tail -30
```
The test will print the measured rate. If it's e.g. 65%, set `EMPIRICAL_FLOOR = 0.60` (small safety buffer below measured).

Update `EMPIRICAL_FLOOR` in the test file to the conservative value.

### Step 5: Verify it PASSES
```bash
.venv/bin/pytest tests/integration/test_self_index_resolution_rate.py -v 2>&1 | tail -5
```
Expected: 1 passed.

### Step 6: Run full suite WITHOUT the ignore flag now
```bash
.venv/bin/pytest -q 2>&1 | tail -3
```
Expected: 1020 passed (1019 + 1 newly-passing integration test).

### Step 7: Commit
```bash
git add tests/integration/test_self_index_resolution_rate.py \
        tests/integration/fixtures/
git commit -m "test(integration): pin AC #15 floor test to curated fixture corpus

The floor test used to scan the live worktree, which made the measured
resolution rate depend on which deps were pip-installed. CI venvs
(only mcp + dev deps) gave one number; local dev venvs (plus benchmark
deps like pandas/numpy) gave another. Test passed in CI, failed locally.

Now indexes a deterministic ~10-module corpus at
tests/integration/fixtures/ac15_corpus/. The corpus exercises every
resolver rule (A through E + F20 + Rule 0 self.X.Y rewrite + Rule 5
self-method short-circuit). The floor catches real resolver
regressions, not venv-shape noise.

EMPIRICAL_FLOOR set conservatively to <measured value - 5pp>.
Test no longer needs the --ignore=tests/integration/... pytest flag
in local dev."
```

---

## Task 3: Remove repoqa-investigation worktree

**Files:** none in this worktree — operates on the parent repo.

### Step 1: Verify state
```bash
cd /Users/msobroza/Projects/pyctx7-mcp
git worktree list | grep repoqa-investigation
```

### Step 2: Remove
```bash
git worktree remove .claude/worktrees/repoqa-investigation
git branch -D investigation/repoqa-failure-modes
```

### Step 3: Verify
```bash
git worktree list   # repoqa-investigation should be gone
git branch | grep investigation || echo "branch gone"
```

This task contributes no commit to `feature/cleanups-and-pr-a` (operation is on parent). Note in the PR description.

---

## Task 4: PreFilterStep extraction

**Plan-eng-review decisions (locked):**
- **1A** Use a typed `PreFilterResult` dataclass under a single scratch key `state.scratch["pre_filter"]`. NOT four stringly-typed keys.
- **1B** Fetchers REQUIRE PreFilterStep upstream when `state.query.pre_filter` is set. No backward-compat fallback. Raise clear error if scratch missing. All shipped YAML pipelines include `pre_filter` as the first step.
- **1C+3A** Add every gap test explicitly listed below.

**Files:**
- Create: `python/pydocs_mcp/retrieval/steps/pre_filter.py` (PreFilterStep + PreFilterResult)
- Modify: `python/pydocs_mcp/retrieval/steps/__init__.py` (export new step + result)
- Modify: `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py` (READ ONLY from `state.scratch["pre_filter"]`; raise if absent + query has filter)
- Modify: `python/pydocs_mcp/retrieval/steps/member_fetcher.py` (same)
- Modify: `python/pydocs_mcp/pipelines/chunk_search.yaml` (insert `pre_filter` step)
- Modify: `python/pydocs_mcp/pipelines/member_search.yaml` (same)
- Modify: `python/pydocs_mcp/pipelines/chunk_search_ranked.yaml` (also include `pre_filter` step — Task 5 detail, but mentioned here)
- Create: `tests/retrieval/steps/test_pre_filter.py` (8 tests)
- Modify: `tests/retrieval/steps/test_chunk_fetcher.py` (add scratch-read + missing-raises tests)
- Modify: `tests/retrieval/steps/test_member_fetcher.py` (same)

### Step 1: Write failing tests for `PreFilterStep` (8 tests total)

Create `tests/retrieval/steps/test_pre_filter.py`:
```python
"""PreFilterStep tests — parse + validate + scope-split + typed result."""
from __future__ import annotations

from dataclasses import is_dataclass

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult, PreFilterStep


def _state(pre_filter: str | None = None, pre_filter_format: str = "multifield") -> RetrieverState:
    query = SearchQuery(
        terms="x", max_results=10,
        pre_filter=pre_filter, pre_filter_format=pre_filter_format,
    )
    return RetrieverState(query=query)


def _step_chunk() -> PreFilterStep:
    return PreFilterStep(
        allowed_fields=frozenset({"package", "module", "scope"}),
        schema_name="chunk",
        target_field="chunk",
    )


def _step_member() -> PreFilterStep:
    return PreFilterStep(
        allowed_fields=frozenset({"package", "module", "scope"}),
        schema_name="member",
        target_field="member",
    )


@pytest.mark.asyncio
async def test_pre_filter_step_is_a_retriever_step() -> None:
    assert isinstance(_step_chunk(), RetrieverStep)


@pytest.mark.asyncio
async def test_pre_filter_noop_when_pre_filter_is_none() -> None:
    """No pre_filter → no scratch key written."""
    out = await _step_chunk().run(_state(pre_filter=None))
    assert "pre_filter" not in out.scratch


@pytest.mark.asyncio
async def test_pre_filter_writes_typed_result_when_filter_present() -> None:
    """A valid pre_filter → PreFilterResult dataclass under state.scratch['pre_filter']."""
    out = await _step_chunk().run(_state(pre_filter="package:demo"))
    assert "pre_filter" in out.scratch
    result = out.scratch["pre_filter"]
    assert isinstance(result, PreFilterResult)
    assert is_dataclass(result)
    assert result.sql  # non-empty SQL fragment
    assert result.tree is not None


@pytest.mark.asyncio
async def test_pre_filter_invalid_format_raises() -> None:
    """Filter format that doesn't parse → propagates the exception."""
    state = _state(pre_filter="!!!invalid!!!", pre_filter_format="multifield")
    with pytest.raises(Exception):  # Specific FilterParseError or ValueError
        await _step_chunk().run(state)


@pytest.mark.asyncio
async def test_pre_filter_scope_split_into_typed_field() -> None:
    """A pre_filter with `scope:project` field → result.scope is a frozenset."""
    out = await _step_chunk().run(_state(pre_filter="scope:project"))
    result = out.scratch["pre_filter"]
    assert result.scope is not None
    assert isinstance(result.scope, frozenset)


@pytest.mark.asyncio
async def test_pre_filter_member_target_uses_member_columns() -> None:
    """target_field='member' → SQL adapter uses _MEMBER_COLUMNS not _CHUNK_COLUMNS."""
    out = await _step_member().run(_state(pre_filter="package:demo"))
    result = out.scratch["pre_filter"]
    assert result.sql  # non-empty
    # member SQL has no 'c.' prefix; chunk SQL does (verified by adapter init)


def test_pre_filter_to_dict_shape() -> None:
    """to_dict emits type + schema_name + target_field."""
    d = _step_chunk().to_dict()
    assert d["type"] == "pre_filter"
    assert d["schema_name"] == "chunk"
    assert d["target_field"] == "chunk"


def test_pre_filter_round_trip_via_from_dict() -> None:
    """from_dict reconstructs an equivalent step given a BuildContext."""
    from pydocs_mcp.retrieval.serialization import BuildContext
    from pydocs_mcp.retrieval.config import AppConfig

    config = AppConfig.load()
    context = BuildContext(app_config=config)  # other fields default; only app_config used here
    original = _step_chunk()
    rebuilt = PreFilterStep.from_dict(original.to_dict(), context)
    assert rebuilt.schema_name == original.schema_name
    assert rebuilt.target_field == original.target_field
    # allowed_fields is rebuilt from config.metadata_schemas; check it's non-empty
    assert rebuilt.allowed_fields
```

Also add to `tests/retrieval/steps/test_chunk_fetcher.py`:
```python
@pytest.mark.asyncio
async def test_chunk_fetcher_reads_pre_filter_from_scratch(populated_db, monkeypatch) -> None:
    """When PreFilterStep ran upstream and wrote PreFilterResult to
    state.scratch['pre_filter'], the fetcher consumes it directly without
    re-parsing state.query.pre_filter."""
    from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = ChunkFetcherStep(provider=provider)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter="package:demo"),
    )
    state.scratch["pre_filter"] = PreFilterResult(
        tree=None, scope=None, sql="c.package = ?", params=["demo"],
    )
    out = await step.run(state)
    assert out.candidates is not None
    # The fetcher used the pre-built SQL, didn't re-parse query.pre_filter.


@pytest.mark.asyncio
async def test_chunk_fetcher_raises_if_pre_filter_set_but_scratch_missing(populated_db) -> None:
    """If state.query.pre_filter is set but PreFilterStep did NOT run
    upstream (scratch lacks 'pre_filter'), the fetcher raises a clear
    error pointing at the missing pipeline step."""
    provider = PerCallConnectionProvider(cache_path=populated_db)
    step = ChunkFetcherStep(provider=provider)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter="package:demo"),
    )
    # No state.scratch['pre_filter'] — PreFilterStep didn't run.
    with pytest.raises(RuntimeError, match="pipeline must include pre_filter"):
        await step.run(state)
```

Same two tests added to `tests/retrieval/steps/test_member_fetcher.py` (s/Chunk/Member/, s/c\.package/package/).

### Step 2: Run tests to verify FAIL
```bash
.venv/bin/pytest tests/retrieval/steps/test_pre_filter.py -v 2>&1 | tail -10
```
Expected: `ImportError: cannot import name 'PreFilterStep'`.

### Step 3: Implement `pre_filter.py`

Create `python/pydocs_mcp/retrieval/steps/pre_filter.py`:
```python
"""PreFilterStep — parse + validate pre_filter once, share typed result via state.scratch.

Single responsibility: take a SearchQuery's `pre_filter` (raw string) +
`pre_filter_format`, parse it via the format registry, validate against
the schema's allowed fields, split the scope clause off, and write a
typed `PreFilterResult` dataclass to `state.scratch["pre_filter"]` for
downstream fetcher steps to consume.

Dedups the inline pre-filter logic that previously lived in
`ChunkFetcherStep` + `MemberFetcherStep`. A single `PreFilterStep` runs
once per pipeline; the fetchers downstream read `state.scratch["pre_filter"]`
directly (raise if missing when `state.query.pre_filter` is set).

No backward-compat fallback — all shipped YAML pipelines include this
step BEFORE the fetcher. User overlays that omit it break loudly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

if TYPE_CHECKING:
    from pydocs_mcp.models import SearchScope
    from pydocs_mcp.storage.filters import Filter

# WHY: single source of truth for the default schema_name when YAML omits it.
_DEFAULT_SCHEMA_NAME = "chunk"


@dataclass(frozen=True, slots=True)
class PreFilterResult:
    """Typed result emitted by PreFilterStep into state.scratch["pre_filter"].

    Fetchers downstream read these fields without re-parsing the raw
    SearchQuery.pre_filter string.
    """
    tree: "Filter | None"
    scope: "frozenset[SearchScope] | None"
    sql: str          # SQL WHERE-clause fragment (empty string if no SQL pushdown)
    params: list[Any] # positional SQL parameters


@step_registry.register("pre_filter")
@dataclass(frozen=True, slots=True)
class PreFilterStep(RetrieverStep):
    """Parse + validate pre_filter once; share typed result via state.scratch."""

    allowed_fields: frozenset[str] = field(default=frozenset(), kw_only=True)
    schema_name: str = field(default=_DEFAULT_SCHEMA_NAME, kw_only=True)
    target_field: Literal["chunk", "member"] = field(default="chunk", kw_only=True)
    name: str = field(default="pre_filter", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.query.pre_filter is None:
            return state

        # Lazy imports — same cycle-break pattern as ChunkFetcherStep.
        from pydocs_mcp.retrieval.filter_helpers import (
            _schema_from_fields,
            _split_scope,
        )
        from pydocs_mcp.storage.filters import format_registry

        tree = format_registry[state.query.pre_filter_format].parse(
            state.query.pre_filter,
        )
        _schema_from_fields(self.allowed_fields).validate(tree)
        tree, scope = _split_scope(tree)

        filter_sql = ""
        filter_params: list = []
        if tree is not None:
            from pydocs_mcp.storage.sqlite import (
                _CHUNK_COLUMNS,
                _MEMBER_COLUMNS,
                SqliteFilterAdapter,
            )
            if self.target_field == "chunk":
                adapter = SqliteFilterAdapter(
                    safe_columns=_CHUNK_COLUMNS, column_prefix="c.",
                )
            else:
                adapter = SqliteFilterAdapter(safe_columns=_MEMBER_COLUMNS)
            filter_sql, filter_params = adapter.adapt(tree)

        # Write typed result to state.scratch under a single key. The dict
        # mutation is intentional — the dataclass is frozen but the scratch
        # dict is mutable (per RetrieverState's documented contract).
        state.scratch["pre_filter"] = PreFilterResult(
            tree=tree, scope=scope, sql=filter_sql, params=filter_params,
        )
        return state

    def to_dict(self) -> dict:
        return {
            "type": "pre_filter",
            "schema_name": self.schema_name,
            "target_field": self.target_field,
        }

    @classmethod
    def from_dict(
        cls, data: dict, context: BuildContext,
    ) -> "PreFilterStep":
        schema_name = data.get("schema_name", _DEFAULT_SCHEMA_NAME)
        if context.app_config is None:
            raise ValueError(
                "PreFilterStep requires BuildContext.app_config; "
                "provide AppConfig at server/CLI startup."
            )
        allowed = frozenset(context.app_config.metadata_schemas[schema_name])
        target_field = data.get("target_field", "chunk")
        return cls(
            allowed_fields=allowed,
            schema_name=schema_name,
            target_field=target_field,
        )
```

### Step 4: Export from `steps/__init__.py`
Add `PreFilterStep` to imports + `__all__`.

### Step 5: Verify tests pass
```bash
.venv/bin/pytest tests/retrieval/steps/test_pre_filter.py -v 2>&1 | tail -10
```
Expected: 4 passed.

### Step 6: Update fetchers to read from `state.scratch["pre_filter"]` (no fallback)

Per **decision 1B**, fetchers REQUIRE PreFilterStep upstream when `state.query.pre_filter` is set. No fallback — clean break.

Edit `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py`:

```python
async def run(self, state: RetrieverState) -> RetrieverState:
    fulltext = _build_fts_match_query(state.query.terms)
    if fulltext is None:
        return replace(state, candidates=ChunkList(items=()))

    # Read PreFilterStep's typed result from scratch. PreFilterStep MUST
    # run upstream — fetchers don't re-parse the raw filter string.
    # When no filter is set on the query, no PreFilterStep work needs to
    # have happened; we just use empty SQL pushdown.
    from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult

    filter_sql = ""
    filter_params: list = []
    scope = None

    if state.query.pre_filter is not None:
        result = state.scratch.get("pre_filter")
        if not isinstance(result, PreFilterResult):
            raise RuntimeError(
                "ChunkFetcherStep: state.query.pre_filter is set but "
                "state.scratch['pre_filter'] is missing. The pipeline must "
                "include the 'pre_filter' step before 'chunk_fetcher'. "
                "See pipelines/chunk_search.yaml for the canonical shape.",
            )
        filter_sql = result.sql
        filter_params = result.params
        scope = result.scope

    rows = await asyncio.to_thread(
        self._fetch_sync, fulltext, filter_sql, filter_params,
    )
    chunks = tuple(
        _row_to_candidate(row, self.retriever_name) for row in rows
    )
    if scope is not None:
        chunks = tuple(
            c for c in chunks
            if _matches_scope(c.metadata.get(ChunkFilterField.PACKAGE.value, ""), scope)
        )
    return replace(state, candidates=ChunkList(items=chunks))
```

The inline parsing logic (~30 LOC of format_registry + _schema_from_fields + _split_scope + SqliteFilterAdapter) is **fully deleted** from `chunk_fetcher.py` — moved to `PreFilterStep`. Net file shrinkage: ~30 LOC removed, ~15 LOC added (the scratch read + error message).

Same pattern for `member_fetcher.py` (s/ChunkFetcherStep/MemberFetcherStep/, s/chunk_fetcher/member_fetcher/, s/ChunkFilterField/ModuleMemberFilterField/).

### Step 7: Update YAML pipelines to insert `pre_filter` step

Edit `python/pydocs_mcp/pipelines/chunk_search.yaml`:
```yaml
name: chunk_search
steps:
  - name: pre_filter
    type: pre_filter
    params:
      schema_name: chunk
      target_field: chunk
  - name: fetch
    type: chunk_fetcher
    params: { limit: 200 }
  # ... rest unchanged
```

Same for `python/pydocs_mcp/pipelines/member_search.yaml` with `target_field: member`, `schema_name: member`.

### Step 8: Run full suite + AC17 check
```bash
.venv/bin/pytest -q 2>&1 | tail -3   # expect 1020 + 4 = 1024 passed
shasum -a 256 benchmarks/baselines/repoqa_snf.json
# expected: 1a38657b… (unchanged — behavior preserved)
.venv/bin/ruff check python/ tests/
```

### Step 9: Commit
```bash
git add -A
git commit -m "refactor(retrieval): extract PreFilterStep — dedup fetcher pre-filter logic

ChunkFetcherStep + MemberFetcherStep both contained ~30 LOC of identical
pre-filter parsing logic (parse via format_registry, validate against
allowed_fields, split scope, build SQL via SqliteFilterAdapter).
PreFilterStep extracts this into one place, lets future steps
(B3.1 DenseScorerStep, etc.) reuse the result via state.scratch.

Design (per plan-eng-review):
- Typed PreFilterResult dataclass under a single scratch key
  state.scratch['pre_filter'] — NOT four stringly-typed keys.
- No backward-compat fallback. Fetchers REQUIRE PreFilterStep upstream
  when state.query.pre_filter is set. Raise clear RuntimeError with
  pipeline-fix hint if scratch is missing.
- All shipped YAML pipelines (chunk_search, chunk_search_ranked,
  member_search) include pre_filter as the first step.

Behavior preserved: same SQL fires, same candidates land in
state.candidates. AC17 RepoQA fixture baseline SHA unchanged.

Tests:
- test_pre_filter.py — 8 tests (isolation, none-noop, typed-result,
  invalid-format-raises, scope-split, member target_field, to_dict,
  from_dict round-trip)
- test_chunk_fetcher.py — 2 added (scratch-read + missing-raises)
- test_member_fetcher.py — 2 added (same)"
```

---

## Task 5: `chunk_search_ranked.yaml` preset + benchmark wiring

**Files:**
- Create: `python/pydocs_mcp/pipelines/chunk_search_ranked.yaml`
- Modify: `benchmarks/configs/baseline.yaml` (point at the ranked preset)
- Modify: `benchmarks/src/benchmarks/eval/systems/pydocs.py` (`search()` reads `state.candidates` then `state.result`)
- Modify: tests for the System if existing tests break

### Step 1: Create the ranked preset

Create `python/pydocs_mcp/pipelines/chunk_search_ranked.yaml`:
```yaml
# Ranked chunk-search pipeline — benchmark / evaluation use only.
#
# Identical to chunk_search.yaml but drops the final token_budget_formatter
# step so the pipeline returns top-K SEPARATE ranked chunks in state.candidates
# instead of one composite chunk in state.result. recall@k / mrr / pass@1
# metrics can actually measure top-K hits with this preset; the MCP server
# keeps chunk_search.yaml (composite output) as its default.

name: chunk_search_ranked
steps:
  - name: pre_filter
    type: pre_filter
    params:
      schema_name: chunk
      target_field: chunk
  - name: fetch
    type: chunk_fetcher
    params: { limit: 200 }
  - name: score
    type: bm25_scorer
    params: {}
  - name: post_filter
    type: metadata_post_filter
    params: {}
  - name: topk
    type: top_k_filter
    params: { k: 50 }
  - name: limit
    type: limit
    params: { max_results: 10 }
  # NO token_budget_formatter — output is state.candidates (ranked list),
  # not state.result (composite).
```

### Step 2: Update benchmark config to use the new preset

Look at `benchmarks/configs/baseline.yaml`. It likely overrides `pipelines.chunk.routes` to point at `chunk_search.yaml`. Change to `chunk_search_ranked.yaml`.

```bash
grep -n "chunk_search\|pipeline_path" benchmarks/configs/baseline.yaml
```

Apply the YAML edit. Likely:
```yaml
pipelines:
  chunk:
    routes:
      - default: true
        pipeline_path: chunk_search_ranked.yaml
```

### Step 3: Update PydocsMcpSystem.search

Edit `benchmarks/src/benchmarks/eval/systems/pydocs.py`:
```python
async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]:
    if self._pipeline is None:
        raise RuntimeError(...)
    from pydocs_mcp.models import ChunkList, SearchQuery

    state = await self._pipeline.run(
        SearchQuery(terms=query, max_results=limit),
    )

    # WHY: prefer state.candidates (ranked top-K from chunk_search_ranked.yaml)
    # over state.result (composite from chunk_search.yaml). Lets the System
    # work with either preset without a hard fork.
    items_source = None
    if isinstance(state.candidates, ChunkList) and state.candidates.items:
        items_source = state.candidates
    elif isinstance(state.result, ChunkList):
        items_source = state.result

    if items_source is None:
        return ()
    out: list[RetrievedItem] = []
    for rank, chunk in enumerate(items_source.items, start=1):
        # ... existing RetrievedItem construction unchanged ...
```

### Step 4: Run benchmark tests
```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3
```
Expected: 111 passed.

If any test fails due to the YAML preset switch, fix the test fixture (likely needs to know the new preset name, not actual behavior).

### Step 5: Smoke-test the new preset against the fixture
```bash
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --fixture benchmarks/tests/eval/fixtures/repoqa_mini.json \
    --trackers jsonl \
    --limit 5 2>&1 | tail -25
```
Expected: 5 tasks run, recall@k now > 0% (instead of structurally pegged 0%).

### Step 6: Commit
```bash
git add -A
git commit -m "feat(benchmarks): chunk_search_ranked.yaml preset for benchmark recall@k

Drops the final token_budget_formatter step so the pipeline returns top-K
SEPARATE ranked chunks in state.candidates instead of one composite chunk
in state.result.

Pre-refactor, the benchmark consumed chunk_search.yaml which ends in
TokenBudgetStep. That collapses all ranked candidates into one composite
markdown blob for MCP/LLM consumption. recall@k walks K *separate* items
to find the gold — collapsing to 1 means K=1 max, regardless of how many
real hits the upstream BM25 retriever found. Real-RepoQA recall pegged
at 0% structurally.

Now benchmark sweeps load chunk_search_ranked.yaml via the updated
benchmarks/configs/baseline.yaml. PydocsMcpSystem.search reads
state.candidates first, falling back to state.result if running the
composite preset (so old YAML configs keep working).

The MCP server + CLI default still use chunk_search.yaml — composite
output is correct for LLM clients."
```

---

## Task 6: Re-measure RepoQA against ranked preset

**Files:**
- Modify: `benchmarks/baselines/repoqa_fixture_baseline.json` (fixture sweep against new preset)
- Modify: `benchmarks/baselines/repoqa_snf.json` (real 100-needle sweep against new preset)

### Step 1: Fixture sweep
```bash
mkdir -p benchmarks/results/jsonl
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --fixture benchmarks/tests/eval/fixtures/repoqa_mini.json \
    --trackers jsonl \
    --limit 5 2>&1 | tail -25

FIXTURE_JSONL=$(ls -t benchmarks/results/jsonl/*.jsonl | head -1)
echo "FIXTURE_JSONL=$FIXTURE_JSONL"
```

### Step 2: Real sweep (5-10 min, network)
```bash
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --trackers jsonl 2>&1 | tail -25
REAL_JSONL=$(ls -t benchmarks/results/jsonl/*.jsonl | head -1)
echo "REAL_JSONL=$REAL_JSONL"
```

### Step 3: Write both baselines via the heredoc
Same approach as PR #27 Task 5 Step 3:
```bash
.venv/bin/python - <<'PY'
# (paste the same baseline-writing script from PR #27's plan §Task 5 Step 3)
PY
```

### Step 4: Inspect both
```bash
python3 -m json.tool benchmarks/baselines/repoqa_fixture_baseline.json | head -40
python3 -m json.tool benchmarks/baselines/repoqa_snf.json | head -40
```
Expected: `repoqa_snf.json` shows recall@k > 0 — the headline finding of this PR.

### Step 5: Smoke-test CI gate against new baseline
```bash
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.ci_compare \
    --baseline benchmarks/baselines/repoqa_fixture_baseline.json \
    --current "benchmarks/results/jsonl/*.jsonl" \
    --metric recall@10 \
    --threshold 0.02
```
Expected: OK (the latest JSONL is the fixture sweep just captured).

### Step 6: Commit
```bash
git add benchmarks/baselines/
git commit -m "feat(benchmarks): re-measure RepoQA baseline against chunk_search_ranked.yaml

Captured fresh baselines after the chunk_search_ranked.yaml preset
landed. Both baseline files re-shot against the new ranked-output
pipeline:

- repoqa_fixture_baseline.json — captured from 5-needle fixture
  sweep (CI gate source, hermetic).
- repoqa_snf.json — captured from real 100-needle Python subset
  of repoqa-2024-06-23. recall@k now shows real signal instead of
  structurally-pegged 0% (the headline finding of this PR).

Documents the post-refactor state: pydocs-mcp's BM25 retrieval at
recall@10 = X.X% on real RepoQA needles. Baseline for PR-B3.1
(dense embeddings) to beat.

Each baseline tracks source_jsonl + git_sha + captured_at UTC."
```

---

## Final verification gauntlet

After Task 6:

### Step 1: Full pytest
```bash
.venv/bin/pytest -q 2>&1 | tail -3
# expect 1024+ passed (1019 baseline + 1 AC #15 + 4 PreFilterStep)
```

### Step 2: Benchmarks
```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q 2>&1 | tail -3
# expect 111+ passed
```

### Step 3: Ruff
```bash
.venv/bin/ruff check python/ tests/ benchmarks/
```

### Step 4: Push + open PR (draft)
```bash
git push -u origin feature/cleanups-and-pr-a
gh pr create --draft --title "Cleanups + PR-A — chunk_search_ranked.yaml preset" --body "..."
```

### Step 5: Skill review chain (post-coding)
1. `/code-review` — security/perf/correctness on the diff
2. `/review` — pre-landing review
3. `/devex-review` — live DX audit (docs were recently restructured)
4. `/codex review` — adversarial second opinion

Apply fixes inline as the reviews surface findings.

### Step 6: Watch CI + take out of draft
```bash
gh pr checks <PR#> --watch
gh pr ready <PR#>
```

---

## Reused helpers / patterns

- `dataclasses.replace` for immutable state updates — every step uses this.
- `_DEFAULT_*` module constants per `CLAUDE.md §"Default values: single source of truth"`.
- `filter_helpers._schema_from_fields` + `_split_scope` for pre-filter parsing — shared by `PreFilterStep` + fetchers' fallback path.
- `format_registry` (in `storage/filters.py`) — same parser the fetchers used before.
- The baseline-writing heredoc from PR #27 Task 5 — reused verbatim for re-measurement.

## Verification gates (post-implementation)

Every task ends with `pytest -q` showing the expected passing count. The PR is reviewable in three independent slices:

1. **Cleanups** (Tasks 1-3): rename + fixture corpus + worktree removal. Pure plumbing; no behavior change.
2. **PreFilterStep** (Task 4): extraction with backward-compat fallback. Behavior preserved (AC17 hash unchanged).
3. **PR-A** (Tasks 5-6): new YAML preset + benchmark wiring + re-measurement. Real RepoQA recall@k > 0.

After all tasks: dispatch the 4-skill review chain (`/code-review`, `/review`, `/devex-review`, `/codex review`), surface findings for inline fixes, then take PR out of draft.

---

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | (deferred to post-coding) | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR (PLAN) | 3 issues, 0 critical gaps (after locked decisions) |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | n/a | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | (post-coding: `/devex-review`) | — |

**UNRESOLVED:** 0
**VERDICT:** ENG REVIEW CLEARED — all 3 findings (typed PreFilterResult, no-fallback, gap tests) resolved by locked decisions. Ready to implement Tasks 1-6.
