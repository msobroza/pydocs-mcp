"""MemberFetcherStep tests — issues a LIKE query, returns candidates without ranks.

Mirrors :mod:`tests.retrieval.steps.test_chunk_fetcher` but for the member side.
LIKE doesn't produce relevance ranks, so candidates are returned in source order
with ``relevance is None``; downstream :class:`TopKFilterStep` handles the cap.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_mcp.db import build_connection_provider, open_index_database
from pydocs_mcp.models import (
    MemberKind,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.member_fetcher import MemberFetcherStep
from pydocs_mcp.storage.sqlite import SqliteModuleMemberRepository


def _member(package: str, module: str, name: str, kind: str, docstring: str = "") -> ModuleMember:
    return ModuleMember(
        metadata={
            ModuleMemberFilterField.PACKAGE.value: package,
            ModuleMemberFilterField.MODULE.value: module,
            ModuleMemberFilterField.NAME.value: name,
            ModuleMemberFilterField.KIND.value: kind,
            "signature": "()",
            "return_annotation": "",
            "parameters": (),
            "docstring": docstring,
        },
    )


@pytest.fixture
async def populated_db(tmp_path: Path) -> Path:
    """A small SQLite with module_members rows populated via the canonical repo path."""
    db_path = tmp_path / "fixtures.db"
    open_index_database(db_path).close()
    provider = build_connection_provider(db_path)
    repo = SqliteModuleMemberRepository(provider=provider)
    await repo.upsert_many([
        _member("demo", "demo.m", "add", MemberKind.FUNCTION.value, "Adds two numbers."),
        _member("demo", "demo.m", "subtract", MemberKind.FUNCTION.value, "Subtracts."),
        _member("demo", "demo.m", "Adder", MemberKind.CLASS.value, "Stateful add helper."),
    ])
    return db_path


async def test_member_fetcher_returns_matching_members(populated_db: Path) -> None:
    """A LIKE-style query for 'add' returns ≥1 candidate whose name contains 'add'."""
    provider = build_connection_provider(populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert isinstance(out.candidates, ModuleMemberList)
    assert len(out.candidates.items) >= 1
    names = [
        str(m.metadata.get(ModuleMemberFilterField.NAME.value, "")).lower()
        for m in out.candidates.items
    ]
    assert any("add" in n for n in names)


async def test_member_fetcher_respects_limit(populated_db: Path) -> None:
    """limit caps the returned candidate count."""
    provider = build_connection_provider(populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=1)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert isinstance(out.candidates, ModuleMemberList)
    assert len(out.candidates.items) <= 1


async def test_member_fetcher_satisfies_retriever_step_protocol(populated_db: Path) -> None:
    """MemberFetcherStep is a RetrieverStep and writes ModuleMemberList candidates."""
    provider = build_connection_provider(populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=10)
    assert isinstance(step, RetrieverStep)
    state = RetrieverState(query=SearchQuery(terms="add", max_results=10))
    out = await step.run(state)
    assert isinstance(out.candidates, ModuleMemberList)
