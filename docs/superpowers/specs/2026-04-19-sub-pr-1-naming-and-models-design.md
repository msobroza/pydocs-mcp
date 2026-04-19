# Sub-PR #1 — Naming sweep + domain models foundation

**Status:** Approved (2026-04-19) — ready for implementation planning in a later session
**⚠️ §5 is the canonical data-model source for the entire sub-PR series** (#1 through #4+). It was rewritten during sub-PR #4's brainstorming to reflect the final shape. Sub-PRs #2, #3, #4 reference §5 here; any inline drift in those specs is superseded by this section.

**Known drift INSIDE this spec (body sections outside §5) — do NOT implement these literal forms:**
- §6.1 rename table entry `Symbol → ModuleMember` (lines around "Class renames"): canonical is `Symbol → ParsedMember` (Rust side) + new Python generic `ModuleMember` constructed at the indexer boundary.
- §6.1 `SearchResult (typed dataclass)` — canonical is `SearchResponse`.
- §6.1 `Hit (implicit) → SearchMatch` — `SearchMatch` is retired; retrieval-time fields live on `Chunk` / `ModuleMember` directly (`relevance`, `retriever_name`).
- §6.2 field rename rows referring to `ModuleMember.kind`, `ModuleMember.docstring`, `ModuleMember.return_annotation`, `ModuleMember.parameters` as typed fields — canonical `ModuleMember` is fully generic; all these are metadata keys.
- §6.2 rows referencing `Query.package_filter`, `Query.internal`, `Query.topic`, `SearchMatch.result/score/source` — canonical `SearchQuery` has `pre_filter` / `post_filter` / format enums; retrieval fields live on `Chunk` / `ModuleMember` directly.
- §6.3 retriever return types `list[SearchMatch]` — canonical returns `ChunkList` / `ModuleMemberList`.
- §8 Files-touched row for `search.py` historically said `list[SearchMatch]` (now updated).
- §9 Risks row saying `ImportError: cannot import name 'ModuleMember'` — the importable Rust class is `ParsedMember`; Python `ModuleMember` exists in `models.py`.
- §12 Pitfalls "Rust side" block describing `ModuleMember` with 8 fields and `#[new]` constructor — obsolete; Rust is `ParsedMember` with 4 fields, no `#[new]`.
- §12 "`SearchQuery.internal` is tri-state" — `internal` exists only at the MCP tool parameter; inside `SearchQuery`, filter data lives in `pre_filter` dict keyed by `ChunkFilterField.SCOPE.value` with values from `SearchScope`.
- §13 Usage-examples section constructing `Chunk(package=..., title=..., text=..., origin=...)` — canonical is `Chunk(text=..., metadata={ChunkFilterField.PACKAGE.value: ..., ChunkFilterField.TITLE.value: ..., ChunkFilterField.ORIGIN.value: ...})`.
- §13 constructing `ModuleMember(package=..., module=..., name=..., kind=..., signature=..., return_annotation=..., parameters=..., docstring=...)` via Rust `#[new]` — canonical `ModuleMember` is Python-only, metadata-only; Rust produces `ParsedMember`.

**The body text of §2, §4, §6, §8, §9, §12, §13 has NOT been fully rewritten to the canonical shape.** Use §5 for types; use the body for high-level decisions and the naming-sweep rationale. Specific ACs (#1, #3, #4, #10) have been updated to reference the canonical shape; other ACs may still echo drift — treat §5 as authoritative when in doubt.
**Date:** 2026-04-19
**PR scope:** first sub-PR in a staged refactor toward an async retrieval pipeline with pluggable strategies (FTS5 / hybrid embeddings / LLM reranker). This sub-PR establishes the vocabulary, data shapes, and database schema; later sub-PRs add protocols, repositories, and pipeline stages.

---

## 1. Goal

Replace tuples and raw dicts flowing between modules with named dataclass models; apply a consistent naming convention across Python, Rust, and the SQLite schema — with **no user-observable behavior change** other than a one-time cache rebuild on first run after upgrade.

---

## 2. Decisions locked in during brainstorming

| Topic | Decision |
|---|---|
| Blast radius | **Option B + DB column/table renames.** Python-surface rename, Rust rename, DB schema renamed to match models. MCP tool signatures unchanged. |
| Parser-output type | **Rust `ParsedMember` (renamed during sub-PR #4 brainstorming)** with 4 typed fields: `name`, `kind`, `signature`, `docstring`. No `#[new]` constructor. Python's `ModuleMember` (generic, metadata-only per §5) is constructed by the indexer from `ParsedMember` + package/module context. |
| `parameters` field shape | **Typed `Parameter` dataclass.** No raw dicts in domain models. |
| Test scope | **Update broken tests + new `tests/test_models.py`** covering construction, defaults, enum round-trips, model↔row round-trips, `parameters` JSON round-trip. |
| Enum style | **`enum.StrEnum`** (Python 3.11+). |
| `Chunk` vs `DocumentPassage` | **`Chunk`** (matches the existing table name). |
| Parsed-entity name | **`ModuleMember`** (matches Python's `inspect.getmembers` vocabulary). |
| "Legacy" terminology | **Forbidden.** Tri-state bool → `SearchScope` conversion is inlined inside each MCP handler; no helper method uses the word "legacy". |
| Cache migration | **`PRAGMA user_version` bump + auto-rebuild.** On open, if `user_version` doesn't match the current schema version, drop all tables and recreate. First run after upgrade re-indexes from scratch. |
| Dataclass flags | `frozen=True, slots=True` **on every domain dataclass.** |
| Pydantic at MCP boundary | **Deferred.** Out of scope for this sub-PR. |

---

## 3. Python version

Minimum Python version moves from **3.10 → 3.11** as a side effect of `enum.StrEnum`.

- `pyproject.toml` `[project] requires-python` → `>=3.11`.
- `CLAUDE.md` note "Python 3.10+ required" → "Python 3.11+ required".
- If `pyo3/abi3-py310` is set in `Cargo.toml` in the future, update to `abi3-py311`.
- CI matrix: drop 3.10; ensure 3.11 and 3.12 are tested.

---

## 4. Scope

### In scope

- New module `python/pydocs_mcp/models.py` with dataclass domain models and `StrEnum`s.
- Rust: rename `#[pyclass] struct Symbol` → `ModuleMember` with 8 fields; field `kind` stays named `kind` but is constrained by `MemberKind` enum values; add `#[new]` constructor and getters for the 4 new fields (`package`, `module`, `return_annotation`, `parameters`). Also rename `#[pyfunction] chunk_text` → `split_into_chunks` to match the Python naming convention (no alias).
- Python fallback `_fallback.py` mirrors the Rust class: `ModuleMember` with the same 8 fields and the same constructor signature.
- Rename Python classes, module-level functions, function parameters, and local variables in files touched by this PR.
- New enums using `enum.StrEnum` with human-readable values (cache rebuilds, so there's no reason to preserve old abbreviations).
- **DB schema renamed** to match model field names. Includes table rename `symbols` → `module_members`.
- `PRAGMA user_version` introduced; mismatched version triggers `DROP TABLE` + recreate from the new DDL.
- DB-row ↔ model mapping helpers added to `db.py`.
- Indexer and search modules flow dataclasses internally; tuples disappear from Python-to-Python seams.
- Tests: **no existing test is deleted.** Existing tests are renamed/refactored only — updates are purely mechanical (imports, renamed symbols/columns, `SearchResult`, `chunk_text` → `split_into_chunks`). Behavior assertions are preserved unchanged because this PR is a refactor and all functionality is identical. New `tests/test_models.py` added for model/mapping coverage.

### Out of scope (deferred to later sub-PRs)

- MCP tool names, parameter names, return formats — signatures identical to `main`.
- Protocols / ABCs (no `Retriever`, `Repository`, `PipelineStage` yet).
- Async conversion of sync functions.
- Retrieval pipeline abstraction (`SearchPipeline`, `PipelineStage`).
- Indexer strategy split (inspect vs. static stay as the current `_collect_*` free functions; they just produce dataclass instances instead of tuples).
- Pydantic at the MCP boundary.
- Any user-observable behavior change beyond the one-time cache rebuild.

---

## 5. Domain models

> **Canonical data model** — this section reflects the final data-model shape agreed during sub-PR #4's brainstorming. It supersedes the earlier drafts in sub-PR #1's brainstorming. The same model is used verbatim in sub-PR #2, #3, #4; adding a "superseded" migration section to each of those was considered but rejected in favor of this single canonical definition. Any implementer of sub-PR #1 should produce the shape below on day one.

### 5.1 Enums

```python
from enum import StrEnum

class ChunkOrigin(StrEnum):
    PROJECT_MODULE_DOC            = "project_module_doc"
    PROJECT_CODE_SECTION          = "project_code_section"
    DEPENDENCY_CODE_SECTION       = "dependency_code_section"
    DEPENDENCY_DOC_FILE           = "dependency_doc_file"
    DEPENDENCY_README             = "dependency_readme"
    DEPENDENCY_MODULE_DOC         = "dependency_module_doc"
    COMPOSITE_OUTPUT              = "composite_output"   # set by formatter stages

class MemberKind(StrEnum):
    FUNCTION = "function"
    CLASS    = "class"
    METHOD   = "method"

class PackageOrigin(StrEnum):
    PROJECT    = "project"
    DEPENDENCY = "dependency"

class SearchScope(StrEnum):
    PROJECT_ONLY       = "project_only"
    DEPENDENCIES_ONLY  = "dependencies_only"
    ALL                = "all"

class MetadataFilterFormat(StrEnum):
    MULTIFIELD    = "multifield"
    FILTER_TREE   = "filter_tree"
    CHROMADB      = "chromadb"
    ELASTICSEARCH = "elasticsearch"
    QDRANT        = "qdrant"

class ChunkFilterField(StrEnum):
    """Canonical metadata keys for Chunk queries (naming the `metadata` entries,
    not dataclass fields). Used for AppConfig validation and SqliteFilterAdapter
    safe-columns."""
    PACKAGE = "package"
    TITLE   = "title"
    ORIGIN  = "origin"
    MODULE  = "module"
    SCOPE   = "scope"     # derived — interpreted as package-based predicate

class ModuleMemberFilterField(StrEnum):
    PACKAGE = "package"
    MODULE  = "module"
    NAME    = "name"
    KIND    = "kind"
```

`SearchScope` has no conversion helper. MCP handlers convert `internal: bool | None` to a `SearchScope` inline — no helper method carries the word "legacy".

### 5.2 Value objects and retrieval types (all `frozen=True, slots=True`)

```python
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import field_validator, model_validator
from pydantic.dataclasses import dataclass as pyd_dataclass


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


@dataclass(frozen=True, slots=True)
class Chunk:
    """Unit of retrieval. `text` is the primary payload; everything else
    (package, title, origin, module) lives in metadata keyed by
    ChunkFilterField.*.value. Composite chunks (formatter output) set
    metadata['origin'] == ChunkOrigin.COMPOSITE_OUTPUT.value.

    Retrieval-time fields (relevance, retriever_name) are optional; None for
    parser-time / indexing usage."""
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
    a typed ParsedMember (see §5.3); the indexer converts into this form."""
    kind: ClassVar[str] = "module_member"
    id: int | None = None
    relevance: float | None = None
    retriever_name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


# List wrappers — what retrieval pipelines produce
@dataclass(frozen=True, slots=True)
class ChunkList:
    kind: ClassVar[str] = "chunk_list"
    items: tuple[Chunk, ...]


@dataclass(frozen=True, slots=True)
class ModuleMemberList:
    kind: ClassVar[str] = "module_member_list"
    items: tuple[ModuleMember, ...]


PipelineResultItem = ChunkList | ModuleMemberList
# Future additions (Summary, Classification, Embedding) added here as siblings.


# Query — pydantic dataclass with construction-time validation
@pyd_dataclass(frozen=True, slots=True)
class SearchQuery:
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

    @model_validator(mode="after")
    def _validate_filter_syntax(self):
        from pydocs_mcp.storage.filters import format_registry
        for f, fmt in ((self.pre_filter, self.pre_filter_format),
                       (self.post_filter, self.post_filter_format)):
            if f is not None:
                format_registry[fmt].validate(f)
        return self


# Response — services return this
@dataclass(frozen=True, slots=True)
class SearchResponse:
    result: PipelineResultItem
    query: SearchQuery
    duration_ms: float = 0.0
```

Key points vs. earlier drafts:
- No `SearchMatch` class — retrieval-time fields live on `Chunk` / `ModuleMember` directly.
- No `FormattedPassage` class — composite chunks are ordinary `Chunk`s with `metadata["origin"] == "composite_output"`.
- `Chunk.text` is the only typed content field; everything else is in `metadata`. Aligns with vector-DB conventions (Qdrant/Chroma `document + metadata` shape).
- `ModuleMember` is fully generic — no typed structural fields — for uniformity. Parser output is converted at the indexer boundary.
- Every type appearing in `SearchResponse.result` carries `kind: ClassVar[str]` for runtime dispatch + config-driven serialization.

### 5.3 `ParsedMember` (Rust-owned; renamed from `ModuleMember` to avoid collision)

Because the Python `ModuleMember` is now generic, the Rust parser-output class is renamed to `ParsedMember`:

```rust
#[pyclass]
pub struct ParsedMember {
    #[pyo3(get)] pub name: String,
    #[pyo3(get)] pub kind: String,         // "function" | "class"
    #[pyo3(get)] pub signature: String,
    #[pyo3(get)] pub docstring: String,
}
```

`_fallback.py` defines a matching `ParsedMember` dataclass (4 typed fields). The indexer converts to generic `ModuleMember`:

```python
# indexer.py (after parsing)
for pm in parsed_list:
    members.append(ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: pkg_name,
            ModuleMemberFilterField.MODULE.value:  module_name,
            ModuleMemberFilterField.NAME.value:    pm.name,
            ModuleMemberFilterField.KIND.value:    pm.kind,
            "signature":           pm.signature,
            "docstring":           pm.docstring,
            "return_annotation":   "",
            "parameters":          (),
        },
    ))
```

Rust change from sub-PR #1's earlier draft: the class is renamed to `ParsedMember`, retains only 4 typed fields (the parser-time output shape). No `#[new]` constructor on Rust side required — the indexer constructs Python `ModuleMember` directly with a metadata dict. Rust structure is simpler than originally planned.

### 5.4 New SQLite schema

Tables are recreated when `PRAGMA user_version` doesn't match. Column names match model fields verbatim so row↔model mapping is one-to-one.

```sql
PRAGMA user_version = 2;

CREATE TABLE packages (
    name TEXT PRIMARY KEY,
    version TEXT,
    summary TEXT,
    homepage TEXT,
    dependencies TEXT,        -- JSON array of dependency names
    content_hash TEXT,
    origin TEXT               -- PackageOrigin value
);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    package TEXT,
    title TEXT,
    text TEXT,
    origin TEXT               -- ChunkOrigin value
);

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    title, text, package,
    content=chunks, content_rowid=id,
    tokenize='porter unicode61'
);

CREATE TABLE module_members (
    id INTEGER PRIMARY KEY,
    package TEXT,
    module TEXT,
    name TEXT,
    kind TEXT,                -- MemberKind value
    signature TEXT,
    return_annotation TEXT,
    parameters TEXT,          -- JSON array of Parameter objects
    docstring TEXT
);

CREATE INDEX ix_chunks_package          ON chunks(package);
CREATE INDEX ix_module_members_package  ON module_members(package);
CREATE INDEX ix_module_members_name     ON module_members(name);
```

### 5.5 Schema versioning

- Current schema version is defined as a module-level constant in `db.py`: `SCHEMA_VERSION = 2`.
- `open_index_database(path)` checks `PRAGMA user_version`; on mismatch it drops all known tables (including the obsolete `symbols` table from version 1) and recreates the schema above, then writes `PRAGMA user_version = 2`.
- This is intentionally destructive; the cache is always rebuildable from source.

---

## 6. Naming convention tables

### 6.1 Class renames

| Current | New |
|---|---|
| `Chunk` (DB-tuple shape) | `Chunk` (typed dataclass, same name) |
| `Symbol` (Rust + `_fallback.py`) | `ModuleMember` |
| `Query` (implicit, tuple-passing) | `SearchQuery` |
| `Hit` (implicit, dict-passing) | `SearchMatch` |
| `SearchResult` (implicit, tuple/dict-passing) | `SearchResult` (typed dataclass, same name) |

### 6.2 Field renames

| Old | New | Why |
|---|---|---|
| `Chunk.heading` | `Chunk.title` | markdown headings are titles |
| `Chunk.body` | `Chunk.text` | clearer |
| `Chunk.kind` | `Chunk.origin` (`ChunkOrigin`) | `origin` = where the chunk came from |
| `Chunk.pkg` (DB column) | `Chunk.package` / column `package` | full word |
| `Symbol.kind` (type `str`) | `ModuleMember.kind` (type `MemberKind`) | same name, now typed by enum |
| `Symbol.doc` | `ModuleMember.docstring` | Python term |
| `Symbol.returns` / DB col `returns` | `ModuleMember.return_annotation` / col `return_annotation` | technical name |
| `Symbol.params` / DB col `params` | `ModuleMember.parameters` / col `parameters` | full word |
| `symbols` table | `module_members` table | matches class name |
| `Package.requires` / col `requires` | `Package.dependencies` / col `dependencies` | standard Python vocab |
| `Package.hash` / col `hash` | `Package.content_hash` / col `content_hash` | describes what is hashed |
| `Package.kind` (implicit) | `Package.origin` (`PackageOrigin`) | consistent with `ChunkOrigin` |
| `Query.text` | `SearchQuery.terms` | searchable input |
| `Query.package` | `SearchQuery.package_filter` | it's a filter |
| `Query.internal` (`bool \| None`) | `SearchQuery.scope` (`SearchScope`) | replaces tri-state bool |
| `Query.topic` | `SearchQuery.title_filter` | filters by title |
| `Query.limit` | `SearchQuery.max_results` | explicit intent |
| `Hit.document` | `SearchMatch.result` | what matched |
| `Hit.score` | `SearchMatch.relevance` | retrieval term |
| `Hit.source` | `SearchMatch.retriever_name` | which retriever produced it |

### 6.3 Function / method renames

| Old | New |
|---|---|
| `search_chunks(conn, ...)` | `retrieve_chunks(connection, query)` — free function, returns `list[SearchMatch]` |
| `search_symbols(conn, ...)` | `retrieve_module_members(connection, query)` — returns `list[SearchMatch]` |
| `concat_context` | `format_within_budget` |
| `index_project` | `index_project_source` |
| `index_deps` | `index_dependencies` |
| `chunk_text` | `split_into_chunks` — renamed in Rust (`#[pyfunction]` in `src/lib.rs`), Python fallback (`_fallback.py`), and all call sites. No alias. |
| `rebuild_fts` | `rebuild_fulltext_index` |
| `open_db` | `open_index_database` |
| `db_path_for` | `cache_path_for_project` |
| `clear_all` | `clear_all_packages` |
| `clear_pkg` | `remove_package` |
| `get_cached_hash` | `get_stored_content_hash` |
| `deps.normalize` | `normalize_package_name` |
| `deps.resolve` | `discover_declared_dependencies` |
| `_parse_source_files` | `_extract_from_source_files` |
| `_base_data` | `_build_package_record` |
| `_add_doc_files` | `_append_doc_file_chunks` |
| `_write_dep` | `_persist_dependency` |
| `_collect_inspect` / `_collect_static` | `_extract_by_import` / `_extract_from_static_sources` |
| `_dep_py_files` | `list_dependency_source_files` |
| `_site_packages_root` | `find_site_packages_root` |
| `_inspect_syms` | `_extract_members_by_import` |
| `_get_sig` | `_extract_callable_signature` |
| `_find_dep_files` | `list_dependency_manifest_files` |
| `_parse_toml` / `_parse_requirements` | `parse_pyproject_dependencies` / `parse_requirements_file` |

### 6.4 Local variable conventions

| Old | New |
|---|---|
| `conn` | `connection` |
| `pkg`, `n` | `package_name` |
| `sym_rows`, `chunk_rows` | `module_members`, `chunks` |
| `fts_q` | `fulltext_query` |
| `dists` | `installed_distributions` |
| `work` | `packages_to_index` |
| `rel` | `relative_path` |
| `sp`, `p` (argparse) | `subparser`, `parser` |
| `mod`, `mo` | `module`, `member` |
| `ms` | `method_summaries` |
| `rows` | specific: `module_members` or `chunks` |
| `data` | `package_record` (typed) |

---

## 7. Key implementation choices

- **Frozen + slotted dataclasses on every domain class.** Immutability catches aliasing bugs; slots reduce memory in large result sets and turn attribute typos into `AttributeError` at write time. Note: `Chunk` and `ModuleMember` carry a `metadata: Mapping[str, Any]` field which makes instances non-hashable when metadata is non-empty — this is accepted because chunks are compared by id / content, not hashed.
- **Collections as tuples, not lists.** `tuple[str, ...]`, `tuple[Chunk, ...]`, `tuple[ModuleMember, ...]`. Consistent with `frozen=True`.
- **Mapping helpers live in `db.py`** as small pure functions: `_chunk_to_row`, `_row_to_chunk`, `_module_member_to_row`, `_row_to_module_member`, `_package_to_row`, `_row_to_package`. `_row_to_chunk` populates `metadata` dict from non-`text` / non-`id` columns; `_chunk_to_row` reverses the mapping when inserting.
- **MCP boundary conversion is inlined in each handler.** `internal: bool | None` stays at the MCP surface. Handlers translate to a `pre_filter` dict keyed by `ChunkFilterField` values. No "legacy" named helper.
- **Rust parser output is `ParsedMember`.** The Rust side produces typed 4-field instances (name, kind, signature, docstring). Indexer converts to generic Python `ModuleMember` by stuffing fields into `metadata`.
- **No Protocol / ABC introduced in sub-PR #1.** Retrievers and repositories remain free functions consuming models. Abstraction choice is deferred to sub-PR #2.
- **Schema migration is a drop-and-rebuild** keyed off `PRAGMA user_version`. Indexing logic stays unaware of the migration.

---

## 8. Files touched

| File | Change | Purpose |
|---|---|---|
| `python/pydocs_mcp/models.py` | **new** | Dataclasses, `StrEnum`s, `Parameter` |
| `python/pydocs_mcp/_fallback.py` | edit | Rename `Symbol` → `ModuleMember`; grow to 8 fields |
| `python/pydocs_mcp/_fast.py` | edit | Update imports for renamed Rust class (`ModuleMember`) and function (`split_into_chunks`) |
| `python/pydocs_mcp/db.py` | edit | New DDL, `SCHEMA_VERSION`, `user_version` check, row↔model mapping helpers, renamed functions |
| `python/pydocs_mcp/indexer.py` | edit | Flow models instead of tuples; rename functions + locals |
| `python/pydocs_mcp/search.py` | edit | Return `ChunkList` / `ModuleMemberList`; rename functions + locals; update SQL to new column names; `_row_to_chunk` puts non-text columns into `metadata` |
| `python/pydocs_mcp/server.py` | edit | Handlers use models internally; tri-state `internal` → `SearchScope` inlined per handler; MCP surface byte-identical |
| `python/pydocs_mcp/deps.py` | edit | Rename `normalize` / `resolve` and helpers |
| `python/pydocs_mcp/__main__.py` | edit | Follow renames; CLI output formatted from new result types |
| `src/lib.rs` | edit | Rename struct `Symbol` → `ParsedMember` with 4 typed fields (`name`, `kind`, `signature`, `docstring`); only getters exposed (no `#[new]` since Python constructs generic `ModuleMember` directly with a metadata dict); rename `#[pyfunction] chunk_text` → `split_into_chunks` |
| `pyproject.toml` | edit | `requires-python = ">=3.11"` |
| `CLAUDE.md` | edit | Python version note; refresh schema/table names in architecture section |
| `tests/test_models.py` | **new** | Construction, defaults, enum round-trips, row↔model round-trips, `parameters` JSON round-trip |
| Existing `tests/*` | edit | **No existing test is deleted.** Existing tests are renamed/refactored only — imports, class names, column names, `SearchResult` fields, and `chunk_text` → `split_into_chunks`. Behavior assertions stay intact. |

---

## 9. Risks and rollback

| Risk | Likelihood | Mitigation |
|---|---|---|
| Stale compiled `_native` extension after upgrade (users see `ImportError: cannot import name 'ModuleMember'`) | Medium | PR description instructs `maturin develop --release`; CI rebuilds the wheel; Python fallback works without a native build |
| First run after upgrade is slower — one-time full re-index | Expected | PR description and CHANGELOG note the one-time cost; re-index on this repo completes in seconds |
| Mapping edge cases (empty strings vs `None`, JSON escape in `parameters`) | Medium | Targeted tests in `test_models.py` |
| Name drift between Rust `ModuleMember` and Python fallback | Low | Test imports both and asserts identical field names and constructor signature |
| Missed rename leaves a dangling `Symbol` / `ApiDefinition` / `symbols` reference | Low | Grep sweep (`Symbol`, `ApiDefinition`, `symbols` table, `pkg`, `heading`, `body`) in the PR; failed imports also catch this |
| Rust `cargo clippy` warnings on new code | Low | CI runs clippy; PR blocked on new warnings |
| Users on Python 3.10 broken by version bump | Medium | Documented in the PR and CHANGELOG; no 3.10 shim — decision is to bump |
| Downgrade after merge finds an incompatible DB | Low | On downgrade, old code sees an unknown `user_version` and must rebuild. If the old code doesn't check `user_version`, it fails to read the new schema. Mitigation: note in PR description that downgrade requires deleting `~/.pydocs-mcp/*.db` |

**Rollback:** revert the merge commit. Users delete `~/.pydocs-mcp/*.db` (or it auto-rebuilds on next run if the reverted code also uses `user_version`). No user data loss since the cache is rebuildable.

---

## 10. Acceptance criteria

1. `python/pydocs_mcp/models.py` exists and exposes the full canonical model from §5.2: `Parameter`, `Package`, `Chunk`, `ModuleMember`, `ChunkList`, `ModuleMemberList`, `PipelineResultItem` TypeAlias, `SearchQuery`, `SearchResponse`, and all enums (`ChunkOrigin` including `COMPOSITE_OUTPUT`, `MemberKind`, `PackageOrigin`, `SearchScope`, `MetadataFilterFormat`, `ChunkFilterField`, `ModuleMemberFilterField`). Every dataclass is `frozen=True, slots=True`. Every enum subclasses `enum.StrEnum`. Every type that appears in `SearchResponse.result` carries `kind: ClassVar[str]`.
2. The word `legacy` does not appear in any Python or Rust source file added or modified by this PR.
3. Rust struct `Symbol` no longer exists. `ParsedMember` exists in `src/lib.rs` with 4 typed fields (`name`, `kind`, `signature`, `docstring`) and `#[pyo3(get)]` getters. No `#[new]` constructor on the Rust class (Python `ModuleMember` is constructed directly with a metadata dict in the indexer). `cargo fmt --check` and `cargo clippy` pass with no new warnings.
4. `_fallback.py` `ParsedMember` dataclass matches Rust's `ParsedMember`: identical field names (`name`, `kind`, `signature`, `docstring`).
5. No tuple is passed between Python modules as a domain payload — only model instances. Row-shaped tuples are confined to `db.py`.
6. SQLite schema matches §5.4 exactly: tables `packages`, `chunks`, `chunks_fts`, `module_members`; no `symbols` table; no columns named `heading`, `body`, `pkg`, `params`, `doc`, `returns`, `requires`, or `hash`.
7. `db.SCHEMA_VERSION = 2`; `open_index_database` drops and recreates all tables when `PRAGMA user_version` differs; after rebuild it writes the current version.
8. MCP tool signatures in `server.py` are byte-identical to `main`: `list_packages`, `get_package_doc`, `search_docs`, `search_api`, `inspect_module` — same tool names, parameter names, type annotations, docstrings, and return-string shapes.
9. Running `pydocs-mcp index .` on this repo after upgrade triggers a single full re-index, then subsequent runs are `<100ms` (cache hit). Package, chunk, and `module_members` counts after upgrade are the same as `symbols` counts before the upgrade on the same input.
10. **No existing test is deleted.** Every test that exists in `tests/` on `main` is still present in the PR branch; updates are mechanical only (imports, renamed symbols/columns, `SearchResponse`, `chunk_text` → `split_into_chunks`) and all behavior assertions are preserved. The full existing test suite passes against the refactored code because functionality is unchanged. New `tests/test_models.py` adds, at minimum:
    - One construction test per dataclass covering defaults (including `Chunk` / `ModuleMember` with empty vs populated `metadata`).
    - One test per enum covering `EnumClass(raw_value)` round-trip for every member, including `ChunkFilterField` / `ModuleMemberFilterField`.
    - Round-trip tests: `Chunk ↔ row` (metadata dict populated from columns), `ModuleMember ↔ row`, `Package ↔ row`.
    - One JSON round-trip test for `parameters` inside `ModuleMember.metadata`.
    - One `user_version` upgrade test: open a fixture DB with `PRAGMA user_version = 0`, confirm tables are dropped and recreated.
11. Behavior parity: manual smoke check shows `search_docs`, `search_api`, `get_package_doc` return strings textually identical to pre-PR output on the same fixture.
12. `pyproject.toml` advertises `requires-python = ">=3.11"`; CI tests 3.11 and 3.12 (3.10 removed).

---

## 11. Open items to confirm before implementation

All earlier open items are now resolved (Python 3.11, frozen+slots everywhere, no Pydantic, cache rebuild on version bump, `ModuleMember` naming, `SearchResult` final name, no aliases, existing tests preserved). One minor item remains:

1. **Downgrade-path advice** (delete `~/.pydocs-mcp/*.db` if downgrading past this PR) — OK to document in the PR description and CHANGELOG only, with no code work required?

---

## 12. Implementation notes — pitfalls for implementers without full brainstorming context

Points that are easy to get wrong if you only have the spec and not the brainstorming history.

### Rust side (`src/lib.rs`)
- `ModuleMember` is a `#[pyclass]` with **8 fields**. The Python-callable parser (`parse_py_file`) only fills 4 (`name`, `kind`, `signature`, `docstring`); the other 4 (`package`, `module`, `return_annotation`, `parameters`) stay at empty-string / empty-list defaults at parse time. The Python indexer builds full instances afterward via the `#[new]` constructor. Don't skip any field when exposing the constructor.
- `_fallback.py`'s `ModuleMember` dataclass MUST mirror Rust's `#[new]` signature exactly — same field names, same argument order. Field-name drift silently breaks the fallback path. A test that reflects on both class layouts is required.
- Don't expose getters piecemeal; add all 8 via `#[pymethods]` in one pass or use `#[pyclass(get_all)]` if it fits your PyO3 version. Mixed exposure strategies create confusion.

### StrEnum values preserve today's DB strings
- **Critical.** `DEPENDENCY_CODE_SECTION = "dep"` — the enum NAME is readable, the enum VALUE matches today's column text. Readers sometimes "fix" these to `"dependency_code_section"`, which silently breaks existing caches.
- See §5.1 for the full value table. Keep every value as shipped, no matter how abbreviation-ugly it looks.

### Schema migration is drop-and-recreate, not ALTER TABLE
- A `PRAGMA user_version` mismatch triggers `DROP TABLE` + recreate from DDL. No ALTER, no data-preserving migration. The cache is fully rebuildable from source, so drop-and-rebuild is correct.
- Any attempt to write an ALTER-based migration is out of scope and against the spec.

### `SearchQuery.internal` is tri-state
- `internal: bool | None` — `True` means project-only, `False` means dependencies-only, `None` means all. Simplifying to `bool = False` breaks the "all scopes" default path used by most MCP queries.

### Python 3.11+ required
- `enum.StrEnum` landed in stdlib 3.11. The spec bumps `requires-python` accordingly. Do not pull in `backports.strenum` or define a custom shim without explicit approval.

### "No existing test deleted"
- This is an invariant across every sub-PR in this refactor series. Even a test whose target function is removed gets kept — refactored to test the replacement. If a test truly has no meaningful replacement, flag it in code review before deleting.

### Zero-residue grep after implementation
- After finishing, grep the codebase for every symbol you renamed. Zero occurrences in Python or Rust outside the rename tables should remain. Easy to miss a string in a log message, a test assertion, or a docstring.

### Renaming, no aliasing
- If you rename X → Y, the codebase should contain only Y. No `X = Y` compat aliases. This was an explicit decision — aliases defeat the naming cleanup.

---

## 13. Usage examples and design patterns

### How consumers interact with this PR

```python
from pydocs_mcp.models import (
    Chunk, Package, Parameter,
    ChunkOrigin, MemberKind, PackageOrigin, SearchScope,
)
from pydocs_mcp._fast import ModuleMember   # from Rust or _fallback.py

# Value objects — frozen, immutable, equality by value
chunk = Chunk(
    package="fastapi",
    title="Routing",
    text="Use APIRouter to group related endpoints.",
    origin=ChunkOrigin.DEPENDENCY_DOC_FILE,
)

member = ModuleMember(                  # keyword-only constructor exposed by Rust #[new]
    package="fastapi",
    module="fastapi.routing",
    name="APIRouter",
    kind=MemberKind.CLASS,
    signature="(prefix: str = '', tags: list[str] | None = None)",
    return_annotation="",
    parameters=(Parameter(name="prefix", default='""'),),
    docstring="Groups related endpoints under a shared prefix.",
)

pkg = Package(
    name="fastapi",
    version="0.104.1",
    summary="FastAPI web framework.",
    homepage="https://fastapi.tiangolo.com",
    dependencies=("starlette>=0.27",),
    content_hash="abc123def456",
    origin=PackageOrigin.DEPENDENCY,
)
```

Consumers don't interact with sub-PR #1 directly — they construct domain types. This PR is foundational; its "surface" is the model definitions that later PRs pass around.

### Design patterns used

| Pattern | Where | Role |
|---|---|---|
| **Value Object (DDD)** | `Chunk`, `Package`, `Parameter`, `ModuleMember` | Frozen, immutable, no identity. Equality by value. `slots=True` for memory. |
| **Anemic DTO** | All domain dataclasses | Data carriers; no business logic on the classes themselves. Logic lives in services / stages / retrievers (later PRs). |
| **Discriminator via StrEnum** | `ChunkOrigin`, `MemberKind`, `PackageOrigin`, `SearchScope` | Readable Python names + string values compatible with DB text columns and JSON. |
| **Mirrored-type pattern** | Rust `#[pyclass] ModuleMember` ↔ Python `_fallback.py` `ModuleMember` | Contract-by-structural-equality. A test reflects on both sides to enforce identical field layout. |
| **Content-hash cache invalidation** | `Package.content_hash` + `PRAGMA user_version` | Deterministic fingerprint for incremental indexing; user_version forces rebuild on schema drift. |
| **Ubiquitous language (DDD)** | Naming sweep | Every concept has one name across code, schema, docs. No `pkg` / `heading` / `kind` grab-bag. |

### Architectural choices

- **Python-level domain model is the single source of truth.** The Rust side exists only for performance; its types mirror Python's, not the other way around.
- **Frozen + slots + tuples over lists** as the default. Hashable domain objects compose well with future pipelines, caching stages, and dict-based registries.
- **String enums** (not integer enums) so the same value round-trips through SQLite TEXT columns, YAML, and JSON without glue code.
- **No compatibility shims.** Breaking renames are committed once; callers update at the same time. Avoids the half-migrated "both names work" state.

---

## 14. Follow-up sub-PRs (preview, not in scope)

For context; each will get its own brainstorm + spec.

- **Sub-PR #2** — Async retriever protocols (`ChunkRetriever`, `ModuleMemberRetriever`); wrap today's SQL paths as concrete retrievers; convert `server.py` handlers to async consumers.
- **Sub-PR #3** — Storage repository layer; move raw SQL out of `server.py` into `SqlitePackageRepository`, `SqliteChunkRepository`, `SqliteModuleMemberRepository`.
- **Sub-PR #4** — `SearchPipeline` + `PipelineStage`; migrate retrievers to stages; `format_within_budget` becomes a `TokenBudgetStage`.
- **Sub-PR #5** — Use-case layer (`SearchDocsUseCase`, etc.); server becomes a thin MCP wrapper.
- **Sub-PR #6** — Indexer strategy split (extractor strategies, cache policy, source provider).
- **Sub-PR #7** — Query parsing component + Pydantic at MCP boundary.
