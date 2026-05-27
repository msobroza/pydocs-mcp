# Improvement SPEC: pydocs-mcp

**Repository:** `msobroza/pydocs-mcp`
**Commit audited:** `6673417`
**Audited on:** 2026-05-27
**Auditor:** `python-library-audit` skill, against the bundled High-Quality Python Library SPEC

---

## Summary

- **Overall score:** **32/100**
- **Severity counts:** **2 P0** · **6 P1** · **8 P2**
- **Status counts:** ✅ 9 Compliant · ⚠️ 8 Partial · ❌ 2 Missing · ➖ 4 N/A

**Score context.** This is a strict *packaging-readiness* heuristic, not a verdict on engineering quality. The project's core engineering is notably strong — 90% coverage gate, multi-arch wheel builds via maturin-action, Trusted Publishing via `uv publish` with OIDC, abi3-py311 stable ABI, rich markdown documentation, clean CLI vs library logging boundary. The score reflects packaging hygiene gaps that would matter if you intend to publish to PyPI in the near term — most are mechanical fixes, and the entire P0+P1 list can land in a single afternoon.

### Top recommendations

1. **Add a top-level `LICENSE` file** — `pyproject.toml` declares MIT but no LICENSE text ships, so wheels and the PyPI listing reference a license they don't actually carry. (P0, §23.1)
2. **Fix the version drift** — `pyproject.toml` says `0.2.0`, `python/pydocs_mcp/__init__.py` says `0.1.0`. Anyone reading `pydocs_mcp.__version__` gets the wrong answer. (P0, §9 / §3.3)
3. **Ship a `py.typed` marker** — 89 of 120 package files (74%) have return-type hints, but without `py.typed` downstream type-checkers silently ignore all of them. The typing investment is invisible to users. (P1, §3.2)
4. **Test on macOS and Windows in CI** — `release.yml` builds wheels for Linux x86_64+aarch64, macOS x86_64+arm64, Windows x64, but `ci.yml` only runs tests on `ubuntu-latest`. Broken non-Linux wheels can ship undetected. (P1, §13.1)
5. **Move dev deps from `[project.optional-dependencies]` to `[dependency-groups]`** — `dev = ["pytest", "pytest-cov", "ruff", "pytest-asyncio"]` currently ships in wheel metadata; PEP 735 dependency-groups don't. (P1, §8.3)

---

## Compliance Matrix

| § | Section | Status | Severity | Findings |
|---|---|---|---|---|
| §2 | Scope and domain | ✅ | — | 0 |
| §3 | Project structure & layout | ⚠️ | P1 | 2 |
| §5 | Python version support | ✅ | — | 0 |
| §6 | pyproject.toml | ⚠️ | P1 | 1 |
| §7 | Build backend | ✅ | — | 0 |
| §8 | Dependency management | ⚠️ | P1 | 2 |
| §9 | Versioning | ❌ | P0 | 1 |
| §10 | Code quality | ⚠️ | P1 | 3 |
| §11 | Testing | ✅ | — | 0 |
| §12 | Documentation | ✅ | — | 0 |
| §13 | CI/CD & publishing | ⚠️ | P1 | 1 |
| §14 | Security | ✅ | — | 0 |
| §15 | API design & ergonomics | ➖ | — | 0 |
| §16 | Exception hierarchy | ⚠️ | P2 | 1 |
| §17 | Logging | ✅ | — | 0 |
| §19 | Deprecation policy | ➖ | — | 0 |
| §21 | Distribution | ✅ | — | 0 |
| §22 | Release automation | ⚠️ | P2 | 1 |
| §23 | Repository hygiene | ❌ | P0 | 4 |
| §24 | Community management | ➖ | — | 0 |
| §25 | Inner source | ➖ | — | 0 |

---

## Findings

### P0 findings

#### P0-1 — Add a top-level `LICENSE` file (§23.1)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Create `LICENSE` at the repository root containing the standard MIT license text matching the SPDX identifier in `pyproject.toml`. Keep `LICENSE-third-party` for vendored/referenced project attributions.

**Why:** `pyproject.toml` declares `license = { text = "MIT" }`, but the working tree only has `LICENSE-third-party` — there is no `LICENSE` file. Without one, the built wheel's metadata won't reference the actual license text, PyPI's project page will show "License: MIT" with no link, and downstream consumers can't redistribute confidently. (SPEC §23.1)

**Code:**

```text
MIT License

Copyright (c) 2026 <Your Name or Organization>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

Pair with the PEP 639 `license-files` entry in P1-1 so the file is included in the wheel.

---

#### P0-2 — Fix version drift between `pyproject.toml` and `__init__.py` (§9 / §3.3)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Either bump `python/pydocs_mcp/__init__.py` to `0.2.0` to match `pyproject.toml`, or — better — eliminate the drift class entirely by sourcing version dynamically from installed metadata.

**Why:** `pyproject.toml` declares `version = "0.2.0"` but `python/pydocs_mcp/__init__.py` declares `__version__ = "0.1.0"`. They are out of sync. Any user reading `pydocs_mcp.__version__` (a standard introspection idiom) sees `0.1.0` while `pip show pydocs-mcp` reports `0.2.0`. This recurs on every release unless the version comes from one source. (SPEC §9.4)

**Code:**

```python
# Before — python/pydocs_mcp/__init__.py
"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""
__version__ = "0.1.0"

# After — read from installed metadata, no manual bumps
"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:  # not installed (e.g. running from a checkout)
    __version__ = "0.0.0+unknown"
```

For a fully VCS-driven version (recommended for the next release), have `release.yml` sync the tag into both `Cargo.toml` and `pyproject.toml` before the build matrix:

```yaml
# .github/workflows/release.yml — add as the first step in each build job
- name: Sync version from tag
  run: |
    VERSION=${GITHUB_REF#refs/tags/v}
    sed -i.bak "s/^version = .*/version = \"$VERSION\"/" Cargo.toml
    sed -i.bak "s/^version = .*/version = \"$VERSION\"/" pyproject.toml
    rm -f *.bak
  shell: bash
```

---

### P1 findings

#### P1-1 — Replace legacy `license` table with PEP 639 SPDX expression (§6.2)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** quick

**What:** Replace `license = { text = "MIT" }` with the PEP 639 SPDX form, and add `license-files`.

**Why:** The `{ text = "..." }` table form is the pre-PEP-639 syntax. Modern build backends (maturin ≥ 1.7 included) and PyPI prefer the SPDX expression string. PEP 639 also requires `license-files` so the LICENSE file is correctly included in wheel metadata. (SPEC §6.1)

**Code:**

```toml
# Before
[project]
license = { text = "MIT" }

# After
[project]
license = "MIT"
license-files = ["LICENSE", "LICENSE-third-party"]
```

Pair with P0-1 (create `LICENSE` first).

---

#### P1-2 — Ship a `py.typed` marker (§3.2)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Add an empty file at `python/pydocs_mcp/py.typed`, and tell maturin to include it.

**Why:** 89 of 120 package files (74%) carry return-type annotations — a substantial typing investment. But PEP 561 requires a `py.typed` marker for downstream type-checkers (mypy, pyright, Pylance) to trust the annotations. Without it, every user importing `pydocs_mcp` sees inferred `Any` types, getting no IDE help, no completion, no type errors at the call site. The typing work is currently invisible. (SPEC §3.2)

**Code:**

```bash
touch python/pydocs_mcp/py.typed
git add python/pydocs_mcp/py.typed
```

Then update `tool.maturin.include` so the wheel actually picks it up:

```toml
# Before
[tool.maturin]
include = [
  "python/pydocs_mcp/defaults/*.yaml",
  "python/pydocs_mcp/defaults/*.json",
  "python/pydocs_mcp/pipelines/*.yaml",
  "python/pydocs_mcp/retrieval/prompts/*.j2",
]

# After
[tool.maturin]
include = [
  "python/pydocs_mcp/py.typed",
  "python/pydocs_mcp/defaults/*.yaml",
  "python/pydocs_mcp/defaults/*.json",
  "python/pydocs_mcp/pipelines/*.yaml",
  "python/pydocs_mcp/retrieval/prompts/*.j2",
]
```

Verify after build: `python -m zipfile -l dist/pydocs_mcp-*.whl | grep py.typed`.

---

#### P1-3 — Configure mypy and run it in CI (§10.2)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** medium

**What:** Add a `[tool.mypy]` section to `pyproject.toml` and a `typecheck` step to `ci.yml`. Start lenient, ratchet to strict.

**Why:** The package has heavy type-hint coverage (~74% of files) but no static checker runs against it. Annotations drift, become incorrect, and silently break the user-facing IDE experience. A mypy run in CI catches both — and with `py.typed` shipped (P1-2), it verifies the annotations are coherent for downstream consumers. (SPEC §10.2)

**Code:**

```toml
# pyproject.toml — add this section
[tool.mypy]
python_version = "3.11"
files = ["python/pydocs_mcp"]
# Start gentle so you can land it without a giant initial cleanup PR.
warn_unused_configs = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_return_any = true
no_implicit_optional = true
disallow_untyped_defs = false   # flip to true once the floor is reached

[[tool.mypy.overrides]]
module = ["tests.*", "benchmarks.*"]
ignore_errors = true
```

```yaml
# .github/workflows/ci.yml — add inside the python job, after Lint
- name: Typecheck
  run: |
    pip install mypy
    mypy python/pydocs_mcp
```

Pragmatic ratchet plan: `mypy --strict python/pydocs_mcp/ > strict_baseline.txt`, commit `strict_baseline.txt`, fail CI only on net-new errors, shrink the baseline over time.

---

#### P1-4 — Test wheels on macOS and Windows in CI (§13.1)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** medium

**What:** Add `macos-13`, `macos-14` (arm64), and `windows-latest` to the test matrix in `ci.yml`. Skip the libopenblas install step on non-Linux (the README confirms those platforms ship CBLAS via Accelerate/MSVC).

**Why:** `release.yml` builds and uploads wheels for Linux (x86_64 + aarch64), macOS (x86_64 + arm64), and Windows x64 — but `ci.yml` only runs tests on `ubuntu-latest`. The asymmetry between what you build and what you test is the most common cause of "released wheel doesn't work on Windows" bugs in PyO3 / maturin projects. Paths, line endings, BLAS resolution, subprocess semantics all differ. (SPEC §13.1)

**Code:**

```yaml
# .github/workflows/ci.yml — replace the python job's strategy + steps
jobs:
  python:
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-13, macos-14, windows-latest]
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      # WHY: libopenblas-pthread-dev is only required on Linux.
      # macOS uses Accelerate, Windows uses the MSVC runtime CBLAS.
      - name: Install OpenBLAS (Linux only)
        if: runner.os == 'Linux'
        run: |
          sudo apt-get update
          sudo apt-get install -y libopenblas-pthread-dev liblapack-dev
          sudo update-alternatives --set libblas.so.3-x86_64-linux-gnu \
            /usr/lib/x86_64-linux-gnu/openblas-pthread/libblas.so.3 || true

      - name: Install project and dev deps
        run: pip install -e ".[dev]"

      - name: Lint
        run: ruff check python/ tests/

      - name: Test with coverage
        env:
          LD_PRELOAD: ${{ runner.os == 'Linux' && '/usr/lib/x86_64-linux-gnu/libopenblas.so.0' || '' }}
        run: |
          python -m pytest tests/ -v \
            --ignore=tests/test_parity.py \
            --cov=pydocs_mcp \
            --cov-report=term-missing \
            --cov-fail-under=90
```

If a handful of tests genuinely don't pass cross-platform (e.g., POSIX-only fork semantics), `@pytest.mark.skipif(platform.system() == "Windows", ...)` them rather than dropping the whole job.

---

#### P1-5 — Move dev deps to `[dependency-groups]` (PEP 735) (§8.3)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** quick

**What:** Migrate `[project.optional-dependencies].dev` to `[dependency-groups]`. Keep `[project.optional-dependencies]` only for *user-facing* extras.

**Why:** Right now `dev = ["pytest", "pytest-cov", "ruff", "pytest-asyncio"]` lives in `[project.optional-dependencies]`, which means: (1) it ships in the wheel's METADATA — anyone running `pip show pydocs-mcp` sees your test dependencies, (2) any user can run `pip install pydocs-mcp[dev]` and get pytest installed, which is almost never intentional, (3) tools that respect PEP 735 (uv, pip 25.1+, PDM) can't distinguish "user-installable extra" from "developer-only group". (SPEC §8.3)

**Code:**

```toml
# Before
[project.optional-dependencies]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff", "pytest-asyncio>=0.23"]

# After — remove the [project.optional-dependencies] block entirely
# (no user-facing extras yet), or keep it for future user-installable extras.

[dependency-groups]
test = [
    "pytest >= 7",
    "pytest-cov >= 4",
    "pytest-asyncio >= 0.23",
]
lint = [
    "ruff",
    "mypy >= 1.10",   # add once P1-3 lands
]
dev = [
    {include-group = "test"},
    {include-group = "lint"},
]
```

CI install becomes:

```yaml
# Before
- run: pip install -e ".[dev]"

# After (pip 25.1+ or uv)
- run: pip install -e . && pip install --group dev
# Or if pinning to uv:
- run: uv sync --group dev
```

---

#### P1-6 — Define the public API surface with `__all__` (§3.3)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** quick

**What:** Extend `python/pydocs_mcp/__init__.py` to re-export the intended public symbols and declare them in `__all__`.

**Why:** Right now `__init__.py` exports only `__version__` — there's no signal to users (or type-checkers, or AI coding assistants) about which submodules and symbols are public vs internal. New integrators have to read `__main__.py` and the docs to find the entry points. Explicit `__all__` is also what `from pydocs_mcp import *` and tooling like Pyright's "exported names" use. (SPEC §3.3)

**Code:**

```python
# python/pydocs_mcp/__init__.py
"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

# Public exception hierarchy
from .application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)

# Optional but recommended — re-export the main façades:
# from .application.docs_service import DocsService
# from .application.indexing_service import IndexingService

__all__ = [
    "__version__",
    "MCPToolError",
    "InvalidArgumentError",
    "NotFoundError",
    "ServiceUnavailableError",
    # "DocsService",
    # "IndexingService",
]
```

Pick the exports based on what external integrators actually need to import. Everything else stays addressable via fully-qualified paths but is implicitly internal.

---

### P2 findings

#### P2-1 — Add a `CHANGELOG.md` (§22)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Create `CHANGELOG.md` in Keep-a-Changelog format. Backfill what you can from git tags / GitHub release notes for `0.1.0` and `0.2.0`.

**Why:** With a release workflow already tag-triggered and PyPI publishing live, users need a place to see what changed between versions. The git log is not a changelog — it's noisy and ordered by author intent, not user impact. (SPEC §22)

**Code:** see `CS-15` in the bundled `code_snippets.md` for the canonical Keep-a-Changelog skeleton.

---

#### P2-2 — Align Ruff `target-version` with `requires-python` (§10.1)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** quick

**What:** Bump `target-version = "py310"` to `"py311"` to match `requires-python = ">=3.11"`.

**Why:** With Ruff still targeting 3.10, you (a) don't get autofixes to 3.11+ idioms via the `UP` rules and (b) accept patterns the 3.11 runtime would never see. Match the floor. (SPEC §5)

**Code:**

```toml
# Before
[tool.ruff]
target-version = "py310"

# After
[tool.ruff]
target-version = "py311"
```

---

#### P2-3 — Expand Ruff `select` to include code-quality and security rules (§10.1)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** quick

**What:** Add `B`, `UP`, `S`, `SIM`, `RUF`, `C901`, `PT`, `PTH` to the `select` list.

**Why:** Current set is `["E", "F", "W", "I"]` — pycodestyle, Pyflakes, isort. That catches syntax-level issues but misses bug-prone patterns (`B`), upgrade opportunities (`UP`), security (`S`/Bandit), idiomatic simplifications (`SIM`), pytest-specific bugs (`PT`), pathlib over os.path (`PTH`), and McCabe complexity (`C901`). Each adds value at zero runtime cost. (SPEC §10.1, §10.4)

**Code:**

```toml
# Before
[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501", "E701"]

# After
[tool.ruff.lint]
select = [
    "E", "W",     # pycodestyle
    "F",          # Pyflakes
    "I",          # isort
    "B",          # flake8-bugbear
    "UP",         # pyupgrade
    "S",          # flake8-bandit (security)
    "SIM",        # flake8-simplify
    "RUF",        # Ruff-specific
    "C901",       # McCabe
    "PT",         # flake8-pytest-style
    "PTH",        # flake8-use-pathlib
]
ignore = ["E501", "E701"]

[tool.ruff.lint.mccabe]
max-complexity = 12   # generous floor; tighten as you refactor

[tool.ruff.lint.per-file-ignores]
"tests/**"                      = ["F401", "F811", "F841", "E402", "I001", "S101", "S105", "S106"]
"python/pydocs_mcp/_fast.py"    = ["F401", "I001"]
"python/pydocs_mcp/__main__.py" = ["T201"]   # CLI is allowed to print
```

Run `ruff check --fix python/ tests/` once after — it'll autofix the bulk.

---

#### P2-4 — Unify exceptions under a single library base class (§16)

**Status:** ⚠️ Partial &nbsp;|&nbsp; **Effort:** medium

**What:** Introduce a `PydocsMCPError` root exception and have all existing custom exception classes inherit from it (in addition to their current `Exception` / `ValueError` / `RuntimeError` parents where appropriate).

**Why:** The project already has nicely-scoped custom exceptions (`MCPToolError`, `InvalidArgumentError`, `NotFoundError`, `ServiceUnavailableError`, `PipelineLoadError`, `UnitOfWorkNotEnteredError`) — but they don't share a common library base. Embedders who want to catch *any* pydocs-mcp failure with one `except` can't. A unified base preserves the existing semantics (you keep the `ValueError`/`RuntimeError` lineage) while giving callers a single library handle. (SPEC §16)

**Code:**

```python
# New file: python/pydocs_mcp/exceptions.py
"""Public exception hierarchy.

All exceptions raised by pydocs-mcp inherit from `PydocsMCPError`, so
callers can `except PydocsMCPError` to catch any library-originated failure
without swallowing unrelated bugs.
"""

from __future__ import annotations


class PydocsMCPError(Exception):
    """Base class for all exceptions raised by pydocs-mcp."""
```

Then update the existing exception classes:

```python
# python/pydocs_mcp/application/mcp_errors.py — Before
class MCPToolError(Exception):
    ...

# After
from pydocs_mcp.exceptions import PydocsMCPError

class MCPToolError(PydocsMCPError):
    ...

# python/pydocs_mcp/storage/errors.py — Before
class UnitOfWorkNotEnteredError(RuntimeError):
    ...

# After
class UnitOfWorkNotEnteredError(PydocsMCPError, RuntimeError):
    ...

# python/pydocs_mcp/retrieval/pipeline/code_pipeline.py — Before
class PipelineLoadError(ValueError):
    ...

# After
class PipelineLoadError(PydocsMCPError, ValueError):
    ...
```

`PydocsMCPError` goes into `__all__` (see P1-6) and becomes the documented "catch any pydocs-mcp error" handle.

---

#### P2-5 — Add a `.pre-commit-config.yaml` (§10.5)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Add pre-commit hooks for ruff (lint + format), basic whitespace/YAML/TOML hygiene, and a local `cargo fmt` hook for the Rust side.

**Why:** Pre-commit catches the cheap stuff before CI does. The Rust+Python CI is heavy (BLAS install, multi-Python matrix, maturin builds) — anything that can fail fast at commit time saves minutes per PR. (SPEC §10.5)

**Code:**

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v4.6.0
    hooks:
      - id: trailing-whitespace
      - id: end-of-file-fixer
      - id: check-yaml
      - id: check-toml
      - id: check-added-large-files

  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.0
    hooks:
      - id: ruff
        args: [--fix]
        files: ^(python/|tests/|benchmarks/|tools/|scripts/)
      - id: ruff-format
        files: ^(python/|tests/|benchmarks/|tools/|scripts/)

  - repo: local
    hooks:
      - id: cargo-fmt
        name: cargo fmt
        entry: cargo fmt --check
        language: system
        files: \.rs$
        pass_filenames: false
```

Then: `pre-commit install`.

---

#### P2-6 — Add a `Makefile` (§23.4)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Add a top-level `Makefile` standardizing the common dev commands (test, lint, format, typecheck, build, clean) with the Rust+Python hybrid in mind.

**Why:** `CLAUDE.md` already documents the canonical commands, but there's no executable single-entry-point. A `Makefile` is the standard CLI for "what do I run for X?" — particularly valuable here because the workflow has both Python (`pytest`) and Rust (`maturin develop`, `cargo test`, `cargo clippy`) flavors that need orchestration. (SPEC §23.4)

**Code:**

```makefile
.PHONY: install test test-rust lint lint-rust format typecheck build clean

install:
	pip install -e ".[dev]"
	maturin develop --release

test:
	python -m pytest tests/ -v --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-report=term-missing

test-rust:
	cargo test

lint:
	ruff check python/ tests/
	ruff format --check python/ tests/

lint-rust:
	cargo fmt --check
	cargo clippy -- -D warnings

format:
	ruff check --fix python/ tests/
	ruff format python/ tests/
	cargo fmt

typecheck:
	mypy python/pydocs_mcp

build:
	maturin build --release

clean:
	rm -rf build/ dist/ target/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
```

(Tabs, not spaces.)

---

#### P2-7 — Add `.editorconfig` (§23.3)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** quick

**What:** Add a top-level `.editorconfig` so contributors' editors agree on indentation, line endings, and final-newline policy without depending on local IDE setup.

**Why:** Cross-platform CI is in scope (P1-4). Without `.editorconfig`, contributors editing on Windows can introduce CRLF or trailing whitespace that survives until CI catches it. (SPEC §23.3)

**Code:**

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
indent_style = space
insert_final_newline = true
trim_trailing_whitespace = true

[*.py]
indent_size = 4
max_line_length = 100

[*.rs]
indent_size = 4
max_line_length = 100

[*.{yaml,yml,toml,json}]
indent_size = 2

[*.md]
indent_size = 2
trim_trailing_whitespace = false

[Makefile]
indent_style = tab
```

---

#### P2-8 — Commit a lockfile and use it in CI (§8.5)

**Status:** ❌ Missing &nbsp;|&nbsp; **Effort:** medium

**What:** Generate `uv.lock` and have CI install from it for reproducible builds.

**Why:** CI currently does `pip install -e ".[dev]"`, which resolves dependencies fresh on every run. A transitive dep can ship a breaking patch release and redden CI without any local change — and the failure is silent in your git history. A locked install ties each commit to a specific reproducible resolution.

This is P2 rather than P1 because the project already pins tight upper bounds on most deps (`turbovec>=0.5,<1.0`, `fastembed>=0.4,<1.0`, `openai>=1.40,<2.0`, `jinja2>=3.0,<4.0`), which substantially reduces churn risk. (SPEC §8.5)

**Code:**

```bash
pip install uv
uv lock
git add uv.lock
```

```yaml
# .github/workflows/ci.yml — replace install step
# Before
- run: pip install -e ".[dev]"

# After (uv)
- uses: astral-sh/setup-uv@v3
- run: uv sync --frozen --group dev
- run: uv pip install -e .   # so the maturin-built local pkg is in the env
```

---

## Quick wins

The fastest items to knock out. In suggested order:

- [ ] **Add `LICENSE`** — copy MIT text into `LICENSE` at repo root (P0-1, ~2 min)
- [ ] **Fix `__version__`** — switch to `importlib.metadata` (P0-2, ~5 min)
- [ ] **Add `py.typed`** — `touch python/pydocs_mcp/py.typed` + add to maturin `include` list (P1-2, ~3 min)
- [ ] **PEP 639 license form** — swap `{ text = "MIT" }` → `"MIT"` + add `license-files` (P1-1, ~2 min)
- [ ] **Define `__all__`** — extend `__init__.py` (P1-6, ~10 min)
- [ ] **Align Ruff `target-version`** — `"py310"` → `"py311"` (P2-2, ~1 min)
- [ ] **Expand Ruff `select`** — paste larger ruleset + run `ruff check --fix` (P2-3, ~30 min including cleanup)
- [ ] **Add `.editorconfig`** — paste template (P2-7, ~2 min)
- [ ] **Add `Makefile`** — paste template (P2-6, ~5 min)
- [ ] **Add `CHANGELOG.md`** — paste skeleton + backfill `0.1` / `0.2` entries (P2-1, ~15 min)

Total: **under 90 minutes** for everything in the quick-win list.

---

## Appendix A — Raw scan output

Selected facts from the static scan. Full JSON in `audit_summary_pydocs-mcp.json`.

| Fact | Value |
|---|---|
| Layout | `python/` (maturin convention) |
| Package name | `pydocs_mcp` |
| Has `py.typed` | **no** |
| Has `__all__` | **no** |
| Build backend | `maturin` |
| `requires-python` | `>=3.11` |
| Runtime deps count | 9 |
| Optional deps groups | `[dev]` |
| Dependency groups (PEP 735) | (none) |
| Lockfile | (none) |
| Python files | 389 (120 in package, ~74% with return-type hints) |
| Test files | 76 |
| CI Python matrix | `3.11`, `3.12`, `3.13` |
| CI OS matrix | `ubuntu-latest` only (tests); Linux+macOS+Windows (release wheels) |
| Trusted Publishing | **yes** (via `uv publish` + `id-token: write`) |
| pip-audit in CI | no |
| Detected anti-patterns | none in library code (`print()`/`basicConfig` only in `__main__.py` — correct CLI/library boundary) |

---

## Appendix B — Scanner notes

The `python-library-audit` scanner had two misfires on this repo, neither affecting the findings above (both were corrected manually during the audit):

1. **Layout auto-detect picked `tools/` first** instead of `python/pydocs_mcp/`. The scanner walks top-level dirs alphabetically and doesn't yet know about maturin's `python-source = "python"` convention. All package-level findings here were re-run against the correct `python/pydocs_mcp/` path. (Scanner improvement: read `tool.maturin.python-source` and `tool.hatch.build.targets.wheel.packages` to locate the package directory.)

2. **Trusted Publishing detection was a false negative.** The scanner currently looks only for the `pypa/gh-action-pypi-publish` action. This repo uses `uv publish` from `astral-sh/setup-uv@v4`, which also supports OIDC trusted publishing — and the `id-token: write` permission confirms it. So §13.2 is ✅ Compliant; the scanner just hadn't learned the `uv publish` pattern yet.

Both will be fixed in a future scanner update.
