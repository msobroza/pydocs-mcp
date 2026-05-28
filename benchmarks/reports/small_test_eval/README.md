# `small_test` late-interaction evaluation — work in progress

This directory holds the side-by-side comparison of the runnable
retrieval methods on `--split small_test` (30 stratified tasks per
dataset). See the PR description for the live status.

## Methods being measured

| Config | Method | Cost |
|---|---|---|
| `repoqa_bm25` / `ds1000_bm25` | BM25 (FTS5, porter stemming) | free |
| `repoqa_hybrid_li_rrf` / `ds1000_hybrid_li_rrf` | BM25 + Late-Interaction MaxSim (LateOn-Code via PyLate + fast-plaid), RRF fused | local CPU encode |
| `repoqa_tree` / `ds1000_tree` | Vectorless LLM tree-reasoning (gpt-4o-mini via OpenAI) | ~$0.001/query |

## Methods deferred

8 RepoQA dense / hybrid configs (`repoqa_dense`,
`repoqa_hybrid_rrf_k{30,60,100}`, `repoqa_hybrid_wsi_{balanced,bm25,dense}`,
`repoqa_hybrid_tree`) require the `TurboQuantVectorStore` to be wired
into `BuildContext.vector_store` — a real pre-existing bug intentionally
scoped out of this PR. They will land in a follow-up alongside their
measured numbers.

## Known surfaces discovered during this eval

- **FTS5 punctuation escape gap** — DS-1000 prompts contain literal
  triple-quoted Python (`u"""probegenes,sample..."""`) and single-quoted
  module names (`'sklearn'`). `_build_fts_match_query` in
  `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py` wraps each
  whitespace-split token in `"..."` but does not strip embedded `"`,
  `,`, `(`, `)`, `*` from the token first — so 2 / 30 DS-1000 prompts
  raise `sqlite3.OperationalError: fts5: syntax error near ","` and
  abort the whole sweep. Affects production: any MCP `search` call
  with one of those chars in the query crashes. Fix is ~5 lines, shipped
  in a follow-up PR (the same `_build_fts_match_query` lives in
  `storage/sqlite.py` under the AC17 byte-parity rule — both copies move
  together).
- **Per-task indexing cost dominates BM25/tree wall time** — the
  shipped `ingestion.yaml` runs `embed_chunks` (FastEmbed encode)
  unconditionally. BM25 and tree retrieval never read the dense vectors,
  so the encode is pure waste. This PR ships
  `python/pydocs_mcp/pipelines/ingestion_bm25_only.yaml` (drops the
  `embed_chunks` stage) and wires the three BM25/tree configs to it.
  Empirically drops per-task indexing wall time by ~80% on the small
  repos these benchmarks use.

## Reproduce

The harness expects `OPENAI_API_KEY` on the shell for the `*_tree`
configs (LLM-only retrieval calls gpt-4o-mini per query).
`repoqa_bm25` / `ds1000_bm25` have no external dependency.
`*_hybrid_li_rrf` needs `pip install -e ".[late-interaction]"`
beforehand (pulls `pylate` + `fast-plaid` + `sentence-transformers` +
`torch`; expect ~1-5 GB).

```bash
# BM25 (fast — ~5 min on small_test)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_bm25.yaml \
  --report benchmarks/reports/small_test_eval/repoqa_bm25.md \
  --trackers jsonl

# LI (slow — ~30 min; needs nohup if you launched from a shell with a
# short idle timeout)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_hybrid_li_rrf.yaml \
  --report benchmarks/reports/small_test_eval/repoqa_hybrid_li_rrf.md \
  --trackers jsonl

# Tree (fast — ~5 min; needs OPENAI_API_KEY)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --dataset repoqa --split small_test --systems pydocs-mcp \
  --configs benchmarks/configs/repoqa_tree.yaml \
  --report benchmarks/reports/small_test_eval/repoqa_tree.md \
  --trackers jsonl
```

Same shape for `ds1000`. JSONL runs land in
`benchmarks/results/jsonl/` (default `JsonlExperimentTracker.output_dir`).

## Distill JSONL → baseline JSON

`benchmarks.eval.plotting.BaselineRecord.from_path` reads a small JSON
that is NOT what `jsonl_tracker` emits — the JSONL is a per-event log,
the baseline is a per-(dataset, system, config) aggregate. Use the
companion script:

```bash
python benchmarks/reports/small_test_eval/jsonl_to_baseline.py \
  benchmarks/results/jsonl/pydocs-mcp_<config>_<dataset>_<ts>.jsonl \
  -o benchmarks/reports/small_test_eval/baselines/<config>.json \
  --label "small_test-30-tasks"
```

The script is stdlib-only (it can be run from any cwd without
`benchmarks` on the Python path) and folds the `_event: metric`
aggregate lines (`*_mean` / `*_ci_low` / `*_ci_high` / `*_p50` /
`*_p95` / `*_p99`, each emitted by the runner with `step=None`) into
the `metrics: {<base>: {<agg>: value}}` shape `BaselineRecord` expects.

## Plots

Once the baseline JSONs land, render via the shipped plotting CLI:

```bash
python -m benchmarks.eval.plotting \
  benchmarks/reports/small_test_eval/baselines/repoqa_*.json \
  --output benchmarks/reports/small_test_eval/repoqa_quality.png

python -m benchmarks.eval.plotting \
  benchmarks/reports/small_test_eval/baselines/repoqa_*.json --timings \
  --output benchmarks/reports/small_test_eval/repoqa_timing.png

python -m benchmarks.eval.plotting \
  benchmarks/reports/small_test_eval/baselines/repoqa_*.json --scatter \
  --scatter-latency indexing_seconds \
  --output benchmarks/reports/small_test_eval/repoqa_quality_vs_cost.png
```

Repeat for `ds1000_*.json`.

## Status

| Sweep | Status |
|---|---|
| `repoqa_bm25` | in flight |
| `repoqa_hybrid_li_rrf` | deferred (needs `[late-interaction]` extra) |
| `repoqa_tree` | deferred (needs `OPENAI_API_KEY` in env) |
| `ds1000_bm25` | blocked on FTS5 escape bug (see above) |
| `ds1000_hybrid_li_rrf` | deferred (needs `[late-interaction]` extra) |
| `ds1000_tree` | deferred (needs `OPENAI_API_KEY` in env) |
