# <img src="assets/icon.png" width="120"> pydocs-mcp

[![CI](https://github.com/msobroza/pydocs-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/msobroza/pydocs-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/pydocs-mcp.svg)](https://pypi.org/project/pydocs-mcp/)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)
[![MCP](https://img.shields.io/badge/MCP-Model_Context_Protocol-1f6feb)](https://modelcontextprotocol.io)
[![Docs](https://img.shields.io/badge/docs-pydocs--mcp-blue)](https://msobroza.github.io/pydocs-mcp/)

**Local, version-aware code & docs search for your AI coding agent — over the
exact library versions installed on your machine.**

📖 **Full documentation: [msobroza.github.io/pydocs-mcp](https://msobroza.github.io/pydocs-mcp/)** — built from [`documentation/`](documentation/).

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
  384 bytes; a 10M-doc corpus fits in ~4 GB instead of ~61 GB) and benchmarks
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
3. **Answer** — results flow back to your agent through nine task-shaped tools:
   `search_codebase` (find by relevance), `get_symbol` / `get_context` (jump to
   known names), `get_references` (trace callers, callees, inheritance, impact),
   `get_overview` (map what's indexed), `get_why` (recorded design rationale),
   plus three filesystem tools — `grep` (exact-string / regex), `glob` (find
   files by name), and `read_file` (line-numbered reads) — that search the live
   source files the indexer sees, so exact-text lookups need no extra server.
   Every response is wrapped in a consistent envelope so the agent always knows
   where it stands — see [Response conventions](#response-conventions). The
   tool surface is frozen by contract
   ([docs/tool-contracts.md](docs/tool-contracts.md)), so MCP clients stay
   stable across server retunes.

Nothing ever leaves your machine unless you opt into a remote provider — the
reasoning mode or `embedding.provider: openai` — with your own key.

## Response conventions

Every tool response — over MCP or the CLI — is wrapped in one shared envelope so
the agent never has to guess whether an answer is stale, complete, or a
dead end. Three conventions travel with every result:

- **Freshness header.** A one-line stamp — `[index: 9bfd0c7 · 2d old · 214
  packages]` — tells the agent which commit the index was built from, how old it
  is, and how much it covers. If your working tree has moved past the indexed
  commit, a `[⚠ index stale: … — run pydocs-mcp index .]` warning is appended, so
  the agent knows to re-index instead of trusting drifted results.
- **Next-step pointers.** Results carry inline, runnable suggestions for the
  obvious follow-up call (jump to a symbol, widen the scope, trace a caller),
  resolved to whichever surface asked — an MCP tool call for clients, a CLI
  invocation on the terminal.
- **Truncation ledger.** When a result is clipped to fit a token budget, a
  `[truncated: N sections — recovery pointers inline]` footer lists exactly what
  was cut and the pointer that fetches each dropped piece in full — nothing goes
  missing silently.
- **Routing suggestions.** Dead ends carry a fixed `[suggestion: …]` line with
  the escape hatch: a zero-hit `grep` points conceptual queries at
  `search_codebase`, a truncated `grep` shows how to narrow (`path=` / `glob=` /
  `head_limit=`), and zero-hit searches point back to `get_overview`. The prefix
  is deterministic, so transcripts always distinguish a server nudge from the
  agent's own routing.

All four are on by default and tunable under `output.envelope`,
`output.next_pointers`, and `output.suggestions.*` in your `pydocs-mcp.yaml`.

The tool descriptions themselves — the prose your agent reads when it picks a
tool — ship in one editable document and can be swapped per deployment
(`pydocs-mcp serve . --descriptions my-descriptions.md`); every run logs a hash
of the description surface it actually served. See
[docs/description-authoring.md](docs/description-authoring.md) for the format,
validation rules, and override precedence.

## Quick start

```bash
pip install pydocs-mcp            # from PyPI — the usual path
```

Prebuilt wheels bundle the Rust acceleration core for **Linux** (x86_64 /
aarch64), **macOS** (Apple Silicon), and **Windows** (x86_64) — no toolchain
needed. On any other platform `pip` builds from the sdist. To build from source
instead — for development, or to compile the Rust core on an unlisted platform:

```bash
git clone https://github.com/msobroza/pydocs-mcp && cd pydocs-mcp
pip install maturin && maturin develop --release
```

**Linux** needs OpenBLAS for the vector store (macOS and Windows already ship it):

```bash
sudo apt-get install -y libopenblas-pthread-dev
```

Then index your project and start the server:

```bash
pydocs-mcp serve .                            # index project + deps, serve over MCP (stdio)
pydocs-mcp serve . --gpu                      # …same, with CUDA-accelerated embeddings
pydocs-mcp search "batch inference"           # the same search, from the CLI
pydocs-mcp refs requests.auth.HTTPBasicAuth --direction inherits
```

Embeddings run on CPU by default. Add `--gpu` to `serve` / `index` / `watch`
(or the benchmark runner) to move all embedder inference — FastEmbed, the
`sentence_transformers` provider, and PyLate — onto CUDA. It's a latency knob only: no YAML change, no
re-index, identical results. Needs the matching GPU runtime — see
[INSTALL.md](INSTALL.md#gpu-inference-optional).

### Live re-indexing

The file watcher is part of the default install — no extra step. If you
edit code while you want the index to stay fresh, pick one of two modes —
both debounce edits to every indexed file type (Python, docs, and the
text/config formats indexed by default — see [Beyond Python](#beyond-python--multilanguage-indexing))
into a single reindex.

```bash
pydocs-mcp serve . --watch   # MCP server + watcher (for AI clients)
pydocs-mcp watch .            # watcher only (no MCP server; index stays fresh for CLI `search` / `symbol` / `refs`)
```

Both modes share the same YAML tunables: debounce, file extensions, and
ignored paths live under `serve.watch.*` in your `pydocs-mcp.yaml` (see
[DOCUMENTATION.md](DOCUMENTATION.md#live-re-indexing)).

### Exclude directories from indexing

Keep generated docs, test fixtures, or vendored trees out of search results.
Declare additional exclusions in your project's own `pyproject.toml` — they
travel with the repo:

```toml
[tool.pydocs-mcp]
exclude_dirs = ["docs/generated", "fixtures"]
```

Bare names (`"fixtures"`) match at any depth; paths (`"docs/generated"`)
match only that directory. Entries are additive over the built-in floor
(`.git`, `.venv`, …) — you can exclude more, never less. A server-side YAML
equivalent (`extraction.discovery.project.exclude_dirs`, plus a `dependency`
sibling) covers both project and dependency walks; see
[DOCUMENTATION.md](DOCUMENTATION.md#excluding-directories-from-indexing).

### Beyond Python — multilanguage indexing

Most Python projects carry more than `.py`: docs, config, and sometimes a
second-language source tree. pydocs-mcp indexes the ones that carry real
search value.

- **Indexed by default:** Python (`.py`), notebooks (`.ipynb`), and the
  text/config formats — Markdown, reStructuredText, plain text, TOML, YAML,
  INI/CFG, and JSON. Docs and config are the bulk of what real pull requests
  touch beyond code, so they are on out of the box. Files are split into
  searchable sections (headings for prose, top-level keys/tables for config)
  with real line numbers.
- **Code languages, opt-in:** JavaScript, TypeScript/TSX, C headers/sources,
  and Rust. These stay off by default so a vendored `node_modules` or C
  extension tree doesn't flood your results. Turn them on per project by
  naming the extensions you want:

  ```yaml
  # pydocs-mcp.yaml
  extraction:
    discovery:
      project:
        include_extensions: [".py", ".md", ".ipynb", ".rs", ".ts"]
  ```

  For structural symbols (functions, classes, structs) from these languages,
  install the grammar extra:

  ```bash
  pip install 'pydocs-mcp[multilang]'
  ```

  Without it, code files still index as searchable text — you lose the
  symbol outline, not the file. A one-line log tells you when that fallback
  kicks in and how to enable full parsing.

**What works per language (today):** full-text search, symbol outlines, and
surrounding-context expansion work for every indexed language. The call/
import/reference graph and per-symbol member listings remain Python-only —
for a non-Python target, `get_references` returns nothing and reports its
reference resolution as unavailable rather than pretending. Vendored trees
(`node_modules`, `extern`, `third_party`, and the like) and binary assets are
never indexed.

### Multi-repo search (optional)

One MCP server can host several already-indexed repos. Index each once (every
project writes a portable `{name}_{hash}.db` + `.tq` bundle under `--cache-dir`),
then serve them all — each query searches across every loaded repo, or one via
the `project` scope:

```bash
# index a few repos into a shared directory of db bundles
pydocs-mcp index ~/code/frontend --cache-dir ~/pydocs-index
pydocs-mcp index ~/code/backend  --cache-dir ~/pydocs-index

# serve them all from ONE MCP server (read-only — no reindex/watch)
pydocs-mcp serve --workspace ~/pydocs-index
pydocs-mcp serve --db ~/pydocs-index/backend_1a2b3c4d5e.db   # or specific bundles

# query across all loaded repos, or scope to one by name
pydocs-mcp search "db pool" --workspace ~/pydocs-index
pydocs-mcp search "db pool" --workspace ~/pydocs-index --project backend
```

On the MCP surface the selector is the `project` filter, a sibling of
`package`/`scope`: `search_codebase(query="db pool", project="backend")` /
`get_symbol(target="app.db.Pool", project="backend")`; omit it to search every loaded
repo. When the same package appears in several repos, a root-project copy wins
over a dependency copy, and among duplicate dependencies the most-recently-indexed
one is kept. Every loaded db must share the configured embedder — a mismatch
fails fast (a read-only load can't re-embed an absent project).

Calling `get_overview` with no selector on a multi-repo server returns a
**workspace orientation card** — one line per loaded repo with its package
count — so an agent that has just connected can see everything on offer before
it narrows to a `project`.

### Cross-repo references (multi-repo workspaces)

When several bundles are served together, a **link pass** resolves each
bundle's unresolved references against its siblings' symbols, so
`get_references` answers cross repository boundaries: callers of a `mylib`
symbol include its `backend` call sites (project-qualified rows), `impact`
walks across repos, and previously-unresolved callees become navigable
targets. References the local index already resolved always take precedence —
cross-links only add what a single bundle could not see.

Links live in a disposable sidecar next to the bundles
(`pydocs-links.sqlite3`); the bundles themselves are never modified. Serve
refreshes stale links automatically at startup; `pydocs-mcp link --workspace
~/pydocs-index` pre-bakes them (CI images, read-only deployments) and
`--check` gates on freshness. Tuning lives under `reference_graph.cross_repo`
in YAML (`enabled: true` by default — inert for single-repo serving): linked
edge kinds, alias resolution for re-exports, workspace-level ranking scores
(PageRank needs the `[graph]` extra; degree ranking works everywhere), and an
opt-in embedding-similarity kind. The workspace overview card reports link
freshness (`cross-repo links: fresh | stale(...)`).

### Ask your docs — chat agent (optional)

A LangGraph ReAct agent plus a Streamlit chat UI over the MCP server, for
asking questions across your indexed repos in natural language. Install the
`ask-your-docs` extra and run its command:

```bash
pip install 'pydocs-mcp[ask-your-docs]'
ask-your-docs --workspace ~/pydocs-index
```

Sidebar pickers pin a project / package / own-code-vs-dependency slice (enforced
on every tool call, not left to the model), and answers cite `project` +
`package.module` with a runnable usage snippet. Configuration and the
GPU-index / CPU-serve recipe live in
[examples/ask_your_docs_agent](examples/ask_your_docs_agent/README.md).

### Fast dependency indexing (selective embedding)

Everything is BM25/FTS-indexed, but **dense embedding is selective by package
tier** — embedding is the dominant indexing cost, and big dependencies (torch,
sklearn) carry tens of thousands of code chunks:

| Tier | What gets dense vectors | Selected by |
|---|---|---|
| Project / subprojects | every chunk (dense + graph, unchanged) | automatic |
| Promoted dependencies | every chunk — project-grade | `--full-dep NAME` (repeatable, globs OK) or `embedding.full_index_dependencies` |
| Regular dependencies | documentation only: one docstring **page per module** (module + public signatures + docstrings) plus `.md`/README chunks | default (`embedding.dependency_policy: doc_pages`) |

So torch indexes in seconds (≈one embedding per module) instead of an hour,
while its docs stay *semantically* searchable and all of its code stays
keyword-searchable + navigable (`get_symbol`, `kind="api"`). `scope=deps` queries
automatically route to a BM25 ∥ dense fusion pipeline that covers both. Set
`dependency_policy: full` to restore embed-everything, or `none` for BM25-only
dependencies:

```bash
pydocs-mcp index . --full-dep my-internal-lib --full-dep "acme-*"
```

Point any MCP-capable AI coding client or editor at it over stdio — copy-paste
client configs are in [DOCUMENTATION.md](DOCUMENTATION.md#mcp-client-integration),
and install troubleshooting (including the `libopenblas` fallback) is in
[INSTALL.md](INSTALL.md).

## What makes it different

Plenty of MCP servers feed documentation to an AI agent. pydocs-mcp is built for
one job — grounding your agent in the exact code and versions on *your* machine —
and a few properties fall out of that:

- **Version-matched, automatically.** It reads the exact releases in your
  `site-packages`, not a curated or hosted snapshot, so an answer can never
  describe a version you don't have installed.
- **Indexes your own code too.** Your project source is a first-class citizen
  (under the `__project__` package), not just third-party docs — so an agent can
  reason about your code and its dependencies in one search.
- **Answers code-structure questions.** A reference graph powers
  `get_references(direction=callers|callees|inherits|impact|governed_by)` — "what
  calls this?", "what breaks if I change it?", "which decisions govern it?" — not
  just relevance-ranked doc retrieval.
- **Local and private by default.** With the default on-device embedder every
  query stays on your machine — no accounts, no keys, no network calls, no
  per-query fees.
- **Lean.** No PyTorch and no FAISS in the default install — a small ONNX
  embedder plus the Rust TurboQuant vector store — so it stays quick and the
  on-disk index stays tiny.

It's OSS (MIT) and mounts alongside any other MCP servers your agent uses, so you
can route by intent rather than pick one.

Prefer names and numbers over adjectives? The [benchmark
suite](benchmarks/README.md#how-the-systems-compare-qualitative) carries a
side-by-side table of the alternatives it implements as baselines — and scores
them head-to-head on identical tasks and gold answers.

## Benchmarked, not hand-waved

pydocs-mcp ships a real benchmark harness that scores retrieval quality on public
code-retrieval benchmarks (RepoQA, DS-1000) with confidence intervals and plots,
so pipeline changes are measured, not asserted. The harness is developer tooling
that lives in its own package under [`benchmarks/`](benchmarks/) — see
[benchmarks/README.md](benchmarks/README.md); its internals are out of scope for
this README.

## Retrieval methods & R&D

Each method below is a named step under
[`python/pydocs_mcp/retrieval/steps/`](python/pydocs_mcp/retrieval/steps/),
addressable from YAML. The default `chunk_search_graph.yaml` composes
single-vector dense retrieval with reference-graph expansion (`graph_expand`) —
on the RepoQA benchmark this lifts recall@10 from 0.40 (keyword-only) to 0.77 on
standard queries and to 1.00 on structurally-reachable answers. Everything else
is opt-in via a preset swap (`--config`).

### Keyword — BM25 over SQLite FTS5

Full-text search with porter stemming and the unicode61 tokenizer.
Free, instant, and the baseline that every other method composes with
through the fusion steps below.

### Single-vector dense — FastEmbed + TurboQuant

- **Embedder.** [FastEmbed](https://github.com/qdrant/fastembed) with
  [BAAI/bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5)
  by default — runs on CPU via ONNX, no PyTorch, no torch download.
  OpenAI `text-embedding-3-small` is the optional alternative for
  users with an API key. Pass `--gpu` to run the on-device embedders
  (FastEmbed / `sentence_transformers`) on CUDA instead — same vectors,
  lower latency.
- **Bigger on-device model — the `sentence_transformers` provider.** For
  stronger dense recall without an API key, switch to
  [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
  served via [sentence-transformers](https://www.sbert.net/) (torch). It is
  GPU-reliable — torch frees CUDA memory between sequential index-builds — and
  the weights download at runtime on first use. Install the extra
  (`pip install 'pydocs-mcp[sentence-transformers]'`, ~1-5 GB with torch;
  torchvision is **not** included — if a model load demands it, the
  construction-time error message walks through the remedies),
  then set it in your YAML:

  ```yaml
  embedding:
    provider: sentence_transformers
    model_name: Qwen/Qwen3-Embedding-0.6B
    dim: 1024
    # Optional. Token cap (attention is O(seq^2) — the OOM guard). Omit to
    # use the embedder's own default (2048).
    max_seq_length: 2048
    # Optional. L2-normalize output (default true).
    normalize: true
    # Optional. Named asymmetric query prompt; omit to use the model's own.
    query_prompt_name: query
  ```

  The provider also runs ONNX / OpenVINO exports for **fast CPU inference** —
  typically 2–4× with a qint8-quantized file — via two optional keys
  (`pip install 'pydocs-mcp[openvino]'` for the OpenVINO runtime):

  ```yaml
  embedding:
    provider: sentence_transformers
    model_name: BAAI/bge-small-en-v1.5
    dim: 384
    backend: openvino          # torch (default) | onnx | openvino
    model_file_name: openvino/openvino_model_qint8_quantized.xml
  ```

  Setting either key re-embeds on the next index (quantized vectors differ
  from full-precision ones); defaults leave existing indexes untouched.

  The provider supports several on-device models — set `model_name` and the
  matching `dim`:

  - [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
    (1024-dim) — strong general-purpose retrieval.
  - [`Alibaba-NLP/gte-modernbert-base`](https://huggingface.co/Alibaba-NLP/gte-modernbert-base)
    (768-dim) — built on [ModernBERT](https://arxiv.org/abs/2412.13663) with a
    native 8192-token context; general-purpose and symmetric. Needs a recent
    `transformers` (≥ 4.48, < 6).
  - [`codefuse-ai/F2LLM-v2-0.6B`](https://huggingface.co/codefuse-ai/F2LLM-v2-0.6B)
    (1024-dim) — the CodeFuse F2LLM embedder; the **strongest dense model in our
    [benchmark](benchmarks/README.md)** on RepoQA code retrieval (recall@10 ≈ 0.93).

  The default remains bge-small; the `sentence_transformers` provider is opt-in.
- **Air-gapped / offline deployments.** Point `embedding.model_name` at a
  local directory of side-loaded weights (e.g. a `git clone` of the HF repo
  made on a connected machine) and nothing is downloaded — HF offline mode
  is forced, so a missing file fails locally instead of reaching for the
  network. Works for every provider: `fastembed` additionally needs the
  model's recipe in YAML (`pooling`, `normalize`, `model_file_name`) since
  an arbitrary ONNX folder doesn't carry it — and note fastembed pools only
  `mean`/`cls`, so last-token models like Qwen3-Embedding must use
  `provider: sentence_transformers` (which reads the recipe from the model
  directory itself). `openai` rejects a local path.
  Package mirrors: the `[sentence-transformers]` extra never pulls
  torchvision, so offline package mirrors need no torchvision wheel; if a
  model load does demand it, the mirror must add a torchvision wheel whose
  version exactly matches the mirrored torch wheel (e.g. torchvision
  0.26.0 ↔ torch 2.11.0, 0.28.0 ↔ torch 2.13.0) — a skewed pair is
  unresolvable offline. See
  `python/pydocs_mcp/defaults/default_config.yaml` for full examples.
- **Vector store.** [TurboQuant](https://arxiv.org/abs/2504.19874)
  ([turbovec](https://github.com/RyanCodrai/turbovec)) — Online Vector
  Quantization with near-optimal distortion. **~16× smaller than
  float32** (a 1536-dim vector drops from 6,144 to 384 bytes; a 10 M-doc
  corpus fits in ~4 GB instead of ~61 GB) and faster than FAISS FastScan
  at the same recall. Persists as a `.tq` sidecar next to the SQLite DB.
- **Query-embedding cache.** At serve time, repeated and concurrent
  identical queries are embedded once — an in-process LRU plus in-flight
  request coalescing, on by default and tunable (or disabled) via
  `embedding.query_cache.*` in your `pydocs-mcp.yaml`; a multi-repo
  workspace also shares a single embedder model load across all projects.
  Late-interaction query encodes get the same treatment via a separate,
  smaller-by-default `late_interaction.query_cache.*` block (per-token
  matrices are bigger entries).

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
- **Lighter-weight model — `lightonai/LateOn-Code-edge`.** For a
  smaller per-token footprint, point the same PyLate path at
  [`lightonai/LateOn-Code-edge`](https://huggingface.co/lightonai/LateOn-Code-edge)
  (48-dim token vectors instead of LateOn-Code's 128) in your YAML:

  ```yaml
  late_interaction:
    enabled: true
    provider: pylate
    model_name: lightonai/LateOn-Code-edge
    embedding_dim: 48
    document_length: 2048
    query_length: 256
  ```

  The default stays LateOn-Code; LateOn-Code-edge is opt-in.
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
- **Post-fusion dense re-rank (`dense_scorer`)** — an optional final
  step that takes the fused candidate list and re-scores just that
  subset against the TurboQuant vectors (an allowlist search, no fresh
  ANN scan), sorting the vector-scored hits to the top. Candidates with
  no dense vector — BM25-only, or skipped by the selective-embed policy —
  keep their fused order and trail behind, so recall is preserved while
  the embedded results get the sharper ordering. Mirrors the
  late-interaction scorer on the single-vector side.

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
this extend?"* questions through the `get_references(direction=…)` MCP tool
— `callers`, `callees`, `inherits`, `impact` (everything that transitively
depends on a symbol), and `governed_by` (which recorded decisions govern it):

```bash
pydocs-mcp refs requests.auth.HTTPBasicAuth --direction inherits
pydocs-mcp refs my_module.Parser.parse --direction callers
```

Capture is on by default and tunable under `reference_graph:` in YAML
(toggle, kinds-to-emit, output bounds).

The graph is also a **search** signal, not just a navigation surface:
the [`chunk_search_graph.yaml`](python/pydocs_mcp/pipelines/chunk_search_graph.yaml)
preset seeds graph expansion from the top dense hits to recover
structurally-adjacent answers a dense embedder misses (callers / callees /
overrides) — on a structural-recall split this lifts recall@10 from 0.30 to
1.00 (see [benchmarks](benchmarks/README.md#structural-recall-graph-expansion)).
The `graph_expand` step's `kind_weights` YAML knob assigns a per-edge-kind
trust so a weak signal (say `MENTIONS`) can be traversed but discounted —
its weight compounds along each expansion path.
Two opt-in index-time analytics (`reference_graph.node_scores` /
`reference_graph.similar_edges`, `[graph]` extra) add PageRank/community
rerankers and synthetic embedding-kNN edges — see
[DOCUMENTATION.md](DOCUMENTATION.md#graph-analytics-opt-in).

### Architectural decisions — the *why* behind the code

Reading code tells your agent *what* it does; it rarely tells it *why*. During
indexing (your project only), pydocs-mcp mines **architectural decisions** from
the artifacts that already record them — ADR files, inline decision markers,
commit messages, the changelog, and prose docs — deduplicates near-identical
findings, and stores each as a first-class, searchable record. An optional LLM
pass structures a chosen record into fields (context / decision / consequences)
when you turn it on.

Two surfaces expose them:

- **`get_why`** — ask *"why is this the way it is?"* by free-text query or by
  target symbol/file, and get the governing decisions back:

  ```bash
  pydocs-mcp why "why do we cache embeddings per chunk"
  pydocs-mcp why --target pydocs_mcp.storage.sqlite.chunk_repository
  ```
- **`search_codebase(kind="decision")`** — search the mined decisions directly,
  alongside the usual `docs` / `api` kinds (`pydocs-mcp search "vector store
  choice" --kind decision`).

Each decision also becomes a graph node linked to the symbols it affects, so
`get_references(direction="governed_by")` traces from a symbol back to the
decisions that govern it. Capture is on by default and tunable under
`decision_capture:` (which sources run, dedup threshold, the optional LLM
structuring); read-side output bounds live under `decisions.output`.

## Learn more

- **[examples/ask_your_docs_agent/](examples/ask_your_docs_agent/)** — a
  minimal LangGraph ReAct chat agent (terminal or notebook) that answers
  questions about your indexed repos through the task-shaped MCP tools.
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
- [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [gte-modernbert-base](https://huggingface.co/Alibaba-NLP/gte-modernbert-base), and [F2LLM-v2-0.6B](https://huggingface.co/codefuse-ai/F2LLM-v2-0.6B) — optional on-device `sentence_transformers` dense embedders (set via the `embedding:` YAML)
- ModernBERT — *Smarter, Better, Faster, Longer: A Modern Bidirectional Encoder for Fast, Memory Efficient, and Long Context Finetuning and Inference* · [arXiv:2412.13663](https://arxiv.org/abs/2412.13663) (2024) — the encoder backbone behind **gte-modernbert-base**
- F2LLM — *F2LLM-v2: Inclusive, Performant, and Efficient Embeddings for a Multilingual World* · [arXiv:2603.19223](https://arxiv.org/abs/2603.19223) (CodeFuse / Ant Group, 2026); original *F2LLM Technical Report: Matching SOTA Embedding Performance with 6 Million Open-Source Data* · [arXiv:2510.02294](https://arxiv.org/abs/2510.02294) (2025) — the source of the opt-in **F2LLM-v2-0.6B** embedder
- [PyLate](https://github.com/lightonai/pylate) with [`lightonai/LateOn-Code`](https://huggingface.co/lightonai/LateOn-Code) — the default model for the opt-in **late-interaction (multi-vector / MaxSim)** mode · *PyLate: Flexible Training and Retrieval for Late Interaction Models* · [arXiv:2508.03555](https://arxiv.org/abs/2508.03555) (LightOn, 2025)
- ColBERT — *Efficient and Effective Passage Search via Contextualized Late Interaction over BERT* · [arXiv:2004.12832](https://arxiv.org/abs/2004.12832) (Khattab & Zaharia, SIGIR 2020) — the late-interaction architecture
- PLAID — *An Efficient Engine for Late Interaction Retrieval* · [arXiv:2205.09707](https://arxiv.org/abs/2205.09707) (Santhanam et al., CIKM 2022) — implemented by [fast-plaid](https://github.com/lightonai/fast-plaid), the engine pydocs-mcp uses for MaxSim scoring
- Reciprocal Rank Fusion — *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods* · [Cormack, Clarke & Buettcher, SIGIR 2009](https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf) — the rank-fusion baseline (k=60)
- [PageIndex](https://github.com/VectifyAI/PageIndex) — inspiration for the LLM tree-reasoning mode

**Protocol**
- [Model Context Protocol](https://modelcontextprotocol.io) — the MCP standard

License: MIT.
