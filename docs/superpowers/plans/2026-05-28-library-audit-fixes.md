# pydocs-mcp library-audit fixes — TDD implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`. Each task below is sized for one ~3-15 min focused subagent dispatch. Run them sequentially (later tasks depend on earlier ones — version unification feeds `__all__`; LICENSE feeds PEP 639; `PydocsMCPError` feeds `__all__`; etc.). Verify the `pytest -q` + `ruff check` gate at the end of every task before moving on.

**Spec:** `docs/superpowers/specs/2026-05-28-library-audit-design.md` (16 findings: 2 P0 + 6 P1 + 8 P2)
**Worktree:** `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/library-audit/`
**Branch:** `feature/library-audit-fixes`
**Base:** `main` at `b80b0c8` (also the most recent commit on the worktree branch)
**Baseline test count (per CLAUDE.md):** 1367 unit + 283 benchmark tests.

---

## Goal

Address all 16 findings from the `python-library-audit` scan against `pydocs-mcp` in a **single PR / single branch**: ship a `LICENSE` file, eliminate version drift, expose a `py.typed` marker + PEP 561 typing surface, run `mypy` in CI, test on macOS + Windows, modernize Ruff rules, define `__all__`, unify exceptions under a `PydocsMCPError` root, migrate dev deps to PEP 735 dependency groups, and add the usual repo-hygiene boilerplate (CHANGELOG, pre-commit, Makefile, `.editorconfig`, `uv.lock`).

No behavioral changes to MCP tools or runtime indexing semantics. The MCP surface remains the fixed 2-tool `search` + `lookup` (CLAUDE.md §"MCP API surface vs YAML configuration").

## Architecture

Three categories of change, grouped by where they land in the repository:

1. **Packaging metadata** (`pyproject.toml`, `LICENSE`, `python/pydocs_mcp/py.typed`, `python/pydocs_mcp/__init__.py`, `python/pydocs_mcp/exceptions.py`) — fixes P0-1, P0-2, P1-1, P1-2, P1-5, P1-6, P2-2, P2-3, P2-4. The wheel will start advertising MIT correctly, ship the `py.typed` marker so PEP 561 type-checkers consume the inline annotations, and expose a stable `__all__` plus a single `PydocsMCPError` root that all custom exceptions inherit from.
2. **CI / dev-tooling** (`.github/workflows/ci.yml`, `.pre-commit-config.yaml`, `Makefile`, `.editorconfig`, `uv.lock`) — fixes P1-3, P1-4, P2-5, P2-6, P2-7, P2-8. CI grows a multi-OS test matrix (ubuntu / macos-13 / macos-14 / windows-latest) plus a `mypy` step; `pre-commit`, `Makefile`, `.editorconfig`, and an optional `uv.lock` standardize the developer entry points.
3. **User-facing docs** (`CHANGELOG.md`) — fixes P2-1. Keep-a-Changelog skeleton with backfilled `0.1.0` and `0.2.0` entries.

**Ordering constraints (dependency graph):**

- **P0-1 (LICENSE) must land before P1-1 (PEP 639 `license-files`)** — `license-files` references files that must already exist.
- **P2-4 (`PydocsMCPError` root) must land before P1-6 (`__all__`)** — `__all__` re-exports `PydocsMCPError`, so the class must exist first.
- **P0-2 (version unification via `importlib.metadata`) must land before P1-6 (`__all__`)** — `__init__.py` is rewritten by the version task; the `__all__` task layers on top of the rewrite.
- **P2-2 (target-version=py311) + P2-3 (expand ruff `select`) should land together** — both edit `[tool.ruff.lint]` and both will trigger autofixes. Bundling them keeps one fixup commit.
- **P1-3 (mypy in CI) and P1-4 (multi-OS CI) edit the same `ci.yml`** — order P1-4 first (easier to verify in isolation), then P1-3 on top.
- **P1-5 (PEP 735 dep-groups) lands after P1-3** so the new `lint` group already includes `mypy >= 1.10`.

**Risk items requiring careful task handling:**

- **P2-3 ruff `select` expansion** (Task 9): adding `B / UP / S / SIM / RUF / C901 / PT / PTH` will surface MANY violations. The task explicitly runs `ruff check --fix` and a follow-up manual cleanup commit. EXPECT fixup time. The `per-file-ignores` block in the spec already silences `S101`/`S105`/`S106` in tests (`assert`, hardcoded passwords) and `T201` in `__main__.py` (CLI `print` is fine).
- **P1-3 mypy** (Task 10): will surface latent type errors. The task starts with the lenient config from the spec (`disallow_untyped_defs = false`, `warn_unused_ignores = true`) and accepts that a handful of narrow `# type: ignore[...]` may be needed in `_fast.py` (rust/python boundary). Document the ratchet plan as a comment in `pyproject.toml`.
- **P1-4 multi-OS CI** (Task 11): macOS + Windows may surface platform-specific test failures (path separators, fork semantics, fastembed model-cache paths, sqlite file-locking semantics). Plan is to mark genuinely platform-bound tests with `@pytest.mark.skipif(...)` rather than dropping the matrix entry. EXPECT a follow-up fixup commit on top of Task 11 if Windows lights up.

**Tech-stack reminders the implementer MUST mirror in every task:**

- Python 3.11+; type hints on every new function (`PydocsMCPError`, anything else).
- Worktree's Python interpreter: `/Users/msobroza/Projects/pyctx7-mcp/.venv/bin/python`.
- Test runner: `pytest -q`. Lint: `ruff check python/ tests/`. Type-check (new, Task 10): `mypy python/pydocs_mcp`.
- Build: `maturin develop --release` (only when Rust changes; this plan has zero Rust changes).
- Comments explain **WHY**, not WHAT (CLAUDE.md §"Code Comments"). No `# AC #N` annotations.
- **Authorship (CRITICAL, global rule from `~/.claude/CLAUDE.md`):** NO `Co-Authored-By:` trailer on any commit. NO `--author` flag. NO `git config` edits. Every commit on this branch is sole-authored by `msobroza`. This applies to direct commits, dispatched subagent commits, and any push/PR operation.
- One commit per task. Commit body via HEREDOC for clean formatting.
- User-facing prose (CHANGELOG, README, CLI help, MCP tool descriptions) MUST NOT reference internal jargon — `PR #N`, `sub-PR`, `Task N of`, `RRF`, `FTS5`, `TurboQuant` (CLAUDE.md §"README files: no internal PR / sub-PR / task jargon"). Code comments may reference history.

## Tech stack

- **Python:** 3.11+ (existing project floor; Task 7 / P2-2 syncs Ruff target).
- **Build:** maturin (PEP 517) bridges Python + Rust cdylib. `tool.maturin.include` carries the new `py.typed` marker (Task 5).
- **Lint:** ruff. Task 9 expands the `select` ruleset; per-file-ignores added for `tests/**`, `_fast.py`, `__main__.py`.
- **Type-check (new):** mypy ≥ 1.10. Task 10 adds `[tool.mypy]` to `pyproject.toml` and a `Typecheck` step to `ci.yml`. Starts lenient.
- **CI:** GitHub Actions matrix grows from `ubuntu-latest × {3.11, 3.12, 3.13}` to `{ubuntu-latest, macos-13, macos-14, windows-latest} × {3.11, 3.12, 3.13}` (Task 11), plus a typecheck step (Task 10).
- **Dependency groups:** PEP 735 `[dependency-groups]` (Task 8). User-facing extras stay in `[project.optional-dependencies]` (e.g., `watch`).
- **Lockfile (optional in this PR):** `uv.lock` via `uv lock`. Task 13 — if `uv` is awkward in this CI environment, the task documents skipping it and filing a follow-up.

---

## Acceptance criteria coverage matrix

| Finding | Severity | Task | Status |
|---|---|---|---|
| P0-1 LICENSE file | P0 | Task 2 | scheduled |
| P0-2 Version drift | P0 | Task 3 | scheduled |
| P1-1 PEP 639 license form | P1 | Task 4 | scheduled |
| P1-2 `py.typed` marker | P1 | Task 5 | scheduled |
| P1-3 mypy in CI | P1 | Task 10 | scheduled |
| P1-4 Multi-OS CI | P1 | Task 11 | scheduled |
| P1-5 PEP 735 dep-groups | P1 | Task 8 | scheduled |
| P1-6 `__all__` + public API | P1 | Task 7 | scheduled |
| P2-1 CHANGELOG.md | P2 | Task 12 | scheduled |
| P2-2 Ruff target=py311 | P2 | Task 9 | scheduled |
| P2-3 Expand ruff `select` | P2 | Task 9 | scheduled |
| P2-4 `PydocsMCPError` root | P2 | Task 6 | scheduled |
| P2-5 `.pre-commit-config.yaml` | P2 | Task 12 | scheduled |
| P2-6 `Makefile` | P2 | Task 12 | scheduled |
| P2-7 `.editorconfig` | P2 | Task 12 | scheduled |
| P2-8 `uv.lock` | P2 | Task 13 | scheduled (may defer) |

Total: 16 findings → 13 tasks (Tasks 1 and 14 are scaffolding/verification, not findings).

---

## Task 1 — Commit plan + baseline metrics

**Goal.** Land the plan file on the branch and capture the pre-change `pytest` test count + `ruff check` violation count, so post-change verification has a numeric baseline.

**Steps:**

1. Confirm the worktree state:

   ```bash
   cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/library-audit
   git status
   git log --oneline -3
   ```

   Expect: branch `feature/library-audit-fixes`, HEAD at `b80b0c8` (or downstream of it), `docs/superpowers/specs/2026-05-28-library-audit-design.md` and `docs/superpowers/specs/2026-05-28-library-audit-raw.json` already present, this plan file present in working tree.

2. Capture baseline numbers (commit the output as a one-line comment in the plan or in the commit body — do NOT commit a generated `.txt` artifact):

   ```bash
   .venv/bin/python -m pytest -q tests/ 2>&1 | tail -3
   .venv/bin/python -m ruff check python/ tests/ 2>&1 | tail -5
   ```

   Expected baseline: 1367 passed (per CLAUDE.md), 0 ruff violations.

3. Stage and commit the plan + spec:

   ```bash
   git add docs/superpowers/plans/2026-05-28-library-audit-fixes.md
   git add docs/superpowers/specs/2026-05-28-library-audit-design.md
   git add docs/superpowers/specs/2026-05-28-library-audit-raw.json
   git status                                # confirm nothing else accidentally staged
   ```

4. Commit (NO `Co-Authored-By:` trailer):

   ```bash
   git commit -m "$(cat <<'COMMIT'
   docs(library-audit): plan + spec for 2 P0 + 6 P1 + 8 P2 fixes

   Adds the implementation plan and the raw audit-spec source for the
   single-PR library-audit cleanup. Baseline: 1367 tests, 0 ruff violations.

   The plan groups 16 findings into 13 tasks and respects the dependency
   order: LICENSE -> PEP 639; PydocsMCPError -> __all__; importlib.metadata
   version -> __init__.py rewrite -> __all__.
   COMMIT
   )"
   ```

**Verification gate:** `git status` clean, `git log --oneline -1` shows the new commit, baseline numbers logged.

---

## Task 2 — LICENSE file (P0-1)

**Goal.** Add a top-level `LICENSE` file containing the standard MIT license text so the wheel and PyPI listing reference the license they actually carry. Keep `LICENSE-third-party` for vendored attributions.

**Failing-test step (file-presence assertion):** the audit spec already establishes the missing-file fact; for this PR the verification is structural — "file exists at expected path, contains MIT-license text, mentions current year (2026), is referenced from `pyproject.toml` once Task 4 lands." Optionally drop a tiny test to lock the invariant in:

```python
# tests/test_packaging_metadata.py — new file (will be extended in later tasks)
"""Structural invariants on packaging metadata files.

These tests are deliberately cheap and don't import the package — they
guard against silent regressions in repo-level packaging hygiene.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_license_file_exists_and_is_mit() -> None:
    """LICENSE file must exist at repo root and declare MIT terms."""
    license_path = REPO_ROOT / "LICENSE"
    assert license_path.exists(), "LICENSE file missing at repo root"
    text = license_path.read_text(encoding="utf-8")
    assert "MIT License" in text
    assert "Permission is hereby granted" in text
    assert "Copyright" in text
```

Run it first; it MUST fail (`LICENSE file missing at repo root`).

**Implementation step.** Create `/Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/library-audit/LICENSE`:

```text
MIT License

Copyright (c) 2026 Max Raphael Sobroza Marques

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

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/                # full suite still 1367+ tests passing
.venv/bin/python -m ruff check python/ tests/       # 0 violations
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add LICENSE tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
docs(license): add top-level LICENSE (MIT) — P0-1

pyproject.toml declares MIT but no LICENSE text shipped, so wheels and
the PyPI listing referenced a license they didn't carry. This commit
adds the canonical MIT text at the repo root; LICENSE-third-party
stays as the vendored-attributions file.

Pairs with the PEP 639 license-files entry coming in P1-1.
COMMIT
)"
```

---

## Task 3 — Version unification via `importlib.metadata` (P0-2)

**Goal.** Eliminate the `0.1.0` vs `0.2.0` drift between `python/pydocs_mcp/__init__.py` and `pyproject.toml` by sourcing the version from installed package metadata at import time. One source of truth: `pyproject.toml`.

**Failing-test step.** Extend `tests/test_packaging_metadata.py`:

```python
import pydocs_mcp


def test_version_matches_pyproject() -> None:
    """pydocs_mcp.__version__ must match pyproject.toml's [project] version."""
    import tomllib

    pyproject_path = REPO_ROOT / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    pyproject_version = data["project"]["version"]
    assert pydocs_mcp.__version__ == pyproject_version, (
        f"__version__={pydocs_mcp.__version__!r} but pyproject says "
        f"{pyproject_version!r}"
    )


def test_version_is_real_when_installed() -> None:
    """When installed (the dev case), __version__ must not be the fallback."""
    import pydocs_mcp

    # Editable install via `pip install -e .` populates importlib.metadata.
    # The fallback string only surfaces when running from a checkout without
    # an install, which the worktree's `.venv` does not do.
    assert pydocs_mcp.__version__ != "0.0.0+unknown"
```

Run; first test FAILS (gets `0.1.0`, expects `0.2.0`).

**Implementation step.** Rewrite `python/pydocs_mcp/__init__.py`:

```python
"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:
    # WHY: surfaces when running from a checkout that wasn't `pip install -e .`'d
    # (e.g. CI bootstrap before the install step). Real installs read from
    # PKG-INFO / dist-info and never hit this branch.
    __version__ = "0.0.0+unknown"
```

**Note:** `__all__` is NOT added in this task — that's Task 7 (P1-6), after the `PydocsMCPError` root lands (Task 6).

**Verification gate:**

```bash
.venv/bin/python -c "import pydocs_mcp; print(pydocs_mcp.__version__)"
# Expect: 0.2.0
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/                # full suite green
.venv/bin/python -m ruff check python/ tests/
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add python/pydocs_mcp/__init__.py tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
fix(version): source __version__ from importlib.metadata — P0-2

pyproject.toml said 0.2.0 but __init__.py said 0.1.0, so anyone reading
pydocs_mcp.__version__ saw the wrong answer. Switch __init__.py to read
the installed-package metadata; pyproject.toml is now the single source
of truth. Falls back to "0.0.0+unknown" only when running from a
checkout without `pip install -e .`.
COMMIT
)"
```

---

## Task 4 — PEP 639 license form (P1-1)

**Goal.** Replace the legacy `license = { text = "MIT" }` table with the PEP 639 SPDX expression and add `license-files`. Maturin ≥ 1.7 honors this and the wheel METADATA now references the LICENSE file from Task 2.

**Failing-test step.** Extend `tests/test_packaging_metadata.py`:

```python
import tomllib


def test_pyproject_license_is_spdx_string() -> None:
    """PEP 639: license must be an SPDX expression string, not a table."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    license_value = pyproject["project"]["license"]
    assert isinstance(license_value, str), (
        f"license should be SPDX string, got {type(license_value).__name__}: "
        f"{license_value!r}"
    )
    assert license_value == "MIT"


def test_pyproject_declares_license_files() -> None:
    """PEP 639 license-files must list LICENSE so wheel metadata picks it up."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    license_files = pyproject["project"].get("license-files", [])
    assert "LICENSE" in license_files
    assert "LICENSE-third-party" in license_files
```

Run; both FAIL.

**Implementation step.** Edit `pyproject.toml`:

```toml
# Before
license = { text = "MIT" }

# After
license = "MIT"
license-files = ["LICENSE", "LICENSE-third-party"]
```

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
# Optional manual check: build the wheel and confirm the LICENSE is inside.
# maturin build --release && python -m zipfile -l target/wheels/pydocs_mcp-*.whl | grep -i license
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add pyproject.toml tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
build(metadata): adopt PEP 639 license expression — P1-1

Replace `license = { text = "MIT" }` (pre-PEP-639 table form) with the
SPDX expression `license = "MIT"` and declare `license-files` so the
wheel metadata correctly bundles the LICENSE file added in P0-1.
Maturin >= 1.7 honors both keys; older backends would have silently
ignored license-files.
COMMIT
)"
```

---

## Task 5 — py.typed marker + maturin include (P1-2)

**Goal.** Make pydocs-mcp PEP 561-compliant: ship an empty `py.typed` marker so downstream type-checkers (mypy, pyright, Pylance) trust the inline annotations. 89 of 120 package files carry return-type hints — without `py.typed` this typing investment is invisible to users.

**Failing-test step.** Extend `tests/test_packaging_metadata.py`:

```python
def test_py_typed_marker_exists() -> None:
    """PEP 561: py.typed marker must be inside the package directory."""
    marker = REPO_ROOT / "python" / "pydocs_mcp" / "py.typed"
    assert marker.exists(), (
        "py.typed missing — downstream type-checkers will ignore inline "
        "annotations"
    )


def test_py_typed_is_in_maturin_include() -> None:
    """maturin must bundle py.typed in the wheel, not just the source tree."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    include = pyproject["tool"]["maturin"]["include"]
    assert "python/pydocs_mcp/py.typed" in include
```

Run; both FAIL.

**Implementation step.**

1. Create the marker file (empty):

   ```bash
   touch python/pydocs_mcp/py.typed
   ```

2. Edit `pyproject.toml` `tool.maturin.include`:

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

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add python/pydocs_mcp/py.typed pyproject.toml tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
build(typing): ship py.typed marker for PEP 561 — P1-2

89 of 120 package files carry return-type annotations, but without a
py.typed marker downstream type-checkers (mypy, pyright, Pylance)
silently ignored every one of them. Add the empty marker inside the
package and bundle it in the wheel via tool.maturin.include.
COMMIT
)"
```

---

## Task 6 — `PydocsMCPError` root + reparent existing exceptions (P2-4)

**Goal.** Create a single `PydocsMCPError` base in a new top-level `python/pydocs_mcp/exceptions.py` and reparent every existing custom exception (`MCPToolError`, `UnitOfWorkNotEnteredError`, `PipelineLoadError`) so callers can `except PydocsMCPError` to catch any library-originated failure. Preserves existing `ValueError` / `RuntimeError` lineage via multiple inheritance.

**Failing-test step.** Add `tests/test_exception_hierarchy.py`:

```python
"""All custom pydocs-mcp exceptions must descend from PydocsMCPError."""
from __future__ import annotations

import pytest


def test_pydocs_mcp_error_exists_and_inherits_exception() -> None:
    from pydocs_mcp.exceptions import PydocsMCPError

    assert issubclass(PydocsMCPError, Exception)


def test_mcp_tool_error_inherits_pydocs_mcp_error() -> None:
    from pydocs_mcp.application.mcp_errors import MCPToolError
    from pydocs_mcp.exceptions import PydocsMCPError

    assert issubclass(MCPToolError, PydocsMCPError)


def test_invalid_argument_not_found_service_unavailable_inherit() -> None:
    from pydocs_mcp.application.mcp_errors import (
        InvalidArgumentError,
        NotFoundError,
        ServiceUnavailableError,
    )
    from pydocs_mcp.exceptions import PydocsMCPError

    for cls in (InvalidArgumentError, NotFoundError, ServiceUnavailableError):
        assert issubclass(cls, PydocsMCPError), cls


def test_uow_not_entered_inherits_both_roots() -> None:
    """Preserves RuntimeError lineage so existing `except RuntimeError`
    callers keep working, while adding the new library-wide handle."""
    from pydocs_mcp.exceptions import PydocsMCPError
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError

    assert issubclass(UnitOfWorkNotEnteredError, PydocsMCPError)
    assert issubclass(UnitOfWorkNotEnteredError, RuntimeError)


def test_pipeline_load_error_inherits_both_roots() -> None:
    """Preserves ValueError lineage."""
    from pydocs_mcp.exceptions import PydocsMCPError
    from pydocs_mcp.retrieval.pipeline.code_pipeline import PipelineLoadError

    assert issubclass(PipelineLoadError, PydocsMCPError)
    assert issubclass(PipelineLoadError, ValueError)


def test_catch_all_works_at_call_site() -> None:
    """The catch-all handle the new root unlocks for embedders."""
    from pydocs_mcp.application.mcp_errors import NotFoundError
    from pydocs_mcp.exceptions import PydocsMCPError

    with pytest.raises(PydocsMCPError):
        raise NotFoundError("ghost")
```

Run; ALL FAIL (no `pydocs_mcp.exceptions` module).

**Implementation step.**

1. Create `python/pydocs_mcp/exceptions.py`:

   ```python
   """Public exception hierarchy.

   All exceptions raised by pydocs-mcp inherit from :class:`PydocsMCPError`,
   so embedders can ``except PydocsMCPError`` to catch any library-originated
   failure without swallowing unrelated bugs.
   """
   from __future__ import annotations


   class PydocsMCPError(Exception):
       """Base class for all exceptions raised by pydocs-mcp."""
   ```

2. Reparent `python/pydocs_mcp/application/mcp_errors.py`:

   ```python
   # Before
   class MCPToolError(Exception):
       """Base — every handler-raised error inherits from this."""

   # After
   from pydocs_mcp.exceptions import PydocsMCPError

   class MCPToolError(PydocsMCPError):
       """Base — every handler-raised error inherits from this."""
   ```

   (Subclasses `InvalidArgumentError` / `NotFoundError` / `ServiceUnavailableError` continue to inherit from `MCPToolError`, so they pick up `PydocsMCPError` transitively.)

3. Reparent `python/pydocs_mcp/storage/errors.py`:

   ```python
   # Before
   class UnitOfWorkNotEnteredError(RuntimeError):

   # After
   from pydocs_mcp.exceptions import PydocsMCPError

   class UnitOfWorkNotEnteredError(PydocsMCPError, RuntimeError):
   ```

4. Reparent `PipelineLoadError` in `python/pydocs_mcp/retrieval/pipeline/code_pipeline.py`:

   ```python
   # Before
   class PipelineLoadError(ValueError):

   # After (add import at top of module if not present)
   from pydocs_mcp.exceptions import PydocsMCPError

   class PipelineLoadError(PydocsMCPError, ValueError):
   ```

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_exception_hierarchy.py
.venv/bin/python -m pytest -q tests/                # full suite still green
.venv/bin/python -m ruff check python/ tests/
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add python/pydocs_mcp/exceptions.py \
         python/pydocs_mcp/application/mcp_errors.py \
         python/pydocs_mcp/storage/errors.py \
         python/pydocs_mcp/retrieval/pipeline/code_pipeline.py \
         tests/test_exception_hierarchy.py
git commit -m "$(cat <<'COMMIT'
feat(exceptions): introduce PydocsMCPError root — P2-4

The project shipped several nicely-scoped custom exceptions
(MCPToolError, UnitOfWorkNotEnteredError, PipelineLoadError, etc.) but
no common library base, so embedders couldn't catch any pydocs-mcp
failure with a single `except`. Add PydocsMCPError as the new root and
reparent the three existing root-level customs via multiple inheritance
(preserves ValueError / RuntimeError lineage where it existed).
COMMIT
)"
```

---

## Task 7 — Public API surface (`__all__`) (P1-6)

**Goal.** Extend `python/pydocs_mcp/__init__.py` to re-export the documented public symbols (the new exception hierarchy) and declare `__all__`. Gives external integrators, type-checkers, and `from pydocs_mcp import *` a stable signal for what's public.

**Failing-test step.** Add `tests/test_public_api.py`:

```python
"""Public API surface — `__all__` lists the documented entry points."""
from __future__ import annotations

import pydocs_mcp


def test_dunder_all_declared() -> None:
    assert hasattr(pydocs_mcp, "__all__"), "pydocs_mcp must declare __all__"


def test_dunder_all_contains_version_and_exception_root() -> None:
    expected = {
        "__version__",
        "PydocsMCPError",
        "MCPToolError",
        "InvalidArgumentError",
        "NotFoundError",
        "ServiceUnavailableError",
    }
    assert expected.issubset(set(pydocs_mcp.__all__))


def test_every_name_in_all_is_importable_from_package() -> None:
    """Every symbol in __all__ must actually be accessible as
    pydocs_mcp.<name> — guards against typos that pass ruff."""
    for name in pydocs_mcp.__all__:
        assert hasattr(pydocs_mcp, name), f"pydocs_mcp.{name} not exported"
```

Run; FAILS (`__all__` not declared).

**Implementation step.** Extend `python/pydocs_mcp/__init__.py` on top of the Task 3 rewrite:

```python
"""pydocs-mcp — Local Python docs MCP server, accelerated with Rust."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pydocs-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

# Public exception hierarchy — exposed at the top level so embedders can
# `except pydocs_mcp.PydocsMCPError` for a catch-all, or one of the
# narrower MCP-handler-shaped errors for specific control flow.
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.exceptions import PydocsMCPError

__all__ = [
    "__version__",
    "PydocsMCPError",
    "MCPToolError",
    "InvalidArgumentError",
    "NotFoundError",
    "ServiceUnavailableError",
]
```

**Decision recorded:** the spec mentions optionally re-exporting `DocsService` / `IndexingService`. Leaving those out of `__all__` for this PR — they're not documented as public façades, and the test gate forces `__all__` symbols to be importable, so leaving them off is the safer default. Adding them is a one-line follow-up if integrators ask.

**Verification gate:**

```bash
.venv/bin/python -c "import pydocs_mcp; print(sorted(pydocs_mcp.__all__))"
.venv/bin/python -m pytest -q tests/test_public_api.py
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add python/pydocs_mcp/__init__.py tests/test_public_api.py
git commit -m "$(cat <<'COMMIT'
feat(api): declare __all__ + re-export exception hierarchy — P1-6

Before: __init__.py exported only __version__, leaving downstream
integrators (and AI coding assistants) without a stable signal for the
public surface. After: __all__ pins __version__ plus the five public
exception classes (PydocsMCPError + four MCP-handler-shaped subclasses).
Re-exporting via the top-level namespace also fixes `from pydocs_mcp
import *` and Pyright's exported-names inference.

Service facades (DocsService, IndexingService) stay implicit for now;
they can land as an explicit follow-up when there's integrator demand.
COMMIT
)"
```

---

## Task 8 — PEP 735 dependency groups (P1-5)

**Goal.** Move `dev = [...]` out of `[project.optional-dependencies]` into `[dependency-groups]` (PEP 735). Keep `[project.optional-dependencies]` only for user-facing extras (today: `watch`). Update CI's install step.

**Failing-test step.** Extend `tests/test_packaging_metadata.py`:

```python
def test_dev_deps_live_in_dependency_groups() -> None:
    """PEP 735: dev tooling should not ship in wheel METADATA's Provides-Extra."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependency_groups = pyproject.get("dependency-groups", {})
    assert "test" in dependency_groups
    assert "lint" in dependency_groups
    assert "dev" in dependency_groups
    # `dev` should compose `test` + `lint`, not redeclare them.
    dev_entries = dependency_groups["dev"]
    assert {"include-group": "test"} in dev_entries
    assert {"include-group": "lint"} in dev_entries


def test_user_facing_extras_dont_include_dev() -> None:
    """[project.optional-dependencies] must not advertise dev tooling."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pyproject.get("project", {}).get("optional-dependencies", {})
    assert "dev" not in extras, (
        "dev tooling should live in [dependency-groups], not "
        "[project.optional-dependencies]"
    )
```

Run; FAIL.

**Implementation step.**

1. Edit `pyproject.toml`:

   ```toml
   # Before
   [project.optional-dependencies]
   dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff", "pytest-asyncio>=0.23"]
   watch = ["watchdog>=4.0,<6.0"]

   # After
   [project.optional-dependencies]
   watch = ["watchdog>=4.0,<6.0"]

   [dependency-groups]
   test = [
       "pytest >= 7",
       "pytest-cov >= 4",
       "pytest-asyncio >= 0.23",
   ]
   lint = [
       "ruff",
       "mypy >= 1.10",
   ]
   dev = [
       {include-group = "test"},
       {include-group = "lint"},
   ]
   ```

2. Edit `.github/workflows/ci.yml` — replace the dev install step. EXISTING uses `pip install -e ".[dev]"`. NEW (pip 25.1+ supports `--group`; if CI's pip is older, the fallback uses `uv`):

   ```yaml
   # Before
   - name: Install project and dev deps
     run: pip install -e ".[dev]"

   # After
   - name: Install project
     run: pip install -e .

   - name: Install dev dependency group (PEP 735)
     run: |
       python -m pip install --upgrade "pip>=25.1"
       pip install --group dev
   ```

3. Smoke-check locally that `pip install --group dev` resolves (or document the `uv sync --group dev` alternative if pip is too old in this worktree's `.venv`).

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add pyproject.toml .github/workflows/ci.yml tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
build(deps): migrate dev tools to PEP 735 dependency-groups — P1-5

Dev tooling (pytest, pytest-cov, pytest-asyncio, ruff, mypy) previously
lived in [project.optional-dependencies].dev, which (1) leaked into
wheel METADATA's Provides-Extra and (2) let `pip install
pydocs-mcp[dev]` quietly install pytest on user machines. Move them to
[dependency-groups] (test / lint / dev composing both); user-facing
extras keep `watch`. CI install step switches to `pip install --group
dev` (pip 25.1+).
COMMIT
)"
```

---

## Task 9 — Ruff: bump target, expand `select`, fix violations (P2-2 + P2-3)

**Goal.** Bump `[tool.ruff].target-version` from `py310` → `py311` to match `requires-python`, AND expand `[tool.ruff.lint].select` from `["E", "F", "W", "I"]` to include `B / UP / S / SIM / RUF / C901 / PT / PTH`. Adds bug-bear, pyupgrade, bandit-security, simplification, ruff-specific, McCabe complexity, pytest-style, and pathlib rules. EXPECT autofixes from `ruff check --fix` + a manual fixup pass.

**Failing-test step.** Optional — the existing CI `Lint` step IS the failing test. Skip dedicated test cases here; the gate is `ruff check` exits 0.

**Implementation step.**

1. Edit `pyproject.toml`:

   ```toml
   # Before
   [tool.ruff]
   line-length = 100
   target-version = "py310"

   [tool.ruff.lint]
   select = ["E", "F", "W", "I"]
   ignore = ["E501", "E701"]

   [tool.ruff.lint.per-file-ignores]
   "tests/**" = ["F401", "F811", "F841", "E402", "I001"]
   "python/pydocs_mcp/_fast.py" = ["F401", "I001"]

   # After
   [tool.ruff]
   line-length = 100
   target-version = "py311"

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
       "C901",       # McCabe complexity
       "PT",         # flake8-pytest-style
       "PTH",        # flake8-use-pathlib
   ]
   ignore = ["E501", "E701"]

   [tool.ruff.lint.mccabe]
   max-complexity = 12

   [tool.ruff.lint.per-file-ignores]
   "tests/**"                      = ["F401", "F811", "F841", "E402", "I001", "S101", "S105", "S106", "PT011", "PTH"]
   "python/pydocs_mcp/_fast.py"    = ["F401", "I001"]
   "python/pydocs_mcp/__main__.py" = ["T201"]   # CLI is allowed to print
   "benchmarks/**"                 = ["S101", "PTH"]
   ```

   (The `tests/**` block extends the existing ignores with: `S101` for `assert`, `S105/S106` for hardcoded-password literals in fixtures, `PT011` for `pytest.raises(Exception)` patterns in narrow cases, and `PTH` because some test fixtures still use `os.path`. Trim once you've audited the diff.)

2. Run autofixes:

   ```bash
   .venv/bin/python -m ruff check --fix python/ tests/ benchmarks/
   .venv/bin/python -m ruff format python/ tests/ benchmarks/
   ```

3. Review remaining violations one by one. Typical residue:
   - `B008` (function call in default) → may need `# noqa: B008` for FastMCP `Field(...)` defaults.
   - `S603 / S607` (`subprocess` calls) → audit each site; most are fine in CLI / build scripts.
   - `C901` (too complex) → may bump `max-complexity = 14` temporarily and add `# TODO(complexity): refactor — see CLAUDE.md §SOLID` notes, OR refactor inline. Lean: bump if needed; refactor in a follow-up.
   - `PTH` migration churn — `os.path.join(...)` → `Path(...)`. Mostly mechanical.

4. If a fix would regress test behavior, prefer adding a narrow `# noqa: <RULE>` with a one-line justification (CLAUDE.md §"Code Comments": comment explains WHY).

**Verification gate:**

```bash
.venv/bin/python -m ruff check python/ tests/ benchmarks/
# Expect: 0 violations.
.venv/bin/python -m pytest -q tests/
# Expect: still 1367+ passed (autofixes mustn't change behavior).
```

**Commit (NO `Co-Authored-By:`) — note: this commit may be large.** If the diff is unwieldy (>500 lines), split into two commits:

- Commit 9a: `pyproject.toml` config change + the autofix output.
- Commit 9b: any manual cleanup.

Use one commit if the diff is reasonable. Commit message:

```bash
git add pyproject.toml python/ tests/ benchmarks/
git commit -m "$(cat <<'COMMIT'
chore(ruff): bump target to py311 + expand rule selection — P2-2 + P2-3

Previously `target-version = "py310"` while requires-python was 3.11+;
ruff was missing UP rules for 3.11 idioms and accepting 3.10-only
patterns. Bump to py311.

Expand `select` from {E, F, W, I} (pycodestyle/Pyflakes/isort only) to
include B (bugbear), UP (pyupgrade), S (bandit security), SIM (simplify),
RUF (ruff-specific), C901 (McCabe), PT (pytest-style), PTH
(pathlib). Each added at zero runtime cost; catches bug-prone patterns
and security smells that the smaller set missed.

The bulk of the diff is `ruff check --fix` autofixes — mechanical
upgrades, no behavior change. The full test suite stays green.

per-file-ignores extended for tests/** (S101 assert, S105/S106 password
literals), __main__.py (T201 print in CLI), and benchmarks/**.
COMMIT
)"
```

---

## Task 10 — mypy config + CI step (P1-3)

**Goal.** Add `[tool.mypy]` to `pyproject.toml` and a `Typecheck` step to `ci.yml`. Start lenient (`disallow_untyped_defs = false`) so this PR doesn't balloon into a typing-cleanup PR. Document the ratchet plan.

**Failing-test step.** Run mypy locally — it WILL surface errors:

```bash
.venv/bin/python -m pip install "mypy>=1.10"
.venv/bin/python -m mypy python/pydocs_mcp
```

Expected: some errors. Triage them in the implementation step.

**Implementation step.**

1. Add `[tool.mypy]` to `pyproject.toml`:

   ```toml
   [tool.mypy]
   # Lenient floor — flip disallow_untyped_defs to true once the codebase
   # reaches that bar. Ratchet plan: run `mypy --strict python/pydocs_mcp/
   # > strict_baseline.txt`, commit the baseline, then fail CI only on
   # net-new errors over the baseline. Shrink the baseline over time.
   python_version = "3.11"
   files = ["python/pydocs_mcp"]
   warn_unused_configs = true
   warn_redundant_casts = true
   warn_unused_ignores = true
   warn_return_any = true
   no_implicit_optional = true
   disallow_untyped_defs = false

   [[tool.mypy.overrides]]
   # Tests + benchmarks aren't part of the published surface; lighter bar.
   module = ["tests.*", "benchmarks.*"]
   ignore_errors = true

   [[tool.mypy.overrides]]
   # _fast.py and _fallback.py form the Rust/Python substitution boundary;
   # _fast.py's `from ._native import *` triggers attr-defined noise when
   # the native module isn't compiled.
   module = ["pydocs_mcp._fast", "pydocs_mcp._fallback", "pydocs_mcp._native"]
   ignore_errors = true

   [[tool.mypy.overrides]]
   # Third-party libs that don't ship type stubs.
   module = ["turbovec.*", "fastembed.*"]
   ignore_missing_imports = true
   ```

2. Triage the residual errors. Typical fixes:
   - Narrow `# type: ignore[<error-code>]` with a one-line comment explaining WHY.
   - Add return-type annotations where mypy says they're missing AND the function is called from typed code (low-friction wins).
   - Add `from __future__ import annotations` where it's missing and required for PEP 604 `X | Y` on 3.11.

3. Edit `.github/workflows/ci.yml` — add a typecheck step after `Lint`:

   ```yaml
   - name: Typecheck (mypy)
     run: mypy python/pydocs_mcp
   ```

   (mypy is already in the `lint` dep-group from Task 8, so no separate install.)

**Verification gate:**

```bash
.venv/bin/python -m mypy python/pydocs_mcp
# Expect: 0 errors.
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add pyproject.toml .github/workflows/ci.yml python/
# Plus whatever narrow `# type: ignore[...]` lines you had to add.
git commit -m "$(cat <<'COMMIT'
build(typing): add mypy config + CI typecheck step — P1-3

The package has heavy type-hint coverage (~74% of files) but no static
checker ran against it; annotations could drift silently and the
downstream IDE experience would erode. Configure mypy in pyproject.toml
with a lenient floor (disallow_untyped_defs = false) so this lands
without a giant cleanup PR, and add a `Typecheck` step to ci.yml.

Overrides: tests + benchmarks ignore errors (not user-facing);
_fast.py / _fallback.py / _native are the Rust/Python substitution
boundary and ignore errors there; turbovec + fastembed are bundled
under ignore_missing_imports (no stubs upstream).

Ratchet plan documented in the [tool.mypy] block: build a strict
baseline, commit it, fail CI only on net-new strict errors, shrink the
baseline over time.
COMMIT
)"
```

---

## Task 11 — Multi-OS CI matrix (P1-4)

**Goal.** Add `macos-13`, `macos-14` (arm64), and `windows-latest` to the test matrix in `ci.yml`. Gate the libopenblas install on Linux only. EXPECT some Win/macOS-specific test failures and `pytest.mark.skipif` decorators.

**Failing-test step.** None pre-merge — the CI run itself is the test. Local verification:

```bash
# On macOS host (this worktree):
.venv/bin/python -m pytest -q tests/
# Should pass — but watch for macOS-specific shimmers.
```

**Implementation step.**

1. Edit `.github/workflows/ci.yml` — replace the python job's `strategy` and surrounding steps:

   ```yaml
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
         # macOS uses Accelerate (CBLAS bundled); Windows uses the MSVC
         # runtime CBLAS. INSTALL.md documents both paths.
         - name: Install OpenBLAS (Linux only)
           if: runner.os == 'Linux'
           run: |
             sudo apt-get update
             sudo apt-get install -y libopenblas-pthread-dev liblapack-dev
             sudo update-alternatives --set libblas.so.3-x86_64-linux-gnu \
               /usr/lib/x86_64-linux-gnu/openblas-pthread/libblas.so.3 || true

         - name: Install project
           run: pip install -e .

         - name: Install dev dependency group (PEP 735)
           run: |
             python -m pip install --upgrade "pip>=25.1"
             pip install --group dev

         - name: Lint
           run: ruff check python/ tests/

         - name: Typecheck (mypy)
           run: mypy python/pydocs_mcp

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

   Note: the existing `Diagnose BLAS availability` step from the current `ci.yml` is Linux-only diagnostic noise; gate it with `if: runner.os == 'Linux'` (keep or drop — recommend dropping in a follow-up commit once Linux CI is reliably green).

2. Post-push, watch the CI run on the PR. Triage genuinely platform-specific failures:

   - **Windows path separators** in tests that hardcode `/` — convert to `pathlib.Path` or add `os.sep` shims.
   - **fork-based multiprocessing** tests — `@pytest.mark.skipif(sys.platform == "win32", reason="fork unavailable on Windows")`.
   - **fastembed cache-dir** assumptions that hit `$HOME/.cache/...` — skip or parametrize the cache root.
   - **SQLite file-locking** semantics on Windows — usually fine but watch concurrent-write tests.

   The strategy: keep the matrix entries green by `skipif`-decorating tests that are genuinely platform-bound, NOT by dropping a matrix entry. Document each `skipif` with a one-line comment explaining WHY (CLAUDE.md §"Code Comments").

3. If a fixup commit is needed for `skipif` decorations:

   ```bash
   git commit -m "$(cat <<'COMMIT'
   fix(tests): skip POSIX-only tests on Windows under multi-OS CI

   Follow-up to the multi-OS matrix landing: a handful of tests use
   POSIX-specific primitives (os.fork, shell-quoted commands, fastembed
   cache paths). Decorate them with @pytest.mark.skipif(...) and a
   one-line WHY comment.
   COMMIT
   )"
   ```

**Verification gate:**

```bash
# Local (macOS, the worktree host):
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/

# Remote:
# Push the branch and inspect the Actions tab. The full matrix
# (12 combinations: 4 OS x 3 Python) should turn green before merge.
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add .github/workflows/ci.yml
git commit -m "$(cat <<'COMMIT'
ci(matrix): test on macOS + Windows in addition to Linux — P1-4

release.yml already built wheels for Linux x86_64+aarch64, macOS
x86_64+arm64, and Windows x64 — but ci.yml only tested on
ubuntu-latest. The asymmetry between what we built and what we tested
was the most common cause of "released wheel doesn't work on Windows"
bugs in PyO3 / maturin projects. Add macos-13, macos-14 (arm64), and
windows-latest to the test matrix; gate the libopenblas install on
Linux only (macOS/Windows ship CBLAS via Accelerate / MSVC runtime).

Platform-specific test failures will be addressed by @pytest.mark.skipif
follow-ups, not by dropping matrix entries.
COMMIT
)"
```

---

## Task 12 — Dev tooling bundle: pre-commit + Makefile + .editorconfig + CHANGELOG (P2-1, P2-5, P2-6, P2-7)

**Goal.** Drop the four canonical dev-tooling files. No behavior change to the project's code; pure repo-hygiene.

**Decision recorded:** the four files are independent, but they're tiny boilerplate and ship together in a single commit. Splitting would be 4 commits of `Add foo.txt`. Lean: one bundled commit.

**Failing-test step (file-presence assertions).** Extend `tests/test_packaging_metadata.py`:

```python
import yaml


def test_changelog_exists_with_unreleased_section() -> None:
    changelog = REPO_ROOT / "CHANGELOG.md"
    assert changelog.exists(), "CHANGELOG.md missing at repo root"
    text = changelog.read_text(encoding="utf-8")
    assert "Keep a Changelog" in text or "Unreleased" in text


def test_pre_commit_config_exists_and_lists_ruff() -> None:
    cfg = REPO_ROOT / ".pre-commit-config.yaml"
    assert cfg.exists(), ".pre-commit-config.yaml missing"
    data = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    repos = [r["repo"] for r in data.get("repos", [])]
    assert any("ruff" in r for r in repos)


def test_makefile_exists_with_canonical_targets() -> None:
    mk = REPO_ROOT / "Makefile"
    assert mk.exists(), "Makefile missing"
    text = mk.read_text(encoding="utf-8")
    for target in ("install:", "test:", "lint:", "format:", "typecheck:", "clean:"):
        assert target in text, f"Makefile missing target {target!r}"


def test_editorconfig_exists() -> None:
    cfg = REPO_ROOT / ".editorconfig"
    assert cfg.exists(), ".editorconfig missing"
    assert "root = true" in cfg.read_text(encoding="utf-8")
```

Run; all FAIL.

**Implementation step.**

1. `CHANGELOG.md` — Keep-a-Changelog format. Backfill `0.1.0` and `0.2.0` minimally from git tags / GitHub releases:

   ```markdown
   # Changelog

   All notable changes to this project will be documented in this file.

   The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
   and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

   ## [Unreleased]

   ### Added
   - Top-level `LICENSE` file (MIT).
   - `py.typed` marker so PEP 561 type-checkers consume inline annotations.
   - `PydocsMCPError` root exception; existing custom exceptions reparented.
   - Public `__all__` in `pydocs_mcp/__init__.py` covering the exception
     hierarchy and `__version__`.
   - `mypy` configuration + CI step.
   - Multi-OS CI matrix (Linux + macOS + Windows).
   - `CHANGELOG.md`, `.pre-commit-config.yaml`, `Makefile`, `.editorconfig`.

   ### Changed
   - `__version__` now read from `importlib.metadata` (eliminates drift
     between `pyproject.toml` and `__init__.py`).
   - License declared as PEP 639 SPDX expression (`license = "MIT"` +
     `license-files`) instead of the legacy table form.
   - Dev tooling moved from `[project.optional-dependencies].dev` to
     PEP 735 `[dependency-groups]`.
   - Ruff `target-version` bumped from `py310` to `py311`; rule selection
     expanded to include B, UP, S, SIM, RUF, C901, PT, PTH.

   ## [0.2.0] — 2026-05-27

   Hybrid retrieval, reference graph, LLM tree reasoning. Refer to git
   history for the full set of merged changes.

   ## [0.1.0] — 2026-04-11

   Initial release: BM25-only retrieval via SQLite FTS5, package
   indexing, MCP `search` + `lookup` tools.
   ```

2. `.pre-commit-config.yaml`:

   ```yaml
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

3. `Makefile` (TABS, not spaces — the spec emphasizes this):

   ```makefile
   .PHONY: install test test-rust lint lint-rust format typecheck build clean

   install:
   	pip install -e .
   	pip install --group dev
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

4. `.editorconfig`:

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

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
# Smoke-check Makefile
make lint
# Smoke-check pre-commit (optional)
# pre-commit run --all-files
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add CHANGELOG.md .pre-commit-config.yaml Makefile .editorconfig tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
chore(devtools): add CHANGELOG, pre-commit, Makefile, .editorconfig
  — P2-1 + P2-5 + P2-6 + P2-7

Bundle the four canonical repo-hygiene files. No behavior change.

* CHANGELOG.md — Keep-a-Changelog skeleton with backfilled 0.1.0 / 0.2.0
  and the [Unreleased] block populated by this PR's user-visible
  changes.
* .pre-commit-config.yaml — whitespace + ruff + cargo fmt hooks; saves
  CI roundtrips on the cheap stuff.
* Makefile — standard `install / test / lint / format / typecheck /
  build / clean` targets, with `test-rust` + `lint-rust` for the
  Rust side.
* .editorconfig — consistent indent, LF, final-newline policy. Matters
  more now that CI runs on Windows (CRLF risk).
COMMIT
)"
```

---

## Task 13 — Lockfile (`uv.lock`) (P2-8)

**Goal.** Commit a `uv.lock` for reproducible installs and switch CI to consume it. Marked **medium effort** in the spec; this task documents what to do AND when to defer.

**Decision recorded:** If `uv` is not viable in the worktree's `.venv` (e.g., the host hasn't installed `uv` and adding it is friction) OR if Task 11 (multi-OS CI) is still smoking out platform-specific failures, defer this task — convert to a tracked follow-up issue and proceed straight to Task 14. The project already pins tight upper bounds on every runtime dep (`turbovec>=0.5,<1.0`, `fastembed>=0.4,<1.0`, `openai>=1.40,<2.0`, `jinja2>=3.0,<4.0`), so the churn risk is bounded.

**Failing-test step.** Extend `tests/test_packaging_metadata.py`:

```python
def test_uv_lock_exists() -> None:
    """uv.lock pins the dependency resolution for reproducible installs."""
    lockfile = REPO_ROOT / "uv.lock"
    assert lockfile.exists(), "uv.lock missing — run `uv lock` and commit it"
```

(Or skip with `pytest.skip("uv.lock deferred to follow-up")` if executing the defer branch.)

**Implementation step (only if not deferred).**

1. Install `uv`:

   ```bash
   .venv/bin/python -m pip install uv
   ```

2. Generate the lockfile:

   ```bash
   .venv/bin/python -m uv lock
   ```

3. Stage `uv.lock`. Smoke-check that it's deterministic by re-running `uv lock` and confirming no diff.

4. Update CI to consume the lockfile:

   ```yaml
   # .github/workflows/ci.yml — replace the install steps from Task 8/11
   - uses: astral-sh/setup-uv@v3
   - name: Install project (frozen, reproducible)
     run: |
       uv sync --frozen --group dev
       uv pip install -e .
   ```

**Verification gate:**

```bash
.venv/bin/python -m pytest -q tests/test_packaging_metadata.py
.venv/bin/python -m pytest -q tests/
.venv/bin/python -m ruff check python/ tests/
# Smoke-check the lockfile is reproducible:
.venv/bin/python -m uv lock --check    # exits 0 = no drift
```

**Commit (NO `Co-Authored-By:`):**

```bash
git add uv.lock .github/workflows/ci.yml tests/test_packaging_metadata.py
git commit -m "$(cat <<'COMMIT'
build(deps): commit uv.lock + switch CI to frozen install — P2-8

CI previously did `pip install -e ".[dev]"` and resolved transitive
dependencies fresh on every run, so a transitive patch release could
silently redden CI without any local change. Generate `uv.lock` (uv
resolves the full transitive set), commit it, and have CI consume it
via `uv sync --frozen`.

The project already pins tight upper bounds on every direct runtime
dep, which kept the churn risk manageable pre-lock; the lockfile makes
the resolution fully reproducible per commit.
COMMIT
)"
```

**If deferring:** instead of the above, drop a one-line commit recording the decision:

```bash
git commit --allow-empty -m "$(cat <<'COMMIT'
docs(audit): defer uv.lock to follow-up — P2-8

Tasks 8-11 surfaced enough multi-OS CI iteration that bolting on uv.lock
in the same PR risks coupling unrelated failures. The project already
pins tight upper bounds on every runtime dep, so churn risk is bounded.
Tracked as a follow-up; see docs/superpowers/plans/2026-05-28-library-
audit-fixes.md Task 13.
COMMIT
)"
```

---

## Task 14 — Verification gauntlet + AC walk

**Goal.** Final defense before opening the PR: run the full test + lint + typecheck + Rust suite, walk every finding in `docs/superpowers/specs/2026-05-28-library-audit-design.md`, and confirm each landed.

**Steps:**

1. **Python suite:**

   ```bash
   .venv/bin/python -m pytest -q tests/
   # Expect: at least 1367 + (new tests from Tasks 2, 3, 4, 5, 6, 7, 8, 12) passed.
   # No regressions vs baseline.
   ```

2. **Benchmark suite (per CLAUDE.md):**

   ```bash
   PYTHONPATH=benchmarks/src .venv/bin/python -m pytest benchmarks/tests/ -q
   # Expect: 283 passed.
   ```

3. **Lint:**

   ```bash
   .venv/bin/python -m ruff check python/ tests/ benchmarks/
   # Expect: 0 violations.
   .venv/bin/python -m ruff format --check python/ tests/ benchmarks/
   # Expect: clean.
   ```

4. **Typecheck:**

   ```bash
   .venv/bin/python -m mypy python/pydocs_mcp
   # Expect: 0 errors.
   ```

5. **Rust:**

   ```bash
   cargo fmt --check
   cargo clippy -- -D warnings
   cargo test
   # Expect: clean. (No Rust changes in this PR, so the Rust suite
   # should be byte-identical to main.)
   ```

6. **Smoke-test the wheel build (manual):**

   ```bash
   .venv/bin/python -m maturin build --release
   python -m zipfile -l target/wheels/pydocs_mcp-*.whl | grep -E "(LICENSE|py.typed)"
   # Expect: LICENSE present, py.typed present.
   ```

7. **AC matrix walk.** Open `docs/superpowers/specs/2026-05-28-library-audit-design.md` side-by-side with `git log --oneline main..HEAD` and check each P0/P1/P2 finding against the commit list using the matrix at the top of this plan. Any unticked finding = NOT done; either complete it or move to a follow-up issue with explicit justification in the PR description.

8. **Internal-jargon audit** (per CLAUDE.md §"README files: no internal PR / sub-PR / task jargon"). User-facing artifacts in this PR: `CHANGELOG.md` and any new README content. Run:

   ```bash
   grep -nE "PR #[0-9]+|sub-PR|trilogy|Task [0-9]+ of|PR-[A-Z][0-9.]+" \
     CHANGELOG.md README.md 2>/dev/null
   # Expect: zero matches in CHANGELOG.md. (README.md isn't touched
   # by this PR, but worth a confirm.)
   ```

9. **Authorship audit** (per global rule):

   ```bash
   git log --pretty=format:"%h %an <%ae>" main..HEAD | sort -u
   # Expect: every line shows `msobroza <max.raphael@gmail.com>`.
   # If any commit shows a Co-Authored-By trailer:
   git log --grep="Co-Authored-By" main..HEAD
   # MUST return nothing. If it does, STOP and ask the user before
   # any history rewrite — see global rule.
   ```

10. **Open the PR.** Title: `library-audit: 2 P0 + 6 P1 + 8 P2 fixes`. Body:
    - Summary: list the 16 findings with their resolutions (refer to the AC matrix above).
    - Test plan: paste the verification gauntlet output.
    - Risks: P2-3 ruff churn, P1-4 multi-OS CI, P1-3 mypy lenient floor.
    - Follow-ups: P2-8 deferral (if applicable), DocsService/IndexingService in `__all__`, strict-mypy ratchet.

**Commit:** none — Task 14 is verification, not implementation. If any AC turns up gaps, loop back to its task and add a fixup commit there.

---

## Risk catalog (reference; revisited in each task)

- **R1 — `ruff check --fix` cascade (Task 9):** the expanded `select` set will autofix many lines but leave residue (`B008`, `C901`, narrow `S6xx`). Plan: bump `max-complexity = 14` temporarily if a refactor would balloon the PR; add `# noqa: <RULE>` with a one-line WHY.
- **R2 — mypy lenient → strict drift (Task 10):** the lenient floor lets the PR land without a typing cleanup, but future contributors must NOT regress past it. The `[tool.mypy]` block documents the ratchet plan.
- **R3 — multi-OS CI churn (Task 11):** Windows + macOS may light up genuine platform bugs. Plan: `@pytest.mark.skipif` (with WHY) the genuinely platform-bound tests; do NOT drop a matrix entry.
- **R4 — uv.lock + CI install path (Task 13):** if pip < 25.1 in the CI runner image, the `pip install --group dev` step from Task 8 fails. Mitigation: the `python -m pip install --upgrade "pip>=25.1"` step from Task 8 forces a recent pip first.
- **R5 — `PydocsMCPError` multiple-inheritance MRO (Task 6):** `class UnitOfWorkNotEnteredError(PydocsMCPError, RuntimeError)` — confirm MRO via `Klass.__mro__` in the test. Python's C3 linearization handles this fine when both bases are diamond-free (they are; both descend from `Exception`).

---

## Out of scope (NOT this PR)

- **Strict-mypy ratchet automation** — committed as `[tool.mypy]` comment in Task 10; doing the actual ratchet (writing the baseline, scripting the CI diff) is a follow-up.
- **`DocsService` / `IndexingService` in `__all__`** — Task 7 decision; needs integrator demand first.
- **Removing the `Diagnose BLAS availability` CI step** — drop in a follow-up once Linux CI is reliably green; it's diagnostic-only and not in audit scope.
- **PyPI publishing dry-run** — release-workflow assertions are out of audit scope. The audit confirmed `release.yml` uses Trusted Publishing correctly (scanner false-negative on detection only).
- **README updates** — none of the 16 findings touched README. CHANGELOG covers user-facing communication.
- **Rust changes** — zero. The PR is Python-side packaging hygiene only.
