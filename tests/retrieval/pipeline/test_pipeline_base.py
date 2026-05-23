"""Tests for the new RetrieverStep ABC + RetrieverPipeline + RetrieverState."""
from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline import (
    RetrieverPipeline,
    RetrieverState,
    RetrieverStep,
)


@dataclass(frozen=True, slots=True)
class _BumpStep(RetrieverStep):
    """Test fixture: bumps duration_ms by 1."""

    async def run(self, state: RetrieverState) -> RetrieverState:
        return replace(state, duration_ms=state.duration_ms + 1.0)


def _query() -> SearchQuery:
    return SearchQuery(terms="anything", max_results=10)


@pytest.mark.asyncio
async def test_pipeline_runs_steps_in_order() -> None:
    pipeline = RetrieverPipeline(
        name="p",
        steps=(
            ("a", _BumpStep(name="a")),
            ("b", _BumpStep(name="b")),
            ("c", _BumpStep(name="c")),
        ),
    )
    out = await pipeline.run(RetrieverState(query=_query()))
    assert out.duration_ms == 3.0


def test_pipeline_addresses_steps_by_name() -> None:
    a = _BumpStep(name="a")
    b = _BumpStep(name="b")
    pipeline = RetrieverPipeline(name="p", steps=(("a", a), ("b", b)))
    assert pipeline["a"] is a
    assert pipeline["b"] is b


def test_pipeline_step_names() -> None:
    pipeline = RetrieverPipeline(
        name="p",
        steps=(
            ("fetch", _BumpStep(name="fetch")),
            ("score", _BumpStep(name="score")),
        ),
    )
    assert pipeline.step_names == ("fetch", "score")


def test_pipeline_rejects_duplicate_step_names() -> None:
    with pytest.raises(ValueError, match="duplicate step names"):
        RetrieverPipeline(
            name="p",
            steps=(("a", _BumpStep(name="a")), ("a", _BumpStep(name="a"))),
        )


def test_pipeline_rejects_zero_steps() -> None:
    with pytest.raises(ValueError, match="has no steps"):
        RetrieverPipeline(name="p", steps=())


def test_pipeline_keyerror_on_unknown_step() -> None:
    pipeline = RetrieverPipeline(name="p", steps=(("a", _BumpStep(name="a")),))
    with pytest.raises(KeyError, match="has no step 'b'"):
        _ = pipeline["b"]


@pytest.mark.asyncio
async def test_pipeline_is_a_step_composes_recursively() -> None:
    """Pipeline IS a RetrieverStep — they nest."""
    inner = RetrieverPipeline(
        name="inner",
        steps=(("a", _BumpStep(name="a")), ("b", _BumpStep(name="b"))),
    )
    outer = RetrieverPipeline(
        name="outer",
        steps=(("inner", inner), ("c", _BumpStep(name="c"))),
    )
    assert isinstance(inner, RetrieverStep)
    out = await outer.run(RetrieverState(query=_query()))
    assert out.duration_ms == 3.0  # 2 from inner + 1 from c


def test_retriever_step_is_abstract() -> None:
    """Can't instantiate the ABC directly."""
    with pytest.raises(TypeError, match="abstract"):
        RetrieverStep(name="bare")  # type: ignore[abstract]


def test_state_is_frozen() -> None:
    state = RetrieverState(query=_query())
    with pytest.raises(AttributeError):
        state.duration_ms = 5.0  # type: ignore[misc]


def test_state_scratch_is_mutable() -> None:
    """Scratch dict can be mutated even though the dataclass is frozen."""
    state = RetrieverState(query=_query())
    state.scratch["bm25.weights"] = [0.5, 0.3]
    assert state.scratch["bm25.weights"] == [0.5, 0.3]


@pytest.mark.asyncio
async def test_state_replace_for_pure_updates() -> None:
    """Stages produce a new state via dataclasses.replace, not mutation."""
    initial = RetrieverState(query=_query())
    bumped = replace(initial, duration_ms=42.0)
    assert initial.duration_ms == 0.0
    assert bumped.duration_ms == 42.0
