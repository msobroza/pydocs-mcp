# pydocs-mcp Benchmark Harness — Full Reference

Companion to [SKILL.md](SKILL.md). All paths relative to the repo root.
All paths relative to repo root: ``

## 0. One-time setup

```bash
uv pip install -e benchmarks                # core: jsonl tracker + RepoQA loader
uv pip install -e "benchmarks[mlflow]"      # + MLflow tracker (only if --trackers mlflow)
uv pip install -e "benchmarks[all]"         # everything (needed for pytest suite)
```
If NOT installed, prefix every `python -m benchmarks.…` command with `PYTHONPATH=benchmarks/src` (PyPA src-layout; the package lives at `benchmarks/src/benchmarks/`). Run everything from the repo root.

## 1. Exact sweep commands

The runner is `python -m benchmarks.eval.runner` (`benchmarks/src/benchmarks/eval/runner.py`). It runs the matrix `(systems × configs)` on one dataset. Configs are comma-separated `AppConfig` overlay YAMLs from `benchmarks/configs/`; the file **stem** becomes the config column key / run name.

**A. Fast path — RepoQA, hermetic, no network (5-needle bundled fixture):**
```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --fixture benchmarks/tests/eval/fixtures/repoqa_mini.json \
  --configs benchmarks/configs/repoqa_bm25.yaml,benchmarks/configs/repoqa_dense.yaml \
  --trackers jsonl --limit 5 \
  --report benchmarks/results/fixture_run.md
```

**A'. Fast iteration on real data — RepoQA `small_test` (~30 stratified needles; network on FIRST run only, then cached at `~/.cache/pydocs-mcp/repoqa/`):**
```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --split small_test \
  --bench-cache on \
  --configs benchmarks/configs/repoqa_bm25.yaml,benchmarks/configs/repoqa_hybrid_rrf_k60.yaml,benchmarks/configs/repoqa_hybrid_wsi_balanced.yaml \
  --trackers jsonl \
  --report benchmarks/results/repoqa_smalltest.md
```
Note: `--fixture` and `--split small_test` are different things — the fixture is 5 synthetic needles fully offline; `small_test` is a deterministic ~30-needle stratified subsample of the held-out `test` tail of the real 100 (size/seed are dataclass defaults `small_test_size=30`, `split_seed=0` in `datasets/repoqa.py`, NOT CLI flags).

**B. Full RepoQA (100 needles, 10 real repos; downloads `repoqa-2024-06-23` on first run):**
```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --configs benchmarks/configs/baseline.yaml,benchmarks/configs/repoqa_dense.yaml \
  --trackers jsonl \
  --report benchmarks/results/repoqa_full.md
```
(`--split dev|test` optionally partitions per-repo; omit for all 100.)

**C. DS-1000 — prerequisites first:**
```bash
# 1. HF datasets (loader pins a revision):
huggingface-cli download --repo-type dataset code-rag-bench/ds1000
huggingface-cli download --repo-type dataset code-rag-bench/library-documentation  # oracle run only

# 2. Pinned reference project venv (needed ONLY for native pydocs-mcp runs):
cd benchmarks/fixtures/ds1000_reference_project
python -m venv .venv && .venv/bin/pip install -e .
cd -
```
Three canonical runs (different output shapes):
```bash
# Run 1 — cross-system, single-blob (rank-1 metrics only):
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --dataset ds1000 \
  --systems pydocs-mcp-composite,context7,neuledge \
  --configs benchmarks/configs/ds1000_composite.yaml \
  --metrics recall@1,mrr,precision@1,coverage,library_resolution@1 \
  --trackers jsonl

# Run 2 — pydocs-only ranked top-K (needs the reference project):
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --dataset ds1000 \
  --systems pydocs-mcp \
  --configs benchmarks/configs/ds1000_ranked.yaml \
  --metrics recall@1,recall@5,recall@10,ndcg@10,mrr,precision@1,coverage \
  --trackers jsonl \
  --corpus-dir benchmarks/fixtures/ds1000_reference_project

# Run 3 — oracle indexing, THE CodeRAG-Bench-comparable run (BM25 ref NDCG@10 ≈ 5.2):
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --dataset ds1000 \
  --systems pydocs-oracle \
  --dataset-full-prompt \
  --configs benchmarks/configs/ds1000_ranked.yaml \
  --metrics recall@1,recall@5,recall@10,ndcg@10,mrr,precision@1,coverage \
  --trackers jsonl
```
Slicing: `--dataset-library-filter pandas,numpy` (case-insensitive, `Sklearn`==`scikit-learn`, `Pytorch`==`torch`); `--split dev|test` (per-library stratified). `--dataset-full-prompt` = unstripped prompt (canonical); omitting it queries the NL question only (stricter, non-canonical).

**Full CLI flags** (`_build_arg_parser`, runner.py:579): `--systems` (default `pydocs-mcp`), `--configs` (required, CSV paths), `--dataset` (default `repoqa`), `--trackers` (default `jsonl`), `--metrics` (default `recall@1,recall@5,recall@10,mrr,pass@1-needle` = `DEFAULT_METRIC_SPECS`), `--limit N`, `--fixture PATH` (repoqa only), `--dataset-library-filter`, `--dataset-full-prompt`, `--split {all,dev,test,small_test}`, `--corpus-dir` (resolved absolute, must exist, never deleted), `--gpu`, `--report PATH`, `--bench-cache {on,off}` (default on), `--bench-cache-cleanup`.

Registered names — systems: `pydocs-mcp`, `pydocs-mcp-composite`, `pydocs-mcp-tree-only`, `pydocs-mcp-tree-parallel`, `pydocs-oracle`, `context7`, `neuledge`. Datasets: `repoqa`, `ds1000`, `repoqa-structural`. Trackers: `jsonl`, `mlflow`.

## 2. Where results land + programmatic reading

- **JSONL (always, default tracker):** `benchmarks/results/jsonl/{system}_{config-stem}_{dataset-slug}_{UTC-ts}.jsonl` — one file per (system, config) leg. Line-delimited JSON, discriminated by `_event`:
  - `{"_event":"run_start", system, config_name, dataset, params, tags, ts}`
  - `{"_event":"metric", name, value, step}` — per-task values carry `step` = task counter; aggregates carry `step: null` with names suffixed `_mean` / `_ci_low` / `_ci_high` (quality) or `_p50` / `_p95` / `_p99` (for `indexing_seconds` / `search_seconds`).
  - `{"_event":"artifact", name, path}` and `{"_event":"run_end", status: "finished"|"failed"}`.
  - Parse: filter `_event=="metric"` and pick names ending `_mean` etc.
- **Markdown report:** printed to stdout always; written to `--report PATH` if given. One column per (system, config), one row per metric (GFM table via `report.py:format_report`).
- **Python API:** `await run_sweep(...)` returns `({(system, config_stem): {metric: (mean, ci_low, ci_high)}}, tasks_ran)`.
- **MLflow (opt-in):** add `mlflow` to `--trackers` (comma-separable with jsonl, e.g. `--trackers jsonl,mlflow`); requires the `[mlflow]` extra. Local file store at `file://./benchmarks/mlruns` (default `tracking_uri` in `trackers/mlflow_tracker.py`). View: `mlflow ui --backend-store-uri file://./benchmarks/mlruns/`. There is no env-var toggle — tracking is selected purely by `--trackers`.

## 3. Index cache

Each `(corpus, ingestion-config-hash)` is indexed once into `~/.pydocs-mcp/bench/` and reused across tasks AND across sweeps sharing an ingestion pipeline — a warm re-sweep skips all indexing. Controlled by `--bench-cache on|off` (default on).

```bash
python -m benchmarks.eval.bench_cache_cli info    # list entries (key, MB, mtime)
python -m benchmarks.eval.bench_cache_cli evict   # wipe all entries
python -m benchmarks.eval.runner --bench-cache-cleanup ...  # run, then wipe ENTIRE cache (even on error)
python -m benchmarks.eval.runner --bench-cache off ...      # fresh tmp DB per task (pre-cache behavior)
```
Key facts: the cache key folds the ingestion pipeline hash, so changing embedder / ingestion YAML rebuilds automatically; **editing corpus contents in place is NOT detected** — `evict` or `--bench-cache off` after. `--gpu` is excluded from the key (no re-index on toggle). **Cache HITs record NO `indexing_seconds`** — take true indexing timings only from a cold run (post-`evict` or `--bench-cache off`). `--bench-cache-cleanup` wipes the whole cache, not just this run's entries — never use with a concurrent sweep.

## 4. Baselines: recording and comparing

- **Format:** tracked JSON in `benchmarks/baselines/` (`repoqa_snf.json` = real 100 needles, `repoqa_fixture_baseline.json` = 5-needle CI gate). Schema: `{dataset, system, config, tasks_ran, metrics: {"<name>": {mean, ci_low, ci_high}} plus {indexing_seconds|search_seconds: {p50, p95, p99}}, captured_at, git_sha, source_jsonl, label}`.
- **Recording is manual** — there is no baseline-writer CLI. Copy the `_mean`/`_ci_low`/`_ci_high` aggregates out of a run's JSONL into this schema; set `source_jsonl` to that file, `git_sha` to the sweep commit.
- **Comparing — `benchmarks/src/benchmarks/eval/ci_compare.py`:**
  ```bash
  PYTHONPATH=benchmarks/src python -m benchmarks.eval.ci_compare \
    --baseline benchmarks/baselines/repoqa_fixture_baseline.json \
    --current 'benchmarks/results/jsonl/*.jsonl' \
    --metric recall@10 --threshold 0.02
  ```
  Reads `metrics.<metric>.mean` from the baseline, finds `<metric>_mean` in the **most-recently-modified** JSONL matching the glob. Exit 0 = OK, 1 = mean dropped > threshold (flat percentage points, default 0.02 — deliberately not CI-based), 2 = input error. This is exactly what `.github/workflows/benchmark.yml` runs (fixture-vs-fixture; the real 100-needle baseline is documentation only).
- **Plotting:** `python -m benchmarks.eval.plotting <baseline.json> [<baseline2.json>...] --output x.png --metrics recall@1,recall@5,mrr` — grouped bars with CI error bars; all baselines in one figure MUST share the same `dataset` field (raises `ValueError` otherwise).

## 5. Metrics (registered in `src/benchmarks/eval/metrics/`)

| Name (spec) | Meaning |
|---|---|
| `recall@k` (use `recall@1/5/10`) | 1.0 if any relevant item appears in the top-k, else 0.0 |
| `precision@1` | 1.0 if the rank-1 item is relevant (== recall@1 for single-blob systems) |
| `mrr` | 1/rank of the first relevant item; 0.0 if none |
| `ndcg@k` (use `ndcg@10`) | Binary-relevance NDCG over top-k, normalized to [0,1] |
| `coverage` | 1.0 if the system surfaced ANY ground truth — health signal, not ranking |
| `library_resolution@1` | 1.0 if Context7's router resolved the right `/org/project` id; 0.0 for other systems |
| `pass@1-needle` | 1.0 if the top-1 item AST-matches the gold needle (RepoQA's strictest signal) |
| `indexing_seconds` / `search_seconds` | Latency observations (not `--metrics` specs), aggregated to p50/p95/p99 |

Relevance backing all of them: `metrics/_relevance.py:is_relevant` — AST-equivalence match (`ast_match.py`) when gold has a body (RepoQA), else membership in `resolved_chunk_ids` populated per-system by `gold_resolver.py` (rapidfuzz partial_ratio ≥ 85 for native; exact `doc_id` for `pydocs-oracle`). Aggregation: mean + 95% bootstrap CI, 1000 resamples, seed 0 (`metrics/aggregate.py`).

## 6. Gotchas

- **`scripts/run_repoqa.sh` referenced in the README does not exist** in `benchmarks/scripts/` — only `run_eval_gpu.sh` and the plot/build scripts. Use `python -m benchmarks.eval.runner` directly.
- **PYTHONPATH:** `PYTHONPATH=benchmarks/src` is required unless `benchmarks` is pip-installed (CI and all docs use the prefix).
- **Ranked vs composite configs:** the MCP default pipeline collapses to ONE composite chunk, which structurally caps `recall@k>1` at 0. Sweeps measuring top-K must use a ranked-preset config (e.g. `baseline.yaml` / `ds1000_ranked.yaml` → `chunk_search_ranked.yaml`); composite configs pair with rank-1 metrics only.
- **GPU:** `--gpu` alone is not enough for FastEmbed — onnxruntime silently falls back to CPU without torch's bundled NVIDIA libs on `LD_LIBRARY_PATH`. Use `benchmarks/scripts/run_eval_gpu.sh` (forces `--gpu`, sets `LD_LIBRARY_PATH` + `PYTHONPATH`; venv override `PYDOCS_VENV=`, default `.venv-li`). CPU dense-indexing RepoQA is 60–215 s/needle (days for a full sweep).
- **Env vars:** `OPENAI_API_KEY` required for tree/LLM-reranker configs (`repoqa_tree.yaml`, `repoqa_hybrid_tree.yaml`, `repoqa_bm25_tree_rerank*.yaml`) — `set -a; source .env; set +a`. Linux: `export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libopenblas.so.0` if `import turbovec` fails on `cblas_sgemm` (CI sets this).
- **Offline fixtures:** `benchmarks/tests/eval/fixtures/{repoqa_mini.json, ds1000_mini.json, ds1000_50.json}`; `--fixture` is repoqa-only. Late-interaction configs need `pip install -e ".[late-interaction]"` (~1–5 GB).
- **`--corpus-dir` typo protection:** runner fast-fails if not a directory (otherwise an empty index would silently score ~0). Only the two native DS-1000 runs need it; oracle/context7/neuledge ignore it.
- **`--metrics` names are exact strings** including `@k` (e.g. `ndcg@10`, `pass@1-needle`); the JSONL aggregate names append `_mean`/`_ci_low`/`_ci_high`.
- Sweep is sequential (one task at a time) — cold-run timings are uncontended; don't parallelize sweeps sharing the bench cache with `--bench-cache-cleanup`.

Key files: `benchmarks/src/benchmarks/eval/runner.py` (sweep + CLI), `metrics/` (metric impls), `trackers/jsonl_tracker.py`, `trackers/mlflow_tracker.py`, `ci_compare.py`, `bench_cache_cli.py`, `_bench_cache.py`, `plotting.py`, `report.py`, `benchmarks/configs/*.yaml`, `benchmarks/baselines/*.json`, `benchmarks/EXPERIMENTS.md` (12-condition small_test playbook), `.github/workflows/benchmark.yml` (CI gate example).