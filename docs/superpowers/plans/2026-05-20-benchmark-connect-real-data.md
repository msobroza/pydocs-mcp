# Benchmark Harness — Connect to Real Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Lift the benchmark harness from "fixture-only" to "real RepoQA data + comparative wiring + latency observability + a first measured baseline" per the [spec](../specs/2026-05-20-benchmark-connect-real-data-design.md).

**Architecture:** No new Protocols. No new directories. Five surgical changes:
1. `strict_suffix` knob added to existing `ReferenceResolverConfig`.
2. `RepoQADataset` loader rewritten — stdlib only (urllib + gzip), GitHub-Releases distribution.
3. Runner gains `_maybe_set_library(system, task.metadata)` helper before each `system.index`.
4. Runner times each `index()` / `search()` and emits `*_seconds` metrics; aggregator gets `percentile()`; report renders p50/p95/p99 cells.
5. Real measured baseline at `benchmarks/baselines/repoqa_snf.json`.

**Tech stack:** Python 3.11+, stdlib only for the loader rewrite (no `datasets` / `huggingface-hub`). Existing pydantic, asyncio, pytest, ruff.

**Reviewer strategy** (per `superpowers:subagent-driven-development`): each task gets two-stage review (spec-compliance + code-quality), with reviewer prompts customized per task category.

| Task category | Spec reviewer focus | Code reviewer focus |
|---|---|---|
| Resolver knob (Task 1) | New config field follows existing `include_stdlib` pattern | No defensive `if cfg is not None`; YAML overlay loads cleanly |
| RepoQA loader (Task 2) | Real-schema field mapping (`name`, `description`, `path`, `start_line/end_line`) | Stdlib-only, deterministic cache key, body-extraction off-by-one |
| Runner library wiring (Task 3) | Systems-agnostic via `hasattr`; runs BEFORE `index()` | No `isinstance` ladder; pure side-effect on system instance |
| Latency aggregator (Task 4) | Per-task `step=task_index` emit + final percentile aggregate | `percentile()` matches numpy default convention; deterministic on equal values |
| Real baseline (Task 5) | Numbers from a REAL run, not extrapolation | Commit message documents the env + git SHA the baseline was captured at |

---

## Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect/`
Branch: `feature/benchmark-connect-to-real-data` (already pushed as draft PR #27 with the spec).

---

## Task 0: Drop the `[repoqa]` optional extra from `pyproject.toml`

**Files:**
- Modify: `benchmarks/pyproject.toml`

- [ ] **Step 1: Read current `benchmarks/pyproject.toml`**

Use the Read tool to confirm the current state. The relevant block:

```toml
[project.optional-dependencies]
mlflow = ["mlflow>=2.0"]
repoqa = ["datasets>=2.0", "huggingface-hub>=0.20"]
all = ["mlflow>=2.0", "datasets>=2.0", "huggingface-hub>=0.20"]
```

- [ ] **Step 2: Replace with stdlib-only extras**

```toml
[project.optional-dependencies]
mlflow = ["mlflow>=2.0"]
all = ["mlflow>=2.0"]
```

The `[repoqa]` extra is dropped entirely. `all` no longer pulls in `datasets` / `huggingface-hub`.

- [ ] **Step 3: Verify baseline tests still pass**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q
```

Expected: 100 passing (unchanged — `[repoqa]` was unused at runtime).

- [ ] **Step 4: Commit**

```bash
git add benchmarks/pyproject.toml
git commit -m "chore(benchmarks): drop [repoqa] optional extra — RepoQA is not on HuggingFace"
```

---

## Task 1: `strict_suffix` knob on `ReferenceResolverConfig`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (add field)
- Modify: `python/pydocs_mcp/extraction/strategies/reference_resolver.py` (gate Rule C)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (thread the toggle through resolver construction)
- Modify: `tests/extraction/test_reference_resolver.py` (new test)
- Create: `benchmarks/configs/strict_suffix_off.yaml` already exists from PR #26 — just verify it loads cleanly

- [ ] **Step 1: Write the failing test**

Add to `tests/extraction/test_reference_resolver.py`:

```python
def test_strict_suffix_off_skips_rule_c() -> None:
    """When strict_suffix=False, Rule C (suffix-within-package) does NOT fire.

    Setup: a reference like ``compute`` from package ``pkg`` and a single
    qname ``pkg.helpers.compute`` in the universe. With strict_suffix=True
    Rule C resolves it. With False, only Rule B (exact match) runs — no
    resolution because the to_name doesn't match the full qname.
    """
    qnames = frozenset({"pkg.helpers.compute"})
    resolver_strict = ReferenceResolver(
        qname_universe=qnames, aliases={}, class_attribute_types={},
        strict_suffix=True,
    )
    resolver_loose = ReferenceResolver(
        qname_universe=qnames, aliases={}, class_attribute_types={},
        strict_suffix=False,
    )
    ref = NodeReference(
        from_package="pkg", from_node_id="pkg.module.fn",
        to_name="compute", to_node_id=None, kind=ReferenceKind.CALLS,
    )
    out_strict = resolver_strict.resolve([ref])
    out_loose = resolver_loose.resolve([ref])
    # Rule C resolves under strict; only Rule B runs under loose.
    assert out_strict[0].to_node_id == "pkg.helpers.compute"
    assert out_loose[0].to_node_id is None
```

- [ ] **Step 2: Run test to verify FAIL**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect
.venv/bin/pytest tests/extraction/test_reference_resolver.py::test_strict_suffix_off_skips_rule_c -v
```

Expected: FAIL with `TypeError: ReferenceResolver.__init__() got an unexpected keyword argument 'strict_suffix'`.

- [ ] **Step 3: Add field to `ReferenceResolverConfig`**

In `python/pydocs_mcp/retrieval/config.py`, locate `ReferenceResolverConfig` and add `strict_suffix`:

```python
class ReferenceResolverConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    include_stdlib: bool = True
    # WHY: when False, the resolver only fires Rule B (exact qname match)
    # — Rule C (strict-suffix-within-package) is skipped. Ablation knob
    # for measuring Rule C's contribution to AC #15 resolution rate.
    strict_suffix: bool = True
```

- [ ] **Step 4: Add `strict_suffix` to `ReferenceResolver` dataclass**

In `python/pydocs_mcp/extraction/strategies/reference_resolver.py`:

```python
@dataclass(frozen=True, slots=True)
class ReferenceResolver:
    qname_universe: frozenset[str]
    aliases: dict[str, dict[str, str]]
    class_attribute_types: dict[str, dict[str, str]]
    strict_suffix: bool = True  # WHY: default True preserves pre-PR behavior

    def _resolve_one(self, ref: NodeReference) -> str | None:
        # ... existing Rule 0, Rule A, Rule B, Rule F20 unchanged ...

        # Rule C — strict suffix within from_package. Gated on the flag.
        if self.strict_suffix:
            candidates: list[str] = []
            for q in self.qname_universe:
                if q.endswith("." + ref.to_name) and (
                    q.startswith(ref.from_package + ".") or q == ref.from_package + "." + ref.to_name
                ):
                    candidates.append(q)
            if len(candidates) == 1:
                return candidates[0]
            if len(candidates) > 1:
                return None  # Rule D — ambiguous

        # Rule E — no match
        return None
```

- [ ] **Step 5: Thread the toggle through `IndexingService`**

In `python/pydocs_mcp/application/indexing_service.py`, locate `_resolve_references` and pass `strict_suffix` to the resolver constructor — same pattern as `include_stdlib`:

```python
cfg = _get_resolver_config()
resolver = ReferenceResolver(
    qname_universe=frozenset(universe),
    aliases=aliases,
    class_attribute_types=class_attribute_types,
    strict_suffix=cfg.strict_suffix,
)
```

- [ ] **Step 6: Run test to verify PASS**

```bash
.venv/bin/pytest tests/extraction/test_reference_resolver.py::test_strict_suffix_off_skips_rule_c -v
```

Expected: PASS.

- [ ] **Step 7: Verify `benchmarks/configs/strict_suffix_off.yaml` loads cleanly**

```bash
.venv/bin/python -c "
from pydocs_mcp.retrieval.config import AppConfig
cfg = AppConfig.load(explicit_path='benchmarks/configs/strict_suffix_off.yaml')
assert cfg.reference_graph.resolver.strict_suffix is False
print('strict_suffix_off.yaml loads:', cfg.reference_graph.resolver.strict_suffix)
"
```

Expected: `strict_suffix_off.yaml loads: False`.

- [ ] **Step 8: Run full suite**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check python/ benchmarks/
```

Expected: all 996+ tests passing, ruff clean.

- [ ] **Step 9: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py \
        python/pydocs_mcp/extraction/strategies/reference_resolver.py \
        python/pydocs_mcp/application/indexing_service.py \
        tests/extraction/test_reference_resolver.py
git commit -m "feat(resolver): strict_suffix toggle on ReferenceResolverConfig

Adds a YAML-driven ablation knob — strict_suffix=False skips Rule C
(suffix-within-package) so the harness can measure Rule C's contribution
to AC #15 resolution rate against a baseline.

Default stays True (pre-PR behavior). benchmarks/configs/strict_suffix_off.yaml
now loads cleanly through AppConfig.load."
```

---

## Task 2: Rewrite `RepoQADataset` for the GitHub-Releases distribution

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/datasets/repoqa.py` (full rewrite)
- Modify: `benchmarks/tests/eval/fixtures/repoqa_mini.json` (rewrite in real schema)
- Modify: `benchmarks/tests/eval/test_repoqa_loader.py` (updated for new schema + stub URL fetch)

- [ ] **Step 1: Write the failing test (new schema)**

Replace the contents of `benchmarks/tests/eval/test_repoqa_loader.py`:

```python
"""RepoQADataset tests — fixture-only (no network)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from benchmarks.eval.datasets.repoqa import RepoQADataset
from benchmarks.eval.protocols import Dataset  # re-exported from base_dataset

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


@pytest.mark.asyncio
async def test_fixture_yields_two_tasks() -> None:
    """Fixture has 1 Python repo with 2 needles → 2 EvalTasks."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 2


@pytest.mark.asyncio
async def test_each_task_has_required_fields() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    async for task in dataset.tasks():
        assert task.task_id
        assert task.query
        assert task.gold.ast_body
        assert task.metadata["repo"]
        assert task.metadata["language"] == "python"


@pytest.mark.asyncio
async def test_gold_body_extracted_from_content() -> None:
    """The gold body comes from content[path] sliced by start_line/end_line."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    # The first fixture needle is at lines 3-5 of fixture_repo/math_helpers.py.
    # The fixture's content has lines: ["def factorial(n: int) -> int:", ...].
    assert "def factorial" in tasks[0].gold.ast_body


@pytest.mark.asyncio
async def test_task_id_includes_repo_sha_path_name() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    # Format: ``{repo}@{sha[:7]}/{path}::{name}``
    assert "@" in tasks[0].task_id
    assert "::" in tasks[0].task_id


@pytest.mark.asyncio
async def test_corpus_source_materializes_repo_content() -> None:
    """Each task's corpus_source returns a tmp dir containing the repo files."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    corpus_dir = tasks[0].corpus_source()
    assert (corpus_dir / "fixture_repo" / "math_helpers.py").exists()


@pytest.mark.asyncio
async def test_dataset_satisfies_protocol() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    assert isinstance(dataset, Dataset)


def test_revision_is_pinned_release_version() -> None:
    dataset = RepoQADataset()
    assert dataset.revision == "2024-06-23"


@pytest.mark.asyncio
async def test_release_download_path_uses_urllib(monkeypatch, tmp_path) -> None:
    """When no fixture is provided, the loader fetches the GitHub release via
    urllib. We stub urlopen to return a tiny gzipped JSON so no network."""
    import gzip
    import io
    import urllib.request

    fake_payload = json.dumps({"python": [], "go": []}).encode()
    fake_gz = gzip.compress(fake_payload)

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *exc): return None
        def read(self): return fake_gz

    def fake_urlopen(url, timeout=None):  # noqa: ARG001
        return _FakeResp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    dataset = RepoQADataset(cache_dir=tmp_path)
    tasks = [t async for t in dataset.tasks()]
    assert tasks == []  # empty python list → 0 tasks
    # Cache file written
    assert (tmp_path / "repoqa-2024-06-23.json").exists()
```

Also rewrite the fixture at `benchmarks/tests/eval/fixtures/repoqa_mini.json`:

```json
{
  "python": [
    {
      "repo": "fixture/synthetic-repo",
      "commit_sha": "abc1234567890abcdef1234567890abcdef12345",
      "topic": "math",
      "entrypoint_path": "fixture_repo",
      "content": {
        "fixture_repo/__init__.py": "",
        "fixture_repo/math_helpers.py": "\"\"\"Module docstring.\"\"\"\n\ndef factorial(n: int) -> int:\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n\n\ndef fibonacci(n: int) -> int:\n    if n < 2:\n        return n\n    return fibonacci(n - 1) + fibonacci(n - 2)\n"
      },
      "dependency": {},
      "functions": {},
      "needles": [
        {
          "name": "factorial",
          "description": "Compute the factorial of a non-negative integer.",
          "path": "fixture_repo/math_helpers.py",
          "start_line": 3,
          "end_line": 6,
          "start_byte": 25,
          "end_byte": 130,
          "global_start_byte": 0,
          "global_end_byte": 0,
          "global_start_line": 0,
          "global_end_line": 0,
          "code_ratio": 0.5
        },
        {
          "name": "fibonacci",
          "description": "Compute the n-th Fibonacci number.",
          "path": "fixture_repo/math_helpers.py",
          "start_line": 9,
          "end_line": 12,
          "start_byte": 140,
          "end_byte": 240,
          "global_start_byte": 0,
          "global_end_byte": 0,
          "global_start_line": 0,
          "global_end_line": 0,
          "code_ratio": 0.5
        }
      ]
    }
  ]
}
```

- [ ] **Step 2: Run test to verify FAIL**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_repoqa_loader.py -v
```

Expected: most tests fail because the loader still expects the old HF schema.

- [ ] **Step 3: Rewrite `benchmarks/src/benchmarks/eval/datasets/repoqa.py`**

Full rewrite — replace the file contents:

```python
"""RepoQA-SNF dataset loader (spec §5.1).

RepoQA is distributed as a single gzipped JSON file from
``evalplus/repoqa_release`` GitHub Releases. Stdlib-only — no
``datasets`` / ``huggingface-hub`` dependency.
"""
from __future__ import annotations

import gzip
import json
import urllib.request
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..corpus import materialize_corpus
from ..serialization import dataset_registry
from .base_dataset import Dataset, EvalTask, GoldAnswer  # re-exported

# WHY: the date-tagged GitHub release. Bump in lockstep with EvalPlus
# release cadence (see https://github.com/evalplus/repoqa_release/releases).
_PINNED_RELEASE_VERSION = "2024-06-23"
_RELEASE_URL = (
    "https://github.com/evalplus/repoqa_release/releases/download/"
    "{version}/repoqa-{version}.json.gz"
)


@dataset_registry.register("repoqa")
@dataclass
class RepoQADataset:
    """RepoQA-SNF (Apache-2.0, EvalPlus, arXiv 2406.06025)."""

    name: str = "repoqa"
    revision: str = _PINNED_RELEASE_VERSION
    fixture_path: Path | None = None
    cache_dir: Path = field(
        default_factory=lambda: Path("~/.cache/pydocs-mcp/repoqa").expanduser(),
    )
    language: str = "python"
    _rows_cache: list[dict[str, Any]] | None = field(
        default=None, init=False, repr=False,
    )

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            self._rows_cache = (
                self._load_from_fixture()
                if self.fixture_path is not None
                else self._load_from_release()
            )
        for row in self._rows_cache:
            yield _row_to_task(row)

    def _load_from_fixture(self) -> list[dict[str, Any]]:
        assert self.fixture_path is not None
        with self.fixture_path.open() as fh:
            data = json.load(fh)
        return _flatten_needles(data.get(self.language, []))

    def _load_from_release(self) -> list[dict[str, Any]]:
        target = self.cache_dir / f"repoqa-{self.revision}.json"
        if not target.exists():
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            url = _RELEASE_URL.format(version=self.revision)
            with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
                payload = gzip.decompress(resp.read())
            target.write_bytes(payload)
        data = json.loads(target.read_text())
        return _flatten_needles(data.get(self.language, []))


def _flatten_needles(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per needle (NOT per repo). Each row carries the repo content
    once; corpus_source closures share it via the default-arg trick."""
    rows: list[dict[str, Any]] = []
    for repo_entry in repos:
        for needle in repo_entry["needles"]:
            rows.append({
                "repo": repo_entry["repo"],
                "commit_sha": repo_entry["commit_sha"],
                "topic": repo_entry["topic"],
                "content": repo_entry["content"],
                "needle": needle,
            })
    return rows


def _row_to_task(row: dict[str, Any]) -> EvalTask:
    needle = row["needle"]
    content: Mapping[str, str] = dict(row["content"])
    needle_body = _extract_body(
        content[needle["path"]], needle["start_line"], needle["end_line"],
    )
    repo_id = f"{row['repo']}@{row['commit_sha'][:7]}"
    return EvalTask(
        task_id=f"{repo_id}/{needle['path']}::{needle['name']}",
        query=needle["description"],
        gold=GoldAnswer(ast_body=needle_body),
        corpus_source=lambda files=content: materialize_corpus(files),
        metadata={
            "repo": row["repo"],
            "commit": row["commit_sha"],
            "topic": row["topic"],
            "language": "python",
            "needle_name": needle["name"],
            "needle_path": needle["path"],
        },
    )


def _extract_body(source: str, start_line: int, end_line: int) -> str:
    """1-indexed inclusive line slice. Same convention as RepoQA's
    ``start_line``/``end_line`` byte-aligned ranges."""
    lines = source.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])
```

- [ ] **Step 4: Run tests to verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_repoqa_loader.py -v
```

Expected: 8 passing.

- [ ] **Step 5: Run full benchmarks suite + ruff**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q
.venv/bin/ruff check benchmarks/
```

Expected: 100+ passing, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/benchmarks/eval/datasets/repoqa.py \
        benchmarks/tests/eval/fixtures/repoqa_mini.json \
        benchmarks/tests/eval/test_repoqa_loader.py
git commit -m "feat(benchmarks): rewrite RepoQA loader for GitHub Releases distribution

RepoQA is not on HuggingFace. Real distribution is a gzipped JSON
from evalplus/repoqa_release GitHub Releases. Stdlib-only — no
datasets/huggingface-hub dependency.

New shape:
- _PINNED_RELEASE_VERSION = '2024-06-23'
- _load_from_release uses urllib.request + gzip
- One row per needle (not per repo); gold body extracted from
  content[needle.path] using start_line/end_line
- Metadata carries repo, commit_sha, topic, needle_name, needle_path
- Fixture rewritten in the real schema (1 repo, 2 needles)
"
```

---

## Task 3: Runner library wiring for Context7 / Neuledge

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/runner.py` (add `_maybe_set_library` helper + call site)
- Modify: `benchmarks/tests/eval/test_runner_smoke.py` (assert library is seeded)

- [ ] **Step 1: Write the failing test**

Add to `benchmarks/tests/eval/test_runner_smoke.py`:

```python
@pytest.mark.asyncio
async def test_runner_seeds_library_on_systems_before_index(tmp_path) -> None:
    """The runner reads task.metadata['repo'] and seeds library_name /
    library on the system instance BEFORE index() is called."""
    from benchmarks.eval.runner import _maybe_set_library

    class _Recorder:
        name = "recorder"
        library_name: str = ""
        library: str = ""

    system = _Recorder()
    _maybe_set_library(system, {"repo": "psf/black", "commit": "abcdef1234"})
    assert system.library_name == "psf/black"
    # library combines repo + 7-char commit
    assert system.library == "psf/black@abcdef1"
```

- [ ] **Step 2: Run test to verify FAIL**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_runner_smoke.py::test_runner_seeds_library_on_systems_before_index -v
```

Expected: `ImportError: cannot import name '_maybe_set_library'`.

- [ ] **Step 3: Add `_maybe_set_library` to `runner.py`**

In `benchmarks/src/benchmarks/eval/runner.py`, near the other private helpers:

```python
def _maybe_set_library(system: object, metadata: Mapping[str, str]) -> None:
    """Seed comparative-system library identifiers from task metadata.

    Context7System expects ``library_name`` (the human name, e.g.
    ``"psf/black"``); NeuledgeSystem expects ``library`` (the install
    identifier, e.g. ``"psf/black@abcdef1"``). Pydocs-mcp ignores both
    — it indexes the corpus directory and answers from its own DB.

    Systems-agnostic via ``hasattr`` — avoids a special-case ladder per
    system type and lets future comparative systems opt in by declaring
    matching fields.
    """
    repo = metadata.get("repo")
    if not repo:
        return
    if hasattr(system, "library_name"):
        system.library_name = repo
    if hasattr(system, "library"):
        commit = metadata.get("commit", "")
        system.library = f"{repo}@{commit[:7]}" if commit else repo
```

- [ ] **Step 4: Wire `_maybe_set_library` into the per-task loop**

In the same file, inside `run_sweep`, BEFORE `corpus_dir = task.corpus_source()`:

```python
async for task in dataset.tasks():
    if limit is not None and count >= limit:
        break

    # WHY: comparative systems (Context7, Neuledge) need a library
    # identifier resolved from task metadata BEFORE index(). Pydocs-mcp
    # ignores this hook (no matching field), so this is systems-agnostic.
    _maybe_set_library(system, task.metadata)

    corpus_dir = task.corpus_source()
    ...
```

- [ ] **Step 5: Run test to verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_runner_smoke.py -v
```

Expected: all runner tests passing including the new one.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py \
        benchmarks/tests/eval/test_runner_smoke.py
git commit -m "feat(benchmarks): runner seeds library_name/library from task.metadata

Adds _maybe_set_library(system, metadata) called before every
system.index(). Reads task.metadata['repo'] / metadata['commit']
and populates Context7System.library_name + NeuledgeSystem.library
via hasattr — systems-agnostic, no isinstance ladder.

Pydocs-mcp has no matching field and is unaffected."
```

---

## Task 4: Latency observation + percentile aggregator

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/runner.py` (time index/search per task, emit `*_seconds` metrics, aggregate percentiles)
- Modify: `benchmarks/src/benchmarks/eval/metrics/aggregate.py` (add `percentile()` helper)
- Modify: `benchmarks/src/benchmarks/eval/report.py` (latency cell renderer)
- Modify: `benchmarks/tests/eval/test_aggregate.py` (percentile tests)
- Modify: `benchmarks/tests/eval/test_runner_smoke.py` (assert latency emitted)
- Modify: `benchmarks/tests/eval/test_report.py` (assert latency rendered)

- [ ] **Step 1: Write failing test for `percentile()`**

Add to `benchmarks/tests/eval/test_aggregate.py`:

```python
def test_percentile_simple_linear() -> None:
    """Linear-interpolation percentile, matching numpy default convention.
    percentile([1, 2, 3, 4], 0.5) == 2.5 (midpoint of 2 and 3)."""
    from benchmarks.eval.metrics.aggregate import percentile
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.5


def test_percentile_extremes() -> None:
    from benchmarks.eval.metrics.aggregate import percentile
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 1.0) == 4.0


def test_percentile_p95_on_100_values() -> None:
    from benchmarks.eval.metrics.aggregate import percentile
    values = [float(i) for i in range(100)]  # 0..99
    # p95 = 0.95 * 99 = 94.05 → 94 + 0.05 * (95 - 94) = 94.05
    assert percentile(values, 0.95) == pytest.approx(94.05)


def test_percentile_empty_returns_zero() -> None:
    from benchmarks.eval.metrics.aggregate import percentile
    assert percentile([], 0.5) == 0.0


def test_percentile_deterministic_on_repeated_calls() -> None:
    from benchmarks.eval.metrics.aggregate import percentile
    values = [0.1, 0.3, 0.5, 0.7, 0.9]
    p50_a = percentile(values, 0.5)
    p50_b = percentile(values, 0.5)
    assert p50_a == p50_b  # no internal randomness
```

- [ ] **Step 2: Run tests to verify FAIL**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_aggregate.py -v
```

Expected: 5 new tests fail with `ImportError`.

- [ ] **Step 3: Implement `percentile()`**

Append to `benchmarks/src/benchmarks/eval/metrics/aggregate.py`:

```python
def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, deterministic. Empty → 0.0.

    ``q`` is in [0, 1]. Matches numpy.percentile's default ``linear``
    interpolation method so external comparisons stay sane.
    """
    if not values:
        return 0.0
    s = sorted(values)
    k = q * (len(s) - 1)
    f = int(k)
    if f >= len(s) - 1:
        return s[-1]
    frac = k - f
    return s[f] + frac * (s[f + 1] - s[f])
```

- [ ] **Step 4: Run percentile tests to verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_aggregate.py -v
```

Expected: all percentile tests passing.

- [ ] **Step 5: Write failing test — runner emits `*_seconds` metrics**

Add to `benchmarks/tests/eval/test_runner_smoke.py`:

```python
@pytest.mark.asyncio
async def test_runner_emits_latency_metrics(tmp_path) -> None:
    """Per-task indexing_seconds / search_seconds + aggregate
    *_seconds_p50/p95/p99 land in the JSONL output."""
    fixture = Path(__file__).parent / "fixtures" / "repoqa_mini.json"
    config_yaml = tmp_path / "baseline.yaml"
    config_yaml.write_text("")  # empty overlay = defaults
    jsonl_dir = tmp_path / "jsonl"

    from benchmarks.eval.runner import run_sweep
    results, tasks_ran = await run_sweep(
        systems=("pydocs-mcp",),
        config_paths=(config_yaml,),
        dataset_name="repoqa",
        dataset_kwargs={"fixture_path": fixture},
        tracker_names=("jsonl",),
        tracker_kwargs={"jsonl": {"output_dir": jsonl_dir}},
        limit=2,
    )

    jsonl_file = next(iter(jsonl_dir.glob("*.jsonl")))
    lines = [json.loads(line) for line in jsonl_file.read_text().splitlines()]
    metric_names = {
        line["name"] for line in lines if line.get("_event") == "metric"
    }
    assert "indexing_seconds" in metric_names
    assert "search_seconds" in metric_names
    assert "indexing_seconds_p50" in metric_names
    assert "indexing_seconds_p95" in metric_names
    assert "search_seconds_p99" in metric_names
```

- [ ] **Step 6: Run test to verify FAIL**

Expected: AssertionError — `indexing_seconds` not in metric names.

- [ ] **Step 7: Wire latency observation in `run_sweep`**

In `benchmarks/src/benchmarks/eval/runner.py`, inside the per-task loop:

```python
import time

async for task in dataset.tasks():
    if limit is not None and count >= limit:
        break

    _maybe_set_library(system, task.metadata)
    corpus_dir = task.corpus_source()
    try:
        t0 = time.perf_counter()
        await system.index(corpus_dir, config)
        index_secs = time.perf_counter() - t0

        t1 = time.perf_counter()
        retrieved = await system.search(task.query, limit=10)
        search_secs = time.perf_counter() - t1

        scores = scorer.score(task, retrieved)
        for metric_name, value in scores.items():
            per_metric_values[metric_name].append(value)
            for h, tracker in zip(handles, trackers):
                tracker.log_metric(h, metric_name, value, step=count)
        # WHY: latency is an observation, not a Metric — see spec §5.5.
        per_metric_values.setdefault("indexing_seconds", []).append(index_secs)
        per_metric_values.setdefault("search_seconds", []).append(search_secs)
        for h, tracker in zip(handles, trackers):
            tracker.log_metric(h, "indexing_seconds", index_secs, step=count)
            tracker.log_metric(h, "search_seconds", search_secs, step=count)
    finally:
        shutil.rmtree(corpus_dir, ignore_errors=True)
    count += 1
```

After the per-task loop (still inside `run_sweep`), add the percentile aggregation:

```python
from .metrics.aggregate import percentile  # add to imports at top

# Existing mean+CI aggregation for quality metrics stays unchanged.

# Latency aggregation — p50/p95/p99 per latency key.
for latency_key in ("indexing_seconds", "search_seconds"):
    values = per_metric_values.get(latency_key, [])
    p50 = percentile(values, 0.5)
    p95 = percentile(values, 0.95)
    p99 = percentile(values, 0.99)
    aggregates[latency_key] = (p50, p95, p99)
    for h, tracker in zip(handles, trackers):
        tracker.log_metric(h, f"{latency_key}_p50", p50, step=None)
        tracker.log_metric(h, f"{latency_key}_p95", p95, step=None)
        tracker.log_metric(h, f"{latency_key}_p99", p99, step=None)
```

- [ ] **Step 8: Run smoke test to verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_runner_smoke.py -v
```

Expected: all runner smoke tests passing including the new latency one.

- [ ] **Step 9: Update `report.py` for latency cells**

In `benchmarks/src/benchmarks/eval/report.py`, locate `_format_cell` (or similar). Replace with:

```python
def _is_latency_metric(name: str) -> bool:
    return name.endswith("_seconds")


def _format_cell(metric_name: str, triple: tuple[float, float, float]) -> str:
    if _is_latency_metric(metric_name):
        p50, p95, p99 = triple
        return f"p50 {p50:.2f}s | p95 {p95:.2f}s | p99 {p99:.2f}s"
    mean, ci_low, ci_high = triple
    return f"{mean:.1%} [{ci_low:.1%}, {ci_high:.1%}]"
```

Update the row-rendering loop to pass `metric_name` to `_format_cell`.

- [ ] **Step 10: Write failing test for latency cell rendering**

Add to `benchmarks/tests/eval/test_report.py`:

```python
def test_format_report_renders_latency_cells_as_percentile_triple() -> None:
    from benchmarks.eval.report import format_report

    results = {
        ("pydocs-mcp", "baseline"): {
            "recall@1": (0.6, 0.4, 0.8),
            "indexing_seconds": (0.5, 1.2, 2.1),
        },
    }
    out = format_report(
        sweep_results=results, dataset_name="repoqa-fixture", n_tasks=5,
    )
    # Quality row uses percent + CI
    assert "60.0% [40.0%, 80.0%]" in out
    # Latency row uses p50/p95/p99
    assert "p50 0.50s | p95 1.20s | p99 2.10s" in out
```

Run + verify PASS.

- [ ] **Step 11: Full benchmarks suite + ruff**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q
.venv/bin/ruff check benchmarks/
```

Expected: 110+ passing, ruff clean.

- [ ] **Step 12: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py \
        benchmarks/src/benchmarks/eval/metrics/aggregate.py \
        benchmarks/src/benchmarks/eval/report.py \
        benchmarks/tests/eval/test_aggregate.py \
        benchmarks/tests/eval/test_runner_smoke.py \
        benchmarks/tests/eval/test_report.py
git commit -m "feat(benchmarks): latency observations + percentile aggregator

Runner times each system.index/search and emits indexing_seconds /
search_seconds per task (step=task_index). After the task loop, it
computes p50/p95/p99 and logs <key>_p50/p95/p99 with step=None.

New aggregate.percentile(values, q) — linear-interpolation, matches
numpy.percentile's default convention. Deterministic on repeated calls
and on equal-value runs.

report.py renders latency rows as 'p50 X.XXs | p95 ... | p99 ...' and
quality rows unchanged as 'X.X% [lo, hi]'. The triple-of-3 semantic is
disambiguated by metric-name suffix (_seconds → percentile)."
```

---

## Task 5: Capture the real measured baseline

**Files:**
- Modify: `benchmarks/baselines/repoqa_snf.json` (real numbers from a full sweep)
- The full sweep itself takes 5-10 minutes; this task IS the run + the commit.

- [ ] **Step 1: Run the full sweep against the real dataset**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect

# WHY: first invocation downloads the gzip; subsequent invocations use cache.
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --trackers jsonl
```

Expected: 5-10 minutes. Final stdout prints the report markdown + the JSONL output path.

- [ ] **Step 2: Extract aggregates from the JSONL output**

```bash
LATEST_JSONL=$(ls -t benchmarks/results/jsonl/*.jsonl | head -1)
echo "JSONL: $LATEST_JSONL"

python3 <<EOF
import json
from pathlib import Path
import datetime
import subprocess

lines = [json.loads(l) for l in Path("$LATEST_JSONL").read_text().splitlines()]
aggs = {}
for line in lines:
    if line.get("_event") != "metric":
        continue
    name = line["name"]
    if name.endswith("_mean") or name.endswith("_ci_low") or name.endswith("_ci_high"):
        metric, suffix = name.rsplit("_", 1)
        aggs.setdefault(metric, {})[suffix] = float(line["value"])
    elif name.endswith("_p50") or name.endswith("_p95") or name.endswith("_p99"):
        metric, suffix = name.rsplit("_", 1)
        aggs.setdefault(metric, {})[suffix] = float(line["value"])

# Tasks_ran from the run header
header = next(l for l in lines if l.get("_event") == "run_start")
git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()

baseline = {
    "dataset": "repoqa-2024-06-23-python",
    "system": "pydocs-mcp",
    "config": "baseline",
    "tasks_ran": header.get("params", {}).get("__tasks_ran__", 100),
    "metrics": aggs,
    "captured_at": datetime.datetime.utcnow().isoformat() + "Z",
    "git_sha": git_sha,
}

with open("benchmarks/baselines/repoqa_snf.json", "w") as f:
    json.dump(baseline, f, indent=2)
print("Baseline written:", json.dumps(aggs, indent=2)[:500])
EOF
```

- [ ] **Step 3: Manually inspect the new baseline**

```bash
cat benchmarks/baselines/repoqa_snf.json | python3 -m json.tool | head -40
```

Verify: tasks_ran ≈ 100, every metric has the expected aggregate keys, latency keys have p50/p95/p99 (not mean/ci_low/ci_high).

- [ ] **Step 4: Confirm `ci_compare` still passes against the new baseline**

```bash
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.ci_compare \
    --baseline benchmarks/baselines/repoqa_snf.json \
    --current "$LATEST_JSONL" \
    --metric recall@10 \
    --threshold 0.02
```

Expected: OK (the JSONL we just produced IS the baseline source).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/baselines/repoqa_snf.json
git commit -m "feat(benchmarks): first real measured baseline against RepoQA 100-needle Python subset

Captured by running:
  PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \\
      --systems pydocs-mcp --configs benchmarks/configs/baseline.yaml \\
      --dataset repoqa --trackers jsonl

Replaces the fixture-extrapolated placeholder with REAL measured
numbers from the 100-needle Python subset of repoqa-2024-06-23.

Run env: $(git rev-parse --short HEAD), $(uname -sr).
Baseline JSON tracks the git SHA + UTC timestamp at capture time."
```

---

## Task 6: Final verification gauntlet + push

- [ ] **Step 1: Run the full pytest suite (main repo + benchmarks)**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect

.venv/bin/pytest -q                                              # main repo: 996+ passing
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q  # benchmarks: 110+ passing
```

- [ ] **Step 2: Ruff clean**

```bash
.venv/bin/ruff check python/ benchmarks/ tests/
```

- [ ] **Step 3: Coverage check**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q --cov=benchmarks/src --cov-report=term 2>&1 | tail -10
```

Expected: ≥85% production coverage (matches PR #26 baseline; the new code paths should be covered by the new tests).

- [ ] **Step 4: Smoke-test the CLI end-to-end against the fixture**

```bash
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --fixture benchmarks/tests/eval/fixtures/repoqa_mini.json \
    --trackers jsonl \
    --limit 2
```

Expected: runs end-to-end in <30s; emits a JSONL file with both quality and latency metrics.

- [ ] **Step 5: Push + verify CI**

```bash
git push
gh pr checks 27 --watch
```

Expected: all 5 CI jobs (python 3.11/3.12/3.13, rust, benchmark-repoqa) green.

- [ ] **Step 6: Take PR out of draft (optional — gated on user approval)**

```bash
gh pr ready 27
```

---

## Reused helpers / patterns

- `materialize_corpus(files)` — unchanged from PR #26; the new loader passes the repo's `content` dict (a fuller view than the placeholder's `files` field).
- `_canonical_dump` lru_cache in `ast_match.py` — still amortizes AST parses across metrics. The new `_extract_body` operates on raw source strings, NOT ASTs, so it doesn't compete with that cache.
- `_close_all` + tracker close-on-failure — unchanged from PR #26.
- The `try/finally rmtree` per-task cleanup — unchanged.

## Verification gates (post-implementation)

Every task ends with `pytest -q` showing the expected passing count. The whole PR is reviewable in three independent slices:

1. **Resolver knob** (Task 1) — pydocs_mcp/ change; isolated.
2. **Loader rewrite + library wiring + latency** (Tasks 2-4) — benchmarks/eval/ changes; integrated via the runner.
3. **Real baseline** (Task 5) — single JSON file; one-shot capture.

After all 6 tasks land: dispatch the final code-reviewer subagent across the entire diff, surface findings for user approval, then take PR #27 out of draft.
