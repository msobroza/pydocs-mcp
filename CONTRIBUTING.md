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

## Style + checks

- `make format` — apply `ruff format` + `cargo fmt`
- `make lint` — `ruff check` + `ruff format --check`
- `make lint-rust` — `cargo fmt --check` + `cargo clippy`
- `make typecheck` — `mypy` on `python/pydocs_mcp`

`pre-commit install` once after cloning will run the cheap checks on every
commit automatically.

## Pull-request expectations

- Tests pass on Ubuntu 3.11 / 3.12 / 3.13 (the CI matrix runs all of these
  plus macOS 13/14 and Windows; you can rely on CI rather than running them
  all locally).
- Coverage threshold (`--cov-fail-under=90`) is enforced by CI.
- New user-facing behaviour gets an entry in `CHANGELOG.md` under
  `[Unreleased]` in Keep-a-Changelog format.

## Reporting a vulnerability

See `SECURITY.md` for the private reporting flow. Please do not file public
GitHub issues for security reports.

## Code of Conduct

This project does not currently ship a formal Code of Conduct file. Be
kind, focus on the work, and assume good intent. A Contributor Covenant
file may land in a future release.
