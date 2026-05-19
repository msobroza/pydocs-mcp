# Benchmark Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Implement the SOLID-extensible benchmark harness per [spec](../specs/2026-05-19-benchmark-harness-repoqa-mlflow-solid-design.md). RepoQA-SNF dataset + MLflow tracking + Protocol+registry plug-in model. Preserves existing comparative slots (Context7, Neuledge) and indexing-latency benchmark.

**Architecture:** Four pluggable axes (Dataset / Metric / ExperimentTracker / System), each Protocol + decorator registry — mirrors `chunker_registry` / `stage_registry` / `retriever_registry` in `extraction/serialization.py` and `retrieval/serialization.py`. MLflow is an optional `[mlflow]` extra; HF datasets is `[repoqa]`. Default JSONL tracker has no extra deps.

**Tech stack:** Python 3.11+, asyncio, pydantic, MLflow (optional), HuggingFace `datasets` (optional). Uses existing `pydocs_mcp.application.ProjectIndexer` + `CodeRetrieverPipeline` — no fork of indexing/retrieval code.

**Reviewer strategy:** Each task gets two-stage review per `subagent-driven-development`, but the **reviewer prompts are customized per task category** to focus on that category's risks:

| Task category | Spec reviewer focus | Code reviewer focus |
|---|---|---|
| Foundation (Task 1) | Protocol shapes match spec §4.3, registry pattern matches existing | SRP per file, frozen dataclasses, lazy imports |
| Metrics (Task 2) | Recall@k / MRR formulas match spec §4.11 | Bootstrap CI determinism (seed=0), edge cases (k > len, gold not in retrieved) |
| Trackers (Task 3) | Optional-dep boundary matches spec §4.5 | Lazy import location, error message contains install command verbatim |
| Systems (Task 4) | Uses existing `build_chunk_pipeline_from_config` for pydocs | No fork of indexing path, teardown is idempotent |
| Dataset (Task 5) | Revision pinning per spec §4.8, AST-equiv match | Cache-hit path, corpus materializer cleanup |
| Runner (Task 6) | (system × config × dataset) loop matches spec §4.6 | Per-task tmp cleanup on exception, sequential not parallel |
| Tests (Task 8) | All 14 ACs from spec §5 have at least one test | No flaky reliance on network / external state |
| CI (Task 9) | Path filter + 2pp threshold matches spec §4.12 | Caching, dataset revision fingerprint |

---

## Working directory

`/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-harness/`
Branch: `feature/benchmark-harness-repoqa-mlflow` (already pushed as draft PR #26).

---

## Task 0: Worktree baseline + benchmarks pyproject extras

**Files:**
- Modify: `benchmarks/pyproject.toml` — add `[project.optional-dependencies]`

- [ ] **Step 1: Read current `benchmarks/pyproject.toml`**

```bash
cat benchmarks/pyproject.toml
```

- [ ] **Step 2: Add optional-dependencies block**

```toml
[project.optional-dependencies]
mlflow = ["mlflow>=2.0"]
repoqa = ["datasets>=2.0", "huggingface-hub>=0.20"]
all    = ["mlflow>=2.0", "datasets>=2.0", "huggingface-hub>=0.20"]
```

- [ ] **Step 3: Confirm baseline `pytest -q` (root) passes**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/benchmark-harness
.venv/bin/pytest -q
```
Expected: 996 passing (no benchmark code added yet).

- [ ] **Step 4: Commit**

```bash
git add benchmarks/pyproject.toml
git commit -m "chore(benchmarks): add [mlflow] [repoqa] [all] optional extras"
```

---

## Task 1: Foundation — Protocols + dataclasses + registries + ast_match

**Files:**
- Create: `benchmarks/benchmarks/eval/__init__.py`
- Create: `benchmarks/benchmarks/eval/protocols.py` — all 5 Protocols + 4 dataclasses
- Create: `benchmarks/benchmarks/eval/serialization.py` — 4 registries (one generic `_Registry[T]` class)
- Create: `benchmarks/benchmarks/eval/ast_match.py` — `ast_equivalent(a: str, b: str) -> bool`
- Create: `benchmarks/tests/eval/__init__.py`
- Create: `benchmarks/tests/eval/test_registries.py` — register + build + unknown-name error
- Create: `benchmarks/tests/eval/test_ast_match.py` — whitespace + comment tolerance

- [ ] **Step 1: Write failing tests for registry + ast_match (tests above)**

Tests pin: `_Registry.register(name)` decorator returns the class unchanged; `build(name, **kwargs)` instantiates; unknown name raises `KeyError` with a helpful list. `ast_equivalent("def f(): return 1", "def f():\n    return 1  # comment\n")` → True. Different bodies → False.

- [ ] **Step 2: Run tests — verify FAIL with ImportError**

```bash
.venv/bin/pytest benchmarks/tests/eval/ -q
```
Expected: collection error / ImportError on missing modules.

- [ ] **Step 3: Implement `protocols.py`**

Exact shapes per spec §4.3. Five `@runtime_checkable Protocol` classes (`Dataset`, `Metric`, `ExperimentTracker`, `System`, plus an auxiliary `Scorer` dataclass). Four `@dataclass(frozen=True, slots=True)` value objects (`EvalTask`, `GoldAnswer`, `RetrievedItem`, `RunHandle`). All `from __future__ import annotations` to keep import cost minimal.

- [ ] **Step 4: Implement `serialization.py`**

One generic `_Registry[T]` class (decorator-based; matches `extraction/serialization.py`'s `ComponentRegistry` shape). Four module-level singletons: `dataset_registry`, `metric_registry`, `tracker_registry`, `system_registry`. Each typed as `_Registry[<Protocol>]`.

- [ ] **Step 5: Implement `ast_match.py`**

```python
import ast

def ast_equivalent(a: str, b: str) -> bool:
    """Return True iff a and b parse to equivalent ASTs (whitespace + comment tolerant)."""
    try:
        return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except SyntaxError:
        return False
```

- [ ] **Step 6: Run tests — verify PASS**

```bash
.venv/bin/pytest benchmarks/tests/eval/ -q
```
Expected: all tests pass.

- [ ] **Step 7: Commit**

```bash
git add benchmarks/benchmarks/eval/{__init__.py,protocols.py,serialization.py,ast_match.py} benchmarks/tests/eval/
git commit -m "feat(benchmarks): foundation — Protocols, registries, ast_match"
```

---

## Task 2: Metrics — recall@k, mrr, pass@1-needle + aggregation

**Files:**
- Create: `benchmarks/benchmarks/eval/metrics/__init__.py` — re-export concretes
- Create: `benchmarks/benchmarks/eval/metrics/base_metric.py` — re-export `Metric` Protocol
- Create: `benchmarks/benchmarks/eval/metrics/recall_at_k.py` — `RecallAtK(k=N)` metric class
- Create: `benchmarks/benchmarks/eval/metrics/mrr.py` — `MRR` metric class
- Create: `benchmarks/benchmarks/eval/metrics/pass_at_1_needle.py` — `PassAt1Needle` metric class
- Create: `benchmarks/benchmarks/eval/metrics/aggregate.py` — mean + 95% bootstrap CI (seed=0, 1000 resamples)
- Create: `benchmarks/tests/eval/test_recall_at_k.py`
- Create: `benchmarks/tests/eval/test_mrr.py`
- Create: `benchmarks/tests/eval/test_pass_at_1.py`
- Create: `benchmarks/tests/eval/test_aggregate.py`

- [ ] **Step 1: Write failing tests**

Tests pin:
- `RecallAtK(k=1).compute(task, retrieved)` where gold is `retrieved[0].text` (AST-equivalent) → 1.0
- Gold is `retrieved[5].text` and k=5 → 1.0 (boundary)
- Gold not in retrieved → 0.0
- MRR formula: gold at rank 1 → 1.0, rank 2 → 0.5, not present → 0.0
- PassAt1Needle: gold matches retrieved[0] → 1.0, else → 0.0
- Aggregate: mean of `[1.0, 0.0, 1.0, 1.0]` = 0.75; bootstrap CI deterministic with `seed=0`

- [ ] **Step 2: Run tests — verify FAIL**

- [ ] **Step 3: Implement each metric**

Each in its own file, registered via `@metric_registry.register("recall@k")` etc. Use `ast_match.ast_equivalent` to compare retrieved chunk text vs `task.gold.ast_body`. `RecallAtK` parameterized on `k: int`; the registered name is `"recall@k"` but per-instance `name` is `f"recall@{k}"` so MLflow keys are unique.

- [ ] **Step 4: Implement aggregate.py**

```python
import random
import statistics
from typing import Sequence

def mean_with_bootstrap_ci(
    values: Sequence[float], *, n_resamples: int = 1000, seed: int = 0,
) -> tuple[float, float, float]:
    """Return (mean, ci_low, ci_high) at 95% confidence via bootstrap."""
    if not values:
        return 0.0, 0.0, 0.0
    rng = random.Random(seed)
    means = []
    n = len(values)
    for _ in range(n_resamples):
        sample = [rng.choice(values) for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    return statistics.fmean(values), means[int(0.025 * n_resamples)], means[int(0.975 * n_resamples)]
```

- [ ] **Step 5: Run tests — verify PASS**

- [ ] **Step 6: Commit**

```bash
git add benchmarks/benchmarks/eval/metrics/ benchmarks/tests/eval/test_recall_at_k.py benchmarks/tests/eval/test_mrr.py benchmarks/tests/eval/test_pass_at_1.py benchmarks/tests/eval/test_aggregate.py
git commit -m "feat(benchmarks): metrics — recall@k, MRR, pass@1-needle, bootstrap CI aggregate"
```

---

## Task 3: Trackers — JSONL (core) + MLflow (optional, lazy)

**Files:**
- Create: `benchmarks/benchmarks/eval/trackers/__init__.py`
- Create: `benchmarks/benchmarks/eval/trackers/base_tracker.py` — re-export `ExperimentTracker` Protocol
- Create: `benchmarks/benchmarks/eval/trackers/jsonl_tracker.py` — always-available
- Create: `benchmarks/benchmarks/eval/trackers/mlflow_tracker.py` — lazy-import `mlflow`
- Create: `benchmarks/tests/eval/test_jsonl_tracker.py`
- Create: `benchmarks/tests/eval/test_mlflow_tracker_import_error.py`

- [ ] **Step 1: Write failing tests**

JSONL test: open run, log 3 metrics + 1 artifact, close. Read the JSONL file back; assert one record per (metric, step) + one for the artifact pointer. Records are JSON-parseable.
MLflow import-error test: monkeypatch `sys.modules['mlflow']` to None / use `importlib`-based stub to simulate ImportError; assert `MlflowExperimentTracker()` raises with `"uv pip install -e benchmarks[mlflow]"` in the message verbatim.

- [ ] **Step 2: Run tests — verify FAIL**

- [ ] **Step 3: Implement `jsonl_tracker.py`**

```python
@tracker_registry.register("jsonl")
@dataclass
class JsonlExperimentTracker:
    name: str = "jsonl"
    output_dir: Path = Path("benchmarks/results/jsonl")

    def open_run(self, *, system, config_name, dataset, params, tags) -> RunHandle:
        # one file per run; write a header record with params + tags
        ...

    def log_metric(self, handle, name, value, step=None) -> None:
        # append one JSON line per call
        ...
```

- [ ] **Step 4: Implement `mlflow_tracker.py`**

Lazy import inside `__post_init__`. `_install_msg = "uv pip install -e benchmarks[mlflow]"`. On ImportError, raise `ImportError(f"MLflow tracker requires {_install_msg!r}")`. Wrap MLflow's `start_run` / `log_param` / `log_metric` / `log_artifact` / `end_run`.

- [ ] **Step 5: Run tests — verify PASS**

- [ ] **Step 6: Commit**

```bash
git add benchmarks/benchmarks/eval/trackers/ benchmarks/tests/eval/test_jsonl_tracker.py benchmarks/tests/eval/test_mlflow_tracker_import_error.py
git commit -m "feat(benchmarks): JSONL tracker (core) + MLflow tracker (lazy-imported optional)"
```

---

## Task 4: Systems — pydocs-mcp (in-process) + migrate Context7 + Neuledge

**Files:**
- Create: `benchmarks/benchmarks/eval/systems/__init__.py`
- Create: `benchmarks/benchmarks/eval/systems/base_system.py` — re-export `System` Protocol
- Create: `benchmarks/benchmarks/eval/systems/pydocs.py` — in-process via `ProjectIndexer` + `CodeRetrieverPipeline`
- Create: `benchmarks/benchmarks/eval/systems/context7.py` — wraps existing `context7_bench.run_context7_benchmark` logic
- Create: `benchmarks/benchmarks/eval/systems/neuledge.py` — wraps existing `neuledge_bench` logic
- Create: `benchmarks/tests/eval/test_pydocs_system.py` — index a tiny fixture project, search, assert retrieved
- Create: `benchmarks/tests/eval/test_system_registry.py` — all three registered

- [ ] **Step 1: Write failing tests**

For pydocs system: tmp project with one .py file containing `def widget_helper(): pass`. Index it, search `"widget helper"`. Assert at least one RetrievedItem with `qualified_name` ending in `widget_helper`.

- [ ] **Step 2: Run tests — verify FAIL**

- [ ] **Step 3: Implement `pydocs.py`**

Use existing wiring:
```python
from pydocs_mcp.application import ProjectIndexer
from pydocs_mcp.db import open_index_database
from pydocs_mcp.retrieval.config import AppConfig, build_chunk_pipeline_from_config
from pydocs_mcp.storage.factories import build_sqlite_indexing_service, build_sqlite_uow_factory
```

Per task:
1. `open_index_database(self._db_path)` — fresh SQLite per (system, config, dataset_revision) tuple. tmp file under `~/.cache/pydocs-mcp/benchmarks/`.
2. Build pipeline via `build_chunk_pipeline_from_config(self._config)`.
3. `ProjectIndexer.index_project(corpus_dir, force=True)`.
4. Run the pipeline against the query.

- [ ] **Step 4: Implement `context7.py` + `neuledge.py`**

Minimal shims that adapt existing `context7_bench` / `neuledge_bench` modules into the `System` Protocol. `index()` may be a no-op for cloud systems that index internally; `search()` calls the existing HTTP client.

- [ ] **Step 5: Run tests — verify PASS**

- [ ] **Step 6: Commit**

```bash
git add benchmarks/benchmarks/eval/systems/ benchmarks/tests/eval/test_pydocs_system.py benchmarks/tests/eval/test_system_registry.py
git commit -m "feat(benchmarks): systems — pydocs-mcp (in-process), context7 + neuledge plugins"
```

---

## Task 5: Dataset — RepoQA loader + corpus materializer

**Files:**
- Create: `benchmarks/benchmarks/eval/datasets/__init__.py`
- Create: `benchmarks/benchmarks/eval/datasets/base_dataset.py`
- Create: `benchmarks/benchmarks/eval/datasets/repoqa.py`
- Create: `benchmarks/benchmarks/eval/corpus.py` — write long-context window to tmp project dir
- Create: `benchmarks/tests/eval/fixtures/repoqa_mini.json` — 5-task fixture
- Create: `benchmarks/tests/eval/test_repoqa_loader.py` — use fixture, no HF download

- [ ] **Step 1: Write failing tests using fixture**

Load fixture → 5 tasks; each yields an `EvalTask` with `task_id`, `query`, `gold.ast_body`, `corpus_source` callable. Calling `corpus_source()` returns a `Path` to a tmp dir containing the expected `.py` files.

- [ ] **Step 2: Run tests — verify FAIL**

- [ ] **Step 3: Implement `corpus.py`**

```python
def materialize_corpus(files: Mapping[str, str], parent: Path | None = None) -> Path:
    """Write {relative_path: content} into a fresh tmp dir, return the dir."""
    base = Path(tempfile.mkdtemp(prefix="repoqa_", dir=parent))
    for rel, body in files.items():
        target = base / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return base
```

- [ ] **Step 4: Implement `repoqa.py`**

`PINNED_REVISION = "<hash>"`. `RepoQADataset` lazy-imports `datasets`. Cache under `~/.cache/pydocs-mcp/repoqa/`. Filters language=python. Each task → `EvalTask(corpus_source=lambda task=task: materialize_corpus(task.files))`. Constructor accepts `fixture_path: Path | None` for tests to bypass HF.

- [ ] **Step 5: Run tests — verify PASS**

- [ ] **Step 6: Commit**

```bash
git add benchmarks/benchmarks/eval/datasets/ benchmarks/benchmarks/eval/corpus.py benchmarks/tests/eval/fixtures/repoqa_mini.json benchmarks/tests/eval/test_repoqa_loader.py
git commit -m "feat(benchmarks): RepoQA dataset loader + corpus materializer"
```

---

## Task 6: Runner CLI + report

**Files:**
- Create: `benchmarks/benchmarks/eval/runner.py` — async `run_sweep` + CLI entry
- Create: `benchmarks/benchmarks/eval/report.py` — markdown report generator
- Create: `benchmarks/tests/eval/test_runner_smoke.py` — runner against fixture + JSONL tracker
- Create: `scripts/run_repoqa.sh`

- [ ] **Step 1: Write failing smoke test**

End-to-end: invoke `run_sweep(systems=("pydocs-mcp",), config_paths=(baseline_yaml,), dataset_name="repoqa_fixture", tracker_names=("jsonl",), limit=5)`. Assert a JSONL file is written under `benchmarks/results/jsonl/` containing one header record + N metric records.

- [ ] **Step 2: Run tests — verify FAIL**

- [ ] **Step 3: Implement `runner.py`**

Exact orchestration per spec §4.6. Adds `argparse` CLI entry: `--systems`, `--configs`, `--dataset`, `--trackers`, `--limit`. Aggregates metrics with bootstrap CI at the end of each (system, config) run; logs aggregates as `step=None`.

- [ ] **Step 4: Implement `report.py`**

Reads the JSONL output (or queries MLflow if tracking_uri set), emits markdown table with per-metric rows × per-config columns + per-repo breakdown.

- [ ] **Step 5: Add `scripts/run_repoqa.sh`**

```bash
#!/usr/bin/env bash
set -euo pipefail
exec python -m benchmarks.eval.runner "$@"
```

- [ ] **Step 6: Run tests — verify PASS**

- [ ] **Step 7: Commit**

```bash
git add benchmarks/benchmarks/eval/runner.py benchmarks/benchmarks/eval/report.py benchmarks/tests/eval/test_runner_smoke.py scripts/run_repoqa.sh
chmod +x scripts/run_repoqa.sh
git commit -m "feat(benchmarks): runner CLI + markdown report"
```

---

## Task 7: Remove placeholder + cleanup

**Files:**
- Delete: `benchmarks/fake_project/` (entire dir)
- Delete: `benchmarks/benchmarks/fake_project.py`
- Delete: `benchmarks/benchmarks/dataset_gen.py`
- Delete: `benchmarks/benchmarks/search_bench.py` (replaced by runner+systems)
- Delete: `benchmarks/tests/test_fake_project.py`
- Delete: `benchmarks/tests/test_dataset_gen.py`
- Modify: `benchmarks/benchmarks/runner.py` — old runner becomes thin wrapper around new `eval.runner` (or remove if entirely superseded — confirm during implementation)
- Modify: `benchmarks/README.md` — replace synthetic-project section with RepoQA + MLflow instructions

- [ ] **Step 1: Confirm no remaining imports of deleted files**

```bash
grep -rn "fake_project\|dataset_gen\|search_bench" benchmarks/ --include="*.py" | head -20
```
Adjust the deletion plan if anything outside the deleted files references them.

- [ ] **Step 2: Delete + remove imports**

```bash
git rm -r benchmarks/fake_project/
git rm benchmarks/benchmarks/fake_project.py benchmarks/benchmarks/dataset_gen.py benchmarks/benchmarks/search_bench.py
git rm benchmarks/tests/test_fake_project.py benchmarks/tests/test_dataset_gen.py
```

- [ ] **Step 3: Rewrite `benchmarks/README.md`**

New sections: Run instructions (uv pip install, `./scripts/run_repoqa.sh`), MLflow UI (`mlflow ui --backend-store-uri file://...`), Metric definitions, "What this proxies / what it doesn't", License attribution.

- [ ] **Step 4: Run full pytest — should still pass after removals**

```bash
.venv/bin/pytest -q
```

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(benchmarks): remove placeholder fake_project + circular dataset_gen, rewrite README"
```

---

## Task 8: Integration tests + smoke

**Files:**
- Create: `benchmarks/tests/eval/test_integration_oracle.py` — oracle retriever scores Recall@10 == 1.0
- Create: `benchmarks/tests/eval/test_integration_empty.py` — empty retriever scores 0.0
- Create: `benchmarks/tests/eval/test_scorer_e2e.py` — Scorer over fixture tasks

- [ ] **Step 1: Write integration tests**

Oracle test: synthesize 5 EvalTasks; build a stub `System` whose `search()` always returns the gold function as `retrieved[0]`. Run the full runner with JSONL tracker; assert aggregate `recall@10 == 1.0` in the output.

Empty test: stub System returns `()`. Aggregate `recall@10 == 0.0`. Pass@1-needle == 0.0.

- [ ] **Step 2: Run tests — verify PASS** (after Tasks 1-6 land, this should just work)

- [ ] **Step 3: Commit**

```bash
git add benchmarks/tests/eval/test_integration_*.py benchmarks/tests/eval/test_scorer_e2e.py
git commit -m "test(benchmarks): integration tests — oracle + empty retriever boundary"
```

---

## Task 9: CI workflow + baseline + LICENSE-third-party

**Files:**
- Create: `.github/workflows/benchmark.yml` — new CI job
- Create: `benchmarks/baselines/repoqa_snf.json` — one-time-run baseline from `main` HEAD
- Modify or create: `LICENSE-third-party` — RepoQA + MLflow attribution
- Create: `benchmarks/configs/baseline.yaml`, `no_stdlib.yaml`, `strict_suffix_off.yaml`, `mentions_on.yaml`, `large_member_cap.yaml`, `narrow_markdown.yaml`

- [ ] **Step 1: Write `.github/workflows/benchmark.yml`**

Triggers: PRs touching `python/pydocs_mcp/extraction/**`, `python/pydocs_mcp/retrieval/**`, `python/pydocs_mcp/defaults/**`, `benchmarks/**`.

Steps:
1. Checkout
2. Set up Python 3.11
3. `uv pip install -e ".[dev]"`
4. `uv pip install -e "benchmarks[all]"`
5. Cache HF datasets dir (key on the pinned revision hash)
6. `./scripts/run_repoqa.sh --systems pydocs-mcp --configs benchmarks/configs/baseline.yaml --dataset repoqa --trackers jsonl`
7. Compare against `benchmarks/baselines/repoqa_snf.json`; fail if recall@10 drops > 2pp.
8. Post PR comment with metrics table.

- [ ] **Step 2: Generate baseline JSON**

Run the runner on `main` HEAD before this PR's behavior changes, write metrics to `benchmarks/baselines/repoqa_snf.json`. (Manual one-time step — committed as part of this task.)

- [ ] **Step 3: Write `benchmarks/configs/*.yaml`**

Six overlay files per spec §4.9. Each is YAML overlay (not full config), loaded by `AppConfig.load(explicit_path=...)`.

- [ ] **Step 4: Add `LICENSE-third-party`**

RepoQA citation + Apache-2.0 notice. MLflow Apache-2.0 attribution.

- [ ] **Step 5: Run full pytest + ruff**

```bash
.venv/bin/pytest -q
.venv/bin/ruff check python/ tests/ benchmarks/
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/benchmark.yml benchmarks/baselines/ benchmarks/configs/ LICENSE-third-party
git commit -m "ci(benchmarks): benchmark-repoqa workflow + baseline JSON + sweep configs"
```

---

## Final verification

```bash
.venv/bin/pytest -q                                       # all pass
.venv/bin/ruff check python/ tests/ benchmarks/           # clean
.venv/bin/python -m benchmarks.eval.runner --help  # registries listed
.venv/bin/python -m benchmarks.eval.runner --systems pydocs-mcp --configs benchmarks/configs/baseline.yaml --dataset repoqa --trackers jsonl --limit 5  # smoke
```

After all 9 tasks land cleanly: dispatch the final code-reviewer subagent across the entire diff, surface findings for human approval, then take PR #26 out of draft.
