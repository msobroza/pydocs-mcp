# pyctx7-mcp Benchmark Suite

Compares **pyctx7-mcp** (local indexing + FTS5 search) against **Context7**
(cloud MCP API) on indexing speed, search latency, and retrieval quality.

## Structure

```
benchmarks/
├── fake_project/        Static Python project used as indexing target
├── benchmarks/
│   ├── fake_project.py  Generates the fake project tree
│   ├── indexer_bench.py Times per-package indexing
│   ├── dataset_gen.py   Synthesizes questions from indexed chunks (with ground truth)
│   ├── search_bench.py  Times pyctx7 search + computes Recall@k and MRR@k
│   ├── context7_client.py  Async HTTP client for Context7 MCP API
│   ├── context7_bench.py   Times Context7 resolve + get-library-docs
│   ├── charts.py        Generates PNG charts
│   └── runner.py        Main CLI entrypoint
└── data/                Output CSV and PNGs (gitignored)
```

## Setup

Requires Python 3.10+ and the parent `pydocs-mcp` package.

```bash
cd benchmarks
pip install -e .          # installs pydocs-mcp from parent + benchmark deps
```

## Running

```bash
# Full benchmark (includes live Context7 API calls)
run-benchmarks

# Local only — no network, faster
run-benchmarks --skip-context7

# Fewer questions for a quick smoke test
run-benchmarks --questions 10 --skip-context7

# Custom output directory
run-benchmarks --out /tmp/bench_results
```

## Output

| File | Description |
|------|-------------|
| `data/results/benchmark_results.csv` | Primary DataFrame: all queries with per-k metrics |
| `data/results/indexing_results.csv` | Per-package indexing timings |
| `data/results/indexing_times.png` | Bar chart: indexing time per package |
| `data/results/search_latency_boxplot.png` | Box plot: pyctx7 vs Context7 latency distribution |
| `data/results/recall_at_k.png` | Line plot: mean Recall@k vs k, pyctx7 vs Context7 |
| `data/results/mrr_at_k.png` | Line plot: mean MRR@k vs k, pyctx7 vs Context7 |

## DataFrame Schema

`benchmark_results.csv` columns:

| Column | Type | Description |
|--------|------|-------------|
| `question` | str | Synthetic question derived from a doc chunk |
| `package` | str | Package the question was drawn from |
| `elapsed_s` | float | Wall-clock search time in seconds |
| `n_results` | int | Number of results returned |
| `source` | str | `pyctx7` or `context7` |
| `recall_at_1` | float | Recall@1 — fraction of relevant chunks in top-1 |
| `recall_at_3` | float | Recall@3 |
| `recall_at_5` | float | Recall@5 |
| `recall_at_10` | float | Recall@10 |
| `recall_at_20` | float | Recall@20 |
| `mrr_at_1` | float | MRR@1 — 1/rank of first relevant result in top-1 (0 if none) |
| `mrr_at_3` | float | MRR@3 |
| `mrr_at_5` | float | MRR@5 |
| `mrr_at_10` | float | MRR@10 |
| `mrr_at_20` | float | MRR@20 |

## How the Synthetic Dataset Is Built

The dataset is built by `dataset_gen.py` via `generate_dataset(db_path, n_questions, seed)`:

1. Opens the pydocs-mcp SQLite DB (already populated by indexing the fake project + its deps)
2. Randomly samples chunks from the `chunks` table (fetches `rowid`, `pkg`, `heading`, `body`, `kind`)
3. Deduplicates by heading
4. For each sampled chunk, derives a natural-language question from the heading using one of 7 templates (e.g. `"How do I use {heading}?"`, `"What does {heading} do?"`)
5. Extracts the first sentence of the chunk body as `expected_answer_snippet`
6. Records the chunk's SQLite `rowid` as `relevant_chunk_ids` — this is the ground truth for Recall@k/MRR@k

### Dataset columns

| Column | Type | Example |
|--------|------|---------|
| `question` | str | `"How do I use requests.get?"` |
| `package` | str | `"requests"` |
| `source_chunk_heading` | str | `"requests.get"` |
| `expected_answer_snippet` | str | `"Send HTTP GET request."` |
| `chunk_kind` | str | `"doc"`, `"project_code"`, `"readme"` |
| `chunk_body_preview` | str | First 200 chars of body |
| `relevant_chunk_ids` | list[int] | `[42]` — rowid(s) used as ground truth |

The `relevant_chunk_ids` column is key — it's what allows `search_bench.py` to compute proper Recall@k and MRR@k by matching search result rowids against these ground-truth IDs.

### Pipeline flow

```
fake project generated → indexed into SQLite → generate_dataset() samples chunks →
dataset fed to run_search_benchmark() (pyctx7) and run_context7_benchmark() (Context7) →
results flattened via to_dataframe() → saved as benchmark_results.csv
```

## Metrics

**Recall@k** — proportion of ground-truth relevant chunks found in the top-k results.

**MRR@k** (Mean Reciprocal Rank) — inverse of the rank position of the first relevant result in top-k (0 if no relevant result found). Averaged across all queries.

Both metrics are evaluated for k ∈ [1, 3, 5, 10, 20].

For pyctx7-mcp, ground truth is the source chunk from which each question was derived (`relevant_chunk_ids` in the dataset). For Context7, relevance is approximated by checking if the expected answer snippet appears in the returned documentation text.

## Benchmark Results (pyctx7-mcp vs Context7, 20 queries)

Benchmark run against packages: requests, pandas, numpy. Context7 accessed via live API.

### Indexing Time Per Package (pyctx7-mcp)

pyctx7-mcp indexes locally — Context7 has no indexing step (cloud API).

| Target | Time (s) | Chunks | Symbols |
|--------|----------|--------|---------|
| `__project__` | 0.001 | 8 | 5 |
| `requests` | 0.063 | 72 | 99 |
| `pandas` | 0.566 | 3,787 | 8,777 |
| `numpy` | 0.252 | 1,941 | 2,830 |

![Indexing time per package](docs/images/indexing_times.png)

### Search Latency Comparison

| Metric | pyctx7-mcp | Context7 |
|--------|-----------|----------|
| **Mean** | 4.62 ms | 102.34 ms |
| **Median** | 4.01 ms | 87.81 ms |
| **Speedup** | **~22x faster** | baseline |

pyctx7-mcp is **~22x faster** than Context7 on median search latency. This is expected — pyctx7 queries a local SQLite FTS5 index, while Context7 makes HTTP round-trips to a cloud API.

![Search latency boxplot](docs/images/search_latency_boxplot.png)

### Retrieval Quality Comparison

| k | pyctx7 Recall@k | Context7 Recall@k | pyctx7 MRR@k | Context7 MRR@k |
|---|----------------|-------------------|--------------|----------------|
| 1 | 0.100 | 0.000 | 0.100 | 0.000 |
| 3 | 0.200 | 0.000 | 0.133 | 0.000 |
| 5 | 0.200 | 0.000 | 0.133 | 0.000 |
| 10 | 0.250 | 0.000 | 0.139 | 0.000 |
| 20 | 0.250 | 0.000 | 0.139 | 0.000 |

![Recall@k](docs/images/recall_at_k.png)

![MRR@k](docs/images/mrr_at_k.png)

### Analysis

- **pyctx7-mcp wins on latency** — local FTS5 queries complete in ~4ms vs ~100ms for Context7's cloud API.
- **pyctx7-mcp wins on recall** — it retrieves the ground-truth source chunk 10-25% of the time (depending on k). Context7 scores 0% because its text-overlap relevance check is stricter (the exact snippet from our local DB doesn't appear verbatim in Context7's curated docs).
- **Context7's 0% is a measurement artifact**, not a real quality failure. Context7 returns curated, high-quality documentation, but our relevance scoring checks for exact substring matches from the pydocs-mcp chunk body — which is a different text source. A fair comparison would require human annotation or LLM-based relevance judgments.
- **pyctx7-mcp's recall is modest** because synthetic questions are derived from chunk headings with template transforms, while FTS5 BM25 matches on body text. Real-world queries would likely score higher for both systems.

## Context7 API

Context7 is accessed at `https://mcp.context7.com/mcp` using the
`resolve-library-id` and `get-library-docs` MCP tools. No API key required.
Network latency will dominate Context7 timings — run from a stable connection.

## Running Tests

```bash
cd benchmarks
pip install pytest pytest-asyncio
pytest tests/ -v
```
