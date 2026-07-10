# pydocs-mcp retrieval methods — RepoQA single-repo notebooks

A small, self-contained set of notebooks that ingest **one real RepoQA repository**
and run its **example queries** through each *single-stage* retrieval method, so you
can see and compare what each method retrieves.

The repo (`marshmallow`) and its 10 example queries are **committed** here
(`sample_repo/` + `queries.json`), so every run uses the exact same example — no
HuggingFace download, fully reproducible.

## What each notebook shows

| Notebook | Method | How it ranks | Extra needed |
|----------|--------|--------------|--------------|
| [`00_setup.ipynb`](00_setup.ipynb) | — | Overview, prerequisites, the sample + queries | — |
| [`01_bm25.ipynb`](01_bm25.ipynb) | **BM25** keyword (SQLite FTS5) | weighted term overlap | none |
| [`02_dense_bge.ipynb`](02_dense_bge.ipynb) | **Dense** (bge-small, 384-d) | embedding cosine similarity | none |
| [`03_dense_qwen3.ipynb`](03_dense_qwen3.ipynb) | **Dense** (Qwen3-0.6B, 1024-d) | embedding cosine similarity | `[sentence-transformers]` |
| [`04_late_interaction.ipynb`](04_late_interaction.ipynb) | **Late interaction** (ColBERT/MaxSim) | per-token MaxSim | `[late-interaction]` |
| [`05_tree.ipynb`](05_tree.ipynb) | **LLM tree reasoning** (vectorless) | LLM picks tree nodes | `OPENAI_API_KEY` |
| [`06_dense_modernbert.ipynb`](06_dense_modernbert.ipynb) | **Dense** (gte-modernbert-base, 768-d) | embedding cosine similarity | `[sentence-transformers]` |
| [`07_dense_f2llm.ipynb`](07_dense_f2llm.ipynb) | **Dense** (F2LLM-v2-0.6B, code) | embedding cosine similarity | `[sentence-transformers]` |

> *Single-stage* = one retrieval method on its own (no hybrid/fusion). Note late
> interaction is technically a candidate-gen → MaxSim re-rank; it's the canonical
> way to use LI and is included as "the LI method".

## Design (how it's wired)

- **Indexing uses the CLI** (`python -m pydocs_mcp index`) — it does the full
  ingestion wiring. Each notebook indexes `sample_repo/` into its **own**
  `.pydocs-cache/<method>/` so the per-embedder indexes never clobber each other.
- **Searching uses the Python retrieval pipeline directly** — `nb_helpers.make_searcher()`
  builds the same `RetrieverPipeline` the CLI/MCP server use and runs each query,
  returning ranked hits (rank, score, `qualified_name`). We don't shell out to
  `pydocs-mcp search`, so we get the scored ranked list instead of merged markdown.
- `nb_helpers.show_results()` prints the NL query, the gold function, and whether/
  where it appears in the ranked hits.

## Prerequisites

- **Kernel:** select **`pydocs-mcp (.venv-li)`** — it has pydocs-mcp + every extra below.
- Or build your own environment from the repo root:
  ```bash
  pip install -e '.[late-interaction,sentence-transformers]'
  pip install jupyterlab        # plus: python -m ipykernel install --user --name <kernel>
  ```
- **Tree notebook only:** an OpenAI key. Put `OPENAI_API_KEY=...` in the repo-root
  `.env` (the notebook loads it via `nb_helpers.load_dotenv('../.env')`).

## Run order

Start with `00_setup.ipynb`, then run `01`–`07` in order. Each method notebook is
independent: it indexes the sample, then searches. Re-running an index cell is cheap
for this small repo (BM25/dense/tree are quick; Qwen3 and late-interaction are slower
because they build a heavier index).

## Files

```
notebooks/
  00_setup.ipynb … 07_dense_f2llm.ipynb   # the walkthrough
  nb_helpers.py                    # build-the-pipeline-in-Python search helpers
  configs/                         # one AppConfig overlay per method
  sample_repo/                     # committed marshmallow source (the corpus)
  queries.json                     # 10 RepoQA needles (NL query + gold function)
  .pydocs-cache/                   # per-method indexes (gitignored; created on run)
```
