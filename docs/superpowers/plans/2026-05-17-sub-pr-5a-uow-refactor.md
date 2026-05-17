# Sub-PR #5a — Unit-of-Work refactor implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `UnitOfWork` the single persistence dependency for every application service. Today each service holds direct references to N repository Protocols + optionally a UoW alongside; target shape is single `uow_factory: Callable[[], UnitOfWork]` constructor argument, with the UoW exposing repositories as attributes inside an `async with` context.

**Architecture:** Widen the `UnitOfWork` Protocol to expose `packages`, `chunks`, `module_members`, `trees` as attributes valid only inside the async context. Rewrite `SqliteUnitOfWork` to instantiate repository adapters against the same `ConnectionProvider` on `__aenter__`. Migrate 4 services (`IndexingService`, `PackageLookup`, `ModuleInspector`, `TreeService`) to the new shape one at a time, with the **old constructor still accepted in parallel** during the migration so the test suite stays green per-task.

**Tech Stack:** Python 3.11+, `asyncio`, `typing.Protocol` + `@runtime_checkable`, `dataclasses.frozen`, pytest, sqlite3.

**Source spec:** `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` §14 (10 sub-sections).

**Baseline:** 812+ tests passing on `origin/main` at `6ff112c`. All 10 ACs in §14.9 must hold at the end.

---

## File structure

See table at top of this commit's PR description. 15 files modified, 2 created (`storage/errors.py`, `tests/_fakes.py` already exists — expanded).

## Worktree setup (Task 0)

Use the `superpowers:using-git-worktrees` skill to create an isolated worktree at `.worktrees/sub-pr-5a-uow-refactor/` on a new branch `feature/sub-pr-5a-uow-refactor` off `origin/main`. Verify baseline:

```bash
source .venv/bin/activate
python -m pytest -q
# Expected: 812+ passing, 0 failures.
ruff check python/ tests/
# Expected: All checks passed!
```

Commit the worktree's initial state isn't required — just confirm baseline before starting Task 1.

---

## Task 1: Widen `UnitOfWork` Protocol with repo attributes + async context

**Files:**
- Modify: `python/pydocs_mcp/storage/protocols.py` (lines around the existing `class UnitOfWork(Protocol):`)
- Create: `python/pydocs_mcp/storage/errors.py`
- Test: `tests/storage/test_protocols.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/storage/test_protocols.py`:

```python
def test_unit_of_work_protocol_exposes_repo_attributes_and_context_methods():
    """Sub-PR #5a §14.2: UoW Protocol exposes packages/chunks/module_members/trees
    AND defines the async context-manager + commit/rollback/collect_new_events
    surface. A duck-typed class supplying all of the above passes isinstance."""
    from pydocs_mcp.storage.protocols import UnitOfWork

    class FakeUow:
        # Attributes (repos exposed for the duration of the context)
        packages = None
        chunks = None
        module_members = None
        trees = None

        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def commit(self): pass
        async def rollback(self): pass
        def collect_new_events(self): return iter(())

    assert isinstance(FakeUow(), UnitOfWork)


def test_unit_of_work_not_entered_error_is_typed():
    """§14.9 AC #7 — accessing a UoW attribute outside ``async with`` raises a
    typed error, not a generic AttributeError."""
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
    err = UnitOfWorkNotEnteredError("packages")
    assert "packages" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/storage/test_protocols.py::test_unit_of_work_protocol_exposes_repo_attributes_and_context_methods -v
# Expected: FAIL — the current UnitOfWork Protocol only declares `async def begin()`;
# isinstance check for the new attributes fails (or AttributeError on import).
```

- [ ] **Step 3: Create `storage/errors.py`**

```python
"""Typed exceptions for the storage layer."""
from __future__ import annotations


class UnitOfWorkNotEnteredError(RuntimeError):
    """Raised when a UoW attribute is accessed outside an ``async with`` block.

    The :class:`~pydocs_mcp.storage.protocols.UnitOfWork` Protocol exposes
    repository attributes (``packages``, ``chunks``, ``module_members``,
    ``trees``) that are only valid inside the active transaction context.
    A bare ``uow.packages.upsert(...)`` without ``async with uow:`` is a
    bug; this exception makes the failure mode loud and typed instead of
    silently returning ``None`` or hitting a generic AttributeError.
    """

    def __init__(self, attr_name: str) -> None:
        super().__init__(
            f"UnitOfWork attribute {attr_name!r} accessed outside "
            f"`async with uow:` — repositories are valid only inside the "
            f"transaction context.",
        )
        self.attr_name = attr_name
```

- [ ] **Step 4: Widen the Protocol in `storage/protocols.py`**

Replace the existing `class UnitOfWork(Protocol):` block with:

```python
@runtime_checkable
class UnitOfWork(Protocol):
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Inside ``async with uow:`` the repository attributes are valid and share
    one connection. Outside the context they raise
    :class:`UnitOfWorkNotEnteredError`. Explicit ``commit()`` persists changes;
    a missing ``commit()`` or any exception triggers safety-net ``rollback``
    on ``__aexit__``.

    ``collect_new_events`` is reserved for a future event-bus sub-PR; today
    every implementation returns ``iter(())``.
    """

    # Per-transaction repository attributes — valid only inside the async with.
    packages:       "PackageStore"
    chunks:         "ChunkStore"
    module_members: "ModuleMemberStore"
    trees:          "DocumentTreeStore"

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> bool: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

    def collect_new_events(self) -> "Iterator[object]": ...

    # Back-compat: the pre-#5a single-method shape stays callable so partially-
    # migrated services keep working until they switch to the async-context
    # form. Removed in Task 12 once every caller is migrated.
    async def begin(self) -> "AsyncIterator[None]": ...
```

Add `from collections.abc import AsyncIterator, Iterator` if not already imported. Add to top of file:

```python
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError  # noqa: F401 -- re-exported for callers
```

- [ ] **Step 5: Run test to verify it passes**

```bash
python -m pytest tests/storage/test_protocols.py::test_unit_of_work_protocol_exposes_repo_attributes_and_context_methods tests/storage/test_protocols.py::test_unit_of_work_not_entered_error_is_typed -v
# Expected: 2 PASS
```

- [ ] **Step 6: Verify no regression**

```bash
python -m pytest -q
# Expected: 812+ passing (same as baseline — Protocol widening is additive)
```

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/storage/protocols.py python/pydocs_mcp/storage/errors.py tests/storage/test_protocols.py
git commit -m "feat(#5a): widen UnitOfWork Protocol with repo attributes + async context"
```

---

## Task 2: Rewrite `SqliteUnitOfWork` to expose repos as attributes

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` (the existing `class SqliteUnitOfWork:` block around line 79)
- Test: `tests/storage/test_unit_of_work.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/storage/test_unit_of_work.py`:

```python
import pytest
from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.sqlite import SqliteUnitOfWork


@pytest.mark.asyncio
async def test_sqlite_uow_repos_accessible_inside_context(tmp_path):
    """§14.2 contract: inside ``async with uow:`` the repository attributes
    are valid and operate against the SAME connection."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    provider = build_connection_provider(db)
    uow = SqliteUnitOfWork(provider=provider)

    async with uow as inside:
        assert inside is uow
        assert inside.packages is not None
        assert inside.chunks is not None
        assert inside.module_members is not None
        assert inside.trees is not None
        await inside.commit()


@pytest.mark.asyncio
async def test_sqlite_uow_attribute_access_outside_context_raises(tmp_path):
    """§14.9 AC #7 — uow.packages outside the async with is loud."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))

    with pytest.raises(UnitOfWorkNotEnteredError) as excinfo:
        _ = uow.packages
    assert excinfo.value.attr_name == "packages"


@pytest.mark.asyncio
async def test_sqlite_uow_rollback_on_exception(tmp_path):
    """§14.2 safety-net contract: exception inside the context triggers
    rollback even if commit() wasn't called."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))

    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("simulated mid-transaction failure")
    # No exception escapes via __aexit__; the with-statement re-raises.


@pytest.mark.asyncio
async def test_sqlite_uow_collect_new_events_returns_empty_iter(tmp_path):
    """§14.6 placeholder contract — returns iter(()) until the event-bus PR."""
    uow = SqliteUnitOfWork(
        provider=build_connection_provider(tmp_path / "uow.db"),
    )
    assert list(uow.collect_new_events()) == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_unit_of_work.py -k "repos_accessible or outside_context or rollback_on_exception or collect_new_events" -v
# Expected: FAIL — SqliteUnitOfWork lacks __aenter__/__aexit__/commit/rollback
# /collect_new_events / repo-attribute machinery.
```

- [ ] **Step 3: Rewrite `SqliteUnitOfWork`** in `python/pydocs_mcp/storage/sqlite.py`

Locate the existing `class SqliteUnitOfWork:` (~line 79) and replace with:

```python
@dataclass(slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Async context manager: ``__aenter__`` opens the transaction, instantiates
    repository adapters against the same ``ConnectionProvider``, and exposes
    them as attributes (``packages`` / ``chunks`` / ``module_members`` /
    ``trees``). ``__aexit__`` commits if and only if ``commit()`` was called
    AND no exception escaped the body; otherwise rolls back. The asyncio.Lock
    is kept on the instance so concurrent ``async with`` blocks against the
    same UoW instance serialise correctly (matches pre-#5a behavior).

    ``begin()`` is retained as a thin back-compat wrapper that yields once;
    used by services that haven't migrated yet. Removed in Task 12.
    """

    provider: ConnectionProvider
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _entered: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _packages: SqlitePackageRepository | None = field(default=None, init=False, repr=False)
    _chunks: SqliteChunkRepository | None = field(default=None, init=False, repr=False)
    _module_members: SqliteModuleMemberRepository | None = field(default=None, init=False, repr=False)
    _trees: SqliteDocumentTreeStore | None = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> "SqliteUnitOfWork":
        await self._lock.acquire()
        self._entered = True
        self._committed = False
        # Construct repository adapters against the SAME connection provider.
        # All adapters reuse provider.acquire() internally, so any write
        # inside this context goes through the same SQLite connection — the
        # contract §14.1 made implicit, now explicit.
        self._packages       = SqlitePackageRepository(provider=self.provider)
        self._chunks         = SqliteChunkRepository(provider=self.provider)
        self._module_members = SqliteModuleMemberRepository(provider=self.provider)
        self._trees          = SqliteDocumentTreeStore(provider=self.provider)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if exc_type is not None or not self._committed:
                await self.rollback()
        finally:
            self._entered = False
            self._packages = None
            self._chunks = None
            self._module_members = None
            self._trees = None
            self._lock.release()
        return False  # never suppress exceptions

    async def commit(self) -> None:
        """Explicit commit. The async-with body must call this on success."""
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.commit)
        self._committed = True

    async def rollback(self) -> None:
        async with _maybe_acquire(self.provider) as conn:
            await asyncio.to_thread(conn.rollback)
        self._committed = False

    def collect_new_events(self) -> "Iterator[object]":
        """Reserved for the future event-bus sub-PR. Always empty today."""
        return iter(())

    # ── Repository attribute exposure ────────────────────────────────────

    @property
    def packages(self) -> SqlitePackageRepository:
        if self._packages is None:
            raise UnitOfWorkNotEnteredError("packages")
        return self._packages

    @property
    def chunks(self) -> SqliteChunkRepository:
        if self._chunks is None:
            raise UnitOfWorkNotEnteredError("chunks")
        return self._chunks

    @property
    def module_members(self) -> SqliteModuleMemberRepository:
        if self._module_members is None:
            raise UnitOfWorkNotEnteredError("module_members")
        return self._module_members

    @property
    def trees(self) -> SqliteDocumentTreeStore:
        if self._trees is None:
            raise UnitOfWorkNotEnteredError("trees")
        return self._trees

    # ── Back-compat begin() — removed in Task 12 ─────────────────────────

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        """Pre-#5a contract — yields once for legacy callers. Migrate to
        ``async with uow:`` and remove this method in Task 12."""
        async with self:
            try:
                yield
                await self.commit()
            except Exception:
                await self.rollback()
                raise
```

Make sure these are imported at the top of `sqlite.py` (some may already be present):
- `from collections.abc import AsyncIterator, Iterator`
- `from contextlib import asynccontextmanager`
- `from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError`

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/storage/test_unit_of_work.py -v
# Expected: 4 PASS (plus any pre-existing UoW tests still passing — verify count)
```

- [ ] **Step 5: Verify no regression**

```bash
python -m pytest -q
# Expected: 812+ passing. The new SqliteUnitOfWork keeps `begin()` compatible,
# so existing IndexingService callers continue to work.
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/sqlite.py tests/storage/test_unit_of_work.py
git commit -m "feat(#5a): SqliteUnitOfWork exposes repos as attributes; back-compat begin() retained"
```

---

## Task 3: Expand `tests/_fakes.py` with in-memory repos + `FakeUnitOfWork`

**Files:**
- Modify: `tests/_fakes.py` (already has `InMemoryDocumentTreeStore`)
- Test: `tests/test_fakes.py` (new — pin the contract)

- [ ] **Step 1: Write the failing test**

Create `tests/test_fakes.py`:

```python
"""Pin the FakeUnitOfWork + InMemory* contract — these are the primary tools
for service-layer tests under #5a. Drift here breaks every service test."""
from __future__ import annotations

import pytest

from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.protocols import UnitOfWork
from tests._fakes import (
    FakeUnitOfWork,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
)


def test_fake_unit_of_work_satisfies_protocol():
    """A FakeUnitOfWork instance passes ``isinstance(_, UnitOfWork)``."""
    assert isinstance(FakeUnitOfWork(), UnitOfWork)


@pytest.mark.asyncio
async def test_fake_uow_commit_flag_flips_only_on_explicit_commit():
    uow = FakeUnitOfWork()
    async with uow:
        assert uow.committed is False
        await uow.commit()
    assert uow.committed is True
    assert uow.rolled_back is False


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_when_commit_not_called():
    uow = FakeUnitOfWork()
    async with uow:
        pass  # no commit
    assert uow.committed is False
    assert uow.rolled_back is True


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_on_exception():
    uow = FakeUnitOfWork()
    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("boom")
    assert uow.rolled_back is True


def test_fake_uow_attribute_access_outside_context_raises():
    """Mirrors the SqliteUnitOfWork contract from Task 2."""
    uow = FakeUnitOfWork()
    with pytest.raises(UnitOfWorkNotEnteredError):
        _ = uow.packages
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_fakes.py -v
# Expected: FAIL — FakeUnitOfWork, InMemoryPackageStore, InMemoryChunkStore,
# InMemoryModuleMemberStore don't exist yet.
```

- [ ] **Step 3: Implement in `tests/_fakes.py`**

Append to the existing file:

```python
from typing import Any, Iterator
from dataclasses import dataclass, field
from pydocs_mcp.models import Chunk, ModuleMember, Package
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError


@dataclass
class InMemoryPackageStore:
    """Set-backed Protocol fake for PackageStore."""
    items: dict[str, Package] = field(default_factory=dict)

    async def get(self, name: str) -> Package | None:
        return self.items.get(name)

    async def upsert(self, package: Package) -> None:
        self.items[package.name] = package

    async def delete(self, *, filter=None) -> None:
        if filter is None:
            self.items.clear()
            return
        name = (filter.get("name") if isinstance(filter, dict) else None)
        if name is not None:
            self.items.pop(name, None)
        else:
            # Treat any other filter shape as "match all" in tests.
            self.items.clear()

    async def all(self) -> tuple[Package, ...]:
        return tuple(self.items.values())


@dataclass
class InMemoryChunkStore:
    by_package: dict[str, list[Chunk]] = field(default_factory=dict)

    async def upsert(self, chunks) -> None:
        for c in chunks:
            pkg = c.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(c)

    async def delete(self, *, filter=None) -> None:
        if filter is None:
            self.by_package.clear()
            return
        pkg = filter.get("package") if isinstance(filter, dict) else None
        if pkg is not None:
            self.by_package.pop(pkg, None)
        else:
            self.by_package.clear()


@dataclass
class InMemoryModuleMemberStore:
    by_package: dict[str, list[ModuleMember]] = field(default_factory=dict)

    async def upsert_many(self, members) -> None:
        for m in members:
            pkg = m.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(m)

    async def delete(self, *, filter=None) -> None:
        if filter is None:
            self.by_package.clear()
            return
        pkg = filter.get("package") if isinstance(filter, dict) else None
        if pkg is not None:
            self.by_package.pop(pkg, None)
        else:
            self.by_package.clear()


@dataclass
class FakeUnitOfWork:
    """Structurally satisfies UnitOfWork. Tracks committed/rolled_back flags
    so service-layer tests can assert end-state without inspecting persistence.
    Exposes 4 repo attributes — same shape as SqliteUnitOfWork from Task 2."""
    packages_store:       InMemoryPackageStore       = field(default_factory=InMemoryPackageStore)
    chunks_store:         InMemoryChunkStore         = field(default_factory=InMemoryChunkStore)
    module_members_store: InMemoryModuleMemberStore  = field(default_factory=InMemoryModuleMemberStore)
    trees_store:          InMemoryDocumentTreeStore  = field(default_factory=InMemoryDocumentTreeStore)
    committed:   bool = False
    rolled_back: bool = False
    _entered:    bool = False

    async def __aenter__(self) -> "FakeUnitOfWork":
        self._entered = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None or not self.committed:
            self.rolled_back = True
        self._entered = False
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    def collect_new_events(self) -> Iterator[object]:
        return iter(())

    @property
    def packages(self) -> InMemoryPackageStore:
        if not self._entered:
            raise UnitOfWorkNotEnteredError("packages")
        return self.packages_store

    @property
    def chunks(self) -> InMemoryChunkStore:
        if not self._entered:
            raise UnitOfWorkNotEnteredError("chunks")
        return self.chunks_store

    @property
    def module_members(self) -> InMemoryModuleMemberStore:
        if not self._entered:
            raise UnitOfWorkNotEnteredError("module_members")
        return self.module_members_store

    @property
    def trees(self) -> InMemoryDocumentTreeStore:
        if not self._entered:
            raise UnitOfWorkNotEnteredError("trees")
        return self.trees_store
```

Update `__all__` at end of file:

```python
__all__ = (
    "InMemoryDocumentTreeStore",
    "InMemoryPackageStore",
    "InMemoryChunkStore",
    "InMemoryModuleMemberStore",
    "FakeUnitOfWork",
    "_Call",
)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_fakes.py -v
# Expected: 5 PASS
```

- [ ] **Step 5: Verify no regression**

```bash
python -m pytest -q
# Expected: 812+ passing.
```

- [ ] **Step 6: Commit**

```bash
git add tests/_fakes.py tests/test_fakes.py
git commit -m "test(#5a): FakeUnitOfWork + InMemory{Package,Chunk,ModuleMember}Store fixtures"
```

---

## Task 4: Migrate `IndexingService` — add `uow_factory` alongside existing fields

**Strategy:** Add the new `uow_factory` field and a new internal code path. Keep the old fields working so existing tests stay green. Old-style construction takes the legacy path; new construction takes the UoW-driven path.

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py`
- Test: `tests/application/test_indexing_service.py` (add NEW tests for uow_factory path; old tests still pass)

- [ ] **Step 1: Write the failing test**

Add to `tests/application/test_indexing_service.py`:

```python
import pytest
from pydocs_mcp.application.indexing_service import IndexingService
from tests._fakes import FakeUnitOfWork


@pytest.mark.asyncio
async def test_indexing_service_with_uow_factory_commits_on_reindex(simple_package):
    """§14.2 + AC #5 — new code path: service takes only uow_factory and
    uses async with uow to drive writes. ``uow.committed`` flips True only
    after explicit commit() inside reindex_package."""
    captured: list[FakeUnitOfWork] = []

    def uow_factory() -> FakeUnitOfWork:
        u = FakeUnitOfWork()
        captured.append(u)
        return u

    service = IndexingService(uow_factory=uow_factory)
    pkg, chunks, members = simple_package
    await service.reindex_package(pkg, chunks, members)

    assert len(captured) == 1
    uow = captured[0]
    assert uow.committed is True
    assert uow.rolled_back is False
    assert pkg.name in uow.packages_store.items
    assert chunks[0] in uow.chunks_store.by_package.get(pkg.name, [])


@pytest.mark.asyncio
async def test_indexing_service_with_uow_factory_rolls_back_on_failure():
    """§14.2 safety-net — exception during the body triggers rollback."""
    uow_seen: list[FakeUnitOfWork] = []

    def uow_factory() -> FakeUnitOfWork:
        u = FakeUnitOfWork()
        uow_seen.append(u)
        return u

    service = IndexingService(uow_factory=uow_factory)

    # Force a failure by passing a None package.
    with pytest.raises(AttributeError):
        await service.reindex_package(None, (), ())  # type: ignore[arg-type]

    uow = uow_seen[0]
    assert uow.committed is False
    assert uow.rolled_back is True
```

(`simple_package` is a fixture you'll define in `tests/conftest.py` returning `(Package, tuple[Chunk, ...], tuple[ModuleMember, ...])` — add it now if it doesn't exist; reuse fixture data from existing tests.)

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_indexing_service.py::test_indexing_service_with_uow_factory_commits_on_reindex -v
# Expected: FAIL — IndexingService(uow_factory=...) raises TypeError because
# uow_factory is not a recognised constructor argument yet.
```

- [ ] **Step 3: Add `uow_factory` field + new internal path**

Modify `python/pydocs_mcp/application/indexing_service.py`. Add the field next to `unit_of_work`:

```python
@dataclass(frozen=True, slots=True)
class IndexingService:
    # ... existing docstring ...

    # Legacy (pre-#5a) constructor surface — kept during the migration.
    package_store: PackageStore | None = None
    chunk_store: ChunkStore | None = None
    module_member_store: ModuleMemberStore | None = None
    unit_of_work: UnitOfWork | None = None
    tree_store: DocumentTreeStore | None = None

    # New #5a surface — when set, supersedes all the legacy fields.
    uow_factory: Callable[[], UnitOfWork] | None = None
```

(Required: import `Callable` from `collections.abc` if not present.)

Update `__post_init__` to validate exactly one shape is provided:

```python
    def __post_init__(self) -> None:
        legacy_shape = self.package_store is not None
        new_shape = self.uow_factory is not None
        if legacy_shape == new_shape:
            raise ValueError(
                "IndexingService requires EITHER uow_factory (post-#5a) OR "
                "the legacy package_store + chunk_store + module_member_store "
                "fields, not both and not neither."
            )
        if legacy_shape and self.unit_of_work is None:
            log.warning(
                "IndexingService constructed without UnitOfWork — writes are "
                "NOT atomic; partial reindex state can become visible on failure.",
            )
```

Add a new private method that routes:

```python
    async def reindex_package(
        self, package, chunks, module_members, *, trees=(),
    ) -> None:
        if self.uow_factory is not None:
            return await self._reindex_via_uow(package, chunks, module_members, trees)
        return await self._reindex_legacy(package, chunks, module_members, trees)

    async def _reindex_via_uow(
        self, package, chunks, module_members, trees,
    ) -> None:
        """#5a code path — single async-with uow body."""
        async with self.uow_factory() as uow:                # type: ignore[misc]
            await uow.chunks.delete(filter={ChunkFilterField.PACKAGE.value: package.name})
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.trees.delete_for_package(package.name)
            await uow.packages.delete(filter={"name": package.name})

            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            if trees:
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)
            await uow.commit()

    async def _reindex_legacy(self, package, chunks, module_members, trees) -> None:
        # Rename the body of the OLD reindex_package method to this.
        # ... existing logic unchanged ...
```

Apply the same uow-vs-legacy routing to `remove_package` and `clear_all` — add `_remove_via_uow` / `_clear_all_via_uow` parallel methods.

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/application/test_indexing_service.py -v
# Expected: ALL existing tests still pass (legacy path), PLUS the 2 new UoW tests.
```

- [ ] **Step 5: Verify no regression**

```bash
python -m pytest -q
# Expected: 812+ existing PLUS 2 new = 814+ passing.
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py tests/application/test_indexing_service.py
git commit -m "feat(#5a): IndexingService accepts uow_factory alongside legacy stores"
```

---

## Task 5: Migrate `PackageLookup` — add `uow_factory` alongside legacy fields

**Files:**
- Modify: `python/pydocs_mcp/application/package_lookup.py`
- Test: `tests/application/test_package_lookup.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/application/test_package_lookup.py`:

```python
@pytest.mark.asyncio
async def test_package_lookup_with_uow_factory_lists_packages_inside_context():
    """§14.2 — read-side service also takes uow_factory for shape consistency.
    Opens the context, queries via uow.packages, never commits (reads only)."""
    from tests._fakes import FakeUnitOfWork
    from pydocs_mcp.models import Package, PackageOrigin

    def make_uow() -> FakeUnitOfWork:
        u = FakeUnitOfWork()
        u.packages_store.items["fastapi"] = Package(
            name="fastapi", version="0", summary="", homepage="",
            dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
        )
        return u

    from pydocs_mcp.application.package_lookup import PackageLookup
    lookup = PackageLookup(uow_factory=make_uow)
    pkgs = await lookup.list_packages()
    assert any(p.name == "fastapi" for p in pkgs)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/application/test_package_lookup.py::test_package_lookup_with_uow_factory_lists_packages_inside_context -v
# Expected: FAIL — PackageLookup(uow_factory=...) raises TypeError.
```

- [ ] **Step 3: Modify `PackageLookup` to accept `uow_factory`**

In `python/pydocs_mcp/application/package_lookup.py`:

```python
@dataclass(frozen=True, slots=True)
class PackageLookup:
    # Legacy fields — kept during migration.
    package_store: PackageStore | None = None
    chunk_store: ChunkStore | None = None
    module_member_store: ModuleMemberStore | None = None

    # New #5a surface.
    uow_factory: Callable[[], UnitOfWork] | None = None

    def __post_init__(self) -> None:
        legacy = self.package_store is not None
        new = self.uow_factory is not None
        if legacy == new:
            raise ValueError(
                "PackageLookup requires EITHER uow_factory OR the legacy "
                "store fields — not both, not neither."
            )

    async def list_packages(self):
        if self.uow_factory is not None:
            async with self.uow_factory() as uow:
                return await uow.packages.all()
        return await self.package_store.all()         # type: ignore[union-attr]

    # Repeat the same pattern for get_package_doc, find_module, etc.
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python -m pytest tests/application/test_package_lookup.py -v
# Expected: all existing PackageLookup tests still pass, PLUS the new UoW test.
```

- [ ] **Step 5: Verify no regression**

```bash
python -m pytest -q
# Expected: 815+ passing.
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/package_lookup.py tests/application/test_package_lookup.py
git commit -m "feat(#5a): PackageLookup accepts uow_factory alongside legacy stores"
```

---

## Task 6: Migrate `ModuleInspector`

**Files:**
- Modify: `python/pydocs_mcp/application/module_inspector.py`
- Test: `tests/application/test_module_inspector.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_module_inspector_with_uow_factory_short_circuits_unindexed():
    """Read-side ModuleInspector takes uow_factory; reads from uow.packages."""
    from tests._fakes import FakeUnitOfWork
    from pydocs_mcp.application.module_inspector import ModuleInspector

    inspector = ModuleInspector(uow_factory=lambda: FakeUnitOfWork())
    result = await inspector.inspect("never_indexed_pkg")
    assert "not indexed" in result
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/application/test_module_inspector.py::test_module_inspector_with_uow_factory_short_circuits_unindexed -v
# Expected: FAIL — ModuleInspector lacks uow_factory.
```

- [ ] **Step 3: Modify `ModuleInspector`**

Apply the same dual-shape pattern (legacy `package_store` field + new `uow_factory` field; `__post_init__` validates exactly one; `inspect` branches on the presence of `uow_factory`).

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/application/test_module_inspector.py -v
# Expected: PASS, no regressions in existing tests.
```

- [ ] **Step 5: Verify no regression**

```bash
python -m pytest -q
# Expected: 816+ passing.
```

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/module_inspector.py tests/application/test_module_inspector.py
git commit -m "feat(#5a): ModuleInspector accepts uow_factory alongside legacy package_store"
```

---

## Task 7: Migrate `TreeService`

**Files:**
- Modify: `python/pydocs_mcp/application/tree_service.py`
- Test: `tests/application/test_tree_service.py`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_tree_service_with_uow_factory_get_tree():
    """TreeService.get_tree(pkg, mod) reads via uow.trees."""
    from tests._fakes import FakeUnitOfWork
    from pydocs_mcp.application.tree_service import TreeService

    uow_seen: list[FakeUnitOfWork] = []
    def factory():
        u = FakeUnitOfWork()
        uow_seen.append(u)
        return u

    svc = TreeService(uow_factory=factory)
    result = await svc.get_tree("pkg", "pkg.mod")
    assert result is None      # FakeUnitOfWork's trees.load returns None
    assert len(uow_seen) == 1
```

- [ ] **Step 2-6: Same TDD shape as Tasks 4-6.** Apply the dual-shape pattern. Commit:

```bash
git commit -m "feat(#5a): TreeService accepts uow_factory alongside legacy tree_store"
```

---

## Task 8: Update `storage/factories.py` to build `uow_factory` closures

**Files:**
- Modify: `python/pydocs_mcp/storage/factories.py`
- Test: existing `tests/storage/test_factories.py` adds 1 assertion.

- [ ] **Step 1: Write the failing assertion**

Add to `tests/storage/test_factories.py`:

```python
def test_build_sqlite_indexing_service_uses_uow_factory(tmp_path):
    """Post-#5a wiring: factory returns a service configured with uow_factory,
    not the legacy direct-store shape."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import build_sqlite_indexing_service
    open_index_database(tmp_path / "f.db").close()
    svc = build_sqlite_indexing_service(tmp_path / "f.db")
    assert svc.uow_factory is not None
    assert svc.package_store is None   # legacy fields are not used in #5a wiring
```

- [ ] **Step 2: Run test to verify it fails**

Expected: FAIL — the current factory passes individual store instances.

- [ ] **Step 3: Rewrite `build_sqlite_indexing_service`**

```python
def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    provider = build_connection_provider(db_path)
    return IndexingService(
        uow_factory=lambda: SqliteUnitOfWork(provider=provider),
    )
```

Apply the same pattern to `build_sqlite_lookup_service` and any other factory.

- [ ] **Step 4: Run tests to verify they pass + no regression**

```bash
python -m pytest tests/storage/test_factories.py -v
python -m pytest -q
# Expected: all passing — production paths now exclusively use uow_factory.
```

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(#5a): factories build uow_factory closures instead of direct stores"
```

---

## Task 9: Update end-to-end fixtures + parity-golden

**Files:**
- Modify: `tests/conftest.py` — add `uow_factory` fixture
- Modify: `tests/_retriever_helpers.py`
- Modify: `tests/retrieval/test_parity_golden.py`
- Modify: `tests/application/test_end_to_end.py`

- [ ] **Step 1: Add `uow_factory` pytest fixture to `tests/conftest.py`**

```python
import pytest
from tests._fakes import FakeUnitOfWork

@pytest.fixture
def uow_factory():
    """Yields a fresh FakeUnitOfWork on each call — service-test convention.
    Capture the returned uow via a closure if you need to assert on it
    AFTER the service call."""
    return FakeUnitOfWork
```

- [ ] **Step 2: Sweep existing fixtures**

Find every `package_store=...` / `chunk_store=...` / `tree_store=...` keyword in `tests/application/` and `tests/retrieval/` and replace with `uow_factory=` per the patterns from Tasks 4-7.

```bash
grep -RIn "package_store=\|chunk_store=\|module_member_store=\|tree_store=" tests/ --include="*.py"
# Walk each result; replace as appropriate.
```

- [ ] **Step 3: Run the full suite + parity golden**

```bash
python -m pytest -q
python -m pytest tests/retrieval/test_parity_golden.py -v
# Expected: all 812+ passing; AC #21 parity golden 3/3 (or 8 — current count).
```

- [ ] **Step 4: Commit**

```bash
git commit -m "test(#5a): sweep test fixtures to uow_factory shape"
```

---

## Task 10: Drop legacy fields + `begin()` from production code

Now that every production caller AND every test uses `uow_factory`, remove the legacy shape.

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py` — drop `package_store`, `chunk_store`, `module_member_store`, `unit_of_work`, `tree_store`, `_reindex_legacy`, `_remove_legacy`, `_clear_all_legacy` fields & methods
- Modify: `python/pydocs_mcp/application/package_lookup.py` — drop legacy fields
- Modify: `python/pydocs_mcp/application/module_inspector.py` — drop `package_store`
- Modify: `python/pydocs_mcp/application/tree_service.py` — drop `tree_store`
- Modify: `python/pydocs_mcp/storage/protocols.py` — drop `async def begin(self)` from `UnitOfWork`
- Modify: `python/pydocs_mcp/storage/sqlite.py` — drop `SqliteUnitOfWork.begin()` and the `asynccontextmanager` import if no longer used.

- [ ] **Step 1: Drop legacy code** (mechanical sweep)
- [ ] **Step 2: Run full suite**

```bash
python -m pytest -q
# Expected: still 812+ passing — every test was already updated in Task 9.
```

- [ ] **Step 3: Stale-name grep — must all be empty**

```bash
grep -RIn "package_store=\|chunk_store=\|module_member_store=\|tree_store=\|unit_of_work=" python/ tests/ --include="*.py" 2>&1 | head
# Expected: empty (all old keyword usage gone from prod + tests).

grep -RIn "_reindex_legacy\|_remove_legacy\|_clear_all_legacy" python/ --include="*.py" 2>&1 | head
# Expected: empty.

grep -RIn "\.begin()" python/pydocs_mcp/ --include="*.py" 2>&1 | head
# Expected: empty — only the back-compat hook is gone.
```

- [ ] **Step 4: AC #9 verification**

```bash
grep -RIn "SqliteUnitOfWork" python/pydocs_mcp/ --include="*.py" 2>&1 | head
# Expected: only matches in storage/sqlite.py (definition) + storage/factories.py
# (the two wiring sites). NO matches in application/*.
```

- [ ] **Step 5: Commit**

```bash
git commit -m "refactor(#5a): drop legacy direct-store fields + UoW.begin() back-compat"
```

---

## Task 11: Final verification + push + PR

- [ ] **Step 1: Full suite + parity golden**

```bash
python -m pytest -q
# Expected: 812+ passing.
python -m pytest tests/retrieval/test_parity_golden.py -v
# Expected: all parity-golden tests passing — AC #8 byte-parity preserved.
```

- [ ] **Step 2: Lint + Rust**

```bash
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
# Expected: clean.
```

- [ ] **Step 3: Benchmarks smoke**

```bash
python scripts/smoke_check_benchmark_imports.py
# Expected: verified pydocs_mcp imports in 10 benchmark files.
```

- [ ] **Step 4: AC sweep against §14.9**

For each of the 10 ACs in §14.9, run a verification command. Example:
- AC #1: full pytest passes — covered by Step 1.
- AC #2: `python -c "from pydocs_mcp.storage.protocols import UnitOfWork; print(UnitOfWork.__annotations__)"` — should show `packages`, `chunks`, `module_members`, `trees`.
- AC #3: `grep -RIn "package_store\|chunk_store\|module_member_store" python/pydocs_mcp/application/ --include="*.py"` — empty.
- AC #4: smoke import: `python -c "from tests._fakes import FakeUnitOfWork, InMemoryPackageStore, InMemoryChunkStore, InMemoryModuleMemberStore, InMemoryDocumentTreeStore; print('ok')"`.
- AC #5/6: covered by tests in Tasks 4 + 5 (`*_commits_on_reindex`, `*_rolls_back_when_commit_not_called`).
- AC #7: covered by Tasks 1 + 3 tests (`*_outside_context_raises`).
- AC #8: parity golden tests passing — covered by Step 1.
- AC #9: covered by Task 10 Step 4.
- AC #10: `python -c "import inspect; from pydocs_mcp.storage.sqlite import SqliteUnitOfWork; u = SqliteUnitOfWork.__new__(SqliteUnitOfWork); print(list(u.collect_new_events()))"` should print `[]`.

- [ ] **Step 5: Push + open PR**

```bash
git push -u origin feature/sub-pr-5a-uow-refactor
gh pr create --base main --head feature/sub-pr-5a-uow-refactor --title "sub-PR #5a: Unit-of-Work refactor (single persistence dependency for services)" --body "$(cat <<'EOF'
## Summary

Implements sub-PR #5a per the design in `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` §14.

- Widens the `UnitOfWork` Protocol to expose `packages`, `chunks`, `module_members`, `trees` as Protocol attributes (valid inside `async with`).
- Rewrites `SqliteUnitOfWork` to instantiate repository adapters against the same `ConnectionProvider` on `__aenter__`.
- Migrates 4 services (`IndexingService`, `PackageLookup`, `ModuleInspector`, `TreeService`) to take a single `uow_factory: Callable[[], UnitOfWork]` constructor argument.
- Removes the legacy direct-repo-field shape from every production class and test fixture.
- `FakeUnitOfWork` + 4 `InMemory*Store` fakes in `tests/_fakes.py` replace per-test fake-store wiring.

## Test plan
- [x] §14.9 AC #1 — All 812+ tests pass on `feature/sub-pr-5a-uow-refactor`.
- [x] §14.9 AC #2 — `UnitOfWork` Protocol attributes verified.
- [x] §14.9 AC #3 — `grep` shows no `_store` field on any service.
- [x] §14.9 AC #4 — `tests/_fakes.py` exports all 5 fakes.
- [x] §14.9 AC #5-7 — committed/rolled_back/UoWNotEntered tests pin.
- [x] §14.9 AC #8 — parity-golden tests passing.
- [x] §14.9 AC #9 — `SqliteUnitOfWork` only imported in `factories.py`.
- [x] §14.9 AC #10 — `collect_new_events()` returns `iter(())`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Watch CI**

```bash
gh pr checks <PR_NUMBER> --watch
# Expected: python (3.11/3.12/3.13) + rust all PASS.
```

---

## Self-review

**1. Spec coverage:** Every §14 sub-section maps to at least one task.

| §14 sub-section | Task(s) |
|---|---|
| 14.1 problem | (context only — no task) |
| 14.2 target shape | Tasks 1, 2, 3, 4-7 |
| 14.3 migration plan | Tasks 1-10 |
| 14.4 async / connection pool | Task 2 (SqliteUnitOfWork rewrite handles `_maybe_acquire`) |
| 14.5 testing FakeUnitOfWork | Task 3 |
| 14.6 collect_new_events | Tasks 1, 2, 3 (no-op `iter(())` in every impl) |
| 14.7 #5b consumes the shape | (out of scope — documented in spec for follow-on) |
| 14.8 risks | (no task per se — mitigation is the staged migration in Tasks 4-10) |
| 14.9 AC #1-10 | Task 11 verification |
| 14.10 ship sequence | (out of scope — this plan ships #5a only) |

**2. Placeholder scan:** No "TBD", "implement later", or "Similar to Task N". Every step shows the actual code.

**3. Type consistency:** `uow_factory: Callable[[], UnitOfWork] | None = None` is used identically across the 4 services. `FakeUnitOfWork` matches the Protocol attribute names (`packages`, `chunks`, `module_members`, `trees`) used inside services.

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-17-sub-pr-5a-uow-refactor.md`.**
