# Sub-PR #3 — Storage Repositories + Unit of Work + Filters Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract all SQL into a typed repository layer, introduce a filter tree + YAML-driven metadata schemas, and migrate every SQL-touching consumer (indexer, server, CLI, retrievers) through backend-agnostic Protocols — while preserving byte-identical MCP/CLI output and honoring the 11 inherited invariants from sub-PR #2's review.

**Architecture:** New `python/pydocs_mcp/storage/` subpackage (4 files) with 8 `@runtime_checkable` Protocols (`PackageStore`, `ChunkStore`, `ModuleMemberStore`, `TextSearchable`, `VectorSearchable`, `HybridSearchable`, `UnitOfWork`, `FilterAdapter`), concrete SQLite adapters, and a `Filter` tree with `MultiFieldFormat` parser. New `python/pydocs_mcp/application/indexing_service.py` coordinates writes across the 3 entity stores via Protocols only. `models.py` `SearchQuery` undergoes a breaking change (removes `package_filter`/`scope`/`title_filter`, adds `pre_filter`/`post_filter` dicts). `retrieval/` stages reshape (drop 3 filter stages, add `MetadataPostFilterStage`); retrievers consume stores via Protocols. `AppConfig` gains `metadata_schemas` loaded from a new shipped `presets/default_config.yaml` via `settings_customise_sources` layering.

**Tech Stack:** Python 3.11+, stdlib `contextvars`/`contextlib.asynccontextmanager`/`sqlite3`/`asyncio`/`importlib.resources`, `pydantic>=2`, `pydantic-settings>=2` (both already present from sub-PR #2), `pyyaml>=6`. No new runtime deps (spec AC #26).

**Spec source of truth:** [`docs/superpowers/specs/2026-04-19-sub-pr-3-storage-repositories-design.md`](../specs/2026-04-19-sub-pr-3-storage-repositories-design.md). §5 is authoritative on types; §5.8 describes the stage reshape; §5.9 is the canonical `AppConfig` shape.

**Work location:** Worktree `.claude/worktrees/sub-pr-3-storage-repositories/` on branch `feature/sub-pr-3-storage-repositories`, draft PR [#15](https://github.com/msobroza/pydocs-mcp/pull/15).

**Depends on:** sub-PR #2 merged as `61db2ef` on `main` — 395 tests, `retrieval/` subpackage, `AppConfig` YAML loading, all in place.

**Repo policy (critical, repeats from sub-PR #2):** No `Co-Authored-By:` trailers on any commit. All commits authored solely by `msobroza`. No git config changes, no `--author` overrides.

---

## File structure

### Files created

- `python/pydocs_mcp/storage/__init__.py` — public re-exports
- `python/pydocs_mcp/storage/protocols.py` — 8 `@runtime_checkable` Protocols + `FilterAdapter`
- `python/pydocs_mcp/storage/filters.py` — `Filter` tree, `MultiFieldFormat`, `MetadataSchema`, `format_registry`
- `python/pydocs_mcp/storage/sqlite.py` — `SqliteUnitOfWork`, `SqliteFilterAdapter`, 3 repositories, `SqliteVectorStore`, contextvar + `_maybe_acquire`, row mappers
- `python/pydocs_mcp/application/__init__.py` — empty
- `python/pydocs_mcp/application/indexing_service.py` — `IndexingService`
- `python/pydocs_mcp/presets/default_config.yaml` — shipped config baseline (spec §5.9)
- `tests/storage/__init__.py` — empty
- `tests/storage/test_filters.py`
- `tests/storage/test_protocols.py`
- `tests/storage/test_unit_of_work.py`
- `tests/storage/test_filter_adapter.py`
- `tests/storage/test_repositories.py`
- `tests/storage/test_vector_store.py`
- `tests/storage/test_end_to_end.py`
- `tests/application/__init__.py` — empty
- `tests/application/test_indexing_service.py` — Protocol fakes only

### Files modified (breaking and non-breaking)

- `python/pydocs_mcp/models.py` — **breaking** `SearchQuery`: remove `package_filter`/`scope`/`title_filter`; add `pre_filter`/`post_filter` with `pre_filter_format`/`post_filter_format`; wire `model_validator` to `storage.filters.format_registry`.
- `python/pydocs_mcp/db.py` — remove the row mappers (they move to `storage/sqlite.py`); keep only schema + `PRAGMA user_version` + `build_connection_provider`.
- `python/pydocs_mcp/retrieval/stages.py` — remove `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`; add `MetadataPostFilterStage`; apply RRF first-seen (AC #33) + composite title sentinel (AC #34) + recursion depth guard (AC #31).
- `python/pydocs_mcp/retrieval/retrievers.py` — `Bm25ChunkRetriever` takes `store: TextSearchable` + `allowed_fields: frozenset[str]`; `LikeMemberRetriever` takes `SqliteModuleMemberRepository` + filters via `.list()`. Both use `provider.acquire()` if they touch SQLite directly (AC #29).
- `python/pydocs_mcp/retrieval/config.py` — add `metadata_schemas`, wire `settings_customise_sources` to layer shipped default + user YAML + env; `PipelineRouteEntry` mutual-exclusion validator (AC #32).
- `python/pydocs_mcp/retrieval/__init__.py` — eagerly import `stages`/`retrievers`/`formatters`/`predicates` so registries populate on `import pydocs_mcp.retrieval` (AC #30).
- `python/pydocs_mcp/server.py` — MCP handlers build `pre_filter` dicts from legacy params (`internal`, `package`, `topic`); `list_packages`/`get_package_doc` use repositories; `inspect_module` uses `SqlitePackageRepository`.
- `python/pydocs_mcp/__main__.py` — `query`/`api` CLI subcommands build `pre_filter`; `index --force` uses `IndexingService.clear_all` + `reindex_package`.
- `python/pydocs_mcp/indexer.py` — writes go through `IndexingService.reindex_package`; no direct SQL remains.
- `python/pydocs_mcp/presets/chunk_fts.yaml` — drop `package_filter`/`scope_filter`/`title_filter` stages; add `metadata_post_filter` or push filters into the retriever level (spec §6.1 shows legacy-param → `pre_filter` translation).
- `python/pydocs_mcp/presets/member_like.yaml` — same treatment.
- `tests/retrieval/test_stages.py` — remove tests for the 3 deleted filter stages; add `MetadataPostFilterStage` tests; keep the `test_parallel_retrieval_stage_preserves_filtered_branches` regression test intact (AC #28 invariant).
- `tests/retrieval/test_retrievers.py` — update for store-based retriever signatures.
- `tests/retrieval/test_parity_golden.py` — keep passing after the migration (AC #27).
- `tests/_retriever_helpers.py` — update the shim for the new `SearchQuery` shape so the 67 pre-existing behavioral tests still work.
- `CLAUDE.md` — architecture section adds `storage/` + `application/` subpackages.

### Files deleted

None in this PR. `db.py` stays but shrinks (row mappers move out).

---

## Task 0 — Baseline verification

- [ ] **Step 0.1:** Worktree check.
```bash
cd /Users/msobroza/Projects/pyctx7-mcp/.claude/worktrees/sub-pr-3-storage-repositories
source .venv/bin/activate
git log -1 --oneline
```
Expected: `<sha> chore: scaffold branch for sub-PR #3 — ...`.

- [ ] **Step 0.2:** Run baseline tests.
```bash
pytest -q
```
Expected: `395 passed`.

- [ ] **Step 0.3:** Rust toolchain check.
```bash
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```
Expected: exit 0.

- [ ] **Step 0.4:** No commit this task.

---

## Task 1 — `storage/filters.py` — filter tree + MultiFieldFormat + schemas

**Files:**
- Create: `python/pydocs_mcp/storage/__init__.py` (empty)
- Create: `python/pydocs_mcp/storage/filters.py`
- Create: `tests/storage/__init__.py` (empty)
- Create: `tests/storage/test_filters.py`

- [ ] **Step 1.1:** `mkdir -p python/pydocs_mcp/storage tests/storage && touch python/pydocs_mcp/storage/__init__.py tests/storage/__init__.py`

- [ ] **Step 1.2:** Write failing tests — `tests/storage/test_filters.py`:

```python
"""Tests for Filter tree + MultiFieldFormat + MetadataSchema (spec §5.1, AC #4/#5/#6)."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    FieldSpec,
    MetadataFilterFormat,
    MetadataSchema,
    MultiFieldFormat,
    Not,
    format_registry,
)


def test_filter_dataclasses_frozen_slots():
    f = FieldEq(field="package", value="fastapi")
    assert f.field == "package"
    assert f.value == "fastapi"
    with pytest.raises(Exception):
        f.field = "x"  # frozen


def test_multifield_format_parse_bare_values_to_eq():
    fmt = MultiFieldFormat()
    tree = fmt.parse({"package": "fastapi", "origin": "dependency_doc_file"})
    assert isinstance(tree, All)
    fields = {c.field: c for c in tree.clauses}
    assert isinstance(fields["package"], FieldEq)
    assert fields["package"].value == "fastapi"


def test_multifield_format_parse_op_dict():
    fmt = MultiFieldFormat()
    tree = fmt.parse({"title": {"like": "routing"}, "package": {"eq": "fastapi"}})
    assert isinstance(tree, All)
    fields = {c.field: c for c in tree.clauses}
    assert isinstance(fields["title"], FieldLike)
    assert fields["title"].substring == "routing"


def test_multifield_format_parse_in_op():
    fmt = MultiFieldFormat()
    tree = fmt.parse({"scope": {"in": ["project_only", "all"]}})
    c = tree.clauses[0]
    assert isinstance(c, FieldIn)
    assert c.values == ("project_only", "all")


def test_multifield_format_rejects_boolean_ops():
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match=r"\$and|filter_tree"):
        fmt.validate({"$and": [{"package": "fastapi"}]})


def test_multifield_format_rejects_unknown_operator():
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="unknown operator"):
        fmt.validate({"package": {"regex": ".*"}})


def test_multifield_format_rejects_non_mapping():
    fmt = MultiFieldFormat()
    with pytest.raises(ValueError, match="mapping"):
        fmt.validate([{"package": "fastapi"}])


def test_metadata_schema_validate_rejects_unknown_field():
    schema = MetadataSchema(
        fields=(FieldSpec(name="package"), FieldSpec(name="origin")),
    )
    ok = All(clauses=(FieldEq(field="package", value="x"),))
    bad = All(clauses=(FieldEq(field="language", value="python"),))
    schema.validate(ok)  # no raise
    with pytest.raises(ValueError, match="language"):
        schema.validate(bad)


def test_format_registry_has_multifield():
    assert MetadataFilterFormat.MULTIFIELD in format_registry
    fmt = format_registry[MetadataFilterFormat.MULTIFIELD]
    assert fmt.format is MetadataFilterFormat.MULTIFIELD


def test_all_filter_composition():
    tree = All(clauses=(
        FieldEq(field="package", value="fastapi"),
        FieldLike(field="title", substring="route"),
    ))
    assert len(tree.clauses) == 2


def test_future_classes_exist():
    # Any_ and Not exist for future FilterTreeFormat, unused in MultiFieldFormat
    assert Any_(clauses=()).clauses == ()
    assert Not(clause=FieldEq(field="x", value="y")).clause is not None
```

- [ ] **Step 1.3:** Run — expect fail.

- [ ] **Step 1.4:** Create `python/pydocs_mcp/storage/filters.py`:

```python
"""Filter tree + MultiFieldFormat + MetadataSchema + format_registry (spec §5.1)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, Union, runtime_checkable


# ── Filter tree ─────────────────────────────────────────────────────────

Filter = Union["FieldEq", "FieldIn", "FieldLike", "All", "Any_", "Not"]


@dataclass(frozen=True, slots=True)
class FieldEq:
    field: str
    value: Any


@dataclass(frozen=True, slots=True)
class FieldIn:
    field: str
    values: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class FieldLike:
    field: str
    substring: str


@dataclass(frozen=True, slots=True)
class All:
    clauses: tuple[Filter, ...]


@dataclass(frozen=True, slots=True)
class Any_:
    """Declared for future FilterTreeFormat; unused in MultiFieldFormat."""
    clauses: tuple[Filter, ...]


@dataclass(frozen=True, slots=True)
class Not:
    """Declared for future FilterTreeFormat; unused in MultiFieldFormat."""
    clause: Filter


# ── Format enum ─────────────────────────────────────────────────────────


class MetadataFilterFormat(StrEnum):
    MULTIFIELD = "multifield"
    FILTER_TREE = "filter_tree"
    CHROMADB = "chromadb"
    ELASTICSEARCH = "elasticsearch"
    QDRANT = "qdrant"


@runtime_checkable
class FilterFormat(Protocol):
    format: MetadataFilterFormat

    def validate(self, native: Any) -> None: ...
    def parse(self, native: Any) -> Filter: ...


# ── MultiFieldFormat ────────────────────────────────────────────────────


_VALID_OPS = frozenset({"eq", "in", "like"})


@dataclass(frozen=True, slots=True)
class MultiFieldFormat:
    format: MetadataFilterFormat = MetadataFilterFormat.MULTIFIELD

    def validate(self, native: Any) -> None:
        if not isinstance(native, Mapping):
            raise ValueError(f"MultiFieldFormat expects a mapping; got {type(native).__name__}")
        for key, value in native.items():
            if key in ("$and", "$or", "$not"):
                raise ValueError(
                    f"MultiFieldFormat does not support boolean operator {key!r}. "
                    f"Use the filter_tree format instead."
                )
            if isinstance(value, Mapping):
                for op in value:
                    if op not in _VALID_OPS:
                        raise ValueError(
                            f"unknown operator {op!r} for field {key!r}; "
                            f"known: {sorted(_VALID_OPS)}"
                        )

    def parse(self, native: Any) -> Filter:
        self.validate(native)
        clauses: list[Filter] = []
        for field, value in native.items():
            if isinstance(value, Mapping):
                op, op_val = next(iter(value.items()))
                if op == "eq":
                    clauses.append(FieldEq(field=field, value=op_val))
                elif op == "in":
                    clauses.append(FieldIn(field=field, values=tuple(op_val)))
                elif op == "like":
                    clauses.append(FieldLike(field=field, substring=str(op_val)))
            else:
                clauses.append(FieldEq(field=field, value=value))
        return All(clauses=tuple(clauses))


# ── MetadataSchema ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    operators: frozenset[str] = frozenset({"eq"})


@dataclass(frozen=True, slots=True)
class MetadataSchema:
    fields: tuple[FieldSpec, ...]

    def field_names(self) -> frozenset[str]:
        return frozenset(f.name for f in self.fields)

    def validate(self, filter: Filter) -> None:
        unknown = _walk_fields(filter) - self.field_names()
        if unknown:
            raise ValueError(
                f"filter references unknown fields {sorted(unknown)}; "
                f"schema allows {sorted(self.field_names())}"
            )


def _walk_fields(filter: Filter) -> frozenset[str]:
    if isinstance(filter, FieldEq | FieldIn | FieldLike):
        return frozenset({filter.field})
    if isinstance(filter, All | Any_):
        out: set[str] = set()
        for c in filter.clauses:
            out |= _walk_fields(c)
        return frozenset(out)
    if isinstance(filter, Not):
        return _walk_fields(filter.clause)
    return frozenset()


# ── format_registry ─────────────────────────────────────────────────────


format_registry: dict[MetadataFilterFormat, FilterFormat] = {
    MetadataFilterFormat.MULTIFIELD: MultiFieldFormat(),
}
```

- [ ] **Step 1.5:** Run — expect 11 passing.

- [ ] **Step 1.6:** Commit.

```bash
git add python/pydocs_mcp/storage/__init__.py python/pydocs_mcp/storage/filters.py tests/storage/__init__.py tests/storage/test_filters.py
git commit -m "feat(storage): add Filter tree + MultiFieldFormat + MetadataSchema + format_registry (spec §5.1)"
```

---

## Task 2 — `storage/protocols.py` — 8 Protocols

**Files:**
- Create: `python/pydocs_mcp/storage/protocols.py`
- Create: `tests/storage/test_protocols.py`

- [ ] **Step 2.1:** Failing tests:

```python
"""Storage Protocols smoke tests (AC #3)."""
from __future__ import annotations

from pydocs_mcp.storage.protocols import (
    ChunkStore,
    FilterAdapter,
    HybridSearchable,
    ModuleMemberStore,
    PackageStore,
    TextSearchable,
    UnitOfWork,
    VectorSearchable,
)


def test_protocol_imports():
    for cls in (
        PackageStore, ChunkStore, ModuleMemberStore,
        TextSearchable, VectorSearchable, HybridSearchable,
        UnitOfWork, FilterAdapter,
    ):
        assert hasattr(cls, "__mro__")
```

- [ ] **Step 2.2:** Create `python/pydocs_mcp/storage/protocols.py`:

```python
"""Storage Protocols — 8 @runtime_checkable contracts (spec §5.2, AC #3)."""
from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydocs_mcp.models import Chunk, ModuleMember, Package
from pydocs_mcp.storage.filters import Filter


@runtime_checkable
class PackageStore(Protocol):
    async def upsert(self, package: Package) -> None: ...
    async def get(self, name: str) -> Package | None: ...
    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[Package]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...


@runtime_checkable
class ChunkStore(Protocol):
    async def upsert(self, chunks: Iterable[Chunk]) -> None: ...
    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[Chunk]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...
    async def rebuild_index(self) -> None: ...


@runtime_checkable
class ModuleMemberStore(Protocol):
    async def upsert_many(self, members: Iterable[ModuleMember]) -> None: ...
    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[ModuleMember]: ...
    async def delete(self, filter: Filter | Mapping) -> int: ...
    async def count(self, filter: Filter | Mapping | None = None) -> int: ...


# Each SearchMatch is a Chunk or ModuleMember with relevance/retriever_name set.
# Returning tuple[Chunk | ModuleMember, ...] — the "SearchMatch" type alias
# carried forward from sub-PR #1 is implemented via the Chunk/ModuleMember
# instances themselves (which carry relevance + retriever_name fields).


@runtime_checkable
class TextSearchable(Protocol):
    async def text_search(
        self,
        query_terms: str,
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]: ...


@runtime_checkable
class VectorSearchable(Protocol):
    async def vector_search(
        self,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]: ...


@runtime_checkable
class HybridSearchable(Protocol):
    async def hybrid_search(
        self,
        query_terms: str,
        query_vector: Sequence[float],
        limit: int,
        filter: Filter | Mapping | None = None,
        *,
        alpha: float = 0.5,
    ) -> tuple[Chunk, ...]: ...


@runtime_checkable
class FilterAdapter(Protocol):
    def adapt(self, filter: Filter) -> Any: ...


class UnitOfWork(Protocol):
    async def begin(self) -> AsyncIterator[None]: ...
```

- [ ] **Step 2.3:** Run + commit.

```bash
pytest tests/storage/test_protocols.py -v
git add python/pydocs_mcp/storage/protocols.py tests/storage/test_protocols.py
git commit -m "feat(storage): add 8 storage Protocols (spec §5.2, AC #3)"
```

---

## Task 3 — `storage/sqlite.py` foundation: contextvar + `SqliteUnitOfWork`

**Files:**
- Create: `python/pydocs_mcp/storage/sqlite.py` (initial)
- Create: `tests/storage/test_unit_of_work.py`

- [ ] **Step 3.1:** Failing tests:

```python
"""Tests for SqliteUnitOfWork + _maybe_acquire (spec §5.3)."""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.storage.sqlite import (
    SqliteUnitOfWork,
    _maybe_acquire,
    _sqlite_transaction,
)


@pytest.fixture
def db_file(tmp_path):
    f = tmp_path / "uow.db"
    open_index_database(f).close()
    return f


async def test_maybe_acquire_without_ambient_opens_fresh(db_file):
    provider = build_connection_provider(db_file)
    async with _maybe_acquire(provider) as conn:
        assert isinstance(conn, sqlite3.Connection)


async def test_maybe_acquire_reuses_ambient(db_file):
    provider = build_connection_provider(db_file)
    # Pretend a UoW has installed an ambient conn
    real = sqlite3.connect(str(db_file))
    token = _sqlite_transaction.set(real)
    try:
        async with _maybe_acquire(provider) as conn:
            assert conn is real
    finally:
        _sqlite_transaction.reset(token)
        real.close()


async def test_unit_of_work_commits_on_success(db_file):
    provider = build_connection_provider(db_file)
    uow = SqliteUnitOfWork(provider=provider)

    async with uow.begin():
        async with _maybe_acquire(provider) as conn:
            conn.execute(
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
                ("test_pkg", "1.0", "", "", "[]", "h", "dependency"),
            )

    # After commit, the row must be visible on a fresh connection
    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute("SELECT COUNT(*) FROM packages WHERE name=?", ("test_pkg",)).fetchone()[0]
    fresh.close()
    assert count == 1


async def test_unit_of_work_rollbacks_on_exception(db_file):
    provider = build_connection_provider(db_file)
    uow = SqliteUnitOfWork(provider=provider)

    with pytest.raises(RuntimeError, match="boom"):
        async with uow.begin():
            async with _maybe_acquire(provider) as conn:
                conn.execute(
                    "INSERT INTO packages (name, version, summary, homepage, "
                    "dependencies, content_hash, origin) VALUES (?,?,?,?,?,?,?)",
                    ("rolled_back", "1.0", "", "", "[]", "h", "dependency"),
                )
            raise RuntimeError("boom")

    fresh = sqlite3.connect(str(db_file))
    count = fresh.execute("SELECT COUNT(*) FROM packages WHERE name=?", ("rolled_back",)).fetchone()[0]
    fresh.close()
    assert count == 0
```

- [ ] **Step 3.2:** Create initial `python/pydocs_mcp/storage/sqlite.py`:

```python
"""SQLite storage adapters — UnitOfWork, Repositories, VectorStore, FilterAdapter."""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from pydocs_mcp.retrieval.protocols import ConnectionProvider


# Ambient transaction connection — set by SqliteUnitOfWork.begin, read by _maybe_acquire.
_sqlite_transaction: ContextVar[sqlite3.Connection | None] = ContextVar(
    "_sqlite_transaction", default=None,
)


@asynccontextmanager
async def _maybe_acquire(
    provider: ConnectionProvider,
) -> AsyncIterator[sqlite3.Connection]:
    """Reuse the ambient transaction's conn if set; otherwise acquire fresh via provider."""
    ambient = _sqlite_transaction.get()
    if ambient is not None:
        yield ambient
    else:
        async with provider.acquire() as conn:
            yield conn


@dataclass(frozen=True, slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope spanning multiple repository operations (spec §5.3)."""

    provider: ConnectionProvider

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        async with self.provider.acquire() as conn:
            await asyncio.to_thread(conn.execute, "BEGIN")
            token = _sqlite_transaction.set(conn)
            try:
                yield
            except BaseException:
                await asyncio.to_thread(conn.rollback)
                raise
            else:
                await asyncio.to_thread(conn.commit)
            finally:
                _sqlite_transaction.reset(token)
```

- [ ] **Step 3.3:** Run + commit.

```bash
pytest tests/storage/test_unit_of_work.py -v
git add python/pydocs_mcp/storage/sqlite.py tests/storage/test_unit_of_work.py
git commit -m "feat(storage): add _sqlite_transaction contextvar + SqliteUnitOfWork (spec §5.3)"
```

---

## Task 4 — Move row mappers `db.py` → `storage/sqlite.py` (spec §5.4)

**Files:**
- Modify: `python/pydocs_mcp/db.py` — delete `_chunk_to_row` / `_row_to_chunk` / `_package_to_row` / `_row_to_package` / `_module_member_to_row` / `_row_to_module_member`
- Modify: `python/pydocs_mcp/storage/sqlite.py` — append the 6 mappers
- Modify: any caller that imported from `pydocs_mcp.db` — update imports to `pydocs_mcp.storage.sqlite`

- [ ] **Step 4.1:** Grep to find every current importer of the 6 mappers:
```bash
grep -RIn "from pydocs_mcp.db import.*_row_to_\|from pydocs_mcp.db import.*_to_row" python/ tests/
```
Expected: `retrieval/retrievers.py` + maybe `tests/retrieval/test_retrievers.py` or helpers.

- [ ] **Step 4.2:** Copy the 6 functions verbatim from `db.py` and append to `storage/sqlite.py`. Make sure `import json` + `from pydocs_mcp.models import Chunk, ChunkFilterField, ModuleMember, ModuleMemberFilterField, Package, PackageOrigin, Parameter` are at the top of `sqlite.py`.

- [ ] **Step 4.3:** Remove the 6 functions from `db.py`.

- [ ] **Step 4.4:** Update every importer identified in Step 4.1 to import from `pydocs_mcp.storage.sqlite` instead of `pydocs_mcp.db`.

- [ ] **Step 4.5:** Run full suite.
```bash
pytest -q | tail -3
```
Expected: 395 passing (all prior tests green).

- [ ] **Step 4.6:** Commit — AC #35 inherited (layering inversion resolved).

```bash
git add -u
git commit -m "refactor: move row mappers db.py → storage/sqlite.py (spec §5.4, AC #35)"
```

---

## Task 5 — `SqliteFilterAdapter` + `SqlitePackageRepository`

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` (append)
- Create: `tests/storage/test_filter_adapter.py`

- [ ] **Step 5.1:** Failing tests — `tests/storage/test_filter_adapter.py`:

```python
"""Tests for SqliteFilterAdapter (spec §5.3 AC #7)."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.filters import All, FieldEq, FieldIn, FieldLike
from pydocs_mcp.storage.sqlite import SqliteFilterAdapter


def test_adapter_field_eq():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"package", "origin"}))
    where, params = adapter.adapt(FieldEq(field="package", value="fastapi"))
    assert where == "package = ?"
    assert params == ["fastapi"]


def test_adapter_field_in():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"scope"}))
    where, params = adapter.adapt(FieldIn(field="scope", values=("a", "b")))
    assert where == "scope IN (?, ?)"
    assert params == ["a", "b"]


def test_adapter_field_like():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"title"}))
    where, params = adapter.adapt(FieldLike(field="title", substring="routing"))
    assert "title LIKE ?" in where
    assert params == ["%routing%"]


def test_adapter_all_joins_with_and():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"package", "origin"}))
    tree = All(clauses=(
        FieldEq(field="package", value="x"),
        FieldEq(field="origin", value="y"),
    ))
    where, params = adapter.adapt(tree)
    assert "AND" in where
    assert params == ["x", "y"]


def test_adapter_rejects_unsafe_column():
    adapter = SqliteFilterAdapter(safe_columns=frozenset({"package"}))
    with pytest.raises(ValueError, match="not in safe_columns"):
        adapter.adapt(FieldEq(field="foo_bar; DROP TABLE", value="x"))
```

- [ ] **Step 5.2:** Append to `storage/sqlite.py`:

```python
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from pydocs_mcp.models import Chunk, ChunkFilterField, ModuleMember, ModuleMemberFilterField, Package, PackageOrigin, Parameter
from pydocs_mcp.storage.filters import All, Any_, FieldEq, FieldIn, FieldLike, Filter, Not, format_registry
from pydocs_mcp.storage.filters import MetadataFilterFormat  # re-used alias


@dataclass(frozen=True, slots=True)
class SqliteFilterAdapter:
    """Translates Filter tree → (WHERE fragment, params) for SQLite (spec §5.3)."""

    safe_columns: frozenset[str]

    def adapt(self, filter: Filter) -> tuple[str, list]:
        return self._adapt(filter)

    def _adapt(self, f: Filter) -> tuple[str, list]:
        if isinstance(f, FieldEq):
            self._check(f.field)
            return f"{f.field} = ?", [f.value]
        if isinstance(f, FieldIn):
            self._check(f.field)
            placeholders = ", ".join(["?"] * len(f.values))
            return f"{f.field} IN ({placeholders})", list(f.values)
        if isinstance(f, FieldLike):
            self._check(f.field)
            return f"{f.field} LIKE ?", [f"%{f.substring}%"]
        if isinstance(f, All):
            parts, params = [], []
            for c in f.clauses:
                sub, sub_p = self._adapt(c)
                parts.append(f"({sub})")
                params.extend(sub_p)
            return " AND ".join(parts), params
        if isinstance(f, (Any_, Not)):
            raise NotImplementedError(
                f"{type(f).__name__} not supported by SqliteFilterAdapter in sub-PR #3"
            )
        raise TypeError(f"unknown Filter type: {type(f).__name__}")

    def _check(self, column: str) -> None:
        if column not in self.safe_columns:
            raise ValueError(
                f"column {column!r} not in safe_columns {sorted(self.safe_columns)}"
            )


# Safe-column whitelists per table (spec §5.3)
_CHUNK_COLUMNS = frozenset({"package", "module", "origin", "title"})
_PACKAGE_COLUMNS = frozenset({"name", "version", "origin"})
_MEMBER_COLUMNS = frozenset({"package", "module", "name", "kind"})


def _resolve_filter(filter: Filter | Mapping | None):
    """Accept Mapping (parse via MultiFieldFormat) or a pre-parsed Filter tree."""
    if filter is None:
        return None
    if isinstance(filter, Mapping):
        return format_registry[MetadataFilterFormat.MULTIFIELD].parse(filter)
    return filter


@dataclass(frozen=True, slots=True)
class SqlitePackageRepository:
    """PackageStore backed by the 'packages' SQLite table (spec §5.3)."""

    provider: ConnectionProvider
    filter_adapter: SqliteFilterAdapter = field(
        default_factory=lambda: SqliteFilterAdapter(safe_columns=_PACKAGE_COLUMNS)
    )

    async def upsert(self, package: Package) -> None:
        row = _package_to_row(package)
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(
                conn.execute,
                "INSERT INTO packages (name, version, summary, homepage, "
                "dependencies, content_hash, origin) "
                "VALUES (:name,:version,:summary,:homepage,:dependencies,:content_hash,:origin) "
                "ON CONFLICT(name) DO UPDATE SET "
                "version=excluded.version, summary=excluded.summary, "
                "homepage=excluded.homepage, dependencies=excluded.dependencies, "
                "content_hash=excluded.content_hash, origin=excluded.origin",
                row,
            )

    async def get(self, name: str) -> Package | None:
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(
                    "SELECT * FROM packages WHERE name=?", (name,)
                ).fetchone()
            )
        return _row_to_package(row) if row else None

    async def list(
        self, filter: Filter | Mapping | None = None, limit: int | None = None,
    ) -> list[Package]:
        tree = _resolve_filter(filter)
        where, params = ("", [])
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
        sql = "SELECT * FROM packages"
        if where:
            sql += f" WHERE {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        async with _maybe_acquire(self.provider) as conn:
            rows = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchall()
            )
        return [_row_to_package(r) for r in rows]

    async def delete(self, filter: Filter | Mapping) -> int:
        tree = _resolve_filter(filter)
        if tree is None:
            raise ValueError("delete requires an explicit filter")
        where, params = self.filter_adapter.adapt(tree)
        async with _maybe_acquire(self.provider) as conn:
            cursor = await asyncio.to_thread(
                conn.execute, f"DELETE FROM packages WHERE {where}", params
            )
            return cursor.rowcount

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        tree = _resolve_filter(filter)
        sql = "SELECT COUNT(*) FROM packages"
        params: list = []
        if tree is not None:
            where, params = self.filter_adapter.adapt(tree)
            sql += f" WHERE {where}"
        async with _maybe_acquire(self.provider) as conn:
            row = await asyncio.to_thread(
                lambda: conn.execute(sql, params).fetchone()
            )
        return row[0]
```

- [ ] **Step 5.3:** Run + commit.

```bash
pytest tests/storage/test_filter_adapter.py -v
git add -u
git commit -m "feat(storage): add SqliteFilterAdapter + SqlitePackageRepository (spec §5.3)"
```

---

## Task 6 — `SqliteChunkRepository` + `SqliteVectorStore`

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` (append)
- Create: `tests/storage/test_vector_store.py`

Follow the same pattern as `SqlitePackageRepository`. Key differences:
- `upsert(chunks: Iterable[Chunk])` — batch insert via `executemany`.
- `rebuild_index()` — `INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')`.
- `SqliteVectorStore.text_search(query_terms, limit, filter)` — FTS5 MATCH + filter push-down via `SqliteFilterAdapter`; returns `tuple[Chunk, ...]` with `relevance` + `retriever_name` populated. SQL body lives in `_search_sync` dispatched via `asyncio.to_thread`. Filter pushes through `c.package = ?`, `c.title LIKE ?`, etc.

Commit: `feat(storage): add SqliteChunkRepository + SqliteVectorStore (spec §5.3, AC #9)`

---

## Task 7 — `SqliteModuleMemberRepository`

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` (append)
- Create: `tests/storage/test_repositories.py`

Mirror the ChunkRepository pattern with `upsert_many(members: Iterable[ModuleMember])`. No `rebuild_index` — module_members has no FTS5. `list`/`count`/`delete` use `_MEMBER_COLUMNS` safe-column set.

Commit: `feat(storage): add SqliteModuleMemberRepository (spec §5.3)`

---

## Task 8 — `storage/__init__.py` public API

**Files:**
- Modify: `python/pydocs_mcp/storage/__init__.py`

```python
"""Storage subpackage — protocols + SQLite adapters + filters."""
from pydocs_mcp.storage.filters import (
    All,
    Any_,
    FieldEq,
    FieldIn,
    FieldLike,
    FieldSpec,
    Filter,
    FilterFormat,
    MetadataFilterFormat,
    MetadataSchema,
    MultiFieldFormat,
    Not,
    format_registry,
)
from pydocs_mcp.storage.protocols import (
    ChunkStore,
    FilterAdapter,
    HybridSearchable,
    ModuleMemberStore,
    PackageStore,
    TextSearchable,
    UnitOfWork,
    VectorSearchable,
)
from pydocs_mcp.storage.sqlite import (
    SqliteChunkRepository,
    SqliteFilterAdapter,
    SqliteModuleMemberRepository,
    SqlitePackageRepository,
    SqliteUnitOfWork,
    SqliteVectorStore,
)

__all__ = [
    "All", "Any_", "ChunkStore", "FieldEq", "FieldIn", "FieldLike", "FieldSpec",
    "Filter", "FilterAdapter", "FilterFormat", "HybridSearchable",
    "MetadataFilterFormat", "MetadataSchema", "ModuleMemberStore",
    "MultiFieldFormat", "Not", "PackageStore", "SqliteChunkRepository",
    "SqliteFilterAdapter", "SqliteModuleMemberRepository",
    "SqlitePackageRepository", "SqliteUnitOfWork", "SqliteVectorStore",
    "TextSearchable", "UnitOfWork", "VectorSearchable", "format_registry",
]
```

Commit: `feat(storage): public re-exports in __init__.py`

---

## Task 9 — `application/indexing_service.py`

**Files:**
- Create: `python/pydocs_mcp/application/__init__.py` (empty)
- Create: `python/pydocs_mcp/application/indexing_service.py`
- Create: `tests/application/__init__.py` (empty)
- Create: `tests/application/test_indexing_service.py` — Protocol-fake stores ONLY (AC #10)

Write `IndexingService` per spec §5.6:

```python
"""Application service coordinating write-side indexing (spec §5.6)."""
from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.models import Chunk, ModuleMember, Package
from pydocs_mcp.storage.protocols import (
    ChunkStore,
    ModuleMemberStore,
    PackageStore,
    UnitOfWork,
)


@dataclass(frozen=True, slots=True)
class IndexingService:
    """Coordinates atomic write-side indexing across 3 entity stores.
    Depends ONLY on Protocols — backend-agnostic (AC #10)."""

    package_store: PackageStore
    chunk_store: ChunkStore
    module_member_store: ModuleMemberStore
    unit_of_work: UnitOfWork | None = None

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
    ) -> None:
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                await self._do_reindex(package, chunks, module_members)
        else:
            await self._do_reindex(package, chunks, module_members)

    async def remove_package(self, name: str) -> None:
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                await self._do_remove(name)
        else:
            await self._do_remove(name)

    async def clear_all(self) -> None:
        if self.unit_of_work is not None:
            async with self.unit_of_work.begin():
                await self._do_clear_all()
        else:
            await self._do_clear_all()

    async def _do_reindex(self, package, chunks, module_members) -> None:
        await self.chunk_store.delete(filter={"package": package.name})
        await self.module_member_store.delete(filter={"package": package.name})
        await self.package_store.delete(filter={"name": package.name})
        await self.package_store.upsert(package)
        await self.chunk_store.upsert(chunks)
        await self.module_member_store.upsert_many(module_members)

    async def _do_remove(self, name) -> None:
        await self.chunk_store.delete(filter={"package": name})
        await self.module_member_store.delete(filter={"package": name})
        await self.package_store.delete(filter={"name": name})

    async def _do_clear_all(self) -> None:
        await self.chunk_store.delete(filter={"package": {"like": "%"}})
        await self.module_member_store.delete(filter={"package": {"like": "%"}})
        await self.package_store.delete(filter={"name": {"like": "%"}})
```

Note: `clear_all` uses `LIKE '%'` — which matches every non-null package name. If you prefer a cleaner primitive, add an `await store.delete_all()` method to the protocols; but that enlarges the Protocol surface. The `LIKE '%'` pattern is acceptable and tested.

Tests use fake `PackageStore`/`ChunkStore`/`ModuleMemberStore` classes implementing the Protocols in-memory. No SQLite touched in these tests.

Commit: `feat(application): add IndexingService on Protocol-only dependencies (spec §5.6, AC #10)`

---

## Task 10 — `models.py` `SearchQuery` breaking change

**Files:**
- Modify: `python/pydocs_mcp/models.py`
- Modify: `tests/test_models.py`

Per spec §5.5: remove `package_filter`, `scope`, `title_filter`. Add `pre_filter` / `post_filter` with `pre_filter_format` / `post_filter_format`. Wire `model_validator(mode="after")` to `storage.filters.format_registry` for syntax validation.

- [ ] **Step 10.1:** Update `models.py`:

```python
# Replace existing SearchQuery class with:
from pydantic import field_validator, model_validator
from pydantic.dataclasses import dataclass as pyd_dataclass

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
        return v.strip()

    @field_validator("max_results")
    @classmethod
    def _positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_results must be positive")
        return v

    @model_validator(mode="after")
    def _validate_filter_syntax(self):
        from pydocs_mcp.storage.filters import format_registry
        for f, fmt in (
            (self.pre_filter, self.pre_filter_format),
            (self.post_filter, self.post_filter_format),
        ):
            if f is not None:
                format_registry[fmt].validate(f)
        return self
```

- [ ] **Step 10.2:** Update `tests/test_models.py` — replace any test referencing `package_filter` / `scope` / `title_filter` with equivalent `pre_filter` dicts. Add tests for malformed filter rejection (boolean ops, unknown operator).

- [ ] **Step 10.3:** Run `pytest tests/test_models.py -v` — update any test that constructs `SearchQuery(..., package_filter=..., scope=...)` to use `pre_filter={"package": ..., "scope": ...}` form. Test count will stay similar.

- [ ] **Step 10.4:** Commit.

```bash
git add python/pydocs_mcp/models.py tests/test_models.py
git commit -m "feat(models): SearchQuery breaking change — pre_filter/post_filter (spec §5.5, AC #12)"
```

---

## Task 11 — `retrieval/stages.py` — remove 3 filter stages, add `MetadataPostFilterStage`

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py` — delete `PackageFilterStage`, `ScopeFilterStage`, `TitleFilterStage`; add `MetadataPostFilterStage` (spec §5.8)
- Modify: `tests/retrieval/test_stages.py` — delete tests for the 3 removed stages; add 3 tests for the new stage

Spec §5.8:

```python
from pydocs_mcp.storage.filters import All, FieldEq, FieldIn, FieldLike, format_registry


@stage_registry.register("metadata_post_filter")
@dataclass(frozen=True, slots=True)
class MetadataPostFilterStage:
    name: str = "metadata_post_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.query.post_filter is None:
            return state
        tree = format_registry[state.query.post_filter_format].parse(state.query.post_filter)
        if state.result is None:
            return state
        kept = tuple(item for item in state.result.items if _evaluate(tree, item))
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=kept))
        return replace(state, result=ModuleMemberList(items=kept))

    def to_dict(self) -> dict:
        return {"type": "metadata_post_filter"}

    @classmethod
    def from_dict(cls, data, context):
        return cls()


def _evaluate(f, item) -> bool:
    if isinstance(f, All):
        return all(_evaluate(c, item) for c in f.clauses)
    if isinstance(f, FieldEq):
        return _field_value(item, f.field) == f.value
    if isinstance(f, FieldIn):
        return _field_value(item, f.field) in f.values
    if isinstance(f, FieldLike):
        v = _field_value(item, f.field) or ""
        return f.substring.lower() in str(v).lower()
    raise NotImplementedError(f"evaluator: {type(f).__name__}")


def _field_value(item, field: str):
    # For Chunk/ModuleMember, all the useful fields live in metadata.
    if hasattr(item, "metadata"):
        return item.metadata.get(field)
    return None
```

Commit: `refactor(retrieval): remove 3 filter stages + add MetadataPostFilterStage (spec §5.8, AC #13)`

---

## Task 12 — `retrieval/retrievers.py` refactor

**Files:**
- Modify: `python/pydocs_mcp/retrieval/retrievers.py`
- Modify: `tests/retrieval/test_retrievers.py`

Per spec §5.7:

```python
from pydocs_mcp.storage.protocols import ModuleMemberStore, TextSearchable
from pydocs_mcp.storage.filters import format_registry, MetadataSchema, FieldSpec


@retriever_registry.register("bm25_chunk")
@dataclass(frozen=True, slots=True)
class Bm25ChunkRetriever:
    store: TextSearchable
    allowed_fields: frozenset[str]
    name: str = "bm25_chunk"

    async def retrieve(self, query: SearchQuery) -> ChunkList:
        tree = None
        if query.pre_filter is not None:
            tree = format_registry[query.pre_filter_format].parse(query.pre_filter)
            unknown = _walk_fields(tree) - self.allowed_fields
            if unknown:
                raise ValueError(
                    f"filter references unknown fields {sorted(unknown)}; "
                    f"retriever allows {sorted(self.allowed_fields)}"
                )
        results = await self.store.text_search(
            query_terms=query.terms, limit=query.max_results, filter=tree,
        )
        return ChunkList(items=tuple(results))

    def to_dict(self) -> dict:
        return {"type": "bm25_chunk", "schema_name": "chunk"}

    @classmethod
    def from_dict(cls, data, context):
        schema_name = data.get("schema_name", "chunk")
        allowed = frozenset(context.app_config.metadata_schemas[schema_name])
        return cls(store=context.vector_store, allowed_fields=allowed)
```

`LikeMemberRetriever` takes a `SqliteModuleMemberRepository` and calls `.list(filter=...)` with a LIKE clause built from `query.terms`. Full code in spec §5.7.

**Important:** `BuildContext` grows `vector_store: SqliteVectorStore | None = None` and `app_config: AppConfig | None = None` fields. These are set at runtime by `server.py` at startup.

Commit: `refactor(retrieval): retrievers consume stores via Protocols + schema allowlist (spec §5.7, AC #15)`

---

## Task 13 — `retrieval/config.py` — `metadata_schemas` + YAML layering

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py`
- Create: `python/pydocs_mcp/presets/default_config.yaml`

Per spec §5.9:

`default_config.yaml`:
```yaml
cache_dir: ~/.pydocs-mcp
log_level: info

metadata_schemas:
  chunk:  [package, scope, origin, title, module]
  member: [package, module, name, kind]

pipelines:
  chunk:
    - default: true
      pipeline_path: presets/chunk_fts.yaml
  member:
    - default: true
      pipeline_path: presets/member_like.yaml
```

`AppConfig` extension: add `metadata_schemas: Mapping[str, tuple[str, ...]]` with **no Python default** (spec §5.9, AC #14). Wire `settings_customise_sources` to layer `shipped_source` → `user_source` → `env_settings` → `init_settings`. Add `PipelineRouteEntry` `@model_validator(mode='after')` enforcing `exactly one of predicate/default` (AC #32).

Commit: `feat(config): AppConfig.metadata_schemas + settings_customise_sources layering (spec §5.9, AC #14, #32)`

---

## Task 14 — Update preset pipelines

**Files:**
- Modify: `python/pydocs_mcp/presets/chunk_fts.yaml`
- Modify: `python/pydocs_mcp/presets/member_like.yaml`

Drop the 3 deleted filter stages; rely on retriever-level filter push-down + optional `metadata_post_filter`:

`chunk_fts.yaml`:
```yaml
name: chunk_fts
stages:
  - type: chunk_retrieval
    retriever:
      type: bm25_chunk
      schema_name: chunk
  - type: metadata_post_filter
  - type: limit
    max_results: 8
  - type: token_budget_formatter
    formatter: {type: chunk_markdown}
    budget: 2000
```

`member_like.yaml`:
```yaml
name: member_like
stages:
  - type: module_member_retrieval
    retriever:
      type: like_member
      schema_name: member
  - type: metadata_post_filter
  - type: limit
    max_results: 15
  - type: token_budget_formatter
    formatter: {type: member_markdown}
    budget: 2000
```

Commit: `refactor(presets): update chunk_fts + member_like for new stage graph (spec §6, AC #20)`

---

## Task 15 — `server.py` repository migration

**Files:**
- Modify: `python/pydocs_mcp/server.py`

Per spec §6.1/§6.2 + AC #17:

- At startup: build `provider`, `AppConfig.load()`, `BuildContext` including `vector_store=SqliteVectorStore(provider)` + `app_config=config` + `package_repository = SqlitePackageRepository(provider)` + module member repo.
- `list_packages` calls `await package_repository.list()`.
- `get_package_doc` builds a package `.get(name)` + chunk repo `.list(filter={"package": pkg, "origin": {"in": ["dependency_doc_file", ...]}})` query.
- `inspect_module` uses `package_repository.get` to check existence.
- `search_docs` / `search_api` translate `(package, internal, topic)` → `pre_filter` dict:
  ```python
  pre_filter: dict[str, Any] = {}
  if package:
      pre_filter["package"] = package
  if internal is True:
      pre_filter["scope"] = SearchScope.PROJECT_ONLY.value
  elif internal is False:
      pre_filter["scope"] = SearchScope.DEPENDENCIES_ONLY.value
  if topic:
      pre_filter["title"] = {"like": topic}
  sq = SearchQuery(terms=query, pre_filter=pre_filter or None)
  ```

MCP tool signatures byte-identical (AC #16).

Commit: `refactor(server): migrate handlers to repository layer (spec §6, AC #16, #17)`

---

## Task 16 — `__main__.py` CLI migration

**Files:**
- Modify: `python/pydocs_mcp/__main__.py`

Per spec §6.3 + AC #18:

- `index --force`: `await indexing_service.clear_all()` then loop `reindex_package` per package.
- `query` / `api` subcommands: build `pre_filter` dict the same way as server; invoke `SearchQuery(terms=..., pre_filter=...)`; run throwaway pipeline.

Commit: `refactor(cli): IndexingService.clear_all + pre_filter for query/api (spec §6.3, AC #18)`

---

## Task 17 — `indexer.py` writes through `IndexingService`

**Files:**
- Modify: `python/pydocs_mcp/indexer.py`

Replace every direct `INSERT INTO packages/chunks/module_members` SQL in `indexer.py` with `await indexing_service.reindex_package(pkg, chunks, members)`. The current signature `index_project_source(connection, project_dir)` becomes `index_project_source(indexing_service, project_dir)`. Same for `index_dependencies`.

Update callers in `__main__.py` to pass `indexing_service` instead of `connection`.

Commit: `refactor(indexer): writes through IndexingService (spec §6.4, AC #19)`

---

## Task 18 — Retriever-provider decoupling (AC #29)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/protocols.py` — add `open_sync()` to `ConnectionProvider`
- Modify: `python/pydocs_mcp/retrieval/pipeline.py` — `PerCallConnectionProvider.open_sync()` returns a fresh connection
- Modify: `python/pydocs_mcp/retrieval/retrievers.py` — any direct SQLite open via `self.provider.cache_path` replaced with `self.provider.open_sync()`

After sub-PR #3's store-based refactor (Tasks 5-7, 12), retrievers typically no longer touch SQLite directly (they delegate to `SqliteVectorStore`). But if `LikeMemberRetriever` still needs `MockConnectionProvider`-friendly access for tests, route through `provider.open_sync()`.

Commit: `refactor(retrieval): route retriever connections through provider protocol (AC #29)`

---

## Task 19 — `retrieval/__init__.py` registry init ordering (AC #30)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/__init__.py`

Ensure these are imported eagerly (with `# noqa: F401`) so decorator side-effects fire on bare `import pydocs_mcp.retrieval`:

```python
from pydocs_mcp.retrieval import stages as _stages  # noqa: F401
from pydocs_mcp.retrieval import retrievers as _retrievers  # noqa: F401
from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401
from pydocs_mcp.retrieval import predicates as _predicates  # noqa: F401
```

Add test:
```python
def test_bare_retrieval_import_populates_registries():
    import pydocs_mcp.retrieval
    from pydocs_mcp.retrieval import stage_registry, retriever_registry, formatter_registry
    from pydocs_mcp.retrieval.predicates import default_predicate_registry

    # Now 11 stages (dropped 3, added 1): chunk_retrieval, module_member_retrieval,
    # limit, parallel_retrieval, reciprocal_rank_fusion, conditional, route,
    # sub_pipeline, token_budget_formatter, metadata_post_filter.
    assert len(stage_registry.names()) >= 10
    assert len(retriever_registry.names()) >= 4
    assert len(formatter_registry.names()) >= 2
    assert len(default_predicate_registry.names()) >= 4
```

Commit: `fix(retrieval): eager import of stages/retrievers/formatters/predicates (AC #30)`

---

## Task 20 — `from_dict` recursion depth guard (AC #31)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/pipeline.py` — `CodeRetrieverPipeline.from_dict` gains `_depth: int = 0`
- Modify: `python/pydocs_mcp/retrieval/stages.py` — `SubPipelineStage.from_dict` increments depth

```python
_MAX_PIPELINE_DEPTH = 32


@classmethod
def from_dict(cls, data, context, _depth: int = 0):
    if _depth > _MAX_PIPELINE_DEPTH:
        raise ValueError(
            f"pipeline nesting exceeds max depth of {_MAX_PIPELINE_DEPTH}"
        )
    return cls(
        name=data["name"],
        stages=tuple(
            context.stage_registry.build(s, context, _depth=_depth)
            for s in data["stages"]
        ),
    )
```

`ComponentRegistry.build` also accepts `_depth` and forwards it; `SubPipelineStage.from_dict` calls `CodeRetrieverPipeline.from_dict(data["pipeline"], context, _depth=_depth + 1)`.

Test: construct a 33-level nested YAML; assert `ValueError` raised at load, not `RecursionError`.

Commit: `fix(retrieval): add recursion depth guard to pipeline from_dict (AC #31)`

---

## Task 21 — `ReciprocalRankFusionStage` first-seen merge (AC #33)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py` — change `items_by_key[key] = item` → `items_by_key.setdefault(key, item)`

Add test: two branches return the same chunk with different `retriever_name`; assert merged output preserves the first branch's retriever_name.

Commit: `fix(retrieval): RRF uses setdefault for first-seen merge (AC #33)`

---

## Task 22 — Composite chunk title sentinel (AC #34)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/stages.py` — `TokenBudgetFormatterStage` composite chunk sets `metadata[ChunkFilterField.TITLE.value] = "_composite"` (or defensibly: leaves TITLE absent and adds a `_sentinel` marker)
- Modify: `MetadataPostFilterStage._evaluate` or similar — treat `_composite` as pass-through

Add test: construct pipeline with `TokenBudgetFormatterStage` → `MetadataPostFilterStage` with a title filter; assert composite chunk is NOT dropped.

Commit: `fix(retrieval): composite chunks carry sentinel title to bypass post-filters (AC #34)`

---

## Task 23 — `PipelineRouteEntry` mutual-exclusion validator (AC #32)

**Files:**
- Modify: `python/pydocs_mcp/retrieval/config.py`

```python
from pydantic import model_validator


class PipelineRouteEntry(BaseModel):
    predicate: str | None = None
    default: bool = False
    pipeline_path: Path

    @model_validator(mode="after")
    def _exactly_one_of_predicate_default(self):
        has_predicate = self.predicate is not None
        if has_predicate and self.default:
            raise ValueError(
                "route entry must set exactly one of predicate or default; both set"
            )
        if not has_predicate and not self.default:
            raise ValueError(
                "route entry must set exactly one of predicate or default; neither set"
            )
        return self
```

Test all 4 cases: predicate-only (valid), default-only (valid), both (raises), neither (raises).

Commit: `fix(config): PipelineRouteEntry mutual-exclusion validator (AC #32)`

---

## Task 24 — Cleanup `db.py`

**Files:**
- Modify: `python/pydocs_mcp/db.py` — after Task 4 moved the row mappers out, `db.py` should contain only: `SCHEMA_VERSION`, `CACHE_DIR`, `cache_path_for_project`, `open_index_database`, `_drop_all_known_tables`, `remove_package`, `clear_all_packages`, `rebuild_fulltext_index`, `get_stored_content_hash`, `build_connection_provider`. Delete any leftover imports of `Chunk`/`ModuleMember`/`Package` that are no longer used.

Test: `grep -RIn "Chunk\|ModuleMember\|Package" python/pydocs_mcp/db.py` — should return few/no matches (only in schema DDL comments, if any).

Commit: `refactor(db): db.py holds schema + helpers only; row mappers moved (spec §5.4)`

---

## Task 25 — Test migration

**Files:**
- Modify: `tests/conftest.py` — existing fixture rewrite for new SearchQuery shape
- Modify: `tests/_retriever_helpers.py` — rewrite shim for `pre_filter` pattern
- Modify: every test using the old `package_filter` / `scope` / `title_filter` SearchQuery fields — update to `pre_filter={"package": ..., "scope": ..., "title": {"like": ...}}`

Run full suite after each file batch:
```bash
pytest -q | tail -3
```
Expected: 395+ passing (±few for renamed/added tests).

Commit: `test: migrate existing tests to new SearchQuery pre_filter shape (AC #21)`

---

## Task 26 — Integration smoke + zero-residue + golden parity

- [ ] **Step 26.1:** Clean cache + full re-index + warm re-index smoke.
```bash
rm -rf ~/.pydocs-mcp
time pydocs-mcp index .
time pydocs-mcp index .
```

- [ ] **Step 26.2:** `pydocs-mcp query "routing"` + `api APIRouter` — output sanity.

- [ ] **Step 26.3:** Golden parity:
```bash
pytest tests/retrieval/test_parity_golden.py -v
```
Expected: all 3 pass.

- [ ] **Step 26.4:** `test_parallel_retrieval_stage_preserves_filtered_branches`:
```bash
pytest tests/retrieval/test_stages.py::test_parallel_retrieval_stage_preserves_filtered_branches -v
```
Expected: pass (AC #28 inherited from sub-PR #2).

- [ ] **Step 26.5:** Zero-residue grep:
```bash
grep -RIn "\blegacy\b" python/ src/ tests/
grep -RIn "package_filter\|title_filter\|\.scope\b" python/pydocs_mcp/ tests/
grep -RIn "PackageFilterStage\|ScopeFilterStage\|TitleFilterStage" python/ tests/
grep -RIn "except Exception" python/pydocs_mcp/retrieval/ python/pydocs_mcp/storage/ python/pydocs_mcp/application/ | grep -v "^[^:]*:[^:]*:\s*raise"
```
All must be empty.

- [ ] **Step 26.6:** Ruff + cargo + pytest final:
```bash
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
pytest -q | tail -3
```

Commit fixes if any: `refactor: clean up residue from Task 26 sweep`.

---

## Task 27 — CLAUDE.md refresh + mark PR ready

**Files:**
- Modify: `CLAUDE.md`

Add architecture section entries for `storage/` + `application/` subpackages. Update the data-flow diagram to mention `IndexingService` and `SqliteVectorStore`.

Commit: `docs: refresh CLAUDE.md architecture — storage/ + application/ subpackages`.

Push + mark PR #15 ready:
```bash
git push
gh pr ready 15
```

Post completion comment on PR #15 summarizing: AC coverage table (1–35), test count, coverage %, CI status, inherited-AC verification summary, and any deviations from the plan.

---

## Self-review

**1. Spec coverage map:**

| AC | Task |
|---|---|
| #1 storage/ 4 files | Tasks 1, 2, 3, 5, 6, 7, 8 |
| #2 application/ 2 files | Task 9 |
| #3 8 Protocols | Task 2 |
| #4 Filter tree + MultiFieldFormat + format_registry | Task 1 |
| #5 MultiFieldFormat.validate rejects | Task 1 |
| #6 MetadataSchema.validate | Task 1 |
| #7 sqlite.py components | Tasks 3, 5, 6, 7 |
| #8 _maybe_acquire uses | Tasks 5, 6, 7 |
| #9 SqliteVectorStore.text_search | Task 6 |
| #10 IndexingService | Task 9 |
| #11 UoW patterns | Task 9 |
| #12 SearchQuery breaking change | Task 10 |
| #13 Stage remove+add | Task 11 |
| #14 AppConfig.metadata_schemas | Task 13 |
| #15 Retrievers use stores | Task 12 |
| #16 server.py MCP surface | Task 15 |
| #17 server.py repo layer | Task 15 |
| #18 __main__.py | Task 16 |
| #19 indexer.py via IndexingService | Task 17 |
| #20 Updated presets | Task 14 |
| #21 No test deleted | Task 25 + invariant |
| #22 tests/storage/ | Tasks 1-7 |
| #23 test_end_to_end | Task 26 |
| #24 No `legacy` | Task 26 grep |
| #25 No blanket swallow (strengthened) | Invariant + Task 26 grep |
| #26 Deps unchanged | Task 0 verify |
| #27 Byte-parity (strengthened) | Task 26 golden test |
| #28 ParallelRetrievalStage dedup (inherited) | Tests carried forward from sub-PR #2 |
| #29 Retriever-provider decoupling | Task 18 |
| #30 retrieval/__init__.py eager imports | Task 19 |
| #31 from_dict recursion depth | Task 20 |
| #32 PipelineRouteEntry validator | Task 23 |
| #33 RRF first-seen | Task 21 |
| #34 Composite title sentinel | Task 22 |
| #35 Retrieval-layer storage boundary | Task 4 (row mappers move) |

**2. Placeholder scan:** No "TBD", "implement later", "add appropriate error handling" — every code change has concrete code. Tasks 6, 7, 14, 15, 16, 17 reference the spec for long-form bodies but the shapes are specified explicitly.

**3. Type consistency:**
- `SearchQuery(terms, pre_filter, post_filter, ...)` is used consistently across Tasks 10, 12, 15, 16.
- `ConnectionProvider` gains `open_sync()` in Task 18; only used there.
- `BuildContext` extension (`vector_store`, `app_config`) in Task 12; used in `from_dict` in the same task.
- `IndexingService(package_store, chunk_store, module_member_store, unit_of_work=None)` consistent across Tasks 9, 16, 17.

---

## Execution handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-19-sub-pr-3-storage-repositories.md`. Two execution options:

**1. Subagent-Driven (recommended)** — fresh Opus subagent per task, two-stage review; invokes `superpowers:subagent-driven-development`.

**2. Inline Execution** — batch execution with checkpoints; invokes `superpowers:executing-plans`.
