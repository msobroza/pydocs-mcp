# Benchmark Harness — RepoQA-SNF + MLflow + SOLID Extensibility

**Status**: Draft, awaiting review
**Date**: 2026-05-19
**Author**: msobroza

## 1. Goal

Replace the placeholder benchmark (`benchmarks/fake_project/` + `dataset_gen.py`) with a **real, public retrieval-quality eval (RepoQA-SNF)** and a **MLflow-backed experiment-tracking layer**, structured around a **Protocol + registry plugin model** so future datasets, metrics, and experiment trackers land without runner edits.

The harness must:

1. Measure **retrieval quality** on `pydocs-mcp` against a credible public benchmark (RepoQA-SNF, arXiv 2406.06025).
2. Support **YAML A/B sweeps** over `AppConfig` (the architectural rule from `CLAUDE.md §"MCP API surface vs YAML configuration"` — pipeline behavior toggles via YAML, never via MCP params).
3. Preserve the existing **comparative slots** (pydocs-mcp vs Context7 vs Neuledge Context) — the existing `runner.py` already wires these.
4. Preserve the existing **indexing-latency benchmark** (`indexer_bench.py`).
5. Track every `(system × config × dataset)` combination as one **MLflow run** with comparable params, metrics, and artifacts.
6. Be **extensible**: adding SWE-bench, Weights & Biases, or a new metric must be a single-file change that does not touch the runner.

## 2. Non-goals

- End-to-end LLM code generation quality.
- Multi-language coverage — Python only.
- Real-user query distribution — RepoQA queries are LLM-generated; in-house log-mined eval is separate future work.
- Multi-file / call-graph retrieval — SWE-bench Verified retrieval-only is the right complement and is flagged as a follow-up (which this architecture supports via a one-file plugin).

## 3. Background

### 3.1 Current state of `benchmarks/`

```
benchmarks/
├── fake_project/          synthetic Python project (PLACEHOLDER)
└── benchmarks/
    ├── runner.py          orchestrator — pydocs-mcp / Context7 / Neuledge loop
    ├── indexer_bench.py   per-package indexing timings
    ├── dataset_gen.py     synthesizes queries FROM the indexed chunks (CIRCULAR)
    ├── search_bench.py    BM25 + rapidfuzz over the synthesized queries
    ├── context7_*.py      Context7 MCP client + bench
    ├── neuledge_*.py      Neuledge Context MCP client + bench
    └── fake_project.py    generates the placeholder project
```

The placeholder's flaw: `dataset_gen.py` synthesizes questions from chunks it just indexed, so the eval can't meaningfully detect a chunker / retriever change that shifts both the corpus and the queries together. Output is CSV — no run-level diffing, no metric provenance.

### 3.2 What `pydocs-mcp` actually serves

The MCP surface (pinned at sub-PRs #4 / #6) is fixed at two tools — `search(query, kind, ...)` and `lookup(target, show, ...)`. The dominant query shape for `search` is **natural-language description → relevant function/class/symbol**, which is exactly what RepoQA-SNF tests.

### 3.3 Why RepoQA-SNF

- **Real benchmark**: arXiv 2406.06025, EvalPlus team, NeurIPS-era.
- **NL → Python function retrieval**: matches the dominant `search` query shape.
- **~150 Python tasks** across 50 popular repos: fast enough for per-PR CI (~10 min), large enough for statistically meaningful diffs.
- **Public, peer-reviewed, Apache-2.0**: results are externally credible.
- **Long-context windows**: matches the production indexing path (real repo slices, not synthetic).

Alternatives considered:

| Benchmark | Why rejected for this PR |
|---|---|
| CodeSearchNet / CoIR-CSN | Docstring-as-query is a poor proxy; scores inflated by pretraining overlap. |
| SWE-bench Verified retrieval-only | Better long-term but ~50 GB disk + ~2h CPU setup. Worth a follow-up. |
| CoSQA+ | Corpus-flat — one function per query, no repo context. Misses the long-context dim. |
| CodeRAG-Bench / CrossCodeEval e2e | Require GPU for the generator leg. |

### 3.4 Why MLflow

- **Run-level tracking**: every `(system × config × dataset)` combination is one MLflow run with params, metrics, and artifacts.
- **Comparable across PRs**: the MLflow UI does diff + parallel-coordinate plots out of the box.
- **Offline-friendly**: `file://./mlruns/` is the default tracking URI — no server, no network at run time.
- **Pluggable storage**: can be swapped for a hosted tracking server via `MLFLOW_TRACKING_URI` without code changes.

### 3.5 Architectural rule that this PR upholds

From `CLAUDE.md §"MCP API surface vs YAML configuration"`:

> Pipeline / feature / behavior settings — capture toggles, resolver thresholds, retrieval limits-on-defaults, ranking weights, embedding model choices, indexing depth, kinds-to-emit, reference-graph capture on/off, etc. — MUST be configured via YAML (loaded through `AppConfig.load(...)` at server / CLI startup), NEVER exposed as new MCP tool parameters.

This harness is **the rule's payoff**: every YAML knob is one MLflow param + one sweep config. The harness exists so that we can compare YAML variants on real benchmarks instead of guessing.

## 4. Design

### 4.1 Plugin model — Protocol + registry (matches the rest of the codebase)

The codebase already uses Protocol + decorator-registry for chunkers (`chunker_registry`), stages (`stage_registry`), retrievers (`retriever_registry`). The eval harness follows the same shape so future plug-ins are obvious to anyone who's worked in `extraction/` or `retrieval/`.

Four pluggable axes:

1. **Dataset** — what corpus + queries to run against.
2. **Metric** — what numbers to compute per task.
3. **ExperimentTracker** — where to send the runs (MLflow, JSONL, future W&B).
4. **System** — what's being measured (pydocs-mcp via in-process pipeline, Context7 via HTTP, Neuledge via HTTP).

Each axis has one Protocol, one registry, and a `base_<axis>.py` file that re-exports the Protocol — same shape as the post-refactor `extraction/pipeline/stages/base_stage.py`.

### 4.2 Module layout

```
benchmarks/benchmarks/eval/
├── __init__.py
├── protocols.py              Dataset / Scorer / Metric / ExperimentTracker / System Protocols + EvalTask, RetrievedItem, RunHandle dataclasses
├── serialization.py          dataset_registry, metric_registry, tracker_registry, system_registry
├── corpus.py                 materialize a task's long-context window → tmp project dir
├── runner.py                 orchestrates (system × config × dataset) → metrics → trackers
├── ast_match.py              AST-equivalence matcher for retrieved-vs-gold (whitespace/comment-tolerant)
├── datasets/
│   ├── __init__.py
│   ├── base_dataset.py       re-exports Dataset Protocol
│   └── repoqa.py             @dataset_registry.register("repoqa") RepoQADataset
├── metrics/
│   ├── __init__.py
│   ├── base_metric.py        re-exports Metric Protocol
│   ├── recall_at_k.py        @metric_registry.register("recall@k") with k parameter
│   ├── mrr.py                @metric_registry.register("mrr")
│   └── pass_at_1_needle.py   @metric_registry.register("pass@1-needle")
├── trackers/
│   ├── __init__.py
│   ├── base_tracker.py          re-exports ExperimentTracker Protocol
│   ├── jsonl_tracker.py              @tracker_registry.register("jsonl") — always available, no extra deps
│   └── mlflow_tracker.py        @tracker_registry.register("mlflow") — lazy-imports mlflow
└── systems/
    ├── __init__.py
    ├── base_system.py        re-exports System Protocol
    ├── pydocs.py             @system_registry.register("pydocs-mcp") — in-process retrieval
    ├── context7.py           @system_registry.register("context7") — HTTP client (re-uses existing)
    └── neuledge.py           @system_registry.register("neuledge") — HTTP client (re-uses existing)
```

One file per concrete plug-in. Add SWE-bench Verified retrieval-only → `datasets/swebench.py`. Add Weights & Biases → `trackers/wandb_tracker.py`. The runner never moves.

### 4.3 Protocol shapes

```python
# protocols.py

@dataclass(frozen=True, slots=True)
class EvalTask:
    """One scoring unit: a query, a gold answer, and a callable that builds the corpus."""
    task_id: str
    query: str
    gold: "GoldAnswer"
    corpus_source: "CorpusSource"  # callable: () -> Path (tmp dir)
    metadata: Mapping[str, str] = field(default_factory=dict)  # repo, language, etc.

@dataclass(frozen=True, slots=True)
class GoldAnswer:
    """What we're trying to retrieve. AST-body for function-retrieval datasets;
    extensible to other shapes for future datasets (e.g. fileset for SWE-bench)."""
    ast_body: str | None = None
    file_set: tuple[str, ...] = ()
    extra: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class RetrievedItem:
    """One item returned by the system under test."""
    rank: int
    text: str
    source_path: str
    qualified_name: str | None = None
    relevance: float | None = None

@dataclass(frozen=True, slots=True)
class RunHandle:
    """Opaque handle for a tracker run — stored by the tracker implementation."""
    tracker_name: str
    raw: object  # implementation-specific (mlflow.ActiveRun, file handle, etc.)

@runtime_checkable
class Dataset(Protocol):
    name: str            # e.g. "repoqa"
    revision: str        # pinned dataset revision/hash for reproducibility
    # NB: plain ``def`` (not ``async def``) — concrete impls are async
    # generators, callers iterate ``async for task in dataset.tasks()``
    # rather than ``async for task in await dataset.tasks()``.
    def tasks(self) -> AsyncIterator[EvalTask]: ...

@runtime_checkable
class Metric(Protocol):
    name: str
    def compute(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> float: ...

@runtime_checkable
class ExperimentTracker(Protocol):
    name: str
    def open_run(self, *, system: str, config_name: str, dataset: str,
                 params: Mapping[str, str], tags: Mapping[str, str]) -> RunHandle: ...
    def log_metric(self, handle: RunHandle, name: str, value: float,
                   step: int | None = None) -> None: ...
    def log_artifact(self, handle: RunHandle, path: Path, name: str | None = None) -> None: ...
    def close_run(self, handle: RunHandle, status: Literal["finished", "failed"]) -> None: ...

@runtime_checkable
class System(Protocol):
    name: str
    async def index(self, corpus_dir: Path, config: "AppConfig") -> None: ...
    async def search(self, query: str, limit: int) -> tuple[RetrievedItem, ...]: ...
    async def teardown(self) -> None: ...

@dataclass(frozen=True, slots=True)
class Scorer:
    """Composes a tuple of Metrics. SRP — Scorer just walks Metrics + Trackers."""
    metrics: tuple[Metric, ...]
    def score(self, task: EvalTask, retrieved: tuple[RetrievedItem, ...]) -> dict[str, float]:
        return {m.name: m.compute(task, retrieved) for m in self.metrics}
```

### 4.4 Registry pattern (decorator + lazy import)

Same shape as `extraction/serialization.py`:

```python
# serialization.py

class _Registry(Generic[T]):
    def __init__(self) -> None:
        self._items: dict[str, type[T]] = {}
    def register(self, name: str) -> Callable[[type[T]], type[T]]:
        def deco(cls: type[T]) -> type[T]:
            self._items[name] = cls
            return cls
        return deco
    def build(self, name: str, **kwargs: object) -> T:
        if name not in self._items:
            raise KeyError(f"{type(self).__name__}: no entry {name!r}; have {sorted(self._items)}")
        return self._items[name](**kwargs)

dataset_registry: _Registry[Dataset] = _Registry()
metric_registry:  _Registry[Metric]  = _Registry()
tracker_registry:    _Registry[ExperimentTracker] = _Registry()
system_registry:  _Registry[System]  = _Registry()
```

The `__init__.py` of each plug-in package imports its concrete modules so the decorator side-effects fire at package import. The runner imports `benchmarks.eval` once (the absolute `from benchmarks.eval.X` import convention used in `serialization.py` requires `PYTHONPATH=benchmarks` at invocation — see `scripts/run_repoqa.sh`).

### 4.5 MLflow as optional extra (uv-friendly)

`benchmarks/pyproject.toml`:

```toml
[project.optional-dependencies]
mlflow = ["mlflow>=2.0"]
repoqa = ["datasets>=2.0", "huggingface-hub>=0.20"]
all    = ["mlflow>=2.0", "datasets>=2.0", "huggingface-hub>=0.20"]
```

Install:

```bash
uv pip install -e benchmarks                # core only — JSONL tracker works, no datasets
uv pip install -e "benchmarks[mlflow]"      # + MLflow tracker
uv pip install -e "benchmarks[repoqa]"      # + HF datasets loader for RepoQA
uv pip install -e "benchmarks[all]"         # everything
```

`MlflowExperimentTracker` does `import mlflow` lazily inside `__init__`:

```python
@tracker_registry.register("mlflow")
@dataclass
class MlflowExperimentTracker:
    name: str = "mlflow"
    tracking_uri: str = "file://./benchmarks/mlruns"
    def __post_init__(self) -> None:
        try:
            import mlflow  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "MLflow tracker requires the optional extra: "
                "`uv pip install -e benchmarks[mlflow]`"
            ) from exc
```

Same pattern for `RepoQADataset` and the `datasets` HuggingFace lib.

### 4.6 Runner orchestration

```python
# runner.py

async def run_sweep(
    *,
    systems: tuple[str, ...],
    config_paths: tuple[Path, ...],
    dataset_name: str,
    tracker_names: tuple[str, ...],
    metric_specs: tuple[str, ...] = ("recall@1", "recall@5", "recall@10", "mrr", "pass@1-needle"),
    limit: int | None = None,
) -> None:
    dataset = dataset_registry.build(dataset_name)
    metrics = tuple(_build_metric(s) for s in metric_specs)
    scorer = Scorer(metrics=metrics)
    trackers: list[ExperimentTracker] = [tracker_registry.build(t) for t in tracker_names]

    for system_name in systems:
        for cfg_path in config_paths:
            config = AppConfig.load(explicit_path=cfg_path)
            # Construct via the registry — Task 4's ``System`` Protocol takes
            # ``config`` at ``index(corpus_dir, config)``-time, NOT in the
            # constructor. The factory just hands back a zero-config instance
            # ready to be re-indexed against any AppConfig.
            system: System = system_registry.build(system_name)
            handles = [
                t.open_run(
                    system=system_name,
                    config_name=cfg_path.stem,
                    dataset=f"{dataset.name}@{dataset.revision}",
                    params=_flatten_app_config(config),
                    tags=_run_tags(),
                ) for t in trackers
            ]
            try:
                count = 0
                async for task in dataset.tasks():
                    if limit is not None and count >= limit:
                        break
                    corpus_dir = task.corpus_source()
                    try:
                        await system.index(corpus_dir, config)
                        retrieved = await system.search(task.query, limit=10)
                        scores = scorer.score(task, retrieved)
                        for h, tracker in zip(handles, trackers):
                            for metric_name, value in scores.items():
                                tracker.log_metric(h, metric_name, value, step=count)
                    finally:
                        shutil.rmtree(corpus_dir, ignore_errors=True)
                    count += 1
                # aggregate metrics — bootstrap CI handled in metrics.aggregate (separate file)
                ...
                for h, tracker in zip(handles, trackers):
                    tracker.close_run(h, status="finished")
            except Exception:
                for h, tracker in zip(handles, trackers):
                    tracker.close_run(h, status="failed")
                raise
            finally:
                await system.teardown()
```

### 4.7 Data flow

```
RepoQA dataset (HF)
       │
       ▼  dataset.tasks() yields EvalTask
EvalTask(query, gold, corpus_source)
       │
       ▼  corpus_source() materializes long-context window
tmp project dir
       │
       ▼  System.index(corpus_dir, config)
indexed corpus (SQLite for pydocs-mcp)
       │
       ▼  System.search(query, limit=10)
tuple[RetrievedItem, ...]
       │
       ▼  Scorer.score(task, retrieved)
{recall@1: float, recall@5: float, ...}
       │
       ▼  for each tracker: log_metric / log_artifact
MLflow run + JSONL line + (future trackers)
```

### 4.8 RepoQA-SNF specifics

- **Loader**: `datasets.load_dataset("evalplus/repoqa", revision=PINNED_HASH)`, filter to `language == "python"`, cache under `~/.cache/pydocs-mcp/repoqa/`.
- **Gold matching**: `ast.dump(ast.parse(needle_body))` vs `ast.dump(ast.parse(retrieved_chunk_text))` — whitespace + comment tolerant.
- **Corpus materialization**: write the long-context window to `<tmp>/repoqa_<task_id>/<original_relative_path>` so the standard `ProjectIndexer.index_project(<tmp>)` runs on a realistic project layout.

### 4.9 YAML A/B sweep configs

Checked-in starter set under `benchmarks/configs/`:

| File | Differs from baseline by |
|---|---|
| `baseline.yaml` | (none — symlink to `defaults/default_config.yaml`) |
| `no_stdlib.yaml` | `reference_graph.resolver.include_stdlib: false` |
| `strict_suffix_off.yaml` | `reference_graph.resolver.strict_suffix: false` |
| `mentions_on.yaml` | `reference_graph.capture.kinds: [calls, imports, inherits, mentions]` |
| `large_member_cap.yaml` | `extraction.members.members_per_module_cap: 500` |
| `narrow_markdown.yaml` | `extraction.chunking.markdown.{min,max}_heading_level: [1, 2]` |

Adding a new sweep config is a new YAML file. The runner discovers it via the `--config` flag.

### 4.10 Per-system implementation notes

- **`systems/pydocs.py`**: in-process. Builds the `CodeRetrieverPipeline` via `build_chunk_pipeline_from_config(config)` (already used by `search_bench.py`), invokes `ProjectIndexer.index_project(corpus_dir, force=True)`, then runs the pipeline. No MCP server spin-up — direct service usage.
- **`systems/context7.py` / `systems/neuledge.py`**: HTTP. Re-uses the existing `context7_client.py` and `neuledge_client.py`. Indexes via Context7's own resolution + Neuledge's own server. The `config` parameter is ignored for these (they're not configurable from our side) but the system name + dataset still flow into MLflow tags so cross-system results are comparable.

### 4.11 Aggregation + reporting

Per-run metrics are streamed via `tracker.log_metric(step=task_index)`. Final aggregates (mean + 95% bootstrap CI, 1000 resamples, seed=0) are computed by `metrics/aggregate.py` after the task loop and logged as the same metric name with `step=None` (MLflow plots both the per-task series and the summary).

A markdown `report.md` is generated by `report.py` and logged as an artifact on every run. The reporter does the per-repo breakdown + a diff against the baseline JSON file.

### 4.12 CI integration

New job `benchmark-repoqa` in `.github/workflows/benchmark.yml`:

- **Trigger**: PRs touching `python/pydocs_mcp/extraction/**`, `python/pydocs_mcp/retrieval/**`, `python/pydocs_mcp/defaults/**`, or `benchmarks/**`.
- **Steps**:
  1. `uv pip install -e ".[dev]"` (root package)
  2. `uv pip install -e "benchmarks[all]"` (benchmark + extras)
  3. `PYTHONPATH=benchmarks python -m benchmarks.eval.datasets.repoqa --download` (cache step — keyed on dataset revision; `PYTHONPATH=benchmarks` mirrors `scripts/run_repoqa.sh`)
  4. `PYTHONPATH=benchmarks python -m benchmarks.eval.runner --system pydocs-mcp --config baseline --dataset repoqa --tracker jsonl` (`PYTHONPATH=benchmarks` is required so the absolute `from benchmarks.eval.X` imports — see `serialization.py` — resolve; this mirrors `scripts/run_repoqa.sh`).
  5. Compare `recall@10` against `benchmarks/baselines/repoqa_snf.json`.
  6. Fail if drop > 2pp outside the 95% CI.
  7. Post PR comment with metrics table + per-repo deltas.
- **Skips** Context7 + Neuledge legs (no outbound network in CI).
- **Runtime budget**: ~12 min.

Baseline JSON is bumped manually via a separate `bench-baseline-bump` workflow when an improvement is intentional.

### 4.13 Tests

| Test type | File | What it checks |
|---|---|---|
| Unit | `tests/eval/test_scorer.py` | Oracle retriever → Recall@10 == 1.0; empty retriever → 0.0 |
| Unit | `tests/eval/test_recall_at_k.py` | Boundary cases (k=1, gold not in retrieved, gold at exact rank k) |
| Unit | `tests/eval/test_mrr.py` | MRR matches known formula on hand-crafted ranked lists |
| Unit | `tests/eval/test_ast_match.py` | Whitespace + comment variations match the same canonical body |
| Unit | `tests/eval/test_registries.py` | Each registry refuses unknown names with a helpful error |
| Unit | `tests/eval/test_jsonl_tracker.py` | Round-trip a run; assert one JSONL line per metric step |
| Unit | `tests/eval/test_mlflow_tracker_import_error.py` | Without `mlflow` installed, tracker construction raises with the install instruction |
| Integration | `tests/eval/test_repoqa_loader.py` | Tiny 5-task fixture (no HF download); end-to-end loader → tasks |
| Integration | `tests/eval/test_runner_smoke.py` | Runner against 5-task fixture + JSONL tracker, asserts JSONL well-formed |

## 5. Acceptance criteria

| # | Statement |
|---|---|
| AC1 | `pip install -e benchmarks` succeeds without optional extras. |
| AC2 | `uv pip install -e "benchmarks[all]"` succeeds in CI. |
| AC3 | `PYTHONPATH=benchmarks python -m benchmarks.eval.runner --help` prints all four registries' currently-registered names (`PYTHONPATH=benchmarks` mirrors `scripts/run_repoqa.sh`). |
| AC4 | `PYTHONPATH=benchmarks python -m benchmarks.eval.runner --system pydocs-mcp --config baseline --dataset repoqa --tracker jsonl --limit 10` produces a JSONL file with one record per (task, metric) pair. |
| AC5 | Same as AC4 with `--tracker mlflow,jsonl` produces both outputs, no crash. |
| AC6 | Adding a new dataset is one file change: create `datasets/foo.py`, register it, runnable without runner edits. |
| AC7 | Adding a new metric is one file change. |
| AC8 | Adding a new experiment tracker is one file change. |
| AC9 | `MlflowExperimentTracker` construction without `mlflow` installed raises `ImportError` with the exact `uv pip install` command in the message. |
| AC10 | RepoQA-SNF baseline metrics on `main` HEAD are committed to `benchmarks/baselines/repoqa_snf.json`. |
| AC11 | CI job `benchmark-repoqa` fails when `recall@10` drops > 2pp outside the 95% CI vs baseline. |
| AC12 | All unit + integration tests pass; ruff clean. |
| AC13 | `benchmarks/README.md` documents: how to run, MLflow UI, metric definitions, the "what this proxies / what it doesn't" disclosure. |
| AC14 | `LICENSE-third-party` carries the RepoQA / Apache-2.0 attribution. |

## 6. Out of scope (deferred to follow-up PRs)

1. **SWE-bench Verified retrieval-only dataset** (`datasets/swebench.py`) — multi-file retrieval coverage.
2. **In-house log-mined eval set** for real-user query distribution.
3. **Weights & Biases tracker** (`trackers/wandb_tracker.py`).
4. **Latency-percentile metrics** (p50 / p95 indexing + search).
5. **Dense-retriever experiment** (when/if pydocs-mcp adds dense retrieval as a YAML-toggleable plugin) — harness already produces the comparable Recall@k baseline.
6. **Internal artifact mirroring** for fully air-gapped CI (RepoQA dataset hosted internally).
7. **MLflow Model Registry** integration if non-default configs ever ship to production.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| HuggingFace dataset revision drift | Pin revision hash in `loader.py`; CI fails on mismatch. |
| First CI run downloads 50 MB | Cache key includes revision hash; subsequent runs cached on runner. |
| Per-task tmp-dir indexing overhead | ~4s/task × 150 = 10 min. Batched indexing flagged as future optimization. |
| MLflow file-store contention with parallel runs | Sequential by default; flagged if parallelism is added. |
| Optional dep drift | `benchmarks[all]` extras provide a known-good install vector; pinned in CI. |

## 8. License and attribution

RepoQA-SNF is Apache-2.0 (EvalPlus). Per the dataset card, evaluation use is compatible with the source repos' licenses.

Citation:
> Liu, J. et al. *RepoQA: Evaluating Long Context Code Understanding.* arXiv:2406.06025, June 2024.

Attribution lands in `benchmarks/README.md` and `LICENSE-third-party`.

## 9. Approval gates

- [ ] **Spec review** (this document). User approves the architecture before plan + implementation.
- [ ] **Plan review** (next step — produced by `superpowers:writing-plans`). User approves the task breakdown before implementation.
- [ ] **Implementation review** (final). Two-stage review (spec compliance + code quality) per `subagent-driven-development`.

## 10. Open questions

None blocking. The optional dep boundary (MLflow + datasets as separate `[mlflow]` / `[repoqa]` extras vs combined) was resolved to **separate** so each axis can be installed independently — useful for users who only want one or the other.
