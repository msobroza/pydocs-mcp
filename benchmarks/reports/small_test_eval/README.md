# small_test late-interaction evaluation — work in progress

This directory will hold the side-by-side comparison of the runnable
retrieval methods on `--split small_test` (30 stratified tasks per
dataset). See the PR description for the live status.

## Methods being measured

| Config | Method | Cost |
|---|---|---|
| `repoqa_bm25` / `ds1000_ranked` | BM25 (FTS5, porter stemming) | free |
| `repoqa_hybrid_li_rrf` / `ds1000_hybrid_li_rrf` | BM25 + Late-Interaction MaxSim (LateOn-Code via PyLate + fast-plaid), RRF fused | local CPU encode |
| `repoqa_tree` / `ds1000_tree` | Vectorless LLM tree-reasoning (gpt-4o-mini via OpenAI) | ~$0.001/query |

## Methods deferred

8 RepoQA dense/hybrid configs (`repoqa_dense`, `repoqa_hybrid_rrf_k{30,60,100}`,
`repoqa_hybrid_wsi_{balanced,bm25,dense}`, `repoqa_hybrid_tree`) require the
`TurboQuantVectorStore` to be wired into `BuildContext.vector_store` — a
real pre-existing bug we intentionally scoped out of this PR. They will land
in a follow-up alongside their measured numbers.

## Reproduce

```bash
set -a; source python/pydocs_mcp/.env; set +a   # OPENAI_API_KEY for tree

# BM25 (fast — ~5 min)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_bm25.yaml \
  --report benchmarks/reports/small_test_eval/repoqa_bm25.md

# LI (slow — ~30 min)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_hybrid_li_rrf.yaml \
  --report benchmarks/reports/small_test_eval/repoqa_hybrid_li_rrf.md

# Tree (fast — ~5 min)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_tree.yaml \
  --report benchmarks/reports/small_test_eval/repoqa_tree.md
```

Same shape for DS-1000.

## Why three sweeps not one

The harness's per-Bash 10-minute ceiling kills any sweep that runs past
it. The LI leg legitimately needs ~30 min/dataset (PyLate encode is the
floor); the BM25/tree legs only need that long if they're forced to run
the dense `embed_chunks` stage they never read. We ship
`ingestion_bm25_only.yaml` so BM25 and tree configs skip that stage
during indexing — drops their per-task time from ~6 min to ~30 s.

The LI sweep still needs a detached `nohup` run because it can't avoid
the multi-vector encode.

## Plots (pending)

Once all 6 baseline JSONs land, render via:

```bash
python -m benchmarks.eval.plotting \
  benchmarks/baselines/repoqa_*.json \
  --output benchmarks/reports/small_test_eval/repoqa_quality.png

python -m benchmarks.eval.plotting \
  benchmarks/baselines/repoqa_*.json --timings \
  --output benchmarks/reports/small_test_eval/repoqa_timing.png

python -m benchmarks.eval.plotting \
  benchmarks/baselines/repoqa_*.json --scatter \
  --scatter-latency indexing_seconds \
  --output benchmarks/reports/small_test_eval/repoqa_quality_vs_cost.png
```
