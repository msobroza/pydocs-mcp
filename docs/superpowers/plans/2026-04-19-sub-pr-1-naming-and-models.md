# Sub-PR #1 — Naming sweep + canonical domain models — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace tuples and raw dicts flowing between modules with named dataclass domain models; apply a consistent naming convention across Python, Rust, and the SQLite schema — with no user-observable behavior change other than a one-time cache rebuild on first run after upgrade.

**Architecture:** Introduce a new `models.py` as the single source of truth for the domain vocabulary (per spec §5). Rust parser output becomes a small typed `ParsedMember` struct that the Python indexer converts into generic metadata-based `Chunk` / `ModuleMember` instances at the boundary. SQLite schema is versioned via `PRAGMA user_version`; on mismatch it drops and recreates the tables. All Python modules flow dataclasses internally — tuples confined to `db.py` row↔model helpers. MCP tool signatures stay byte-identical to `main`; tri-state `internal: bool | None` is converted to `SearchScope` inline per handler.

**Tech Stack:** Python 3.11+ (for `enum.StrEnum`), `@dataclass(frozen=True, slots=True)`, `pydantic.dataclasses` for `SearchQuery` validators, PyO3 / maturin for Rust, SQLite FTS5.

**Spec source of truth:** [`docs/superpowers/specs/2026-04-19-sub-pr-1-naming-and-models-design.md`](../specs/2026-04-19-sub-pr-1-naming-and-models-design.md). **§5 is the canonical data-model definition** — if anything in the body text of other spec sections conflicts with §5, §5 wins.

**Work location:** Worktree `.claude/worktrees/sub-pr-1-naming-and-models/` on branch `feature/sub-pr-1-naming-and-models`, draft PR [#13](https://github.com/msobroza/pydocs-mcp/pull/13).

**Deferred from §5 canonical to sub-PR #3/#4:** `SearchQuery._validate_filter_syntax` model-validator (depends on `pydocs_mcp.storage.filters.format_registry`, which is created in sub-PR #3). This plan ships `SearchQuery` with only the `terms` non-empty and `max_results` positive validators; a comment marks the filter-format validator as deferred.

---

## File structure

Files created:
- `python/pydocs_mcp/models.py` — dataclasses, StrEnums, `Parameter`
- `tests/test_models.py` — model construction, enum round-trips, row↔model round-trips, `user_version` upgrade

Files modified (rename + reshape):
- `src/lib.rs` — `Symbol` → `ParsedMember` (4 fields, no `#[new]`); `chunk_text` → `split_into_chunks`
- `python/pydocs_mcp/_fallback.py` — mirror Rust rename + function rename
- `python/pydocs_mcp/_fast.py` — update imports for the renames
- `python/pydocs_mcp/db.py` — `SCHEMA_VERSION = 2`, `user_version` drop-and-rebuild, new DDL, row↔model helpers, function renames
- `python/pydocs_mcp/deps.py` — `normalize`/`resolve`/helpers renamed
- `python/pydocs_mcp/indexer.py` — flow `ModuleMember` via metadata dict; function + local renames; `ParsedMember`→`ModuleMember` conversion at boundary
- `python/pydocs_mcp/search.py` — `retrieve_chunks` / `retrieve_module_members` returning `ChunkList` / `ModuleMemberList`; SQL uses new column names
- `python/pydocs_mcp/server.py` — handlers build `SearchQuery`, call retrievers, render from typed lists; MCP surface byte-identical
- `python/pydocs_mcp/__main__.py` — CLI output formatted from new result types; imports updated
- `pyproject.toml` — `requires-python = ">=3.11"`
- `CLAUDE.md` — Python version note + architecture section column/table names
- `.github/workflows/*.yml` — drop Python 3.10 from matrix; keep 3.11, 3.12
- All test files in `tests/` — **mechanical-only** updates (imports, renamed symbols/columns, `chunk_text` → `split_into_chunks`). No test deleted. All behavior assertions preserved.

---

## Task 0 — Baseline verification

**Files:** none (worktree setup check)

- [ ] **Step 0.1: Switch to the worktree**

```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-1-naming-and-models
```

- [ ] **Step 0.2: Install the project in editable mode (pure Python path first)**

```bash
pip install -e . && pip install pytest pydantic maturin
```

Expected: `Successfully installed pydocs-mcp-...`

- [ ] **Step 0.3: Run the test baseline**

```bash
pytest -q
```

Expected: green (record count; this becomes the invariant "behavior unchanged" test baseline). If any tests fail on `main`, stop and report — the refactor cannot start from a red baseline.

- [ ] **Step 0.4: Build the Rust native module (optional but recommended)**

```bash
maturin develop --release
pytest -q tests/test_fast_import.py tests/test_parity.py -v
```

Expected: Rust and fallback paths both import; parity tests pass.

- [ ] **Step 0.5: No commit at this stage** — baseline check only.

---

## Task 1 — Bump Python requirement to 3.11

**Files:**
- Modify: `pyproject.toml`
- Modify: `CLAUDE.md`

- [ ] **Step 1.1: Edit `pyproject.toml`** — change `requires-python = ">=3.10"` to `>=3.11`.

- [ ] **Step 1.2: Edit `CLAUDE.md`** — change "Python 3.10+ required" to "Python 3.11+ required" (appears once under "Key Technical Details").

- [ ] **Step 1.3: Verify baseline still passes**

```bash
pytest -q
```

Expected: green.

- [ ] **Step 1.4: Commit**

```bash
git add pyproject.toml CLAUDE.md
git commit -m "chore: bump requires-python to >=3.11 for enum.StrEnum"
```

---

## Task 2 — `models.py` foundation: enums

**Files:**
- Create: `python/pydocs_mcp/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 2.1: Write the failing enum tests** — create `tests/test_models.py`:

```python
"""Tests for domain models in pydocs_mcp.models.

Per sub-PR #1 spec §5, every enum subclasses enum.StrEnum and values round-trip
through SQLite TEXT columns, YAML, and JSON without glue code.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkOrigin,
    MemberKind,
    MetadataFilterFormat,
    ModuleMemberFilterField,
    PackageOrigin,
    SearchScope,
)


@pytest.mark.parametrize("enum_cls,value", [
    (ChunkOrigin, "project_module_doc"),
    (ChunkOrigin, "project_code_section"),
    (ChunkOrigin, "dependency_code_section"),
    (ChunkOrigin, "dependency_doc_file"),
    (ChunkOrigin, "dependency_readme"),
    (ChunkOrigin, "dependency_module_doc"),
    (ChunkOrigin, "composite_output"),
    (MemberKind, "function"),
    (MemberKind, "class"),
    (MemberKind, "method"),
    (PackageOrigin, "project"),
    (PackageOrigin, "dependency"),
    (SearchScope, "project_only"),
    (SearchScope, "dependencies_only"),
    (SearchScope, "all"),
    (MetadataFilterFormat, "multifield"),
    (MetadataFilterFormat, "filter_tree"),
    (MetadataFilterFormat, "chromadb"),
    (MetadataFilterFormat, "elasticsearch"),
    (MetadataFilterFormat, "qdrant"),
    (ChunkFilterField, "package"),
    (ChunkFilterField, "title"),
    (ChunkFilterField, "origin"),
    (ChunkFilterField, "module"),
    (ChunkFilterField, "scope"),
    (ModuleMemberFilterField, "package"),
    (ModuleMemberFilterField, "module"),
    (ModuleMemberFilterField, "name"),
    (ModuleMemberFilterField, "kind"),
])
def test_enum_value_roundtrip(enum_cls, value):
    """Every enum value round-trips: str ↔ enum member."""
    member = enum_cls(value)
    assert member.value == value
    assert str(member) == value
```

- [ ] **Step 2.2: Run the test to verify it fails**

```bash
pytest tests/test_models.py -v
```

Expected: FAIL — `ImportError: No module named 'pydocs_mcp.models'`.

- [ ] **Step 2.3: Create `python/pydocs_mcp/models.py`** with the enums from spec §5.1:

```python
"""Canonical domain models for pydocs-mcp.

This module is the single source of truth for the domain vocabulary — see
docs/superpowers/specs/2026-04-19-sub-pr-1-naming-and-models-design.md §5.

All dataclasses are frozen + slotted. All enums subclass enum.StrEnum so values
round-trip through SQLite TEXT columns and JSON without glue code.
"""
from __future__ import annotations

from enum import StrEnum


class ChunkOrigin(StrEnum):
    PROJECT_MODULE_DOC       = "project_module_doc"
    PROJECT_CODE_SECTION     = "project_code_section"
    DEPENDENCY_CODE_SECTION  = "dependency_code_section"
    DEPENDENCY_DOC_FILE      = "dependency_doc_file"
    DEPENDENCY_README        = "dependency_readme"
    DEPENDENCY_MODULE_DOC    = "dependency_module_doc"
    COMPOSITE_OUTPUT         = "composite_output"


class MemberKind(StrEnum):
    FUNCTION = "function"
    CLASS    = "class"
    METHOD   = "method"


class PackageOrigin(StrEnum):
    PROJECT    = "project"
    DEPENDENCY = "dependency"


class SearchScope(StrEnum):
    PROJECT_ONLY      = "project_only"
    DEPENDENCIES_ONLY = "dependencies_only"
    ALL               = "all"


class MetadataFilterFormat(StrEnum):
    MULTIFIELD    = "multifield"
    FILTER_TREE   = "filter_tree"
    CHROMADB      = "chromadb"
    ELASTICSEARCH = "elasticsearch"
    QDRANT        = "qdrant"


class ChunkFilterField(StrEnum):
    """Canonical metadata keys for Chunk queries (keys in the `metadata` mapping,
    not dataclass fields). Used by MCP handlers to build pre_filter dicts."""
    PACKAGE = "package"
    TITLE   = "title"
    ORIGIN  = "origin"
    MODULE  = "module"
    SCOPE   = "scope"


class ModuleMemberFilterField(StrEnum):
    PACKAGE = "package"
    MODULE  = "module"
    NAME    = "name"
    KIND    = "kind"
```

- [ ] **Step 2.4: Run the test to verify it passes**

```bash
pytest tests/test_models.py -v
```

Expected: all 29 parametrized cases PASS.

- [ ] **Step 2.5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add canonical StrEnums (spec §5.1)"
```

---

## Task 3 — `models.py`: `Parameter` and `Package`

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 3.1: Write the failing tests** — append to `tests/test_models.py`:

```python
from pydocs_mcp.models import Package, PackageOrigin, Parameter


def test_parameter_defaults():
    p = Parameter(name="prefix")
    assert p.name == "prefix"
    assert p.annotation == ""
    assert p.default == ""


def test_parameter_is_frozen():
    p = Parameter(name="prefix")
    with pytest.raises(Exception):
        p.name = "other"


def test_package_construction():
    pkg = Package(
        name="fastapi",
        version="0.104.1",
        summary="Web framework.",
        homepage="https://fastapi.tiangolo.com",
        dependencies=("starlette>=0.27",),
        content_hash="abc123",
        origin=PackageOrigin.DEPENDENCY,
    )
    assert pkg.kind == "package"
    assert pkg.dependencies == ("starlette>=0.27",)
    assert pkg.origin is PackageOrigin.DEPENDENCY


def test_package_is_frozen():
    pkg = Package(
        name="fastapi", version="0.1", summary="", homepage="",
        dependencies=(), content_hash="h", origin=PackageOrigin.DEPENDENCY,
    )
    with pytest.raises(Exception):
        pkg.name = "other"
```

- [ ] **Step 3.2: Run — expect fail (ImportError on `Package`, `Parameter`).**

```bash
pytest tests/test_models.py -v
```

- [ ] **Step 3.3: Edit `models.py`** — add below the enums:

```python
from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class Parameter:
    name: str
    annotation: str = ""
    default: str = ""


@dataclass(frozen=True, slots=True)
class Package:
    kind: ClassVar[str] = "package"
    name: str
    version: str
    summary: str
    homepage: str
    dependencies: tuple[str, ...]
    content_hash: str
    origin: PackageOrigin
```

- [ ] **Step 3.4: Run — expect pass.**

- [ ] **Step 3.5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add Parameter and Package value objects (spec §5.2)"
```

---

## Task 4 — `models.py`: `Chunk` and `ModuleMember` (generic, metadata-based)

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 4.1: Write the failing tests** — append:

```python
from pydocs_mcp.models import Chunk, ChunkOrigin, ModuleMember


def test_chunk_default_metadata_empty():
    c = Chunk(text="hello")
    assert c.kind == "chunk"
    assert c.text == "hello"
    assert c.id is None
    assert c.relevance is None
    assert c.retriever_name is None
    assert c.metadata == {}


def test_chunk_with_metadata_and_retrieval_fields():
    c = Chunk(
        text="body",
        id=7,
        relevance=0.93,
        retriever_name="fts5",
        metadata={
            "package": "fastapi",
            "title": "Routing",
            "origin": ChunkOrigin.DEPENDENCY_DOC_FILE.value,
        },
    )
    assert c.id == 7
    assert c.relevance == 0.93
    assert c.retriever_name == "fts5"
    assert c.metadata["origin"] == "dependency_doc_file"


def test_chunk_is_frozen():
    c = Chunk(text="x")
    with pytest.raises(Exception):
        c.text = "y"


def test_module_member_default_metadata_empty():
    m = ModuleMember()
    assert m.kind == "module_member"
    assert m.id is None
    assert m.metadata == {}


def test_module_member_with_metadata():
    m = ModuleMember(
        id=3,
        relevance=0.7,
        retriever_name="like",
        metadata={
            "package": "fastapi",
            "module": "fastapi.routing",
            "name": "APIRouter",
            "kind": "class",
            "signature": "(prefix: str = '')",
            "docstring": "Group endpoints.",
            "return_annotation": "",
            "parameters": (),
        },
    )
    assert m.metadata["name"] == "APIRouter"
    assert m.metadata["kind"] == "class"
```

- [ ] **Step 4.2: Run — expect fail.**

- [ ] **Step 4.3: Edit `models.py`** — add:

```python
from collections.abc import Mapping
from dataclasses import field
from typing import Any


@dataclass(frozen=True, slots=True)
class Chunk:
    """Unit of retrieval. `text` is the primary payload; everything else
    (package, title, origin, module) lives in metadata keyed by
    ChunkFilterField.*.value. Composite chunks (formatter output) set
    metadata['origin'] == ChunkOrigin.COMPOSITE_OUTPUT.value.

    Retrieval-time fields (relevance, retriever_name) are None until a
    retriever populates them."""
    kind: ClassVar[str] = "chunk"
    text: str
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ModuleMember:
    """A named Python API member (function, class, method). Fully generic —
    all structural fields (name, module, package, kind, signature, docstring,
    return_annotation, parameters) live in metadata. The Rust parser produces
    a typed ParsedMember (see _fallback.py / src/lib.rs); the indexer
    converts into this form at the boundary."""
    kind: ClassVar[str] = "module_member"
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

- [ ] **Step 4.4: Run — expect pass.**

- [ ] **Step 4.5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add Chunk and ModuleMember with metadata (spec §5.2)"
```

---

## Task 5 — `models.py`: list wrappers + `PipelineResultItem`

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 5.1: Write the failing tests** — append:

```python
from pydocs_mcp.models import ChunkList, ModuleMemberList, PipelineResultItem


def test_chunk_list_carries_kind():
    cl = ChunkList(items=(Chunk(text="a"), Chunk(text="b")))
    assert cl.kind == "chunk_list"
    assert len(cl.items) == 2


def test_module_member_list_carries_kind():
    ml = ModuleMemberList(items=(ModuleMember(), ModuleMember()))
    assert ml.kind == "module_member_list"
    assert len(ml.items) == 2


def test_pipeline_result_item_is_union():
    # Smoke check: both list wrappers are valid PipelineResultItem values.
    items: list[PipelineResultItem] = [ChunkList(items=()), ModuleMemberList(items=())]
    assert len(items) == 2
```

- [ ] **Step 5.2: Run — expect fail.**

- [ ] **Step 5.3: Edit `models.py`** — add:

```python
@dataclass(frozen=True, slots=True)
class ChunkList:
    kind: ClassVar[str] = "chunk_list"
    items: tuple[Chunk, ...]


@dataclass(frozen=True, slots=True)
class ModuleMemberList:
    kind: ClassVar[str] = "module_member_list"
    items: tuple[ModuleMember, ...]


PipelineResultItem = ChunkList | ModuleMemberList
```

- [ ] **Step 5.4: Run — expect pass.**

- [ ] **Step 5.5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add list wrappers + PipelineResultItem alias (spec §5.2)"
```

---

## Task 6 — `models.py`: `SearchQuery` (pydantic dataclass)

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 6.1: Write the failing tests** — append:

```python
from pydocs_mcp.models import MetadataFilterFormat, SearchQuery


def test_search_query_defaults():
    q = SearchQuery(terms="fastapi routing")
    assert q.terms == "fastapi routing"
    assert q.max_results == 8
    assert q.pre_filter is None
    assert q.post_filter is None
    assert q.pre_filter_format is MetadataFilterFormat.MULTIFIELD
    assert q.post_filter_format is MetadataFilterFormat.MULTIFIELD


def test_search_query_rejects_empty_terms():
    with pytest.raises(Exception):
        SearchQuery(terms="   ")


def test_search_query_rejects_non_positive_max_results():
    with pytest.raises(Exception):
        SearchQuery(terms="x", max_results=0)
    with pytest.raises(Exception):
        SearchQuery(terms="x", max_results=-1)


def test_search_query_carries_pre_filter_dict():
    q = SearchQuery(terms="x", pre_filter={"package": "fastapi"})
    assert q.pre_filter == {"package": "fastapi"}
```

- [ ] **Step 6.2: Run — expect fail (ImportError on `SearchQuery`).**

- [ ] **Step 6.3: Edit `models.py`** — add:

```python
from pydantic import field_validator
from pydantic.dataclasses import dataclass as pyd_dataclass


@pyd_dataclass(frozen=True, slots=True)
class SearchQuery:
    """Pydantic dataclass with construction-time validation.

    NOTE: The canonical spec §5.2 also defines a `_validate_filter_syntax`
    model-validator that validates pre_filter / post_filter against a
    format_registry from pydocs_mcp.storage.filters. That module lands in
    sub-PR #3; this validator is intentionally deferred until then.
    """
    terms: str
    max_results: int = 8
    pre_filter: Mapping[str, Any] | None = None
    post_filter: Mapping[str, Any] | None = None
    pre_filter_format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD
    post_filter_format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD

    @field_validator("terms")
    @classmethod
    def _terms_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("terms must be non-empty")
        return v

    @field_validator("max_results")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_results must be positive")
        return v
```

- [ ] **Step 6.4: Run — expect pass.**

- [ ] **Step 6.5: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add SearchQuery pydantic dataclass (spec §5.2)"
```

---

## Task 7 — `models.py`: `SearchResponse`

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

- [ ] **Step 7.1: Write the failing tests** — append:

```python
from pydocs_mcp.models import SearchResponse


def test_search_response_construction():
    q = SearchQuery(terms="x")
    cl = ChunkList(items=())
    r = SearchResponse(result=cl, query=q, duration_ms=12.5)
    assert r.result is cl
    assert r.query is q
    assert r.duration_ms == 12.5


def test_search_response_default_duration():
    r = SearchResponse(result=ModuleMemberList(items=()), query=SearchQuery(terms="x"))
    assert r.duration_ms == 0.0


def test_search_response_is_frozen():
    r = SearchResponse(result=ChunkList(items=()), query=SearchQuery(terms="x"))
    with pytest.raises(Exception):
        r.duration_ms = 1.0
```

- [ ] **Step 7.2: Run — expect fail.**

- [ ] **Step 7.3: Edit `models.py`** — add:

```python
@dataclass(frozen=True, slots=True)
class SearchResponse:
    result: PipelineResultItem
    query: SearchQuery
    duration_ms: float = 0.0
```

- [ ] **Step 7.4: Run — expect pass.**

- [ ] **Step 7.5: Full `test_models.py` sanity run**

```bash
pytest tests/test_models.py -v
```

Expected: every test from Tasks 2–7 green.

- [ ] **Step 7.6: Commit**

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): add SearchResponse (spec §5.2)"
```

---

## Task 8 — Rust: `Symbol` → `ParsedMember`; `chunk_text` → `split_into_chunks`

**Files:**
- Modify: `src/lib.rs`

- [ ] **Step 8.1: Edit `src/lib.rs`** — rename the struct and function. Keep the same 4 fields (`name`, `kind`, `signature`, `docstring`) and the same regex parsing. Replace every `Symbol` occurrence with `ParsedMember`; rename `#[pyfunction] chunk_text` → `split_into_chunks`; update the doc comment atop the file. Specific diff points:

```rust
// struct rename (around line 233)
#[pyclass]
#[derive(Clone)]
struct ParsedMember {
    #[pyo3(get)]
    name: String,
    #[pyo3(get)]
    kind: String,
    #[pyo3(get)]
    signature: String,
    #[pyo3(get)]
    docstring: String,
}

// parse_py_file's return type
fn parse_py_file(source: &str) -> Vec<ParsedMember> {
    // ... body unchanged, but the struct literal pushes ParsedMember
    symbols.push(ParsedMember { name, kind, signature, docstring });
    // ...
}

// chunk_text rename (around line 179-181)
#[pyfunction]
#[pyo3(signature = (text, max_chars=4000))]
fn split_into_chunks(text: &str, max_chars: usize) -> Vec<(String, String)> {
    // body unchanged
}

// module registration (bottom)
#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(walk_py_files, m)?)?;
    m.add_function(wrap_pyfunction!(hash_files, m)?)?;
    m.add_function(wrap_pyfunction!(split_into_chunks, m)?)?;
    m.add_function(wrap_pyfunction!(parse_py_file, m)?)?;
    m.add_function(wrap_pyfunction!(extract_module_doc, m)?)?;
    m.add_function(wrap_pyfunction!(read_file, m)?)?;
    m.add_function(wrap_pyfunction!(read_files_parallel, m)?)?;
    m.add_class::<ParsedMember>()?;
    Ok(())
}
```

Also update the file header comment to reflect the renames.

- [ ] **Step 8.2: Format + lint**

```bash
cargo fmt --check && cargo clippy -- -D warnings
```

Expected: both commands exit 0.

- [ ] **Step 8.3: Build the native module**

```bash
maturin develop --release
```

Expected: build succeeds; the new `_native.ParsedMember` and `_native.split_into_chunks` are importable.

- [ ] **Step 8.4: Smoke-test the import**

```bash
python -c "from pydocs_mcp._native import ParsedMember, split_into_chunks; print(ParsedMember.__name__, split_into_chunks.__name__)"
```

Expected: `ParsedMember split_into_chunks`.

- [ ] **Step 8.5: Commit**

```bash
git add src/lib.rs
git commit -m "refactor(rust): rename Symbol→ParsedMember, chunk_text→split_into_chunks"
```

Note: `_fast.py` import will be broken until Task 10 — tests will fail transiently until then. That's OK inside this multi-task flow; the commits are restored to green at the end of Task 10.

---

## Task 9 — `_fallback.py`: mirror Rust renames

**Files:**
- Modify: `python/pydocs_mcp/_fallback.py`

- [ ] **Step 9.1: Edit `_fallback.py`** — rename `Symbol` dataclass → `ParsedMember`; rename `chunk_text` → `split_into_chunks`. Same 4 fields, same function body:

```python
# around line 50-74: function rename
def split_into_chunks(text: str, max_chars: int = 4000) -> list[tuple[str, str]]:
    """Split text into (heading, body) tuples at heading boundaries."""
    # ... body unchanged ...


# around line 77-83: class rename
@dataclass
class ParsedMember:
    name: str
    kind: str
    signature: str
    docstring: str


# around line 85+: parse_py_file returns ParsedMember
def parse_py_file(source: str) -> list[ParsedMember]:
    # ... body unchanged, push ParsedMember(...) at the bottom ...
    symbols.append(ParsedMember(name, kind, f"({sig.strip()})", docstring))
```

- [ ] **Step 9.2: Commit (pause tests — `_fast.py` still broken)**

```bash
git add python/pydocs_mcp/_fallback.py
git commit -m "refactor(fallback): rename Symbol→ParsedMember, chunk_text→split_into_chunks"
```

---

## Task 10 — `_fast.py`: update imports for renames

**Files:**
- Modify: `python/pydocs_mcp/_fast.py`

- [ ] **Step 10.1: Edit `_fast.py`** — replace `Symbol` → `ParsedMember` and `chunk_text` → `split_into_chunks` in both the Rust-try block and the fallback block, and in the `disable_rust()` name tuple:

```python
try:
    from pydocs_mcp._native import (  # type: ignore[import]
        walk_py_files,
        hash_files,
        split_into_chunks,
        parse_py_file,
        extract_module_doc,
        read_file,
        read_files_parallel,
        ParsedMember,
    )
    RUST_AVAILABLE = True
    log.debug("Using Rust-accelerated functions")

except ImportError:
    from pydocs_mcp._fallback import (
        walk_py_files,
        hash_files,
        split_into_chunks,
        parse_py_file,
        extract_module_doc,
        read_file,
        read_files_parallel,
        ParsedMember,
    )
    RUST_AVAILABLE = False
    log.debug("Rust extension not found, using Python fallback")


def disable_rust() -> None:
    """..."""
    import pydocs_mcp._fast as mod
    from pydocs_mcp import _fallback
    for name in (
        "walk_py_files", "hash_files", "split_into_chunks", "parse_py_file",
        "extract_module_doc", "read_file", "read_files_parallel", "ParsedMember",
    ):
        setattr(mod, name, getattr(_fallback, name))
    mod.RUST_AVAILABLE = False
    log.info("Rust acceleration disabled, using Python fallback")
```

- [ ] **Step 10.2: Python import smoke test**

```bash
python -c "from pydocs_mcp._fast import ParsedMember, split_into_chunks; print('ok')"
```

Expected: `ok`.

- [ ] **Step 10.3: Commit**

```bash
git add python/pydocs_mcp/_fast.py
git commit -m "refactor(_fast): update imports for ParsedMember / split_into_chunks"
```

Note: `indexer.py` and `tests/*` still reference old names — those get fixed in their own tasks.

---

## Task 11 — `db.py`: `SCHEMA_VERSION = 2`, new DDL, drop-and-rebuild on mismatch

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Modify: `tests/test_models.py` (add upgrade test)

- [ ] **Step 11.1: Write the failing user_version upgrade test** — append to `tests/test_models.py`:

```python
import sqlite3
from pathlib import Path

from pydocs_mcp.db import SCHEMA_VERSION, open_index_database


def test_schema_version_upgrade_rebuilds(tmp_path: Path):
    """A DB created with user_version=0 and stale tables is dropped and rebuilt
    when opened by the current code."""
    db_file = tmp_path / "stale.db"
    con = sqlite3.connect(db_file)
    con.executescript("""
        PRAGMA user_version = 0;
        CREATE TABLE symbols (id INTEGER PRIMARY KEY, stale TEXT);
        INSERT INTO symbols (stale) VALUES ('old');
    """)
    con.commit()
    con.close()

    con2 = open_index_database(db_file)
    version = con2.execute("PRAGMA user_version").fetchone()[0]
    tables = {
        r[0]
        for r in con2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    con2.close()

    assert version == SCHEMA_VERSION
    assert "symbols" not in tables
    assert {"packages", "chunks", "module_members"}.issubset(tables)
```

- [ ] **Step 11.2: Run — expect fail (imports missing).**

- [ ] **Step 11.3: Rewrite `python/pydocs_mcp/db.py`** with the canonical schema from spec §5.4 plus a `SCHEMA_VERSION` constant and `open_index_database` that drops stale tables on version mismatch. Keep the keyword `open_db` absent in this task — Task 13 renames all the other functions. For now, just add `SCHEMA_VERSION`, `open_index_database`, and the new DDL; leave the other helpers alone (still referencing old column names — they'll be fixed in Task 12/13 when callers switch). Actually simpler: add `open_index_database` alongside existing `open_db` temporarily, have `open_db` be a deprecated thin wrapper, then remove in Task 13 once callers migrate. This keeps the tree building at every step.

Concretely:

```python
"""SQLite database with FTS5 full-text search.

Schema is versioned via PRAGMA user_version; a mismatch drops all tables and
recreates from the current DDL. See spec §5.4-5.5.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

CACHE_DIR = Path.home() / ".pydocs-mcp"

SCHEMA_VERSION = 2

_DDL = """
    CREATE TABLE packages (
        name TEXT PRIMARY KEY, version TEXT, summary TEXT,
        homepage TEXT, dependencies TEXT, content_hash TEXT, origin TEXT
    );
    CREATE TABLE chunks (
        id INTEGER PRIMARY KEY, package TEXT,
        title TEXT, text TEXT, origin TEXT
    );
    CREATE VIRTUAL TABLE chunks_fts USING fts5(
        title, text, package,
        content=chunks, content_rowid=id,
        tokenize='porter unicode61'
    );
    CREATE TABLE module_members (
        id INTEGER PRIMARY KEY, package TEXT, module TEXT,
        name TEXT, kind TEXT, signature TEXT,
        return_annotation TEXT, parameters TEXT, docstring TEXT
    );
    CREATE INDEX ix_chunks_package         ON chunks(package);
    CREATE INDEX ix_module_members_package ON module_members(package);
    CREATE INDEX ix_module_members_name    ON module_members(name);
"""

_KNOWN_TABLES = ("chunks_fts", "chunks", "module_members", "packages", "symbols")


def db_path_for(project_dir: Path) -> Path:
    """(Deprecated name; Task 13 renames to cache_path_for_project.)"""
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.db"


def _drop_all_known_tables(conn: sqlite3.Connection) -> None:
    for tbl in _KNOWN_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {tbl}")


def open_index_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database, rebuilding if PRAGMA user_version differs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current != SCHEMA_VERSION:
        _drop_all_known_tables(conn)
        conn.executescript(_DDL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    conn.commit()
    return conn


# Thin shim; Task 13 deletes this.
def open_db(path: Path) -> sqlite3.Connection:
    return open_index_database(path)
```

- [ ] **Step 11.4: Run — expect pass.**

- [ ] **Step 11.5: Commit**

```bash
git add python/pydocs_mcp/db.py tests/test_models.py
git commit -m "feat(db): SCHEMA_VERSION=2, drop-and-rebuild on user_version mismatch (spec §5.4-5.5)"
```

---

## Task 12 — `db.py`: row↔model mapping helpers

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Modify: `tests/test_models.py`

- [ ] **Step 12.1: Write the failing round-trip tests** — append:

```python
import json

from pydocs_mcp.db import (
    _chunk_to_row,
    _module_member_to_row,
    _package_to_row,
    _row_to_chunk,
    _row_to_module_member,
    _row_to_package,
)
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkOrigin,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)


def test_chunk_row_roundtrip():
    c = Chunk(
        text="body",
        id=1,
        metadata={
            ChunkFilterField.PACKAGE.value: "fastapi",
            ChunkFilterField.TITLE.value: "Routing",
            ChunkFilterField.ORIGIN.value: ChunkOrigin.DEPENDENCY_DOC_FILE.value,
        },
    )
    row = _chunk_to_row(c)
    c2 = _row_to_chunk(row)
    assert c2.id == 1
    assert c2.text == "body"
    assert c2.metadata["package"] == "fastapi"
    assert c2.metadata["title"] == "Routing"
    assert c2.metadata["origin"] == "dependency_doc_file"


def test_module_member_row_roundtrip():
    m = ModuleMember(
        id=5,
        metadata={
            ModuleMemberFilterField.PACKAGE.value: "fastapi",
            ModuleMemberFilterField.MODULE.value: "fastapi.routing",
            ModuleMemberFilterField.NAME.value: "APIRouter",
            ModuleMemberFilterField.KIND.value: "class",
            "signature": "(prefix: str = '')",
            "docstring": "Groups endpoints.",
            "return_annotation": "",
            "parameters": (Parameter(name="prefix", default='""'),),
        },
    )
    row = _module_member_to_row(m)
    m2 = _row_to_module_member(row)
    assert m2.id == 5
    assert m2.metadata["name"] == "APIRouter"
    assert m2.metadata["kind"] == "class"
    # parameters round-trip through JSON
    ps = m2.metadata["parameters"]
    assert len(ps) == 1
    assert ps[0].name == "prefix"
    assert ps[0].default == '""'


def test_package_row_roundtrip():
    pkg = Package(
        name="fastapi", version="0.1", summary="", homepage="",
        dependencies=("starlette",), content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )
    row = _package_to_row(pkg)
    pkg2 = _row_to_package(row)
    assert pkg2 == pkg
```

- [ ] **Step 12.2: Run — expect fail (imports missing).**

- [ ] **Step 12.3: Edit `db.py`** — append the mapping helpers:

```python
import json

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkOrigin,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)


# ── Chunk ↔ row ──────────────────────────────────────────────────────────
def _chunk_to_row(c: Chunk) -> dict[str, object]:
    md = c.metadata
    return {
        "id": c.id,
        "package": md.get(ChunkFilterField.PACKAGE.value, ""),
        "title":   md.get(ChunkFilterField.TITLE.value, ""),
        "text":    c.text,
        "origin":  md.get(ChunkFilterField.ORIGIN.value, ""),
    }


def _row_to_chunk(row) -> Chunk:
    metadata: dict[str, object] = {}
    for key in (
        ChunkFilterField.PACKAGE.value,
        ChunkFilterField.TITLE.value,
        ChunkFilterField.ORIGIN.value,
    ):
        value = row[key] if key in row.keys() else None
        if value:
            metadata[key] = value
    return Chunk(
        text=row["text"] or "",
        id=row["id"],
        metadata=metadata,
    )


# ── ModuleMember ↔ row ───────────────────────────────────────────────────
def _module_member_to_row(m: ModuleMember) -> dict[str, object]:
    md = m.metadata
    params = md.get("parameters", ())
    # Serialize Parameter dataclasses as JSON (name/annotation/default).
    params_json = json.dumps(
        [
            {"name": p.name, "annotation": p.annotation, "default": p.default}
            if isinstance(p, Parameter)
            else p
            for p in params
        ]
    )
    return {
        "id": m.id,
        "package": md.get(ModuleMemberFilterField.PACKAGE.value, ""),
        "module":  md.get(ModuleMemberFilterField.MODULE.value, ""),
        "name":    md.get(ModuleMemberFilterField.NAME.value, ""),
        "kind":    md.get(ModuleMemberFilterField.KIND.value, ""),
        "signature":         md.get("signature", ""),
        "return_annotation": md.get("return_annotation", ""),
        "parameters":        params_json,
        "docstring":         md.get("docstring", ""),
    }


def _row_to_module_member(row) -> ModuleMember:
    raw_params = json.loads(row["parameters"] or "[]")
    params = tuple(
        Parameter(name=p["name"], annotation=p.get("annotation", ""), default=p.get("default", ""))
        for p in raw_params
    )
    metadata = {
        ModuleMemberFilterField.PACKAGE.value: row["package"] or "",
        ModuleMemberFilterField.MODULE.value:  row["module"] or "",
        ModuleMemberFilterField.NAME.value:    row["name"] or "",
        ModuleMemberFilterField.KIND.value:    row["kind"] or "",
        "signature":         row["signature"] or "",
        "return_annotation": row["return_annotation"] or "",
        "parameters":        params,
        "docstring":         row["docstring"] or "",
    }
    return ModuleMember(id=row["id"], metadata=metadata)


# ── Package ↔ row ────────────────────────────────────────────────────────
def _package_to_row(pkg: Package) -> dict[str, object]:
    return {
        "name": pkg.name,
        "version": pkg.version,
        "summary": pkg.summary,
        "homepage": pkg.homepage,
        "dependencies": json.dumps(list(pkg.dependencies)),
        "content_hash": pkg.content_hash,
        "origin": pkg.origin.value,
    }


def _row_to_package(row) -> Package:
    return Package(
        name=row["name"],
        version=row["version"] or "",
        summary=row["summary"] or "",
        homepage=row["homepage"] or "",
        dependencies=tuple(json.loads(row["dependencies"] or "[]")),
        content_hash=row["content_hash"] or "",
        origin=PackageOrigin(row["origin"] or PackageOrigin.DEPENDENCY.value),
    )
```

- [ ] **Step 12.4: Run — expect pass for the round-trip tests. (Existing tests may still be red — that's fine until Task 19.)**

- [ ] **Step 12.5: Commit**

```bash
git add python/pydocs_mcp/db.py tests/test_models.py
git commit -m "feat(db): row↔model mapping helpers for Chunk/ModuleMember/Package"
```

---

## Task 13 — `db.py`: function renames + delete shims

**Files:**
- Modify: `python/pydocs_mcp/db.py`
- Modify: every caller (temporarily; callers are fully rewritten in their own tasks — keep grep narrow here)

- [ ] **Step 13.1: Rename functions in `db.py`** — delete the `open_db` shim and the old helper names; add the canonical names:

```python
def cache_path_for_project(project_dir: Path) -> Path:
    """Per-project cache file path inside ~/.pydocs-mcp/."""
    slug = hashlib.md5(str(project_dir.resolve()).encode()).hexdigest()[:10]
    return CACHE_DIR / f"{project_dir.resolve().name}_{slug}.db"


def remove_package(connection: sqlite3.Connection, package_name: str) -> None:
    """Remove all data for one package."""
    connection.execute("DELETE FROM chunks         WHERE package=?", (package_name,))
    connection.execute("DELETE FROM module_members WHERE package=?", (package_name,))
    connection.execute("DELETE FROM packages       WHERE name=?",    (package_name,))


def clear_all_packages(connection: sqlite3.Connection) -> None:
    """Clear every table (packages, chunks, module_members)."""
    connection.execute("DELETE FROM packages")
    connection.execute("DELETE FROM chunks")
    connection.execute("DELETE FROM module_members")
    connection.commit()


def rebuild_fulltext_index(connection: sqlite3.Connection) -> None:
    """Rebuild the FTS5 index after bulk writes."""
    connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    connection.commit()


def get_stored_content_hash(connection: sqlite3.Connection, package_name: str) -> str | None:
    """Return the cached content_hash for a package, or None if not indexed."""
    row = connection.execute(
        "SELECT content_hash FROM packages WHERE name=?", (package_name,)
    ).fetchone()
    return row["content_hash"] if row else None
```

Delete `db_path_for`, `clear_pkg`, `clear_all`, `rebuild_fts`, `get_cached_hash`, `open_db`.

- [ ] **Step 13.2: Commit (the tree is intentionally broken until callers catch up in Tasks 14-18)**

```bash
git add python/pydocs_mcp/db.py
git commit -m "refactor(db): rename public functions to canonical names (spec §6.3)"
```

---

## Task 14 — `deps.py`: renames

**Files:**
- Modify: `python/pydocs_mcp/deps.py`

- [ ] **Step 14.1: Rename in `deps.py`**:
- `normalize` → `normalize_package_name`
- `resolve` → `discover_declared_dependencies`
- `_find_dep_files` → `list_dependency_manifest_files` (now public; use it verbatim)
- `_parse_toml` → `parse_pyproject_dependencies`
- `_parse_requirements` → `parse_requirements_file`

Replace internal call sites in the same file. Rename local variables per §6.4 where they appear (`all_deps` stays; `found` → keep or rename to `manifest_paths`; follow spec only where listed).

- [ ] **Step 14.2: Commit (tree still broken — callers land next)**

```bash
git add python/pydocs_mcp/deps.py
git commit -m "refactor(deps): rename public functions to canonical names (spec §6.3)"
```

---

## Task 15 — `indexer.py`: flow `ModuleMember` with metadata, function + local renames

**Files:**
- Modify: `python/pydocs_mcp/indexer.py`

This is the largest single task — touches imports, function names, local variables, and the `ParsedMember`→`ModuleMember` boundary conversion.

- [ ] **Step 15.1: Update the imports block at the top of `indexer.py`**:

```python
from pydocs_mcp._fast import (
    split_into_chunks,
    extract_module_doc,
    hash_files,
    parse_py_file,
    read_files_parallel,
    walk_py_files,
)
# ... constants unchanged ...
from pydocs_mcp.db import remove_package, get_stored_content_hash
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkOrigin,
    MemberKind,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
    Parameter,
)
```

- [ ] **Step 15.2: Rename module-level functions per spec §6.3**:
- `index_project` → `index_project_source`
- `index_deps` → `index_dependencies`
- `_parse_source_files` → `_extract_from_source_files`
- `_base_data` → `_build_package_record`
- `_add_doc_files` → `_append_doc_file_chunks`
- `_write_dep` → `_persist_dependency`
- `_collect_inspect` → `_extract_by_import`
- `_collect_static` → `_extract_from_static_sources`
- `_dep_py_files` → `list_dependency_source_files`
- `_site_packages_root` → `find_site_packages_root`
- `_inspect_syms` → `_extract_members_by_import`
- `_get_sig` → `_extract_callable_signature`

Use grep to confirm no residual old names remain:

```bash
grep -n "def index_project\|def index_deps\|def _parse_source_files\|def _base_data\|def _add_doc_files\|def _write_dep\|def _collect_inspect\|def _collect_static\|def _dep_py_files\|def _site_packages_root\|def _inspect_syms\|def _get_sig" python/pydocs_mcp/indexer.py
```

Expected: no matches.

- [ ] **Step 15.3: Replace tuples/dicts with typed models** — wherever the indexer writes rows, it should build `Chunk` / `ModuleMember` / `Package` via `models.py`, then use `db._chunk_to_row`, `db._module_member_to_row`, `db._package_to_row` before the SQL `INSERT`. The previous regex `parse_py_file(source)` now returns `list[ParsedMember]` (or `_native.ParsedMember`). Convert at the boundary:

```python
# Inside whichever function consumes parse_py_file output (was _parse_source_files).
# Previously stored tuples; now build Python ModuleMember.
for pm in parse_py_file(source):
    kind_enum = MemberKind.CLASS if pm.kind == "class" else MemberKind.FUNCTION
    member = ModuleMember(metadata={
        ModuleMemberFilterField.PACKAGE.value: package_name,
        ModuleMemberFilterField.MODULE.value:  module_name,
        ModuleMemberFilterField.NAME.value:    pm.name,
        ModuleMemberFilterField.KIND.value:    kind_enum.value,
        "signature":         pm.signature,
        "return_annotation": "",
        "parameters":        (),
        "docstring":         pm.docstring,
    })
    row = _module_member_to_row(member)
    connection.execute(
        "INSERT INTO module_members "
        "(package, module, name, kind, signature, return_annotation, parameters, docstring) "
        "VALUES (:package, :module, :name, :kind, :signature, :return_annotation, :parameters, :docstring)",
        row,
    )
```

For chunks: wrap `split_into_chunks(...)` output (list of `(heading, body)` tuples) into `Chunk` with metadata keyed by `ChunkFilterField`:

```python
for title, text in split_into_chunks(doc_text):
    chunk = Chunk(text=text, metadata={
        ChunkFilterField.PACKAGE.value: package_name,
        ChunkFilterField.TITLE.value:   title,
        ChunkFilterField.ORIGIN.value:  ChunkOrigin.DEPENDENCY_DOC_FILE.value,  # or the right origin per caller
    })
    row = _chunk_to_row(chunk)
    connection.execute(
        "INSERT INTO chunks (package, title, text, origin) "
        "VALUES (:package, :title, :text, :origin)",
        row,
    )
```

For packages: build `Package` then `_package_to_row`. The `_base_data` renamed to `_build_package_record` should return a `Package` instance (not a dict) — its caller does the row conversion.

For inspect-mode members (`_extract_by_import` → previously `_collect_inspect` → previously `_inspect_syms`): same conversion pattern; call with `kind_enum = MemberKind.METHOD` for class methods, `CLASS` for classes, `FUNCTION` for functions. Extract `return_annotation` from `inspect.signature(obj).return_annotation` (already done — just feed into the metadata dict under `"return_annotation"`).

`Parameter` objects are built from `inspect.Parameter` objects and stuffed into `metadata["parameters"]` as a tuple. The `_extract_callable_signature` helper should produce both the signature string and the tuple of `Parameter`s.

- [ ] **Step 15.4: Rename local variables per spec §6.4** where they appear:
- `conn` → `connection`
- `pkg` / `n` → `package_name`
- `sym_rows` / `chunk_rows` → `module_members` / `chunks`
- `dists` → `installed_distributions`
- `work` → `packages_to_index`
- `rel` → `relative_path`
- `mod` / `mo` → `module` / `member`
- `ms` → `method_summaries`
- `data` → `package_record`

- [ ] **Step 15.5: Run focused tests** (indexer unit tests)

```bash
pytest tests/test_indexer.py tests/test_indexer_extended.py -v
```

Expected: tests that haven't been updated yet will likely fail due to renamed imports. That's fine; Task 19 mechanically fixes them. Only fix here tests whose failure is logic — not naming.

- [ ] **Step 15.6: Commit**

```bash
git add python/pydocs_mcp/indexer.py
git commit -m "refactor(indexer): flow ModuleMember via metadata; rename functions/locals (spec §6)"
```

---

## Task 16 — `search.py`: typed returns, function renames, new column names

**Files:**
- Modify: `python/pydocs_mcp/search.py`

- [ ] **Step 16.1: Rewrite `search.py`** — function signatures per §6.3, SQL per §5.4, typed returns per §5.2:

```python
"""Retrieval functions: FTS5 for Chunks, LIKE for ModuleMembers.

Returns typed ChunkList / ModuleMemberList per spec §5.2. Retrieval-time
fields (relevance, retriever_name) are populated on each Chunk / ModuleMember
directly — there is no SearchMatch wrapper.
"""
from __future__ import annotations

import sqlite3

from pydocs_mcp.constants import CONTEXT_TOKEN_BUDGET
from pydocs_mcp.db import _row_to_chunk, _row_to_module_member
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)

_CHARS_PER_TOKEN = 4


def _apply_scope(where: list[str], scope: SearchScope, column: str) -> None:
    if scope is SearchScope.PROJECT_ONLY:
        where.append(f"{column} = '__project__'")
    elif scope is SearchScope.DEPENDENCIES_ONLY:
        where.append(f"{column} != '__project__'")
    # ALL → no clause


def retrieve_chunks(connection: sqlite3.Connection, query: SearchQuery) -> ChunkList:
    """BM25 full-text search over indexed chunks.

    Reads pre_filter for ChunkFilterField.PACKAGE (single package restriction),
    ChunkFilterField.TITLE (substring LIKE on chunk title), and
    ChunkFilterField.SCOPE (SearchScope value for project/deps gating).
    """
    _FTS_OPS = {"OR", "AND", "NOT"}
    tokens = query.terms.split()
    if any(t in _FTS_OPS for t in tokens):
        fulltext_query = query.terms
    else:
        words = [w for w in tokens if len(w) > 1]
        if not words:
            return ChunkList(items=())
        fulltext_query = " OR ".join(f'"{w}"' for w in words)

    where: list[str] = ["chunks_fts MATCH ?"]
    params: list = [fulltext_query]

    pf = query.pre_filter or {}
    package = pf.get(ChunkFilterField.PACKAGE.value)
    if package is not None:
        literal_package = package if package == "__project__" else normalize_package_name(package)
        where.append("c.package = ?")
        params.append(literal_package)

    scope_value = pf.get(ChunkFilterField.SCOPE.value)
    if scope_value is not None:
        _apply_scope(where, SearchScope(scope_value), "c.package")

    title = pf.get(ChunkFilterField.TITLE.value)
    if title:
        escaped_title = title.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("c.title LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped_title}%")

    params.append(query.max_results)
    sql = (
        "SELECT c.id, c.package, c.title, c.text, c.origin, -m.rank AS rank"
        " FROM chunks_fts m JOIN chunks c ON c.id = m.rowid"
        f" WHERE {' AND '.join(where)}"
        " ORDER BY rank LIMIT ?"
    )
    try:
        rows = connection.execute(sql, params).fetchall()
    except Exception:
        return ChunkList(items=())
    items: list[Chunk] = []
    for row in rows:
        chunk = _row_to_chunk(row)
        # Populate retrieval-time fields on a new Chunk (frozen).
        items.append(
            Chunk(
                text=chunk.text,
                id=chunk.id,
                relevance=float(row["rank"]),
                retriever_name="fts5",
                metadata=chunk.metadata,
            )
        )
    return ChunkList(items=tuple(items))


def retrieve_module_members(connection: sqlite3.Connection, query: SearchQuery) -> ModuleMemberList:
    """Substring LIKE search on module member name + docstring.

    Reads pre_filter for ModuleMemberFilterField.PACKAGE and ChunkFilterField.SCOPE
    (scope is the same enum for both retrievers).
    """
    escaped = query.terms.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pat = f"%{escaped}%"

    where: list[str] = ["(lower(name) LIKE ? ESCAPE '\\' OR lower(docstring) LIKE ? ESCAPE '\\')"]
    params: list = [pat, pat]

    pf = query.pre_filter or {}
    package = pf.get(ModuleMemberFilterField.PACKAGE.value)
    if package is not None:
        literal_package = package if package == "__project__" else normalize_package_name(package)
        where.append("package = ?")
        params.append(literal_package)

    scope_value = pf.get(ChunkFilterField.SCOPE.value)
    if scope_value is not None:
        _apply_scope(where, SearchScope(scope_value), "package")

    params.append(query.max_results)
    sql = (
        "SELECT id, package, module, name, kind, signature,"
        "       return_annotation, parameters, docstring"
        "  FROM module_members"
        f" WHERE {' AND '.join(where)}"
        " LIMIT ?"
    )
    try:
        rows = connection.execute(sql, params).fetchall()
    except Exception:
        return ModuleMemberList(items=())
    items: list[ModuleMember] = []
    for row in rows:
        member = _row_to_module_member(row)
        items.append(
            ModuleMember(
                id=member.id,
                relevance=None,  # LIKE doesn't score; may fill in when hybrid retriever lands
                retriever_name="like",
                metadata=member.metadata,
            )
        )
    return ModuleMemberList(items=tuple(items))


def format_within_budget(chunks: ChunkList, max_tokens: int = CONTEXT_TOKEN_BUDGET) -> str:
    """Concatenate chunks into a single text blob within the token budget.

    Pulls title and text from each Chunk's metadata / text field. Stops when
    the accumulated size would exceed the budget; truncates the last chunk
    only if at least 100 chars remain.
    """
    max_chars = max_tokens * _CHARS_PER_TOKEN
    parts: list[str] = []
    total = 0
    for chunk in chunks.items:
        title = chunk.metadata.get(ChunkFilterField.TITLE.value, "") or ""
        text = chunk.text or ""
        piece = f"## {title}\n{text}\n"
        if total + len(piece) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                parts.append(piece[:remaining])
            break
        parts.append(piece)
        total += len(piece)
    return "\n".join(parts)
```

- [ ] **Step 16.2: Run focused tests**

```bash
pytest tests/test_search.py tests/test_search_extended.py -v
```

Expect naming-related failures (test updates land in Task 19); logic failures should be zero.

- [ ] **Step 16.3: Commit**

```bash
git add python/pydocs_mcp/search.py
git commit -m "refactor(search): typed ChunkList/ModuleMemberList returns; rename fns; new columns (spec §6)"
```

---

## Task 17 — `server.py`: MCP handlers use models; inline `internal` → `SearchScope`

**Files:**
- Modify: `python/pydocs_mcp/server.py`

MCP surface is byte-identical — tool names, parameter names, type annotations, docstrings, and return string shapes must not change.

- [ ] **Step 17.1: Update imports**:

```python
from pydocs_mcp.db import open_index_database
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.search import (
    format_within_budget,
    retrieve_chunks,
    retrieve_module_members,
)
```

- [ ] **Step 17.2: Replace `open_db` calls with `open_index_database`** throughout the module.

- [ ] **Step 17.3: Build a helper for inline scope conversion** — at the top of `run()` (after `FastMCP` import), add:

```python
def _scope_from_internal(internal: bool | None) -> SearchScope:
    """Convert the tri-state MCP parameter to a SearchScope value."""
    if internal is True:
        return SearchScope.PROJECT_ONLY
    if internal is False:
        return SearchScope.DEPENDENCIES_ONLY
    return SearchScope.ALL
```

Spec §2 forbids any helper name containing "legacy"; `_scope_from_internal` is acceptable.

- [ ] **Step 17.4: Rewrite `search_docs` body** — keep the external signature; internally build a `SearchQuery` with `pre_filter` dict and call `retrieve_chunks`:

```python
@mcp.tool()
async def search_docs(
    query: str,
    package: str = "",
    internal: bool | None = None,
    topic: str = "",
) -> str:
    """Search documentation and source chunks with BM25 ranking.

    Args:
        query: Search terms (space-separated words, OR logic).
        package: Restrict to a specific package name. Leave empty for all packages.
        internal: True → search only the project's own source; False → search only
            dependency packages; omit (None) → search everything.
        topic: If given, restrict to chunks whose heading contains this string.
    """
    scope = _scope_from_internal(internal)
    pre_filter: dict[str, object] = {ChunkFilterField.SCOPE.value: scope.value}
    if package.strip():
        pre_filter[ChunkFilterField.PACKAGE.value] = package.strip()
    if topic.strip():
        pre_filter[ChunkFilterField.TITLE.value] = topic.strip()
    search_query = SearchQuery(terms=query, pre_filter=pre_filter)

    connection = await asyncio.to_thread(open_index_database, db_path)
    try:
        chunks = await asyncio.to_thread(retrieve_chunks, connection, search_query)
    finally:
        connection.close()
    if not chunks.items:
        return "No matches found."
    return format_within_budget(chunks)
```

- [ ] **Step 17.5: Rewrite `search_api` body** — same pattern:

```python
@mcp.tool()
async def search_api(
    query: str,
    package: str = "",
    internal: bool | None = None,
) -> str:
    """Search symbols (functions, classes) by name or docstring.

    Args:
        query: Name fragment or docstring keyword to search for.
        package: Restrict to a specific package name. Leave empty for all packages.
        internal: True → project symbols only; False → dependency symbols only;
            omit (None) → all symbols.
    """
    scope = _scope_from_internal(internal)
    pre_filter: dict[str, object] = {ChunkFilterField.SCOPE.value: scope.value}
    if package.strip():
        pre_filter[ModuleMemberFilterField.PACKAGE.value] = package.strip()
    search_query = SearchQuery(terms=query, pre_filter=pre_filter, max_results=SEARCH_RESULTS_MAX)

    connection = await asyncio.to_thread(open_index_database, db_path)
    try:
        result = await asyncio.to_thread(retrieve_module_members, connection, search_query)
    finally:
        connection.close()
    if not result.items:
        return "No symbols found."
    lines = []
    for member in result.items:
        md = member.metadata
        name = md.get("name", "")
        sig = f"{name}{md.get('signature', '')}" if md.get("signature") else name
        ret = f" -> {md['return_annotation']}" if md.get("return_annotation") else ""
        doc = (md.get("docstring") or "")[:SEARCH_DOC_DISPLAY]
        lines.append(
            f"**`[{md.get('package', '')}] {md.get('module', '')}.{sig}{ret}`** "
            f"({md.get('kind', '')})\n{doc}"
        )
    return "\n\n---\n\n".join(lines)
```

- [ ] **Step 17.6: Update `list_packages`, `get_package_doc`, `inspect_module`** — same SQL column renames (`pkg` → `package`, `heading` → `title`, `body` → `text`, `requires` → `dependencies`, `hash` → `content_hash`, `symbols` → `module_members`, `returns` → `return_annotation`, `params` → `parameters`, `doc` → `docstring`). Keep docstrings and return strings textually identical (tool surface unchanged).

- [ ] **Step 17.7: Commit**

```bash
git add python/pydocs_mcp/server.py
git commit -m "refactor(server): handlers use models internally; inline SearchScope (MCP surface unchanged)"
```

---

## Task 18 — `__main__.py`: CLI output from new result types

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`

- [ ] **Step 18.1: Update imports**:

```python
from pydocs_mcp.db import (
    cache_path_for_project,
    clear_all_packages,
    open_index_database,
    rebuild_fulltext_index,
)
from pydocs_mcp.deps import discover_declared_dependencies
from pydocs_mcp.indexer import index_dependencies, index_project_source
from pydocs_mcp.models import SearchQuery
from pydocs_mcp.search import retrieve_chunks, retrieve_module_members
```

- [ ] **Step 18.2: Rewrite the body**:

```python
# ... argparse unchanged ...

project = Path(getattr(args, "project", ".")).resolve()
db_path = cache_path_for_project(project)

if args.cmd in ("serve", "index"):
    connection = open_index_database(db_path)

    if args.force:
        clear_all_packages(connection)
        log.info("Cache cleared")

    if not args.skip_project:
        log.info("Project: %s", project)
        index_project_source(connection, project)

    dependencies = discover_declared_dependencies(str(project))
    if dependencies:
        use_inspect = not args.no_inspect
        stats = index_dependencies(connection, dependencies, args.depth, args.workers, use_inspect)
        log.info(
            "Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
            stats["indexed"], stats["cached"], stats["failed"],
            db_path.stat().st_size / 1024,
        )

    rebuild_fulltext_index(connection)
    connection.close()

    if args.cmd == "serve":
        run(db_path)

elif args.cmd in ("query", "api"):
    connection = open_index_database(db_path)
    terms = " ".join(args.terms)
    search_query = SearchQuery(
        terms=terms,
        pre_filter={ChunkFilterField.PACKAGE.value: args.package} if args.package else None,
    )

    if args.cmd == "query":
        for chunk in retrieve_chunks(connection, search_query).items:
            title = chunk.metadata.get("title", "")
            origin = chunk.metadata.get("origin", "")
            package = chunk.metadata.get("package", "")
            print(f"\n{'─' * 60}")
            print(f"[{origin}] {package} → {title}")
            print(chunk.text[:SEARCH_BODY_CLI])
    else:
        for member in retrieve_module_members(connection, search_query).items:
            md = member.metadata
            print(f"\n{'─' * 60}")
            print(f"{md.get('kind', '')} {md.get('module', '')}.{md.get('name', '')}{md.get('signature', '')}")
            print((md.get("docstring") or "—")[:SEARCH_DOC_CLI])
```

- [ ] **Step 18.3: Also import `ChunkFilterField`** at the top.

- [ ] **Step 18.4: Commit**

```bash
git add python/pydocs_mcp/__main__.py
git commit -m "refactor(cli): follow renames; format output from ChunkList/ModuleMemberList"
```

---

## Task 19 — Update existing tests (mechanical-only)

**Files:** every file under `tests/` except `test_models.py`.

Goal: every test that existed on `main` still exists here; behavior assertions are unchanged; updates are purely mechanical.

- [ ] **Step 19.1: Inventory the symbols to rewrite across `tests/`**:
- `Symbol` → `ParsedMember`
- `chunk_text` → `split_into_chunks`
- `open_db` → `open_index_database`
- `db_path_for` → `cache_path_for_project`
- `clear_all` → `clear_all_packages`
- `clear_pkg` → `remove_package`
- `get_cached_hash` → `get_stored_content_hash`
- `rebuild_fts` → `rebuild_fulltext_index`
- `normalize` → `normalize_package_name`
- `resolve` → `discover_declared_dependencies`
- `index_project` → `index_project_source`
- `index_deps` → `index_dependencies`
- `search_chunks` → `retrieve_chunks`
- `search_symbols` → `retrieve_module_members`
- `concat_context` → `format_within_budget`
- column names in raw SQL fixtures: `pkg` → `package`, `heading` → `title`, `body` → `text`, `requires` → `dependencies`, `hash` → `content_hash`, `symbols` → `module_members`, `returns` → `return_annotation`, `params` → `parameters`, `doc` → `docstring`
- `list[dict]` return assertions → `ChunkList` / `ModuleMemberList` `items` assertions

- [ ] **Step 19.2: Rewrite call sites** — for tests that constructed a raw dict `query` or positional args, migrate to `SearchQuery(terms=..., pre_filter={...}, max_results=...)`. The pre-refactor dict-returns in `search_chunks` become `.items` tuples — adapt the assertion accordingly.

- [ ] **Step 19.3: No behavior assertions change.** If you find a test that would need its assertion rewritten for the refactor to pass, stop — that indicates the refactor changed behavior, which contradicts spec §10 AC 11.

- [ ] **Step 19.4: Run the full suite**

```bash
pytest -q
```

Expected: green across every file. Same count (≈) as the baseline from Task 0.3, plus the new tests in `test_models.py`.

- [ ] **Step 19.5: Commit**

```bash
git add tests/
git commit -m "test: mechanical updates for renamed models/columns/functions (no behavior change)"
```

---

## Task 20 — Integration smoke + zero-residue grep sweep

**Files:** none directly; this task is verification.

- [ ] **Step 20.1: Clear any prior cache** (you want to prove the upgrade path works):

```bash
rm -rf ~/.pydocs-mcp
```

- [ ] **Step 20.2: Run a full index on this repo**

```bash
time pydocs-mcp index .
```

Expected: a single full re-index. Capture the elapsed time.

- [ ] **Step 20.3: Re-run — expect cache hit <100ms**

```bash
time pydocs-mcp index .
```

Expected: near-instant. The `Done: ... cached` line should show every package cached.

- [ ] **Step 20.4: Smoke-test the search tools**

```bash
pydocs-mcp query "batch inference"
pydocs-mcp api predict -p vllm
```

Expected: output textually identical to what `main` produced on the same fixture.

- [ ] **Step 20.5: Zero-residue grep sweep (spec AC 2 and 6)**

```bash
# legacy must not appear anywhere in the changed code
grep -RIn "legacy" python/pydocs_mcp src && echo "FAIL: legacy appears" || echo "OK: no legacy"

# old class / function names must be gone (outside this plan and the spec)
grep -RIn "\bSymbol\b\|\bApiDefinition\b\|chunk_text\|search_chunks\|search_symbols\|concat_context\|open_db\b\|db_path_for\|clear_pkg\|rebuild_fts\|get_cached_hash\|index_project\b\|index_deps\b" python/pydocs_mcp src

# old column names in SQL / code
grep -RIn "\bpkg\b\|heading\b\|\bbody\b\|\brequires\b" python/pydocs_mcp src | grep -v "tomllib\|requires-python"

# old table name
grep -RIn "\bsymbols\b" python/pydocs_mcp src
```

Expected: for every pattern that should be gone, no results. `grep -v` exclusions cover legitimate hits (`requires-python` in `pyproject.toml`, `tomllib` mentions).

- [ ] **Step 20.6: Rust lints**

```bash
cargo fmt --check && cargo clippy -- -D warnings
```

Expected: both exit 0.

- [ ] **Step 20.7: Full test run**

```bash
pytest -q
```

Expected: green.

- [ ] **Step 20.8: If anything fails, fix inline and commit**; otherwise skip the commit.

```bash
git add -A
git commit -m "fix: residual occurrences from zero-residue sweep"
```

---

## Task 21 — CI matrix + `CLAUDE.md` architecture refresh

**Files:**
- Modify: `.github/workflows/*.yml` (whichever defines the test matrix)
- Modify: `CLAUDE.md`

- [ ] **Step 21.1: Inspect the workflow(s)**

```bash
ls .github/workflows/
```

- [ ] **Step 21.2: In the CI workflow(s) that set a Python matrix, drop `3.10` and ensure `3.11` and `3.12` are listed.** Verify the `setup-python` step matches.

- [ ] **Step 21.3: Update `CLAUDE.md` "Key Technical Details" section**:
- "Python 3.10+ required" → "Python 3.11+ required"
- "DB has three main tables: `packages`, `chunks` (with FTS5 virtual table), `symbols`" → "DB has three main tables: `packages`, `chunks` (with FTS5 virtual table), `module_members`"

- [ ] **Step 21.4: Commit**

```bash
git add .github/workflows CLAUDE.md
git commit -m "chore(ci): drop Python 3.10; refresh CLAUDE.md architecture section"
```

---

## Task 22 — Mark PR ready for review

**Files:** none (GitHub state).

- [ ] **Step 22.1: Push all commits**

```bash
git push
```

- [ ] **Step 22.2: Convert the draft PR to ready-for-review**

```bash
gh pr ready 13
```

- [ ] **Step 22.3: Post a completion comment on PR #13** summarizing which spec acceptance criteria are met, how many tests pass, integration smoke result, and the zero-residue grep confirmation. Paste the test-plan checklist from the PR body with each item marked done.

```bash
gh pr comment 13 --body "$(cat <<'EOF'
Implementation complete per plan docs/superpowers/plans/2026-04-19-sub-pr-1-naming-and-models.md.

Test plan status:
- [x] tests/test_models.py added (NN new tests)
- [x] Existing test suite passes (NN tests, 0 failures)
- [x] cargo fmt --check clean
- [x] cargo clippy clean, no new warnings
- [x] Zero-residue grep sweep clean
- [x] No `legacy` in changed files
- [x] pydocs-mcp index . triggers one full re-index then <100ms
- [x] search_docs / search_api / get_package_doc return textually identical strings on same fixture

Spec AC coverage: 1 (models.py) ✓, 2 (no legacy) ✓, 3 (ParsedMember) ✓, 4 (fallback matches) ✓, 5 (no tuples across modules) ✓, 6 (schema) ✓, 7 (user_version) ✓, 8 (MCP surface unchanged) ✓, 9 (cache behavior) ✓, 10 (tests preserved) ✓, 11 (behavior parity) ✓, 12 (Python 3.11) ✓.

Ready for review.
EOF
)"
```

- [ ] **Step 22.4: Update the PR body** (optional) — replace the "Draft — scaffold commit only" line with a link to the completion comment.

---

## Self-review checklist

Run these checks before handing off. Fix inline, no re-review.

**1. Spec coverage:**
- AC 1 (models.py) → Tasks 2-7
- AC 2 (no "legacy") → Task 20.5 grep
- AC 3 (Rust ParsedMember, fmt/clippy) → Tasks 8, 20.6
- AC 4 (fallback matches Rust) → Task 9
- AC 5 (no tuples between modules) → Tasks 15, 16, 17, 18
- AC 6 (exact schema) → Task 11
- AC 7 (SCHEMA_VERSION, drop-and-rebuild) → Task 11
- AC 8 (MCP surface byte-identical) → Task 17 (structural); Task 20.4 (empirical)
- AC 9 (cache behavior) → Task 20.2 and 20.3
- AC 10 (tests preserved + new test_models.py content) → Tasks 2-7, 11, 12, 19
- AC 11 (behavior parity) → Task 20.4
- AC 12 (requires-python + CI) → Tasks 1, 21

**2. Placeholder scan:** no TBD / TODO / "implement later" / "add validation" appear in tasks. Every code change carries concrete code.

**3. Type consistency check:**
- `ModuleMember` is always constructed with `metadata=` (Tasks 4, 12, 15, 16).
- `Chunk` is always constructed with `text=` and `metadata=` (Tasks 4, 12, 15, 16, 18).
- `SearchQuery` is always constructed with `terms=` and optional `pre_filter=` / `max_results=` (Tasks 6, 17, 18).
- `ParsedMember` has exactly 4 fields: `name`, `kind`, `signature`, `docstring` (Tasks 8, 9, 15).
- Function names used in Task 18 (`cache_path_for_project`, `open_index_database`, `clear_all_packages`, `rebuild_fulltext_index`, `discover_declared_dependencies`, `index_project_source`, `index_dependencies`, `retrieve_chunks`, `retrieve_module_members`) match those introduced in Tasks 13, 14, 15, 16 — verified.

**4. Deferred item** (`_validate_filter_syntax` on `SearchQuery`): explicitly noted in Task 6 step 6.3 with a pointer to sub-PR #3.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-sub-pr-1-naming-and-models.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `executing-plans`, batch with checkpoints.
