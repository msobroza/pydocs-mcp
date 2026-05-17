"""Shared in-memory Protocol fakes for tests.

Promotes Protocol fakes from inline-test-definitions to a single
canonical place so multiple test files don't drift on what
``DocumentTreeStore``'s shape actually is. Each new method on a
Protocol must be reflected here once, instead of in every test file's
copy of the fake.

Currently exports:
- :class:`InMemoryDocumentTreeStore` — records call history and keeps
  per-package payloads. Structurally satisfies
  :class:`~pydocs_mcp.storage.protocols.DocumentTreeStore`.
- :class:`InMemoryPackageStore` / :class:`InMemoryChunkStore` /
  :class:`InMemoryModuleMemberStore` — mirror the real
  ``Sqlite*Repository`` Protocol method signatures (``list(filter,
  limit)``, ``delete(filter) -> int``) so any service that runs against
  the real wiring also runs against the fakes without surprise.
- :class:`FakeUnitOfWork` — structurally satisfies the widened
  :class:`~pydocs_mcp.storage.protocols.UnitOfWork` Protocol (sub-PR
  #5a Task 1). Tracks ``committed`` / ``rolled_back`` flags so service
  tests can assert end-state without inspecting persistence.

Tests that need to assert call ordering can either import the fake's
own ``calls`` list (each entry is a ``(method, payload)`` tuple) or
inject a shared audit list at construction time.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from pydocs_mcp.models import Chunk, ModuleMember, Package
from pydocs_mcp.storage.errors import UnitOfWorkNotEnteredError


class _NotEnteredProxy:
    """Repo placeholder used outside ``async with uow:``.

    Has to be a real instance attribute (not a property) so that
    ``inspect.getattr_static`` — used by Python 3.12+'s
    ``typing._ProtocolMeta.__instancecheck__`` — can see it. Any actual
    method call raises :class:`UnitOfWorkNotEnteredError`, which is what
    test code (and real services) would trigger.
    """

    __slots__ = ("_attr_name",)

    def __init__(self, attr_name: str) -> None:
        self._attr_name = attr_name

    def __getattr__(self, name: str) -> Any:
        raise UnitOfWorkNotEnteredError(self._attr_name)

    def __bool__(self) -> bool:  # truthy for isinstance probes
        return True


@dataclass
class _Call:
    method: str
    payload: Any


@dataclass
class InMemoryDocumentTreeStore:
    """Structurally satisfies DocumentTreeStore — async methods only.

    Use directly in tests that exercise ``IndexingService`` /
    ``LookupService`` write+read interactions without touching SQLite.
    """

    calls: list[_Call] = field(default_factory=list)
    by_package: dict[str, list] = field(default_factory=dict)

    async def save_many(
        self, trees, *, package, uow=None,
    ) -> None:
        materialised = tuple(trees)
        self.calls.append(_Call("save_many", (package, materialised)))
        self.by_package.setdefault(package, []).extend(materialised)

    async def load(self, package, module):
        return None  # not exercised in write-side tests

    async def load_all_in_package(self, package):
        return {}

    async def exists(self, package, module):
        return False  # not exercised in write-side tests

    async def delete_for_package(self, package, *, uow=None) -> None:
        self.calls.append(_Call("delete_for_package", package))
        self.by_package.pop(package, None)

    async def delete_all(self, *, uow=None) -> None:
        self.calls.append(_Call("delete_all", None))
        self.by_package.clear()


# ── Entity stores ────────────────────────────────────────────────────────
# These mirror the real ``Sqlite*Repository`` Protocol signatures
# exactly — ``list(filter=..., limit=...)``, ``delete(filter) -> int``,
# ``count(filter) -> int``. Eng plan-review caught a planned ``.all()``
# method that would have crashed against ``SqlitePackageRepository``;
# the contract test in ``test_fakes.py`` now pins that signature so a
# future drift is caught immediately.


@dataclass
class InMemoryPackageStore:
    items: dict[str, Package] = field(default_factory=dict)

    async def get(self, name: str) -> Package | None:
        return self.items.get(name)

    async def upsert(self, package: Package) -> None:
        self.items[package.name] = package

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[Package]:
        rows = list(self.items.values())
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        before = len(self.items)
        if filter is None:
            self.items.clear()
        elif isinstance(filter, dict) and "name" in filter:
            self.items.pop(filter["name"], None)
        else:
            # Treat any non-dict filter (e.g. All()) as match-all in tests.
            self.items.clear()
        return before - len(self.items)

    async def count(self, filter: Any | None = None) -> int:
        if isinstance(filter, dict) and "name" in filter:
            return 1 if filter["name"] in self.items else 0
        return len(self.items)


@dataclass
class InMemoryChunkStore:
    by_package: dict[str, list[Chunk]] = field(default_factory=dict)

    async def upsert(self, chunks) -> None:
        for c in chunks:
            pkg = c.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(c)

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[Chunk]:
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [c for cs in self.by_package.values() for c in cs]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        before = sum(len(v) for v in self.by_package.values())
        if filter is None:
            self.by_package.clear()
        elif isinstance(filter, dict) and "package" in filter:
            self.by_package.pop(filter["package"], None)
        else:
            self.by_package.clear()
        return before - sum(len(v) for v in self.by_package.values())

    async def count(self, filter: Any | None = None) -> int:
        if isinstance(filter, dict) and "package" in filter:
            return len(self.by_package.get(filter["package"], []))
        return sum(len(v) for v in self.by_package.values())

    async def rebuild_index(self) -> None:
        # In-memory store has no FTS index to rebuild.
        return None


@dataclass
class InMemoryModuleMemberStore:
    by_package: dict[str, list[ModuleMember]] = field(default_factory=dict)

    async def upsert_many(self, members) -> None:
        for m in members:
            pkg = m.metadata.get("package", "")
            self.by_package.setdefault(pkg, []).append(m)

    async def list(
        self, filter: Any | None = None, limit: int | None = None,
    ) -> list[ModuleMember]:
        if isinstance(filter, dict) and "package" in filter:
            rows = list(self.by_package.get(filter["package"], []))
        else:
            rows = [m for ms in self.by_package.values() for m in ms]
        if limit is not None:
            rows = rows[:limit]
        return rows

    async def delete(self, filter: Any | None = None) -> int:
        before = sum(len(v) for v in self.by_package.values())
        if filter is None:
            self.by_package.clear()
        elif isinstance(filter, dict) and "package" in filter:
            self.by_package.pop(filter["package"], None)
        else:
            self.by_package.clear()
        return before - sum(len(v) for v in self.by_package.values())

    async def count(self, filter: Any | None = None) -> int:
        if isinstance(filter, dict) and "package" in filter:
            return len(self.by_package.get(filter["package"], []))
        return sum(len(v) for v in self.by_package.values())


# ── FakeUnitOfWork ───────────────────────────────────────────────────────


@dataclass
class FakeUnitOfWork:
    """Structurally satisfies UnitOfWork. Tracks committed/rolled_back.

    Mirrors :class:`~pydocs_mcp.storage.sqlite.SqliteUnitOfWork`:
    repository attributes are only valid inside ``async with uow:`` and
    raise :class:`UnitOfWorkNotEnteredError` outside; ``__aexit__``
    triggers ``rolled_back`` if the body exited without calling
    ``commit()`` (or if an exception escaped). ``begin()`` is the
    pre-#5a back-compat shim, matching SqliteUnitOfWork's
    ``@asynccontextmanager`` shape.

    Repo accessors (``packages`` / ``chunks`` / ``module_members`` /
    ``trees``) are stored as real instance attributes (rather than
    ``@property`` or ``__getattribute__``-synthesized) because Python
    3.12+'s ``typing._ProtocolMeta.__instancecheck__`` uses
    ``inspect.getattr_static`` — which bypasses both descriptors and
    ``__getattribute__``, so synthesized attributes are invisible to
    Protocol checks. Outside the context they are bound to
    :class:`_NotEnteredProxy` (any method call raises
    :class:`UnitOfWorkNotEnteredError`); ``__aenter__`` swaps them with
    the real stores and ``__aexit__`` swaps back.
    """

    packages_store:       InMemoryPackageStore       = field(default_factory=InMemoryPackageStore)
    chunks_store:         InMemoryChunkStore         = field(default_factory=InMemoryChunkStore)
    module_members_store: InMemoryModuleMemberStore  = field(default_factory=InMemoryModuleMemberStore)
    trees_store:          InMemoryDocumentTreeStore  = field(default_factory=InMemoryDocumentTreeStore)
    committed:   bool = False
    rolled_back: bool = False
    _entered:    bool = False

    # Real instance attributes — swapped by __aenter__/__aexit__. Initialized
    # in __post_init__ so getattr_static() (used by typing on 3.12+) sees them.
    packages:       Any = field(init=False, repr=False)
    chunks:         Any = field(init=False, repr=False)
    module_members: Any = field(init=False, repr=False)
    trees:          Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.packages       = _NotEnteredProxy("packages")
        self.chunks         = _NotEnteredProxy("chunks")
        self.module_members = _NotEnteredProxy("module_members")
        self.trees          = _NotEnteredProxy("trees")

    async def __aenter__(self) -> FakeUnitOfWork:
        if self._entered:
            raise RuntimeError("FakeUnitOfWork is already entered.")
        self._entered = True
        # Swap proxies for real stores.
        self.packages       = self.packages_store
        self.chunks         = self.chunks_store
        self.module_members = self.module_members_store
        self.trees          = self.trees_store
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        if exc_type is not None or not self.committed:
            self.rolled_back = True
        self._entered = False
        # Swap back to proxies so post-exit access raises.
        self.packages       = _NotEnteredProxy("packages")
        self.chunks         = _NotEnteredProxy("chunks")
        self.module_members = _NotEnteredProxy("module_members")
        self.trees          = _NotEnteredProxy("trees")
        return False

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True

    @asynccontextmanager
    async def begin(self):
        async with self:
            yield
            await self.commit()


__all__ = (
    "FakeUnitOfWork",
    "InMemoryChunkStore",
    "InMemoryDocumentTreeStore",
    "InMemoryModuleMemberStore",
    "InMemoryPackageStore",
    "_Call",
)
