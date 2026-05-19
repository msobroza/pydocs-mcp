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
