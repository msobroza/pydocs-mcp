"""ParallelStep — fan-out to multiple sub-stages, merge results.

Each inner stage sees an ISOLATED copy of the input state (its own
scratch dict), so concurrent branches cannot race on a shared mutable
dict. After all branches finish, the parent:

- merges the ``state.result.items`` lists by ``item.id`` (falling back to
  ``id(item)`` when ``.id`` is None), concatenating in branch order, and
  earlier-wins on duplicates so the first branch's representative
  ``retriever_name`` / ``relevance`` survive (AC #33).
- merges each branch's ``state.scratch`` writes back into the final
  state's scratch dict (last-write-wins per branch order), starting from
  a copy of the input scratch. This is how branches publish their named
  ranked lists (e.g. ``bm25.ranked`` / ``dense.ranked``) for a downstream
  :class:`RRFFusionStep` to consume (spec §3.1 Decision 3 + AC-21
  prereq).

YAML shapes (``from_dict``):

- ``branches: [{name, steps: [...]}]`` — preferred for hybrid search;
  each branch becomes a :class:`CodeRetrieverPipeline` whose ``.name``
  is the branch identifier so its inner :class:`TopKFilterStep` can
  publish to ``state.scratch[<branch>.ranked]``.
- ``stages: [step_dict, ...]`` — the legacy raw-step-list shape that
  ``ParallelStep.to_dict`` emits; kept for in-code constructions that
  round-trip through ``to_dict`` / ``from_dict``.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import (
    CodeRetrieverPipeline,
    RetrieverState,
    RetrieverStep,
)
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry


@step_registry.register("parallel_retrieval")
@dataclass(frozen=True, slots=True)
class ParallelStep(RetrieverStep):
    stages: tuple[RetrieverStep, ...] = ()
    name: str = "parallel_retrieval"

    async def run(self, state: RetrieverState) -> RetrieverState:
        # Each branch runs on its OWN scratch dict so concurrent writes
        # don't race on the input's shared mutable dict. ``replace`` keeps
        # all other fields (query / candidates / result) pointing at the
        # same immutable objects — those are safe to share.
        branch_inputs = tuple(
            replace(state, scratch=dict(state.scratch)) for _ in self.stages
        )
        results = await asyncio.gather(
            *(s.run(bi) for s, bi in zip(self.stages, branch_inputs))
        )

        initial_items: tuple = ()
        if state.result is not None:
            initial_items = state.result.items

        first_type = type(state.result) if state.result is not None else None

        # Track items by their identity (id field if set, else Python id() fallback).
        # Branches may filter or reorder; we dedupe by content-key, not position.
        seen_keys: set = set()
        accumulated_items: list = []

        def _key(item):
            return item.id if item.id is not None else id(item)

        for item in initial_items:
            k = _key(item)
            if k not in seen_keys:
                seen_keys.add(k)
                accumulated_items.append(item)

        # Last-write-wins merge in branch order so earlier branches set the
        # baseline and later branches can deliberately override a shared
        # key (e.g. two branches both publishing "ranked" — the rightmost
        # wins, matching the natural reading order of YAML branches).
        merged_scratch: dict[str, object] = dict(state.scratch)

        for branch_state in results:
            merged_scratch.update(branch_state.scratch)
            if branch_state.result is None:
                continue
            if first_type is None:
                first_type = type(branch_state.result)
            for item in branch_state.result.items:
                k = _key(item)
                if k not in seen_keys:
                    seen_keys.add(k)
                    accumulated_items.append(item)

        if first_type is ChunkList:
            return replace(
                state,
                result=ChunkList(items=tuple(accumulated_items)),
                scratch=merged_scratch,
            )
        if first_type is ModuleMemberList:
            return replace(
                state,
                result=ModuleMemberList(items=tuple(accumulated_items)),
                scratch=merged_scratch,
            )
        # No branch produced typed output — still propagate scratch so
        # branches that only publish via scratch (e.g., fetcher + topk
        # branches that hand results to RRFFusionStep without setting
        # state.result themselves) aren't silently dropped.
        return replace(state, scratch=merged_scratch)

    def to_dict(self) -> dict:
        return {"type": "parallel_retrieval", "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ParallelStep":
        has_branches = "branches" in data
        has_stages = "stages" in data
        if has_branches and has_stages:
            raise ValueError(
                "parallel_retrieval YAML must not mix 'branches' and 'stages' keys; "
                "use 'branches: [{name, steps: [...]}]' (preferred) or "
                "'stages: [step_dict, ...]' (legacy raw-step list)"
            )
        if has_branches:
            # Named-branches shape: each branch becomes a CodeRetrieverPipeline
            # whose .name carries the branch identifier. The pipeline itself
            # IS a RetrieverStep (CodeRetrieverPipeline subclasses RetrieverStep
            # directly), so the resulting stages tuple is homogeneous.
            stages = tuple(
                CodeRetrieverPipeline.from_dict(branch, context)
                for branch in data["branches"]
            )
            return cls(stages=stages)
        if has_stages:
            return cls(
                stages=tuple(
                    context.step_registry.build(s, context) for s in data["stages"]
                ),
            )
        # Empty parallel — degenerate but not illegal (the run() loop
        # is a no-op fan-out that returns the input scratch + result).
        return cls(stages=())


__all__ = ("ParallelStep",)
