# pyctx7-mcp Benchmark Suite

Compares **pyctx7-mcp** (local indexing + FTS5 search) against **Context7**
(cloud MCP API) and **Neuledge Context** (local MCP server) on search
latency and retrieval quality.

## Structure

```
benchmarks/
├── fake_project/          Static Python project used as indexing target
├── benchmarks/
│   ├── fake_project.py    Generates the fake project tree
│   ├── indexer_bench.py   Times per-package indexing
│   ├── dataset_gen.py     Synthesizes questions from indexed chunks
│   ├── search_bench.py    pyctx7 search (token-budget concat + fuzzy recall)
│   ├── context7_client.py Async HTTP client for Context7 MCP API
│   ├── context7_bench.py  Context7 benchmark (resolve + query-docs)
│   ├── neuledge_client.py Async HTTP client for Neuledge Context MCP
│   ├── neuledge_bench.py  Neuledge benchmark (get_docs)
│   └── runner.py          Main CLI entrypoint
├── data/results/          Output CSVs
└── data/checkpoints/      Saved results for offline re-use
```

## Setup

Requires Python 3.10+ and the parent `pydocs-mcp` package.

```bash
cd benchmarks
pip install -e .          # installs pydocs-mcp from parent + benchmark deps
```

## Output Format

`benchmark_results.csv` — one row per query per system:

| Column | Type | Description |
|--------|------|-------------|
| `question` | str | Natural language question derived from chunk heading |
| `package` | str | Package name (e.g., `numpy`, `pandas`, `requests`) |
| `source` | str | System: `pyctx7`, `neuledge`, or `context7` |
| `elapsed_s` | float | Wall-clock latency in seconds |
| `recall` | float | Binary: `1.0` if relevant content found, `0.0` otherwise |

## Methodology

All three systems are evaluated with the **same methodology**:

1. **Token-budget response.** Each system concatenates search results within a ~2000-token budget into a single text blob.
2. **Fuzzy relevance scoring.** `rapidfuzz.fuzz.partial_ratio` (longest common substring) checks if the chunk heading or expected snippet appears in the response. Threshold >= 60.
3. **Binary recall.** Each query scores 1.0 (match found) or 0.0 (no match). Mean recall = fraction of queries with a relevant match.
4. **Latency.** Wall-clock time from query to response (excludes relevance scoring).

### How Each System Is Queried

| System | Parameters used | What gets passed |
|--------|----------------|-----------------|
| **pyctx7** | `query`, `pkg`, `topic`, `internal` | Heading terms + LIKE filter + package scope |
| **Neuledge** | `library`, `topic` | Package@version + heading as topic |
| **Context7** | `libraryName`, `query` | Package name + natural language question |

## Benchmark Results (20 queries each)

Benchmark run against packages: requests, pandas, numpy.

> **Note:** Context7 results are from a prior run (free API quota: 1,000 requests/month). pyctx7 and Neuledge results are current.

### Summary

| Metric | pyctx7-mcp | Neuledge Context | Context7 |
|--------|-----------|-----------------|----------|
| **Recall** | **1.000** | 0.650 | 0.550 |
| **Latency (mean)** | **2.3 ms** | 4.5 ms | 1,321 ms |
| **Type** | Local (Python/Rust) | Local (Node.js) | Cloud API |
| **Corpus** | Installed packages | GitHub repo docs | Curated cloud docs |
| **Token budget** | ~2000 tokens | ~2000 tokens | ~5000 tokens |

### Indexing Time (pyctx7-mcp only)

pyctx7-mcp indexes locally. Context7 and Neuledge have no indexing step at query time.

| Target | Time (s) | Chunks | Symbols |
|--------|----------|--------|---------|
| `__project__` | 0.004 | 8 | 5 |
| `requests` | 0.061 | 72 | 99 |
| `pandas` | 0.338 | 3,788 | 8,768 |
| `numpy` | 0.137 | 1,941 | 2,814 |

### Why pyctx7-mcp Achieves 100% Recall

- **Indexes what you have installed.** pyctx7 searches the exact packages in your virtualenv — no corpus mismatch between ground truth and search index.
- **Token-budget concatenation.** Top FTS5 results are concatenated within ~2000 tokens. The topic LIKE filter ensures the right chunks are included.
- **Same fuzzy scoring.** All three systems use identical `rapidfuzz.partial_ratio` evaluation.

### Why Neuledge Recall Is 65%

1. **Different corpus.** Neuledge indexes GitHub repo docs (markdown), while ground truth comes from pyctx7's locally-indexed source code + docstrings. Some internal module names don't appear in GitHub docs.
2. **`search_topic` helps.** Passing the heading as topic (e.g., `"numpy.lib._datasource"`) instead of natural language improved recall from 20% to 65%.
3. **~2000-token budget.** Neuledge internally caps results with a 50% relevance drop filter.

### Why Context7 Recall Is 55%

1. **Some queries fail.** Context7 cannot resolve internal submodules (e.g., `pandas.core._numba.kernels`), returning errors for ~45% of queries.
2. **Different corpus.** Context7's curated cloud docs may use different headings than locally-indexed packages.

## Rust vs Pure-Python Performance

pyctx7-mcp optionally uses Rust (PyO3) for file walking, hashing, text chunking, and parallel reads. Use `--no-rust` to force the pure-Python fallback.

| Target | Rust (s) | Python (s) | Speedup |
|--------|----------|------------|---------|
| `requests` | 0.053 | 0.030 | ~1x |
| `pandas` | 0.269 | 0.265 | ~1x |
| `numpy` | 0.124 | 0.127 | ~1x |

For this benchmark (3 small packages, ~5,800 chunks), Rust and Python perform identically. Rust acceleration helps at scale (100+ packages, 50k+ files) via `walk_py_files` (walkdir ~10x), `read_files_parallel` (rayon with GIL release), and `hash_files` (xxh3 ~3x).

## Steps to Reproduce

```bash
# 1. Install
git clone https://github.com/msobroza/pydocs-mcp.git
cd pydocs-mcp/benchmarks
pip install -e .
pip install requests pandas numpy

# 2. pyctx7 only (no external services)
run-benchmarks --questions 20 --skip-context7 --skip-neuledge

# 3. With Context7 (requires network + API quota)
run-benchmarks --questions 20 --skip-neuledge

# 4. With Neuledge (requires local server)
npm install -g @neuledge/context
context add https://github.com/psf/requests --name requests --tag v2.32.3 --path docs
context add https://github.com/pandas-dev/pandas --name pandas --tag v2.2.2 --path doc
context add https://github.com/numpy/numpy --name numpy --tag v2.0.0 --path doc
context serve --http 8080 &
run-benchmarks --questions 20 --skip-context7

# 5. All three
context serve --http 8080 &
run-benchmarks --questions 20

# 6. Load from checkpoints (no services needed)
run-benchmarks --load-context7 data/checkpoints/context7.csv \
               --load-neuledge data/checkpoints/neuledge.csv

# 7. Rust vs Python
run-benchmarks --questions 20 --skip-context7 --skip-neuledge           # Rust
run-benchmarks --questions 20 --skip-context7 --skip-neuledge --no-rust  # Python

# 8. Results
cat data/results/benchmark_results.csv
```

## Running Tests

```bash
cd benchmarks
pip install pytest pytest-asyncio
pytest tests/ -v
```
