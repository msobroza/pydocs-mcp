"""ParallelStep — deterministic last-branch-wins merge + input-state isolation.

These tests pin invariants the ``_merge_branch_results`` helper and any
step that may run inside a ``ParallelStep`` branch must honour:

1. **Input scratch is not mutated.** A branch that publishes into
   ``state.scratch[<key>]`` operates on an isolated per-branch copy; the
   merged scratch is built into a fresh dict so the caller's input state
   is unaffected.
2. **Last-write-wins on shared keys.** When two branches publish the
   same key, the rightmost branch's value survives (matches the natural
   reading order of YAML ``branches:`` entries).
3. **Branch steps publish via ``replace``, not in-place dict writes.**
   :class:`TopKFilterStep`'s ``publish_to`` produces a NEW scratch dict
   so the caller's state is preserved (this is what lets a sibling
   ``TopKFilterStep`` inside a parallel branch observe an isolated
   per-branch copy of scratch instead of a shared one).
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import pytest

from pydocs_mcp.models import Chunk, ChunkList, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep


@dataclass(frozen=True, slots=True)
class _WriteScratchStep(RetrieverStep):
    """Test helper: writes a fixed value into ``state.scratch[key]``.

    Uses ``dataclasses.replace`` with a brand-new scratch dict so the
    step itself doesn't rely on the mutable-dict escape hatch (this is
    the discipline the narrowed ``RetrieverState`` docstring asks of
    any step that may run inside a ``ParallelStep`` branch).
    """

    key: str = "shared"
    value: int = 0
    name: str = field(default="writer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        new_scratch = {**state.scratch, self.key: self.value}
        return replace(state, scratch=new_scratch)


@pytest.mark.asyncio
async def test_parallel_step_last_branch_wins_on_scratch() -> None:
    """Last branch in declaration order wins on a shared scratch key.

    Also asserts the input state's scratch is not mutated and that
    pre-existing scratch keys survive the merge.
    """
    initial = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        scratch={"existing": 1},
    )
    parallel = ParallelStep(
        stages=(
            _WriteScratchStep(key="shared", value=10, name="a"),
            _WriteScratchStep(key="shared", value=20, name="b"),
        ),
    )
    out = await parallel.run(initial)

    # (a) last branch wins on the shared key.
    assert out.scratch["shared"] == 20
    # (b) pre-existing scratch keys survive untouched.
    assert out.scratch["existing"] == 1
    # (c) input state's scratch is NOT mutated — caller-side isolation.
    assert "shared" not in initial.scratch
    assert initial.scratch == {"existing": 1}


@pytest.mark.asyncio
async def test_top_k_filter_publish_to_does_not_mutate_input_scratch() -> None:
    """``TopKFilterStep`` with ``publish_to`` returns a NEW scratch dict.

    Pre-refactor, ``new_state.scratch[self.publish_to] = ...`` mutated
    the caller's ``state.scratch`` in place (aliased through
    ``dataclasses.replace``). That breaks ParallelStep's per-branch
    scratch isolation: if a branch contains a ``TopKFilterStep`` with
    ``publish_to``, the aliasing would leak the write into the shared
    input dict before merge. Post-refactor the step must produce a
    fresh dict via ``replace(state, scratch={...})``.
    """
    state = RetrieverState(
        query=SearchQuery(terms="x", max_results=10),
        candidates=ChunkList(items=(
            Chunk(text="a", id=1, relevance=0.9),
            Chunk(text="b", id=2, relevance=0.7),
        )),
        scratch={"pre.existing": "hi"},
    )
    step = TopKFilterStep(name="topk", k=2, publish_to="bm25.ranked")
    out = await step.run(state)

    # The publish key landed in the OUTPUT state's scratch...
    assert "bm25.ranked" in out.scratch
    # ...but the INPUT state's scratch was not touched.
    assert "bm25.ranked" not in state.scratch
    assert state.scratch == {"pre.existing": "hi"}
    # And the two dicts are distinct objects (no aliasing).
    assert state.scratch is not out.scratch
