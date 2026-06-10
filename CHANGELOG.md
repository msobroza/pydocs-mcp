# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/msobroza/pydocs-mcp/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.3.0
[0.2.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.1.0
