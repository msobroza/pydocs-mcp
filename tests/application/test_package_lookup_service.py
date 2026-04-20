"""Tests for PackageLookupService — list_packages + get_package_doc (spec §5.1).

Uses in-memory fakes local to this file (per Task 5 plan; shared conftest
fixtures land in Task 14). The fakes rely on structural duck typing — the
Protocols in ``storage/protocols.py`` are ``runtime_checkable`` and the
service only calls ``list`` / ``get``, so we implement just what's needed
plus a few capture fields to let the tests assert call arguments.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from pydocs_mcp.application.package_lookup_service import PackageLookupService
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


# ── Helpers ────────────────────────────────────────────────────────────────


def _pkg(name: str) -> Package:
    return Package(
        name=name,
        version="1.0.0",
        summary=f"{name} summary",
        homepage="",
        dependencies=(),
        content_hash="deadbeef",
        origin=PackageOrigin.DEPENDENCY,
    )


def _chunk(package: str, title: str) -> Chunk:
    return Chunk(
        text=f"{title} body",
        metadata={
            ChunkFilterField.PACKAGE.value: package,
            ChunkFilterField.TITLE.value: title,
        },
    )


def _member(package: str, name: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.MODULE.value: f"{package}.core",
            ModuleMemberFilterField.KIND.value: MemberKind.FUNCTION.value,
        }
    )


# ── Fakes ──────────────────────────────────────────────────────────────────


@dataclass
class FakePackageStore:
    packages: dict[str, Package] = field(default_factory=dict)
    last_list_kwargs: dict[str, Any] = field(default_factory=dict)
    list_call_count: int = 0
    get_call_count: int = 0

    async def get(self, name: str) -> Package | None:
        self.get_call_count += 1
        return self.packages.get(name)

    async def list(self, **kwargs: Any) -> list[Package]:
        self.list_call_count += 1
        self.last_list_kwargs = kwargs
        return list(self.packages.values())

    async def upsert(self, package: Package) -> None:
        self.packages[package.name] = package

    async def delete(self, filter: Any) -> int:  # noqa: ARG002
        return 0

    async def count(self, filter: Any = None) -> int:  # noqa: ARG002
        return len(self.packages)


@dataclass
class FakeChunkStore:
    chunks: list[Chunk] = field(default_factory=list)
    last_list_kwargs: dict[str, Any] = field(default_factory=dict)
    list_call_count: int = 0

    async def list(self, **kwargs: Any) -> list[Chunk]:
        self.list_call_count += 1
        self.last_list_kwargs = kwargs
        return list(self.chunks)

    async def upsert(self, chunks: Any) -> None:  # noqa: ARG002
        return None

    async def delete(self, filter: Any) -> int:  # noqa: ARG002
        return 0

    async def count(self, filter: Any = None) -> int:  # noqa: ARG002
        return len(self.chunks)

    async def rebuild_index(self) -> None:
        return None


@dataclass
class FakeModuleMemberStore:
    members: list[ModuleMember] = field(default_factory=list)
    last_list_kwargs: dict[str, Any] = field(default_factory=dict)
    list_call_count: int = 0

    async def list(self, **kwargs: Any) -> list[ModuleMember]:
        self.list_call_count += 1
        self.last_list_kwargs = kwargs
        return list(self.members)

    async def upsert_many(self, members: Any) -> None:  # noqa: ARG002
        return None

    async def delete(self, filter: Any) -> int:  # noqa: ARG002
        return 0

    async def count(self, filter: Any = None) -> int:  # noqa: ARG002
        return len(self.members)


def _service(
    *,
    packages: dict[str, Package] | None = None,
    chunks: list[Chunk] | None = None,
    members: list[ModuleMember] | None = None,
) -> tuple[PackageLookupService, FakePackageStore, FakeChunkStore, FakeModuleMemberStore]:
    pkg_store = FakePackageStore(packages=dict(packages or {}))
    chunk_store = FakeChunkStore(chunks=list(chunks or []))
    member_store = FakeModuleMemberStore(members=list(members or []))
    svc = PackageLookupService(
        package_store=pkg_store,
        chunk_store=chunk_store,
        module_member_store=member_store,
    )
    return svc, pkg_store, chunk_store, member_store


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_packages_returns_tuple() -> None:
    svc, _, _, _ = _service(packages={"foo": _pkg("foo"), "bar": _pkg("bar")})

    result = await svc.list_packages()

    assert isinstance(result, tuple)
    assert {p.name for p in result} == {"foo", "bar"}


@pytest.mark.asyncio
async def test_list_packages_respects_limit() -> None:
    svc, pkg_store, _, _ = _service(packages={"foo": _pkg("foo")})

    await svc.list_packages()

    assert pkg_store.last_list_kwargs == {"limit": 200}
    assert pkg_store.list_call_count == 1


@pytest.mark.asyncio
async def test_get_package_doc_missing_returns_none() -> None:
    svc, pkg_store, chunk_store, member_store = _service(packages={})

    result = await svc.get_package_doc("ghost")

    assert result is None
    assert pkg_store.get_call_count == 1
    # When the package is missing the service must short-circuit — neither
    # dependent store should be queried.
    assert chunk_store.list_call_count == 0
    assert member_store.list_call_count == 0


@pytest.mark.asyncio
async def test_get_package_doc_composes_all_three_stores() -> None:
    pkg = _pkg("foo")
    chunks = [_chunk("foo", "overview"), _chunk("foo", "api")]
    members = [_member("foo", "run"), _member("foo", "init")]
    svc, pkg_store, chunk_store, member_store = _service(
        packages={"foo": pkg}, chunks=chunks, members=members,
    )

    result = await svc.get_package_doc("foo")

    assert isinstance(result, PackageDoc)
    assert result.package is pkg
    assert result.chunks == tuple(chunks)
    assert result.members == tuple(members)
    # Spec-mandated per-call limits.
    assert chunk_store.last_list_kwargs.get("limit") == 10
    assert member_store.last_list_kwargs.get("limit") == 30
    assert pkg_store.get_call_count == 1


@pytest.mark.asyncio
async def test_get_package_doc_passes_enum_filter_keys() -> None:
    svc, _, chunk_store, member_store = _service(packages={"foo": _pkg("foo")})

    await svc.get_package_doc("foo")

    chunk_filter = chunk_store.last_list_kwargs.get("filter")
    member_filter = member_store.last_list_kwargs.get("filter")
    assert chunk_filter == {ChunkFilterField.PACKAGE.value: "foo"}
    assert member_filter == {ModuleMemberFilterField.PACKAGE.value: "foo"}
    # The enum values are the literal wire keys — assert both explicitly so
    # any rename of the enum surfaces here, not only in integration tests.
    assert "package" in chunk_filter
    assert "package" in member_filter


def test_service_is_frozen_slotted_dataclass() -> None:
    svc, _, _, _ = _service()

    with pytest.raises((AttributeError, Exception)):
        svc.package_store = None  # type: ignore[misc]
    assert not hasattr(svc, "__dict__")
