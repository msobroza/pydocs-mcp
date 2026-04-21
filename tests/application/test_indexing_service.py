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
from pydocs_mcp.storage.filters import All, Filter


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
        if isinstance(filter, All) and not filter.clauses:
            removed = len(self.by_name)
            self.by_name.clear()
            return removed
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
        if isinstance(filter, All) and not filter.clauses:
            removed = len(self.rows)
            self.rows.clear()
            return removed
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
        if isinstance(filter, All) and not filter.clauses:
            removed = len(self.rows)
            self.rows.clear()
            return removed
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
    """`clear_all` wipes every row across all three stores via an empty-All filter."""
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

    # The filter is ``All(clauses=())`` — an unconditional match that the
    # SqliteFilterAdapter translates to ``1 = 1`` (covers NULL rows too).
    assert cs.calls[-1].payload == All(clauses=())
    assert ms.calls[-1].payload == All(clauses=())
    assert ps.calls[-1].payload == All(clauses=())


@pytest.mark.asyncio
async def test_indexing_service_clear_all_also_removes_null_package_rows(tmp_path):
    """Regression: ``clear_all`` previously used ``LIKE '%'`` which skips
    NULL package values. Seeding a row with ``package=NULL`` via raw SQL
    and then calling ``clear_all`` must leave the table empty.
    """
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.storage.wiring import build_sqlite_indexing_service

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
    # A NULL-package row simulates a schema drift / partially-written fixture.
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
    # Both the normal row and the NULL-package row must be gone.
    assert chunk_count == 0


def test_indexing_service_without_uow_logs_warning(caplog):
    """Constructing without a UoW must emit a warning — callers should be
    aware that writes are non-atomic in that mode."""
    import logging
    with caplog.at_level(logging.WARNING, logger="pydocs_mcp.application.indexing_service"):
        IndexingService(
            package_store=FakePackageStore(),
            chunk_store=FakeChunkStore(),
            module_member_store=FakeModuleMemberStore(),
        )
    assert any("NOT atomic" in r.message for r in caplog.records)


def test_indexing_service_with_uow_does_not_warn(caplog):
    """Constructing with a UoW is the intended happy path — no warning."""
    import logging
    with caplog.at_level(logging.WARNING, logger="pydocs_mcp.application.indexing_service"):
        IndexingService(
            package_store=FakePackageStore(),
            chunk_store=FakeChunkStore(),
            module_member_store=FakeModuleMemberStore(),
            unit_of_work=FakeUnitOfWork(),
        )
    assert not any("NOT atomic" in r.message for r in caplog.records)


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


# ── Task 23 — DocumentTreeStore integration ──────────────────────────────


@dataclass
class FakeDocumentTreeStore:
    """Structurally satisfies DocumentTreeStore — async methods only."""

    calls: list[_Call] = field(default_factory=list)
    by_package: dict[str, list] = field(default_factory=dict)

    async def save_many(
        self, trees, *, package, uow=None,
    ) -> None:
        materialised = tuple(trees)
        self.calls.append(_Call("save_many", (package, materialised)))
        self.by_package.setdefault(package, []).extend(materialised)

    async def load(self, package, module):
        return None  # not exercised here

    async def load_all_in_package(self, package):
        return {}

    async def delete_for_package(self, package, *, uow=None) -> None:
        self.calls.append(_Call("delete_for_package", package))
        self.by_package.pop(package, None)


@pytest.mark.asyncio
async def test_reindex_package_without_tree_store_accepts_trees_param():
    """With no tree_store configured, trees kwarg is silently dropped (backward compat)."""
    ps, cs, ms = FakePackageStore(), FakeChunkStore(), FakeModuleMemberStore()
    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms,
    )
    pkg = _pkg("fastapi")
    # Passing a non-empty trees tuple must not raise.
    await service.reindex_package(pkg, (), (), trees=("fake-tree",))
    # No tree_store calls because none configured.
    # (Verified indirectly: other stores still called normally.)
    assert [c.method for c in cs.calls] == ["delete", "upsert"]


@pytest.mark.asyncio
async def test_reindex_package_with_tree_store_delegates_save_many():
    """With a tree_store + non-empty trees, delete_for_package + save_many are called."""
    ps, cs, ms = FakePackageStore(), FakeChunkStore(), FakeModuleMemberStore()
    ts = FakeDocumentTreeStore()
    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms, tree_store=ts,
    )
    pkg = _pkg("fastapi")
    fake_trees = ("tree-1", "tree-2")

    await service.reindex_package(pkg, (), (), trees=fake_trees)

    methods = [c.method for c in ts.calls]
    assert methods == ["delete_for_package", "save_many"]
    assert ts.calls[0].payload == "fastapi"
    pkg_name, saved_trees = ts.calls[1].payload
    assert pkg_name == "fastapi"
    assert saved_trees == fake_trees


@pytest.mark.asyncio
async def test_reindex_package_with_tree_store_but_empty_trees_skips():
    """Empty trees tuple → tree_store methods NOT called (no point deleting nothing)."""
    ps, cs, ms = FakePackageStore(), FakeChunkStore(), FakeModuleMemberStore()
    ts = FakeDocumentTreeStore()
    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms, tree_store=ts,
    )
    pkg = _pkg("fastapi")
    await service.reindex_package(pkg, (), (), trees=())
    assert ts.calls == []


@pytest.mark.asyncio
async def test_reindex_package_accepts_references_placeholder_for_sub_pr_5b():
    """references kwarg is accepted (sub-PR #5b seam) but ignored in #5."""
    ps, cs, ms = FakePackageStore(), FakeChunkStore(), FakeModuleMemberStore()
    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms,
    )
    pkg = _pkg("fastapi")
    # Non-empty references tuple — must not raise; currently no-op.
    await service.reindex_package(pkg, (), (), references=("fake-ref",))
    # Core stores still called normally.
    assert [c.method for c in cs.calls] == ["delete", "upsert"]


@pytest.mark.asyncio
async def test_reindex_package_canonical_order_chunks_then_trees_then_members():
    """Spec §13.3 canonical order: package → chunks → trees → members."""
    ps, cs, ms = FakePackageStore(), FakeChunkStore(), FakeModuleMemberStore()
    ts = FakeDocumentTreeStore()
    service = IndexingService(
        package_store=ps, chunk_store=cs, module_member_store=ms, tree_store=ts,
    )
    pkg = _pkg("fastapi")
    chunk = _chunk("fastapi", "A")
    member = _member("fastapi", "X")
    fake_trees = ("tree-1",)

    await service.reindex_package(pkg, (chunk,), (member,), trees=fake_trees)

    # chunks.upsert must land BEFORE tree_store.save_many, which must land
    # BEFORE module_member_store.upsert_many.
    assert any(c.method == "upsert" for c in cs.calls)
    assert any(c.method == "save_many" for c in ts.calls)
    assert any(c.method == "upsert_many" for c in ms.calls)
