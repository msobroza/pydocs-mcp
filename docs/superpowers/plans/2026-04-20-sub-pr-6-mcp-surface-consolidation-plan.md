# Sub-PR #6 — MCP Surface Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5 pre-#6 MCP tools (`list_packages`, `get_package_doc`, `search_docs`, `search_api`, `inspect_module`) with 2 consolidated tools (`search`, `lookup`) backed by Pydantic input validation, typed exceptions, and a unified `LookupService` dispatch. Drop `inspect_module` feature.

**Architecture:** `server.py` handlers become 2 thin adapters over `SearchDocsService` / `SearchApiService` / `LookupService` — all accepting Pydantic models. New `LookupService` routes targets to the right backing service (`PackageLookupService` always; optional `DocumentTreeService` / `ReferenceService` when #5/#5b land). `kind="any"` search uses a new `unified_search.yaml` preset that runs both retrievers through `ParallelRetrievalStage → RRF → TokenBudget`. Error policy: typed `MCPToolError` subclasses raise to the MCP protocol; blanket `try/except Exception: return "..."` patterns are eliminated.

**Tech Stack:** Python 3.11+, FastMCP (`mcp>=1.0`), Pydantic v2 (already a runtime dep via pydantic-settings), SQLite FTS5, pytest.

**Spec:** [docs/superpowers/specs/2026-04-20-sub-pr-6-mcp-surface-consolidation-design.md](../specs/2026-04-20-sub-pr-6-mcp-surface-consolidation-design.md)

---

## File Structure

### New files
- `python/pydocs_mcp/application/mcp_inputs.py` — `SearchInput`, `LookupInput` Pydantic models
- `python/pydocs_mcp/application/mcp_errors.py` — `MCPToolError` hierarchy
- `python/pydocs_mcp/application/lookup_service.py` — `LookupService` (dispatch + soft-dependency handling)
- `python/pydocs_mcp/presets/unified_search.yaml` — `kind="any"` pipeline preset
- `tests/application/test_lookup_service.py` — full coverage of LookupService dispatch
- `tests/application/test_mcp_inputs.py` — Pydantic input validation tests
- `tests/application/test_mcp_errors.py` — exception hierarchy tests
- `tests/test_mcp_surface.py` — golden-fixture byte-parity tests for the new 2-tool surface

### Modified files
- `python/pydocs_mcp/application/package_lookup_service.py` — add `find_module(package, module) → bool` method
- `python/pydocs_mcp/application/__init__.py` — export `LookupService`, `MCPToolError` subclasses, `SearchInput`, `LookupInput`
- `python/pydocs_mcp/models.py` — add `ModuleMemberFilterField.SCOPE = "scope"` (sub-PR #1 §5 amendment required for AC #25 invariant)
- `python/pydocs_mcp/retrieval/config.py` — add `build_unified_pipeline_from_config(config, context)` factory
- `python/pydocs_mcp/server.py` — rewrite: 5 handlers → 2, Pydantic inputs, typed-error handling
- `python/pydocs_mcp/__main__.py` — replace `_cmd_query` / `_cmd_api` with `_cmd_search` / `_cmd_lookup`
- `tests/test_server.py` — rewrite for the 2-tool surface
- `tests/application/test_package_lookup_service.py` — add `find_module` tests

### Spec files edited (amendments — docs only)
- `docs/superpowers/specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md` — add AC for `application/__init__.py` exporting `DocumentTreeService`
- `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` — add AC for exporting `ReferenceService`

### Deleted files
- None. (The existing handler code in `server.py` is rewritten in place; tests updated.)

---

## Task 1: Pre-flight — `SCOPE` field + `find_module` method (sub-PR #1 + #4 amendments)

**Files:**
- Modify: `python/pydocs_mcp/models.py:66-71` (add `SCOPE` to `ModuleMemberFilterField`)
- Modify: `python/pydocs_mcp/application/package_lookup_service.py:56-57` (add `find_module` method)
- Test: `tests/application/test_package_lookup_service.py`

- [ ] **Step 1.1: Write failing test for filter-field invariant (AC #25)**

Append to `tests/application/test_package_lookup_service.py`:

```python
from pydocs_mcp.models import ChunkFilterField, ModuleMemberFilterField


def test_filter_field_scope_parity() -> None:
    """AC #25 — SCOPE key must be identical across the two field enums
    so a single SearchQuery.pre_filter works in the unified pipeline."""
    assert ChunkFilterField.PACKAGE.value == ModuleMemberFilterField.PACKAGE.value == "package"
    assert ChunkFilterField.SCOPE.value == ModuleMemberFilterField.SCOPE.value == "scope"
```

- [ ] **Step 1.2: Run test — verify it fails**

```
pytest tests/application/test_package_lookup_service.py::test_filter_field_scope_parity -v
```

Expected: FAIL with `AttributeError: SCOPE` on `ModuleMemberFilterField`.

- [ ] **Step 1.3: Add `SCOPE` to `ModuleMemberFilterField`**

Edit `python/pydocs_mcp/models.py:66-71`:

```python
class ModuleMemberFilterField(StrEnum):
    PACKAGE = "package"
    MODULE  = "module"
    NAME    = "name"
    KIND    = "kind"
    SCOPE   = "scope"   # added by sub-PR #6 — matches ChunkFilterField.SCOPE for unified queries
```

- [ ] **Step 1.4: Run test — verify it passes**

```
pytest tests/application/test_package_lookup_service.py::test_filter_field_scope_parity -v
```

Expected: PASS.

- [ ] **Step 1.5: Write failing test for `find_module`**

Append to `tests/application/test_package_lookup_service.py` (copy existing fixture imports from the file's top; do NOT assume — re-read the file first if needed):

```python
@pytest.mark.asyncio
async def test_find_module_returns_true_when_indexed(
    package_lookup_with_chunks,  # existing fixture that seeds ('fastapi', 'fastapi.routing')
) -> None:
    assert await package_lookup_with_chunks.find_module("fastapi", "fastapi.routing") is True


@pytest.mark.asyncio
async def test_find_module_returns_false_when_not_indexed(
    package_lookup_with_chunks,
) -> None:
    assert await package_lookup_with_chunks.find_module("fastapi", "fastapi.nonexistent") is False


@pytest.mark.asyncio
async def test_find_module_returns_false_on_empty_args(
    package_lookup_with_chunks,
) -> None:
    assert await package_lookup_with_chunks.find_module("", "fastapi.routing") is False
    assert await package_lookup_with_chunks.find_module("fastapi", "") is False
```

(If the existing `package_lookup_with_chunks` fixture doesn't exist in that file, check `tests/application/test_package_lookup_service.py` for the real fixture name and adapt; if nothing seeds chunks, add a small fixture in this task that constructs a `PackageLookupService` with in-memory fakes.)

- [ ] **Step 1.6: Run tests — verify they fail**

```
pytest tests/application/test_package_lookup_service.py -k find_module -v
```

Expected: FAIL — `AttributeError: find_module` on `PackageLookupService`.

- [ ] **Step 1.7: Add `find_module` to `PackageLookupService`**

Append to `python/pydocs_mcp/application/package_lookup_service.py:56` (after the existing `get_package_doc`):

```python
    async def find_module(self, package: str, module: str) -> bool:
        """Return True iff at least one indexed Chunk exists for (package, module).

        Added by sub-PR #6 — used by LookupService._longest_indexed_module
        to resolve dotted-path targets when tree_svc (from #5) isn't wired.
        """
        if not package or not module:
            return False
        chunks = await self.chunk_store.list(
            filter={
                ChunkFilterField.PACKAGE.value: package,
                ChunkFilterField.MODULE.value: module,
            },
            limit=1,
        )
        return len(chunks) > 0
```

- [ ] **Step 1.8: Run tests — verify all pass**

```
pytest tests/application/test_package_lookup_service.py -v
```

Expected: PASS (including all existing tests still green).

- [ ] **Step 1.9: Commit**

```bash
git add python/pydocs_mcp/models.py \
        python/pydocs_mcp/application/package_lookup_service.py \
        tests/application/test_package_lookup_service.py
git commit -m "feat(#6): ModuleMemberFilterField.SCOPE + PackageLookupService.find_module"
```

---

## Task 2: Typed exception hierarchy (`mcp_errors.py`)

**Files:**
- Create: `python/pydocs_mcp/application/mcp_errors.py`
- Test: `tests/application/test_mcp_errors.py`

- [ ] **Step 2.1: Write failing tests**

Create `tests/application/test_mcp_errors.py`:

```python
"""Tests for the typed MCP exception hierarchy (sub-PR #6 §5.1)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)


def test_all_subclasses_inherit_from_mcptoolerror() -> None:
    assert issubclass(InvalidArgumentError, MCPToolError)
    assert issubclass(NotFoundError, MCPToolError)
    assert issubclass(ServiceUnavailableError, MCPToolError)


def test_mcptoolerror_is_an_exception() -> None:
    assert issubclass(MCPToolError, Exception)


def test_error_carries_message() -> None:
    err = NotFoundError("target 'foo' not found")
    assert str(err) == "target 'foo' not found"


def test_exceptions_raise_and_catch_as_base() -> None:
    with pytest.raises(MCPToolError):
        raise InvalidArgumentError("bad input")
    with pytest.raises(MCPToolError):
        raise NotFoundError("missing")
    with pytest.raises(MCPToolError):
        raise ServiceUnavailableError("backend down")
```

- [ ] **Step 2.2: Run tests — verify they fail**

```
pytest tests/application/test_mcp_errors.py -v
```

Expected: FAIL — module does not exist.

- [ ] **Step 2.3: Create `mcp_errors.py`**

Create `python/pydocs_mcp/application/mcp_errors.py`:

```python
"""Typed exception hierarchy for MCP tool handlers (sub-PR #6 §5.1).

Handlers raise these instead of returning error strings. FastMCP maps
them to JSON-RPC error responses; see §5.3 for the code mapping.
"""
from __future__ import annotations


class MCPToolError(Exception):
    """Base — every handler-raised error inherits from this."""


class InvalidArgumentError(MCPToolError):
    """Semantic validation failure — input parsed but domain-invalid.

    Pydantic ``ValidationError`` covers schema-level failures; this is
    for post-parse checks (e.g., ``show="inherits"`` on a non-class target).
    """


class NotFoundError(MCPToolError):
    """Specified target doesn't exist in the index.

    Raised by ``lookup`` for unknown packages/modules/symbols.
    NOT raised by ``search`` — an empty search returns success with
    an empty-result string.
    """


class ServiceUnavailableError(MCPToolError):
    """Backend raised an unexpected error (SQLite, pipeline)
    or a required optional service is missing (e.g., tree_svc is None
    but ``show="tree"`` was requested).
    """
```

- [ ] **Step 2.4: Run tests — verify they pass**

```
pytest tests/application/test_mcp_errors.py -v
```

Expected: all 4 PASS.

- [ ] **Step 2.5: Commit**

```bash
git add python/pydocs_mcp/application/mcp_errors.py tests/application/test_mcp_errors.py
git commit -m "feat(#6): MCPToolError hierarchy (InvalidArgument/NotFound/ServiceUnavailable)"
```

---

## Task 3: Pydantic input models (`mcp_inputs.py`)

**Files:**
- Create: `python/pydocs_mcp/application/mcp_inputs.py`
- Test: `tests/application/test_mcp_inputs.py`

- [ ] **Step 3.1: Write failing tests**

Create `tests/application/test_mcp_inputs.py`:

```python
"""Tests for SearchInput / LookupInput Pydantic models (sub-PR #6 §4.3)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput


# ─── SearchInput ──────────────────────────────────────────────────────────

def test_search_input_defaults() -> None:
    m = SearchInput(query="hello")
    assert m.query == "hello"
    assert m.kind == "any"
    assert m.package == ""
    assert m.scope == "all"
    assert m.limit == 10


def test_search_input_empty_query_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="")


def test_search_input_huge_query_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x" * 30001)


def test_search_input_bad_kind_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", kind="weird")  # type: ignore[arg-type]


def test_search_input_bad_scope_rejected() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", scope="galaxy")  # type: ignore[arg-type]


def test_search_input_limit_out_of_range() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=0)
    with pytest.raises(ValidationError):
        SearchInput(query="x", limit=1001)


@pytest.mark.parametrize(
    "package", ["fastapi", "__project__", "Flask-Login", "scikit-learn", "a_b", "pkg.sub"]
)
def test_search_input_package_accepts_valid(package: str) -> None:
    SearchInput(query="x", package=package)  # no raise


@pytest.mark.parametrize(
    "package", ["has space", "!", "-leading-dash", "trailing.", ".leading"]
)
def test_search_input_package_rejects_invalid(package: str) -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="x", package=package)


def test_search_input_empty_package_ok() -> None:
    """Empty means 'all packages'; validator must not reject."""
    SearchInput(query="x", package="")


# ─── LookupInput ──────────────────────────────────────────────────────────

def test_lookup_input_defaults() -> None:
    m = LookupInput()
    assert m.target == ""
    assert m.show == "default"


@pytest.mark.parametrize(
    "target",
    [
        "",
        "fastapi",
        "fastapi.routing",
        "fastapi.routing.APIRouter",
        "fastapi.routing.APIRouter.include_router",
        "__project__",
    ],
)
def test_lookup_input_target_accepts_valid(target: str) -> None:
    LookupInput(target=target)


@pytest.mark.parametrize(
    "target",
    [
        "has spaces",
        "foo..bar",
        "foo.",
        ".foo",
        "1bad",
        "foo!",
    ],
)
def test_lookup_input_target_rejects_invalid(target: str) -> None:
    with pytest.raises(ValidationError):
        LookupInput(target=target)


def test_lookup_input_bad_show_rejected() -> None:
    with pytest.raises(ValidationError):
        LookupInput(show="invalid")  # type: ignore[arg-type]
```

- [ ] **Step 3.2: Run tests — verify they fail**

```
pytest tests/application/test_mcp_inputs.py -v
```

Expected: FAIL — module does not exist.

- [ ] **Step 3.3: Create `mcp_inputs.py`**

Create `python/pydocs_mcp/application/mcp_inputs.py`:

```python
"""Pydantic input models for MCP tools (sub-PR #6 §4.3).

Enforces format via regex + protocol-safety caps. Limits are permissive:
query up to 30k chars, limit up to 1000 — covers runaway clients without
rejecting legit edge cases.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# Format validators — reject malformed input at the boundary.
_PACKAGE_RE = re.compile(r"^(?:[a-zA-Z0-9][a-zA-Z0-9._-]*|__project__)$")
_TARGET_RE = re.compile(
    r"^(?:[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)?$"
)  # empty or dotted-identifier chain; rejects foo..bar, foo., leading digit


class SearchInput(BaseModel):
    """Input for the ``search`` MCP tool.

    Examples (from spec §4.1):
        SearchInput(query="batch inference", kind="docs")
        SearchInput(query="HTTPBasicAuth", kind="api")
        SearchInput(query="retry logic", package="requests")
    """

    query: str = Field(min_length=1, max_length=30000)
    kind: Literal["docs", "api", "any"] = "any"
    package: str = ""
    scope: Literal["project", "deps", "all"] = "all"
    limit: int = Field(default=10, ge=1, le=1000)

    @field_validator("package")
    @classmethod
    def _check_package(cls, v: str) -> str:
        if v and not _PACKAGE_RE.match(v):
            raise ValueError(
                "package must match ^[a-zA-Z0-9][a-zA-Z0-9._-]*$ or be '__project__'"
            )
        return v


class LookupInput(BaseModel):
    """Input for the ``lookup`` MCP tool.

    Examples (from spec §4.1):
        LookupInput(target="")
        LookupInput(target="fastapi.routing.APIRouter")
        LookupInput(target="fastapi.routing.APIRouter.include_router", show="callers")
    """

    target: str = ""
    show: Literal["default", "tree", "callers", "callees", "inherits"] = "default"

    @field_validator("target")
    @classmethod
    def _check_target(cls, v: str) -> str:
        if v and not _TARGET_RE.match(v):
            raise ValueError(
                "target must be a dotted identifier like 'pkg.mod.Class.method' or empty"
            )
        return v
```

- [ ] **Step 3.4: Run tests — verify they pass**

```
pytest tests/application/test_mcp_inputs.py -v
```

Expected: all PASS.

- [ ] **Step 3.5: Commit**

```bash
git add python/pydocs_mcp/application/mcp_inputs.py tests/application/test_mcp_inputs.py
git commit -m "feat(#6): SearchInput and LookupInput Pydantic models with format validators"
```

---

## Task 4: `LookupService` skeleton + empty-target + package-only dispatch

**Files:**
- Create: `python/pydocs_mcp/application/lookup_service.py`
- Test: `tests/application/test_lookup_service.py`

- [ ] **Step 4.1: Write failing tests for the first two dispatch branches**

Create `tests/application/test_lookup_service.py`:

```python
"""Tests for LookupService dispatch (sub-PR #6 §6)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.models import Package


@pytest.fixture
def fake_package() -> Package:
    return Package(
        name="fastapi",
        version="0.110.0",
        summary="A modern web framework",
        homepage="https://fastapi.tiangolo.com",
        dependencies=("starlette", "pydantic"),
    )


@pytest.fixture
def package_lookup_mock(fake_package: Package) -> MagicMock:
    m = MagicMock()
    m.list_packages = AsyncMock(return_value=(fake_package,))
    m.get_package_doc = AsyncMock(return_value=None)
    m.find_module = AsyncMock(return_value=False)
    return m


@pytest.mark.asyncio
async def test_lookup_empty_target_returns_package_list(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(package_lookup=package_lookup_mock)
    out = await svc.lookup(LookupInput(target=""))
    assert "fastapi" in out
    assert "0.110.0" in out
    package_lookup_mock.list_packages.assert_awaited_once()


@pytest.mark.asyncio
async def test_lookup_package_only_returns_package_doc(
    package_lookup_mock: MagicMock, fake_package: Package,
) -> None:
    # Arrange: return a real PackageDoc
    from pydocs_mcp.models import PackageDoc
    doc = PackageDoc(package=fake_package, chunks=(), members=())
    package_lookup_mock.get_package_doc = AsyncMock(return_value=doc)

    svc = LookupService(package_lookup=package_lookup_mock)
    out = await svc.lookup(LookupInput(target="fastapi"))

    assert "fastapi" in out
    assert "A modern web framework" in out
    package_lookup_mock.get_package_doc.assert_awaited_once_with("fastapi")


@pytest.mark.asyncio
async def test_lookup_unknown_package_raises_not_found(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.get_package_doc = AsyncMock(return_value=None)
    svc = LookupService(package_lookup=package_lookup_mock)

    with pytest.raises(NotFoundError) as exc:
        await svc.lookup(LookupInput(target="nonexistent"))
    assert "nonexistent" in str(exc.value)
```

- [ ] **Step 4.2: Run tests — verify they fail (module missing)**

```
pytest tests/application/test_lookup_service.py -v
```

Expected: FAIL — `lookup_service` module doesn't exist.

- [ ] **Step 4.3: Create minimal `LookupService` covering these branches**

Create `python/pydocs_mcp/application/lookup_service.py`:

```python
"""LookupService — unified dispatch for the 'lookup' MCP tool (sub-PR #6 §6)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.application.formatting import (
    render_package_doc,
    render_packages_list,
)
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput
from pydocs_mcp.application.package_lookup_service import PackageLookupService

if TYPE_CHECKING:
    # Avoid hard imports — these services may be absent pre-#5 / pre-#5b.
    from pydocs_mcp.application.document_tree_service import DocumentTreeService
    from pydocs_mcp.application.reference_service import ReferenceService


@dataclass(frozen=True, slots=True)
class LookupService:
    """Routes lookup targets to the right backing service.

    ``tree_svc`` (from #5) and ``ref_svc`` (from #5b) are optional.
    When either is ``None``, corresponding ``show`` values raise
    ``ServiceUnavailableError``. See spec §6.2.
    """

    package_lookup: PackageLookupService
    tree_svc: "DocumentTreeService | None" = None
    ref_svc: "ReferenceService | None" = None

    async def lookup(self, payload: LookupInput) -> str:
        target = payload.target
        show = payload.show

        # 1. Empty target → list packages
        if not target:
            packages = await self.package_lookup.list_packages()
            return render_packages_list(packages)

        parts = target.split(".")
        package = parts[0]

        # 2. Package-only lookup
        if len(parts) == 1:
            return await self._package_lookup(package, show)

        # 3+ — module/symbol lookup (implemented in later tasks)
        raise NotFoundError(f"target '{target}' not resolvable yet (later tasks)")

    async def _package_lookup(self, package: str, show: str) -> str:
        doc = await self.package_lookup.get_package_doc(package)
        if doc is None:
            raise NotFoundError(f"package '{package}' not indexed")
        # show="tree" on a package requires tree_svc — covered in later task
        # show="default" / any other → render the package doc as markdown
        return render_package_doc(doc)
```

**Note:** `render_packages_list` and `render_package_doc` must exist in `application/formatting.py`. If they don't, add them (see application/formatting.py for existing helpers from sub-PR #4 — likely `_render_packages_list` and `_render_package_doc` live in server.py today and need to be moved). In that case, add an early step in Task 4 to extract them. Check first:

```bash
grep -n "def render_packages_list\|def render_package_doc" python/pydocs_mcp/application/formatting.py
```

If absent, copy the existing `_render_packages_list` / `_render_package_doc` bodies from today's `server.py` into `application/formatting.py` as public helpers (drop the leading underscore), and update `server.py` to import from there.

- [ ] **Step 4.4: Run tests — verify they pass**

```
pytest tests/application/test_lookup_service.py -v
```

Expected: 3 PASS.

- [ ] **Step 4.5: Commit**

```bash
git add python/pydocs_mcp/application/lookup_service.py \
        python/pydocs_mcp/application/formatting.py \
        tests/application/test_lookup_service.py
git commit -m "feat(#6): LookupService empty-target + package-only dispatch"
```

---

## Task 5: `LookupService` — `_longest_indexed_module` resolution

**Files:**
- Modify: `python/pydocs_mcp/application/lookup_service.py`
- Test: `tests/application/test_lookup_service.py`

- [ ] **Step 5.1: Write failing tests for module resolution**

Append to `tests/application/test_lookup_service.py`:

```python
@pytest.mark.asyncio
async def test_longest_indexed_module_prefers_tree_when_wired(
    package_lookup_mock: MagicMock,
) -> None:
    tree_svc = MagicMock()
    # tree_svc.get_tree returns truthy for "fastapi.routing", None for "fastapi.routing.APIRouter"
    async def _get_tree(package: str, module: str):
        return object() if module == "fastapi.routing" else None
    tree_svc.get_tree = _get_tree

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    module = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "routing", "APIRouter", "include_router"]
    )
    assert module == "fastapi.routing"


@pytest.mark.asyncio
async def test_longest_indexed_module_falls_back_to_find_module(
    package_lookup_mock: MagicMock,
) -> None:
    """tree_svc is None → use PackageLookupService.find_module (sub-PR #4 amendment)."""
    async def _find(package: str, module: str):
        return module == "fastapi.routing"
    package_lookup_mock.find_module = AsyncMock(side_effect=_find)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=None)
    module = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "routing", "APIRouter"]
    )
    assert module == "fastapi.routing"


@pytest.mark.asyncio
async def test_longest_indexed_module_returns_none_when_nothing_matches(
    package_lookup_mock: MagicMock,
) -> None:
    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=None)
    module = await svc._longest_indexed_module(
        "fastapi", ["fastapi", "nonexistent", "foo"]
    )
    assert module is None
```

- [ ] **Step 5.2: Run tests — verify they fail**

```
pytest tests/application/test_lookup_service.py -k longest -v
```

Expected: FAIL — method not yet implemented.

- [ ] **Step 5.3: Add `_longest_indexed_module`**

Add to `python/pydocs_mcp/application/lookup_service.py` inside the `LookupService` class:

```python
    async def _longest_indexed_module(
        self, package: str, parts: list[str]
    ) -> str | None:
        """Walk longest-prefix-first, return the longest dotted path that
        is an indexed module (prefers tree_svc, falls back to find_module)."""
        for i in range(len(parts), 0, -1):
            candidate = ".".join(parts[:i])
            if self.tree_svc is not None:
                tree = await self.tree_svc.get_tree(package, candidate)
                if tree is not None:
                    return candidate
            if await self.package_lookup.find_module(package, candidate):
                return candidate
        return None
```

- [ ] **Step 5.4: Run tests — verify they pass**

```
pytest tests/application/test_lookup_service.py -v
```

Expected: ALL PASS.

- [ ] **Step 5.5: Commit**

```bash
git add python/pydocs_mcp/application/lookup_service.py tests/application/test_lookup_service.py
git commit -m "feat(#6): LookupService._longest_indexed_module with tree+find_module fallback"
```

---

## Task 6: `LookupService` — module-level + symbol-level dispatch (with `show` modes)

**Files:**
- Modify: `python/pydocs_mcp/application/lookup_service.py`
- Test: `tests/application/test_lookup_service.py`

- [ ] **Step 6.1: Write failing tests**

Append to `tests/application/test_lookup_service.py`:

```python
@pytest.mark.asyncio
async def test_module_lookup_without_tree_svc_raises_service_unavailable(
    package_lookup_mock: MagicMock,
) -> None:
    package_lookup_mock.find_module = AsyncMock(return_value=True)
    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=None)

    # show="default" on a module without tree_svc — no way to render the tree.
    with pytest.raises(ServiceUnavailableError) as exc:
        await svc.lookup(LookupInput(target="fastapi.routing"))
    assert "tree" in str(exc.value).lower() or "document" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_module_lookup_with_tree_svc_returns_rendered_tree(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_tree.to_pageindex_json = MagicMock(return_value={"title": "routing", "nodes": []})
    tree_svc = MagicMock()
    tree_svc.get_tree = AsyncMock(return_value=fake_tree)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    out = await svc.lookup(LookupInput(target="fastapi.routing"))
    assert "routing" in out


@pytest.mark.asyncio
async def test_symbol_lookup_not_found_when_module_resolves_but_symbol_missing(
    package_lookup_mock: MagicMock,
) -> None:
    """Module prefix resolves but no symbol node matches — raise NotFoundError."""
    fake_tree = MagicMock()
    # Tree returns no child matching "APIRouter" — simulate: find_node returns None
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=None)
    tree_svc = MagicMock()
    tree_svc.get_tree = AsyncMock(return_value=fake_tree)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    with pytest.raises(NotFoundError):
        await svc.lookup(LookupInput(target="fastapi.routing.NoSuchClass"))


@pytest.mark.asyncio
async def test_show_callers_without_ref_svc_raises_service_unavailable(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_node = MagicMock()
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = MagicMock()
    tree_svc.get_tree = AsyncMock(return_value=fake_tree)

    svc = LookupService(
        package_lookup=package_lookup_mock,
        tree_svc=tree_svc,
        ref_svc=None,  # #5b not wired
    )
    with pytest.raises(ServiceUnavailableError):
        await svc.lookup(LookupInput(target="fastapi.routing.X", show="callers"))


@pytest.mark.asyncio
async def test_show_inherits_on_non_class_raises_invalid_argument(
    package_lookup_mock: MagicMock,
) -> None:
    fake_tree = MagicMock()
    fake_node = MagicMock()
    fake_node.kind = "method"  # not a class
    fake_tree.find_node_by_qualified_name = MagicMock(return_value=fake_node)
    tree_svc = MagicMock()
    tree_svc.get_tree = AsyncMock(return_value=fake_tree)

    svc = LookupService(package_lookup=package_lookup_mock, tree_svc=tree_svc)
    with pytest.raises(InvalidArgumentError) as exc:
        await svc.lookup(LookupInput(target="fastapi.routing.X.y", show="inherits"))
    assert "class" in str(exc.value).lower()
```

- [ ] **Step 6.2: Run tests — verify they fail**

```
pytest tests/application/test_lookup_service.py -v
```

Expected: the 5 new tests FAIL.

- [ ] **Step 6.3: Extend `LookupService` with module + symbol dispatch**

Replace the `async def lookup` + `_package_lookup` section in `python/pydocs_mcp/application/lookup_service.py` with the full dispatch (merge with prior `_longest_indexed_module`):

```python
    async def lookup(self, payload: LookupInput) -> str:
        target = payload.target
        show = payload.show

        if not target:
            packages = await self.package_lookup.list_packages()
            return render_packages_list(packages)

        parts = target.split(".")
        package = parts[0]

        if len(parts) == 1:
            return await self._package_lookup(package, show)

        module = await self._longest_indexed_module(package, parts)
        if module is None:
            raise NotFoundError(
                f"no module matching '{target}' found under '{package}'"
            )

        symbol_path = parts[len(module.split(".")):]
        if not symbol_path:
            return await self._module_lookup(package, module, show)

        return await self._symbol_lookup(package, module, target, show)

    async def _module_lookup(self, package: str, module: str, show: str) -> str:
        if self.tree_svc is None:
            raise ServiceUnavailableError(
                f"module tree for '{module}' unavailable — enable via sub-PR #5"
            )
        tree = await self.tree_svc.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"no tree stored for '{package}.{module}'")
        # show="tree" is the default/only meaningful mode for a module.
        import json
        return json.dumps(tree.to_pageindex_json(), indent=2)

    async def _symbol_lookup(
        self, package: str, module: str, target: str, show: str,
    ) -> str:
        if self.tree_svc is None:
            raise ServiceUnavailableError(
                f"symbol tree for '{target}' unavailable — enable via sub-PR #5"
            )
        tree = await self.tree_svc.get_tree(package, module)
        if tree is None:
            raise NotFoundError(f"no tree for '{package}.{module}'")
        node = tree.find_node_by_qualified_name(target)
        if node is None:
            raise NotFoundError(f"'{target}' not found in {module}")

        if show == "default" or show == "tree":
            import json
            return json.dumps(node.to_pageindex_json(), indent=2)

        if show == "callers":
            if self.ref_svc is None:
                raise ServiceUnavailableError(
                    "reference graph not indexed — enable via sub-PR #5b"
                )
            refs = await self.ref_svc.callers(package, node.node_id)
            return self._render_refs(refs)

        if show == "callees":
            if self.ref_svc is None:
                raise ServiceUnavailableError(
                    "reference graph not indexed — enable via sub-PR #5b"
                )
            refs = await self.ref_svc.callees(package, node.node_id)
            return self._render_refs(refs)

        if show == "inherits":
            if getattr(node, "kind", "") != "class":
                raise InvalidArgumentError(
                    f"show='inherits' only applies to CLASS nodes, got {node.kind}"
                )
            # Read directly from node metadata — no ref_svc required (degraded mode).
            inherits = node.extra_metadata.get("inherits_from", [])
            return "\n".join(f"- {base}" for base in inherits) or "(no base classes)"

        raise InvalidArgumentError(f"unknown show value: {show}")

    @staticmethod
    def _render_refs(refs) -> str:
        if not refs:
            return "(no references)"
        return "\n".join(
            f"- {r.from_node_id} → {r.to_name} ({r.kind})" for r in refs
        )
```

- [ ] **Step 6.4: Run tests — verify they pass**

```
pytest tests/application/test_lookup_service.py -v
```

Expected: ALL PASS (including prior tasks' tests).

- [ ] **Step 6.5: Commit**

```bash
git add python/pydocs_mcp/application/lookup_service.py tests/application/test_lookup_service.py
git commit -m "feat(#6): LookupService module + symbol dispatch (tree, callers, callees, inherits)"
```

---

## Task 7: `unified_search.yaml` preset + `build_unified_pipeline_from_config`

**Files:**
- Create: `python/pydocs_mcp/presets/unified_search.yaml`
- Modify: `python/pydocs_mcp/retrieval/config.py`
- Test: existing test suite should cover preset loading; add a focused test if none does

- [ ] **Step 7.1: Inspect how `build_chunk_pipeline_from_config` works**

```
pytest --collect-only -q | grep -i 'pipeline' | head
# then read:
grep -n "build_chunk_pipeline_from_config\|build_member_pipeline_from_config" python/pydocs_mcp/retrieval/config.py
```

This is context-gathering, not a test. Use the shape you find in `config.py:216-230` as the template for the new factory.

- [ ] **Step 7.2: Write failing test for unified pipeline construction**

Append to a suitable pipeline test (`tests/retrieval/` has files; pick `test_pipelines_extended.py` or create `test_unified_pipeline.py`):

Create `tests/retrieval/test_unified_pipeline.py`:

```python
"""Tests for the unified (chunks + members) search pipeline (sub-PR #6 §8)."""
from __future__ import annotations

import pytest

from pydocs_mcp.retrieval.config import (
    AppConfig,
    build_unified_pipeline_from_config,
)
from pydocs_mcp.retrieval.wiring import build_retrieval_context


def test_build_unified_pipeline_returns_a_runnable_pipeline(tmp_path) -> None:
    """The factory should return an object with a .run() coroutine."""
    config = AppConfig()  # defaults; does not need a YAML file
    db_path = tmp_path / "test.db"
    context = build_retrieval_context(db_path, config)
    pipeline = build_unified_pipeline_from_config(config, context)
    assert hasattr(pipeline, "run")
```

- [ ] **Step 7.3: Run test — verify it fails**

```
pytest tests/retrieval/test_unified_pipeline.py -v
```

Expected: FAIL — `build_unified_pipeline_from_config` not defined.

- [ ] **Step 7.4: Create the YAML preset**

Create `python/pydocs_mcp/presets/unified_search.yaml`:

```yaml
# Unified search preset (sub-PR #6 §8) — runs both retrievers then RRF-merges.
# Used by the "search" MCP tool when kind="any".
name: unified_search
description: Merges BM25 chunk results and LIKE module-member results via RRF.
stages:
  - type: ParallelRetrievalStage
    retrievers:
      - type: Bm25ChunkRetriever
      - type: LikeMemberRetriever
  - type: ReciprocalRankFusionStage
  - type: TokenBudgetFormatterStage
```

- [ ] **Step 7.5: Add `build_unified_pipeline_from_config` to `retrieval/config.py`**

Check the shape of `build_chunk_pipeline_from_config(config, context)` in `python/pydocs_mcp/retrieval/config.py` first. Then append alongside it (around line 230):

```python
def build_unified_pipeline_from_config(
    config: AppConfig,
    context: "RetrievalContext",
):
    """Build the unified (chunks + members) search pipeline from
    ``presets/unified_search.yaml`` (sub-PR #6 §8). Used for kind='any'."""
    return _build_pipeline_from_preset(
        preset_name="unified_search",
        config=config,
        context=context,
    )
```

Where `_build_pipeline_from_preset` is whatever internal helper `build_chunk_pipeline_from_config` already uses. If no such helper exists, inline the preset-loading logic matching `build_chunk_pipeline_from_config`'s pattern exactly.

- [ ] **Step 7.6: Run test — verify it passes**

```
pytest tests/retrieval/test_unified_pipeline.py -v
```

Expected: PASS.

- [ ] **Step 7.7: Commit**

```bash
git add python/pydocs_mcp/presets/unified_search.yaml \
        python/pydocs_mcp/retrieval/config.py \
        tests/retrieval/test_unified_pipeline.py
git commit -m "feat(#6): unified_search.yaml preset + build_unified_pipeline_from_config"
```

---

## Task 8: Export new application types from `application/__init__.py`

**Files:**
- Modify: `python/pydocs_mcp/application/__init__.py`

- [ ] **Step 8.1: Update exports**

Edit `python/pydocs_mcp/application/__init__.py` — add imports + extend `__all__`:

```python
from pydocs_mcp.application.lookup_service import LookupService
from pydocs_mcp.application.mcp_errors import (
    InvalidArgumentError,
    MCPToolError,
    NotFoundError,
    ServiceUnavailableError,
)
from pydocs_mcp.application.mcp_inputs import LookupInput, SearchInput
```

Extend `__all__` with: `"InvalidArgumentError"`, `"LookupInput"`, `"LookupService"`, `"MCPToolError"`, `"NotFoundError"`, `"SearchInput"`, `"ServiceUnavailableError"`.

- [ ] **Step 8.2: Write a smoke test**

Append to `tests/application/test_mcp_errors.py` (or a new file):

```python
def test_new_application_exports_are_importable() -> None:
    from pydocs_mcp.application import (
        InvalidArgumentError,
        LookupInput,
        LookupService,
        MCPToolError,
        NotFoundError,
        SearchInput,
        ServiceUnavailableError,
    )
    assert all([
        InvalidArgumentError, LookupInput, LookupService, MCPToolError,
        NotFoundError, SearchInput, ServiceUnavailableError,
    ])
```

- [ ] **Step 8.3: Run test**

```
pytest tests/application/test_mcp_errors.py::test_new_application_exports_are_importable -v
```

Expected: PASS.

- [ ] **Step 8.4: Commit**

```bash
git add python/pydocs_mcp/application/__init__.py tests/application/test_mcp_errors.py
git commit -m "feat(#6): export LookupService, MCPToolError, SearchInput, LookupInput"
```

---

## Task 9: Rewrite `server.py` — 2 handlers + typed errors

**Files:**
- Modify: `python/pydocs_mcp/server.py` (full rewrite)

**Before coding:** re-read the current `python/pydocs_mcp/server.py` to copy the `_render_*`, `_build_*_query`, and `_normalize_pkg_filter_value` helpers verbatim — they stay. Only the `@mcp.tool()` handlers change.

- [ ] **Step 9.1: Write a smoke test for the new `search` handler**

Replace `tests/test_server.py`'s contents with the new 2-tool test file:

Create `tests/test_mcp_surface.py`:

```python
"""Integration smoke tests for the post-#6 MCP surface (search + lookup).

Full golden-fixture parity tests live in tests/test_integration.py; this
module exercises the wiring, not the search ranking math.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from pydocs_mcp.application import (
    InvalidArgumentError,
    LookupInput,
    LookupService,
    MCPToolError,
    NotFoundError,
    SearchInput,
    ServiceUnavailableError,
)


def test_search_input_roundtrips_through_pydantic() -> None:
    m = SearchInput(query="auth", kind="docs", package="requests")
    assert m.query == "auth"
    assert m.kind == "docs"


def test_lookup_input_roundtrips_through_pydantic() -> None:
    m = LookupInput(target="fastapi.routing", show="tree")
    assert m.target == "fastapi.routing"
    assert m.show == "tree"


def test_search_rejects_empty_query_at_boundary() -> None:
    with pytest.raises(ValidationError):
        SearchInput(query="")


def test_all_typed_errors_subclass_mcptoolerror() -> None:
    for exc in [InvalidArgumentError, NotFoundError, ServiceUnavailableError]:
        assert issubclass(exc, MCPToolError)
```

- [ ] **Step 9.2: Run — verify passes (doesn't exercise server.py yet)**

```
pytest tests/test_mcp_surface.py -v
```

Expected: PASS.

- [ ] **Step 9.3: Rewrite `server.py`**

Fully replace `python/pydocs_mcp/server.py` with:

```python
"""MCP server exposing 2 consolidated tools: search, lookup (sub-PR #6).

Handlers are thin adapters over application-layer services. All rendering
lives in :mod:`pydocs_mcp.application.formatting` and the service classes.

Error policy (spec §5.2):
- Typed exceptions from :mod:`pydocs_mcp.application.mcp_errors` raise
  directly — FastMCP turns them into structured MCP tool errors.
- Blanket ``try/except Exception: return "..."`` is forbidden. All
  unexpected exceptions are re-raised as ``ServiceUnavailableError``.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from pydocs_mcp.application import (
    LookupInput,
    LookupService,
    MCPToolError,
    SearchInput,
    ServiceUnavailableError,
)
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    SearchQuery,
    SearchResponse,
    SearchScope,
)

log = logging.getLogger("pydocs-mcp")


def _scope_from_string(scope: str) -> SearchScope:
    """Map SearchInput.scope (Literal) to the SearchScope enum."""
    return {
        "project": SearchScope.PROJECT_ONLY,
        "deps": SearchScope.DEPENDENCIES_ONLY,
        "all": SearchScope.ALL,
    }[scope]


def _normalize_pkg_filter_value(package: str) -> str:
    pkg = package.strip()
    return pkg if pkg == "__project__" else normalize_package_name(pkg)


def _build_search_query(payload: SearchInput) -> SearchQuery:
    """Build a single SearchQuery used for kind in {docs, api, any}.

    The filter-key strings are shared across ChunkFilterField and
    ModuleMemberFilterField (see invariant AC #25), so one query shape
    drives both retrievers in the unified pipeline.
    """
    pre_filter: dict = {ChunkFilterField.SCOPE.value: _scope_from_string(payload.scope).value}
    if payload.package:
        pre_filter[ChunkFilterField.PACKAGE.value] = _normalize_pkg_filter_value(payload.package)
    return SearchQuery(terms=payload.query, pre_filter=pre_filter)


def _render_search_response(response: SearchResponse, empty_msg: str) -> str:
    result = response.result
    if result is None or not result.items:
        return empty_msg
    return result.items[0].text


def run(db_path: Path, config_path: Path | None = None) -> None:
    """Start the MCP server."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        log.error("Missing dependency: pip install mcp")
        sys.exit(1)

    from pydocs_mcp.application import (
        PackageLookupService,
        SearchApiService,
        SearchDocsService,
    )
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
        build_unified_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.wiring import build_retrieval_context
    from pydocs_mcp.storage.sqlite import (
        SqliteChunkRepository,
        SqlitePackageRepository,
    )

    config = AppConfig.load(explicit_path=config_path)
    context = build_retrieval_context(db_path, config)
    provider = context.connection_provider
    package_store = SqlitePackageRepository(provider=provider)
    chunk_store = SqliteChunkRepository(provider=provider)
    member_store = context.module_member_store
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)
    unified_pipeline = build_unified_pipeline_from_config(config, context)

    package_lookup = PackageLookupService(
        package_store=package_store,
        chunk_store=chunk_store,
        module_member_store=member_store,
    )
    search_docs_svc = SearchDocsService(chunk_pipeline=chunk_pipeline)
    search_api_svc = SearchApiService(member_pipeline=member_pipeline)

    # Optional services — present only if their sub-PR has landed.
    # #5 / #5b must explicitly export DocumentTreeService / ReferenceService
    # from application.__init__ for these imports to resolve.
    tree_svc = None
    ref_svc = None
    try:
        from pydocs_mcp.application import DocumentTreeService  # type: ignore[attr-defined]
        # tree_store wiring (sub-PR #5) would be resolved here when that PR lands.
        # Until then, tree_svc stays None.
    except ImportError:
        pass
    try:
        from pydocs_mcp.application import ReferenceService  # type: ignore[attr-defined]
    except ImportError:
        pass

    lookup_svc = LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )

    mcp = FastMCP("pydocs-mcp")

    @mcp.tool()
    async def search(
        query: str,
        kind: str = "any",
        package: str = "",
        scope: str = "all",
        limit: int = 10,
    ) -> str:
        """Full-text search over indexed docs and code (BM25 ranked).

        Use when the user describes a topic or keyword, not a specific target.

        Params:
          query:   search terms (space-separated)
          kind:    "docs" (prose/README) | "api" (functions/classes) | "any" (default)
          package: restrict to one package (e.g. "fastapi"); "" = all; "__project__" = your code
          scope:   "project" | "deps" | "all" (default)
          limit:   1–1000, default 10

        Examples:
          search(query="batch inference", kind="docs")
          search(query="HTTPBasicAuth", kind="api")
          search(query="retry logic", package="requests")
          search(query="parser", scope="project")

        For a specific known target (package, module, class, method), use lookup.
        """
        payload = SearchInput(query=query, kind=kind, package=package, scope=scope, limit=limit)
        try:
            return await _do_search(payload, search_docs_svc, search_api_svc, unified_pipeline)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("search failed unexpectedly")
            raise ServiceUnavailableError(f"search failed: {e}") from e

    @mcp.tool()
    async def lookup(target: str = "", show: str = "default") -> str:
        """Navigate to a specific named package/module/symbol; show its info or references.

        Use when the user names an exact target.

        Params:
          target: dotted path
            ""                                          → list all indexed packages
            "fastapi"                                   → package overview + deps
            "fastapi.routing"                           → module tree
            "fastapi.routing.APIRouter"                 → class + children
            "fastapi.routing.APIRouter.include_router"  → method details
          show: "default" | "tree" (full subtree)
                | "callers" (who calls this)
                | "callees" (what this calls)
                | "inherits" (base classes)

        Examples:
          lookup(target="")
          lookup(target="fastapi.routing.APIRouter")
          lookup(target="fastapi.routing.APIRouter.include_router", show="callers")
          lookup(target="requests.auth.HTTPBasicAuth", show="inherits")

        For keyword/topic search, use search.
        """
        payload = LookupInput(target=target, show=show)
        try:
            return await lookup_svc.lookup(payload)
        except MCPToolError:
            raise
        except Exception as e:
            log.exception("lookup failed unexpectedly")
            raise ServiceUnavailableError(f"lookup failed: {e}") from e

    log.info("MCP ready (db: %s)", db_path)
    mcp.run(transport="stdio")


async def _do_search(
    payload: SearchInput,
    search_docs_svc,
    search_api_svc,
    unified_pipeline,
) -> str:
    """Dispatch search by kind; returns rendered markdown."""
    query = _build_search_query(payload)
    if payload.kind == "docs":
        response = await search_docs_svc.search(query)
        return _render_search_response(response, empty_msg="No matches found.")
    if payload.kind == "api":
        response = await search_api_svc.search(query)
        return _render_search_response(response, empty_msg="No symbols found.")
    # kind == "any"
    state = await unified_pipeline.run(query)
    result = state.result
    if result is None or not result.items:
        return "No matches found."
    return result.items[0].text
```

- [ ] **Step 9.4: Run full test suite — verify no regressions**

```
pytest tests/ -x -q
```

Expected: all tests pass. Existing `test_server.py` tests may break — if they reference old tool names, update them to the new surface in the next task.

- [ ] **Step 9.5: Commit**

```bash
git add python/pydocs_mcp/server.py tests/test_mcp_surface.py
git commit -m "feat(#6): rewrite server.py — 5 MCP tools -> 2 (search, lookup)"
```

---

## Task 10: Rewrite `tests/test_server.py` for the 2-tool surface

**Files:**
- Modify / rewrite: `tests/test_server.py`

- [ ] **Step 10.1: Inspect existing tests for golden fixtures**

```bash
cat tests/test_server.py | head -40
```

Identify fixtures (likely an in-memory `FastMCP` server boot + sample DB). These stay; only tool-name references change.

- [ ] **Step 10.2: Update tool-name references globally**

Search & replace within `tests/test_server.py`:
- `list_packages()` → `lookup(target="")`
- `get_package_doc(package="X")` → `lookup(target="X")`
- `search_docs(query="X")` → `search(query="X", kind="docs")`
- `search_api(query="X")` → `search(query="X", kind="api")`
- `inspect_module(...)` → **delete the test** — feature dropped; add a comment above the deletion: `# inspect_module dropped in sub-PR #6 (see spec §1 breaking change)`

- [ ] **Step 10.3: Run test — verify passes**

```
pytest tests/test_server.py -v
```

Expected: all remaining tests PASS.

- [ ] **Step 10.4: Commit**

```bash
git add tests/test_server.py
git commit -m "test(#6): update test_server.py for 2-tool surface (search/lookup)"
```

---

## Task 11: CLI — rewrite `__main__.py` subcommands

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`

- [ ] **Step 11.1: Locate and inspect current subcommand wiring**

```bash
grep -n "def _cmd_query\|def _cmd_api\|add_parser" python/pydocs_mcp/__main__.py
```

Current structure: `_cmd_query` / `_cmd_api` functions; argparse subparser registration around line 41-70.

- [ ] **Step 11.2: Replace `_cmd_query` and `_cmd_api` with `_cmd_search` and `_cmd_lookup`**

Strategy: keep `_cmd_index` and `_cmd_serve` untouched. Write `_cmd_search` that builds a `SearchInput` and calls the same pipelines as the MCP handler; `_cmd_lookup` calls `LookupService`.

Add to `python/pydocs_mcp/__main__.py` (replacing `_cmd_query` around line 176-200):

```python
def _cmd_search(args: argparse.Namespace) -> int:
    """CLI: run the same search as the MCP tool."""
    import asyncio
    from pydocs_mcp.application import SearchInput
    from pydocs_mcp.server import _do_search  # noqa — reuse handler logic
    from pydocs_mcp.retrieval.config import (
        AppConfig, build_chunk_pipeline_from_config,
        build_member_pipeline_from_config, build_unified_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.wiring import build_retrieval_context
    from pydocs_mcp.application import SearchApiService, SearchDocsService

    db_path = Path(args.project).resolve()
    # (…reuse DB-path computation logic from _cmd_query…)
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    context = build_retrieval_context(db_path, config)
    search_docs_svc = SearchDocsService(chunk_pipeline=build_chunk_pipeline_from_config(config, context))
    search_api_svc = SearchApiService(member_pipeline=build_member_pipeline_from_config(config, context))
    unified_pipeline = build_unified_pipeline_from_config(config, context)

    payload = SearchInput(
        query=args.query,
        kind=args.kind,
        package=args.package or "",
        scope=args.scope or "all",
        limit=args.limit or 10,
    )
    output = asyncio.run(_do_search(payload, search_docs_svc, search_api_svc, unified_pipeline))
    print(output)
    return 0
```

```python
def _cmd_lookup(args: argparse.Namespace) -> int:
    """CLI: navigate to a specific target."""
    import asyncio
    from pydocs_mcp.application import (
        LookupInput, LookupService, PackageLookupService,
    )
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.wiring import build_retrieval_context
    from pydocs_mcp.storage.sqlite import (
        SqliteChunkRepository, SqlitePackageRepository,
    )

    db_path = Path(args.project).resolve()  # (…reuse logic…)
    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    context = build_retrieval_context(db_path, config)
    provider = context.connection_provider
    package_lookup = PackageLookupService(
        package_store=SqlitePackageRepository(provider=provider),
        chunk_store=SqliteChunkRepository(provider=provider),
        module_member_store=context.module_member_store,
    )
    svc = LookupService(package_lookup=package_lookup)  # tree_svc/ref_svc optional

    payload = LookupInput(target=args.target or "", show=args.show or "default")
    output = asyncio.run(svc.lookup(payload))
    print(output)
    return 0
```

- [ ] **Step 11.3: Update subparser registration**

In `python/pydocs_mcp/__main__.py` around line 41-70, replace the existing `query` and `api` subparsers with:

```python
sp_search = sub.add_parser("search", help="Full-text search over indexed docs/code")
sp_search.add_argument("query", help="Search terms (space-separated)")
sp_search.add_argument("--kind", choices=["docs", "api", "any"], default="any")
sp_search.add_argument("--package", default="", help="Restrict to one package")
sp_search.add_argument("--scope", choices=["project", "deps", "all"], default="all")
sp_search.add_argument("--limit", type=int, default=10)
sp_search.add_argument("project", nargs="?", default=".")
sp_search.set_defaults(func=_cmd_search)

sp_lookup = sub.add_parser("lookup", help="Navigate to a specific named target")
sp_lookup.add_argument("target", nargs="?", default="", help="Dotted path (empty = list)")
sp_lookup.add_argument(
    "--show",
    choices=["default", "tree", "callers", "callees", "inherits"],
    default="default",
)
sp_lookup.add_argument("project", nargs="?", default=".")
sp_lookup.set_defaults(func=_cmd_lookup)
```

Keep existing `index` and `serve` subparsers unchanged. Delete the `query` / `api` registrations.

- [ ] **Step 11.4: Update existing CLI tests**

```bash
pytest tests/test_cli.py -v
```

Any failing tests that reference `query` / `api` subcommands need rewording to `search` / `lookup`. Most assertions on output text will need the new tool-output shape.

- [ ] **Step 11.5: Run CLI tests — verify pass**

```
pytest tests/test_cli.py -v
```

Expected: PASS.

- [ ] **Step 11.6: Commit**

```bash
git add python/pydocs_mcp/__main__.py tests/test_cli.py
git commit -m "feat(#6): CLI — replace query/api subcommands with search/lookup"
```

---

## Task 12: Spec amendments — sub-PR #5 and #5b ACs

**Files:**
- Modify: `docs/superpowers/specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md`
- Modify: `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md`

These are documentation-only edits satisfying ACs #22, #23, #28 of the #6 spec.

- [ ] **Step 12.1: Amend #5 spec — add export AC**

Locate the acceptance-criteria section of `docs/superpowers/specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md` and append a new AC row:

```markdown
| Xb | `application/__init__.py` explicitly re-exports `DocumentTreeService` so `from pydocs_mcp.application import DocumentTreeService` resolves at sub-PR #6's wiring time. |
```

(Replace `Xb` with the next available AC number — likely around AC #21-22 given the current spec layout.)

- [ ] **Step 12.2: Amend #5b spec — add export AC**

Same in `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md`:

```markdown
| Xb | `application/__init__.py` explicitly re-exports `ReferenceService` so `from pydocs_mcp.application import ReferenceService` resolves at sub-PR #6's wiring time. |
```

- [ ] **Step 12.3: Commit**

```bash
git add docs/superpowers/specs/2026-04-20-sub-pr-5-extraction-strategies-and-document-tree-design.md \
        docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md
git commit -m "docs(#6): #5 + #5b ACs — require re-export of DocumentTreeService/ReferenceService"
```

---

## Task 13: Full-suite verification + spec parity sweep

**Files:** no new files; verification only.

- [ ] **Step 13.1: Run the full test suite**

```
pytest tests/ -x -q
```

Expected: ALL PASS. Fix any reference to old tool names that slipped through.

- [ ] **Step 13.2: Grep for dropped patterns**

```bash
# No blanket error-swallowing in server.py
grep -nE 'except Exception' python/pydocs_mcp/server.py

# No old tool names anywhere in the production code
grep -rn "search_docs\|search_api\|list_packages\|get_package_doc\|inspect_module" python/pydocs_mcp/ \
    | grep -v "search_docs_svc\|search_api_svc"  # these service names are fine
```

Both should return either nothing or only the service-name matches.

- [ ] **Step 13.3: Verify tool descriptions match spec §4.1 verbatim**

```bash
# Should show the spec's production-copy descriptions in server.py docstrings
grep -A 10 "Full-text search over indexed" python/pydocs_mcp/server.py
grep -A 10 "Navigate to a specific named" python/pydocs_mcp/server.py
```

- [ ] **Step 13.4: Final commit (if any drift fixes made)**

```bash
git status
# If dirty:
git add -A
git commit -m "chore(#6): final sweep — match spec §4.1 tool descriptions"
```

- [ ] **Step 13.5: Push**

```bash
git push origin HEAD
```

---

## Self-review checklist

- **Spec coverage** — scan spec §3 decisions:
  - Decision #1 (scope B) ✓ Task 1 (SCOPE), Task 2 (typed errors), Task 3 (Pydantic), Task 9 (handler error policy)
  - Decision #2 (Z5, 2 tools) ✓ Task 9 rewrites server.py
  - Decision #3 (lookup absorbs) ✓ Tasks 4-6 (LookupService dispatch covers all `show` modes)
  - Decision #4 (inspect_module dropped) ✓ Task 10 deletes its tests
  - Decision #5 (LLM-first descriptions) ✓ Task 9 embeds spec §4.1 copy
  - Decision #6 (format regex, not length) ✓ Task 3's validators
  - Decision #7 (`unified_search.yaml` for `kind="any"`) ✓ Task 7
  - Decision #8 (exception hierarchy) ✓ Task 2
  - Decision #9 (search empty = success, lookup missing = error) ✓ Task 6
  - Decision #10 (soft dependencies) ✓ Task 4's `Optional` params; Task 5's `find_module` fallback
- **Placeholder scan** — no "TBD/TODO/placeholder" in steps; code blocks present where steps require code; imports explicit; file paths absolute where possible.
- **Type consistency** — `SearchInput`/`LookupInput` field names consistent across Tasks 3, 9, 11; `find_module` signature the same in Tasks 1, 5, 6; `LookupService` constructor signature consistent across Tasks 4-6, 9, 11.
- **Sub-PR #4 amendment** — spec requires it (§§13b, AC #27); Task 1 implements both the code (`find_module` method) AND the spec-file amendment is carried in the already-committed fix round (commit `e50f013`). Plan does NOT re-amend #4's spec file.
- **ACs from spec** verified:
  - #1 (2 handlers only) ✓ Task 9
  - #2, #3, #4 (byte-parity success paths) ✓ Tasks 9, 10 via test_mcp_surface + test_server
  - #5-11 (lookup dispatch + degraded modes) ✓ Tasks 4, 5, 6
  - #12-18 (Pydantic + typed errors) ✓ Tasks 2, 3, 9
  - #19 (no blanket except) ✓ Task 13.2 grep
  - #20 (LookupService coverage) ✓ Tasks 4-6
  - #21 (CLI) ✓ Task 11
  - #22, #23, #28 (#5 + #5b AC amendments) ✓ Task 12
  - #24 (verbatim tool copy) ✓ Task 9, verified in 13.3
  - #25 (filter-field invariant) ✓ Task 1
  - #26 (find_module) ✓ Task 1
  - #27 (#4 spec edited) — already committed pre-plan (commit `e50f013`); not re-done by plan.
