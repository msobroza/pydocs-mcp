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

## Metrics

**Recall@k** — proportion of ground-truth relevant chunks found in the top-k results.

**MRR@k** (Mean Reciprocal Rank) — inverse of the rank position of the first relevant result in top-k (0 if no relevant result found). Averaged across all queries.

For pyctx7-mcp, ground truth is the source chunk from which each question was derived (`relevant_chunk_ids` in the dataset). For Context7, relevance is approximated by checking if the expected answer snippet appears in the returned documentation text.

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
