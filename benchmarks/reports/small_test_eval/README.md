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

- **FTS5 punctuation escape gap (fixed in this PR, commit `55abe8b`)** —
  DS-1000 prompts contain literal triple-quoted Python
  (`u"""probegenes,sample..."""`) and single-quoted module names
  (`'sklearn'`). Pre-fix `_build_fts_match_query` wrapped each
  whitespace-split token in `"..."` but stripped nothing first, so 2 /
  30 DS-1000 prompts raised `sqlite3.OperationalError: fts5: syntax
  error near ","` and aborted the sweep. Affects production: any MCP
  `search` call with one of those chars in the query crashed. The fix
  is a positive regex filter (`[^\w.\-]+` → space) applied per token
  before the phrase wrap — word chars + dots + hyphens survive
  (preserves BM25 ranking on `pd.DataFrame` / `multi-index`); FTS5
  specials collapse to whitespace. Lands in both
  `chunk_fetcher.py` and `storage/sqlite.py` per the AC17 byte-parity
  rule, with 11 new regression tests at
  `tests/retrieval/steps/test_fts5_escape.py`.
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

## DS-1000 setup gate

All DS-1000 sweeps need `benchmarks/fixtures/ds1000_reference_project`
populated. The committed copy is a stub (a 5-line `__init__.py`); the
real corpus comes from installing its `pyproject.toml`, which pins the
pandas / numpy / scikit-learn / torch / tensorflow versions DS-1000 was
authored against:

```bash
cd benchmarks/fixtures/ds1000_reference_project
python -m venv .venv
.venv/bin/pip install -e .
```

The materialized `site-packages/` is then what `pydocs-mcp` indexes
when the runner is called with `--corpus-dir
benchmarks/fixtures/ds1000_reference_project`. Without that flag, every
DS-1000 task indexes the stub project and every metric is 0.

## Status

| Sweep | Status | Blocker |
|---|---|---|
| `repoqa_bm25` | in flight | — |
| `repoqa_hybrid_li_rrf` | in flight | — |
| `repoqa_tree` | deferred | needs `OPENAI_API_KEY` |
| `ds1000_bm25` | deferred | reference project setup (FTS5 bug fixed) |
| `ds1000_hybrid_li_rrf` | deferred | reference project setup (FTS5 bug fixed) |
| `ds1000_tree` | deferred | (1) reference project setup, (2) `OPENAI_API_KEY` |
