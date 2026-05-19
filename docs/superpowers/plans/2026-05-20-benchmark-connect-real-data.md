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
| RepoQA loader (Task 2) | Real-schema field mapping (`name`, `description`, `path`, `start_line/end_line`) | Stdlib-only, deterministic cache key, body-extraction off-by-one, atomic write |
| Runner library wiring (Task 3) | Systems-agnostic via `LibraryConfigurable` Protocol; runs BEFORE `index()` | No `isinstance` ladder; pure side-effect on system instance |
| Latency aggregator (Task 4) | Per-task `step=task_index` emit + final percentile aggregate | `percentile()` matches numpy default convention; deterministic on equal values |
| Real baseline (Task 5) | Numbers from a REAL run, not extrapolation | Commit message documents the env + git SHA the baseline was captured at |

**Authorship rule (every commit in this plan):** the user's global CLAUDE.md mandates all commits authored by `msobroza` ONLY. **Do NOT add `Co-Authored-By:` trailers to any commit message in this plan.** This applies whether commits are made directly or by dispatched subagents.

**Revisions from engineering review** (applied 2026-05-20 — supersedes initial draft):
- Task 0 now greps for `[repoqa]` references in README/docs (M1).
- Task 1 now updates `defaults/default_config.yaml` for the new `strict_suffix` field (M6).
- Task 2 ordering rewritten: existing-test rescue BEFORE fixture shape flip (C1, C2). Adds atomic write (I2), mixed-newline test (I3), and `asyncio.to_thread` wrapping for the network call (I6).
- Task 3 adopts a `LibraryConfigurable` Protocol instead of bare `hasattr` (I1) and adds a no-op test for systems without library fields (I5).
- Task 4 updates the existing `test_runner_smoke_pydocs_jsonl_fixture` count assertions in lockstep with the new latency records (C5).
- Task 5 splits the baseline into two files — `repoqa_snf.json` (real numbers) and `repoqa_fixture_baseline.json` (CI gate source) — to keep `ci_compare` hermetic (C6). The tautological self-compare step is removed (I7) and the heredoc is hardened (I8).

---

## Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect/`
Branch: `feature/benchmark-connect-to-real-data` (already pushed as draft PR #27 with the spec).

---

## Task 0: Drop the `[repoqa]` optional extra from `pyproject.toml`

**Files:**
- Modify: `benchmarks/pyproject.toml`
- Possibly modify: `benchmarks/README.md` + any other doc referencing `[repoqa]`

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

- [ ] **Step 3: Grep for residual `[repoqa]` / `datasets>=2.0` doc references**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect
grep -RIn "\[repoqa\]\|datasets>=2.0\|huggingface-hub" benchmarks/ docs/ README.md 2>/dev/null | grep -v "\.lock\|pyproject.toml"
```

For every hit:
- If it's an install instruction (e.g., `pip install -e .[repoqa]`), remove the `[repoqa]` extra reference — the loader is stdlib-only now.
- If it explains "RepoQA pulled from HuggingFace via `datasets`", replace with "RepoQA pulled from GitHub Releases via `urllib`".

Expected hits: `benchmarks/README.md` (if present), possibly `docs/superpowers/plans/*.md` (PR #26's plan). Update the README; leave historical plan docs unchanged.

- [ ] **Step 4: Verify baseline tests still pass**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q
```

Expected: 100 passing (unchanged — `[repoqa]` was unused at runtime).

- [ ] **Step 5: Commit**

```bash
git add benchmarks/pyproject.toml benchmarks/README.md 2>/dev/null  # README only if it changed
git commit -m "chore(benchmarks): drop [repoqa] optional extra — RepoQA is not on HuggingFace"
```

---

## Task 1: `strict_suffix` knob on `ReferenceResolverConfig`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py` (add field)
- Modify: `python/pydocs_mcp/extraction/strategies/reference_resolver.py` (gate Rule C)
- Modify: `python/pydocs_mcp/application/indexing_service.py` (thread the toggle through resolver construction)
- Modify: `python/pydocs_mcp/defaults/default_config.yaml` (publish the new tunable's default)
- Modify: `tests/extraction/test_reference_resolver.py` (new test)
- Verify: `benchmarks/configs/strict_suffix_off.yaml` already exists from PR #26 — verify it loads cleanly

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

- [ ] **Step 6: Update `defaults/default_config.yaml` for the new tunable**

CLAUDE.md §"MCP API surface vs YAML configuration" says `default_config.yaml` is "the canonical reference of every tunable". Add `strict_suffix: true` under the `reference_graph.resolver` block in `python/pydocs_mcp/defaults/default_config.yaml`:

```yaml
reference_graph:
  resolver:
    include_stdlib: true
    strict_suffix: true   # ablation knob — see ReferenceResolverConfig
```

Smoke-load the defaults to make sure pydantic's `extra="forbid"` is happy:

```bash
.venv/bin/python -c "
from pydocs_mcp.retrieval.config import AppConfig
cfg = AppConfig.load()
assert cfg.reference_graph.resolver.strict_suffix is True
print('defaults.strict_suffix =', cfg.reference_graph.resolver.strict_suffix)
"
```

Expected: `defaults.strict_suffix = True`.

- [ ] **Step 7: Run test to verify PASS**

```bash
.venv/bin/pytest tests/extraction/test_reference_resolver.py::test_strict_suffix_off_skips_rule_c -v
```

Expected: PASS.

- [ ] **Step 8: Verify `benchmarks/configs/strict_suffix_off.yaml` loads cleanly**

```bash
.venv/bin/python -c "
from pydocs_mcp.retrieval.config import AppConfig
cfg = AppConfig.load(explicit_path='benchmarks/configs/strict_suffix_off.yaml')
assert cfg.reference_graph.resolver.strict_suffix is False
print('strict_suffix_off.yaml loads:', cfg.reference_graph.resolver.strict_suffix)
"
```

Expected: `strict_suffix_off.yaml loads: False`.

- [ ] **Step 9: Run full suite**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check python/ benchmarks/
```

Expected: all 996+ tests passing, ruff clean.

- [ ] **Step 10: Commit**

```bash
git add python/pydocs_mcp/retrieval/config.py \
        python/pydocs_mcp/extraction/strategies/reference_resolver.py \
        python/pydocs_mcp/application/indexing_service.py \
        python/pydocs_mcp/defaults/default_config.yaml \
        tests/extraction/test_reference_resolver.py
git commit -m "feat(resolver): strict_suffix toggle on ReferenceResolverConfig

Adds a YAML-driven ablation knob — strict_suffix=False skips Rule C
(suffix-within-package) so the harness can measure Rule C's contribution
to AC #15 resolution rate against a baseline.

Default stays True (pre-PR behavior). defaults/default_config.yaml
publishes the new tunable. benchmarks/configs/strict_suffix_off.yaml
now loads cleanly through AppConfig.load."
```

---

## Task 2: Rewrite `RepoQADataset` for the GitHub-Releases distribution

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/datasets/repoqa.py` (full rewrite — stdlib-only, atomic write, `asyncio.to_thread` wrapping)
- Modify: `benchmarks/tests/eval/fixtures/repoqa_mini.json` (rewrite in real schema — **1 repo × 5 needles**, preserves `tasks_ran == 5` smoke-test invariant)
- Modify: `benchmarks/tests/eval/test_repoqa_loader.py` (updated for new schema + stub URL fetch + atomic-write test + mixed-newline test)
- **Modify: `benchmarks/tests/eval/test_scorer_e2e.py`** (rewrite `_load_fixture_tasks` to consume `RepoQADataset` instead of raw JSON — see Finding C1)

**Ordering rationale** (Finding C2): the old plan flipped the fixture schema in Step 1 and rewrote the loader in Step 3 simultaneously, leaving everything broken between commits. The new ordering walks two clean half-steps: (1) rescue the *fixture-consumer* test file by moving it onto the `RepoQADataset` interface so we never read raw JSON in test code, then (2) flip the schema + loader together — at which point both layers move in lockstep and the test suite returns to green in a single commit.

- [ ] **Step 1: Rescue `test_scorer_e2e.py::_load_fixture_tasks` — move it onto the `RepoQADataset` interface**

In `benchmarks/tests/eval/test_scorer_e2e.py`, replace `_load_fixture_tasks`:

```python
import asyncio
from benchmarks.eval.datasets.repoqa import RepoQADataset

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


def _load_fixture_tasks() -> list[EvalTask]:
    """Consume the Dataset Protocol — the same path the runner walks.
    Decouples this test from the on-disk JSON shape so future schema
    changes only touch the loader."""
    async def _collect() -> list[EvalTask]:
        dataset = RepoQADataset(fixture_path=_FIXTURE)
        return [t async for t in dataset.tasks()]
    return asyncio.run(_collect())
```

Delete the old import (`from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer` — wait, keep `EvalTask` for the return type; remove `GoldAnswer` if no longer referenced) and the old JSON-walking body. Leave `_oracle_retrieved`, `_gold_at_rank_3_retrieved`, and every test body unchanged — they consume `EvalTask` objects, not raw rows.

- [ ] **Step 2: Run the test_scorer_e2e suite to verify FAIL**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_scorer_e2e.py -v
```

Expected: the 3 tests fail because the OLD `RepoQADataset` still expects HuggingFace schema and the OLD fixture is flat-rows; either path errors out. This is the intentional "all-loader-readers broken" mid-state.

- [ ] **Step 3: Replace the fixture file** at `benchmarks/tests/eval/fixtures/repoqa_mini.json`

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
        "fixture_repo/math_helpers.py": "\"\"\"Module docstring.\"\"\"\n\ndef factorial(n: int) -> int:\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n\n\ndef fibonacci(n: int) -> int:\n    if n < 2:\n        return n\n    return fibonacci(n - 1) + fibonacci(n - 2)\n\n\ndef is_prime(n: int) -> bool:\n    if n < 2:\n        return False\n    for i in range(2, int(n**0.5) + 1):\n        if n % i == 0:\n            return False\n    return True\n\n\ndef gcd(a: int, b: int) -> int:\n    while b:\n        a, b = b, a % b\n    return a\n\n\ndef lcm(a: int, b: int) -> int:\n    return a * b // gcd(a, b)\n"
      },
      "dependency": {},
      "functions": {},
      "needles": [
        {"name": "factorial", "description": "Compute the factorial of a non-negative integer.", "path": "fixture_repo/math_helpers.py", "start_line": 3, "end_line": 6, "start_byte": 25, "end_byte": 130, "global_start_byte": 0, "global_end_byte": 0, "global_start_line": 0, "global_end_line": 0, "code_ratio": 0.5},
        {"name": "fibonacci", "description": "Compute the n-th Fibonacci number.", "path": "fixture_repo/math_helpers.py", "start_line": 9, "end_line": 12, "start_byte": 140, "end_byte": 240, "global_start_byte": 0, "global_end_byte": 0, "global_start_line": 0, "global_end_line": 0, "code_ratio": 0.5},
        {"name": "is_prime", "description": "Test whether n is prime.", "path": "fixture_repo/math_helpers.py", "start_line": 15, "end_line": 21, "start_byte": 250, "end_byte": 400, "global_start_byte": 0, "global_end_byte": 0, "global_start_line": 0, "global_end_line": 0, "code_ratio": 0.5},
        {"name": "gcd", "description": "Greatest common divisor of two integers.", "path": "fixture_repo/math_helpers.py", "start_line": 24, "end_line": 27, "start_byte": 410, "end_byte": 480, "global_start_byte": 0, "global_end_byte": 0, "global_start_line": 0, "global_end_line": 0, "code_ratio": 0.5},
        {"name": "lcm", "description": "Least common multiple via gcd.", "path": "fixture_repo/math_helpers.py", "start_line": 30, "end_line": 31, "start_byte": 490, "end_byte": 550, "global_start_byte": 0, "global_end_byte": 0, "global_start_line": 0, "global_end_line": 0, "code_ratio": 0.5}
      ]
    }
  ]
}
```

**Why 1 repo × 5 needles, not 1 × 2:** the existing `test_runner_smoke_returns_aggregate_tuple_shape` (`test_runner_smoke.py:190`) asserts `tasks_ran == 5`. Keeping the new fixture at 5 needles preserves that invariant — no cross-test breakage.

- [ ] **Step 4: Replace `benchmarks/tests/eval/test_repoqa_loader.py`**

```python
"""RepoQADataset tests — fixture-only by default, urllib stubbed for the
release path (no network in tests)."""
from __future__ import annotations

import asyncio
import gzip
import json
import urllib.request
from pathlib import Path

import pytest
from benchmarks.eval.datasets.repoqa import (
    RepoQADataset,
    _extract_body,
)
from benchmarks.eval.datasets.base_dataset import Dataset

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


def _collect(dataset: RepoQADataset) -> list:
    async def _go():
        return [t async for t in dataset.tasks()]
    return asyncio.run(_go())


@pytest.mark.asyncio
async def test_fixture_yields_five_tasks() -> None:
    """1 Python repo × 5 needles → 5 EvalTasks."""
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
    assert len(tasks) == 5


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
    assert "def factorial" in tasks[0].gold.ast_body
    assert "def fibonacci" in tasks[1].gold.ast_body


@pytest.mark.asyncio
async def test_task_id_includes_repo_sha_path_name() -> None:
    dataset = RepoQADataset(fixture_path=FIXTURE_PATH)
    tasks = [t async for t in dataset.tasks()]
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


def test_extract_body_handles_mixed_line_endings() -> None:
    """RepoQA needles can carry any of \\n / \\r\\n / \\r line endings — the
    extraction must produce the same body regardless of source convention.
    (Spec §8 risk row.)"""
    source_unix = "line1\nline2\nline3\nline4\n"
    source_win = "line1\r\nline2\r\nline3\r\nline4\r\n"
    source_old_mac = "line1\rline2\rline3\rline4\r"
    # 1-indexed inclusive lines 2..3 → "line2", "line3"
    assert _extract_body(source_unix, 2, 3) == "line2\nline3"
    assert _extract_body(source_win, 2, 3) == "line2\nline3"
    assert _extract_body(source_old_mac, 2, 3) == "line2\nline3"


@pytest.mark.asyncio
async def test_release_download_path_uses_urllib(monkeypatch, tmp_path) -> None:
    """When no fixture is provided, the loader fetches the GitHub release via
    urllib. Stub urlopen to return a tiny gzipped JSON — no network."""
    fake_payload = json.dumps({"python": [], "go": []}).encode()
    fake_gz = gzip.compress(fake_payload)

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *exc): return None
        def read(self): return fake_gz

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: _FakeResp(),
    )

    dataset = RepoQADataset(cache_dir=tmp_path)
    tasks = [t async for t in dataset.tasks()]
    assert tasks == []
    assert (tmp_path / "repoqa-2024-06-23.json").exists()


@pytest.mark.asyncio
async def test_release_download_corrupt_payload_does_not_clobber_cache(
    monkeypatch, tmp_path,
) -> None:
    """A gzipped download that decompresses to non-JSON must NOT leave a
    'good-looking' cache file behind. Atomic-write contract:
    write-to-tmp → validate-JSON → os.replace into place."""
    fake_gz = gzip.compress(b"not valid json{][")

    class _FakeResp:
        def __enter__(self): return self
        def __exit__(self, *exc): return None
        def read(self): return fake_gz

    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=None: _FakeResp(),
    )

    dataset = RepoQADataset(cache_dir=tmp_path)
    with pytest.raises(json.JSONDecodeError):
        _ = [t async for t in dataset.tasks()]
    # The final cache file must not exist — atomic write aborts before replace.
    assert not (tmp_path / "repoqa-2024-06-23.json").exists()
```

- [ ] **Step 5: Run loader tests + scorer tests to verify FAIL**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest \
    benchmarks/tests/eval/test_repoqa_loader.py \
    benchmarks/tests/eval/test_scorer_e2e.py -v
```

Expected: most tests fail because the loader still expects the old HF schema. `test_scorer_e2e.py` may also fail since the fixture is now nested-by-language. This is the intentional pre-impl state.

- [ ] **Step 6: Rewrite `benchmarks/src/benchmarks/eval/datasets/repoqa.py`**

Full rewrite — replace the file contents:

```python
"""RepoQA-SNF dataset loader (spec §5.1).

RepoQA is distributed as a single gzipped JSON file from
``evalplus/repoqa_release`` GitHub Releases. Stdlib-only — no
``datasets`` / ``huggingface-hub`` dependency.
"""
from __future__ import annotations

import asyncio
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

# WHY: the date-tagged GitHub release. To bump: download the new gz,
# run _flatten_needles + _row_to_task on it, verify _extract_body produces
# valid Python for 3 sample needles, then update this constant.
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
            if self.fixture_path is not None:
                self._rows_cache = self._load_from_fixture()
            else:
                # WHY: urllib.urlopen + gzip.decompress are sync + CPU-bound.
                # The runner loop is async (CLAUDE.md §"Async Patterns") — offload
                # to a worker thread so the 12MB download doesn't block the event loop.
                self._rows_cache = await asyncio.to_thread(self._load_from_release)
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
            self._download_release_atomic(target)
        data = json.loads(target.read_text())
        return _flatten_needles(data.get(self.language, []))

    def _download_release_atomic(self, target: Path) -> None:
        # WHY: a partial / corrupt download must NOT masquerade as a good
        # cache file. Pattern: write to .tmp, validate JSON parses, then
        # os.replace into place. If JSON validation fails we propagate the
        # decode error and the .tmp file gets garbage-collected by the OS.
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(version=self.revision)
        tmp = target.with_suffix(target.suffix + ".tmp")
        with urllib.request.urlopen(url, timeout=60) as resp:  # noqa: S310
            payload = gzip.decompress(resp.read())
        # Validate JSON shape before publishing. If this raises, the
        # decode error propagates and `target` is never created.
        json.loads(payload.decode())
        tmp.write_bytes(payload)
        tmp.replace(target)


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
    """1-indexed inclusive line slice. ``splitlines()`` normalizes mixed
    line endings (\\n, \\r\\n, \\r) so the body is reconstructed with a
    canonical \\n separator — extraction is endpoint-stable regardless of
    the source's line-ending convention."""
    lines = source.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])
```

- [ ] **Step 7: Run loader + scorer suites to verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest \
    benchmarks/tests/eval/test_repoqa_loader.py \
    benchmarks/tests/eval/test_scorer_e2e.py -v
```

Expected: 10 loader tests pass (8 existing-style + 2 new — mixed-newline + corrupt-payload); 3 scorer tests pass (oracle = 1.0, rank-3 = MRR 1/3, empty = 0.0).

- [ ] **Step 8: Run full benchmarks suite + ruff**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q
.venv/bin/ruff check benchmarks/
```

Expected: 100+ passing, ruff clean. (Net new tests in this task: ≈ +2 in loader. Existing scorer + smoke tests still pass because Task 3 fixture preserves the 5-needle count.)

- [ ] **Step 9: Commit**

```bash
git add benchmarks/src/benchmarks/eval/datasets/repoqa.py \
        benchmarks/tests/eval/fixtures/repoqa_mini.json \
        benchmarks/tests/eval/test_repoqa_loader.py \
        benchmarks/tests/eval/test_scorer_e2e.py
git commit -m "feat(benchmarks): rewrite RepoQA loader for GitHub Releases distribution

RepoQA is not on HuggingFace. Real distribution is a gzipped JSON
from evalplus/repoqa_release GitHub Releases. Stdlib-only — no
datasets/huggingface-hub dependency.

New shape:
- _PINNED_RELEASE_VERSION = '2024-06-23'
- _load_from_release uses urllib.request + gzip wrapped in
  asyncio.to_thread so the runner's event loop doesn't block
- Atomic download: write to .tmp, validate JSON parses, then
  os.replace into target. Corrupt payloads never become cache files.
- One row per needle (not per repo); gold body extracted from
  content[needle.path] using start_line/end_line; splitlines()
  normalizes mixed line endings.
- Metadata carries repo, commit_sha, topic, needle_name, needle_path
- Fixture rewritten in real schema (1 repo, 5 needles — preserves
  tasks_ran == 5 invariant in test_runner_smoke)
- test_scorer_e2e.py rescued: _load_fixture_tasks now consumes the
  RepoQADataset Protocol instead of raw JSON access"
```

---

## Task 3: Runner library wiring for Context7 / Neuledge

**Files:**
- Modify: `benchmarks/src/benchmarks/eval/systems/base_system.py` (add `HasLibraryName` + `HasLibrary` Protocols)
- Modify: `benchmarks/src/benchmarks/eval/runner.py` (add `_maybe_set_library` helper + call site)
- Modify: `benchmarks/tests/eval/test_runner_smoke.py` (assert library is seeded; no-op for bare system)

**Design note** (Finding I1): the original plan used a bare `hasattr` check. That works but the contract is invisible — a future system that happens to expose an unrelated `library_name` field would silently get repo-name injection. Two `runtime_checkable` Protocols (`HasLibraryName`, `HasLibrary`) make the opt-in explicit while preserving the same `hasattr` semantics under the hood (Protocols check via `hasattr`).

- [ ] **Step 1: Write the failing tests**

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


def test_maybe_set_library_noop_on_system_without_fields() -> None:
    """Pydocs-mcp doesn't declare library_name / library — the runner
    helper must be a strict no-op (no setattr fallback). Finding I5."""
    from benchmarks.eval.runner import _maybe_set_library

    class _Bare:
        name = "bare"

    bare = _Bare()
    _maybe_set_library(bare, {"repo": "psf/black", "commit": "abcdef1234"})
    assert not hasattr(bare, "library_name")
    assert not hasattr(bare, "library")


def test_maybe_set_library_noop_when_metadata_missing_repo() -> None:
    """If task.metadata lacks 'repo', the helper must not touch the system."""
    from benchmarks.eval.runner import _maybe_set_library

    class _Recorder:
        library_name: str = "initial"
        library: str = "initial"

    sys = _Recorder()
    _maybe_set_library(sys, {})
    assert sys.library_name == "initial"
    assert sys.library == "initial"
```

- [ ] **Step 2: Run tests to verify FAIL**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_runner_smoke.py -k library -v
```

Expected: `ImportError: cannot import name '_maybe_set_library'` for all three new tests.

- [ ] **Step 3: Define the opt-in Protocols**

Add to `benchmarks/src/benchmarks/eval/systems/base_system.py` (alongside the existing `RetrievedItem` / `System` Protocol):

```python
from typing import Protocol, runtime_checkable


@runtime_checkable
class HasLibraryName(Protocol):
    """A system that wants the human-readable library identifier
    (e.g. ``"psf/black"``) seeded from ``task.metadata['repo']`` before
    ``index()``. ``Context7System`` is the primary implementor."""
    library_name: str


@runtime_checkable
class HasLibrary(Protocol):
    """A system that wants the install identifier (``"{repo}@{commit[:7]}"``)
    seeded from ``task.metadata`` before ``index()``. ``NeuledgeSystem`` is
    the primary implementor."""
    library: str
```

- [ ] **Step 4: Add `_maybe_set_library` to `runner.py`**

In `benchmarks/src/benchmarks/eval/runner.py`, near the other private helpers:

```python
from .systems.base_system import HasLibrary, HasLibraryName


def _maybe_set_library(system: object, metadata: Mapping[str, str]) -> None:
    """Seed comparative-system library identifiers from task metadata.

    Systems-agnostic via two runtime_checkable Protocols:
    - ``HasLibraryName`` — the human name (e.g. ``"psf/black"``).
      Context7System opts in.
    - ``HasLibrary`` — the install identifier (e.g. ``"psf/black@abcdef1"``).
      NeuledgeSystem opts in.

    Pydocs-mcp implements neither and is a strict no-op.
    """
    repo = metadata.get("repo")
    if not repo:
        return
    if isinstance(system, HasLibraryName):
        system.library_name = repo
    if isinstance(system, HasLibrary):
        commit = metadata.get("commit", "")
        system.library = f"{repo}@{commit[:7]}" if commit else repo
```

- [ ] **Step 5: Wire `_maybe_set_library` into the per-task loop**

In the same file, inside `run_sweep`, BEFORE `corpus_dir = task.corpus_source()`:

```python
async for task in dataset.tasks():
    if limit is not None and count >= limit:
        break

    # WHY: comparative systems (Context7, Neuledge) need a library
    # identifier resolved from task metadata BEFORE index(). Opt-in
    # via the HasLibraryName / HasLibrary Protocols (systems/base_system.py).
    _maybe_set_library(system, task.metadata)

    corpus_dir = task.corpus_source()
    ...
```

- [ ] **Step 6: Run tests to verify PASS**

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_runner_smoke.py -v
```

Expected: all runner tests passing including the 3 new library-wiring tests.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/src/benchmarks/eval/runner.py \
        benchmarks/src/benchmarks/eval/systems/base_system.py \
        benchmarks/tests/eval/test_runner_smoke.py
git commit -m "feat(benchmarks): runner seeds library_name/library from task.metadata via opt-in Protocols

Adds two runtime_checkable Protocols to systems/base_system.py:
- HasLibraryName — human-readable library identifier
- HasLibrary — install identifier ({repo}@{commit[:7]})

Adds _maybe_set_library(system, metadata) called before every
system.index(). Routes by isinstance against the Protocols, so opt-in
is documented at the type level rather than left to bare hasattr —
prevents accidental injection into unrelated library_name fields.

Pydocs-mcp implements neither Protocol and is a strict no-op."
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

- [ ] **Step 8: Update existing smoke-test count assertions (Finding C5)**

Adding 2 new latency observations per task + 6 new aggregate records (3 percentiles × 2 latency keys) will break `test_runner_smoke_pydocs_jsonl_fixture` at `test_runner_smoke.py:78,81,82`. Update those line-pinned counts.

Old (at `test_runner_smoke.py:72-82`):

```python
    # Shape: 1 run_start + (limit × 5 metrics) per-task + 15 aggregate
    ...
    # 2 tasks × 5 metrics = 10 per-task, plus 5 × 3 = 15 aggregate.
    assert len(metric_records) == 10 + 15
    ...
    aggregate = [r for r in metric_records if r.get("step") is None]
    assert len(per_task) == 10
    assert len(aggregate) == 15
```

New:

```python
    # Shape: 1 run_start + (limit × (5 quality + 2 latency)) per-task +
    # (5 quality × 3 stats + 2 latency × 3 percentiles) aggregate
    ...
    # 2 tasks × 7 metrics = 14 per-task, plus 5×3 + 2×3 = 21 aggregate.
    assert len(metric_records) == 14 + 21
    ...
    aggregate = [r for r in metric_records if r.get("step") is None]
    assert len(per_task) == 14
    assert len(aggregate) == 21
```

Run + verify it still passes alongside the new latency assertions:

```bash
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/eval/test_runner_smoke.py -v
```

Expected: all runner smoke tests passing.

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

## Task 5: Capture the real measured baseline (and rewire CI to a fixture baseline)

**Files:**
- Create: `benchmarks/baselines/repoqa_fixture_baseline.json` (CI gate source — captured from fixture sweep)
- Modify: `benchmarks/baselines/repoqa_snf.json` (replaces fixture-extrapolated placeholder with real numbers — documentation only, not gated by CI)
- Modify: `.github/workflows/benchmark.yml` (point `ci_compare --baseline` at the new fixture baseline)

**Why two baselines now** (Finding C6): the existing CI workflow runs `--fixture repoqa_mini.json` and compares against `repoqa_snf.json`. If `repoqa_snf.json` carries real-data numbers (100 needles, different repos) but CI only ever runs the 5-needle fixture, the threshold gate (`--threshold 0.02`) fires on every PR. Split: `repoqa_fixture_baseline.json` is what CI compares against (fixture-vs-fixture, hermetic), and `repoqa_snf.json` is the real-data record kept alongside for documentation and out-of-CI manual comparisons.

- [ ] **Step 1: Capture the fixture baseline (this is what CI gates against)**

```bash
set -euo pipefail
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect

# Reuse the same CLI shape the CI workflow uses (--fixture, --limit 5).
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --fixture benchmarks/tests/eval/fixtures/repoqa_mini.json \
    --trackers jsonl \
    --limit 5

FIXTURE_JSONL=$(ls -t benchmarks/results/jsonl/*.jsonl | head -1)
echo "Fixture JSONL: $FIXTURE_JSONL"
```

- [ ] **Step 2: Run the full sweep against the real dataset**

```bash
set -euo pipefail
# WHY: first invocation downloads the 12MB gzip from GitHub Releases;
# subsequent invocations use the cache file at ~/.cache/pydocs-mcp/repoqa.
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --trackers jsonl

REAL_JSONL=$(ls -t benchmarks/results/jsonl/*.jsonl | head -1)
echo "Real JSONL: $REAL_JSONL"
```

Expected: 5-10 minutes. Final stdout prints the report markdown + the JSONL output path.

- [ ] **Step 3: Extract aggregates and write BOTH baseline JSONs**

```bash
set -euo pipefail

python3 - <<'PY'
import datetime
import json
import subprocess
from pathlib import Path

JSONL_DIR = Path("benchmarks/results/jsonl")
runs = sorted(JSONL_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
# Last two runs: Step 1 = fixture (older), Step 2 = real (newer)
fixture_jsonl, real_jsonl = runs[-2], runs[-1]

git_sha = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
now_iso = datetime.datetime.now(datetime.UTC).isoformat()


def _summarize(jsonl_path: Path, label: str, dataset_id: str) -> dict:
    lines = [json.loads(l) for l in jsonl_path.read_text().splitlines() if l.strip()]
    aggs: dict[str, dict[str, float]] = {}
    # Count of distinct (step != None) records with name == "recall@10" gives
    # tasks_ran. No magic number needed.
    tasks_ran = sum(
        1 for l in lines
        if l.get("_event") == "metric"
        and l.get("step") is not None
        and l.get("name") == "recall@10"
    )
    for line in lines:
        if line.get("_event") != "metric":
            continue
        name = line["name"]
        for suffix in ("_mean", "_ci_low", "_ci_high", "_p50", "_p95", "_p99"):
            if name.endswith(suffix):
                metric = name[: -len(suffix)]
                aggs.setdefault(metric, {})[suffix.lstrip("_")] = float(line["value"])
                break
    return {
        "dataset": dataset_id,
        "system": "pydocs-mcp",
        "config": "baseline",
        "tasks_ran": tasks_ran,
        "metrics": aggs,
        "captured_at": now_iso,
        "git_sha": git_sha,
        "source_jsonl": str(jsonl_path),
        "label": label,
    }


fixture_baseline = _summarize(
    fixture_jsonl, label="fixture-5-needles", dataset_id="repoqa-fixture-python",
)
real_baseline = _summarize(
    real_jsonl, label="real-100-needles", dataset_id="repoqa-2024-06-23-python",
)

Path("benchmarks/baselines/repoqa_fixture_baseline.json").write_text(
    json.dumps(fixture_baseline, indent=2) + "\n",
)
Path("benchmarks/baselines/repoqa_snf.json").write_text(
    json.dumps(real_baseline, indent=2) + "\n",
)
print("fixture tasks_ran:", fixture_baseline["tasks_ran"])
print("real tasks_ran:", real_baseline["tasks_ran"])
PY
```

Expected: `fixture tasks_ran: 5`, `real tasks_ran: 100`.

- [ ] **Step 4: Manually inspect both baselines**

```bash
python3 -m json.tool benchmarks/baselines/repoqa_fixture_baseline.json | head -30
python3 -m json.tool benchmarks/baselines/repoqa_snf.json | head -30
```

Verify: each baseline has its expected `tasks_ran`; every metric has `mean`/`ci_low`/`ci_high`; each `*_seconds` latency key has `p50`/`p95`/`p99`.

- [ ] **Step 5: Update `.github/workflows/benchmark.yml` to point at the fixture baseline**

In `.github/workflows/benchmark.yml`, locate the `Check recall@10 vs baseline` step and swap:

```yaml
      - name: Check recall@10 vs baseline
        run: |
          PYTHONPATH=benchmarks/src python -m benchmarks.eval.ci_compare \
            --baseline benchmarks/baselines/repoqa_fixture_baseline.json \
            --current 'benchmarks/results/jsonl/*.jsonl' \
            --metric recall@10 \
            --threshold 0.02
```

Update the WHY comment block above it to mention that `repoqa_snf.json` is a real-data record kept for documentation and that CI gates only against the fixture-derived baseline (hermetic / reproducible).

- [ ] **Step 6: Smoke-test the CI gate locally**

```bash
set -euo pipefail
PYTHONPATH=benchmarks/src .venv/bin/python -m benchmarks.eval.ci_compare \
    --baseline benchmarks/baselines/repoqa_fixture_baseline.json \
    --current "benchmarks/results/jsonl/*.jsonl" \
    --metric recall@10 \
    --threshold 0.02
```

Expected: OK (the latest fixture JSONL is the source of the baseline, so they agree within threshold).

- [ ] **Step 7: Commit**

```bash
git add benchmarks/baselines/repoqa_fixture_baseline.json \
        benchmarks/baselines/repoqa_snf.json \
        .github/workflows/benchmark.yml
git commit -m "feat(benchmarks): first real measured baseline + dedicated fixture CI baseline

Splits the baseline into two files:
- repoqa_fixture_baseline.json — captured from the 5-needle fixture
  sweep; this is what CI's ci_compare gates against (hermetic +
  reproducible).
- repoqa_snf.json — real measured numbers from the 100-needle Python
  subset of repoqa-2024-06-23, kept as a documentation record.

Updates .github/workflows/benchmark.yml to point ci_compare at the
fixture baseline so the gate stays meaningful (was comparing fixture
sweep against placeholder real-data baseline, now compares like for
like).

Each baseline tracks the source JSONL path, the git SHA, and the UTC
timestamp at capture time."
```

---

## Task 6: Final verification gauntlet + push

- [ ] **Step 1: Run the full pytest suite (main repo + benchmarks)**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-connect

.venv/bin/pytest -q                                              # main repo: 996+ passing
PYTHONPATH=benchmarks/src .venv/bin/pytest benchmarks/tests/ -q  # benchmarks: ~113 passing
                                                                  # (100 baseline + 1 strict_suffix
                                                                  #  + 2 loader (mixed-newline + atomic write)
                                                                  #  + 3 library wiring
                                                                  #  + 7 latency = ~113)
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
