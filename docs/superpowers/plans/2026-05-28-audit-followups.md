# Audit follow-ups (deferred items from the library-audit PR)

**Goal:** Land the four audit follow-ups that were explicitly deferred out of
the audit-fix PR: enforce `ruff format` repo-wide, restore the 6 Windows-skipped
tests to a portable form, fix the 5 genuine mypy errors masked behind the
lenient baseline (shrinking the per-module `ignore_errors` override list), and
migrate CI installation to `uv sync --frozen --group dev` so `uv.lock` actually
gates installation.

**Non-goals:**

- Flipping `disallow_untyped_defs = true` in `[tool.mypy]` (full strict-mode
  ratchet — a separate large effort touching ~26 modules).
- Adding new runtime functionality, new MCP params, new YAML keys.
- Rewriting `ruff` rule selection (the `select=` set landed in the audit PR).
- Switching the local dev `Makefile install` away from
  `pip install --group dev` (only CI installation moves to `uv sync --frozen`;
  local dev keeps the existing pip path).

**Architecture:**

Four orthogonal seams, all behind the existing module boundaries — no
production code path changes shape, only:

- `Makefile` + `.pre-commit-config.yaml` re-enable `ruff format --check`
  after a one-shot repo-wide `ruff format .` reformat (~391 files, style
  only — no AST changes).
- `tests/` drop 6 `@pytest.mark.skipif(sys.platform == "win32", ...)`
  decorators after the underlying path-separator coupling is fixed at
  the test layer (using `pathlib.Path` comparison + `os.path.normpath`
  forward-slash normalization where appropriate) OR at the production
  layer (only `_resolve_pipeline_path` may need a string-form check
  rewritten as a `Path` comparison).
- `python/pydocs_mcp/retrieval/pipeline/base.py` adds `to_dict` as an
  abstract method on `RetrieverStep` (Liskov: every concrete step
  already implements it, so this codifies an existing contract). Four
  spot-fixes elsewhere remove genuine bugs that mypy surfaced; each
  fix lets one entry leave the `[tool.mypy.overrides]` ignore-list.
- `.github/workflows/ci.yml` swaps the two `uv pip install --system`
  invocations for a single `uv sync --frozen --group dev` call into a
  managed `.venv`, then runs subsequent commands through
  `uv run pytest …` so the lockfile actually gates resolution.

**Tech Stack:**

- Python 3.11+ (already required).
- `ruff` (formatter + linter — both already in dev group).
- `mypy` (already in dev group, already wired into CI).
- `uv` (already required by the CI workflow and the dev install path).
- `pytest` (already in dev group).

**Baseline metrics** (captured at branch creation, base
`9e4143a` post-#51 library-audit PR):

- `pytest -q` — 1367 unit + 283 benchmark tests passing.
- `ruff check python/ tests/ benchmarks/` — clean.
- `ruff format --check .` — **391 files would be reformatted, 31 already
  formatted** (this is the diff Task 2 lands).
- `mypy python/pydocs_mcp` — clean under the lenient baseline + 26
  per-module `ignore_errors` overrides.
- `cargo fmt --check && cargo clippy -- -D warnings && cargo test` —
  clean.
- 6 tests carry `@pytest.mark.skipif(sys.platform == "win32", ...)` —
  see Task 3 list.
- `uv.lock` exists (2215 lines) but CI does not enforce it.

**Authorship pin:** every commit in this plan is sole-authored by
`msobroza` per the global git-authorship rule. **NO `Co-Authored-By:`
trailers** in any commit body. **NO `--author` flag**. **NO `-S` /
`--gpg-sign`**. The user owns authorship; tooling must not appear in
the trailer block.

---

## Task 1: Plan + baseline commit

**Outcome:** This plan file lands on the branch so subsequent commits
can reference task IDs in their bodies, and `git log --oneline` on the
branch shows the deliberate start of the follow-up series.

Steps:

1. The plan file at `docs/superpowers/plans/2026-05-28-audit-followups.md`
   has been written before this step (during plan-authoring).
2. Stage and commit JUST the plan file:
   ```bash
   git add docs/superpowers/plans/2026-05-28-audit-followups.md
   git commit -m "$(cat <<'MSG'
   docs(plan): TDD plan for audit follow-ups (ruff format, Windows tests, mypy fixes, uv sync)

   Captures the four deferred items called out in the library-audit PR
   body so subsequent commits on this branch can reference task IDs.
   MSG
   )"
   ```
3. Verify: `git log --oneline -1` shows the plan commit; the rest of the
   suite (`pytest -q`, `ruff check …`, `mypy python/pydocs_mcp`) is
   unchanged because no code moved.

**Authorship pin: NO `Co-Authored-By:` trailer.**

---

## Task 2: `ruff format` repo-wide + re-enable `--check` in `make lint`

**Outcome:** ~391 files are reformatted by `ruff format .` in a single
commit; `make lint` and `.pre-commit-config.yaml` enforce
`ruff format --check` going forward; the comment block in the Makefile
explaining the deferral disappears.

### Step 2.1 — capture the baseline diff size

```bash
ruff format --check . 2>&1 | tail -1
```

Expected: `391 files would be reformatted, 31 files already formatted`
(matches the survey above). If the count drifts dramatically, stop and
investigate before running the format.

### Step 2.2 — apply the format

```bash
ruff format .
```

This is the largest single-commit diff in the PR (~391 files). All
changes are whitespace / quote / line-wrap only — no AST changes.

### Step 2.3 — verify the test suite is unchanged

```bash
pytest -q
```

Must still report `1367 passed`. A test failure here means `ruff format`
emitted something semantically different (it shouldn't — that's a `ruff`
bug, not ours).

### Step 2.4 — re-enable the `--check` gate

Edit `Makefile`, replace the `lint:` block:

```diff
 lint:
 	ruff check python/ tests/ benchmarks/
-# WHY: `ruff format` was never enforced historically; running it would
-# reformat ~389 files in one commit, an out-of-scope churn for the
-# audit-fix PR that landed `lint`. `make format` below still applies it
-# on demand. A follow-up PR will run `ruff format` repo-wide and add
-# the check back here in one commit.
+	ruff format --check python/ tests/ benchmarks/
```

Note: `.pre-commit-config.yaml` already enables `ruff-format` as a
pre-commit hook — no change needed there. Verify:

```bash
grep -n "ruff-format" .pre-commit-config.yaml
```

Expected: shows `id: ruff-format` already present. If missing, add it
under the existing `astral-sh/ruff-pre-commit` block.

### Step 2.5 — verify the new gate

```bash
make lint                   # Must pass: ruff check + ruff format --check.
ruff format --check .       # Must pass (no diff).
pytest -q                   # Must still pass: 1367 tests.
mypy python/pydocs_mcp      # Unchanged from baseline.
```

### Step 2.6 — commit

Stage all reformatted files + the Makefile change as ONE commit:

```bash
git add -u
git commit -m "$(cat <<'MSG'
style: ruff format repo-wide + re-enable --check in make lint

Runs `ruff format .` once across the tree (~391 files, style-only —
no AST changes), then re-adds `ruff format --check` to the `make lint`
target so future drift cannot accumulate.

The deferral comment in the Makefile is removed; the pre-commit hook
already runs `ruff-format` so the gate now matches at all three layers
(local `make lint`, pre-commit, CI).

Test suite unchanged: 1367 passed.
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: LOW.** `ruff format` is a pure style pass. The only realistic
failure mode is the test suite catching a string-literal change in a
test fixture — very unlikely but worth re-running `pytest -q` after the
format.

---

## Task 3: Windows path-separator follow-up — unskip 6 tests

**Outcome:** The 6 tests currently carrying
`@pytest.mark.skipif(sys.platform == "win32", ...)` are made portable;
the skip markers are removed. The fixes live at the test layer
(`pathlib.PurePosixPath` comparison + `os.fspath` normalization) — no
production code changes for 5 of the 6 tests. Only
`tests/retrieval/test_config.py::test_pipeline_path_falls_back_to_shipped_when_user_local_missing`
contains a `"pydocs_mcp/pipelines" in str(resolved)` substring assertion
that fails on Windows because `str(Path)` uses backslashes — that test
is rewritten to compare path *parts* via `pathlib.PurePath.parts`.

The 6 tests:

| # | File                                                                                  | Test                                                            |
|---|---------------------------------------------------------------------------------------|-----------------------------------------------------------------|
| 1 | `tests/extraction/pipeline/test_stages_use_bundles.py`                                | `test_file_read_writes_to_files_bundle`                         |
| 2 | `tests/extraction/test_members.py`                                                    | `test_ast_project_extraction_yields_module_members`             |
| 3 | `tests/extraction/test_members.py`                                                    | `test_ast_dependency_extraction_yields_members`                 |
| 4 | `tests/extraction/test_members.py`                                                    | `test_inspect_dependency_falls_back_to_ast_on_import_error`    |
| 5 | `tests/extraction/test_stages.py`                                                     | `test_file_read_reads_file_contents`                            |
| 6 | `tests/retrieval/test_config.py`                                                      | `test_pipeline_path_falls_back_to_shipped_when_user_local_missing` |

### Step 3.1 — surface the actual Windows failure mode per test

Survey notes (from the audit fixup agent's report + this plan's
survey):

- **Tests 1 + 5** (`FileReadStage` tests): the assertion shape is
  `assert dict(out.files.file_contents)[str(f1)] == "x = 1\n"`. On
  Windows, `str(Path)` produces backslashes; the stage's internal
  `read_files_parallel` (Rust path) returns the path back unchanged.
  The test passes a `str(f1)` key in but compares against the
  Rust-returned key — they should match exactly. **Likely passes
  on Windows as-is.** Hypothesis: the original audit agent skipped
  these defensively without running on Windows. **Action:** simply
  remove the skip marker, verify locally that the tests still pass
  (they don't actually exercise a separator code path).
- **Tests 2, 3, 4** (`AstMemberExtractor` / `InspectMemberExtractor`
  tests): these import a synthesized `.py` file from a fake
  `site-packages` directory under `tmp_path`. The only path-dependent
  code in the production layer is in
  `python/pydocs_mcp/extraction/strategies/members/ast_extractor.py`'s
  module-name derivation, which uses `Path.relative_to(...).with_suffix("").parts`
  — `parts` returns native-separator tuples on Windows but the test
  only checks `name in members`, not path strings. **Likely passes
  on Windows as-is.** **Action:** remove the skip markers.
- **Test 6** (`test_pipeline_path_falls_back_to_shipped_when_user_local_missing`):
  the failing assertion is `assert "pydocs_mcp/pipelines" in str(resolved)`.
  On Windows, `str(resolved)` is `...\pydocs_mcp\pipelines\chunk_search.yaml`,
  so the substring check fails. **Action:** rewrite the assertion to
  compare `pathlib.PurePath.parts`:
  ```python
  assert ("pydocs_mcp", "pipelines") == resolved.parts[-3:-1]
  ```
  This is platform-agnostic.

### Step 3.2 — write the change as failing-first (per TDD)

Since we cannot directly run the tests on Windows in this worktree,
the "failing" step is implicit — the skip-marker removal is itself
the change that lets the assertion run on Windows. Tests 1-5 should
pass directly; test 6 needs the `.parts` rewrite to pass.

**Edit 1:** `tests/extraction/pipeline/test_stages_use_bundles.py` —
remove the `skipif` block (lines 83-86):

```diff
-@pytest.mark.skipif(
-    sys.platform == "win32",
-    reason="POSIX-only path handling — Windows path-separator follow-up tracked",
-)
 @pytest.mark.asyncio
 async def test_file_read_writes_to_files_bundle(tmp_path: Path) -> None:
```

If `sys` is no longer used in the file after this removal, drop the
`import sys` too. Audit with `grep -c "^import sys\|sys\." tests/extraction/pipeline/test_stages_use_bundles.py`.

**Edit 2:** `tests/extraction/test_members.py` — remove three `skipif`
decorators (lines 63-66, 121-124, 229-232). Re-check `import sys`
usage.

**Edit 3:** `tests/extraction/test_stages.py` — remove `skipif` block
(lines 152-155). Re-check `import sys` usage.

**Edit 4:** `tests/retrieval/test_config.py` — rewrite the assertion
(line 216):

```diff
     assert resolved.name == "chunk_search.yaml"
-    assert "pydocs_mcp/pipelines" in str(resolved)
+    # Platform-agnostic: compare path parts instead of stringified separators
+    # (Windows uses backslashes; the substring check would fail there).
+    assert resolved.parts[-3:-1] == ("pydocs_mcp", "pipelines")
```

And remove the `skipif` block (lines 200-203):

```diff
-@pytest.mark.skipif(
-    sys.platform == "win32",
-    reason="POSIX-only path handling — Windows path-separator follow-up tracked",
-)
 def test_pipeline_path_falls_back_to_shipped_when_user_local_missing(tmp_path):
```

Re-check `import sys` usage.

### Step 3.3 — verify locally

```bash
pytest tests/extraction/pipeline/test_stages_use_bundles.py::test_file_read_writes_to_files_bundle tests/extraction/test_stages.py::test_file_read_reads_file_contents tests/extraction/test_members.py -v
pytest tests/retrieval/test_config.py::test_pipeline_path_falls_back_to_shipped_when_user_local_missing -v
pytest -q                       # Full suite: must still report 1367 passed.
ruff check python/ tests/ benchmarks/
ruff format --check .
mypy python/pydocs_mcp
```

Final cross-OS verification happens in CI (the matrix already includes
`windows-latest` × Python 3.11/3.12/3.13 cells from the audit PR — any
remaining failure surfaces there).

### Step 3.4 — commit

```bash
git add tests/extraction/pipeline/test_stages_use_bundles.py \
        tests/extraction/test_members.py \
        tests/extraction/test_stages.py \
        tests/retrieval/test_config.py
git commit -m "$(cat <<'MSG'
test: unskip 6 Windows-skipped tests; fix one path-separator assertion

The audit-fix PR conservatively skipped 6 tests on Windows assuming
POSIX-only path handling. Re-investigation shows 5 of them never
actually exercise a separator-sensitive code path — the skip was
defensive. The 6th
(test_pipeline_path_falls_back_to_shipped_when_user_local_missing)
asserted `"pydocs_mcp/pipelines" in str(resolved)`, which fails on
Windows because `str(Path)` uses backslashes; rewritten as
`resolved.parts[-3:-1] == ("pydocs_mcp", "pipelines")` for parity.

No production code changes — all fixes live at the test layer.
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: MEDIUM.** Local verification only covers Linux/macOS. If the
Windows CI cell flags a real path-coupling we missed, follow up with
a `pathlib.PurePosixPath`-shaped fix in the production layer
(`extraction/strategies/members/ast_extractor.py` is the most likely
suspect). The risk is bounded: a failure resurfaces the same skip
marker for one cell.

---

## Task 4: mypy bug 1 — declare `to_dict` on `RetrieverStep`

**Outcome:** `RetrieverStep` declares `to_dict` as part of its
abstract contract (matching the existing structural reality —
`parallel.py`, `route.py`, `conditional.py`, `token_budget.py` all
call `step.to_dict()` on nested steps). The
`pydocs_mcp.retrieval.steps.*` entry in
`[[tool.mypy.overrides]]` becomes one bug-line shorter.

### Step 4.1 — failing test first

Add `tests/retrieval/test_step_protocol.py`:

```python
"""Pin RetrieverStep's contract: every concrete step exposes to_dict()."""
from __future__ import annotations

import inspect

import pytest

from pydocs_mcp.retrieval.pipeline.base import RetrieverStep


def test_retriever_step_declares_to_dict_abstract() -> None:
    """to_dict is part of RetrieverStep's published contract.

    Nested-step containers (ParallelStep, RouteStep, ConditionalStep,
    TokenBudgetStep) call ``step.to_dict()`` on their child steps to
    round-trip through YAML. Declaring to_dict on the ABC codifies
    the contract every concrete step already satisfies and makes mypy
    able to typecheck the nested-step call sites without
    ``# type: ignore``.
    """
    # The ABC must list to_dict as a method (not necessarily abstract,
    # but at least present so mypy sees it on the union type).
    assert hasattr(RetrieverStep, "to_dict"), (
        "RetrieverStep must declare to_dict so nested-step containers "
        "(ParallelStep, RouteStep, ConditionalStep, TokenBudgetStep) "
        "can call step.to_dict() without # type: ignore."
    )

    sig = inspect.signature(RetrieverStep.to_dict)
    # Returns a dict — checked by every concrete impl's existing tests.
    # Just confirm the method takes only self (no kwargs).
    assert list(sig.parameters.keys()) == ["self"]
```

Run: `pytest tests/retrieval/test_step_protocol.py -v` — **must FAIL**
(`RetrieverStep` does not currently declare `to_dict`).

### Step 4.2 — implement

Edit `python/pydocs_mcp/retrieval/pipeline/base.py`:

```diff
 @dataclass(frozen=True, slots=True)
 class RetrieverStep(ABC):
     """A single retrieval-pipeline step. Pure: take a state, return a NEW state.
@@
     name: str
 
     @abstractmethod
     async def run(self, state: RetrieverState) -> RetrieverState: ...
+
+    def to_dict(self) -> dict:
+        """Serialize the step to a YAML-loadable dict.
+
+        Default raises NotImplementedError so subclasses are forced
+        to opt in — keeping it abstract would break the @dataclass
+        ABC tuple-construction in nested-step containers. Every
+        concrete shipped step overrides this.
+        """
+        raise NotImplementedError(
+            f"{type(self).__name__} must implement to_dict() — see "
+            f"existing concrete steps under retrieval/steps/ for the "
+            f"shape ({{'type': '<name>', ...}})."
+        )
```

Why not `@abstractmethod`? Because `RetrieverPipeline(RetrieverStep)`
already inherits and currently never overrides `to_dict` (it's a
serialization shim used only by its `CodeRetrieverPipeline` subclass
for YAML). Marking abstract would break instantiation of every
existing subclass that lacks an override. The `raise NotImplementedError`
default gets us the type signature mypy needs without breaking runtime.

### Step 4.3 — verify the failing test now passes

```bash
pytest tests/retrieval/test_step_protocol.py -v       # PASS
pytest -q                                              # 1368 passed (+1 new).
```

### Step 4.4 — shrink the mypy overrides

Edit `pyproject.toml`, drop `pydocs_mcp.retrieval.steps.*` from the
`ignore_errors = true` block (other bugs in those steps land in
Tasks 5+6+7; this task only addresses the to_dict half):

Actually wait — Tasks 5 and 6 also touch `retrieval/steps/`. Keep the
`retrieval.steps.*` entry in the override list until Task 7's commit
removes it as a single atomic move. Just verify mypy passes
unchanged:

```bash
mypy python/pydocs_mcp                                 # Unchanged: clean.
```

### Step 4.5 — commit

```bash
git add python/pydocs_mcp/retrieval/pipeline/base.py \
        tests/retrieval/test_step_protocol.py
git commit -m "$(cat <<'MSG'
fix(retrieval): declare to_dict() on RetrieverStep ABC

ParallelStep, RouteStep, ConditionalStep, TokenBudgetStep all call
step.to_dict() on nested RetrieverStep children but the method was
not declared on the ABC, forcing # type: ignore or hiding under the
[[tool.mypy.overrides]] ignore_errors entry.

The default impl raises NotImplementedError so subclasses keep their
shipped explicit overrides — every concrete step under
retrieval/steps/ already implements it. RetrieverPipeline /
CodeRetrieverPipeline are unaffected (the latter declares its own
to_dict; the former never serializes).

Adds tests/retrieval/test_step_protocol.py to pin the contract.
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: LOW.** Adding an inherited method with a `raise
NotImplementedError` default cannot break any caller that previously
worked (everyone who called `.to_dict()` was already on a concrete
subclass with an explicit override).

---

## Task 5: mypy bug 2 — `dense_fetcher.py:89` ndarray collapsing

**Outcome:** The `query_vec` variable at the
`store.vector_search(query_vector=query_vec, …)` call site has a
single coherent type (`np.ndarray`, not the
`np.ndarray | list[np.ndarray]` union returned by `embed_query`).
`dense_fetcher.py` becomes mypy-clean (subject to the broader
`retrieval.steps.*` override removal in Task 7).

### Step 5.1 — read the bug

Survey:

```python
query_vec = await self.embedder.embed_query(query_text)   # ndarray | list[ndarray]
if is_multi_vector(query_vec):
    query_vec = query_vec[0]                              # Now: ndarray
candidates = await self.store.vector_search(
    query_vector=query_vec,                               # mypy: ndarray | list[ndarray]
    ...,
)
```

The narrowing in the `if is_multi_vector(query_vec)` branch is only
visible to mypy if `is_multi_vector` is annotated as a `TypeGuard`.
Survey the function signature.

### Step 5.2 — failing test first

Add `tests/retrieval/test_dense_fetcher_typing.py`:

```python
"""Pin the type contract at the dense_fetcher boundary.

After is_multi_vector narrows the embedder return type, query_vec
must satisfy the VectorSearchable.vector_search signature without
# type: ignore.
"""
from __future__ import annotations

import inspect
import typing

from pydocs_mcp.models import is_multi_vector


def test_is_multi_vector_is_a_typeguard() -> None:
    """is_multi_vector must be annotated as TypeGuard[list[np.ndarray]]
    so mypy narrows the union after the True branch."""
    hints = typing.get_type_hints(is_multi_vector)
    return_type = hints.get("return")
    assert return_type is not None
    # TypeGuard[T] presents as typing.TypeGuard[T] at runtime via
    # typing.get_type_hints; we just check the origin is TypeGuard.
    origin = typing.get_origin(return_type)
    assert origin is typing.TypeGuard, (
        f"is_multi_vector must return TypeGuard[...] so mypy can narrow "
        f"the union in dense_fetcher; got {return_type!r}"
    )
```

Run: `pytest tests/retrieval/test_dense_fetcher_typing.py -v` —
**must FAIL** (`is_multi_vector` currently returns `bool`, not
`TypeGuard`).

### Step 5.3 — implement

Edit `python/pydocs_mcp/models.py` — change `is_multi_vector`'s return
annotation:

```diff
-def is_multi_vector(v: object) -> bool:
+def is_multi_vector(v: object) -> TypeGuard[list[np.ndarray]]:
```

Add `from typing import TypeGuard` at the top of the file if missing.

If the survey reveals `is_multi_vector` actually has different
semantics (e.g., checks `len > 1` so a single-element list is NOT
multi-vector), keep the function body unchanged — only the return
annotation moves to `TypeGuard`. If `TypeGuard` is too coarse for
the actual runtime check, fall back to an explicit narrowing in
`dense_fetcher.py`:

```diff
     if is_multi_vector(query_vec):
+        # Help mypy narrow: is_multi_vector returns bool, not TypeGuard.
+        assert isinstance(query_vec, list)
         query_vec = query_vec[0]
```

Pick whichever fix is local to ONE file and doesn't change runtime
behavior.

### Step 5.4 — verify

```bash
pytest tests/retrieval/test_dense_fetcher_typing.py -v   # PASS (if TypeGuard route)
pytest -q                                                  # 1369 passed.
mypy python/pydocs_mcp/retrieval/steps/dense_fetcher.py \
    --no-incremental                                       # No errors on this file specifically.
```

### Step 5.5 — commit

```bash
git add python/pydocs_mcp/models.py \
        python/pydocs_mcp/retrieval/steps/dense_fetcher.py \
        tests/retrieval/test_dense_fetcher_typing.py
git commit -m "$(cat <<'MSG'
fix(retrieval): annotate is_multi_vector as TypeGuard for dense_fetcher narrowing

The embedder boundary returns ndarray | list[ndarray]; the
is_multi_vector check narrows the runtime type but mypy could not
follow the narrowing because the function was annotated -> bool.
Switching to TypeGuard[list[np.ndarray]] lets the post-if branch
type-narrow to list[np.ndarray] and the post-collapse query_vec
back to np.ndarray, satisfying VectorSearchable.vector_search's
single-vector signature.

Surfaces one of the five genuine type bugs the audit-fix PR
deferred behind [[tool.mypy.overrides]] ignore_errors.
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: MEDIUM.** `TypeGuard` is a no-op at runtime; semantics are
unchanged. The risk is a subtle annotation mismatch (e.g., the actual
runtime check rejects a single-element list — adjust the TypeGuard
narrowing accordingly).

---

## Task 6: mypy bugs 3+4 — `member_fetcher` None-filter + `chunk_fetcher`/`member_fetcher` provider contract

**Outcome:** Two adjacent type fixes:

- `member_fetcher.py:137` — the chained `tuple(_keep_by_terms(m, needle) for m in members)` produces `tuple[ModuleMember | None, ...]` but the next line drops the `None`s. Mypy correctly flags that the intermediate variable widens the type. **Fix:** collapse the two-step filter into one generator: `tuple(filtered for m in members if (filtered := _keep_by_terms(m, needle)) is not None)`.
- `chunk_fetcher.py:239` + `member_fetcher.py:203` — `from_dict` reads `context.connection_provider` (typed `ConnectionProvider | None`) and passes it as a required `provider: ConnectionProvider` constructor kwarg. The composition-root contract guarantees the provider is wired before any fetcher step runs, so the `None` branch is impossible — codify it with an explicit `if context.connection_provider is None: raise ValueError("...")` check, mirroring the `app_config` / `embedder` checks already at the top of those `from_dict` methods.

### Step 6.1 — failing test first (bug 3)

Add to `tests/retrieval/steps/test_member_fetcher.py`:

```python
def test_member_fetcher_keep_by_terms_drops_none_in_one_pass() -> None:
    """Regression: the two-step filter (build None-tagged tuple, then
    drop the Nones) widens the tuple element type to ModuleMember | None.
    The one-pass walrus-filter form keeps the intermediate type as
    ModuleMember throughout, satisfying mypy without # type: ignore.
    """
    from pydocs_mcp.retrieval.steps.member_fetcher import _keep_by_terms
    from pydocs_mcp.models import ModuleMember
    from pydocs_mcp.storage.protocols import ModuleMemberFilterField

    keep = ModuleMember(metadata={
        ModuleMemberFilterField.NAME.value: "matchme",
        ModuleMemberFilterField.PACKAGE.value: "p",
        ModuleMemberFilterField.MODULE.value: "m",
        "docstring": "",
    })
    drop = ModuleMember(metadata={
        ModuleMemberFilterField.NAME.value: "other",
        ModuleMemberFilterField.PACKAGE.value: "p",
        ModuleMemberFilterField.MODULE.value: "m",
        "docstring": "",
    })
    members = (keep, drop)
    # The one-pass form: equivalent semantics, narrower type.
    filtered = tuple(
        m for m in (_keep_by_terms(x, "match") for x in members) if m is not None
    )
    assert filtered == (keep,)
```

Run — should already pass at the semantic level, but the test exists
to lock in the expected behavior before refactoring the production
call site.

### Step 6.2 — failing test first (bug 4)

Add to `tests/retrieval/test_fetcher_provider_contract.py`:

```python
"""Both fetcher steps require a non-None ConnectionProvider — the
composition root wires it before calling from_dict. Surface a clear
error if a misconfigured BuildContext slips through."""
from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.serialization import BuildContext
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.member_fetcher import MemberFetcherStep
from pydocs_mcp.config import AppConfig


def test_chunk_fetcher_from_dict_rejects_none_provider() -> None:
    ctx = BuildContext(connection_provider=None, app_config=AppConfig())
    with pytest.raises(ValueError, match="ChunkFetcherStep requires.*connection_provider"):
        ChunkFetcherStep.from_dict({}, ctx)


def test_member_fetcher_from_dict_rejects_none_provider() -> None:
    ctx = BuildContext(connection_provider=None, app_config=AppConfig())
    with pytest.raises(ValueError, match="MemberFetcherStep requires.*connection_provider"):
        MemberFetcherStep.from_dict({}, ctx)
```

Run: **both must FAIL** today (the current `from_dict` impls pass
`context.connection_provider` straight through to the constructor,
which has no None check and would later blow up at `_fetch_sync` with
an AttributeError instead of a clear ValueError).

### Step 6.3 — implement bug 3

Edit `python/pydocs_mcp/retrieval/steps/member_fetcher.py`, lines 134-138:

```diff
-        members = tuple(_row_to_candidate(row, self.retriever_name) for row in rows)
-        # Apply LIKE-style substring match in-process (matches legacy
-        # LikeMemberRetriever's Python-side `needle in name or needle in
-        # docstring` post-filter).
-        members = tuple(_keep_by_terms(m, needle) for m in members)
-        members = tuple(m for m in members if m is not None)
+        members = tuple(_row_to_candidate(row, self.retriever_name) for row in rows)
+        # Apply LIKE-style substring match in-process (matches legacy
+        # LikeMemberRetriever's Python-side `needle in name or needle in
+        # docstring` post-filter). Single-pass form keeps mypy from
+        # widening the element type to ModuleMember | None.
+        members = tuple(
+            kept for m in members
+            if (kept := _keep_by_terms(m, needle)) is not None
+        )
```

### Step 6.4 — implement bug 4

Edit `python/pydocs_mcp/retrieval/steps/chunk_fetcher.py`,
`from_dict` (around line 230):

```diff
     @classmethod
     def from_dict(cls, data: dict, context: BuildContext) -> ChunkFetcherStep:
         schema_name = data.get("schema_name", "chunk")
         if context.app_config is None:
             raise ValueError(
                 "ChunkFetcherStep requires BuildContext.app_config; "
                 "provide AppConfig at server/CLI startup."
             )
+        if context.connection_provider is None:
+            raise ValueError(
+                "ChunkFetcherStep requires BuildContext.connection_provider; "
+                "the composition root must wire a PerCallConnectionProvider "
+                "(see storage/factories.py)."
+            )
         allowed = frozenset(context.app_config.metadata_schemas[schema_name])
         return cls(
             provider=context.connection_provider,
             ...
         )
```

Repeat the same pattern in
`python/pydocs_mcp/retrieval/steps/member_fetcher.py`'s `from_dict`.

### Step 6.5 — verify

```bash
pytest tests/retrieval/steps/test_member_fetcher.py tests/retrieval/test_fetcher_provider_contract.py -v
pytest -q                                                  # 1371 passed (+3 new).
```

### Step 6.6 — commit

```bash
git add python/pydocs_mcp/retrieval/steps/member_fetcher.py \
        python/pydocs_mcp/retrieval/steps/chunk_fetcher.py \
        tests/retrieval/steps/test_member_fetcher.py \
        tests/retrieval/test_fetcher_provider_contract.py
git commit -m "$(cat <<'MSG'
fix(retrieval): tighten member_fetcher None-filter + fetcher provider contracts

Two adjacent type fixes the audit-fix PR's lenient mypy baseline
masked behind [[tool.mypy.overrides]] ignore_errors:

1. member_fetcher's two-pass _keep_by_terms filter widened the
   intermediate tuple to ModuleMember | None even though the second
   pass drops the Nones. Collapse to a single walrus-filter
   generator so the intermediate type stays ModuleMember throughout.

2. chunk_fetcher.from_dict and member_fetcher.from_dict passed
   context.connection_provider (typed Optional) into a constructor
   declaring a non-None provider field. Codify the composition-root
   contract by raising a clear ValueError when the provider is
   missing, mirroring the existing app_config and embedder checks.

No runtime behavior change: a None provider previously failed deep
inside _fetch_sync with an AttributeError; it now fails at
from_dict with a YAML-anchored ValueError.
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: MEDIUM.** The walrus form requires Python 3.8+ (the project
already requires 3.11+, so safe). The provider-None guard could
surface a latent composition-root bug in a downstream YAML — if so,
that's a real bug worth fixing, not a regression.

---

## Task 7: mypy bug 5 — `CompositeUnitOfWork` Protocol/concrete shape

**Outcome:** `__main__.py:346,400,430` pass `uow_factory: Callable[[],
CompositeUnitOfWork]` into call sites expecting
`Callable[[], UnitOfWork]`. `CompositeUnitOfWork` is structurally a
`UnitOfWork` but mypy needs the relationship made explicit.
**Fix:** annotate `CompositeUnitOfWork`'s `__aenter__` return type as
`UnitOfWork` (via the `Self`/protocol coercion) and let the factory
closure cast to `Callable[[], UnitOfWork]` at the composition-root
boundary. The `pydocs_mcp.__main__` and
`pydocs_mcp.storage.sqlite` entries leave the override list (if
clean).

### Step 7.1 — failing test first

Add `tests/storage/test_composite_uow_protocol_conformance.py`:

```python
"""CompositeUnitOfWork must satisfy the UnitOfWork Protocol so the
factory closures in __main__.py and storage/factories.py can be typed
as Callable[[], UnitOfWork] without # type: ignore."""
from __future__ import annotations

from pydocs_mcp.storage.composite_uow import CompositeUnitOfWork
from pydocs_mcp.storage.protocols import UnitOfWork


def test_composite_uow_passes_runtime_isinstance_check_against_protocol() -> None:
    """@runtime_checkable Protocol membership: CompositeUnitOfWork must
    answer True to ``isinstance(uow, UnitOfWork)`` so factories that
    return Callable[[], UnitOfWork] accept it without explicit cast."""
    # Construct via a NullVectorStore-only child so we don't need a
    # SQLite file on disk for this protocol check.
    from pydocs_mcp.storage.null_vector_store import NullVectorStore

    class _FakeChild:
        vectors = NullVectorStore()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def commit(self):
            return None

        async def rollback(self):
            return None

    uow = CompositeUnitOfWork(_FakeChild())
    assert isinstance(uow, UnitOfWork), (
        "CompositeUnitOfWork must satisfy the @runtime_checkable UnitOfWork "
        "Protocol so composition roots can type uow_factory as "
        "Callable[[], UnitOfWork]."
    )
```

Run: **must FAIL** today if `CompositeUnitOfWork` is missing one of
the Protocol's required attrs (likely `packages` / `chunks` / etc.
that route through `_attr_map`). The fix is either to attribute-route
those via `__getattr__` (already present) and ensure the Protocol
check finds them (this requires the Protocol's `@runtime_checkable`
to inspect `__getattr__` — it doesn't by default).

If the test reveals the Protocol-membership gap is structural (not
fixable without adding stub attributes to `CompositeUnitOfWork`),
fall back to the cleaner approach: declare the explicit
`UnitOfWork`-conforming attributes on `CompositeUnitOfWork` as
properties that route through `_attr_map`:

```python
@property
def packages(self) -> PackageStore: return self._attr_map["packages"]

@property
def chunks(self) -> ChunkStore: return self._attr_map["chunks"]

@property
def module_members(self) -> ModuleMemberStore: return self._attr_map["module_members"]

@property
def trees(self) -> DocumentTreeStore: return self._attr_map["trees"]

@property
def references(self) -> ReferenceStore: return self._attr_map["references"]

@property
def vectors(self) -> VectorStore: return self._attr_map["vectors"]
```

These properties make the Protocol membership explicit AND give mypy
the concrete return types it needs at the call sites.

### Step 7.2 — implement

Edit `python/pydocs_mcp/storage/composite_uow.py` to add the six
properties (or whichever subset is needed to make
`isinstance(composite_uow, UnitOfWork)` pass). Preserve the existing
`__getattr__` fallback — properties take precedence, so behavior is
unchanged for attribute access.

If `CompositeUnitOfWork` already routes through `__getattr__` for
these attrs, the properties are redundant at runtime but required for
the Protocol's structural check at typecheck time. **Keep the
properties** — runtime no-op, typecheck win.

### Step 7.3 — verify the test passes + mypy is happy

```bash
pytest tests/storage/test_composite_uow_protocol_conformance.py -v   # PASS
pytest -q                                                              # 1372 passed.
mypy python/pydocs_mcp/__main__.py --no-incremental                    # No errors on __main__ specifically.
```

### Step 7.4 — shrink the mypy override list

After Tasks 4-7 land, the `[[tool.mypy.overrides]]` `ignore_errors`
entry can drop the following modules (verify by running `mypy
python/pydocs_mcp` after each removal — re-add any entry that surfaces
new errors):

- `pydocs_mcp.retrieval.steps.*` — bugs 1, 2, 3 fixed in Tasks 4-6.
- `pydocs_mcp.__main__` — bug 5 fixed in Task 7. **Caveat:** the
  argparse `add_argument(**dict)` overload-resolution noise the
  comment block mentions may still be present. If so, leave
  `pydocs_mcp.__main__` in the list but add a focused
  `# type: ignore[arg-type]` per call site if there are only a few.
  If the override is still load-bearing after the bug-5 fix, document
  it in the comment block ("retained for argparse overload noise; the
  CompositeUnitOfWork fix landed in Task 7").
- `pydocs_mcp.storage.sqlite` — likely unaffected by this PR; keep
  in the override list.

Final state: the override list is shorter by at least
`pydocs_mcp.retrieval.steps.*` (a tight, valuable shrink). Document
the remaining entries with a short reason apiece.

### Step 7.5 — commit

```bash
git add python/pydocs_mcp/storage/composite_uow.py \
        tests/storage/test_composite_uow_protocol_conformance.py \
        pyproject.toml
git commit -m "$(cat <<'MSG'
fix(storage): CompositeUnitOfWork conforms to UnitOfWork Protocol + shrink mypy overrides

CompositeUnitOfWork routes attribute access through a runtime
__getattr__ map but the @runtime_checkable Protocol membership check
in storage.protocols.UnitOfWork could not see those attributes,
forcing composition roots in __main__.py to hide three call sites
behind [[tool.mypy.overrides]] ignore_errors.

Adds explicit properties (packages/chunks/module_members/trees/
references/vectors) that route through the same _attr_map — runtime
behavior unchanged, but the Protocol's structural check now passes
and mypy correctly types Callable[[], CompositeUnitOfWork] as a
subtype of Callable[[], UnitOfWork].

With Tasks 4-6 already fixing the three retrieval.steps bugs, drops
the following from [[tool.mypy.overrides]] ignore_errors:

  - pydocs_mcp.retrieval.steps.*

(pydocs_mcp.__main__ and pydocs_mcp.storage.sqlite retain their
entries — the residual errors there are argparse overload noise and
SQLite row-mapper variance respectively, both deferred to the
strict-mode ratchet PR.)
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: MEDIUM.** Adding explicit properties to a class that already
routes via `__getattr__` is safe (properties shadow `__getattr__`).
The risk is a property raising `KeyError` on a child UoW that doesn't
expose all six attrs — mitigated by keeping `__getattr__` as the
fallback when `_attr_map` lookup misses, AND by the existing
`_DISPATCH_ATTRS` scan that already validates child-attr coverage at
construction time.

---

## Task 8: `uv sync --frozen --group dev` in CI

**Outcome:** CI installation moves from
`uv pip install --system -e . && uv pip install --system --group dev`
(resolves fresh each run, `--system` writes into the runner Python)
to `uv sync --frozen --group dev` (uses `uv.lock` verbatim into a
managed `.venv`). Lockfile is enforced. Subsequent commands wrap
through `uv run` so they pick up the venv automatically. All 12 cells
(4 OS × 3 Python versions) must pass.

### Step 8.1 — survey existing CI shape

`.github/workflows/ci.yml`'s `python` job currently has:

```yaml
- name: Install uv
  uses: astral-sh/setup-uv@v4

- name: Install project + dev group
  run: |
    uv pip install --system -e .
    uv pip install --system --group dev

- name: Lint
  run: ruff check python/ tests/
- name: Typecheck
  run: mypy python/pydocs_mcp
- name: Smoke-test out-of-tree pydocs_mcp imports
  run: python scripts/smoke_check_benchmark_imports.py
- name: Test with coverage (Linux)
  ...
```

### Step 8.2 — failing-first check

The "failing" step here is implicit: running the new
`uv sync --frozen` against the existing `uv.lock` must succeed
locally (in the worktree) before pushing. Run:

```bash
uv sync --frozen --group dev
```

If this fails with a `Lock file out of sync` error, the lockfile
generated in the audit PR is stale; re-generate it with `uv lock` (no
arguments) and commit the resulting `uv.lock` diff in the same task.
Otherwise the lockfile is fresh and the rest of the task focuses on
CI wiring.

### Step 8.3 — edit `.github/workflows/ci.yml`

Replace the `Install project + dev group` block + adjust subsequent
commands:

```diff
       - name: Install uv
         uses: astral-sh/setup-uv@v4
 
-      - name: Install project + dev group
-        run: |
-          uv pip install --system -e .
-          uv pip install --system --group dev
+      # WHY: `uv sync --frozen` installs into a managed .venv directly
+      # from uv.lock, enforcing reproducible resolution. Previously the
+      # two `uv pip install --system` calls resolved fresh against
+      # PyPI on every run — uv.lock existed but did not gate anything.
+      # Subsequent CI commands wrap through `uv run` so they pick up
+      # the venv (no need to source activate).
+      - name: Install project + dev group (frozen)
+        run: uv sync --frozen --group dev
 
       - name: Lint
-        run: ruff check python/ tests/
+        run: uv run ruff check python/ tests/
+
+      - name: Format check
+        run: uv run ruff format --check python/ tests/ benchmarks/
 
       - name: Typecheck
-        run: mypy python/pydocs_mcp
+        run: uv run mypy python/pydocs_mcp
 
       - name: Smoke-test out-of-tree pydocs_mcp imports
-        run: python scripts/smoke_check_benchmark_imports.py
+        run: uv run python scripts/smoke_check_benchmark_imports.py
 
       - name: Test with coverage (Linux)
         if: runner.os == 'Linux'
         env:
           LD_PRELOAD: /usr/lib/x86_64-linux-gnu/libopenblas.so.0
         run: |
-          python -m pytest tests/ -v \
+          uv run python -m pytest tests/ -v \
             --ignore=tests/test_parity.py \
             --cov=pydocs_mcp \
             --cov-report=term-missing \
             --cov-report=xml:coverage.xml \
             --cov-fail-under=90
 
       - name: Test with coverage (macOS / Windows)
         if: runner.os != 'Linux'
-        run: |
-          python -m pytest tests/ -v --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-report=term-missing --cov-fail-under=90
+        run: uv run python -m pytest tests/ -v --ignore=tests/test_parity.py --cov=pydocs_mcp --cov-report=term-missing --cov-fail-under=90
```

Note the new `Format check` step — added so Task 2's `ruff format
--check` gate runs in CI explicitly (matches the `make lint` change).

### Step 8.4 — confirm cross-cell expectations

The CI matrix is `{ubuntu-latest, macos-13, macos-14, windows-latest}
× {3.11, 3.12, 3.13}` = 12 cells. `uv sync --frozen` requires the
`uv.lock` to be resolvable on every cell — if any cell ships with a
platform-incompatible wheel pin (e.g., a Windows-only `tokenizers`
wheel), `uv lock` regeneration may be needed. Verify the lockfile is
multi-platform by inspecting:

```bash
grep -E "^name = |^markers = " uv.lock | head -20
```

If markers like `sys_platform == 'linux'` aren't present, the lock is
single-platform and needs `uv lock --universal` re-generation. Commit
the regenerated lockfile in the same task.

### Step 8.5 — verify locally + push to CI

```bash
uv sync --frozen --group dev                                # Must succeed.
uv run pytest -q                                             # 1372 passed.
uv run ruff check python/ tests/ benchmarks/
uv run ruff format --check .
uv run mypy python/pydocs_mcp
```

Push the branch and watch the CI matrix. **All 12 cells must turn
green.** A failure in only the Windows cells likely means a `uv.lock`
platform-marker gap; macOS-13 vs macOS-14 differences usually come
from arch (x86_64 vs arm64) wheel mismatch.

### Step 8.6 — commit

```bash
git add .github/workflows/ci.yml
# uv.lock too if re-generated
git diff --cached --stat   # Confirm only ci.yml (and optionally uv.lock).
git commit -m "$(cat <<'MSG'
ci: switch installation to uv sync --frozen --group dev

The audit-fix PR added uv.lock but CI continued to use
`uv pip install --system -e . && uv pip install --system --group dev`,
which resolves dependencies fresh against PyPI on every run.
`uv sync --frozen --group dev` enforces the lockfile and installs
into a managed .venv. Subsequent CI commands wrap through `uv run`
so they pick up the venv without needing an explicit `source
.venv/bin/activate` step.

Adds an explicit `ruff format --check` step in CI to match the
`make lint` change in the same PR — the format gate now runs at
three layers (local make, pre-commit, CI).
MSG
)"
```

**Authorship pin: NO `Co-Authored-By:` trailer.**

**Risk: MEDIUM.** Cross-platform venv-setup behaviour is the main
unknown. Specifically: (1) Windows `uv run` shell quoting, (2)
macos-13 x86_64 vs macos-14 arm64 wheel availability,
(3) the `LD_PRELOAD` Linux openblas trick must still work inside the
venv (the env var is set on the runner shell — should still propagate
to `uv run` subprocesses). If the matrix surfaces issues, the
rollback is a one-line revert of this commit; subsequent PRs can do
the migration more carefully.

---

## Task 9: Verification gauntlet + AC matrix

**Outcome:** A single commit (or just the verification log captured in
the PR description) confirming that all four follow-ups landed
cleanly and the full test/lint/typecheck gauntlet passes locally on
this worktree's OS. The CI matrix run on the eventual PR confirms
the cross-OS expectation.

### Step 9.1 — local gauntlet

Run, in this order, capturing each command's output to the PR
description:

```bash
# 1. Lint + format gates (Task 2)
ruff check python/ tests/ benchmarks/
ruff format --check .

# 2. Typecheck (Tasks 4-7)
mypy python/pydocs_mcp

# 3. Test suite (all tasks)
pytest -q

# 4. Rust (unrelated to this PR but the standard check)
cargo fmt --check
cargo clippy -- -D warnings
cargo test

# 5. uv-managed venv path (Task 8) — verifies the locked deps install
uv sync --frozen --group dev
uv run pytest -q

# 6. Windows-skipped tests now run (Task 3) — implicit in pytest -q;
#    additionally confirm:
grep -rn "skipif.*win32\|skipif.*Windows" tests/ | grep -v "^Binary"
# Expected: empty (all 6 were removed).

# 7. mypy override list shrank (Tasks 4-7) — confirm the
#    retrieval.steps.* entry is gone:
grep -A2 'module = \[' pyproject.toml | grep -i "retrieval.steps"
# Expected: empty.
```

All commands must pass. If any fails, return to the relevant task
and fix.

### Step 9.2 — AC matrix (for the PR description)

| # | Audit follow-up                                | Task | Evidence                                                                                  |
|---|------------------------------------------------|------|-------------------------------------------------------------------------------------------|
| 1 | `ruff format` repo-wide enforced               | 2    | `ruff format --check .` returns 0; `make lint` runs format check; pre-commit hook present |
| 2 | 6 Windows-skipped tests unskipped + portable   | 3    | `grep -rn "skipif.*win32" tests/` is empty; CI matrix windows-latest cells pass           |
| 3 | mypy bug 1 (`to_dict` on RetrieverStep)        | 4    | New `tests/retrieval/test_step_protocol.py` passes; `base.py` declares `to_dict`          |
| 3 | mypy bug 2 (`dense_fetcher` ndarray collapse)  | 5    | `is_multi_vector -> TypeGuard[...]`; new `test_dense_fetcher_typing.py` passes            |
| 3 | mypy bug 3 (`member_fetcher` None filter)      | 6    | One-pass walrus filter; test_member_fetcher.py keep_by_terms regression test passes       |
| 3 | mypy bug 4 (`provider: ConnectionProvider`)    | 6    | `from_dict` raises clear `ValueError`; new `test_fetcher_provider_contract.py` passes     |
| 3 | mypy bug 5 (`CompositeUnitOfWork` as UoW)      | 7    | `isinstance(composite, UnitOfWork)` passes; explicit properties added                     |
| 3 | mypy override list shrank                      | 7    | `pydocs_mcp.retrieval.steps.*` no longer in `[[tool.mypy.overrides]] ignore_errors`       |
| 4 | CI uses `uv sync --frozen --group dev`          | 8    | `ci.yml` shows the new install step; all 12 matrix cells pass                              |

### Step 9.3 — optional: re-run baseline metrics

Capture the final state for the PR description:

```
pytest -q                       # 1372 passed (1367 baseline + 5 new tests).
ruff check + ruff format --check  # both clean.
mypy python/pydocs_mcp          # clean; override list shrunk by 1 entry.
cargo fmt + clippy + test       # clean (unchanged from baseline).
```

### Step 9.4 — no commit needed for verification

The verification gauntlet itself does not produce a commit — its
output goes into the PR description. The branch is ready to push and
PR.

**Authorship pin: NO `Co-Authored-By:` trailer.** (Applies retroactively
to every commit on the branch; verify via
`git log --pretty=format:"%h %an <%ae>  %s%n%b" main..HEAD | grep -i "co-authored"`
— **must be empty**.)

---

## Final state expected

After Tasks 1-9 land, the branch has:

- **9 commits** total (one per task; Task 9 produces no commit).
- ~391 file reformat (Task 2) + ~4 test edits (Task 3) + ~4 production
  spot-fixes (Tasks 4-7) + ~5 new test files (Tasks 4-7) + ~1
  `ci.yml` edit + ~1 `pyproject.toml` edit + ~1 `Makefile` edit.
- 1372 tests passing (1367 baseline + 5 new tests in Tasks 4/5/6/6/7).
- `[[tool.mypy.overrides]]` `ignore_errors` list shorter by 1 entry
  (`pydocs_mcp.retrieval.steps.*`).
- CI matrix runs `uv sync --frozen` and unskipped tests run on
  Windows cells.

**PR-creation step is OUT OF SCOPE for this plan** — the user creates
the PR separately after the branch lands. The plan ends at Task 9's
verification gauntlet.
