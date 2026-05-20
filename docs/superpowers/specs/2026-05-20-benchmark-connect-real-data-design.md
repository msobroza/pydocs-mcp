# Benchmark Harness — Connect to Real Data + Latency + Comparative Wiring

**Status**: Draft, awaiting review
**Date**: 2026-05-20
**Author**: msobroza
**Builds on**: `2026-05-19-benchmark-harness-repoqa-mlflow-solid-design.md` (PR #26, merged as `644abf0`)

## 1. Goal

Lift the benchmark harness from "structurally complete on a fixture" to "actually running on the real benchmark with real comparative-system wiring and real latency measurement." Bundles five deferred follow-ups from PR #26:

1. Replace the placeholder HuggingFace loader with the real RepoQA GitHub-Release distribution.
2. Expose `strict_suffix` in `ReferenceResolverConfig` (unblocks `benchmarks/configs/strict_suffix_off.yaml`).
3. Wire `library_name` / `library` from `EvalTask.metadata` for Context7 + Neuledge.
4. Capture the first real measured baseline against the 100-needle Python subset.
5. Add latency-percentile metrics (p50 / p95 for index + search) via runner-emitted observations.

## 2. Non-goals

- SWE-bench Verified retrieval-only dataset (separate follow-up).
- Weights & Biases tracker (separate follow-up).
- Cross-language RepoQA support — Python-only stays the in-scope subset; the loader's per-language branch is kept narrow.
- Real-user log-mined eval set.
- Production-grade Context7 / Neuledge orchestration (e.g., concurrent sessions, retries). The wiring lands as the simplest correct shape; ops hardening is later.

## 3. Critical discovery: RepoQA distribution model

PR #26 assumed RepoQA was a HuggingFace dataset reachable via `datasets.load_dataset("evalplus/repoqa", revision=...)`. Investigation against the real EvalPlus repo (`https://github.com/evalplus/repoqa/blob/main/repoqa/data.py`) revealed:

- **RepoQA is NOT on HuggingFace.** No `evalplus/repoqa` HF dataset exists; HF search returns only unrelated `Nutanix/RepoQA-*` forks.
- **Real distribution**: a single gzipped JSON file from the `evalplus/repoqa_release` GitHub Releases at
  `https://github.com/evalplus/repoqa_release/releases/download/{VERSION}/repoqa-{VERSION}.json.gz`
- **Current pinned version**: `2024-06-23` (verified — downloads cleanly, 12 MB compressed).
- **No third-party loader needed.** `urllib.request` + `gzip` + `json` (stdlib only) suffice.

This invalidates four PR #26 assumptions:
- The `[repoqa]` optional extra (`datasets>=2.0`, `huggingface-hub>=0.20`) is unnecessary — drop it.
- The `_PINNED_REVISION = "<TODO>"` placeholder becomes `_PINNED_RELEASE_VERSION = "2024-06-23"`.
- `RepoQADataset._load_from_hf` becomes `_load_from_release`.
- The fixture JSON shape needs to match the real schema.

## 4. Real RepoQA schema (post-investigation)

Top-level dict keyed by language:

```python
{
  "python": [repo, ...],   # 10 Python repos
  "cpp": [...], "java": [...], "typescript": [...], "rust": [...], "go": [...]
}
```

Per-repo entry (Python: 10 repos × 10 needles = 100 needles, 1394 source files):

| Key | Type | Description |
|---|---|---|
| `repo` | `str` | e.g. `"psf/black"` |
| `commit_sha` | `str` (40-char) | pinned commit for reproducibility |
| `topic` | `str` | one-word category, e.g. `"formatter"` |
| `entrypoint_path` | `str` | top-level dir within the repo, e.g. `"src/black"` |
| `content` | `dict[str, str]` | every source file's content keyed by repo-relative path |
| `dependency` | `dict` | file-dependency graph (unused by retrieval) |
| `functions` | `dict[str, list[fn]]` | extracted functions per file (unused by retrieval) |
| `needles` | `list[needle]` | 10 needles per repo |

Per-needle entry:

| Key | Type | Description |
|---|---|---|
| `name` | `str` | the gold function name |
| `description` | `str` | NL query (the `EvalTask.query`) |
| `path` | `str` | file path within the repo |
| `start_line` / `end_line` | `int` | 1-indexed line range of the needle's body in `content[path]` |
| `start_byte` / `end_byte` | `int` | byte range alternative |
| `global_*_line` / `global_*_byte` | `int` | range across the concatenated repo (unused) |
| `code_ratio` | `float` | repo-level statistic (unused by retrieval) |

## 5. Design

### 5.1 RepoQA loader (item #1)

`benchmarks/src/benchmarks/eval/datasets/repoqa.py` is rewritten end-to-end:

```python
# protocols (unchanged) — RepoQADataset emits EvalTask with the body extracted from content[path]
_PINNED_RELEASE_VERSION = "2024-06-23"
_RELEASE_URL = (
    "https://github.com/evalplus/repoqa_release/releases/download/"
    "{version}/repoqa-{version}.json.gz"
)

@dataset_registry.register("repoqa")
@dataclass
class RepoQADataset:
    name: str = "repoqa"
    revision: str = _PINNED_RELEASE_VERSION   # the date tag, surfaces in run metadata
    fixture_path: Path | None = None          # offline / test override
    cache_dir: Path = field(default_factory=lambda: Path("~/.cache/pydocs-mcp/repoqa").expanduser())
    language: str = "python"                  # subset filter
    _rows_cache: list[dict] | None = field(default=None, init=False, repr=False)

    async def tasks(self) -> AsyncIterator[EvalTask]:
        if self._rows_cache is None:
            self._rows_cache = (
                self._load_from_fixture()
                if self.fixture_path is not None
                else self._load_from_release()
            )
        for row in self._rows_cache:
            yield _row_to_task(row)
```

**`_load_from_release`** uses stdlib only:

```python
def _load_from_release(self) -> list[dict]:
    target = self.cache_dir / f"repoqa-{self.revision}.json"
    if not target.exists():
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        url = _RELEASE_URL.format(version=self.revision)
        with urllib.request.urlopen(url, timeout=60) as resp:
            payload = gzip.decompress(resp.read())
        target.write_bytes(payload)
    data = json.loads(target.read_text())
    repos = data.get(self.language, [])
    return _flatten_needles(repos)

def _flatten_needles(repos: list[dict]) -> list[dict]:
    """One row per needle (NOT per repo). Each row carries the repo content
    once; corpus_source closures share it via the default-arg trick."""
    rows: list[dict] = []
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
```

**`_row_to_task`** extracts the needle body from `content[path]` using the line range:

```python
def _row_to_task(row: dict) -> EvalTask:
    needle = row["needle"]
    content = row["content"]  # dict[str, str]
    needle_body = _extract_body(
        content[needle["path"]],
        needle["start_line"],
        needle["end_line"],
    )
    repo_id = f"{row['repo']}@{row['commit_sha'][:7]}"
    return EvalTask(
        task_id=f"{repo_id}/{needle['path']}::{needle['name']}",
        query=needle["description"],
        gold=GoldAnswer(ast_body=needle_body),
        corpus_source=lambda files=content: materialize_corpus(files),
        metadata={
            "repo": row["repo"],         # consumed by Context7System.library_name + NeuledgeSystem.library
            "commit": row["commit_sha"],
            "topic": row["topic"],
            "language": "python",
            "needle_name": needle["name"],
            "needle_path": needle["path"],
        },
    )

def _extract_body(source: str, start_line: int, end_line: int) -> str:
    lines = source.splitlines()
    return "\n".join(lines[start_line - 1 : end_line])
```

**No `[repoqa]` extra in pyproject.** Drop:
```toml
repoqa = ["datasets>=2.0", "huggingface-hub>=0.20"]
all = ["mlflow>=2.0", "datasets>=2.0", "huggingface-hub>=0.20"]
```
becomes:
```toml
all = ["mlflow>=2.0"]
```

**Update the fixture** at `benchmarks/tests/eval/fixtures/repoqa_mini.json` to match the real shape: a top-level dict with `"python"` → 1 repo entry × 2 needles. Synthetic content/source paths so tests stay offline + fast.

### 5.2 `strict_suffix` resolver knob (item #2)

`python/pydocs_mcp/retrieval/config.py` — add the field to the existing `ReferenceResolverConfig` sub-model:

```python
class ReferenceResolverConfig(BaseModel):
    include_stdlib: bool = True
    # WHY: when False, the resolver only fires Rule B (exact qname match)
    # — Rule C (strict-suffix-within-package) is skipped. Useful as an
    # ablation knob: how much does suffix matching contribute vs cost?
    strict_suffix: bool = True
```

`python/pydocs_mcp/extraction/strategies/reference_resolver.py` — wire the toggle:

```python
def _resolve_one(self, ref):
    ...
    # Rule B (exact match) always runs.
    if to_name in self.qname_universe:
        return to_name
    # Rule C (strict suffix) runs only when enabled.
    if self.strict_suffix:
        candidates = [...]
        if len(candidates) == 1:
            return candidates[0]
    # F20 / Rule D / Rule E unchanged.
```

`ReferenceResolver` gains a `strict_suffix: bool = True` field. `IndexingService._resolve_references` reads the toggle from `_get_resolver_config()` and threads it into the resolver constructor — same pattern as `include_stdlib`.

`benchmarks/configs/strict_suffix_off.yaml` (already checked in) loads cleanly after this change.

### 5.3 Context7 / Neuledge metadata wiring (item #3)

Both adapter classes already carry `library_name` / `library` instance fields with empty defaults. The runner needs to populate them per task BEFORE calling `index()`.

`runner.py` inside the per-task loop:

```python
async for task in dataset.tasks():
    if limit is not None and count >= limit:
        break

    # Seed system metadata from the task before index — Context7 and
    # Neuledge resolve their library identifier from this seed.
    _maybe_set_library(system, task.metadata)

    corpus_dir = task.corpus_source()
    try:
        t0 = time.perf_counter()
        await system.index(corpus_dir, config)
        index_secs = time.perf_counter() - t0
        ...
```

`_maybe_set_library` is small + system-agnostic:

```python
def _maybe_set_library(system: System, metadata: Mapping[str, str]) -> None:
    """Set the comparative-system library identifier from task metadata.

    Context7 expects ``library_name`` (the human name, e.g. ``"psf/black"``);
    Neuledge expects ``library`` (the install identifier, e.g. ``"psf/black@<sha>"``).
    Pydocs-mcp ignores both — it indexes the corpus directory and answers
    from its own DB.
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

The branchless `hasattr` form keeps the runner systems-agnostic and avoids a special-case `if isinstance(...)` ladder.

### 5.4 First real baseline (item #4)

Once items 1–3 land, run:

```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
    --systems pydocs-mcp \
    --configs benchmarks/configs/baseline.yaml \
    --dataset repoqa \
    --trackers jsonl
```

(No `--fixture`, no `--limit`. Full 100-needle Python subset.) Expected runtime: 5–10 min on CPU.

Capture the resulting JSONL aggregates, write the real metrics into `benchmarks/baselines/repoqa_snf.json`. The schema of the baseline file stays identical to PR #26's; only the numbers change.

**CI compatibility**: the `.github/workflows/benchmark.yml` workflow currently uses `--fixture benchmarks/tests/eval/fixtures/repoqa_mini.json` to keep CI hermetic. That stays — CI runs the fixture-baseline comparison (fixture-vs-fixture). The REAL baseline is `repoqa_snf.json` and is bumped manually via the existing `bench-baseline-bump` workflow (or a one-shot manual commit) when the loader changes.

**Mocking note** (from the brainstorm): the user picked "mock HF in CI" as the fallback strategy. With the GitHub Releases distribution this becomes trivial — the CI cache step can mirror the gzip file into an artifact instead of re-downloading per run. We DON'T need to mock at all for the fixture-based CI flow; the real-data download only happens when the user runs locally with `--no-fixture`.

### 5.5 Latency-percentile metrics (item #5) — Option A

Runner emits two new per-task observations: `indexing_seconds` and `search_seconds`. They flow through the same JSONL / MLflow tracker channel as quality metrics, with `step=task_index` per task.

Aggregation: a new helper alongside `mean_with_bootstrap_ci` computes simple percentiles deterministically:

```python
# benchmarks/src/benchmarks/eval/metrics/aggregate.py

def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile, deterministic. Empty → 0.0.

    ``q`` is in [0, 1]. ``percentile([1,2,3,4], 0.5) == 2.5``.
    Matches numpy.percentile's default method.
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

Runner aggregation block (mirrors the existing `_mean` / `_ci_low` / `_ci_high` triple):

```python
# After the per-task loop:
for latency_key in ("indexing_seconds", "search_seconds"):
    values = per_metric_values.get(latency_key, [])
    p50 = percentile(values, 0.5)
    p95 = percentile(values, 0.95)
    p99 = percentile(values, 0.99)
    aggregates[latency_key] = (p50, p95, p99)  # Note: triple shape but different semantic
    for h, tracker in zip(handles, trackers):
        tracker.log_metric(h, f"{latency_key}_p50", p50, step=None)
        tracker.log_metric(h, f"{latency_key}_p95", p95, step=None)
        tracker.log_metric(h, f"{latency_key}_p99", p99, step=None)
```

**The aggregate dict's tuple-of-3 semantic differs between quality metrics (`(mean, ci_low, ci_high)`) and latency metrics (`(p50, p95, p99)`).** The report module needs to know which is which — either by name convention (`_seconds` suffix → percentiles, else → mean+CI), or by a typed `MetricSummary` wrapper. Pick the name-convention approach: it's a runner-only convention, no Protocol change, and the suffix is already part of the metric name.

`report.py` updates to render the appropriate cells per row:

```python
def _format_cell(triple: tuple[float, float, float], is_latency: bool) -> str:
    if is_latency:
        p50, p95, p99 = triple
        return f"p50 {p50:.2f}s | p95 {p95:.2f}s | p99 {p99:.2f}s"
    mean, lo, hi = triple
    return f"{mean:.1%} [{lo:.1%}, {hi:.1%}]"

def _is_latency_metric(name: str) -> bool:
    return name.endswith("_seconds")
```

No new metric Protocol. No new metric classes. No Scorer changes. Latency is observably different from retrieval quality (different aggregation, different units) and stays a runner-side concern.

## 6. Module / file layout (delta on top of PR #26)

```
benchmarks/
├── pyproject.toml                          ← drop [repoqa] extra
├── src/benchmarks/eval/
│   ├── datasets/repoqa.py                  ← rewritten (stdlib + GitHub Releases)
│   ├── runner.py                           ← +time.perf_counter + _maybe_set_library
│   ├── metrics/aggregate.py                ← +percentile()
│   └── report.py                           ← +_is_latency_metric, _format_cell branch
└── tests/eval/
    ├── fixtures/repoqa_mini.json           ← rewritten in real schema (1 repo × 2 needles)
    ├── test_repoqa_loader.py               ← updated for new schema + URL stub
    ├── test_aggregate.py                   ← +percentile tests
    ├── test_runner_smoke.py                ← +latency emitted; +library wiring assertion
    └── test_report.py                      ← +latency cell formatting

python/pydocs_mcp/
├── retrieval/config.py                     ← +ReferenceResolverConfig.strict_suffix
└── extraction/strategies/reference_resolver.py ← Rule C gated on strict_suffix flag
```

No new directories. No new Protocols. No new registries.

## 7. Acceptance criteria

| # | Statement |
|---|---|
| AC1 | `RepoQADataset` constructs without `datasets` / `huggingface-hub` installed (extras dropped). |
| AC2 | `python -m benchmarks.eval.runner --help` lists `repoqa` in `--dataset` choices. |
| AC3 | `_PINNED_RELEASE_VERSION = "2024-06-23"`. The cache file `~/.cache/pydocs-mcp/repoqa/repoqa-2024-06-23.json` exists after the first real run. |
| AC4 | The fixture path (`--fixture <path>`) bypasses the GitHub download entirely. |
| AC5 | A real (non-fixture) full Python sweep produces 100 `EvalTask`s, each with a non-empty `gold.ast_body`. |
| AC6 | `strict_suffix: false` in YAML overlay parses cleanly through `AppConfig.load`. |
| AC7 | With `strict_suffix=False`, the resolver does NOT execute Rule C — verifiable by a unit test that pins `to_node_id` differences. |
| AC8 | `runner.py` calls `_maybe_set_library(system, task.metadata)` before `system.index(...)` each iteration. |
| AC9 | Context7 + Neuledge systems receive `library_name` / `library` populated from `metadata["repo"]` (and `metadata["commit"]` for Neuledge). Pydocs-mcp is unaffected. |
| AC10 | Per-task `indexing_seconds` and `search_seconds` flow through every selected tracker. |
| AC11 | Aggregate per-leg emits `*_seconds_p50`, `*_seconds_p95`, `*_seconds_p99`. |
| AC12 | `report.py` renders latency rows as `p50 X.XXs | p95 ... | p99 ...` and quality rows as `X.X% [lo, hi]` (existing behavior preserved). |
| AC13 | `benchmarks/baselines/repoqa_snf.json` contains REAL measured values from the 100-needle sweep (NOT the fixture's 5-task values from PR #26). |
| AC14 | Tests: 100 passing → ≥110 passing (no regressions; new tests for percentile + loader + library wiring + strict_suffix). |
| AC15 | Ruff clean across `benchmarks/` + `python/`. |
| AC16 | All existing PR #26 ACs (1–14) still hold. |

## 8. Risks and mitigations

| Risk | Mitigation |
|---|---|
| GitHub Releases URL changes / `2024-06-23` version is retired by EvalPlus | Pinned version + checksum (added to `_PINNED_RELEASE_VERSION` comment). Future bump via a new spec; loader is stdlib so no transitive dep churn. |
| Cache directory permissions vary in dev / CI environments | Lazy `mkdir(parents=True, exist_ok=True)`; the loader falls back to a tmp dir if `cache_dir` is unwritable. |
| `urllib.request.urlopen` lacks retries / connection pooling | First real run is one-shot; retries are unnecessary. If a future PR sees retry needs, swap `urllib` → `httpx` (already a dep). |
| `_extract_body` line-range extraction off-by-one across line-ending conventions | Pin with a unit test that compares `_extract_body(content, start, end)` against the byte-range alternative on a known needle from the real dataset. |
| Loader's first run downloads 12 MB | Cache + reuse for subsequent runs. Same cost as PR #26's HF download would have been. |
| Real baseline numbers reveal pydocs-mcp underperforms expectations | That's the POINT of the benchmark. The PR ships the numbers as-is; tuning is a separate (intended-by-design) follow-up. |
| Latency measurement is sensitive to CI noise | Report includes p50/p95/p99 (already noise-tolerant). For CI, the `ci_compare` gate runs against quality recall@10 only; latency is observational. |

## 9. Out of scope (separate follow-up PRs)

- Multi-language RepoQA (cpp, java, typescript, rust, go).
- SWE-bench Verified retrieval-only dataset.
- Weights & Biases tracker.
- In-house log-mined eval set.
- Peak-memory observability (would compose with the latency design — runner-emitted observation).
- Real Context7 / Neuledge end-to-end runs (network access, account credentials — out of scope for THIS PR's CI).

## 10. Approval gates

- [ ] **Spec review** (this document).
- [ ] **Plan review** — produced by `superpowers:writing-plans` after this spec is approved.
- [ ] **Implementation review** — two-stage (spec + code quality) per task via `superpowers:subagent-driven-development`.

## 11. Open questions

None. The brainstorm investigated the real RepoQA distribution and locked the latency design (Option A) + HF-fallback question dissolves (no HF involved).
