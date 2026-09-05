# Contributing to pydocs-mcp

Thanks for your interest in contributing.

## Development setup

```bash
# 1. Clone + install dev dependencies
git clone https://github.com/msobroza/pydocs-mcp
cd pydocs-mcp
make install    # pip install -e . + maturin develop --release + dev group

# 2. Run the test suite (Python — fast)
make test

# 3. Build + run the Rust accelerator's parity tests (slower)
make test-rust
```

System requirement: on Linux, `libopenblas-pthread-dev` is needed for the
turbovec CBLAS path. macOS uses Accelerate; Windows uses MSVC's CBLAS.
See `INSTALL.md` for details.

## Repository layout

- `python/pydocs_mcp/` — the Python package (maturin python-source layout).
- `src/` — the Rust accelerator crate (NOT Python; compiled into
  `pydocs_mcp._native`).
- `tests/` — the product test suite.
- `benchmarks/` — the separately-packaged `pydocs-mcp-eval` suite (its own
  `pyproject.toml` and `benchmarks/tests/`).
- `docs/` — internal engineering record: the normative tool contract + ADRs,
  plus historical plans/specs (see `docs/README.md`).
- `documentation/` — the Sphinx/MyST site shell; it `{include}`s the root
  `.md` files, which are the single-source content — do not merge them.
- `notebooks/`, `examples/` — demos and sample material.
- `scripts/` — repo-level wrappers and CI smoke checks (vs
  `benchmarks/scripts/`, which are eval-internal utilities).

## Style + checks

- `make format` — apply `ruff format` + `cargo fmt`
- `make lint` — `ruff check` + `ruff format --check`
- `make lint-rust` — `cargo fmt --check` + `cargo clippy`
- `make typecheck` — `mypy` on `python/pydocs_mcp`
- `make gate` — the full CI-equivalent local gate: lint + format + types +
  the cognitive-complexity ceiling (`complexipy`, max 15) + the dead-code
  check (`vulture`, min confidence 80) + the test suite with the 90%
  coverage threshold. Run this before pushing to catch what CI catches.

`pre-commit install` once after cloning will run the cheap checks on every
commit automatically.

## Pull-request expectations

- Tests pass on Ubuntu + Python 3.13 (the single cell CI runs on every PR
  and push to `main`). The full OS x Python matrix — Ubuntu / macOS 14 /
  Windows x 3.11 / 3.12 / 3.13 — only runs on a release tag or a manual
  workflow dispatch, so a PR run won't surface platform-specific issues;
  call those out in the PR description if your change is platform-sensitive.
- Coverage threshold (`--cov-fail-under=90`) is enforced by CI, alongside a
  benchmark-import smoke check (renaming an internal `pydocs_mcp` module can
  silently break `benchmarks/`, which lives outside `tests/`) and a
  dedicated test pass for the `[graph]` extra (PageRank / community
  detection).
- CI also runs `uv lock --check` — run `uv lock` after editing
  `pyproject.toml` dependencies so the committed lockfile doesn't drift —
  and `pip-audit --strict` against the locked dependency set.
- New user-facing behaviour gets an entry in `CHANGELOG.md` under
  `[Unreleased]` in Keep-a-Changelog format.

## Reporting a vulnerability

See `SECURITY.md` for the private reporting flow. Please do not file public
GitHub issues for security reports.

## Code of Conduct

This project does not currently ship a formal Code of Conduct file. Be
kind, focus on the work, and assume good intent. A Contributor Covenant
file may land in a future release.
