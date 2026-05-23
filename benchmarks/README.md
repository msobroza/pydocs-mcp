# pydocs-mcp Benchmark Suite

Real retrieval-quality evaluation for `pydocs-mcp` against a public benchmark
(**RepoQA-SNF**, arXiv 2406.06025) with **MLflow**-backed experiment tracking
and comparative slots for **Context7** and **Neuledge Context**.

The harness exists to A/B test YAML pipeline tunings (`AppConfig`) on a real
benchmark, then track every `(system × config × dataset)` combination as one
MLflow run with comparable params, metrics, and artifacts.

> The pre-trilogy placeholder (`fake_project/` + synthetic `dataset_gen.py`)
> has been removed — it synthesized queries from the chunks it just indexed,
> so a chunker change shifted both the corpus and the queries together and
> the eval was blind. The new harness uses an external benchmark (RepoQA-SNF)
> with stable gold answers that the system under test cannot influence.

## Install

`uv`-friendly extras let you pull in only what you need:

```bash
uv pip install -e benchmarks                # core only — JSONL tracker, stdlib RepoQA loader
uv pip install -e "benchmarks[mlflow]"      # + MLflow tracker
uv pip install -e "benchmarks[all]"         # everything
```

`pip` works too — the optional extras are stock PEP 508 syntax.

## Run

The runner CLI is exposed as a module entry-point:

```bash
# Baseline run, pydocs-mcp only, against the bundled fixture (no network download).
./scripts/run_repoqa.sh \
    --systems pydocs-mcp \
    --configs <path-to-baseline.yaml> \
    --trackers jsonl \
    --fixture <path-to-fixture> \
    --limit 5

# Full sweep across YAML config variants (when configs/ lands in Task 9).
./scripts/run_repoqa.sh \
    --systems pydocs-mcp \
    --configs <baseline.yaml>,<no_stdlib.yaml>,<wide_chunks.yaml> \
    --trackers jsonl

# View results in MLflow UI (requires the [mlflow] extra).
mlflow ui --backend-store-uri file://./benchmarks/mlruns/
```

The runner can also be invoked directly (the `benchmarks/` package lives
under `benchmarks/src/` following the PyPA src-layout):

```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --help
```

For tests and offline development, pass a `--fixture` JSON to bypass the
RepoQA download entirely (see `benchmarks/tests/eval/fixtures/repoqa_mini.json`).

## Metrics

Every `(system × config × dataset)` run reports the following per-task metrics
plus aggregate values with a 95% bootstrap CI (1000 resamples, seed=0):

- **`recall@k`** — `1.0` iff the gold function appears in the top-`k` retrieved
  chunks under an AST-equivalent match (whitespace and comment tolerant);
  `0.0` otherwise. Reported at `k ∈ {1, 5, 10}`.
- **`mrr`** — Mean reciprocal rank. The score per task is `1/rank` of the first
  AST-matching item, or `0.0` if no match exists in the returned set. The
  aggregate is the arithmetic mean across tasks.
- **`pass@1-needle`** — `1.0` iff the top-1 retrieved item matches the gold
  needle, `0.0` otherwise. The strictest signal — sensitive to small ranking
  changes that `recall@k` smooths over.

The aggregator (`benchmarks/eval/metrics/aggregate.py`) emits the mean plus a
bootstrap confidence interval for each metric so regression gates can compare
runs without false positives from per-task variance.

## Current baselines

Two baseline JSON files are tracked in `benchmarks/baselines/`:

| File | What | Tasks | recall@1 | recall@5 | recall@10 | MRR |
|---|---|---:|---:|---:|---:|---:|
| `repoqa_snf.json` | Real 100-needle sweep against the Python subset of `repoqa-2024-06-23` | 100 | 14.0% [7%, 21%] | 17.0% [10%, 24%] | 18.0% [11%, 26%] | 15.2% [9%, 22%] |
| `repoqa_fixture_baseline.json` | 5-needle hermetic CI gate fixture | 5 | 60.0% | 80.0% | 80.0% | 70.0% |

CIs are 95% Wilson intervals from bootstrap resampling (1000 iter, seed=0).
Both baselines were captured against the `chunk_search_ranked.yaml` preset
that returns top-K ranked separate chunks (the MCP server's default
`chunk_search.yaml` collapses to one composite chunk and structurally pegs
`recall@k > 1` at 0 — see PR #31 for the rationale split).

The real-100-needle numbers are the headline figure: PR-B3.1 (dense
embeddings + RRF) should beat `recall@10 = 18%` to be worth landing.

## Visualizing baselines

`benchmarks.eval.plotting` produces grouped vertical bar plots from one or
more baseline JSON files. Each baseline becomes a colored bar group; each
metric becomes an X-axis category; 95% CI error bars come straight from
each metric's `ci_low` / `ci_high`. Default palette is seaborn's
`colorblind` (colorblind-safe + Nature figure-guideline compliant).

```bash
# Plot a single baseline (BM25 only, current state).
PYTHONPATH=benchmarks/src python -m benchmarks.eval.plotting \
    benchmarks/baselines/repoqa_snf.json \
    --output benchmarks/results/plots/bm25_only.png \
    --metrics recall@1,recall@5,recall@10,mrr,pass@1-needle

# Side-by-side compare two baselines (e.g., future dense vs current BM25).
PYTHONPATH=benchmarks/src python -m benchmarks.eval.plotting \
    benchmarks/baselines/repoqa_snf.json \
    benchmarks/baselines/repoqa_snf_dense.json \
    --output benchmarks/results/plots/bm25_vs_dense.png \
    --title "BM25 vs dense on RepoQA-2024-06-23 (Python, n=100)"
```

The legend identifies each system as `<system> / <config> (<label>) [<git_sha>, n=<tasks>]`
so a plot stays self-describing even when copy-pasted into a PR
description. Sample output (committed to `benchmarks/docs/repoqa_baselines.png`):

![pydocs-mcp BM25 baseline plot](docs/repoqa_baselines.png)

Programmatic API — same behavior, more flexible for notebook use:

```python
from pathlib import Path
from benchmarks.eval.plotting import plot_baselines

fig = plot_baselines(
    baselines=[
        Path("benchmarks/baselines/repoqa_snf.json"),
        # Path("benchmarks/baselines/repoqa_snf_dense.json"),  # PR-B3.1
    ],
    metrics=("recall@1", "recall@5", "recall@10", "mrr"),
    output=Path("benchmarks/results/plots/repoqa_real.png"),
    palette="colorblind",          # also: "deep", "muted", "Set2"
    title="pydocs-mcp on RepoQA",  # default: <dataset> (<tasks_ran> tasks)
)
```

The returned `matplotlib.figure.Figure` is yours to further customize,
`.show()` in a notebook, or `.savefig()` again with different DPI.

## What this benchmark proxies — and what it does NOT

**What it proxies well:**

- **Natural-language description → Python function retrieval.** This is the
  dominant query shape for `search(query, kind, ...)` on the MCP surface.
- **Long-context indexing.** RepoQA tasks ship a full repo slice per task, so
  the chunker and indexer are exercised on real-world code layouts (not
  synthetic toys).
- **A/B testing YAML tunings.** Capture toggles, ranking weights, chunker
  parameters, and resolver thresholds can all be sweep-compared against the
  same dataset and metric set. This is the architectural payoff of the
  "behavior in YAML, surface stable" rule from `CLAUDE.md`.
- **Cross-system retrieval comparison.** `pydocs-mcp` (in-process pipeline)
  is comparable against `context7` (cloud MCP API) and `neuledge`
  (local MCP HTTP) on the same queries and the same gold answers.

**What it does NOT proxy:**

- **End-to-end LLM code generation quality.** The harness measures retrieval
  only — what an LLM does with the retrieved chunks is out of scope.
- **Multi-file / call-graph retrieval.** Each RepoQA task is single-needle;
  SWE-bench Verified retrieval-only would cover this and is on the roadmap
  as a one-file plugin.
- **Real-user query distribution.** RepoQA queries are LLM-generated from
  function docstrings. An in-house log-mined eval set is separate future
  work.
- **Multi-language coverage.** Python only.
- **Indexing throughput.** The harness optimises for retrieval signal, not
  indexing latency; an indexing-latency benchmark is a deferred follow-up.

When you read a result, treat it as evidence about the retrieval surface,
not the whole system.

## License and attribution

- **RepoQA-SNF** — Apache-2.0, by the EvalPlus team. Cite:
  > Liu, J. et al. *RepoQA: Evaluating Long Context Code Understanding.*
  > arXiv:2406.06025, June 2024.
- **MLflow** — Apache-2.0, Databricks. Used as the experiment-tracking
  backend; tracking URI defaults to a local `file://` store so no network or
  remote server is required to run the harness.
- Third-party attribution lands in `LICENSE-third-party` once it is added.

## Running tests

```bash
uv pip install -e "benchmarks[all]"
pytest benchmarks/ -q
```

The bundled fixture (`benchmarks/tests/eval/fixtures/repoqa_mini.json`) lets
the full test suite run without network access.
