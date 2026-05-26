"""UnitOfWork.delete_all — atomic destructive sweep (spec I3).

Pins that calling :meth:`UnitOfWork.delete_all` clears every row across
every entity store atomically (within the single transaction). Lets
:meth:`IndexingService.clear_all` express its intent in one line and
makes a hypothetical Postgres / DuckDB adapter only need to satisfy
this Protocol method.
"""
from __future__ import annotations

import pytest

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ModuleMember,
    ModuleMemberFilterField,
    Package,
    PackageOrigin,
)
from tests._fakes import (
    InMemoryChunkStore,
    InMemoryModuleMemberStore,
    InMemoryPackageStore,
    make_fake_uow_factory,
)


def _make_chunk(*, package: str, title: str) -> Chunk:
    return Chunk(
        text="payload",
        metadata={
            ChunkFilterField.PACKAGE.value: package,
            ChunkFilterField.TITLE.value: title,
        },
    )


def _make_member(*, package: str, module: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.MODULE.value: module,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.KIND.value: "function",
        },
    )


@pytest.mark.asyncio
async def test_sqlite_uow_delete_all_wipes_every_table(tmp_path):
    """End-to-end SqliteUnitOfWork.delete_all wipes every table."""
    from pydocs_mcp.db import open_index_database
    from pydocs_mcp.extraction.reference_kind import ReferenceKind
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.storage.node_reference import NodeReference
    from pydocs_mcp.storage.sqlite import SqliteUnitOfWork

    db = tmp_path / "x.db"
    open_index_database(db).close()
    provider = PerCallConnectionProvider(cache_path=db)

    async with SqliteUnitOfWork(provider=provider) as uow:
        await uow.packages.upsert(
            Package(
                name="p", version="1", summary="", homepage="",
                dependencies=(), content_hash="h",
                origin=PackageOrigin.DEPENDENCY,
            ),
        )
        await uow.chunks.upsert([_make_chunk(package="p", title="t")])
        await uow.module_members.upsert_many(
            [_make_member(package="p", module="p.m", name="f")],
        )
        await uow.references.save_many(
            [
                NodeReference(
                    from_package="p",
                    from_node_id="p.m.f",
                    to_name="target",
                    to_node_id=None,
                    kind=ReferenceKind.CALLS,
                )
            ],
            package="p",
        )
        await uow.commit()

    async with SqliteUnitOfWork(provider=provider) as uow:
        await uow.delete_all()
        await uow.commit()

    async with SqliteUnitOfWork(provider=provider) as uow:
        assert await uow.packages.list() == []
        assert await uow.chunks.list() == []
        assert await uow.module_members.list() == []
        assert await uow.references.find_by_name("target") == []


@pytest.mark.asyncio
async def test_uow_delete_all_wipes_every_store():
    """delete_all clears packages, chunks, module_members, trees, references."""
    pkgs = InMemoryPackageStore()
    pkgs.items["p"] = Package(
        name="p",
        version="1",
        summary="",
        homepage="",
        dependencies=(),
        content_hash="h",
        origin=PackageOrigin.DEPENDENCY,
    )
    chunks = InMemoryChunkStore()
    chunks.by_package.setdefault("p", []).append(_make_chunk(package="p", title="t"))
    members = InMemoryModuleMemberStore()
    members.by_package.setdefault("p", []).append(
        _make_member(package="p", module="p.m", name="f"),
    )
    uow_factory = make_fake_uow_factory(
        packages=pkgs, chunks=chunks, module_members=members,
    )

    async with uow_factory() as uow:
        await uow.delete_all()
        await uow.commit()

    async with uow_factory() as uow:
        assert await uow.packages.list() == []
        assert await uow.chunks.list() == []
        assert await uow.module_members.list() == []
        assert await uow.trees.load_all_in_package("p") == {}
