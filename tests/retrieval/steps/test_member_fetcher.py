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
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult
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
    await repo.upsert_many(
        [
            _member("demo", "demo.m", "add", MemberKind.FUNCTION.value, "Adds two numbers."),
            _member("demo", "demo.m", "subtract", MemberKind.FUNCTION.value, "Subtracts."),
            _member("demo", "demo.m", "Adder", MemberKind.CLASS.value, "Stateful add helper."),
        ]
    )
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


async def test_member_fetcher_reads_pre_filter_from_scratch(populated_db: Path) -> None:
    """When PreFilterStep ran upstream and wrote PreFilterResult to
    state.scratch['pre_filter.result'], the fetcher consumes it directly without
    re-parsing state.query.pre_filter."""
    provider = build_connection_provider(populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter={"package": "demo"}),
    )
    # Simulate PreFilterStep having run upstream. Post-C5 commit 2 the
    # typed result carries only ``tree`` + ``scope``; the fetcher itself
    # calls ``ctx.filter_adapter.adapt`` to materialize SQL — so the
    # tree is what matters here, not a pre-computed SQL fragment.
    from pydocs_mcp.storage.filters import FieldEq

    state.scratch["pre_filter.result"] = PreFilterResult(
        tree=FieldEq(field="package", value="demo"),
        scope=None,
    )
    out = await step.run(state)
    # The fetcher used the pre-built SQL pushdown, didn't re-parse query.pre_filter.
    assert isinstance(out.candidates, ModuleMemberList)
    # All seeded members are in 'demo' package → all pass the pushdown.
    assert all(
        m.metadata.get(ModuleMemberFilterField.PACKAGE.value) == "demo"
        for m in out.candidates.items
    )


async def test_member_fetcher_raises_if_pre_filter_set_but_scratch_missing(
    populated_db: Path,
) -> None:
    """If state.query.pre_filter is set but PreFilterStep did NOT run
    upstream (scratch lacks 'pre_filter'), the fetcher raises a clear
    error pointing at the missing pipeline step."""
    provider = build_connection_provider(populated_db)
    step = MemberFetcherStep(name="fetch", provider=provider, limit=10)
    state = RetrieverState(
        query=SearchQuery(terms="add", max_results=10, pre_filter={"package": "demo"}),
    )
    # No state.scratch['pre_filter.result'] — PreFilterStep didn't run.
    with pytest.raises(RuntimeError, match="pre_filter"):
        await step.run(state)


def test_member_fetcher_keep_by_terms_drops_none_in_one_pass() -> None:
    """Regression: the two-step filter (build None-tagged tuple, then
    drop the Nones) widens the tuple element type to
    ``ModuleMember | None``. The one-pass walrus-filter form keeps the
    intermediate type as ``ModuleMember`` throughout, satisfying mypy
    without ``# type: ignore``.

    Pins the *behavior* (drops the non-matching member, keeps the
    matcher) so the refactor from two-pass to one-pass walrus is
    semantically a no-op.
    """
    from pydocs_mcp.retrieval.steps.member_fetcher import _keep_by_terms

    keep = ModuleMember(
        metadata={
            ModuleMemberFilterField.NAME.value: "matchme",
            ModuleMemberFilterField.PACKAGE.value: "p",
            ModuleMemberFilterField.MODULE.value: "m",
            ModuleMemberFilterField.KIND.value: MemberKind.FUNCTION.value,
            "signature": "()",
            "return_annotation": "",
            "parameters": (),
            "docstring": "",
        },
    )
    drop = ModuleMember(
        metadata={
            ModuleMemberFilterField.NAME.value: "other",
            ModuleMemberFilterField.PACKAGE.value: "p",
            ModuleMemberFilterField.MODULE.value: "m",
            ModuleMemberFilterField.KIND.value: MemberKind.FUNCTION.value,
            "signature": "()",
            "return_annotation": "",
            "parameters": (),
            "docstring": "",
        },
    )
    members = (keep, drop)
    # The one-pass form: equivalent semantics, narrower static type.
    filtered = tuple(kept for m in members if (kept := _keep_by_terms(m, "match")) is not None)
    assert filtered == (keep,)
