# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Known issues
- Six extraction / IO tests are skipped on Windows pending POSIX-vs-Windows
  path-separator handling work. Tracked as a follow-up; the release wheel
  build matrix is unaffected — only the CI test job for Windows surfaces
  the skips. The affected tests live under
  `tests/extraction/pipeline/test_stages_use_bundles.py`,
  `tests/extraction/test_members.py`, `tests/extraction/test_stages.py`,
  and `tests/retrieval/test_config.py`.

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

[Unreleased]: https://github.com/msobroza/pydocs-mcp/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.2.0
[0.1.0]: https://github.com/msobroza/pydocs-mcp/releases/tag/v0.1.0
