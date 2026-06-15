# RepoQA retrieval experiments

A turnkey suite for comparing pydocs-mcp's retrieval strategies on a small,
representative slice of **RepoQA-SNF** (Apache-2.0, EvalPlus, arXiv:2406.06025).

Twelve core conditions (plus a 7-condition fusion-ablation extension below) are
compared on the same `small_test` split so the only thing that varies between
runs is the retrieval pipeline:

| # | Condition | Config overlay | Pipeline | Extra dep |
|---|-----------|----------------|----------|:---:|
| 1 | BM25 only | `configs/repoqa_bm25.yaml` | `exp_bm25` | — |
| 2 | Dense only (bge-small) | `configs/repoqa_dense.yaml` | `exp_dense` | — |
| 3 | Hybrid, RRF `k=30` | `configs/repoqa_hybrid_rrf_k30.yaml` | `exp_hybrid_rrf_k30` | — |
| 4 | Hybrid, RRF `k=60` (default) | `configs/repoqa_hybrid_rrf_k60.yaml` | `exp_hybrid_rrf_k60` | — |
| 5 | Hybrid, RRF `k=100` | `configs/repoqa_hybrid_rrf_k100.yaml` | `exp_hybrid_rrf_k100` | — |
| 6 | Hybrid, weighted 0.7/0.3 (BM25-heavy) | `configs/repoqa_hybrid_wsi_bm25.yaml` | `exp_hybrid_wsi_bm25` | — |
| 7 | Hybrid, weighted 0.5/0.5 (balanced) | `configs/repoqa_hybrid_wsi_balanced.yaml` | `exp_hybrid_wsi_balanced` | — |
| 8 | Hybrid, weighted 0.3/0.7 (dense-heavy) | `configs/repoqa_hybrid_wsi_dense.yaml` | `exp_hybrid_wsi_dense` | — |
| 9 | Hybrid + late-interaction (ColBERT/MaxSim), RRF | `configs/repoqa_hybrid_li_rrf.yaml` | `exp_hybrid_li_rrf` | `[late-interaction]` |
| 10 | Hybrid + late-interaction, weighted 0.5/0.5 | `configs/repoqa_hybrid_li_wsi.yaml` | `exp_hybrid_li_wsi` | `[late-interaction]` |
| 11 | LLM tree reasoning (vectorless) | `configs/repoqa_tree.yaml` | `exp_tree` | `OPENAI_API_KEY` |
| 12 | Hybrid + LLM tree rerank (top-10) | `configs/repoqa_hybrid_tree.yaml` | `exp_hybrid_tree` | `OPENAI_API_KEY` |

Conditions 3–5 vary the RRF rank-bias constant `k`; conditions 6–8 vary the
BM25/dense weight split of the linear (weighted-score-interpolation) blend.
Conditions 9–10 swap the single-vector dense branch for token-level
late-interaction (ColBERT/MaxSim) re-ranking, fused with BM25 by RRF (9) or by
the same 0.5/0.5 weighted blend (10) — a 9-vs-10 delta isolates the fusion
method, while 9-vs-4 (RRF, both) and 10-vs-7 (weighted 0.5/0.5, both) each
isolate the late-interaction branch against its single-vector dense twin. Every
experiment pipeline emits a ranked top-10 candidate list (`max_results: 10`) so
`recall@10` is measurable — the shipped `*_ranked` presets cap at 8.

### Fusion ablation — finer sweep + Qwen3 dense (conditions 13–19)

A config-only extension of the hybrid sweep: finer fusion sampling, and the
stronger Qwen3 dense branch fused with BM25. No new pipeline machinery.

| # | Condition | Config overlay | Pipeline | Extra dep |
|---|-----------|----------------|----------|:---:|
| 13 | Hybrid, RRF `k=45` (finer) | `configs/repoqa_hybrid_rrf_k45.yaml` | `exp_hybrid_rrf_k45` | — |
| 14 | Hybrid, RRF `k=75` (finer) | `configs/repoqa_hybrid_rrf_k75.yaml` | `exp_hybrid_rrf_k75` | — |
| 15 | Hybrid, weighted 0.6/0.4 (mild BM25) | `configs/repoqa_hybrid_wsi_bm25_mild.yaml` | `exp_hybrid_wsi_bm25_mild` | — |
| 16 | Hybrid, weighted 0.4/0.6 (mild dense) | `configs/repoqa_hybrid_wsi_dense_mild.yaml` | `exp_hybrid_wsi_dense_mild` | — |
| 17 | Hybrid + **Qwen3** dense, RRF `k=60` | `configs/repoqa_hybrid_rrf_k60_qwen.yaml` | `exp_hybrid_rrf_k60` | `[sentence-transformers]` |
| 18 | Hybrid + **Qwen3** dense, weighted 0.5/0.5 | `configs/repoqa_hybrid_wsi_balanced_qwen.yaml` | `exp_hybrid_wsi_balanced` | `[sentence-transformers]` |
| 19 | Hybrid + **Qwen3** dense, weighted 0.3/0.7 | `configs/repoqa_hybrid_wsi_dense_qwen.yaml` | `exp_hybrid_wsi_dense` | `[sentence-transformers]` |

Conditions 13–14 fill in the RRF `k` curve between the coarse 30/60/100 samples;
15–16 fill in the weight curve between 0.7/0.3, 0.5/0.5, and 0.3/0.7. Conditions
17–19 **reuse the existing hybrid pipelines** (4, 7, 8) but swap the dense
branch's embedder to `Qwen/Qwen3-Embedding-0.6B` via an `embedding:` overlay
block — so 17-vs-4, 18-vs-7, and 19-vs-8 each isolate the dense-embedder quality
effect (bge-small vs Qwen3) inside hybrid fusion. The Qwen3 conditions need the
`[sentence-transformers]` extra (torch) and build their own index (the embedder
is folded into the index-cache hash).

### Late-interaction model variants (conditions 9–10)

Both late-interaction conditions default to `lightonai/LateOn-Code`. The project
ships a second multi-vector model — swap it by adding a `model_name:` (and its
matching dims) under `late_interaction:` in the overlay, no pipeline change:

| Model | `embedding_dim` | `document_length` | `query_length` | Trade-off |
|-------|:---:|:---:|:---:|-----------|
| `lightonai/LateOn-Code` (default) | 128 | 180 | 32 | Higher per-token fidelity; larger fast-plaid index + slower MaxSim. The quality reference. |
| `lightonai/LateOn-Code-edge` | 48 | 2048 | 256 | ~2.7× smaller token vectors (48 vs 128) → smaller index + faster MaxSim, and a far longer context window (2048/256 vs 180/32) so large chunks aren't truncated. The speed / long-context proposition. |

```yaml
# overlay snippet to run conditions 9–10 with the edge model
late_interaction:
  enabled: true
  model_name: lightonai/LateOn-Code-edge
  embedding_dim: 48
  document_length: 2048
  query_length: 256
```

Because the index-cache key folds the ingestion-pipeline hash (which includes
`LateInteractionConfig`), switching models triggers a one-time re-index — the
two models never share cached vectors.

The harness persists the dense `.tq` sidecar at index time, so the dense and
hybrid conditions (2–8) measure real dense retrieval — not a lexical fallback.

The harness caches each index on disk and reuses it across conditions and
re-runs, so a full sweep indexes the 30 repos once rather than once per
condition — see [§4 The index cache](#4-the-index-cache-fast-re-runs).

The experiment pipelines live in `configs/pipelines/` and are resolved
relative to each overlay (the `pipeline_path` search-path prefers a file next
to the config before the shipped `pydocs_mcp/pipelines/` dir), so they stay
out of the installed package.

> **Embedder note.** The single-vector dense and hybrid conditions (2–8) use the
> default single-vector embedder (`embedding:` → FastEmbed `bge-small`). The
> late-interaction conditions (9–10) use a multi-vector ColBERT model
> (`late_interaction.model_name`, default `lightonai/LateOn-Code`; the edge
> variant `lightonai/LateOn-Code-edge` is the swap above) via PyLate +
> fast-plaid (configured via the `late_interaction` config section).

## 1. Prerequisites

```bash
# From the repo root, in a Python 3.11+ virtualenv.

# pydocs-mcp (FastEmbed, the default dense embedder, is a core dependency) +
# the benchmark harness extras.
pip install -e .
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
  cp .env.example .env          # then edit .env (gitignored) with your key
  set -a; source .env; set +a   # export OPENAI_API_KEY for the tree runs
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

### Late-interaction conditions (need the `[late-interaction]` extra)

```bash
pip install -e ".[late-interaction]"   # pylate + fast-plaid + torch (~1-5 GB)

PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner \
  --systems pydocs-mcp \
  --dataset repoqa \
  --split small_test \
  --configs \
benchmarks/configs/repoqa_hybrid_li_rrf.yaml,\
benchmarks/configs/repoqa_hybrid_li_wsi.yaml \
  --report benchmarks/results/repoqa_smalltest_li.md
```

Add `--gpu` to any runner command to move embedder inference (FastEmbed /
sentence_transformers / PyLate) onto CUDA — no YAML change, no re-index. Needs the matching GPU runtime
(see INSTALL.md §"GPU inference").

To run the same two conditions with the edge model, point the overlays at
`lightonai/LateOn-Code-edge` (see the model-variants table in §"RepoQA retrieval
experiments") — switching models triggers a one-time re-index.

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

## 4. The index cache (fast re-runs)

Indexing dominates wall time — on these small repos a single needle takes
tens of seconds to several minutes to index, while search is sub-second. By
default the harness indexes each `(corpus, ingestion-config)` **once** and
reuses it across needles and across sweeps that share an ingestion pipeline,
so the first run pays the indexing cost and every later run (tuning a config,
re-plotting, debugging) is near-instant. The cache lives at
`~/.pydocs-mcp/bench/`, outside the repo.

Because every condition here uses the same default ingestion pipeline, the
first condition you run indexes all 30 needles; the other nine reuse those
cached indexes — so a full ten-condition sweep indexes the 30 repos once, not
ten times.

```bash
# inspect / clear the cache
python -m benchmarks.eval.bench_cache_cli info
python -m benchmarks.eval.bench_cache_cli evict

# run all conditions, then free the disk when the sweep finishes
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --bench-cache-cleanup ...

# reproduce pre-cache numbers exactly (a fresh index per needle)
PYTHONPATH=benchmarks/src python -m benchmarks.eval.runner --bench-cache off ...
```

The cache key folds the ingestion-pipeline hash, so changing the embedder or
the ingestion YAML rebuilds automatically; a change to a corpus's *contents*
under the same path is NOT auto-detected — `bench_cache_cli evict` (or
`--bench-cache off`) after editing a corpus in place.

> **Reading indexing time.** A cache hit makes indexing a ~0 s lookup, so
> warm needles record NO `indexing_seconds` (the metric would otherwise read
> "0.0 s"). Take true indexing-time numbers from a **cold** run — the first
> sweep after `bench_cache_cli evict`, or any `--bench-cache off` run. Warm
> sweeps still report correct quality and `search_seconds`.

## 5. Results → plots

Each `(system, config)` leg writes one JSONL file under
`benchmarks/results/jsonl/` (named `pydocs-mcp_<config>_repoqa@<rev>_<ts>.jsonl`)
with per-task metric/latency events and final `*_mean` / `*_ci_low` /
`*_ci_high` aggregates, plus the markdown report at the `--report` path.
`benchmarks/results/` is gitignored.

To get the comparison plots committed: run the sweeps above, then attach the
contents of `benchmarks/results/jsonl/` (and the markdown reports) back here —
the plotting + committing of the figures is handled from those result files.
