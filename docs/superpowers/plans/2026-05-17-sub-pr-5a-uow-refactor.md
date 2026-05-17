# Sub-PR #5a — UnitOfWork Protocol widening + SqliteUnitOfWork rewrite (TRIMMED)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the `UnitOfWork` Protocol with `__aenter__`/`__aexit__`/`commit`/`rollback` and 4 repository attributes (`packages`, `chunks`, `module_members`, `trees`). Rewrite `SqliteUnitOfWork` to expose those repositories against the same `ConnectionProvider` and properly manage the `_sqlite_transaction` ContextVar. Add `FakeUnitOfWork` for downstream test use. **No application service is modified.** The legacy `begin()` method stays as a back-compat shim so existing services and tests work unchanged.

**Architecture:** Additive Protocol + concrete-class refactor. Services keep their existing 5-field constructor shape. The new shape is available for #5b's tests to consume; whether #5b's `IndexingService` ever migrates is a future decision with no current pressure.

**Tech Stack:** Python 3.11+, `asyncio`, `typing.Protocol` + `@runtime_checkable`, `ContextVar`, `dataclasses`, pytest, sqlite3.

**Source spec:** `docs/superpowers/specs/2026-04-20-sub-pr-5b-cross-node-reference-graph-design.md` §14 (trimmed 2026-05-17 after plan-review).

**Baseline:** 812+ tests passing on `origin/main` (commit `6ff112c`). **Diff size:** ~250 LOC across 6 files.

---

## Task 0: Worktree + baseline

Use `superpowers:using-git-worktrees` to create an isolated worktree at `.worktrees/sub-pr-5a-uow/` on a new branch `feature/sub-pr-5a-uow-refactor` off `origin/main`.

- [ ] **Step 1: Baseline verification**

```bash
source .venv/bin/activate
python -m pytest -q
ruff check python/ tests/
```

Expected: 812+ passing, ruff clean.

---

## Task 1: Widen `UnitOfWork` Protocol + `UnitOfWorkNotEnteredError`

**Files:**
- Create: `python/pydocs_mcp/storage/errors.py`
- Modify: `python/pydocs_mcp/storage/protocols.py`
- Test: `tests/storage/test_protocols.py`

- [ ] **Step 1: Write failing tests** — Add to `tests/storage/test_protocols.py`:

```python
def test_unit_of_work_protocol_exposes_repo_attributes_and_context_methods():
    """§14.2 — UoW Protocol exposes packages/chunks/module_members/trees
    AND defines __aenter__/__aexit__/commit/rollback."""
    from pydocs_mcp.storage.protocols import UnitOfWork

    class FakeUow:
        packages = None
        chunks = None
        module_members = None
        trees = None
        async def __aenter__(self): return self
        async def __aexit__(self, exc_type, exc, tb): return False
        async def commit(self): pass
        async def rollback(self): pass
        async def begin(self): yield

    assert isinstance(FakeUow(), UnitOfWork)


def test_unit_of_work_not_entered_error_is_typed():
    """§14.9 AC #7 — outside-context access raises typed error."""
    from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
    err = UnitOfWorkNotEnteredError("packages")
    assert "packages" in str(err)
    assert err.attr_name == "packages"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_protocols.py::test_unit_of_work_protocol_exposes_repo_attributes_and_context_methods tests/storage/test_protocols.py::test_unit_of_work_not_entered_error_is_typed -v
```

Expected: FAIL on both.

- [ ] **Step 3: Create `python/pydocs_mcp/storage/errors.py`**

```python
"""Typed exceptions for the storage layer."""
from __future__ import annotations


class UnitOfWorkNotEnteredError(RuntimeError):
    """Raised when a UoW attribute is accessed outside ``async with``.

    Repository attributes on :class:`UnitOfWork` are only valid inside
    the active transaction context. Loud failure beats silent None.
    """

    def __init__(self, attr_name: str) -> None:
        super().__init__(
            f"UnitOfWork attribute {attr_name!r} accessed outside "
            f"`async with uow:` — repositories are valid only inside "
            f"the transaction context.",
        )
        self.attr_name = attr_name
```

- [ ] **Step 4: Widen the Protocol** — In `python/pydocs_mcp/storage/protocols.py`, locate the existing minimal `class UnitOfWork(Protocol):` and replace with:

```python
@runtime_checkable
class UnitOfWork(Protocol):
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2)."""

    packages:       "PackageStore"
    chunks:         "ChunkStore"
    module_members: "ModuleMemberStore"
    trees:          "DocumentTreeStore"

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, exc_type, exc, tb) -> bool: ...

    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...

    # Pre-#5a back-compat — services that haven't migrated still call this.
    async def begin(self) -> "AsyncIterator[None]": ...
```

Add at top of file:

```python
from collections.abc import AsyncIterator
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError  # noqa: F401
```

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/storage/test_protocols.py -v
python -m pytest -q
```

Expected: 2 new PASS; full suite still 812+ passing.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/errors.py python/pydocs_mcp/storage/protocols.py tests/storage/test_protocols.py
git commit -m "feat(#5a): widen UnitOfWork Protocol with repo attributes + async context"
```

---

## Task 2: Rewrite `SqliteUnitOfWork`

**Files:**
- Modify: `python/pydocs_mcp/storage/sqlite.py` (existing `class SqliteUnitOfWork:` ~line 79)
- Test: `tests/storage/test_unit_of_work.py`

**4 correctness requirements caught by eng plan-review:**

1. `__aenter__` MUST set `_sqlite_transaction` ContextVar — else repo attributes open their own connections (atomicity broken).
2. `commit()` / `rollback()` MUST operate on held connection directly — going via `_maybe_acquire` would deadlock on `self._lock`.
3. `__aexit__` MUST reset ContextVar AND release lock in `finally` — even on rollback failure.
4. `begin()` MUST preserve prior contract exactly — existing `IndexingService._in_uow` depends on it.

- [ ] **Step 1: Write failing tests** — Replace `tests/storage/test_unit_of_work.py`:

```python
import pytest
from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import Package, PackageOrigin
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
from pydocs_mcp.storage.sqlite import SqliteUnitOfWork


def _pkg(name: str = "x") -> Package:
    return Package(
        name=name, version="0", summary="", homepage="",
        dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
    )


@pytest.mark.asyncio
async def test_sqlite_uow_repos_accessible_inside_context(tmp_path):
    """§14.9 AC #2 — repo attributes valid inside async with."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow as inside:
        assert inside is uow
        assert inside.packages is not None
        assert inside.chunks is not None
        assert inside.module_members is not None
        assert inside.trees is not None
        await inside.commit()


@pytest.mark.asyncio
async def test_sqlite_uow_attribute_outside_context_raises(tmp_path):
    """§14.9 AC #7."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    with pytest.raises(UnitOfWorkNotEnteredError) as excinfo:
        _ = uow.packages
    assert excinfo.value.attr_name == "packages"


@pytest.mark.asyncio
async def test_sqlite_uow_commit_persists_across_reopen(tmp_path):
    """§14.9 AC #3 — proves the ContextVar wired through. Without the
    ContextVar fix the upsert commits to a transient connection and
    is not visible after reopen."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()

    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow:
        await uow.packages.upsert(_pkg("inside_uow"))
        await uow.commit()

    uow2 = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow2:
        got = await uow2.packages.get("inside_uow")
        assert got is not None
        assert got.name == "inside_uow"


@pytest.mark.asyncio
async def test_sqlite_uow_rollback_on_exception(tmp_path):
    """§14.2 safety-net — exception triggers rollback."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()

    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    with pytest.raises(ValueError):
        async with uow:
            await uow.packages.upsert(_pkg("doomed"))
            raise ValueError("simulated")

    uow2 = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow2:
        got = await uow2.packages.get("doomed")
        assert got is None


@pytest.mark.asyncio
async def test_sqlite_uow_rollback_when_commit_not_called(tmp_path):
    """§14.2 — exit without commit rolls back."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()

    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow:
        await uow.packages.upsert(_pkg("nocommit"))

    uow2 = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow2:
        got = await uow2.packages.get("nocommit")
        assert got is None


@pytest.mark.asyncio
async def test_sqlite_uow_legacy_begin_still_works(tmp_path):
    """§14.9 AC #4 — pre-#5a callers using begin() unaffected."""
    db = tmp_path / "uow.db"
    open_index_database(db).close()
    uow = SqliteUnitOfWork(provider=build_connection_provider(db))
    async with uow.begin():
        pass  # body without exception → commit on exit
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/storage/test_unit_of_work.py -v
```

Expected: ALL FAIL.

- [ ] **Step 3: Rewrite the class** — In `python/pydocs_mcp/storage/sqlite.py`, locate the existing `class SqliteUnitOfWork:` (~line 79) and replace its body with:

```python
@dataclass(slots=True)
class SqliteUnitOfWork:
    """Atomic transaction scope + per-transaction repository accessor (spec §14.2).

    Async context manager: ``__aenter__`` acquires the lock + a single
    connection, runs BEGIN, sets the ``_sqlite_transaction`` ContextVar
    (so repo adapters route through the held connection), and exposes
    repos as attributes. ``__aexit__`` rolls back if commit wasn't
    called or an exception escaped, resets the ContextVar, releases
    the connection, and releases the lock — in a ``finally`` block so
    cleanup always runs.

    ``commit()`` / ``rollback()`` operate on ``self._held_conn``
    directly — NOT via ``_maybe_acquire`` (which would deadlock trying
    to re-acquire ``self._lock``).

    ``begin()`` is the pre-#5a back-compat shim — services using
    ``async with uow.begin():`` keep working.
    """

    provider: ConnectionProvider
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _entered: bool = field(default=False, init=False, repr=False)
    _committed: bool = field(default=False, init=False, repr=False)
    _held_conn: object | None = field(default=None, init=False, repr=False)
    _ctx_token: object | None = field(default=None, init=False, repr=False)
    _packages: "SqlitePackageRepository | None" = field(default=None, init=False, repr=False)
    _chunks: "SqliteChunkRepository | None" = field(default=None, init=False, repr=False)
    _module_members: "SqliteModuleMemberRepository | None" = field(default=None, init=False, repr=False)
    _trees: "SqliteDocumentTreeStore | None" = field(default=None, init=False, repr=False)

    async def __aenter__(self) -> "SqliteUnitOfWork":
        await self._lock.acquire()
        try:
            conn = await self.provider.acquire()
            await asyncio.to_thread(conn.execute, "BEGIN")
            self._held_conn = conn
            self._ctx_token = _sqlite_transaction.set((conn, self._lock))
            self._packages       = SqlitePackageRepository(provider=self.provider)
            self._chunks         = SqliteChunkRepository(provider=self.provider)
            self._module_members = SqliteModuleMemberRepository(provider=self.provider)
            self._trees          = SqliteDocumentTreeStore(provider=self.provider)
            self._entered = True
            self._committed = False
            return self
        except BaseException:
            self._lock.release()
            raise

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        try:
            if (exc_type is not None or not self._committed) and self._held_conn is not None:
                await asyncio.to_thread(self._held_conn.rollback)
        finally:
            if self._ctx_token is not None:
                _sqlite_transaction.reset(self._ctx_token)
                self._ctx_token = None
            if self._held_conn is not None:
                try:
                    await self.provider.release(self._held_conn)
                except Exception:  # noqa: BLE001 -- best-effort cleanup
                    log.debug("provider.release raised during UoW exit", exc_info=True)
                self._held_conn = None
            self._packages = self._chunks = self._module_members = self._trees = None
            self._entered = False
            self._lock.release()
        return False

    async def commit(self) -> None:
        if self._held_conn is None:
            raise UnitOfWorkNotEnteredError("commit")
        await asyncio.to_thread(self._held_conn.commit)
        self._committed = True

    async def rollback(self) -> None:
        if self._held_conn is None:
            raise UnitOfWorkNotEnteredError("rollback")
        await asyncio.to_thread(self._held_conn.rollback)
        self._committed = False

    @property
    def packages(self) -> "SqlitePackageRepository":
        if self._packages is None:
            raise UnitOfWorkNotEnteredError("packages")
        return self._packages

    @property
    def chunks(self) -> "SqliteChunkRepository":
        if self._chunks is None:
            raise UnitOfWorkNotEnteredError("chunks")
        return self._chunks

    @property
    def module_members(self) -> "SqliteModuleMemberRepository":
        if self._module_members is None:
            raise UnitOfWorkNotEnteredError("module_members")
        return self._module_members

    @property
    def trees(self) -> "SqliteDocumentTreeStore":
        if self._trees is None:
            raise UnitOfWorkNotEnteredError("trees")
        return self._trees

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        """Pre-#5a contract — yields once for legacy callers."""
        async with self:
            try:
                yield
                await self.commit()
            except Exception:
                raise  # __aexit__ safety-net rolls back
```

Add to top of `sqlite.py` if not already present:

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/storage/test_unit_of_work.py -v
```

Expected: 6 PASS.

- [ ] **Step 5: Full no-regression check (CRITICAL)**

```bash
python -m pytest -q
```

Expected: 818+ passing. The `begin()` back-compat path must preserve every existing `IndexingService` test. If anything fails, the regression is in the `begin()` wrapper — most likely a ContextVar ordering issue. Use `pytest -x --pdb` to investigate.

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/storage/sqlite.py tests/storage/test_unit_of_work.py
git commit -m "feat(#5a): SqliteUnitOfWork exposes repos via async context manager"
```

---

## Task 3: `FakeUnitOfWork` + InMemory stores

**Files:**
- Modify: `tests/_fakes.py`
- Create: `tests/test_fakes.py`

InMemory store fakes MUST match real Protocol method signatures — `list(filter, limit)` (NOT `all()`).

- [ ] **Step 1: Confirm real Protocol shapes**

```bash
grep -B 1 -A 10 "^class PackageStore\|^class ChunkStore\|^class ModuleMemberStore" python/pydocs_mcp/storage/protocols.py
```

Note exact method signatures.

- [ ] **Step 2: Write failing tests** — Create `tests/test_fakes.py`:

```python
"""Pin the FakeUnitOfWork + InMemory* contract."""
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
    """§14.9 AC #2."""
    assert isinstance(FakeUnitOfWork(), UnitOfWork)


@pytest.mark.asyncio
async def test_fake_uow_committed_only_on_explicit_commit():
    """§14.9 AC #6."""
    uow = FakeUnitOfWork()
    async with uow:
        assert uow.committed is False
        await uow.commit()
    assert uow.committed is True
    assert uow.rolled_back is False


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_when_commit_not_called():
    """§14.9 AC #6."""
    uow = FakeUnitOfWork()
    async with uow:
        pass
    assert uow.committed is False
    assert uow.rolled_back is True


@pytest.mark.asyncio
async def test_fake_uow_rolls_back_on_exception():
    """§14.9 AC #6."""
    uow = FakeUnitOfWork()
    with pytest.raises(ValueError):
        async with uow:
            raise ValueError("boom")
    assert uow.rolled_back is True


def test_fake_uow_attribute_outside_context_raises():
    """§14.9 AC #7."""
    uow = FakeUnitOfWork()
    with pytest.raises(UnitOfWorkNotEnteredError):
        _ = uow.packages


@pytest.mark.asyncio
async def test_inmemory_package_store_list_matches_protocol_signature():
    """§14.9 AC #5 — `list(filter, limit)` signature matches real PackageStore."""
    store = InMemoryPackageStore()
    result = await store.list(filter=None, limit=200)
    assert result == []
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/test_fakes.py -v
```

Expected: FAIL — imports don't exist.

- [ ] **Step 4: Implement** — Append to `tests/_fakes.py`:

```python
from contextlib import asynccontextmanager
from typing import Iterator

from pydocs_mcp.models import Chunk, ModuleMember, Package
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError


@dataclass
class InMemoryPackageStore:
    items: dict[str, Package] = field(default_factory=dict)

    async def get(self, name: str) -> Package | None:
        return self.items.get(name)

    async def upsert(self, package: Package) -> None:
        self.items[package.name] = package

    async def list(self, *, filter=None, limit: int | None = None) -> list[Package]:
        rows = list(self.items.values())
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, *, filter=None) -> int:
        before = len(self.items)
        if filter is None:
            self.items.clear()
        elif isinstance(filter, dict) and "name" in filter:
            self.items.pop(filter["name"], None)
        else:
            self.items.clear()
        return before - len(self.items)


@dataclass
class InMemoryChunkStore:
    by_package: dict[str, list[Chunk]] = field(default_factory=dict)

    async def upsert(self, chunks) -> None:
        for c in chunks:
            pkg = c.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(c)

    async def list(self, *, filter=None, limit: int | None = None) -> list[Chunk]:
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [c for cs in self.by_package.values() for c in cs]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, *, filter=None) -> int:
        count = sum(len(v) for v in self.by_package.values())
        if filter is None:
            self.by_package.clear()
        elif isinstance(filter, dict) and "package" in filter:
            self.by_package.pop(filter["package"], None)
        else:
            self.by_package.clear()
        return count - sum(len(v) for v in self.by_package.values())


@dataclass
class InMemoryModuleMemberStore:
    by_package: dict[str, list[ModuleMember]] = field(default_factory=dict)

    async def upsert_many(self, members) -> None:
        for m in members:
            pkg = m.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(m)

    async def list(self, *, filter=None, limit: int | None = None) -> list[ModuleMember]:
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [m for ms in self.by_package.values() for m in ms]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, *, filter=None) -> int:
        count = sum(len(v) for v in self.by_package.values())
        if filter is None:
            self.by_package.clear()
        elif isinstance(filter, dict) and "package" in filter:
            self.by_package.pop(filter["package"], None)
        else:
            self.by_package.clear()
        return count - sum(len(v) for v in self.by_package.values())


@dataclass
class FakeUnitOfWork:
    """Structurally satisfies UnitOfWork. Tracks committed/rolled_back."""
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

    @asynccontextmanager
    async def begin(self):
        async with self:
            try:
                yield
                await self.commit()
            except Exception:
                raise

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
    "FakeUnitOfWork",
    "InMemoryChunkStore",
    "InMemoryDocumentTreeStore",
    "InMemoryModuleMemberStore",
    "InMemoryPackageStore",
    "_Call",
)
```

- [ ] **Step 5: Run tests + full no-regression**

```bash
python -m pytest tests/test_fakes.py -v
python -m pytest -q
```

Expected: 6 new PASS; full suite 824+ passing.

- [ ] **Step 6: Commit**

```bash
git add tests/_fakes.py tests/test_fakes.py
git commit -m "test(#5a): FakeUnitOfWork + InMemory{Package,Chunk,ModuleMember}Store"
```

---

## Task 4: Final verification + push + PR

- [ ] **Step 1: Full gauntlet**

```bash
python -m pytest -q
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
python scripts/smoke_check_benchmark_imports.py
python -m pytest tests/retrieval/test_parity_golden.py -v
```

Expected: 824+ passing, ruff clean, cargo clean, parity-golden green.

- [ ] **Step 2: Per-AC verification**

- AC #1: full pytest pass — Step 1 ✓
- AC #2: `python -c "from pydocs_mcp.storage.protocols import UnitOfWork; print(UnitOfWork.__annotations__)"` shows the 4 repo attribute hints
- AC #3: `test_sqlite_uow_commit_persists_across_reopen` ✓ (proves ContextVar wired)
- AC #4: `test_sqlite_uow_legacy_begin_still_works` ✓ + existing 812 tests pass
- AC #5: `python -c "from tests._fakes import FakeUnitOfWork, InMemoryPackageStore, InMemoryChunkStore, InMemoryModuleMemberStore, InMemoryDocumentTreeStore; print('ok')"`
- AC #6: 3 commit/rollback flag tests ✓
- AC #7: 2 outside-context-raises tests (Sqlite + Fake) ✓
- AC #8: parity-golden ✓

- [ ] **Step 3: Push + open PR**

```bash
git push -u origin feature/sub-pr-5a-uow-refactor
gh pr create --base main --head feature/sub-pr-5a-uow-refactor \
    --title "sub-PR #5a: UoW Protocol widening + SqliteUnitOfWork rewrite (trimmed scope)" \
    --body "<paste from spec §14 + trimmed scope rationale>"
```

- [ ] **Step 4: Watch CI**

```bash
gh pr checks <NEW_PR_NUMBER> --watch
```

Expected: all 4 jobs PASS.

---

## Self-review

**1. Spec coverage:**

| §14 sub-section | Task |
|---|---|
| 14.1 problem | (context) |
| 14.2 target shape | 1, 2 |
| 14.3 implementation scope | 1, 2, 3 |
| 14.4 ContextVar mechanics | 2 Step 3 |
| 14.5 FakeUnitOfWork | 3 |
| 14.6 service migration REJECTED | (rationale only) |
| 14.7 #5b consumption | (forward-looking) |
| 14.8 risks | 2 Step 3 mitigations + Step 5 |
| 14.9 AC #1-8 | 4 Step 2 |
| 14.10 ship sequence | (out of scope) |

**2. Placeholder scan:** No "TBD" / "implement later" / "Similar to Task N". Every step has the actual code or command.

**3. Type consistency:** `UnitOfWork` Protocol attribute names (`packages`, `chunks`, `module_members`, `trees`) used identically across Tasks 1, 2, 3.

**4. Eng plan-review findings addressed:**
- ✅ ContextVar set/reset in `__aenter__`/`__aexit__` (Task 2 Step 3 — explicit `_sqlite_transaction.set(...)` + `.reset(...)`)
- ✅ `InMemory*Store.list(filter, limit)` matches real Protocol (Task 3 Step 4)
- ✅ `commit()`/`rollback()` use `self._held_conn` directly (Task 2 Step 3)
- ✅ Lock release in `finally` (Task 2 Step 3)
- ✅ `begin()` preserves prior contract — existing IndexingService tests unchanged
- ✅ ProjectIndexer reach-through irrelevant (no service migration)
- ✅ No `simple_package` fixture needed (no service migration test)

---

**Plan complete and saved to `docs/superpowers/plans/2026-05-17-sub-pr-5a-uow-refactor.md`.** Two execution options:

1. **Subagent-Driven (recommended)** — dispatch a fresh subagent per task with two-stage review.
2. **Inline Execution** — execute tasks in this session with checkpoints.
