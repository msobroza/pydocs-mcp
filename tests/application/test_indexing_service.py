"""Tests for IndexingService using Protocol-fake stores ONLY (AC #10).

These tests prove that IndexingService is backend-agnostic: they use
plain in-memory fakes that structurally satisfy the PackageStore /
ChunkStore / ModuleMemberStore / UnitOfWork Protocols. No SQLite
connection is opened, no concrete repository is imported.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

from pydocs_mcp.application.indexing_service import IndexingService
from pydocs_mcp.models import Chunk, ModuleMember, Package, PackageOrigin
from pydocs_mcp.storage.filters import Filter


# ── Protocol fakes (in-memory, no SQLite) ─────────────────────────────────


@dataclass
class _Call:
    method: str
    payload: object


@dataclass
class FakePackageStore:
    """Structurally satisfies PackageStore — no base class inheritance."""

    calls: list[_Call] = field(default_factory=list)
    by_name: dict[str, Package] = field(default_factory=dict)

    async def upsert(self, package: Package) -> None:
        self.calls.append(_Call("upsert", package))
        self.by_name[package.name] = package

    async def get(self, name: str) -> Package | None:
        self.calls.append(_Call("get", name))
        return self.by_name.get(name)

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Package]:
        self.calls.append(_Call("list", filter))
        return list(self.by_name.values())

    async def delete(self, filter: Filter | Mapping) -> int:
        self.calls.append(_Call("delete", dict(filter) if isinstance(filter, Mapping) else filter))
        if isinstance(filter, Mapping):
            name = filter.get("name")
            if isinstance(name, str):
                removed = 1 if self.by_name.pop(name, None) is not None else 0
                return removed
            if isinstance(name, Mapping) and name.get("like") == "%":
                removed = len(self.by_name)
                self.by_name.clear()
                return removed
        return 0

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        return len(self.by_name)


@dataclass
class FakeChunkStore:
    calls: list[_Call] = field(default_factory=list)
    rows: list[Chunk] = field(default_factory=list)

    async def upsert(self, chunks: Iterable[Chunk]) -> None:
        materialised = tuple(chunks)
        self.calls.append(_Call("upsert", materialised))
        self.rows.extend(materialised)

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[Chunk]:
        return list(self.rows)

    async def delete(self, filter: Filter | Mapping) -> int:
        self.calls.append(_Call("delete", dict(filter) if isinstance(filter, Mapping) else filter))
        if isinstance(filter, Mapping):
            pkg = filter.get("package")
            if isinstance(pkg, str):
                before = len(self.rows)
                self.rows = [c for c in self.rows if c.metadata.get("package") != pkg]
                return before - len(self.rows)
            if isinstance(pkg, Mapping) and pkg.get("like") == "%":
                removed = len(self.rows)
                self.rows.clear()
                return removed
        return 0

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        return len(self.rows)

    async def rebuild_index(self) -> None:
        self.calls.append(_Call("rebuild_index", None))


@dataclass
class FakeModuleMemberStore:
    calls: list[_Call] = field(default_factory=list)
    rows: list[ModuleMember] = field(default_factory=list)

    async def upsert_many(self, members: Iterable[ModuleMember]) -> None:
        materialised = tuple(members)
        self.calls.append(_Call("upsert_many", materialised))
        self.rows.extend(materialised)

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[ModuleMember]:
        return list(self.rows)

    async def delete(self, filter: Filter | Mapping) -> int:
        self.calls.append(_Call("delete", dict(filter) if isinstance(filter, Mapping) else filter))
        if isinstance(filter, Mapping):
            pkg = filter.get("package")
            if isinstance(pkg, str):
                before = len(self.rows)
                self.rows = [m for m in self.rows if m.metadata.get("package") != pkg]
                return before - len(self.rows)
            if isinstance(pkg, Mapping) and pkg.get("like") == "%":
                removed = len(self.rows)
                self.rows.clear()
                return removed
        return 0

    async def count(self, filter: Filter | Mapping | None = None) -> int:
        return len(self.rows)


@dataclass
class FakeUnitOfWork:
    began: int = 0
    committed: int = 0
    rolled_back: int = 0

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        self.began += 1
        try:
            yield
        except BaseException:
            self.rolled_back += 1
            raise
        else:
            self.committed += 1


# ── Fixtures ─────────────────────────────────────────────────────────────


def _pkg(name: str = "fastapi") -> Package:
    return Package(
        name=name,
        version="0.1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
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
async def test_reindex_package_without_uow():
    """Without a UoW the service calls delete→upsert on all three stores in order."""
    ps = FakePackageStore()
    cs = FakeChunkStore()
    ms = FakeModuleMemberStore()

    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms, unit_of_work=None,
    )
    pkg = _pkg("fastapi")
    chunks = (_chunk("fastapi", "Routing"), _chunk("fastapi", "Middleware"))
    members = (_member("fastapi", "APIRouter"),)

    await service.reindex_package(pkg, chunks, members)

    # Deletes happen BEFORE the upserts, one per store.
    assert [c.method for c in cs.calls] == ["delete", "upsert"]
    assert [c.method for c in ms.calls] == ["delete", "upsert_many"]
    assert [c.method for c in ps.calls] == ["delete", "upsert"]

    # Delete filters key on the package name.
    assert cs.calls[0].payload == {"package": "fastapi"}
    assert ms.calls[0].payload == {"package": "fastapi"}
    assert ps.calls[0].payload == {"name": "fastapi"}

    # Upserted rows made it into the fake stores.
    assert ps.by_name["fastapi"] is pkg
    assert len(cs.rows) == 2
    assert len(ms.rows) == 1


@pytest.mark.asyncio
async def test_reindex_package_with_uow():
    """A UoW's begin() is entered around the whole mutation."""
    ps = FakePackageStore()
    cs = FakeChunkStore()
    ms = FakeModuleMemberStore()
    uow = FakeUnitOfWork()

    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms, unit_of_work=uow,
    )
    pkg = _pkg("starlette")
    await service.reindex_package(pkg, (), ())

    assert uow.began == 1
    assert uow.committed == 1
    assert uow.rolled_back == 0
    # The underlying writes still happened inside the scope.
    assert ps.by_name["starlette"] is pkg


@pytest.mark.asyncio
async def test_remove_package_deletes_all_three_stores():
    """`remove_package` deletes from every store but never upserts."""
    ps = FakePackageStore()
    ps.by_name["fastapi"] = _pkg("fastapi")
    cs = FakeChunkStore()
    cs.rows.extend([_chunk("fastapi", "A"), _chunk("starlette", "B")])
    ms = FakeModuleMemberStore()
    ms.rows.extend([_member("fastapi", "X"), _member("starlette", "Y")])

    service = IndexingService(package_store=ps, chunk_store=cs, module_member_store=ms)
    await service.remove_package("fastapi")

    assert [c.method for c in cs.calls] == ["delete"]
    assert [c.method for c in ms.calls] == ["delete"]
    assert [c.method for c in ps.calls] == ["delete"]

    # Only fastapi rows removed; starlette survives.
    assert list(ps.by_name.keys()) == []  # we only seeded fastapi in ps
    assert [c.metadata["package"] for c in cs.rows] == ["starlette"]
    assert [m.metadata["package"] for m in ms.rows] == ["starlette"]


@pytest.mark.asyncio
async def test_clear_all_cascades_through_fakes():
    """`clear_all` wipes every row across all three stores via a LIKE '%' filter."""
    ps = FakePackageStore()
    ps.by_name["fastapi"] = _pkg("fastapi")
    ps.by_name["starlette"] = _pkg("starlette")
    cs = FakeChunkStore()
    cs.rows.extend([_chunk("fastapi", "A"), _chunk("starlette", "B")])
    ms = FakeModuleMemberStore()
    ms.rows.extend([_member("fastapi", "X"), _member("starlette", "Y")])

    service = IndexingService(package_store=ps, chunk_store=cs, module_member_store=ms)
    await service.clear_all()

    assert ps.by_name == {}
    assert cs.rows == []
    assert ms.rows == []

    # The filter shape is the LIKE '%' pattern described in the spec note.
    assert cs.calls[-1].payload == {"package": {"like": "%"}}
    assert ms.calls[-1].payload == {"package": {"like": "%"}}
    assert ps.calls[-1].payload == {"name": {"like": "%"}}


def test_indexing_service_accepts_fake_stores_only():
    """Constructing the service from plain Protocol fakes type-checks fine.

    The goal of AC #10 is that IndexingService only depends on Protocols —
    so the bare fake classes above (which have no inheritance relationship
    to the concrete SQLite repositories) are enough.
    """
    service = IndexingService(
        package_store=FakePackageStore(),
        chunk_store=FakeChunkStore(),
        module_member_store=FakeModuleMemberStore(),
        unit_of_work=FakeUnitOfWork(),
    )
    # Frozen dataclass — fields must be wired correctly.
    assert isinstance(service.package_store, FakePackageStore)
    assert isinstance(service.chunk_store, FakeChunkStore)
    assert isinstance(service.module_member_store, FakeModuleMemberStore)
    assert isinstance(service.unit_of_work, FakeUnitOfWork)

    # And also with unit_of_work=None (the non-transactional path).
    service2 = IndexingService(
        package_store=FakePackageStore(),
        chunk_store=FakeChunkStore(),
        module_member_store=FakeModuleMemberStore(),
    )
    assert service2.unit_of_work is None
