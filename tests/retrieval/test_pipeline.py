"""Tests for CodeRetrieverPipeline + PipelineState."""
from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from pydocs_mcp.models import ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import CodeRetrieverPipeline, PipelineState


@dataclass(frozen=True, slots=True)
class _AppendStage:
    """Test fake — records runs in a shared list."""
    name: str
    log: list

    async def run(self, state: PipelineState) -> PipelineState:
        self.log.append(self.name)
        return state


async def test_pipeline_state_defaults():
    q = SearchQuery(terms="x")
    s = PipelineState(query=q)
    assert s.query is q
    assert s.result is None
    assert s.duration_ms == 0.0


def test_pipeline_state_frozen():
    s = PipelineState(query=SearchQuery(terms="x"))
    with pytest.raises(Exception):
        s.query = SearchQuery(terms="y")


async def test_pipeline_runs_stages_in_order():
    log: list[str] = []
    pipeline = CodeRetrieverPipeline(
        name="p",
        stages=(_AppendStage(name="a", log=log), _AppendStage(name="b", log=log)),
    )
    state = await pipeline.run(SearchQuery(terms="x"))
    assert log == ["a", "b"]
    assert state.query.terms == "x"


async def test_pipeline_empty_stages_is_noop():
    pipeline = CodeRetrieverPipeline(name="empty", stages=())
    state = await pipeline.run(SearchQuery(terms="x"))
    assert state.query.terms == "x"
    assert state.result is None
