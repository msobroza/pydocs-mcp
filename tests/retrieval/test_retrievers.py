"""Tests for concrete retrievers against store Protocol fakes."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.retrievers import Bm25ChunkRetriever, LikeMemberRetriever
from pydocs_mcp.storage.filters import Filter


# ── Protocol fakes ──────────────────────────────────────────────────────


@dataclass
class _FakeTextSearchable:
    """Minimal TextSearchable that records arguments and returns seeded rows."""
    rows: tuple[Chunk, ...] = ()
    recorded: list[dict] = field(default_factory=list)

    async def text_search(
        self,
        query_terms: str,
        limit: int,
        filter: Filter | Mapping | None = None,
    ) -> tuple[Chunk, ...]:
        self.recorded.append({"query_terms": query_terms, "limit": limit, "filter": filter})
        return self.rows


@dataclass
class _FakeModuleMemberStore:
    """Minimal ModuleMemberStore used by LikeMemberRetriever tests."""
    rows: tuple[ModuleMember, ...] = ()
    recorded: list[dict] = field(default_factory=list)

    async def list(
        self,
        filter: Filter | Mapping | None = None,
        limit: int | None = None,
    ) -> list[ModuleMember]:
        self.recorded.append({"filter": filter, "limit": limit})
        return list(self.rows)


# ── Bm25ChunkRetriever ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_wraps_store_rows_in_chunk_list():
    store = _FakeTextSearchable(
        rows=(
            Chunk(
                text="body",
                id=7,
                relevance=3.14,
                retriever_name="bm25_chunk",
                metadata={ChunkFilterField.PACKAGE.value: "fastapi"},
            ),
        ),
    )
    retriever = Bm25ChunkRetriever(
        store=store, allowed_fields=frozenset({"package", "scope", "title"})
    )
    result = await retriever.retrieve(SearchQuery(terms="APIRouter"))
    assert isinstance(result, ChunkList)
    assert len(result.items) == 1
    assert store.recorded[0]["query_terms"] == "APIRouter"
    assert store.recorded[0]["filter"] is None


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_pushes_parsed_pre_filter_to_store():
    store = _FakeTextSearchable()
    retriever = Bm25ChunkRetriever(
        store=store, allowed_fields=frozenset({"package", "scope"})
    )
    await retriever.retrieve(SearchQuery(
        terms="x",
        pre_filter={"package": "fastapi"},
    ))
    tree = store.recorded[0]["filter"]
    assert tree is not None  # a Filter tree, not the raw dict


@pytest.mark.asyncio
async def test_bm25_chunk_retriever_rejects_filter_fields_outside_allowlist():
    store = _FakeTextSearchable()
    retriever = Bm25ChunkRetriever(
        store=store, allowed_fields=frozenset({"package"})
    )
    with pytest.raises(ValueError, match="unknown fields"):
        await retriever.retrieve(SearchQuery(
            terms="x",
            pre_filter={"title": {"like": "Routing"}},
        ))


# ── LikeMemberRetriever ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_like_member_retriever_filters_rows_by_terms_substring():
    store = _FakeModuleMemberStore(rows=(
        ModuleMember(id=1, metadata={
            ModuleMemberFilterField.NAME.value: "APIRouter",
            "docstring": "Groups endpoints.",
        }),
        ModuleMember(id=2, metadata={
            ModuleMemberFilterField.NAME.value: "Middleware",
            "docstring": "Unrelated.",
        }),
    ))
    retriever = LikeMemberRetriever(
        store=store, allowed_fields=frozenset({"package", "module", "name", "kind"})
    )
    result = await retriever.retrieve(SearchQuery(terms="APIRouter"))
    assert isinstance(result, ModuleMemberList)
    assert len(result.items) == 1
    assert result.items[0].retriever_name == "like_member"
    assert result.items[0].metadata[ModuleMemberFilterField.NAME.value] == "APIRouter"


@pytest.mark.asyncio
async def test_like_member_retriever_pushes_pre_filter_to_store_list():
    store = _FakeModuleMemberStore(rows=(
        ModuleMember(id=1, metadata={
            ModuleMemberFilterField.NAME.value: "APIRouter",
            "docstring": "",
        }),
    ))
    retriever = LikeMemberRetriever(
        store=store, allowed_fields=frozenset({"package", "module", "name", "kind"})
    )
    await retriever.retrieve(SearchQuery(
        terms="APIRouter",
        pre_filter={"package": "fastapi"},
    ))
    assert store.recorded[0]["filter"] is not None
    assert store.recorded[0]["limit"] == 8


@pytest.mark.asyncio
async def test_like_member_retriever_rejects_filter_fields_outside_allowlist():
    store = _FakeModuleMemberStore()
    retriever = LikeMemberRetriever(
        store=store, allowed_fields=frozenset({"package", "module"})
    )
    with pytest.raises(ValueError, match="unknown fields"):
        await retriever.retrieve(SearchQuery(
            terms="x",
            pre_filter={"kind": "class"},
        ))


# ── Pipeline adapter retrievers ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_chunk_retriever_forwards_to_inner_pipeline(tmp_path):
    """Adapter runs the inner pipeline and returns the ChunkList at state.result."""
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
    from pydocs_mcp.retrieval.retrievers import PipelineChunkRetriever

    @dataclass(frozen=True, slots=True)
    class _ReturnOneChunk:
        name: str = "return_one"
        async def run(self, state: PipelineState) -> PipelineState:
            return PipelineState(
                query=state.query,
                result=ChunkList(items=(Chunk(text="payload"),)),
            )

    inner = CodeRetrieverPipeline(name="inner", stages=(_ReturnOneChunk(),))
    adapter = PipelineChunkRetriever(pipeline=inner)
    out = await adapter.retrieve(SearchQuery(terms="x"))
    assert isinstance(out, ChunkList)
    assert len(out.items) == 1
    assert out.items[0].text == "payload"


@pytest.mark.asyncio
async def test_pipeline_module_member_retriever_forwards():
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState
    from pydocs_mcp.retrieval.retrievers import PipelineModuleMemberRetriever

    @dataclass(frozen=True, slots=True)
    class _ReturnOneMember:
        name: str = "return_one"
        async def run(self, state: PipelineState) -> PipelineState:
            return PipelineState(
                query=state.query,
                result=ModuleMemberList(items=(ModuleMember(metadata={"name": "f"}),)),
            )

    inner = CodeRetrieverPipeline(name="inner", stages=(_ReturnOneMember(),))
    adapter = PipelineModuleMemberRetriever(pipeline=inner)
    out = await adapter.retrieve(SearchQuery(terms="x"))
    assert isinstance(out, ModuleMemberList)
    assert len(out.items) == 1


@pytest.mark.asyncio
async def test_scope_filter_accepts_field_in():
    """``{"scope": {"in": [...]}}`` must be stripped from the pushed-down
    filter tree just like the singleton ``{"scope": "x"}`` form — otherwise
    the store's safe-column gate raises ``ValueError("column 'scope' not in
    safe_columns")`` because ``scope`` is a semantic field, not a DB column.
    """
    store = _FakeTextSearchable(
        rows=(
            Chunk(
                text="project body",
                metadata={ChunkFilterField.PACKAGE.value: "__project__"},
            ),
            Chunk(
                text="dep body",
                metadata={ChunkFilterField.PACKAGE.value: "fastapi"},
            ),
        ),
    )
    retriever = Bm25ChunkRetriever(
        store=store,
        allowed_fields=frozenset({"package", "scope"}),
    )

    # Must not raise: scope gets split out of the filter tree.
    result = await retriever.retrieve(SearchQuery(
        terms="body",
        pre_filter={"scope": {"in": ["project_only", "all"]}},
    ))

    # The store received filter=None (scope was popped off; no other clauses).
    assert store.recorded[0]["filter"] is None
    # Both rows match: ``all`` covers everything.
    assert len(result.items) == 2
