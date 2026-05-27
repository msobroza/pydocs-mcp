# pydocs-mcp

**Local, version-aware code & docs search for your AI coding agent — over the
exact library versions installed on your machine. No cloud, no API keys.**

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

## Learn more

- **[DOCUMENTATION.md](DOCUMENTATION.md)** — how it works in depth: retrieval
  pipeline, reference graph, cache, configuration, database schema, and the full
  CLI reference.
- **[EXTENSIONS.md](EXTENSIONS.md)** — extend it: new vector-store backends,
  pipeline steps, and fusion strategies.
- **[benchmarks/README.md](benchmarks/README.md)** — the evaluation harness.
- **[INSTALL.md](INSTALL.md)** — installation & troubleshooting.
- **[CLAUDE.md](CLAUDE.md)** — architecture & contributor guide.

License: MIT.
