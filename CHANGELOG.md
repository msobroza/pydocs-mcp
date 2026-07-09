# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## v0.5.1

### Changed

- The tool-docs contract constants — `REQUIRED_MARKERS`, `CHARS_PER_TOKEN`,
  `PER_TOOL_TOKEN_BUDGET`, and `TOTAL_TOKEN_BUDGET` — are now part of the public
  importable surface of `pydocs_mcp.application.tool_docs`. The `pydocs-mcp-eval`
  optimizer artifacts import them to share the §D13 tool-docs validation logic,
  so they need a published release that exposes them.

## v0.5.0

Headline: the MCP surface becomes **six task-shaped tools**, every response now
travels in a **freshness / next-step / truncation envelope**, and the index
grows an **architectural-decision layer** (mine decisions at index time, ask
`get_why` at query time).

### Added

- **Six task-shaped MCP tools + full CLI parity** — the surface is now
  `get_overview`, `search_codebase`, `get_symbol`, `get_context`,
  `get_references`, and `get_why`, each mirrored by a CLI subcommand
  (`overview` / `search` / `symbol` / `context` / `refs` / `why`). `get_context`
  packs one or more targets under a shared token budget; `get_overview` returns a
  structural orientation card for a package or the whole workspace. The old
  `search` / `lookup` pair is retired (`lookup` stays as a deprecated CLI alias).
  (#141)
- **Response conventions — one envelope around every answer** — each response
  (MCP or CLI) carries a freshness header (`[index: <sha> · <N>d old · <M>
  packages]`, plus a stale warning when the working tree has moved past the
  indexed commit), inline next-step pointers resolved to the calling surface,
  and a recoverable truncation footer (`[truncated: …]`) that names every clipped
  section and the pointer to fetch it in full. On by default; tunable under
  `output.envelope`. (#139)
- **Architectural-decision layer** — decisions are mined from your project at
  index time (ADR files, inline markers, commit messages, changelog, docs prose,
  deduplicated; optional LLM structuring). The read side exposes them via the new
  `get_why` tool (free-text or by-target), `search_codebase(kind="decision")`,
  `get_references(direction="governed_by")`, and dedicated overview blocks; each
  decision is a graph node with `GOVERNS` edges to the symbols it affects.
  Configured under `decision_capture:` (write) and `decisions.output` (read);
  schema v14. (#145, #146)
- **Multi-repo workspace orientation card** — an empty `get_overview` against a
  multi-repo server now returns one line per loaded repo with its package count,
  so a freshly connected agent can orient before narrowing to a `project`. (#153)
- **`graph_expand` per-edge-kind trust (`kind_weights`) + `MENTIONS` traversal** —
  graph expansion can now traverse weaker edge kinds at a discounted weight (the
  weight compounds along each path), with sweep configs for tuning. (#166)
- **`ask-your-docs` as a first-class install extra** — the LangGraph ReAct agent
  + Streamlit chat UI now ship inside the package (`pydocs_mcp/ask_your_docs/`)
  behind `pip install 'pydocs-mcp[ask-your-docs]'` and the `ask-your-docs`
  console command, with sidebar project/package/scope pickers enforced on every
  tool call and a read-only interactive graph-explorer page. (#157)
- **Benchmark harness expansion** — the harness is now a first-class programmatic
  surface with a paired agent-efficiency track (indexed vs bare, blind judge,
  spend guardrails), a SWE-QA / SWE-QA-Pro retrieval track, a `small_dev` split,
  and a `comparing-retrieval-methods` guide. Developer tooling under
  `benchmarks/`. (#132, #133, #144, #171)

### Changed

- **`dense_scorer` is now a post-fusion re-ranker** — instead of a standalone
  dense retriever, it re-scores the fused candidate subset against the TurboQuant
  vectors via an allowlist search (no fresh ANN scan) and sorts the vector-scored
  hits to the top; candidates without a dense vector keep their fused order and
  trail behind, so recall is preserved. Mirrors the late-interaction scorer on
  the single-vector side. (#154)
- **Docs modernized to the six task-shaped surface** — README, DOCUMENTATION,
  SPEC, IDEAS, EXTENSIONS, and the benchmarks README no longer describe the
  retired two-tool `search` / `lookup` surface; the root README is now
  vendor-neutral (no named third-party comparisons). (#141)
- **Storage / retrieval internals refactored** for maintainability — `sqlite.py`
  split into a per-repository package with shared CRUD helpers, the CLI
  write-side composition root extracted into `storage/factories`, the retrieval
  config split into a package, and several hexagonal-seam leaks closed
  (`FilterAdapter` wiring, FTS builder dedup, `db.py` layering). No user-facing
  behavior change. (#128, #130, #135, #136, #137)

### Removed

- **`SqliteVectorStore` deprecated alias** — the class was renamed
  `SqliteLexicalStore` (it is the FTS5/BM25 lexical store, not a vector
  store); the back-compat alias is gone from `pydocs_mcp.storage` and
  `pydocs_mcp.storage.sqlite`. Import `SqliteLexicalStore` instead.

### Fixed

- **Two audit-hardening waves — ~65 bug fixes with regression tests** — a
  high-risk wave (18 fixes + 24 new regression-test files) followed by a
  medium/low wave across storage, db, server, retrieval, extraction, the
  envelope, the CLI, the watcher, and the Rust core. Includes three reproduced
  crash bugs (FTS5 operator queries, composite-UoW enter-leak, migration
  crash-loop), dead watch mode with the real watchdog, `--force` inherited on
  every save, and `chunks_fts` desync on package deletes.
  (#148, #150, #152, #158, #159, #160, #161, #162, #163, #164)
- **`get_context` budget accounting** — `_split_budget` now honors one shared
  budget so the summed output never exceeds the requested total. (#161)
- **`examples/ask_your_docs_agent` crashed on startup** — the agent fetched
  the removed `lookup` tool (`StopIteration` on connect); it now targets the
  six task-shaped tools and reads the indexed-projects listing via
  `get_overview`.

### CI

- **`uv lock --check` drift gate** added; the `[graph]` extra now pulls `scipy`
  (PageRank stopped crashing) and is exercised in CI; the heavy `ask-your-docs`
  extra is kept off the core test matrix. (#168, #169, #170, #173, #174)

## [0.4.1] — 2026-07-03

### Added

- **Air-gapped / offline model loading** — point `embedding.model_name` at a
  local directory of side-loaded weights and nothing is ever downloaded, for
  every provider. fastembed states the model recipe in YAML (new `pooling`
  knob + `normalize` / `model_file_name`) and loads via a pinned local path;
  sentence-transformers and PyLate take the directory natively (the right
  choice for last-token models like Qwen3-Embedding) with HF offline mode
  forced so a missing file fails locally; `openai` rejects a local path with
  an actionable error. Existing configs keep their exact pipeline hashes —
  nothing re-embeds. (#121)
- **Ask-your-docs Streamlit webapp** — `examples/ask_your_docs_agent` now
  ships a themed chat UI (`streamlit run streamlit_app.py`) over the same
  LangGraph agent: sidebar config, conversation memory with follow-up
  reformulation, code snippets rendered in fenced blocks. The example is now
  Streamlit + notebook only (the terminal REPL is gone). (#122)

### Fixed

- **Full-suite test failures in the fast-plaid storage tests** — the
  default-install no-torch test evicted torch from `sys.modules` without
  restoring it, so any later torch import in the same run crashed
  (`function '_has_torch_function' already has a docstring`). The evicted
  modules are now restored, and the suite is fully green. (#123)

### Changed

- The ask-your-docs example defines its dependencies in a single
  `requirements.txt` (the short-lived `requirements.py` variant is gone).

## [0.4.0] — 2026-07-03

### Added

- **Multi-repo search** — one MCP server (or CLI query) over several already-
  indexed repos: `serve --workspace <dir>` / `--db <file>` load pre-built
  `{name}_{hash}.db` bundles read-only; a new `project` filter on `search` /
  `lookup` scopes one repo, omitted it unions across all with dedup (a repo's
  own code beats the same symbol seen as a dependency; most-recently-indexed
  wins among duplicates). A per-database identity stamp (`index_metadata`)
  rejects bundles built with a mismatching embedder up front.
- **Reference-graph readers on `lookup`** — `show="impact"` (everything that
  transitively calls a symbol, ranked — "what breaks if I change X?") and
  `show="context"` (the symbol's dependency closure packed under a token
  budget at graded fidelity — "everything to understand X").
- **Graph-boosted retrieval** — `graph_expand` step (dense-seeded 1-hop
  reference-graph expansion), index-time `node_scores` (PageRank / community,
  optional `[graph]` extra) with centrality / diversity rerankers, synthetic
  embedding-kNN `similar` edges, graph pipeline presets, and a
  structural-recall benchmark split.
- **Selective dependency embedding** — everything stays BM25/FTS-indexed, but
  dense vectors are written per package tier: the project embeds fully;
  dependencies embed one docstring **page per module** (module + public
  signatures + docstrings) plus markdown/READMEs by default. Promote chosen
  dependencies to full embedding with `--full-dep NAME` /
  `embedding.full_index_dependencies` (globs supported);
  `embedding.dependency_policy: full | doc_pages | none`. Indexing
  torch-sized dependencies drops from ~an hour to seconds on CPU.
- **ONNX / OpenVINO backends for `sentence_transformers`** —
  `embedding.backend: torch | onnx | openvino` + `embedding.model_file_name`
  (e.g. a qint8-quantized export) for ~2–4× faster CPU inference; new
  `[openvino]` extra. Index on GPU, serve on CPU with the same model.
- **New embedders** — `gte-modernbert-base` and the code-specialized
  `F2LLM-v2` family via the `sentence_transformers` provider (RepoQA
  leaderboard + figures in the benchmark docs).
- **Dependency manifests** — `[project.optional-dependencies]` and PEP 735
  `[dependency-groups]` (what `uv add --group` writes) are now parsed; the
  `--watch` watcher re-indexes when `pyproject.toml` / `requirements*.txt`
  change, so adding a package updates the index automatically.
- **Example agent** — `examples/ask_your_docs_agent/`: a minimal LangGraph
  ReAct chat agent (terminal or notebook) answering questions about your
  indexed repos through the MCP tools, with conversation memory, follow-up
  reformulation, and project inference.
- **Documentation site** — Sphinx + Furo under `documentation/`.

### Changed

- **Default chunk search is now dense + graph expansion**
  (`chunk_search_graph.yaml`), replacing BM25-only — RepoQA recall@10 0.40 →
  0.77 on standard queries and 0.30 → 1.00 on structurally-reachable answers,
  at no extra indexing cost. BM25 and hybrid remain as presets.
- Dependencies embed documentation pages only by default (see Added);
  `scope="deps"` searches route to a BM25 ∥ dense fusion preset so dependency
  code stays reachable by keyword.
- `graph_expand` decay default raised to 0.9.
- Schema v10 → v12 (`node_scores`, `index_metadata`, `chunks.embedded`) —
  additive, migrated automatically on open. Note: the ingestion pipeline
  identity changed, so the first re-index after upgrading re-extracts and
  re-embeds packages; serving existing indexes keeps working without it.

### Fixed

- Reference resolver no longer rescans the whole symbol universe per
  reference (O(N²) → bucketed) — indexing large dependencies such as numpy /
  torch previously appeared to hang.
- The startup SQLite ↔ vector-store integrity check compares intended
  embeddings instead of raw chunk counts, ending the repeated
  re-extract-everything loop for deployments that don't embed every chunk.
- Dense search over a partially-embedded corpus no longer raises when the
  candidate set contains vectorless chunks.
- BM25 candidates carry `qualified_name`, unblocking LLM tree reranking.
- GPU benchmark runs no longer silently fall back to CPU
  (onnxruntime CUDA library path).

## [0.3.1] — 2026-06-10

### Added

- **`--skip-deps` CLI flag** on `serve` / `index` / `watch` — index only the
  project source, skipping dependency resolution + indexing entirely. The CLI
  counterpart of `ProjectIndexer.index_project(include_dependencies=False)`
  and the inverse of `--skip-project`.

## [0.3.0] — 2026-06-10

### Added (LLM tree-reasoning — enrichment, token budget, two-stage rerank)

- **PageIndex node enrichment** — each LLM-visible tree node now carries its real
  signature (params + type hints + return annotation), its decorators, and a
  docstring excerpt, beyond the generated summary. Tunable via `doc_excerpt`
  (`sections` | `full` | `off`) and `doc_excerpt_max_chars`. A non-destructive
  schema auto-refresh (v9) re-extracts the metadata on next index without
  re-embedding unchanged chunks.
- **Token-counted tree budget** — the serialized tree handed to the LLM is bounded
  in real `tiktoken` tokens (previously whitespace words, which under-counted code
  ~3× and could overflow the model's context window with a 400
  `context_length_exceeded`). `max_tree_words` → **`max_tree_tokens`**
  (`int | None`; `None` auto-derives from the configured model's context window).
  Over-budget pruning is content-first — drop per-node doc excerpts before whole
  nodes. Adds `tiktoken` as a runtime dependency.
- **BM25 → tree two-stage rerank** — opt-in `rerank_candidates` mode on the
  `llm_tree_reasoning` step scopes the LLM-visible tree to a prior BM25/dense
  candidate set and writes its ranked picks back as the pipeline's final ranking
  (with a `repoqa_bm25_tree_rerank` benchmark config).
- Persist `chunks.qualified_name` (schema v7) so tree-reasoning picks resolve to
  the correct chunks.

### Added (on-device dense embeddings)

- **`sentence_transformers` embedding provider** (`provider: sentence_transformers`)
  serving `Qwen/Qwen3-Embedding-0.6B` and other SentenceTransformer models via
  torch — a GPU-reliable on-device dense embedder (torch frees CUDA memory
  between sequential index-builds). Opt-in via the `[sentence-transformers]`
  extra. New `EmbeddingConfig` knobs `max_seq_length` / `normalize` /
  `query_prompt_name` (the first two fold into the pipeline hash; the
  query-only prompt does not).

### Removed

- **The `onnx` embedding provider** (`OnnxEmbedder` and the `onnx_file` /
  `query_instruction` config fields). The torch-backed `sentence_transformers`
  provider replaces it for on-device Qwen3-Embedding — onnxruntime leaked the
  CUDA arena across the benchmark's sequential index-builds.

### Added (GPU inference)

- **`--gpu` flag** on `serve`, `index`, and `watch` (and the benchmark runner)
  to run all embedder inference — FastEmbed, the `sentence_transformers`
  provider, and PyLate late-interaction — on CUDA. No YAML change; covers both
  index-time and query-time embedding. The execution device is excluded from the
  pipeline / index-cache hash, so toggling `--gpu` shares the same `.tq` /
  fast-plaid index and never forces a re-index (it is a latency knob, not a
  quality change).
- **`EmbeddingConfig.device`** (`cpu` / `cuda`) wiring through `build_embedder`
  into the FastEmbed and sentence_transformers embedders;
  `AppConfig.with_device(gpu=...)` stamps the device after config load. GPU
  runtimes (`onnxruntime-gpu`, `fastembed-gpu`, CUDA torch) are documented in
  `INSTALL.md`, not auto-installed.

## [0.2.0] — 2026-05-28

### Added (late-interaction retrieval — ColBERT / PyLate via fast-plaid)

- **Late-interaction (multi-vector / MaxSim) retrieval backend**, opt-in via
  `pip install 'pydocs-mcp[late-interaction]'` + `late_interaction.enabled: true`
  in YAML. Ships `lightonai/LateOn-Code` as the default model via PyLate
  ([arXiv:2508.03555](https://arxiv.org/abs/2508.03555)) and scores MaxSim
  through [fast-plaid](https://github.com/lightonai/fast-plaid) (PLAID —
  [arXiv:2205.09707](https://arxiv.org/abs/2205.09707)).
- **`chunk_multi_vector_ids` SQLite mapping table** (schema v6) bridges
  `chunk_id` ↔ fast-plaid's auto-assigned `plaid_doc_id`. The existing
  `FilterAdapter` Protocol scopes MaxSim to the SQLite-filtered candidate set
  via fast-plaid's `subset=` parameter.
- **Three new YAML presets** (`ingestion_late_interaction.yaml`,
  `chunk_search_late_interaction.yaml`,
  `chunk_search_late_interaction_ranked.yaml`) plus benchmark sweep configs
  (`repoqa_hybrid_li_rrf.yaml`, `ds1000_hybrid_li_rrf.yaml`).
- **`LateInteractionScorerStep` retrieval step** + `EmbedChunksMultiVectorStage`
  ingestion stage + `FastPlaidUnitOfWork` storage adapter + `NullMultiVectorStore`
  for the disabled deployment path.

### Added (PyPI packaging polish)

- `[project] authors`, `keywords`, `classifiers`, `[project.urls]` for PyPI
  rendering. `Cargo.toml` version synced to `0.2.0`.

### Fixed

- `build_retrieval_context` now wires `BuildContext.embedder =
  build_embedder(config.embedding)`. Any pipeline that referenced
  `DenseFetcherStep` / `DenseScorerStep` previously crashed at decode time
  with the actionable `ValueError`. FastEmbed's ONNX model stays lazy so the
  BM25-only deployment still pays nothing at startup.

### Added (final P2 follow-ups — closes #14 audit findings)

- `SECURITY.md` at repo root — GitHub-rendered private vulnerability reporting flow with 72h ack / 7d confirm / 30d fix SLAs.
- `CONTRIBUTING.md` at repo root — external-contributor entry point referencing the `make install` / `make test` workflow.
- `pip-audit` security job in `.github/workflows/ci.yml` — scans the locked dep tree for CVEs in strict mode.
- `release.yml` syncs version from the pushed git tag — `sed -i` updates both `Cargo.toml` and `pyproject.toml` in every build job, gated on `refs/tags/v`.

### Changed

- `python/pydocs_mcp/__init__.py` attaches a `logging.NullHandler` at the package logger (PEP 282 library convention). Users who configure logging via `logging.basicConfig()` see no behaviour change.


### Added
- MIT `LICENSE` file at the repository root.
- PEP 561 `py.typed` marker so downstream type-checkers honor the package's type hints.
- `PydocsMCPError` root exception so embedders can catch any pydocs-mcp failure with one `except`.
- `__all__` declaration in `pydocs_mcp/__init__.py` with the public exception hierarchy re-exported.
- `[dependency-groups]` (PEP 735) for dev / test / lint deps.
- mypy configuration + CI typecheck step.
- Multi-OS CI matrix (macOS + Windows in addition to Linux).
- `.pre-commit-config.yaml`, `Makefile`, `.editorconfig` for contributor ergonomics.
- This `CHANGELOG.md`.

### Changed
- `pyproject.toml` license declaration migrated to PEP 639 SPDX form (`license = "MIT"` + `license-files`).
- `pydocs_mcp.__version__` sourced from installed metadata via `importlib.metadata` (was hard-coded; drifted from `pyproject.toml`).
- Ruff `target-version` bumped to `py311` (matches `requires-python`).
- Ruff `select` expanded with `B`, `UP`, `S`, `SIM`, `RUF`, `C901`, `PT`, `PTH`.

### Fixed
- Cross-platform path-separator / encoding / newline handling in six
  extraction / IO tests so they execute on Windows as well as POSIX hosts.
  Assertions now compare via `Path.as_posix()` (test fixtures previously
  embedded forward slashes in `str(Path)` comparisons), test fixtures
  pin UTF-8 explicitly when writing test files with non-ASCII content
  (`write_text(..., encoding="utf-8")`) and use `write_bytes` to avoid
  Windows CRLF translation on the file-read round-trip.

## [0.2.0]

### Added
- `pydocs-mcp serve --watch` flag — live re-indexing via the new `FileWatcher` module.
- `pydocs-mcp watch` standalone subcommand — watcher only, no MCP server.
- Rich `description=` + `epilog=` on the `search` / `lookup` CLI subparsers.
- Server-level `FastMCP(instructions=...)` block with workflow framing for AI clients.

### Changed
- Tool annotations on MCP `search` + `lookup` (`readOnlyHint`, `idempotentHint`, `openWorldHint`).

## [0.1.0]

### Added
- Initial public release.
- Local MCP server indexing Python project + dependency docs/code into a hybrid (BM25 + dense embeddings) index.
- 2 MCP tools: `search` (BM25 + dense, RRF-fused) and `lookup` (with reference-graph traversal).
- Rust acceleration via maturin (PyO3) with a pure-Python fallback.

[v0.5.0]: https://github.com/msobroza/pydocs-mcp/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.4.1
[0.4.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.4.0
[0.3.1]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.1
[0.3.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.1.0
