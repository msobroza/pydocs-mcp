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


@pytest.mark.asyncio
async def test_parallel_retrieval_stage_runs_branches_concurrently():
    """Each inner stage sees the same input state. Their results are CONCATENATED."""
    from pydocs_mcp.retrieval.stages import ParallelRetrievalStage

    @dataclass(frozen=True, slots=True)
    class _AppendA:
        name: str = "append_a"
        async def run(self, state):
            existing = state.result.items if state.result else ()
            return replace(state, result=ChunkList(items=existing + (Chunk(text="A"),)))

    @dataclass(frozen=True, slots=True)
    class _AppendB:
        name: str = "append_b"
        async def run(self, state):
            existing = state.result.items if state.result else ()
            return replace(state, result=ChunkList(items=existing + (Chunk(text="B"),)))

    from dataclasses import replace
    stage = ParallelRetrievalStage(stages=(_AppendA(), _AppendB()))
    state = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    texts = [c.text for c in state.result.items]
    # Both branch contributions should be present (order depends on gather)
    assert set(texts) == {"A", "B"}


@pytest.mark.asyncio
async def test_reciprocal_rank_fusion_basic():
    from pydocs_mcp.retrieval.stages import ReciprocalRankFusionStage

    # 4 chunks, 2 duplicates — RRF sums 1/(k+rank) across duplicates
    # The duplicate should rank higher than singletons
    items = (
        Chunk(text="a", id=1),
        Chunk(text="b", id=2),
        Chunk(text="a", id=1),  # duplicate of #1 at a lower initial position
    )
    state = PipelineState(query=SearchQuery(terms="x"), result=ChunkList(items=items))
    out = await ReciprocalRankFusionStage(k=60).run(state)
    # "a" (id=1) has 2 appearances; its RRF score is strictly higher than "b"'s single.
    assert out.result.items[0].id == 1
    # Duplicates deduplicated by id
    ids = [c.id for c in out.result.items]
    assert ids.count(1) == 1
