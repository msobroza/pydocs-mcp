# RepoQA retrieval experiments

A turnkey suite for comparing pydocs-mcp's retrieval strategies on a small,
representative slice of **RepoQA-SNF** (Apache-2.0, EvalPlus, arXiv:2406.06025).

Ten conditions are compared on the same `small_test` split so the only thing
that varies between runs is the retrieval pipeline:

| # | Condition | Config overlay | Pipeline | Needs `OPENAI_API_KEY` |
|---|-----------|----------------|----------|:---:|
| 1 | BM25 only | `configs/repoqa_bm25.yaml` | `exp_bm25` | no |
| 2 | Dense only (bge-small) | `configs/repoqa_dense.yaml` | `exp_dense` | no |
| 3 | Hybrid, RRF `k=30` | `configs/repoqa_hybrid_rrf_k30.yaml` | `exp_hybrid_rrf_k30` | no |
| 4 | Hybrid, RRF `k=60` (default) | `configs/repoqa_hybrid_rrf_k60.yaml` | `exp_hybrid_rrf_k60` | no |
| 5 | Hybrid, RRF `k=100` | `configs/repoqa_hybrid_rrf_k100.yaml` | `exp_hybrid_rrf_k100` | no |
| 6 | Hybrid, weighted 0.7/0.3 (BM25-heavy) | `configs/repoqa_hybrid_wsi_bm25.yaml` | `exp_hybrid_wsi_bm25` | no |
| 7 | Hybrid, weighted 0.5/0.5 (balanced) | `configs/repoqa_hybrid_wsi_balanced.yaml` | `exp_hybrid_wsi_balanced` | no |
| 8 | Hybrid, weighted 0.3/0.7 (dense-heavy) | `configs/repoqa_hybrid_wsi_dense.yaml` | `exp_hybrid_wsi_dense` | no |
| 9 | LLM tree reasoning (vectorless) | `configs/repoqa_tree.yaml` | `exp_tree` | **yes** |
| 10 | Hybrid + LLM tree rerank (top-10) | `configs/repoqa_hybrid_tree.yaml` | `exp_hybrid_tree` | **yes** |

Conditions 3–5 vary the RRF rank-bias constant `k`; conditions 6–8 vary the
BM25/dense weight split of the linear (weighted-score-interpolation) blend.
Every experiment pipeline emits a ranked top-10 candidate list (`max_results:
10`) so `recall@10` is measurable — the shipped `*_ranked` presets cap at 8.

The experiment pipelines live in `configs/pipelines/` and are resolved
relative to each overlay (the `pipeline_path` search-path prefers a file next
to the config before the shipped `pydocs_mcp/pipelines/` dir), so they stay
out of the installed package.

> **Dense model note.** The dense and hybrid conditions use the current
> default single-vector embedder (`embedding:` → FastEmbed `bge-small`).
> Late-interaction / multi-vector models such as `lightonai/LateOn-Code` are a
> separate feature — see
> `docs/superpowers/specs/2026-05-28-late-interaction-dense-retrieval-design.md`.

## 1. Prerequisites

```bash
# From the repo root, in a Python 3.11+ virtualenv.

# pydocs-mcp + the dense embedder + the benchmark harness extras.
pip install -e ".[fastembed]"
pip install -e "benchmarks[all]"   # datasets / trackers / report deps

# Linux: libopenblas is a hard requirement for the turbovec native module.
sudo apt-get install -y libopenblas-pthread-dev   # see INSTALL.md for fallbacks
```

- **Hugging Face access** is needed the first time you run any condition: the
  ingestion pipeline embeds chunks at index time, so even the BM25 condition
  downloads the FastEmbed `bge-small` model once (cached afterwards).
- **`OPENAI_API_KEY`** is needed only for conditions 9 and 10 (the tree step
  calls the LLM, default `gpt-4o-mini`, once per query). Put it in a
  gitignored `.env` and load it before those runs:

  ```bash
  echo 'OPENAI_API_KEY=sk-...' > .env       # .env is gitignored
  set -a; source .env; set +a               # export for the tree runs
  ```

- If `import turbovec` fails with `undefined symbol: cblas_sgemm` even after
  installing libopenblas, preload it for the run:
  `export LD_PRELOAD=/usr/lib/x86_64-linux-gnu/libopenblas.so.0`.

## 2. The `small_test` split

`--split small_test` selects a deterministic, stratified subsample of the
held-out `test` tail — **~30 needles** (`small_test_size`, default 30),
apportioned across repos in proportion to their `test` size (Hamilton
largest-remainder), seeded by `split_seed` (default 0). It is a strict subset
of `--split test`, small enough to run the LLM-tree conditions affordably.

To change the size or seed, edit the dataclass defaults on `RepoQADataset`
(`small_test_size`, `split_seed`) in
`benchmarks/src/benchmarks/eval/datasets/repoqa.py` — they are not CLI flags
(mirrors `dev_fraction` / `split_seed`).

## 3. Run the experiments

All commands run from the repo root. The harness module path is
`benchmarks.eval.runner` under `PYTHONPATH=benchmarks/src`.

### Core conditions (no API key) — BM25, dense, hybrid, weighted

```bash
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --split small_test \
  --configs \
benchmarks/configs/repoqa_bm25.yaml,\
benchmarks/configs/repoqa_dense.yaml,\
benchmarks/configs/repoqa_hybrid_rrf_k30.yaml,\
benchmarks/configs/repoqa_hybrid_rrf_k60.yaml,\
benchmarks/configs/repoqa_hybrid_rrf_k100.yaml,\
benchmarks/configs/repoqa_hybrid_wsi_bm25.yaml,\
benchmarks/configs/repoqa_hybrid_wsi_balanced.yaml,\
benchmarks/configs/repoqa_hybrid_wsi_dense.yaml \
  --report benchmarks/results/repoqa_smalltest_core.md
```

### Tree conditions (need `OPENAI_API_KEY`)

```bash
set -a; source .env; set +a   # load OPENAI_API_KEY

PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --split small_test \
  --configs \
benchmarks/configs/repoqa_tree.yaml,\
benchmarks/configs/repoqa_hybrid_tree.yaml \
  --report benchmarks/results/repoqa_smalltest_tree.md
```

With `OPENAI_API_KEY` exported you can also fold all ten `--configs` into a
single sweep (one combined report column per config).

The default metrics (`recall@1,recall@5,recall@10,mrr,pass@1-needle`) are
used unless you pass `--metrics`. Add `--limit N` for a quick smoke run over
the first N needles of the split.

## 4. Results → plots

Each `(system, config)` leg writes one JSONL file under
`benchmarks/results/jsonl/` (named `pydocs-mcp_<config>_repoqa@<rev>_<ts>.jsonl`)
with per-task metric/latency events and final `*_mean` / `*_ci_low` /
`*_ci_high` aggregates, plus the markdown report at the `--report` path.
`benchmarks/results/` is gitignored.

To get the comparison plots committed: run the sweeps above, then attach the
contents of `benchmarks/results/jsonl/` (and the markdown reports) back here —
the plotting + committing of the figures is handled from those result files.
