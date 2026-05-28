# pydocs-mcp

**Local, version-aware code & docs search for your AI coding agent — over the
exact library versions installed on your machine.**

![pydocs-mcp architecture overview: your project source and installed Python libraries are indexed into a SQLite database (chunks, metadata, reference graph) plus a TurboQuant .tq vector file; an AI coding assistant's query runs through keyword (BM25) and vector search fused together — with a tree-navigating mode over the code map — then a result ranker returns version-aware answers, all locally with no API keys or network upload.](assets/pydocs-mcp-overview.png)

Your AI assistant thinks you're on `requests` 2.28. You actually have 2.31. It
calls a kwarg that was renamed two versions ago, your test fails, and you lose
twenty minutes. The fix isn't a smarter prompt — it's giving the AI docs that
match **your lockfile**, not the average of every StackOverflow answer it ever
read.

pydocs-mcp indexes your project plus every installed dependency, right on your
machine, in seconds. Your agent connects over MCP and gets answers grounded in
*your* code — fully offline.

## What you get

- **Matched to your install.** Searches the exact versions sitting in your
  `site-packages`, so your agent stops inventing APIs from some older release.
- **Private & offline.** Everything runs locally — no API keys, no uploads, no
  rate limits, no per-query fees.
- **Three ways to find code.** Keyword, meaning, and LLM reasoning (see
  [How it works](#how-it-works)) — on their own or fused into one ranked answer.
- **Knows how your code connects.** Ask *"what calls this?"*, *"what does it
  call?"*, or *"what does this class inherit?"* — across your project and every
  dependency.
- **Lean, not bloated.** Minimal dependencies — **no PyTorch, no FAISS**. A small
  local ONNX embedder plus the Rust [TurboQuant](https://arxiv.org/abs/2504.19874)
  vector store ([`turbovec`](https://github.com/RyanCodrai/turbovec)), which packs
  embeddings **~16× smaller than float32** (a 1536-dim vector drops from 6,144 to
  384 bytes; a 10M-doc corpus fits in 4 GB instead of 31 GB) and benchmarks
  **faster than FAISS FastScan**. The on-disk index stays tiny and search stays
  quick.
- **Cheap to keep current.** Edit a doc and only the *changed* chunks are
  re-embedded — **partial re-ingestion**, not a full rebuild — while unchanged
  packages are skipped in under 100 ms. A Rust core does the heavy lifting.

## How it works

Three steps, all on your machine (see the diagram above):

1. **Index** — pydocs-mcp scans your project and installed deps into a local
   SQLite database (code chunks, metadata, and a graph of how everything
   references everything else) plus a compact TurboQuant `.tq` vector file for
   meaning-based search. Re-running is cheap: unchanged packages are skipped, and
   when a file *does* change, only its changed chunks are re-embedded.
2. **Search** — each query can use three complementary modes and fuse them into
   one ranked list:
   - **Keyword** — instant, exact matches for names, error strings, and
     signatures.
   - **Meaning** — dense embeddings find the right code even when your words
     differ from the docs', via a small model that runs locally.
   - **Reasoning** — for broad or structural questions, an LLM walks your code's
     map (titles + summaries, no embeddings) to pick the best spots.
3. **Answer** — results flow back to your agent through two simple tools:
   `search` (find by relevance) and `lookup` (jump to a known name, or trace its
   callers, callees, and inheritance).

The only call that ever leaves your machine is the optional reasoning mode — and
only if you turn it on with your own key.

## Quick start

```bash
pip install -e .                  # pure Python, works everywhere
# …or with the Rust core for speed:
pip install maturin && maturin develop --release
```

**Linux** needs OpenBLAS for the vector store (macOS and Windows already ship it):

```bash
sudo apt-get install -y libopenblas-pthread-dev
```

Then index your project and start the server:

```bash
pydocs-mcp serve .                            # index project + deps, serve over MCP (stdio)
pydocs-mcp search "batch inference"           # the same search, from the CLI
pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits
```

### Live re-indexing (optional)

If you edit code while you want the index to stay fresh, install the
`watch` extras and pick one of two modes — both debounce edits to
`.py`, `.md`, and `.ipynb` files into a single reindex.

```bash
pip install 'pydocs-mcp[watch]'
pydocs-mcp serve . --watch   # MCP server + watcher (for AI clients)
pydocs-mcp watch .            # watcher only (no MCP server; index stays fresh for CLI `search` / `lookup`)
```

Both modes share the same YAML tunables: debounce, file extensions, and
ignored paths live under `serve.watch.*` in your `pydocs-mcp.yaml` (see
[DOCUMENTATION.md](DOCUMENTATION.md#live-re-indexing)).

Point Claude Code, Cursor, or Continue.dev at it over stdio — copy-paste client
configs are in [DOCUMENTATION.md](DOCUMENTATION.md#mcp-client-integration), and
install troubleshooting (including the `libopenblas` fallback) is in
[INSTALL.md](INSTALL.md).

## How it compares

pydocs-mcp, **Context7**, and **Neuledge Context** all feed docs to an AI agent
over MCP, but optimize for different things. They aren't mutually exclusive — an
agent can mount all three and route by intent.

| | **pydocs-mcp** | **Context7** | **Neuledge Context** |
|---|---|---|---|
| **Deployment** | Local stdio MCP server | Hosted MCP (`mcp.context7.com`) | Local stdio MCP server |
| **Doc source** | Your installed Python deps + your own project, indexed in place | Curated community docs hosted by Upstash | Community registry (~100+ libraries), pulled then queried locally |
| **Version match** | Exactly what's in your `site-packages` — automatic | Library + version chosen in the prompt | Latest from the registry |
| **Languages** | Python | Multi-language | Multi-language (~100+ libraries) |
| **Retrieval** | Keyword (BM25) + dense embeddings + LLM tree reasoning, fused via RRF or weighted scores | Not publicly documented | BM25 over SQLite FTS5 |
| **Code-structure queries** | Reference graph — `lookup(show=callers\|callees\|inherits)` | None (doc retrieval only) | None (doc retrieval only) |
| **Indexes your code** | Yes — under the `__project__` package | No | No |
| **Privacy** | Fully offline with the default embedder — zero network calls | Queries hit Upstash; OAuth + API key | Local once packages are downloaded |
| **Dependencies** | Lean — no PyTorch, no FAISS (Rust TurboQuant store + small ONNX embedder) | Hosted service (nothing to install) | Local service |
| **Cost** | **$0** — OSS (MIT); no keys, limits, or fees | Free tier (rate-limited) + paid plans | **$0** — OSS (Apache-2.0) |

**In short:** choose pydocs-mcp for offline, version-matched Python retrieval
where you also navigate code structure; Context7 for hosted, multi-language docs;
Neuledge for a local-first multi-language registry.

## Benchmarked, not hand-waved

pydocs-mcp ships a real benchmark harness that scores retrieval quality on public
benchmarks (RepoQA, DS-1000) and head-to-head against Context7 and Neuledge —
with confidence intervals and plots. See
[benchmarks/README.md](benchmarks/README.md).

## Retrieval methods & R&D

Each method below is a named step under
[`python/pydocs_mcp/retrieval/steps/`](python/pydocs_mcp/retrieval/steps/),
addressable from YAML. The default `chunk_search.yaml` composes BM25 +
single-vector dense fused via RRF; everything else is opt-in via a
preset swap (`--config`), with no behavioral change for default installs.

### Keyword — BM25 over SQLite FTS5

Full-text search with porter stemming and the unicode61 tokenizer.
Free, instant, and the baseline that every other method composes with
through the fusion steps below.

### Single-vector dense — FastEmbed + TurboQuant

- **Embedder.** [FastEmbed](https://github.com/qdrant/fastembed) with
  [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)
  by default — runs on CPU via ONNX, no PyTorch, no torch download.
  OpenAI `text-embedding-3-small` is the optional alternative for
  users with an API key.
- **Vector store.** [TurboQuant](https://arxiv.org/abs/2504.19874)
  ([turbovec](https://github.com/RyanCodrai/turbovec)) — Online Vector
  Quantization with near-optimal distortion. **~16× smaller than
  float32** (a 1536-dim vector drops from 6,144 to 384 bytes; a 10 M-doc
  corpus fits in 4 GB instead of 31 GB) and faster than FAISS FastScan
  at the same recall. Persists as a `.tq` sidecar next to the SQLite DB.

### Late-interaction (multi-vector / MaxSim) — opt-in

The flagship R&D backend. One vector per **token** instead of one
pooled vector per chunk; queries score via ColBERT's **MaxSim** — for
each query token, take the maximum cosine to any document token, then
sum. Higher recall on long, structurally distant queries (often the
hard cases for single-vector retrievers).

- **Method.** ColBERT late interaction
  ([Khattab & Zaharia, SIGIR 2020](https://arxiv.org/abs/2004.12832)).
- **Engine.** [PLAID](https://arxiv.org/abs/2205.09707)
  ([Santhanam et al., CIKM 2022](https://arxiv.org/abs/2205.09707))
  via [fast-plaid](https://github.com/lightonai/fast-plaid) — a
  Rust-backed IVF + residual-decompression engine. Persists as a
  per-project directory sidecar at `~/.pydocs-mcp/{slug}.plaid/`.
- **Embedder.** [PyLate](https://github.com/lightonai/pylate)
  ([arXiv:2508.03555](https://arxiv.org/abs/2508.03555)) with the
  default model
  [`lightonai/LateOn-Code`](https://huggingface.co/lightonai/LateOn-Code)
  — late-interaction trained on code.
- **SQLite + fast-plaid coupling.** A `chunk_multi_vector_ids`
  mapping table bridges SQLite's `chunk_id` to fast-plaid's
  `plaid_doc_id`. The shipped `FilterAdapter` Protocol pushes
  metadata filters down to SQLite, then the result chunk-id list is
  passed as `subset=` to fast-plaid's MaxSim search — so MaxSim is
  always bounded to the SQLite-eligible candidates and the two
  engines stay in their own id spaces.
- **Enable.** `pip install 'pydocs-mcp[late-interaction]'`, set
  `late_interaction.enabled: true` in your YAML, then point
  `--config` at the shipped
  [`chunk_search_late_interaction.yaml`](python/pydocs_mcp/pipelines/chunk_search_late_interaction.yaml)
  preset.

### Hybrid fusion

- **Reciprocal Rank Fusion (RRF)** —
  [Cormack, Clarke & Buettcher, SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf).
  Rank-only `1 / (k + rank)` with `k=60` default; the workhorse for
  combining BM25 + dense, or BM25 + late-interaction.
- **Weighted Score Interpolation (WSI)** — score-space
  `α · score_a + (1 − α) · score_b` with min-max normalization, for
  cases where the score distributions are well-calibrated and rank
  isn't enough. `α` is tunable from YAML.

### LLM tree reasoning — opt-in

A **vectorless** mode for broad, structural questions ("walk me
through the request lifecycle"). Instead of embedding text, an LLM
walks the code map — module / class titles plus short summaries —
and picks the best spots itself. Inspired by
[PageIndex (VectifyAI)](https://github.com/VectifyAI/PageIndex)'s
reasoning-over-tree-of-contents approach.

Three shipped presets under
[`python/pydocs_mcp/pipelines/`](python/pydocs_mcp/pipelines/):
`tree_only.yaml`, `chunk_search_with_tree_reasoning_parallel.yaml`
(run alongside chunk search, fuse via WSI), and
`chunk_search_with_tree_reasoning_after.yaml` (use chunk search as
the candidate pool, let the LLM re-rank). Provider / model /
temperature / max_tokens are tuned under the `llm:` section of YAML;
any OpenAI-compatible endpoint works.

### Code reference graph

Beyond embeddings, pydocs-mcp captures a **graph of how code
references code** during indexing: `CALLS`, `IMPORTS`, `INHERITS`,
and optional `MENTIONS` (backtick-quoted dotted names in markdown).
The same surface answers an AI's *"what calls this?"* / *"what does
this extend?"* questions through the `lookup(show=…)` MCP tool:

```bash
pydocs-mcp lookup requests.auth.HTTPBasicAuth --show inherits
pydocs-mcp lookup my_module.Parser.parse --show callers
```

Capture is on by default and tunable under `reference_graph:` in YAML
(toggle, kinds-to-emit, output bounds).

## Learn more

- **[DOCUMENTATION.md](DOCUMENTATION.md)** — how it works in depth: retrieval
  pipeline, reference graph, cache, configuration, database schema, and the full
  CLI reference.
- **[EXTENSIONS.md](EXTENSIONS.md)** — extend it: new vector-store backends,
  pipeline steps, and fusion strategies.
- **[benchmarks/README.md](benchmarks/README.md)** — the evaluation harness.
- **[INSTALL.md](INSTALL.md)** — installation & troubleshooting.
- **[CLAUDE.md](CLAUDE.md)** — architecture & contributor guide.

## Sources & references

**Benchmarks**
- RepoQA — *Evaluating Long Context Code Understanding* · [arXiv:2406.06025](https://arxiv.org/abs/2406.06025) (2024)
- DS-1000 — *A Natural and Reliable Benchmark for Data Science Code Generation* · [arXiv:2211.11501](https://arxiv.org/abs/2211.11501) (2023)
- CodeRAG-Bench — *Can Retrieval Augment Code Generation?* · [arXiv:2406.14497](https://arxiv.org/abs/2406.14497) (2024)

**Vectors & retrieval**
- TurboQuant — *Online Vector Quantization with Near-optimal Distortion Rate* · [arXiv:2504.19874](https://arxiv.org/abs/2504.19874) (Google Research, 2025); implemented by [`turbovec`](https://github.com/RyanCodrai/turbovec)
- [FAISS](https://github.com/facebookresearch/faiss) — the similarity-search library used as the speed/storage baseline above
- [FastEmbed](https://github.com/qdrant/fastembed) with [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) — the default on-device embedder for the **single-vector** dense mode
- [PyLate](https://github.com/lightonai/pylate) with [`lightonai/LateOn-Code`](https://huggingface.co/lightonai/LateOn-Code) — the default model for the opt-in **late-interaction (multi-vector / MaxSim)** mode · *PyLate: Flexible Training and Retrieval for Late Interaction Models* · [arXiv:2508.03555](https://arxiv.org/abs/2508.03555) (LightOn, 2025)
- ColBERT — *Efficient and Effective Passage Search via Contextualized Late Interaction over BERT* · [arXiv:2004.12832](https://arxiv.org/abs/2004.12832) (Khattab & Zaharia, SIGIR 2020) — the late-interaction architecture
- PLAID — *An Efficient Engine for Late Interaction Retrieval* · [arXiv:2205.09707](https://arxiv.org/abs/2205.09707) (Santhanam et al., CIKM 2022) — implemented by [fast-plaid](https://github.com/lightonai/fast-plaid), the engine pydocs-mcp uses for MaxSim scoring
- Reciprocal Rank Fusion — *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods* · [Cormack, Clarke & Buettcher, SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — the rank-fusion baseline (k=60)
- [PageIndex](https://github.com/VectifyAI/PageIndex) — inspiration for the LLM tree-reasoning mode

**Protocol & comparable tools**
- [Model Context Protocol](https://modelcontextprotocol.io) — the MCP standard
- [Context7](https://github.com/upstash/context7) · [Neuledge Context](https://github.com/neuledge/context)

License: MIT.
