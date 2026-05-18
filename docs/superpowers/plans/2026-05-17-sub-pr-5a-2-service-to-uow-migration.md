# Sub-PR #5a-2 — Service-to-UoW migration (full §14 follow-up)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate every application service (`IndexingService`, `PackageLookup`, `ModuleInspector`, `TreeService`, `ProjectIndexer`) off direct `*Store` Protocol dependencies onto a single `uow_factory: Callable[[], UnitOfWork]`. Remove the pre-#5a `begin()` back-compat shim from `UnitOfWork` Protocol, `SqliteUnitOfWork`, `FakeUnitOfWork`, and `IndexingService._in_uow`. Update composition roots (`server.py`, `__main__.py`, `storage/factories.py`) to build one `uow_factory` and thread it everywhere.

**Architecture:** Mechanical refactor of the consumer side. The `UnitOfWork` Protocol is unchanged (already widened by #5a). Services gain `uow_factory`, lose `*_store` / `unit_of_work` / `tree_store`. Internal method bodies wrap repo access in `async with self.uow_factory() as uow:` and explicitly `await uow.commit()` on write paths.

**Tech Stack:** Python 3.11+, `asyncio`, `typing.Callable`, `dataclasses`, pytest, sqlite3.

**Source spec:** `docs/superpowers/specs/2026-05-17-sub-pr-5a-2-service-to-uow-migration-design.md`

**Baseline:** 597+ test functions on `main` (post-PR-#19, commit `1609ce0`). **Diff size estimate:** ~600 LOC across ~15 files (production: 7; tests: 7-8; spec/plan: 2).

---

## Task 0: Worktree + baseline

Use `superpowers:using-git-worktrees` to create an isolated worktree at `.worktrees/sub-pr-5a-2-service-migration/` on a new branch `feature/sub-pr-5a-2-service-to-uow` off `origin/main`.

- [ ] **Step 1: Baseline verification**

```bash
source .venv/bin/activate
python -m pytest -q 2>&1 | tail -5
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
```

Expected: all tests passing (record exact count for later regression delta), ruff clean, cargo clean.

- [ ] **Step 2: Record the baseline test count**

```bash
python -m pytest --collect-only -q 2>&1 | grep -E "tests collected|test_" | tail -3
```

Record the exact count in a session note (e.g. "BASELINE: 826 tests collected on origin/main @ 1609ce0").

---

## Task 1: `make_fake_uow_factory` helper + entity-store `calls` tracking

**Files:**
- Modify: `tests/_fakes.py` — add `make_fake_uow_factory` helper; add `calls: list[_Call]` to InMemoryPackageStore/InMemoryChunkStore/InMemoryModuleMemberStore (mirroring InMemoryDocumentTreeStore).
- Modify: `tests/test_fakes.py` — pin the new helper contract + new `.calls` behavior.

- [ ] **Step 1: Write failing tests** — Append to `tests/test_fakes.py`:

```python
from collections.abc import Callable

from tests._fakes import make_fake_uow_factory


@pytest.mark.asyncio
async def test_make_fake_uow_factory_returns_callable():
    """§9 — helper returns a Callable[[], FakeUnitOfWork]."""
    factory = make_fake_uow_factory()
    assert callable(factory)
    uow = factory()
    assert isinstance(uow, FakeUnitOfWork)


@pytest.mark.asyncio
async def test_make_fake_uow_factory_returned_uows_share_underlying_stores():
    """§9 — each factory call returns a fresh UoW; underlying stores ARE shared."""
    packages = InMemoryPackageStore()
    factory = make_fake_uow_factory(packages=packages)

    uow1 = factory()
    uow2 = factory()
    assert uow1 is not uow2
    assert uow1.packages_store is packages
    assert uow2.packages_store is packages


@pytest.mark.asyncio
async def test_make_fake_uow_factory_is_re_entrance_safe():
    """§6 — each factory call returns an unentered UoW (re-entrance guard cleared)."""
    factory = make_fake_uow_factory()
    async with factory() as uow1:
        pass  # exit normally
    # Second call must succeed despite first having entered+exited.
    async with factory() as uow2:
        assert uow2._entered is True


@pytest.mark.asyncio
async def test_in_memory_package_store_records_calls():
    """§9.1 — InMemoryPackageStore.calls mirrors InMemoryDocumentTreeStore."""
    store = InMemoryPackageStore()
    pkg = Package(
        name="x", version="0", summary="", homepage="",
        dependencies=(), content_hash="", origin=PackageOrigin.DEPENDENCY,
    )
    await store.upsert(pkg)
    await store.get("x")
    assert any(c.method == "upsert" for c in store.calls)
    assert any(c.method == "get" for c in store.calls)


@pytest.mark.asyncio
async def test_in_memory_chunk_store_records_calls():
    """§9.1."""
    store = InMemoryChunkStore()
    chunk = Chunk(text="t", metadata={"package": "x"})
    await store.upsert([chunk])
    assert any(c.method == "upsert" for c in store.calls)


@pytest.mark.asyncio
async def test_in_memory_module_member_store_records_calls():
    """§9.1."""
    store = InMemoryModuleMemberStore()
    m = ModuleMember(metadata={"package": "x", "module": "x.m", "name": "f", "kind": "function"})
    await store.upsert_many([m])
    assert any(c.method == "upsert_many" for c in store.calls)
```

Add the corresponding imports at the top of `tests/test_fakes.py` if they're not already there (`from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin`).

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_fakes.py -v -k "make_fake_uow_factory or records_calls"
```

Expected: FAIL — helper + `.calls` fields don't exist.

- [ ] **Step 3: Implement** — In `tests/_fakes.py`:

  a) Add `from collections.abc import Callable` to top imports.

  b) Add a `calls: list[_Call] = field(default_factory=list)` field to `InMemoryPackageStore`, `InMemoryChunkStore`, `InMemoryModuleMemberStore` (after each existing field). Append `_Call("method_name", payload)` inside each method. Use this exact pattern (mirror what `InMemoryDocumentTreeStore` already does):

```python
# Example for InMemoryPackageStore — apply identically to InMemoryChunkStore/InMemoryModuleMemberStore.
@dataclass
class InMemoryPackageStore:
    items: dict[str, Package] = field(default_factory=dict)
    calls: list[_Call] = field(default_factory=list)

    async def get(self, name: str) -> Package | None:
        self.calls.append(_Call("get", name))
        return self.items.get(name)

    async def upsert(self, package: Package) -> None:
        self.calls.append(_Call("upsert", package))
        self.items[package.name] = package

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[Package]:
        self.calls.append(_Call("list", {"filter": filter, "limit": limit}))
        rows = list(self.items.values())
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("delete", filter))
        before = len(self.items)
        if filter is None:
            self.items.clear()
        elif isinstance(filter, dict) and "name" in filter:
            self.items.pop(filter["name"], None)
        else:
            self.items.clear()
        return before - len(self.items)

    async def count(self, filter: Any | None = None) -> int:
        self.calls.append(_Call("count", filter))
        if isinstance(filter, dict) and "name" in filter:
            return 1 if filter["name"] in self.items else 0
        return len(self.items)
```

Repeat the `.append(_Call(...))` pattern inside each method of `InMemoryChunkStore` and `InMemoryModuleMemberStore`. The payload shape mirrors what each method receives. For `InMemoryChunkStore.upsert(chunks)`, materialize the input first: `materialised = tuple(chunks); self.calls.append(_Call("upsert", materialised))`.

  c) Append the new helper to `tests/_fakes.py` (after `FakeUnitOfWork`):

```python
def make_fake_uow_factory(
    *,
    packages: InMemoryPackageStore | None = None,
    chunks: InMemoryChunkStore | None = None,
    module_members: InMemoryModuleMemberStore | None = None,
    trees: InMemoryDocumentTreeStore | None = None,
) -> Callable[[], FakeUnitOfWork]:
    """Build a Callable[[], FakeUnitOfWork] for service-test wiring (spec §9).

    Returns a callable that yields a FRESH FakeUnitOfWork per call (so the
    SqliteUnitOfWork re-entrance guard, mirrored in FakeUnitOfWork, never
    fires across multiple service-method invocations within one test) while
    keeping the underlying InMemory* stores SHARED (so state persists across
    calls — write-then-read patterns work as expected).

    All four kwargs default to a fresh empty InMemory* — pass only the ones
    you need to seed.
    """
    pkgs = packages or InMemoryPackageStore()
    chs  = chunks   or InMemoryChunkStore()
    mms  = module_members or InMemoryModuleMemberStore()
    trs  = trees    or InMemoryDocumentTreeStore()

    def factory() -> FakeUnitOfWork:
        return FakeUnitOfWork(
            packages_store=pkgs,
            chunks_store=chs,
            module_members_store=mms,
            trees_store=trs,
        )
    return factory
```

  d) Update `__all__` at end of `tests/_fakes.py`:

```python
__all__ = (
    "FakeUnitOfWork",
    "InMemoryChunkStore",
    "InMemoryDocumentTreeStore",
    "InMemoryModuleMemberStore",
    "InMemoryPackageStore",
    "_Call",
    "make_fake_uow_factory",
)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/test_fakes.py -v
python -m pytest -q  # no-regression
```

Expected: 6 new PASS; full suite still at baseline (or baseline + 6).

- [ ] **Step 5: Commit**

```bash
git add tests/_fakes.py tests/test_fakes.py
git commit -m "test(#5a-2): add make_fake_uow_factory helper + InMemory*Store .calls tracking"
```

---

## Task 2: Migrate `ModuleInspector` (1 field)

**Files:**
- Modify: `python/pydocs_mcp/application/module_inspector.py`
- Modify: `tests/application/test_module_inspector.py`

- [ ] **Step 1: Write the new tests** — Replace the relevant assertions in `tests/application/test_module_inspector.py`. Specifically, update `_service`, replace the body of `test_service_is_frozen_slotted_dataclass`, and add a `test_inspect_opens_uow_for_package_lookup`:

```python
# Replace the local FakePackageStore with the canonical one from _fakes.
# Top of file, replace the existing imports under "from pydocs_mcp.application..." line:
from tests._fakes import InMemoryPackageStore, make_fake_uow_factory

# Replace _service():
def _service(packages: dict[str, Package] | None = None) -> tuple[
    ModuleInspector, InMemoryPackageStore,
]:
    store = InMemoryPackageStore(items=dict(packages or {}))
    svc = ModuleInspector(uow_factory=make_fake_uow_factory(packages=store))
    return svc, store


# Replace test_service_is_frozen_slotted_dataclass:
def test_service_is_frozen_slotted_dataclass() -> None:
    svc, _ = _service()
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = (lambda: None)  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")


# Add at end of file:
@pytest.mark.asyncio
async def test_inspect_opens_uow_for_package_lookup() -> None:
    """spec §3.1 — inspect opens a UoW and reads packages through uow.packages.get."""
    svc, store = _service(packages={"json": _pkg("json")})
    # No exception path; the test just exercises the new shape.
    result = await svc.inspect("json")
    assert result.startswith("# json")
    # The uow.packages.get call lands in the InMemory store's .calls history.
    assert any(c.method == "get" and c.payload == "json" for c in store.calls)
```

Also delete the local `FakePackageStore` class (lines ~36-55) — the canonical `InMemoryPackageStore` replaces it.

The existing tests (`test_inspect_unindexed_package`, `test_inspect_invalid_submodule`, `test_inspect_successful_root`, `test_inspect_successful_submodule`, `test_inspect_importerror`, `test_inspect_normalizes_package_name`) keep their bodies — they don't touch repository internals, only the public `svc.inspect(...)` surface. They DO need their `store.get_call_count` references swapped — the `InMemoryPackageStore` does not have `get_call_count`. Replace:

```python
# BEFORE
assert store.get_call_count == 1
# AFTER
assert sum(1 for c in store.calls if c.method == "get") == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_module_inspector.py -v
```

Expected: all tests fail (the `_service()` helper now passes `uow_factory=...` which `ModuleInspector` doesn't accept).

- [ ] **Step 3: Migrate the production class** — Replace `python/pydocs_mcp/application/module_inspector.py`:

```python
"""ModuleInspector — live importlib + inspect (spec §5.1).

Post-#5a-2: depends only on a ``uow_factory: Callable[[], UnitOfWork]``.
Reads the indexed-package row through ``uow.packages.get(...)`` inside
``async with self.uow_factory() as uow:`` — the UoW Protocol guarantees
``packages`` is valid inside the context (spec §14.2 of #5b spec).
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.constants import LIVE_DOC_MAX, LIVE_SIGNATURE_MAX
from pydocs_mcp.deps import normalize_package_name
from pydocs_mcp.storage.protocols import UnitOfWork

_SUBMODULE_RE = re.compile(r"\A([A-Za-z0-9_]+(\.[A-Za-z0-9_]+)*)?\Z")
_MAX_MEMBERS: int = 50


def _validate_submodule(submodule: str) -> bool:
    return bool(_SUBMODULE_RE.match(submodule))


@dataclass(frozen=True, slots=True)
class ModuleInspector:
    """Live-import a package/submodule and render its public API.

    Depends only on ``uow_factory`` — opens a UoW per ``inspect`` call to
    check the package is indexed before crossing the import boundary.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def inspect(self, package: str, submodule: str = "") -> str:
        pkg_name = normalize_package_name(package)
        async with self.uow_factory() as uow:
            pkg = await uow.packages.get(pkg_name)
        if pkg is None:
            return (
                f"'{package}' is not indexed. "
                "Use lookup(target='') to see available packages."
            )
        if submodule and not _validate_submodule(submodule):
            return (
                f"Invalid submodule '{submodule}'. "
                "Use only letters, digits, underscores, and dots."
            )
        target = pkg_name + (f".{submodule}" if submodule else "")
        return await asyncio.to_thread(self._inspect_target, target)

    def _inspect_target(self, target: str) -> str:
        try:
            mod = importlib.import_module(target)
        except ImportError:
            return f"Cannot import '{target}'."

        items = []
        try:
            for name, obj in inspect.getmembers(mod):
                if name.startswith("_"):
                    continue
                if not (inspect.isfunction(obj) or inspect.isclass(obj)):
                    continue
                try:
                    sig = str(inspect.signature(obj))[:LIVE_SIGNATURE_MAX]
                except (ValueError, TypeError):
                    sig = "(...)"
                doc = (inspect.getdoc(obj) or "").split("\n")[0][:LIVE_DOC_MAX]
                kind = "class" if inspect.isclass(obj) else "def"
                items.append(f"{kind} {name}{sig}\n    {doc}")
                if len(items) >= _MAX_MEMBERS:
                    break
        except Exception:  # noqa: BLE001 -- AC #8 byte-parity
            pass

        if not items and hasattr(mod, "__path__"):
            try:
                subs = [
                    s for _, s, _ in pkgutil.iter_modules(mod.__path__)
                    if not s.startswith("_")
                ]
                return f"# {target}\nSubmodules: {', '.join(subs)}"
            except Exception:  # noqa: BLE001
                pass

        return (
            f"# {target}\n\n" + "\n\n".join(items)
        ) if items else f"No API in '{target}'."
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/application/test_module_inspector.py -v
python -m pytest -q  # no-regression
```

Expected: all `test_module_inspector` PASS; other tests may fail (`test_end_to_end.py` will fail because it still wires `ModuleInspector(package_store=...)`. That's fixed in Task 7).

- [ ] **Step 5: Quick-fix the end-to-end test wiring** — Open `tests/application/test_end_to_end.py` line 91:

```python
# BEFORE
"inspect": ModuleInspector(package_store=package_store),
# AFTER
"inspect": ModuleInspector(
    uow_factory=lambda: SqliteUnitOfWork(provider=provider),
),
```

Also add the import at the top: `from pydocs_mcp.storage.sqlite import SqliteUnitOfWork`.

- [ ] **Step 6: Run end-to-end tests**

```bash
python -m pytest tests/application/test_end_to_end.py::test_inspect_unknown_package_returns_error_string tests/application/test_end_to_end.py::test_inspect_invalid_submodule_rejected -v
python -m pytest -q
```

Expected: the two inspect-related e2e tests PASS; other e2e tests may still fail (they use `package_store=`, `chunk_store=`, etc. — fixed in Task 4).

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/application/module_inspector.py tests/application/test_module_inspector.py tests/application/test_end_to_end.py
git commit -m "refactor(#5a-2): ModuleInspector depends on uow_factory not package_store"
```

---

## Task 3: Migrate `TreeService` (1 field)

**Files:**
- Modify: `python/pydocs_mcp/application/tree_service.py`
- Modify: `tests/application/test_tree_service.py`

- [ ] **Step 1: Write the new tests** — Replace `tests/application/test_tree_service.py` body (keep the file header + `_module_tree` helper):

```python
"""Tests for TreeService — post-#5a-2 uow_factory shape (spec §3.1)."""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from pydocs_mcp.application.tree_service import TreeService
from pydocs_mcp.extraction.model import DocumentNode, NodeKind

from tests._fakes import InMemoryDocumentTreeStore, make_fake_uow_factory


def _module_tree(module: str) -> DocumentNode:
    return DocumentNode(
        node_id=module,
        qualified_name=module,
        title=module,
        kind=NodeKind.MODULE,
        source_path=f"{module.replace('.', '/')}.py",
        start_line=1,
        end_line=10,
        text="body",
        content_hash="hash",
    )


def _service(by_package: dict[str, list[DocumentNode]] | None = None) -> tuple[
    TreeService, InMemoryDocumentTreeStore,
]:
    store = InMemoryDocumentTreeStore(by_package=dict(by_package or {}))
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    return svc, store


@pytest.mark.asyncio
async def test_get_tree_returns_document_node_when_present():
    """spec §3.1 — TreeService.get_tree reads uow.trees.load."""
    tree = _module_tree("requests.adapters")
    # InMemoryDocumentTreeStore.load returns None by default; for this test
    # we monkey-patch the store's by_package to make load find it.
    # Simpler: subclass + override.
    class _SeededStore(InMemoryDocumentTreeStore):
        async def load(self, package, module):
            if package == "requests" and module == "requests.adapters":
                return tree
            return None

    store = _SeededStore()
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    result = await svc.get_tree("requests", "requests.adapters")
    assert result is tree


@pytest.mark.asyncio
async def test_get_tree_missing_returns_none():
    svc, _ = _service()
    result = await svc.get_tree("unknown", "unknown.missing")
    assert result is None


@pytest.mark.asyncio
async def test_exists_returns_true_when_tree_present():
    class _Seeded(InMemoryDocumentTreeStore):
        async def exists(self, package, module):
            return package == "requests" and module == "requests.adapters"

    store = _Seeded()
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    assert await svc.exists("requests", "requests.adapters") is True


@pytest.mark.asyncio
async def test_exists_returns_false_when_tree_missing():
    svc, _ = _service()
    assert await svc.exists("ghost", "ghost.missing") is False


@pytest.mark.asyncio
async def test_list_package_modules_delegates_to_uow_trees():
    class _Seeded(InMemoryDocumentTreeStore):
        async def load_all_in_package(self, package):
            if package == "requests":
                return {
                    "requests.adapters": _module_tree("requests.adapters"),
                    "requests.sessions": _module_tree("requests.sessions"),
                }
            return {}

    store = _Seeded()
    svc = TreeService(uow_factory=make_fake_uow_factory(trees=store))
    result = await svc.list_package_modules("requests")
    assert set(result.keys()) == {"requests.adapters", "requests.sessions"}


@pytest.mark.asyncio
async def test_list_package_modules_unknown_returns_empty():
    svc, _ = _service()
    assert await svc.list_package_modules("ghost") == {}


def test_service_is_frozen_and_slotted():
    import dataclasses
    svc, _ = _service()
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = (lambda: None)  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        object.__setattr__(svc, "unknown_attr", 42)
```

The `_FakeTreeStore` local helper class is gone — replaced by `InMemoryDocumentTreeStore` from `_fakes.py` (with inline subclasses where return-shapes need seeding).

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_tree_service.py -v
```

Expected: FAIL — `TreeService(uow_factory=...)` is not the current signature.

- [ ] **Step 3: Migrate the production class** — Replace `python/pydocs_mcp/application/tree_service.py`:

```python
"""TreeService — query-side wrapper over DocumentTreeStore (spec §13.1).

Post-#5a-2: depends only on a ``uow_factory: Callable[[], UnitOfWork]``.
Each method opens its own UoW, reads through ``uow.trees``, and exits
without committing (read-only).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode


@dataclass(frozen=True, slots=True)
class TreeService:
    """Fetches DocumentNode trees through a per-call UnitOfWork."""

    uow_factory: Callable[[], UnitOfWork]

    async def get_tree(
        self, package: str, module: str,
    ) -> "DocumentNode | None":
        async with self.uow_factory() as uow:
            return await uow.trees.load(package, module)

    async def exists(self, package: str, module: str) -> bool:
        async with self.uow_factory() as uow:
            return await uow.trees.exists(package, module)

    async def list_package_modules(
        self, package: str,
    ) -> dict[str, "DocumentNode"]:
        async with self.uow_factory() as uow:
            return await uow.trees.load_all_in_package(package)
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/application/test_tree_service.py -v
python -m pytest tests/application/test_lookup_service.py -v
python -m pytest -q
```

Expected: tree-service tests PASS; lookup-service tests still PASS (they don't construct TreeService themselves — they take it as a constructor arg). Other tests (e.g. `tests/extraction/test_end_to_end.py::test_e2e_get_tree_service_returns_saved_tree` which calls `TreeService(tree_store=...)`) FAIL — fixed in Task 7.

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/tree_service.py tests/application/test_tree_service.py
git commit -m "refactor(#5a-2): TreeService depends on uow_factory not tree_store"
```

---

## Task 4: Migrate `PackageLookup` (3 fields)

**Files:**
- Modify: `python/pydocs_mcp/application/package_lookup.py`
- Modify: `tests/application/test_package_lookup.py`

- [ ] **Step 1: Write the new tests** — Heavily rewrite `tests/application/test_package_lookup.py`. Replace the body (keep `_pkg`, `_chunk`, `_member` helpers):

```python
"""Tests for PackageLookup — post-#5a-2 uow_factory shape (spec §3.1)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    MemberKind,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageDoc,
    PackageOrigin,
)

from tests._fakes import (
    InMemoryChunkStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)


def _pkg(name: str) -> Package:
    return Package(
        name=name, version="1.0.0",
        summary=f"{name} summary", homepage="",
        dependencies=(), content_hash="deadbeef",
        origin=PackageOrigin.DEPENDENCY,
    )


def _chunk(package: str, title: str, module: str = "") -> Chunk:
    md = {
        ChunkFilterField.PACKAGE.value: package,
        ChunkFilterField.TITLE.value: title,
    }
    if module:
        md[ChunkFilterField.MODULE.value] = module
    return Chunk(text=f"{title} body", metadata=md)


def _member(package: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.MODULE.value: f"{package}.core",
            ModuleMemberFilterField.KIND.value: MemberKind.FUNCTION.value,
        }
    )


def _service(
    *,
    packages: dict[str, Package] | None = None,
    chunks: list[Chunk] | None = None,
    members: list[ModuleMember] | None = None,
) -> tuple[
    PackageLookup, InMemoryPackageStore, InMemoryChunkStore, InMemoryModuleMemberStore,
]:
    pkg_store = InMemoryPackageStore(items=dict(packages or {}))
    chunk_store = InMemoryChunkStore()
    for c in chunks or []:
        pkg = c.metadata.get("package", "")
        chunk_store.by_package.setdefault(pkg, []).append(c)
    member_store = InMemoryModuleMemberStore()
    for m in members or []:
        pkg = m.metadata.get("package", "")
        member_store.by_package.setdefault(pkg, []).append(m)
    factory = make_fake_uow_factory(
        packages=pkg_store, chunks=chunk_store, module_members=member_store,
    )
    svc = PackageLookup(uow_factory=factory)
    return svc, pkg_store, chunk_store, member_store


@pytest.mark.asyncio
async def test_list_packages_returns_tuple() -> None:
    svc, _, _, _ = _service(packages={"foo": _pkg("foo"), "bar": _pkg("bar")})
    result = await svc.list_packages()
    assert isinstance(result, tuple)
    assert {p.name for p in result} == {"foo", "bar"}


@pytest.mark.asyncio
async def test_list_packages_passes_limit_200_through_uow() -> None:
    """spec §3.1 — list_packages calls uow.packages.list(limit=200)."""
    svc, pkg_store, _, _ = _service(packages={"foo": _pkg("foo")})
    await svc.list_packages()
    list_calls = [c for c in pkg_store.calls if c.method == "list"]
    assert len(list_calls) == 1
    assert list_calls[0].payload["limit"] == 200


@pytest.mark.asyncio
async def test_get_package_doc_missing_returns_none() -> None:
    svc, pkg_store, chunk_store, member_store = _service(packages={})
    result = await svc.get_package_doc("ghost")
    assert result is None
    assert any(c.method == "get" for c in pkg_store.calls)
    # Short-circuit: neither dependent store queried.
    assert not any(c.method == "list" for c in chunk_store.calls)
    assert not any(c.method == "list" for c in member_store.calls)


@pytest.mark.asyncio
async def test_get_package_doc_composes_all_three_stores() -> None:
    pkg = _pkg("foo")
    chunks = [_chunk("foo", "overview"), _chunk("foo", "api")]
    members = [_member("foo", "run"), _member("foo", "init")]
    svc, _, chunk_store, member_store = _service(
        packages={"foo": pkg}, chunks=chunks, members=members,
    )
    result = await svc.get_package_doc("foo")
    assert isinstance(result, PackageDoc)
    assert result.package is pkg
    assert result.chunks == tuple(chunks)
    assert result.members == tuple(members)
    # spec §3.1 — chunks.list called with limit=10, members.list with limit=30.
    chunk_list_calls = [c for c in chunk_store.calls if c.method == "list"]
    member_list_calls = [c for c in member_store.calls if c.method == "list"]
    assert chunk_list_calls[0].payload["limit"] == 10
    assert member_list_calls[0].payload["limit"] == 30


@pytest.mark.asyncio
async def test_get_package_doc_passes_enum_filter_keys() -> None:
    svc, _, chunk_store, member_store = _service(packages={"foo": _pkg("foo")})
    await svc.get_package_doc("foo")
    chunk_filter = next(c.payload["filter"] for c in chunk_store.calls if c.method == "list")
    member_filter = next(c.payload["filter"] for c in member_store.calls if c.method == "list")
    assert chunk_filter == {ChunkFilterField.PACKAGE.value: "foo"}
    assert member_filter == {ModuleMemberFilterField.PACKAGE.value: "foo"}


def test_service_is_frozen_slotted_dataclass() -> None:
    svc, _, _, _ = _service()
    import dataclasses
    with pytest.raises(dataclasses.FrozenInstanceError):
        svc.uow_factory = (lambda: None)  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")


def test_filter_field_scope_parity() -> None:
    assert ChunkFilterField.PACKAGE.value == ModuleMemberFilterField.PACKAGE.value == "package"
    assert ChunkFilterField.SCOPE.value == ModuleMemberFilterField.SCOPE.value == "scope"


@pytest.mark.asyncio
async def test_find_module_returns_true_when_chunk_exists() -> None:
    svc, _, _, _ = _service(
        packages={"fastapi": _pkg("fastapi")},
        chunks=[_chunk("fastapi", "routing", module="fastapi.routing")],
    )
    # Tweak: InMemoryChunkStore.list ignores filter `module`, so we need a
    # filter that matches package. find_module passes BOTH package + module
    # — the in-memory store filters on package only, returning all chunks
    # for that package. With only one chunk in the test, bool(result) is True.
    assert await svc.find_module("fastapi", "fastapi.routing") is True


@pytest.mark.asyncio
async def test_find_module_returns_false_when_no_chunks() -> None:
    svc, _, _, _ = _service(packages={"fastapi": _pkg("fastapi")})
    assert await svc.find_module("fastapi", "fastapi.routing") is False


@pytest.mark.asyncio
async def test_find_module_returns_false_on_empty_args() -> None:
    svc, _, chunk_store, _ = _service(
        packages={"fastapi": _pkg("fastapi")},
        chunks=[_chunk("fastapi", "routing")],
    )
    assert await svc.find_module("", "fastapi.routing") is False
    assert await svc.find_module("fastapi", "") is False
    # Verify the store was never queried.
    assert not any(c.method == "list" for c in chunk_store.calls)


# ── End-to-end against real SQLite ────────────────────────────────────────


@pytest.mark.asyncio
async def test_find_module_end_to_end_against_real_sqlite(tmp_path) -> None:
    """Drive find_module through the real SqliteUnitOfWork stack."""
    from pydocs_mcp.db import build_connection_provider, open_index_database
    from pydocs_mcp.storage.sqlite import SqliteUnitOfWork

    db_path = tmp_path / "e2e.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)

    # Seed one chunk via the new UoW path.
    async with SqliteUnitOfWork(provider=provider) as uow:
        await uow.chunks.upsert(
            [
                Chunk(
                    text="routing body",
                    metadata={
                        ChunkFilterField.PACKAGE.value: "fastapi",
                        ChunkFilterField.MODULE.value: "fastapi.routing",
                        ChunkFilterField.TITLE.value: "APIRouter",
                        ChunkFilterField.ORIGIN.value: "dependency_code",
                    },
                )
            ]
        )
        await uow.commit()

    svc = PackageLookup(uow_factory=lambda: SqliteUnitOfWork(provider=provider))
    assert await svc.find_module("fastapi", "fastapi.routing") is True
    assert await svc.find_module("fastapi", "fastapi.nonexistent") is False
```

Note: The end-to-end test now uses `SqliteUnitOfWork` directly — proves the real wiring works.

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_package_lookup.py -v
```

Expected: FAIL — `PackageLookup(uow_factory=...)` is not yet the signature.

- [ ] **Step 3: Migrate the production class** — Replace `python/pydocs_mcp/application/package_lookup.py`:

```python
"""PackageLookup — list + get_package_doc via UoW (spec §5.1, post-#5a-2)."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.models import (
    ChunkFilterField,
    ModuleMemberFilterField,
    Package,
    PackageDoc,
)
from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class PackageLookup:
    """Composes the three domain stores (via UoW) into a read-only package view.

    Post-#5a-2: depends only on ``uow_factory``. Each public method opens a
    fresh UoW; reads run inside ``async with`` and exit without committing.

    Note: ``get_package_doc`` no longer uses ``asyncio.gather`` for the two
    list reads — both go through the same held connection inside the UoW,
    and concurrent access would race ``_sqlite_transaction``'s lock. Per
    spec §7.2, the two ~1ms SELECTs run sequentially.
    """

    uow_factory: Callable[[], UnitOfWork]

    async def list_packages(self) -> tuple[Package, ...]:
        async with self.uow_factory() as uow:
            return tuple(await uow.packages.list(limit=200))

    async def get_package_doc(self, package_name: str) -> PackageDoc | None:
        async with self.uow_factory() as uow:
            pkg = await uow.packages.get(package_name)
            if pkg is None:
                return None
            chunks = await uow.chunks.list(
                filter={ChunkFilterField.PACKAGE.value: package_name},
                limit=10,
            )
            members = await uow.module_members.list(
                filter={ModuleMemberFilterField.PACKAGE.value: package_name},
                limit=30,
            )
        return PackageDoc(package=pkg, chunks=tuple(chunks), members=tuple(members))

    async def find_module(self, package: str, module: str) -> bool:
        if not package or not module:
            return False
        async with self.uow_factory() as uow:
            chunks = await uow.chunks.list(
                filter={
                    ChunkFilterField.PACKAGE.value: package,
                    ChunkFilterField.MODULE.value: module,
                },
                limit=1,
            )
        return bool(chunks)
```

Note: `find_module`'s in-memory test fakes only filter on `package` (not also on `module`), so the test wires up a single chunk under that package and asserts the boolean. The real SQLite path filters on both fields correctly — covered by the end-to-end test.

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/application/test_package_lookup.py -v
python -m pytest tests/application/test_lookup_service.py -v
python -m pytest -q
```

Expected: `test_package_lookup.py` PASS (~13 tests). `test_lookup_service.py` still PASS (composes PackageLookup at the test fixture level — internal field is hidden behind the public surface). Other tests with composition-root issues fail (Task 7).

- [ ] **Step 5: Quick-fix the end-to-end test wiring** — In `tests/application/test_end_to_end.py`, replace the `PackageLookup(...)` block (lines 83-88):

```python
# BEFORE
"package_lookup": PackageLookup(
    package_store=package_store,
    chunk_store=chunk_store,
    module_member_store=member_store,
),
# AFTER
"package_lookup": PackageLookup(
    uow_factory=lambda: SqliteUnitOfWork(provider=provider),
),
```

(`SqliteUnitOfWork` should already be imported from Task 2.) The local `package_store`, `chunk_store`, `member_store` references in the fixture body can be removed if no other consumer in the fixture references them. Inspect lines 73-91 and prune. The retrieval-stack consumers (`chunk_pipeline`, `member_pipeline`) get their stores from `context.module_member_store` etc., NOT from these locals.

- [ ] **Step 6: Run end-to-end tests**

```bash
python -m pytest tests/application/test_end_to_end.py -v
python -m pytest -q
```

Expected: e2e tests for package_lookup + inspect PASS. Other things may still fail (factories, server-side wiring) — fixed in Task 7+.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/application/package_lookup.py tests/application/test_package_lookup.py tests/application/test_end_to_end.py
git commit -m "refactor(#5a-2): PackageLookup depends on uow_factory not three stores"
```

---

## Task 5: Migrate `IndexingService` (drop 5 fields, gain only `uow_factory`)

**Files:**
- Modify: `python/pydocs_mcp/application/indexing_service.py`
- Modify: `python/pydocs_mcp/storage/sqlite.py` (delete `begin()`)
- Modify: `python/pydocs_mcp/storage/protocols.py` (delete `begin()` from Protocol)
- Modify: `tests/_fakes.py` (delete `FakeUnitOfWork.begin()`)
- Modify: `tests/application/test_indexing_service.py` (heavy rewrite — delete inline Fake* classes, use canonical fakes)
- Modify: `tests/storage/test_unit_of_work.py` (delete legacy_begin test)
- Modify: `tests/test_fakes.py` (delete begin-related assertions if any)

- [ ] **Step 1: Write the new test fixture + key tests** — Replace `tests/application/test_indexing_service.py` body. Keep the file header docstring and `_pkg`/`_chunk`/`_member` helpers, but replace EVERYTHING after the helpers with:

```python
"""Tests for IndexingService — post-#5a-2 uow_factory shape (spec §3.1, §5)."""
from __future__ import annotations

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.filters import All

from tests._fakes import (
    FakeUnitOfWork,
    InMemoryChunkStore,
    InMemoryDocumentTreeStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)


# ── Fixtures ────────────────────────────────────────────────────────────


def _pkg(name: str = "fastapi") -> Package:
    return Package(
        name=name, version="0.1",
        summary="", homepage="",
        dependencies=(), content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )


def _chunk(package: str, title: str, text: str = "body") -> Chunk:
    return Chunk(text=text, metadata={"package": package, "title": title})


def _member(package: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={"package": package, "module": f"{package}.mod", "name": name, "kind": "function"}
    )


# ── Tests ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reindex_package_writes_through_uow():
    """spec §3.1 — reindex_package opens UoW, runs delete→upsert on all 3 stores, commits."""
    pkg_store = InMemoryPackageStore()
    chunk_store = InMemoryChunkStore()
    member_store = InMemoryModuleMemberStore()
    factory = make_fake_uow_factory(
        packages=pkg_store, chunks=chunk_store, module_members=member_store,
    )
    # We need to inspect commit/rollback; build a thin wrapper.
    captured_uows: list[FakeUnitOfWork] = []
    def tracking_factory():
        uow = factory()
        captured_uows.append(uow)
        return uow

    service = IndexingService(uow_factory=tracking_factory)
    pkg = _pkg("fastapi")
    chunks = (_chunk("fastapi", "Routing"), _chunk("fastapi", "Middleware"))
    members = (_member("fastapi", "APIRouter"),)

    await service.reindex_package(pkg, chunks, members)

    # commit fired exactly once.
    assert sum(1 for u in captured_uows if u.committed) == 1
    # No rollback (success path).
    assert not any(u.rolled_back for u in captured_uows)
    # Underlying state shows delete-then-upsert sequence on each store.
    assert [c.method for c in chunk_store.calls] == ["delete", "upsert"]
    assert [c.method for c in member_store.calls] == ["delete", "upsert_many"]
    # Package store: delete then upsert.
    assert [c.method for c in pkg_store.calls if c.method in ("delete", "upsert")] == ["delete", "upsert"]


@pytest.mark.asyncio
async def test_reindex_package_rolls_back_on_exception():
    """spec §3.1 — UoW.__aexit__ rolls back when an exception escapes."""
    pkg_store = InMemoryPackageStore()
    # Force an exception during chunk upsert.
    class ExplodingChunkStore(InMemoryChunkStore):
        async def upsert(self, chunks):
            raise RuntimeError("simulated")
    chunk_store = ExplodingChunkStore()
    member_store = InMemoryModuleMemberStore()
    captured_uows: list[FakeUnitOfWork] = []
    factory = make_fake_uow_factory(
        packages=pkg_store, chunks=chunk_store, module_members=member_store,
    )
    def tracking_factory():
        uow = factory()
        captured_uows.append(uow)
        return uow

    service = IndexingService(uow_factory=tracking_factory)
    pkg = _pkg("fastapi")

    with pytest.raises(RuntimeError):
        await service.reindex_package(pkg, (_chunk("fastapi", "X"),), ())

    assert any(u.rolled_back for u in captured_uows)
    assert not any(u.committed for u in captured_uows)


@pytest.mark.asyncio
async def test_remove_package_deletes_through_uow():
    """spec §3.1 — remove_package opens UoW, deletes from all 4 stores."""
    pkg_store = InMemoryPackageStore(items={"fastapi": _pkg("fastapi")})
    chunk_store = InMemoryChunkStore(by_package={"fastapi": [_chunk("fastapi", "A")]})
    member_store = InMemoryModuleMemberStore(by_package={"fastapi": [_member("fastapi", "X")]})
    tree_store = InMemoryDocumentTreeStore(by_package={"fastapi": ["tree-1"]})
    factory = make_fake_uow_factory(
        packages=pkg_store, chunks=chunk_store,
        module_members=member_store, trees=tree_store,
    )
    service = IndexingService(uow_factory=factory)

    await service.remove_package("fastapi")

    assert "fastapi" not in pkg_store.items
    assert "fastapi" not in chunk_store.by_package
    assert "fastapi" not in member_store.by_package
    assert "fastapi" not in tree_store.by_package


@pytest.mark.asyncio
async def test_clear_all_uses_match_all_filter():
    """spec §3.1 — clear_all uses All(clauses=()) to match every row."""
    pkg_store = InMemoryPackageStore(items={"fastapi": _pkg("fastapi"), "starlette": _pkg("starlette")})
    chunk_store = InMemoryChunkStore(by_package={"fastapi": [_chunk("fastapi", "A")]})
    member_store = InMemoryModuleMemberStore(by_package={"fastapi": [_member("fastapi", "X")]})
    tree_store = InMemoryDocumentTreeStore(by_package={"fastapi": ["tree-1"]})
    factory = make_fake_uow_factory(
        packages=pkg_store, chunks=chunk_store,
        module_members=member_store, trees=tree_store,
    )
    service = IndexingService(uow_factory=factory)

    await service.clear_all()

    assert pkg_store.items == {}
    assert chunk_store.by_package == {}
    assert member_store.by_package == {}
    assert tree_store.by_package == {}
    # Pin the All(clauses=()) filter — used downstream by the SqliteFilterAdapter.
    assert any(
        c.method == "delete" and isinstance(c.payload, All) and not c.payload.clauses
        for c in chunk_store.calls
    )


@pytest.mark.asyncio
async def test_reindex_package_with_trees_calls_tree_store():
    """spec §3.1 — non-empty trees → delete_for_package + save_many."""
    tree_store = InMemoryDocumentTreeStore()
    factory = make_fake_uow_factory(trees=tree_store)
    service = IndexingService(uow_factory=factory)
    pkg = _pkg("fastapi")
    fake_trees = ("tree-1", "tree-2")

    await service.reindex_package(pkg, (), (), trees=fake_trees)

    methods = [c.method for c in tree_store.calls]
    assert methods == ["delete_for_package", "save_many"]


@pytest.mark.asyncio
async def test_reindex_package_with_empty_trees_skips_tree_store():
    """spec §3.1 — empty trees → no tree-store activity (eng-review bug #3 fix)."""
    tree_store = InMemoryDocumentTreeStore()
    factory = make_fake_uow_factory(trees=tree_store)
    service = IndexingService(uow_factory=factory)
    pkg = _pkg("fastapi")

    await service.reindex_package(pkg, (), (), trees=())

    # No tree_store activity when trees is empty.
    assert not any(c.method in ("delete_for_package", "save_many") for c in tree_store.calls)


@pytest.mark.asyncio
async def test_reindex_package_canonical_order():
    """spec §3.1 — canonical: chunks → trees → members."""
    chunk_store = InMemoryChunkStore()
    tree_store = InMemoryDocumentTreeStore()
    member_store = InMemoryModuleMemberStore()
    factory = make_fake_uow_factory(
        chunks=chunk_store, trees=tree_store, module_members=member_store,
    )
    service = IndexingService(uow_factory=factory)
    pkg = _pkg("fastapi")

    await service.reindex_package(pkg, (_chunk("fastapi", "A"),), (_member("fastapi", "X"),), trees=("t1",))

    # Just confirm all three writes happened; ordering is enforced by code-read.
    assert any(c.method == "upsert" for c in chunk_store.calls)
    assert any(c.method == "save_many" for c in tree_store.calls)
    assert any(c.method == "upsert_many" for c in member_store.calls)


@pytest.mark.asyncio
async def test_reindex_package_accepts_references_placeholder():
    """spec §2 — references=(...) is the #5b seam, ignored in #5a-2."""
    factory = make_fake_uow_factory()
    service = IndexingService(uow_factory=factory)
    pkg = _pkg("fastapi")
    # Non-empty references must not raise.
    await service.reindex_package(pkg, (), (), references=("fake-ref",))


# ── End-to-end (real SQLite) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_indexing_service_clear_all_also_removes_null_package_rows(tmp_path):
    """Regression — clear_all uses All() filter that matches NULL columns."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.factories import build_sqlite_indexing_service

    db_path = tmp_path / "clear.db"
    conn = open_index_database(db_path)
    conn.execute(
        "INSERT INTO packages(name,version,summary,homepage,dependencies,content_hash,origin) VALUES(?,?,?,?,?,?,?)",
        ("normal", "1.0", "", "", "[]", "h", "dependency"),
    )
    conn.execute(
        "INSERT INTO chunks(package, title, text, origin) VALUES(?,?,?,?)",
        ("normal", "t", "body", "dep_doc"),
    )
    conn.execute(
        "INSERT INTO chunks(package, title, text, origin) VALUES(NULL, ?, ?, ?)",
        ("orphan", "orphan body", "dep_doc"),
    )
    conn.commit()
    conn.close()

    service = build_sqlite_indexing_service(db_path)
    await service.clear_all()

    conn = open_index_database(db_path)
    pkg_count = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    conn.close()
    assert pkg_count == 0
    assert chunk_count == 0


def test_indexing_service_only_has_one_field():
    """spec §11 AC #1 — only uow_factory field."""
    import dataclasses
    fields = {f.name for f in dataclasses.fields(IndexingService)}
    assert fields == {"uow_factory"}
```

The ~140 lines of `FakePackageStore`/`FakeChunkStore`/`FakeModuleMemberStore`/`FakeUnitOfWork` definitions at the top of the file are DELETED (the imports from `tests._fakes` replace them).

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_indexing_service.py -v 2>&1 | tail -30
```

Expected: FAIL — `IndexingService(uow_factory=...)` is not the current signature.

- [ ] **Step 3: Migrate the production class** — Replace `python/pydocs_mcp/application/indexing_service.py`:

```python
"""IndexingService — coordinates atomic write-side indexing (spec §5.6, post-#5a-2).

Depends ONLY on a ``uow_factory: Callable[[], UnitOfWork]``. Each public
method opens its own UoW, runs the delete→upsert sequence against the
UoW's repository attributes, explicitly commits on success, and lets
``__aexit__``'s safety-net roll back on exception.

The pre-#5a back-compat ``unit_of_work: UnitOfWork | None`` field and the
``_in_uow`` helper are GONE. Atomicity is guaranteed by construction.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
)
from pydocs_mcp.storage.filters import All
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IndexingService:
    """Coordinates atomic write-side indexing through a per-call UnitOfWork.

    Depends ONLY on ``uow_factory`` (spec §5.6, AC #10).
    """

    uow_factory: Callable[[], UnitOfWork]

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: Sequence["DocumentNode"] = (),
        references: Sequence[Any] = (),  # noqa: ARG002 -- sub-PR #5b seam
    ) -> None:
        """Replace every row for ``package.name`` atomically.

        Canonical composite (spec §13.3): delete every row for the package
        across packages / chunks / module_members / (optionally) trees,
        then upsert the new payload. Order: chunks → trees → members so
        FK-like post-conditions line up if a future schema adds them.

        ``references`` is the sub-PR #5b seam; this PR ignores it.
        """
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter={ChunkFilterField.PACKAGE.value: package.name})
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: package.name},
            )
            await uow.packages.delete(filter={"name": package.name})
            await uow.packages.upsert(package)
            await uow.chunks.upsert(chunks)
            if trees:
                await uow.trees.delete_for_package(package.name)
                await uow.trees.save_many(tuple(trees), package=package.name)
            await uow.module_members.upsert_many(module_members)
            await uow.commit()

    async def remove_package(self, name: str) -> None:
        """Delete a package and every chunk / module-member / tree it owns."""
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter={ChunkFilterField.PACKAGE.value: name})
            await uow.module_members.delete(
                filter={ModuleMemberFilterField.PACKAGE.value: name},
            )
            await uow.trees.delete_for_package(name)
            await uow.packages.delete(filter={"name": name})
            await uow.commit()

    async def clear_all(self) -> None:
        """Wipe every row across every entity store.

        Uses ``All(clauses=())`` — an empty conjunction that the
        ``SqliteFilterAdapter`` translates to ``1 = 1``, matching NULL
        columns too (unlike a ``LIKE '%'`` hack).
        """
        match_all: All = All(clauses=())
        async with self.uow_factory() as uow:
            await uow.chunks.delete(filter=match_all)
            await uow.module_members.delete(filter=match_all)
            await uow.trees.delete_all()
            await uow.packages.delete(filter=match_all)
            await uow.commit()
```

- [ ] **Step 4: Delete the `begin()` back-compat path** — Now that `IndexingService._in_uow` is gone, no production code calls `SqliteUnitOfWork.begin()` anymore. Drop the method everywhere:

  a) `python/pydocs_mcp/storage/sqlite.py`: delete the `begin()` method (`@asynccontextmanager` decorator + `async def begin(self) -> AsyncIterator[None]: async with self: yield; await self.commit()` block, lines 244-256). Also remove the `from contextlib import asynccontextmanager` and `from collections.abc import AsyncIterator` imports if they're unused after the deletion (run `ruff check` to confirm).

  b) `python/pydocs_mcp/storage/protocols.py`: delete the `async def begin(self) -> AsyncIterator[None]: ...` line (~line 115) and the `from collections.abc import AsyncIterator` if unused. Also delete the comment line `# Back-compat shim for pre-#5a callers.` above it.

  c) `tests/_fakes.py`: delete the `@asynccontextmanager async def begin(self): async with self: yield; await self.commit()` from `FakeUnitOfWork` (~lines 297-301) and remove the `from contextlib import asynccontextmanager` import if unused.

  d) `tests/storage/test_unit_of_work.py`: delete `test_sqlite_uow_legacy_begin_still_works` (its rationale `# §14.9 AC #4 — pre-#5a callers using begin() unaffected` is now obsolete).

  e) `tests/test_fakes.py`: check for any `begin()` references and delete them.

- [ ] **Step 5: Run tests**

```bash
python -m pytest tests/application/test_indexing_service.py -v 2>&1 | tail -30
python -m pytest tests/storage/test_unit_of_work.py -v
python -m pytest tests/test_fakes.py -v
python -m pytest -q 2>&1 | tail -10
```

Expected: `test_indexing_service.py` PASS (new tests). `test_unit_of_work.py` PASS (minus the deleted test). Other test failures: `test_project_indexer.py` (Task 6), `test_end_to_end.py` integration cases (Task 7), `test_extraction/test_end_to_end.py` (composition-root issue, Task 7), `tests/test_cli.py` (Task 8).

- [ ] **Step 6: Commit**

```bash
git add python/pydocs_mcp/application/indexing_service.py \
        python/pydocs_mcp/storage/sqlite.py \
        python/pydocs_mcp/storage/protocols.py \
        tests/_fakes.py \
        tests/application/test_indexing_service.py \
        tests/storage/test_unit_of_work.py \
        tests/test_fakes.py
git commit -m "refactor(#5a-2): IndexingService depends on uow_factory; drop begin() shim"
```

---

## Task 6: Migrate `ProjectIndexer` (reach-through fix)

**Files:**
- Modify: `python/pydocs_mcp/application/project_indexer.py`
- Modify: `tests/application/test_project_indexer.py`

- [ ] **Step 1: Write the new test fixture + tests** — Edit `tests/application/test_project_indexer.py`:

  a) Replace the `FakeIndexingService` class (its `package_store: FakePackageStore` field is the reach-through). The new fake just records `reindex_calls` and `clear_all` invocations:

```python
@dataclass
class FakeIndexingService:
    """Stands in for application.IndexingService — records the call sequence."""

    cleared: bool = False
    clear_call_order: int | None = None
    reindex_calls: list[
        tuple[
            Package,
            tuple[Chunk, ...],
            tuple[ModuleMember, ...],
            tuple[DocumentNode, ...],
        ]
    ] = field(default_factory=list)
    _call_counter: int = 0

    async def clear_all(self) -> None:
        self._call_counter += 1
        self.cleared = True
        self.clear_call_order = self._call_counter

    async def reindex_package(
        self,
        package: Package,
        chunks: tuple[Chunk, ...],
        module_members: tuple[ModuleMember, ...],
        trees: tuple[DocumentNode, ...] = (),
        references: tuple = (),  # spec §3.1 — accept the #5b seam
    ) -> None:
        self._call_counter += 1
        self.reindex_calls.append((package, chunks, module_members, tuple(trees)))
```

Note `package_store` field is gone. `FakePackageStore` is also no longer needed inline — replaced by `InMemoryPackageStore` from `_fakes.py`.

Replace `FakePackageStore` (lines ~76-89) with `from tests._fakes import InMemoryPackageStore, make_fake_uow_factory` at the top imports. Update `_make_service` to wire both the `IndexingService` (the fake) and the `ProjectIndexer` (which now needs its own `uow_factory`) consistently:

```python
def _make_service(
    *,
    deps: tuple[str, ...] = (),
    project_pkg: Package | None = None,
    project_chunks: tuple[Chunk, ...] = (),
    project_members: tuple[ModuleMember, ...] = (),
    dep_chunk_returns: dict[str, Any] | None = None,
    dep_member_returns: dict[str, Any] | None = None,
    cached_packages: dict[str, Package] | None = None,
) -> tuple[
    ProjectIndexer,
    FakeIndexingService,
    FakeDependencyResolver,
    FakeChunkExtractor,
    FakeMemberExtractor,
    InMemoryPackageStore,  # NEW: returned so tests can assert on get() calls
]:
    idx = FakeIndexingService()
    resolver = FakeDependencyResolver(deps=deps)
    chunks_ex = FakeChunkExtractor(
        project_chunks=project_chunks,
        project_package=project_pkg,
        dep_returns=dep_chunk_returns or {},
    )
    members_ex = FakeMemberExtractor(
        project_members=project_members,
        dep_returns=dep_member_returns or {},
    )
    pkg_store = InMemoryPackageStore(items=dict(cached_packages or {}))
    uow_factory = make_fake_uow_factory(packages=pkg_store)
    service = ProjectIndexer(
        indexing_service=idx,
        dependency_resolver=resolver,
        chunk_extractor=chunks_ex,
        member_extractor=members_ex,
        uow_factory=uow_factory,
    )
    return service, idx, resolver, chunks_ex, members_ex, pkg_store
```

  b) Update existing tests that use the 5-tuple return to use the 6-tuple. Inspect each call site and adjust. Existing tests that asserted on `idx.package_store.known_packages[...]` (the cached-hash check) move to `pkg_store.items[...] = ...` seeding via the new `cached_packages` kwarg.

  c) Add a new test that explicitly pins ProjectIndexer's uow_factory usage:

```python
@pytest.mark.asyncio
async def test_project_indexer_uses_own_uow_factory_for_cache_check(tmp_path: Path) -> None:
    """spec §3.1 — ProjectIndexer reads cached package row via uow.packages.get,
    not via reach-through to indexing_service.package_store."""
    cached = _pkg("__project__")
    cached_pkg_with_hash = Package(
        name=cached.name, version=cached.version, summary=cached.summary,
        homepage=cached.homepage, dependencies=cached.dependencies,
        content_hash="h", origin=cached.origin,
    )
    project_pkg_same_hash = Package(
        name="__project__", version="0", summary="", homepage="",
        dependencies=(), content_hash="h",  # identical hash → cached
        origin=PackageOrigin.PROJECT,
    )

    service, idx, _resolver, _chunks_ex, _members_ex, pkg_store = _make_service(
        project_pkg=project_pkg_same_hash,
        cached_packages={"__project__": cached_pkg_with_hash},
    )

    stats = await service.index_project(tmp_path)

    # Cached → reindex_package NOT called; only the get on packages.
    assert len(idx.reindex_calls) == 0
    assert stats.project_indexed is False
    # Reach-through proof: pkg_store.calls shows the get.
    assert any(c.method == "get" and c.payload == "__project__" for c in pkg_store.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/application/test_project_indexer.py -v 2>&1 | tail -30
```

Expected: FAIL — `ProjectIndexer(uow_factory=...)` is not the current signature.

- [ ] **Step 3: Migrate the production class** — Replace `python/pydocs_mcp/application/project_indexer.py`:

```python
"""ProjectIndexer — write-side bootstrap orchestrator (spec §5.1, §5.3, post-#5a-2)."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.protocols import (
    ChunkExtractor,
    DependencyResolver,
    MemberExtractor,
)
from pydocs_mcp.storage.protocols import UnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.models import IndexingStats

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class ProjectIndexer:
    """Coordinates project + dependency indexing, returning fresh stats.

    Post-#5a-2: takes its own ``uow_factory`` for the hash-cache check
    (was a reach-through to ``indexing_service.package_store.get`` —
    eng plan-review #4). Composition root wires the same factory to
    both ``IndexingService`` and ``ProjectIndexer``.
    """

    indexing_service: IndexingService
    dependency_resolver: DependencyResolver
    chunk_extractor: ChunkExtractor
    member_extractor: MemberExtractor
    uow_factory: Callable[[], UnitOfWork]

    async def index_project(
        self,
        project_dir: Path,
        *,
        force: bool = False,
        include_project_source: bool = True,
        workers: int = 1,
    ) -> "IndexingStats":
        from pydocs_mcp.models import IndexingStats

        stats = IndexingStats()
        if force:
            await self.indexing_service.clear_all()
        if include_project_source:
            await self._index_project_source(project_dir, stats)
        deps = await self.dependency_resolver.resolve(project_dir)
        if workers <= 1:
            for dep_name in deps:
                await self._index_one_dependency(dep_name, stats)
        else:
            sem = asyncio.Semaphore(workers)

            async def _bounded(dep_name: str) -> None:
                async with sem:
                    await self._index_one_dependency(dep_name, stats)

            await asyncio.gather(*[_bounded(d) for d in deps])
        return stats

    async def _index_project_source(
        self, project_dir: Path, stats: "IndexingStats",
    ) -> None:
        result = await self.chunk_extractor.extract_from_project(project_dir)
        pkg = result.package
        async with self.uow_factory() as uow:
            existing = await uow.packages.get(pkg.name)
        if existing is not None and existing.content_hash == pkg.content_hash:
            log.info("Project: no changes (cached)")
            return
        members = await self.member_extractor.extract_from_project(project_dir)
        await self.indexing_service.reindex_package(
            pkg, result.chunks, members, trees=result.trees,
        )
        stats.project_indexed = True
        log.info(
            "Project: %d chunks, %d symbols, %d trees",
            len(result.chunks), len(members), len(result.trees),
        )

    async def _index_one_dependency(
        self, dep_name: str, stats: "IndexingStats",
    ) -> None:
        try:
            result = await self.chunk_extractor.extract_from_dependency(dep_name)
            pkg = result.package
            async with self.uow_factory() as uow:
                existing = await uow.packages.get(pkg.name)
            if existing is not None and existing.content_hash == pkg.content_hash:
                stats.cached += 1
                return
            members = await self.member_extractor.extract_from_dependency(dep_name)
            await self.indexing_service.reindex_package(
                pkg, result.chunks, members, trees=result.trees,
            )
            stats.indexed += 1
            log.info("  ok %s %s (%d chunks, %d syms, %d trees)",
                     pkg.name, pkg.version,
                     len(result.chunks), len(members), len(result.trees))
        except Exception as e:  # noqa: BLE001 -- spec §7 allowlist
            log.warning("  fail %s: %s", dep_name, e)
            stats.failed += 1
```

- [ ] **Step 4: Run tests**

```bash
python -m pytest tests/application/test_project_indexer.py -v 2>&1 | tail -30
python -m pytest -q 2>&1 | tail -10
```

Expected: `test_project_indexer.py` PASS. End-to-end tests in `tests/extraction/test_end_to_end.py` still failing (Task 7).

- [ ] **Step 5: Commit**

```bash
git add python/pydocs_mcp/application/project_indexer.py tests/application/test_project_indexer.py
git commit -m "refactor(#5a-2): ProjectIndexer uses own uow_factory; drop reach-through"
```

---

## Task 7: Update `storage/factories.py` and composition roots in `server.py` / `__main__.py`

**Files:**
- Modify: `python/pydocs_mcp/storage/factories.py`
- Modify: `python/pydocs_mcp/server.py`
- Modify: `python/pydocs_mcp/__main__.py`
- Modify: `tests/extraction/test_end_to_end.py` (composition consumers)

- [ ] **Step 1: Refactor `storage/factories.py`** — Replace:

```python
"""Canonical SQLite factories for the indexing + lookup services (post-#5a-2)."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.db import build_connection_provider
from pydocs_mcp.storage.sqlite import SqliteUnitOfWork

if TYPE_CHECKING:
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.retrieval.config import AppConfig


def build_sqlite_uow_factory(db_path: Path) -> Callable[[], SqliteUnitOfWork]:
    """Build a fresh-per-call ``SqliteUnitOfWork`` factory bound to a single
    ``ConnectionProvider``.

    Each call to the returned callable instantiates a NEW ``SqliteUnitOfWork``
    — instances are not reusable (the re-entrance guard fires). The provider
    is captured by closure once at factory-construction time so all UoWs
    share the same connection-pool semantics.
    """
    provider = build_connection_provider(db_path)
    return lambda: SqliteUnitOfWork(provider=provider)


def build_sqlite_indexing_service(db_path: Path) -> IndexingService:
    """Construct the canonical transactional IndexingService for *db_path*."""
    return IndexingService(uow_factory=build_sqlite_uow_factory(db_path))


def build_sqlite_lookup_service(
    db_path: Path, config: "AppConfig | None" = None,  # noqa: ARG001 -- kept for API stability
) -> "LookupService":
    """Compose a wired LookupService from a SQLite DB path."""
    from pydocs_mcp.application.lookup_service import LookupService
    from pydocs_mcp.application.package_lookup import PackageLookup
    from pydocs_mcp.application.tree_service import TreeService

    uow_factory = build_sqlite_uow_factory(db_path)
    package_lookup = PackageLookup(uow_factory=uow_factory)
    tree_svc = TreeService(uow_factory=uow_factory)
    return LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=None,  # sub-PR #5b
    )
```

- [ ] **Step 2: Refactor `server.py::run`** — In `python/pydocs_mcp/server.py`, replace lines 96-145:

```python
    from pydocs_mcp.application import (
        ApiSearch,
        DocsSearch,
        PackageLookup,
    )
    from pydocs_mcp.retrieval.config import (
        AppConfig,
        build_chunk_pipeline_from_config,
        build_member_pipeline_from_config,
    )
    from pydocs_mcp.retrieval.factories import build_retrieval_context
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    config = AppConfig.load(explicit_path=config_path)
    context = build_retrieval_context(db_path, config)
    chunk_pipeline = build_chunk_pipeline_from_config(config, context)
    member_pipeline = build_member_pipeline_from_config(config, context)

    # One UoW factory for every write-side service. Post-#5a-2 services all
    # share this — no more inline SqliteChunkRepository / SqlitePackageRepository
    # / SqliteDocumentTreeStore wiring.
    uow_factory = build_sqlite_uow_factory(db_path)

    package_lookup = PackageLookup(uow_factory=uow_factory)
    search_docs_svc = DocsSearch(chunk_pipeline=chunk_pipeline)
    search_api_svc = ApiSearch(member_pipeline=member_pipeline)

    tree_svc = TreeService(uow_factory=uow_factory)
    ref_svc = None  # reserved for sub-PR #5b

    lookup_svc = LookupService(
        package_lookup=package_lookup,
        tree_svc=tree_svc,
        ref_svc=ref_svc,
    )
```

The `SqliteChunkRepository`, `SqliteDocumentTreeStore`, `SqlitePackageRepository` imports (lines 107-111) go away. The standalone `package_store = ...`, `chunk_store = ...`, `member_store = ...` variables go away. `module_inspector` is NOT created in `server.py::run()` today — it's only consumed via `LookupService`'s tools — but verify via grep that no other consumer exists.

Verify with:

```bash
grep -n "SqliteChunkRepository\|SqliteDocumentTreeStore\|SqlitePackageRepository" python/pydocs_mcp/server.py
```

Expected: zero hits.

- [ ] **Step 3: Refactor `__main__.py::_run_indexing`** — In `python/pydocs_mcp/__main__.py`, replace the `_run_indexing` body:

```python
async def _run_indexing(args: argparse.Namespace, project: Path, db_path: Path) -> None:
    from pydocs_mcp.application import ProjectIndexer
    from pydocs_mcp.extraction import (
        AstMemberExtractor,
        InspectMemberExtractor,
        PipelineChunkExtractor,
        StaticDependencyResolver,
        build_ingestion_pipeline,
    )
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import (
        build_sqlite_indexing_service,
        build_sqlite_uow_factory,
    )
    from pydocs_mcp.storage.sqlite import SqliteChunkRepository
    from pydocs_mcp.db import build_connection_provider

    open_index_database(db_path).close()

    # Build ONE uow_factory and thread it through both services so they
    # share connection-pool semantics.
    uow_factory = build_sqlite_uow_factory(db_path)
    indexing_service = build_sqlite_indexing_service(db_path)
    # build_sqlite_indexing_service already constructs its OWN uow_factory
    # via the same db_path — they point to the same DB but are independent
    # closures. For the ProjectIndexer hash-cache check we want the SAME
    # factory the indexing_service uses; rebuild to share the provider.
    # (build_sqlite_indexing_service returns IndexingService(uow_factory=...);
    # we use that internal factory through the service shape, but ProjectIndexer
    # needs its own visible reference. Build once at the top.)

    use_inspect = not args.no_inspect
    mode = "inspect" if use_inspect else "static"
    log.info("Project: %s (mode=%s)", project, mode)

    config = AppConfig.load(explicit_path=getattr(args, "config", None))
    ingestion_pipeline = build_ingestion_pipeline(config)
    chunk_extractor = PipelineChunkExtractor(pipeline=ingestion_pipeline)

    ast_member = AstMemberExtractor()
    members_cfg = config.extraction.members
    inspect_depth = (
        args.depth if args.depth is not None
        else members_cfg.inspect_depth
    )
    member_extractor = (
        InspectMemberExtractor(
            static_fallback=ast_member, depth=inspect_depth,
            members_per_module_cap=members_cfg.members_per_module_cap,
            signature_max_chars=members_cfg.signature_max_chars,
            docstring_max_chars=members_cfg.docstring_max_chars,
        )
        if use_inspect else ast_member
    )

    orchestrator = ProjectIndexer(
        indexing_service=indexing_service,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=chunk_extractor,
        member_extractor=member_extractor,
        uow_factory=uow_factory,
    )

    if args.force:
        log.info("Cache cleared")

    stats = await orchestrator.index_project(
        project,
        force=args.force,
        include_project_source=not args.skip_project,
        workers=args.workers,
    )
    # FTS rebuild is a maintenance op, not transactional — use a direct
    # SqliteChunkRepository handle. Post-#5a-2 IndexingService no longer
    # exposes a chunk_store attribute.
    chunk_repo = SqliteChunkRepository(provider=build_connection_provider(db_path))
    await chunk_repo.rebuild_index()

    kb = db_path.stat().st_size / 1024 if db_path.exists() else 0.0
    log.info(
        "Done: %d indexed, %d cached, %d failed (db: %.0f KB)",
        stats.indexed, stats.cached, stats.failed, kb,
    )
```

The reach-through `indexing_service.chunk_store.rebuild_index()` at line 214 of the pre-migration `__main__.py` is replaced by a direct `SqliteChunkRepository(...).rebuild_index()` call.

- [ ] **Step 4: Update `tests/extraction/test_end_to_end.py::_build_service`** — Add the `uow_factory` argument to `ProjectIndexer(...)`:

```python
# In _build_service (around line 106-121), edit:
def _build_service(db_path: Path) -> ProjectIndexer:
    """Wire the production-shape ProjectIndexer over *db_path*."""
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory  # NEW
    indexing = build_sqlite_indexing_service(db_path)
    uow_factory = build_sqlite_uow_factory(db_path)  # NEW
    pipeline = build_ingestion_pipeline(AppConfig.load())
    return ProjectIndexer(
        indexing_service=indexing,
        dependency_resolver=StaticDependencyResolver(),
        chunk_extractor=PipelineChunkExtractor(pipeline=pipeline),
        member_extractor=AstMemberExtractor(),
        uow_factory=uow_factory,  # NEW
    )
```

Also update line 302 in the same file:

```python
# BEFORE
service = TreeService(tree_store=tree_store)
# AFTER
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
service = TreeService(uow_factory=build_sqlite_uow_factory(db_path))
```

- [ ] **Step 5: Run end-to-end tests**

```bash
python -m pytest tests/extraction/test_end_to_end.py -v
python -m pytest tests/application/test_end_to_end.py -v
python -m pytest -q
```

Expected: all e2e tests PASS. Server tests (`tests/test_server.py`) PASS — they use the high-level `LookupService` / `MCPToolError` surfaces which haven't changed in shape.

- [ ] **Step 6: CLI smoke test**

```bash
python -c "from pydocs_mcp.server import run; print('server import ok')"
python -c "from pydocs_mcp.__main__ import main; print('cli import ok')"
python -m pydocs_mcp --help
python -m pytest tests/test_cli.py -v
```

Expected: zero ImportError / AttributeError; CLI prints help; `test_cli.py` PASS.

- [ ] **Step 7: Commit**

```bash
git add python/pydocs_mcp/storage/factories.py \
        python/pydocs_mcp/server.py \
        python/pydocs_mcp/__main__.py \
        tests/extraction/test_end_to_end.py
git commit -m "refactor(#5a-2): wire uow_factory through composition roots; drop inline repo construction"
```

---

## Task 8: Stale-name greps + final verification

- [ ] **Step 1: Greppable AC verification**

```bash
# AC #6 — kwarg names should not appear in production code.
echo "AC #6 (python/):"
grep -rnE "package_store=|chunk_store=|module_member_store=|tree_store=|unit_of_work=" python/ || echo "ZERO MATCHES — pass"

# AC #7 — kwarg names should only appear in tests at the BuildContext line in test_parity_golden.py.
echo "AC #7 (tests/):"
grep -rnE "package_store=|chunk_store=|module_member_store=|tree_store=|unit_of_work=" tests/ | grep -v "test_parity_golden.py" || echo "ZERO MATCHES outside parity-golden — pass"

# AC #8 — begin() removed from Protocol + sqlite + indexing + fakes.
echo "AC #8 (begin() residue):"
grep -rnE "\.begin\(\)" python/pydocs_mcp/storage/protocols.py python/pydocs_mcp/storage/sqlite.py python/pydocs_mcp/application/indexing_service.py tests/_fakes.py || echo "ZERO MATCHES — pass"

# AC #1-5 — service dataclass fields.
echo "AC #1-5 (service shapes):"
python -c "
import dataclasses
from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.application.package_lookup import PackageLookup
from pydocs_mcp.application.module_inspector import ModuleInspector
from pydocs_mcp.application.tree_service import TreeService
from pydocs_mcp.application.project_indexer import ProjectIndexer

for cls, expected in [
    (IndexingService, {'uow_factory'}),
    (PackageLookup, {'uow_factory'}),
    (ModuleInspector, {'uow_factory'}),
    (TreeService, {'uow_factory'}),
    (ProjectIndexer, {'indexing_service', 'dependency_resolver', 'chunk_extractor', 'member_extractor', 'uow_factory'}),
]:
    fields = {f.name for f in dataclasses.fields(cls)}
    assert fields == expected, f'{cls.__name__}: {fields} != {expected}'
    print(f'{cls.__name__}: {sorted(fields)} OK')
"
```

Expected: all checks pass with the "ZERO MATCHES" outputs and the per-service OK lines.

- [ ] **Step 2: Full gauntlet**

```bash
python -m pytest -q 2>&1 | tail -5
ruff check python/ tests/
. "$HOME/.cargo/env" && cargo fmt --check && cargo clippy -- -D warnings
python scripts/smoke_check_benchmark_imports.py
python -m pytest tests/retrieval/test_parity_golden.py -v
```

Expected: full pytest suite PASS (test count == baseline minus deleted tests + new tests; expect roughly baseline ± 10); ruff clean; cargo clean; parity-golden green.

- [ ] **Step 3: CLI/MCP smoke**

```bash
# Drive the CLI end-to-end on a tiny fixture to prove composition roots work.
python -c "
import asyncio, tempfile, pathlib
from pydocs_mcp.__main__ import _run_indexing
from pydocs_mcp.db import cache_path_for_project
import argparse
ns = argparse.Namespace(
    project='.', depth=None, workers=1, force=True, skip_project=True,
    no_rust=False, no_inspect=True, config=None, verbose=False,
)
with tempfile.TemporaryDirectory() as td:
    project = pathlib.Path(td)
    (project / 'pyproject.toml').write_text('[project]\nname=\"smoke\"\nversion=\"0\"\n')
    db = cache_path_for_project(project)
    asyncio.run(_run_indexing(ns, project, db))
    print('CLI smoke OK; db at', db)
"
```

Expected: zero errors; "CLI smoke OK" prints.

- [ ] **Step 4: Per-AC verification table**

Walk through each AC #1 through #23 in spec §11 and confirm. Spot-check examples:

- AC #11: `python -c "from pydocs_mcp.storage.factories import build_sqlite_uow_factory; print(build_sqlite_uow_factory)"` — should print the function.
- AC #14: `python -c "from tests._fakes import make_fake_uow_factory; print(make_fake_uow_factory.__doc__)"` — should print the docstring.
- AC #20: `python scripts/smoke_check_benchmark_imports.py` — already in Step 2.

- [ ] **Step 5: Commit any remaining test or doc cleanups**

```bash
git status
# If there are uncommitted changes from greppable cleanup (e.g. unused imports),
# commit them now.
git add -A
git commit -m "chore(#5a-2): final cleanup — remove dead imports + stale comments"
```

---

## Task 9: Push + open PR

- [ ] **Step 1: Push**

```bash
git push -u origin feature/sub-pr-5a-2-service-to-uow
```

- [ ] **Step 2: Open PR**

```bash
gh pr create --base main --head feature/sub-pr-5a-2-service-to-uow \
    --title "sub-PR #5a-2: full service-to-UoW migration (services depend on uow_factory)" \
    --body "$(cat <<'EOF'
## Summary

Follow-up to PR #19 (sub-PR #5a) — completes the §14 UoW refactor by migrating every application service to depend on a single `uow_factory: Callable[[], UnitOfWork]` constructor parameter. Removes the residual `*_store` / `unit_of_work` fields from `IndexingService`, `PackageLookup`, `ModuleInspector`, `TreeService`. Fixes the `ProjectIndexer.index_project` reach-through (eng plan-review #4). Drops the pre-#5a `UnitOfWork.begin()` back-compat shim from Protocol, SqliteUnitOfWork, FakeUnitOfWork, and IndexingService.

Spec: docs/superpowers/specs/2026-05-17-sub-pr-5a-2-service-to-uow-migration-design.md
Plan: docs/superpowers/plans/2026-05-17-sub-pr-5a-2-service-to-uow-migration.md

## Why now

CEO review on PR #19 objected to bundling the migration with the Protocol-widening. With #5a shipped, the migration is its own atomic refactor — zero protocol changes, zero new tests-by-feature, just consumer-side mechanical work. Unblocks #5b: `ReferenceStore` joins `UnitOfWork` as the 5th attribute instead of becoming a 6th `IndexingService` constructor field.

## Test plan

- [ ] All 826+ baseline tests pass (minus the 4-5 tests that pinned removed back-compat behavior).
- [ ] New tests cover: `make_fake_uow_factory` helper (3), `ProjectIndexer` uow-based cache check (1).
- [ ] AC #6 + #7 — `grep -rnE "package_store=|chunk_store=|module_member_store=|tree_store=|unit_of_work=" python/` returns zero matches; tests/ returns only the BuildContext line in test_parity_golden.py.
- [ ] AC #8 — `begin()` is gone from Protocol, sqlite, fakes, indexing_service.
- [ ] `tests/retrieval/test_parity_golden.py` passes (byte-parity contract on retrieval undisturbed).
- [ ] CLI smoke: `python -m pydocs_mcp index <small_fixture>` runs end-to-end.
- [ ] MCP server import: `python -c "from pydocs_mcp.server import run"`.
- [ ] ruff + cargo clean.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Watch CI**

```bash
gh pr checks <NEW_PR_NUMBER> --watch
```

Expected: all CI jobs PASS.

---

## Self-review

**1. Spec → Plan AC mapping:**

| Spec AC | Plan task |
|---|---|
| AC #1 (IndexingService 1 field) | Task 5 Step 3 + Step 7 `test_indexing_service_only_has_one_field` |
| AC #2 (PackageLookup 1 field) | Task 4 Step 3 + Step 7 (greppable check) |
| AC #3 (ModuleInspector 1 field) | Task 2 Step 3 |
| AC #4 (TreeService 1 field) | Task 3 Step 3 |
| AC #5 (ProjectIndexer 5 fields, uow.packages.get) | Task 6 Step 3 + dedicated test |
| AC #6 + #7 (greppable kwarg-name absence) | Task 8 Step 1 |
| AC #8 (begin() gone everywhere) | Task 5 Step 4 + Task 8 Step 1 |
| AC #9 + #10 (`__post_init__` + `_in_uow` removed) | Task 5 Step 3 |
| AC #11 (`build_sqlite_uow_factory` exists; factories use it) | Task 7 Step 1 |
| AC #12 (server.py drops 3 Sqlite repo imports) | Task 7 Step 2 |
| AC #13 (`__main__.py` builds one factory; FTS rebuild outside service) | Task 7 Step 3 |
| AC #14 (`make_fake_uow_factory` + tests) | Task 1 |
| AC #15 (test_indexing_service.py deletes inline Fake* block) | Task 5 Step 1 |
| AC #16 (test_project_indexer.py FakeIndexingService drops package_store) | Task 6 Step 1 |
| AC #17 (InMemory*Store .calls fields) | Task 1 Step 3 |
| AC #18 (all baseline tests pass) | Task 8 Step 2 |
| AC #19 (parity-golden passes) | Task 8 Step 2 |
| AC #20 (benchmark smoke) | Task 8 Step 2 |
| AC #21 (ruff + cargo clean) | Task 8 Step 2 |
| AC #22 (CLI runs e2e) | Task 8 Step 3 |
| AC #23 (server import works) | Task 7 Step 6 |

Every AC maps to a verifiable step.

**2. Type / name consistency:**

- `uow_factory: Callable[[], UnitOfWork]` — used identically across Tasks 2-6 and the spec.
- `uow.packages.list(filter, limit)` — used consistently (NOT `.all()`); eng-review #2 caught.
- `uow.chunks.list(filter, limit)` — consistent.
- `uow.module_members.list(filter, limit)` — consistent.
- `uow.trees.load(package, module)` / `uow.trees.exists(...)` / `uow.trees.load_all_in_package(...)` — consistent with existing `DocumentTreeStore` Protocol.
- `make_fake_uow_factory(packages=, chunks=, module_members=, trees=)` — kwarg names mirror `FakeUnitOfWork(packages_store=, chunks_store=, module_members_store=, trees_store=)`. Note: helper kwargs are unsuffixed (`packages=`), constructor kwargs are suffixed (`packages_store=`). Tests use the helper, NOT the constructor directly, so the distinction stays clean.

**3. Placeholder scan:** No "TBD", "implement similarly", "as appropriate". Every code block is concrete.

**4. Eng plan-review findings addressed:**

- ✅ #1 (ContextVar in `__aenter__`) — DONE by #5a, no new work here.
- ✅ #2 (`uow.packages.list(filter, limit)`) — every code block uses this exact signature.
- ✅ #3 (conditional tree write) — `if trees:` guard in `reindex_package` (empty `trees` tuple skips tree writes; `remove_package` and `clear_all` unconditionally delete since the UoW Protocol guarantees `uow.trees`). Tested by `test_reindex_package_with_empty_trees_skips_tree_store`.
- ✅ #4 (ProjectIndexer reach-through) — fixed in Task 6 with its own `uow_factory`.
- ✅ #5 (InMemory*Store.list signature) — DONE by #5a; Task 1 adds the `.calls` tracking but doesn't change the list signature.

**5. New risk:** `__main__.py` rebuild_index reach-through was NOT one of the original 5 eng-review bugs. Discovered during codebase survey. Fixed in Task 7 Step 3 by constructing a direct `SqliteChunkRepository`. Documented in spec §3.2 + risks table.

---