"""Tests for stage classes — Part 1 (retrieval + filters + limit)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
    SearchScope,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.stages import (
    ChunkRetrievalStage,
    LimitStage,
    ModuleMemberRetrievalStage,
    PackageFilterStage,
    ScopeFilterStage,
    TitleFilterStage,
)


@dataclass(frozen=True, slots=True)
class _StaticChunkRetriever:
    name: str = "static_chunk"
    _payload: tuple[Chunk, ...] = ()
    async def retrieve(self, query): return ChunkList(items=self._payload)


@dataclass(frozen=True, slots=True)
class _StaticMemberRetriever:
    name: str = "static_member"
    _payload: tuple[ModuleMember, ...] = ()
    async def retrieve(self, query): return ModuleMemberList(items=self._payload)


@pytest.mark.asyncio
async def test_chunk_retrieval_stage_sets_result():
    stage = ChunkRetrievalStage(retriever=_StaticChunkRetriever(_payload=(Chunk(text="a"),)))
    state = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert isinstance(state.result, ChunkList)
    assert state.result.items[0].text == "a"


@pytest.mark.asyncio
async def test_member_retrieval_stage_sets_result():
    stage = ModuleMemberRetrievalStage(
        retriever=_StaticMemberRetriever(_payload=(ModuleMember(metadata={"n": "f"}),))
    )
    state = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert isinstance(state.result, ModuleMemberList)


@pytest.mark.asyncio
async def test_package_filter_stage_keeps_matching_package():
    payload = ChunkList(items=(
        Chunk(text="a", metadata={ChunkFilterField.PACKAGE.value: "keep"}),
        Chunk(text="b", metadata={ChunkFilterField.PACKAGE.value: "drop"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", pre_filter={ChunkFilterField.PACKAGE.value: "keep"}),
        result=payload,
    )
    out = await PackageFilterStage().run(state)
    assert len(out.result.items) == 1
    assert out.result.items[0].text == "a"


@pytest.mark.asyncio
async def test_package_filter_stage_no_filter_is_noop():
    payload = ChunkList(items=(Chunk(text="a"), Chunk(text="b")))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await PackageFilterStage().run(state)
    assert len(out.result.items) == 2


@pytest.mark.asyncio
async def test_scope_filter_stage_project_only():
    payload = ChunkList(items=(
        Chunk(text="proj", metadata={ChunkFilterField.PACKAGE.value: "__project__"}),
        Chunk(text="dep", metadata={ChunkFilterField.PACKAGE.value: "fastapi"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", pre_filter={ChunkFilterField.SCOPE.value: SearchScope.PROJECT_ONLY.value}),
        result=payload,
    )
    out = await ScopeFilterStage().run(state)
    assert len(out.result.items) == 1
    assert out.result.items[0].text == "proj"


@pytest.mark.asyncio
async def test_title_filter_stage_substring_match():
    payload = ChunkList(items=(
        Chunk(text="a", metadata={ChunkFilterField.TITLE.value: "Routing"}),
        Chunk(text="b", metadata={ChunkFilterField.TITLE.value: "Middleware"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", pre_filter={ChunkFilterField.TITLE.value: "rout"}),
        result=payload,
    )
    out = await TitleFilterStage().run(state)
    assert len(out.result.items) == 1


@pytest.mark.asyncio
async def test_limit_stage_truncates():
    payload = ChunkList(items=tuple(Chunk(text=str(i)) for i in range(10)))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await LimitStage(max_results=3).run(state)
    assert len(out.result.items) == 3


@pytest.mark.asyncio
async def test_limit_stage_default_eight():
    payload = ChunkList(items=tuple(Chunk(text=str(i)) for i in range(20)))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await LimitStage().run(state)
    assert len(out.result.items) == 8
