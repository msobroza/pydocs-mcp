"""Pin the ``composite_mode`` slot-preference flip on ``PydocsMcpSystem``.

The comparison run (DS-1000) needs pydocs to emit ONE composite chunk
(matching Context7/Neuledge's single blob) so cross-system ``recall@1``
is fair — pydocs must not get a "more text -> more chance of a fuzzy
match" edge from its pre-budget N-item ranked list. ``composite_mode``
flips ``search()`` to prefer ``state.result`` (the TokenBudgetStep
composite) over ``state.candidates`` (the ranked list).

These tests fake the pipeline directly: a stub ``run()`` returns a
``RetrieverState`` with both ``candidates`` and ``result`` slots set, so
the slot-preference logic is exercised without an index round-trip.
"""
from __future__ import annotations

import pytest
from benchmarks.eval.systems.pydocs import PydocsMcpSystem
from pydocs_mcp.models import Chunk, ChunkList


class _FakePipeline:
    """Minimal stand-in for ``CodeRetrieverPipeline``.

    ``run()`` ignores the query and returns a pre-built
    ``RetrieverState`` so the test controls exactly what lands in the
    ``candidates`` / ``result`` slots.
    """

    def __init__(self, state: object) -> None:
        self._state = state

    async def run(self, query: object) -> object:
        return self._state


def _chunk(text: str) -> Chunk:
    return Chunk(text=text, metadata={"source_path": f"{text}.py"})


def _make_state(
    *, candidates: ChunkList | None, result: ChunkList | None
) -> object:
    from pydocs_mcp.models import SearchQuery
    from pydocs_mcp.retrieval.pipeline import RetrieverState

    return RetrieverState(
        query=SearchQuery(terms="q", max_results=10),
        candidates=candidates,
        result=result,
    )


@pytest.mark.asyncio
async def test_default_prefers_candidates_even_when_result_set() -> None:
    # Regression guard: composite_mode=False (the default) must keep the
    # legacy candidates-preferred behavior so RepoQA / other callers are
    # unaffected. 3 candidates + 1 composite result -> returns the 3.
    candidates = ChunkList(items=(_chunk("a"), _chunk("b"), _chunk("c")))
    result = ChunkList(items=(_chunk("composite"),))
    system = PydocsMcpSystem()
    system._pipeline = _FakePipeline(
        _make_state(candidates=candidates, result=result)
    )

    items = await system.search("q", limit=10)

    assert len(items) == 3
    assert [it.text for it in items] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_composite_mode_prefers_result_over_candidates() -> None:
    # composite_mode=True: the 1-item composite (state.result) wins over
    # the 3-item ranked list (state.candidates) for output-shape parity.
    candidates = ChunkList(items=(_chunk("a"), _chunk("b"), _chunk("c")))
    result = ChunkList(items=(_chunk("composite"),))
    system = PydocsMcpSystem(composite_mode=True)
    system._pipeline = _FakePipeline(
        _make_state(candidates=candidates, result=result)
    )

    items = await system.search("q", limit=10)

    assert len(items) == 1
    assert [it.text for it in items] == ["composite"]


@pytest.mark.asyncio
async def test_composite_mode_falls_back_to_candidates_when_result_empty() -> None:
    # Graceful degradation: composite_mode=True but the result slot is
    # None (e.g. the token_budget_formatter step was omitted) -> fall
    # back to the 3-item candidates rather than returning nothing.
    candidates = ChunkList(items=(_chunk("a"), _chunk("b"), _chunk("c")))
    system = PydocsMcpSystem(composite_mode=True)
    system._pipeline = _FakePipeline(
        _make_state(candidates=candidates, result=None)
    )

    items = await system.search("q", limit=10)

    assert len(items) == 3
    assert [it.text for it in items] == ["a", "b", "c"]
