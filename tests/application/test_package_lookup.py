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
