"""Tests for SqliteModuleMemberRepository (spec §5.3)."""
from __future__ import annotations

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import MemberKind, ModuleMember, ModuleMemberFilterField
from pydocs_mcp.storage.sqlite import SqliteModuleMemberRepository


@pytest.fixture
def db_file(tmp_path):
    f = tmp_path / "members.db"
    open_index_database(f).close()
    return f


def _member(package: str, module: str, name: str, kind: str) -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.MODULE.value: module,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.KIND.value: kind,
            "signature": "()",
            "return_annotation": "",
            "parameters": (),
            "docstring": "",
        },
    )


async def test_member_repository_upsert_many_and_list(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteModuleMemberRepository(provider=provider)
    await repo.upsert_many([
        _member("fastapi", "fastapi.routing", "APIRouter", MemberKind.CLASS.value),
        _member("fastapi", "fastapi.security", "OAuth2", MemberKind.CLASS.value),
        _member("requests", "requests.api", "get", MemberKind.FUNCTION.value),
    ])

    all_members = await repo.list()
    assert len(all_members) == 3
    names = {m.metadata["name"] for m in all_members}
    assert names == {"APIRouter", "OAuth2", "get"}


async def test_member_repository_delete_by_package(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteModuleMemberRepository(provider=provider)
    await repo.upsert_many([
        _member("fastapi", "fastapi.routing", "APIRouter", MemberKind.CLASS.value),
        _member("fastapi", "fastapi.security", "OAuth2", MemberKind.CLASS.value),
        _member("requests", "requests.api", "get", MemberKind.FUNCTION.value),
    ])
    assert await repo.count() == 3

    deleted = await repo.delete({"package": "fastapi"})
    assert deleted == 2
    assert await repo.count() == 1
    remaining = await repo.list()
    assert remaining[0].metadata["package"] == "requests"


async def test_member_repository_filter_by_kind(db_file):
    provider = build_connection_provider(db_file)
    repo = SqliteModuleMemberRepository(provider=provider)
    await repo.upsert_many([
        _member("fastapi", "fastapi.routing", "APIRouter", MemberKind.CLASS.value),
        _member("fastapi", "fastapi.security", "OAuth2", MemberKind.CLASS.value),
        _member("requests", "requests.api", "get", MemberKind.FUNCTION.value),
    ])

    classes = await repo.list(filter={"kind": MemberKind.CLASS.value})
    assert len(classes) == 2
    assert all(m.metadata["kind"] == MemberKind.CLASS.value for m in classes)

    functions = await repo.list(filter={"kind": MemberKind.FUNCTION.value})
    assert len(functions) == 1
    assert functions[0].metadata["name"] == "get"
