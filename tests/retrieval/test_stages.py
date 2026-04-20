"""Tests for stage classes — Part 1 (retrieval + post-filter + limit)."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from pydocs_mcp.models import (
    Chunk,
    ChunkFilterField,
    ChunkList,
    ChunkOrigin,
    ModuleMember,
    ModuleMemberFilterField,
    ModuleMemberList,
    SearchQuery,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.stages import (
    ChunkRetrievalStage,
    LimitStage,
    MetadataPostFilterStage,
    ModuleMemberRetrievalStage,
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
async def test_metadata_post_filter_stage_noop_when_post_filter_none():
    payload = ChunkList(items=(Chunk(text="a"), Chunk(text="b")))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await MetadataPostFilterStage().run(state)
    assert len(out.result.items) == 2


@pytest.mark.asyncio
async def test_metadata_post_filter_stage_filters_chunks_by_eq():
    payload = ChunkList(items=(
        Chunk(text="a", metadata={ChunkFilterField.PACKAGE.value: "fastapi"}),
        Chunk(text="b", metadata={ChunkFilterField.PACKAGE.value: "django"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", post_filter={"package": "fastapi"}),
        result=payload,
    )
    out = await MetadataPostFilterStage().run(state)
    assert len(out.result.items) == 1
    assert out.result.items[0].text == "a"


@pytest.mark.asyncio
async def test_metadata_post_filter_stage_filters_by_like():
    payload = ChunkList(items=(
        Chunk(text="a", metadata={ChunkFilterField.TITLE.value: "Routing"}),
        Chunk(text="b", metadata={ChunkFilterField.TITLE.value: "Middleware"}),
    ))
    state = PipelineState(
        query=SearchQuery(terms="x", post_filter={"title": {"like": "rout"}}),
        result=payload,
    )
    out = await MetadataPostFilterStage().run(state)
    assert len(out.result.items) == 1
    assert out.result.items[0].text == "a"


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


@pytest.mark.asyncio
async def test_conditional_stage_runs_when_predicate_true():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import ConditionalStage

    registry = PredicateRegistry()

    @predicate("always", registry=registry)
    def _true(state): return True

    @dataclass(frozen=True, slots=True)
    class _Sentinel:
        name: str = "sentinel"
        async def run(self, state):
            return replace(state, result=ChunkList(items=(Chunk(text="fired"),)))

    from dataclasses import replace
    stage = ConditionalStage(stage=_Sentinel(), predicate_name="always", registry=registry)
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result.items[0].text == "fired"


@pytest.mark.asyncio
async def test_conditional_stage_skipped_when_predicate_false():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import ConditionalStage

    registry = PredicateRegistry()

    @predicate("never", registry=registry)
    def _false(state): return False

    @dataclass(frozen=True, slots=True)
    class _Sentinel:
        name: str = "sentinel"
        async def run(self, state): raise AssertionError("should not run")

    stage = ConditionalStage(stage=_Sentinel(), predicate_name="never", registry=registry)
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result is None  # state unchanged


@pytest.mark.asyncio
async def test_route_stage_first_match_wins():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import RouteCase, RouteStage

    registry = PredicateRegistry()

    @predicate("always", registry=registry)
    def _t1(s): return True

    @predicate("also_always", registry=registry)
    def _t2(s): return True

    @dataclass(frozen=True, slots=True)
    class _Tag:
        tag: str
        name: str = "tag"
        async def run(self, state):
            return replace(state, result=ChunkList(items=(Chunk(text=self.tag),)))

    from dataclasses import replace
    stage = RouteStage(
        routes=(
            RouteCase(predicate_name="always", stage=_Tag("first")),
            RouteCase(predicate_name="also_always", stage=_Tag("second")),
        ),
        registry=registry,
    )
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result.items[0].text == "first"


@pytest.mark.asyncio
async def test_route_stage_falls_through_to_default():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import RouteCase, RouteStage

    registry = PredicateRegistry()

    @predicate("never", registry=registry)
    def _f(s): return False

    @dataclass(frozen=True, slots=True)
    class _Tag:
        tag: str
        name: str = "tag"
        async def run(self, state):
            return replace(state, result=ChunkList(items=(Chunk(text=self.tag),)))

    from dataclasses import replace
    stage = RouteStage(
        routes=(RouteCase(predicate_name="never", stage=_Tag("route")),),
        default=_Tag("fallback"),
        registry=registry,
    )
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result.items[0].text == "fallback"


@pytest.mark.asyncio
async def test_route_stage_no_match_no_default_is_noop():
    from pydocs_mcp.retrieval.predicates import PredicateRegistry, predicate
    from pydocs_mcp.retrieval.stages import RouteStage

    registry = PredicateRegistry()

    @predicate("never", registry=registry)
    def _f(s): return False

    stage = RouteStage(routes=(), default=None, registry=registry)
    out = await stage.run(PipelineState(query=SearchQuery(terms="x")))
    assert out.result is None


@pytest.mark.asyncio
async def test_sub_pipeline_stage_runs_nested_stages_on_incoming_state():
    from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline
    from pydocs_mcp.retrieval.stages import SubPipelineStage

    @dataclass(frozen=True, slots=True)
    class _Tag:
        tag: str
        name: str = "tag"
        async def run(self, state):
            existing = state.result.items if state.result else ()
            return replace(state, result=ChunkList(items=existing + (Chunk(text=self.tag),)))

    from dataclasses import replace
    nested = CodeRetrieverPipeline(name="n", stages=(_Tag("inner1"), _Tag("inner2")))
    state = PipelineState(
        query=SearchQuery(terms="x"),
        result=ChunkList(items=(Chunk(text="pre"),)),  # incoming state is preserved
    )
    out = await SubPipelineStage(pipeline=nested).run(state)
    texts = [c.text for c in out.result.items]
    assert texts == ["pre", "inner1", "inner2"]  # state was threaded, not reset


@pytest.mark.asyncio
async def test_token_budget_formatter_stage_composite_output():
    from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter
    from pydocs_mcp.retrieval.stages import TokenBudgetFormatterStage

    payload = ChunkList(items=(
        Chunk(text="abc", metadata={ChunkFilterField.TITLE.value: "A"}),
        Chunk(text="def", metadata={ChunkFilterField.TITLE.value: "B"}),
    ))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await TokenBudgetFormatterStage(
        formatter=ChunkMarkdownFormatter(),
        budget=10_000,
    ).run(state)
    # Result is a ChunkList of length 1 whose metadata origin is COMPOSITE_OUTPUT
    assert isinstance(out.result, ChunkList)
    assert len(out.result.items) == 1
    composite = out.result.items[0]
    assert composite.metadata[ChunkFilterField.ORIGIN.value] == ChunkOrigin.COMPOSITE_OUTPUT.value
    assert "## A" in composite.text
    assert "## B" in composite.text


@pytest.mark.asyncio
async def test_token_budget_formatter_respects_budget():
    from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter
    from pydocs_mcp.retrieval.stages import TokenBudgetFormatterStage

    # 100 chunks * ~10-byte render ≈ 1000 bytes. Budget = 50 tokens ≈ 200 bytes (cut early).
    payload = ChunkList(items=tuple(
        Chunk(text="x" * 20, metadata={ChunkFilterField.TITLE.value: f"T{i}"})
        for i in range(100)
    ))
    state = PipelineState(query=SearchQuery(terms="x"), result=payload)
    out = await TokenBudgetFormatterStage(
        formatter=ChunkMarkdownFormatter(),
        budget=50,
    ).run(state)
    composite = out.result.items[0]
    assert len(composite.text) <= 50 * 4 + 200  # budget is bytes, 4 bytes/token, some slack


@pytest.mark.asyncio
async def test_token_budget_formatter_none_result_noop():
    from pydocs_mcp.retrieval.formatters import ChunkMarkdownFormatter
    from pydocs_mcp.retrieval.stages import TokenBudgetFormatterStage

    state = PipelineState(query=SearchQuery(terms="x"), result=None)
    out = await TokenBudgetFormatterStage(
        formatter=ChunkMarkdownFormatter(),
        budget=1000,
    ).run(state)
    assert out.result is None


@pytest.mark.asyncio
async def test_parallel_retrieval_stage_preserves_filtered_branches():
    """A branch that filters must still contribute its kept items, even if initial items drop."""
    from dataclasses import replace
    from pydocs_mcp.retrieval.stages import ParallelRetrievalStage

    @dataclass(frozen=True, slots=True)
    class _FilterAndTag:
        tag: str
        name: str = "filter_tag"
        async def run(self, state):
            # Drops 1 item, adds 1 — positional slice at start=len(initial) fails to capture
            existing = state.result.items if state.result else ()
            kept = existing[:-1] if existing else ()  # drop last initial item
            new = Chunk(text=self.tag, id=999)
            return replace(state, result=ChunkList(items=kept + (new,)))

    state = PipelineState(
        query=SearchQuery(terms="x"),
        result=ChunkList(items=(Chunk(text="pre", id=1), Chunk(text="drop", id=2))),
    )
    stage = ParallelRetrievalStage(stages=(_FilterAndTag(tag="A"), _FilterAndTag(tag="B")))
    out = await stage.run(state)
    ids = {c.id for c in out.result.items}
    # Both branches' new items (id=999) must be present; initial id=1 preserved;
    # dedup by id means only ONE copy of id=999 is in the accumulator.
    assert 1 in ids
    assert 999 in ids
    # Each branch contributed a new item with id=999 — deduped to a single entry
    assert sum(1 for c in out.result.items if c.id == 999) == 1
