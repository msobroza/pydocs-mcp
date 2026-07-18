---
name: comparing-retrieval-methods
description: Use when comparing retrieval pipeline configs (BM25, dense, hybrid RRF/WSI, late-interaction, LLM tree-rerank, graph expansion) on the pydocs-mcp benchmark harness, deciding which config wins, checking a change for metric regressions against a baseline, or choosing the right dataset, split, or metric for a sweep.
---

# Comparing Retrieval Methods

## Overview

Run the in-repo benchmark harness to compare retrieval pipeline YAMLs and read the verdict off three numbers: **recall@5 (headline) · MRR (tiebreaker) · recall@10 on the `repoqa-structural` dataset (do-no-harm gate)**. All three are plain `DEFAULT_METRIC_SPECS` metrics — the gate is not a separate metric, it is `recall@10` from a second sweep run with `--dataset repoqa-structural`. Report rows follow `DEFAULT_METRIC_SPECS` order: recall@1, recall@5, recall@10, mrr, pass@1-needle.

Full CLI flags, DS-1000 recipes, baseline schema, and JSONL format: see [harness-reference.md](harness-reference.md).

## Canonical comparison command

Run from the repo root — the runner resolves `--configs` paths relative to the **current working directory**, and all examples assume it is the repo root. The `PYTHONPATH` prefix is mandatory unless `benchmarks` is pip-installed (`uv pip install -e benchmarks`):

```bash
PYTHONPATH=benchmarks/src python -m pydocs_eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --split small_test \
  --bench-cache on \
  --configs benchmarks/configs/bm25.yaml,benchmarks/configs/hybrid_rrf_k60.yaml \
  --trackers jsonl \
  --report benchmarks/results/compare.md
```

The config file **stem** becomes the column name in the report (`bm25.yaml` → column `bm25`). Add more configs comma-separated — the sweep is `(systems × configs)`.

For **quality** comparisons a warm bench cache is expected and correct — leave `--bench-cache on` and let repeated sweeps reuse the index. Evict only when you need true indexing timings or after editing a corpus in place. Any config with a dense branch (hybrid included) embeds on the first cold run: on CPU that is 60–215 s per needle, so use `benchmarks/scripts/run_eval_gpu.sh` for cold dense runs.

## Which config YAML is which method

| Method | Canonical config | Notes |
|---|---|---|
| BM25 only | `bm25.yaml` | |
| Dense (default embedder) | `dense.yaml` | FastEmbed bge-small |
| Dense, best recorded | `dense_f2llm330m.yaml` | Older recorded results carry dataset-prefixed (and underscore-variant) stems of this config — treat them as the same method; the old→new map lives in the "Renamed configs" section of `benchmarks/EXPERIMENTS.md` |
| Hybrid RRF | `hybrid_rrf_k60.yaml` | k=60 is the default; k=30/100 recorded as equivalent |
| Hybrid weighted (WSI) | `hybrid_wsi_balanced.yaml` | `_bm25` / `_dense` variants shift the weights |
| Late interaction | `li.yaml` | needs `pip install -e ".[late-interaction]"` (~1–5 GB) |
| LLM tree rerank | `bm25_tree_rerank_gpt55.yaml` | needs `OPENAI_API_KEY` (`set -a; source .env; set +a`) |
| Graph expansion | `dense_graph_f2llm330m.yaml` | pairs with the `repoqa-structural` dataset gate |
| Graph + MENTIONS edges | `dense_graph_mentions_f2llm330m.yaml` | `_weighted` variant down-weights doc edges via `graph_expand.kind_weights`; structural gate can't show mentions wins (golds are calls/inherits-minted) |
| Ranked project baseline | `baseline.yaml` | |

## Choosing dataset and split

| You want | Use | Cost |
|---|---|---|
| Smoke-test the sweep wiring, fully offline | `--fixture benchmarks/tests/fixtures/repoqa_mini.json --limit 5` | seconds |
| Fast iteration on real data | `--dataset repoqa --split small_test` (~30 needles) | minutes warm-cache |
| Confirm a winner before adopting it | full `--split test`, plus `--dataset repoqa-structural` as the do-no-harm gate | GPU recommended |
| External calibration vs published numbers | DS-1000 oracle run (see reference, Run 3) | HF downloads + setup |

`--fixture` and `--split small_test` are different things: the fixture is 5 synthetic needles, hermetic; `small_test` is a stratified subsample of the real held-out test tail.

**Overfitting warning:** `small_test` has absorbed many recorded tuning sweeps — treat it as a development split, and never promote a config on `small_test` numbers alone. If a `small_dev` split is available (`--split small_dev`), iterate there instead: it draws from the dev partition and keeps the test tail unburned. Promotion is one-way: iterate on `small_test` → confirm once on full `test` + structural gate. A config that loses on `test` goes back to iteration; its variants don't get fresh test shots.

## Reading the result

- The GFM table prints to stdout and to `--report PATH`. One column per (system, config), one row per metric with 95% bootstrap CI.
- Programmatic: parse `benchmarks/results/jsonl/{system}_{config-stem}_{dataset}_{ts}.jsonl` — take `_event=="metric"` lines with names ending `_mean` / `_ci_low` / `_ci_high`. Filenames are timestamped: pick the newest matching glob, the same way `reporting/ci_compare.py` does.
- Regression gate against a recorded baseline:

```bash
PYTHONPATH=benchmarks/src python -m pydocs_eval.reporting.ci_compare \
  --baseline benchmarks/baselines/repoqa_fixture_baseline.json \
  --current 'benchmarks/results/jsonl/*.jsonl' \
  --metric recall@10 --threshold 0.02
```

## Common mistakes

| Mistake | Reality |
|---|---|
| Comparing composite configs on `recall@5`/`recall@10` | The composite pipeline collapses to ONE chunk, structurally capping recall@k>1 at 0. Top-K sweeps need a ranked-preset config (`baseline.yaml`, `ranked.yaml`); composite configs pair with rank-1 metrics only. |
| Reading `indexing_seconds` from a warm-cache run | Cache HITs record no indexing time. Take indexing timings only from a cold run (`--bench-cache off` or after `bench_cache_cli evict`). |
| Calling a <2-needle delta a win on `small_test` | n=30 → one needle = 3.3 points; recall CIs are ±0.15 wide. Require non-overlapping CIs or a paired per-needle comparison from the per-task JSONL events; confirm on full `test`. |
| Running dense sweeps on CPU with bare `--gpu` | onnxruntime silently falls back to CPU without torch's NVIDIA libs on `LD_LIBRARY_PATH`. Use `benchmarks/scripts/run_eval_gpu.sh`. CPU dense-indexing is 60–215 s/needle. |
| Looking for `run_repoqa.sh` under `benchmarks/scripts/` | It lives at the repo root — `scripts/run_repoqa.sh` — and is a thin forwarder that sets `PYTHONPATH` and execs `python -m pydocs_eval.runner`. |
| Editing a corpus in place between cached runs | The bench cache does not detect in-place corpus edits — `evict` or `--bench-cache off` after editing. |
| Trusting hybrid-LI results recorded before 2026-07-10 | Their overlays set ingestion under a dead `pipelines.ingestion` key, so fast-plaid was never populated — the LI branch scored nothing and those "hybrid LI" numbers are effectively BM25-only. Re-run; see benchmarks/EXPERIMENTS.md §Late-interaction conditions. |
| Tree/LLM configs failing silently | They need `OPENAI_API_KEY` in the environment before the sweep starts. |
