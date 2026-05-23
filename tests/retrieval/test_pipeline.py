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


def test_from_dict_rejects_excessive_nesting(tmp_path):
    """AC #31 — from_dict enforces _MAX_PIPELINE_DEPTH (Task 8: ``steps:`` schema)."""
    from pydocs_mcp.retrieval.pipeline import (
        PerCallConnectionProvider,
        _MAX_PIPELINE_DEPTH,
    )
    from pydocs_mcp.retrieval.serialization import BuildContext

    # Build a pipeline dict that recursively nests ``type: sub_pipeline``
    # one level beyond the allowed depth.
    def _leaf() -> dict:
        return {"name": "leaf", "steps": []}

    def _wrap(inner: dict) -> dict:
        return {
            "name": "wrap",
            "steps": [
                {"name": "nested", "type": "sub_pipeline", "params": {"pipeline": inner}},
            ],
        }

    data = _leaf()
    for _ in range(_MAX_PIPELINE_DEPTH + 1):
        data = _wrap(data)

    context = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    with pytest.raises(ValueError, match="max depth"):
        CodeRetrieverPipeline.from_dict(data, context)


def test_from_dict_accepts_shallow_nesting(tmp_path):
    """Shallow nesting well under the depth cap must still succeed."""
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.retrieval.serialization import BuildContext

    inner = {"name": "inner", "steps": []}
    data = {
        "name": "outer",
        "steps": [
            {"name": "nested", "type": "sub_pipeline", "params": {"pipeline": inner}},
        ],
    }
    context = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    pipeline = CodeRetrieverPipeline.from_dict(data, context)
    assert pipeline.name == "outer"
    assert len(pipeline.stages) == 1


def test_from_dict_rejects_legacy_stages_key(tmp_path):
    """Task 8: ``stages:`` is rejected with a migration error."""
    from pydocs_mcp.retrieval.pipeline import (
        PerCallConnectionProvider,
        PipelineLoadError,
    )
    from pydocs_mcp.retrieval.serialization import BuildContext

    context = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    with pytest.raises(PipelineLoadError, match="'stages:' key is no longer accepted"):
        CodeRetrieverPipeline.from_dict({"name": "old", "stages": []}, context)


def test_from_dict_rejects_step_missing_name(tmp_path):
    """Task 8: every step must declare a ``name:``."""
    from pydocs_mcp.retrieval.pipeline import (
        PerCallConnectionProvider,
        PipelineLoadError,
    )
    from pydocs_mcp.retrieval.serialization import BuildContext

    context = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    data = {
        "name": "p",
        "steps": [{"type": "limit", "params": {"max_results": 3}}],
    }
    with pytest.raises(PipelineLoadError, match="missing required 'name:'"):
        CodeRetrieverPipeline.from_dict(data, context)


def test_from_dict_rejects_missing_steps_key(tmp_path):
    """Task 8: top-level pipeline YAML must declare ``steps:``."""
    from pydocs_mcp.retrieval.pipeline import (
        PerCallConnectionProvider,
        PipelineLoadError,
    )
    from pydocs_mcp.retrieval.serialization import BuildContext

    context = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    with pytest.raises(PipelineLoadError, match="missing required 'steps:' key"):
        CodeRetrieverPipeline.from_dict({"name": "p"}, context)


def test_from_dict_steps_with_names_round_trip(tmp_path):
    """Task 8: ``steps:`` with ``name:`` + ``type:`` + ``params:`` round-trips."""
    from pydocs_mcp.retrieval.pipeline import PerCallConnectionProvider
    from pydocs_mcp.retrieval.serialization import BuildContext

    context = BuildContext(
        connection_provider=PerCallConnectionProvider(cache_path=tmp_path / "x.db"),
    )
    data = {
        "name": "p",
        "steps": [
            {"name": "trim", "type": "limit", "params": {"max_results": 3}},
        ],
    }
    pipeline = CodeRetrieverPipeline.from_dict(data, context)
    assert pipeline.name == "p"
    assert len(pipeline.stages) == 1
    # Limit stage carries the configured cap.
    assert pipeline.stages[0].max_results == 3
