"""ParallelStep — fan-out to multiple sub-stages, merge results.

Each inner stage sees the SAME input state independently; outputs are
deduped by ``item.id`` (falling back to ``id(item)`` when ``.id`` is
None) and concatenated in branch order. Earlier branches' representative
items win on duplicates so retriever_name / relevance from the first
hit survive the merge (AC #33).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline_legacy import PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import PipelineStage


@stage_registry.register("parallel_retrieval")
@dataclass(frozen=True, slots=True)
class ParallelStep:
    stages: tuple["PipelineStage", ...] = ()
    name: str = "parallel_retrieval"

    async def run(self, state: PipelineState) -> PipelineState:
        results = await asyncio.gather(*(s.run(state) for s in self.stages))

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

        for branch_state in results:
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
            return replace(state, result=ChunkList(items=tuple(accumulated_items)))
        if first_type is ModuleMemberList:
            return replace(state, result=ModuleMemberList(items=tuple(accumulated_items)))
        return state

    def to_dict(self) -> dict:
        return {"type": "parallel_retrieval", "stages": [s.to_dict() for s in self.stages]}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "ParallelStep":
        return cls(stages=tuple(context.stage_registry.build(s, context) for s in data["stages"]))


__all__ = ("ParallelStep",)
