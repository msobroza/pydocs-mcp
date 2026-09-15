"""LimitStep: whose cap wins, and how the cut is reported (#271).

``SearchQuery.max_results`` carries the client's ``limit`` (already bounded by
``search.output.max_limit``); the step's own ``max_results`` is the deployment
default for a query that carries none. Whatever the cap, the rows it drops are
published on the scratch so the application layer can mark the response as
partial instead of letting it read as complete.
"""

from __future__ import annotations

from pydocs_mcp.models import Chunk, ChunkList, ModuleMember, ModuleMemberList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState
from pydocs_mcp.retrieval.steps import LimitStep, rows_dropped_by_limit
from pydocs_mcp.retrieval.steps._constants import LIMIT_DROPPED_SCRATCH_KEY


def _chunks(count: int) -> ChunkList:
    return ChunkList(items=tuple(Chunk(text=str(i)) for i in range(count)))


def _state(count: int, *, max_results: int | None = None) -> RetrieverState:
    return RetrieverState(
        query=SearchQuery(terms="x", max_results=max_results),
        candidates=_chunks(count),
    )


# ── Which cap applies ─────────────────────────────────────────────────────


async def test_request_cap_outranks_the_configured_default() -> None:
    out = await LimitStep(max_results=8).run(_state(20, max_results=15))
    assert out.candidates is not None
    assert len(out.candidates.items) == 15


async def test_configured_default_applies_when_the_query_carries_no_cap() -> None:
    out = await LimitStep(max_results=3).run(_state(20))
    assert out.candidates is not None
    assert len(out.candidates.items) == 3


async def test_a_request_cap_wider_than_the_corpus_keeps_every_row() -> None:
    out = await LimitStep(max_results=8).run(_state(5, max_results=100))
    assert out.candidates is not None
    assert len(out.candidates.items) == 5


async def test_member_lists_keep_their_type_under_the_request_cap() -> None:
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=2),
        candidates=ModuleMemberList(items=tuple(ModuleMember(metadata={}) for _ in range(6))),
    )
    out = await LimitStep().run(state)
    assert isinstance(out.candidates, ModuleMemberList)
    assert len(out.candidates.items) == 2


# ── Reporting the cut ─────────────────────────────────────────────────────


async def test_dropped_rows_are_published_on_the_scratch() -> None:
    out = await LimitStep().run(_state(20, max_results=5))
    assert rows_dropped_by_limit(out) == 15


async def test_nothing_dropped_publishes_nothing() -> None:
    out = await LimitStep().run(_state(5, max_results=5))
    assert LIMIT_DROPPED_SCRATCH_KEY not in out.scratch
    assert rows_dropped_by_limit(out) == 0


async def test_the_input_scratch_is_never_mutated() -> None:
    """A limit step may sit inside a ``ParallelStep`` branch, where an in-place
    scratch write leaks into the sibling branches (CLAUDE.md §scratch)."""
    state = _state(20, max_results=5)
    await LimitStep().run(state)
    assert state.scratch == {}


async def test_a_legacy_result_only_state_is_still_capped_and_reported() -> None:
    state = RetrieverState(query=SearchQuery(terms="x", max_results=4), result=_chunks(10))
    out = await LimitStep().run(state)
    assert out.result is not None
    assert len(out.result.items) == 4
    assert rows_dropped_by_limit(out) == 6
