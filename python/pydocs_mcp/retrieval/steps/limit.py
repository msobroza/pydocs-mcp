"""LimitStep — cap the item count at ``max_results``.

Task 8: operates on ``state.candidates`` (the intermediate ranked
list) when present, falling back to ``state.result`` for backward
compatibility with code that hasn't migrated to the
candidates/result split.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry


@stage_registry.register("limit")
@dataclass(frozen=True, slots=True)
class LimitStep(RetrieverStep):
    max_results: int = 8
    name: str = "limit"

    async def run(self, state: RetrieverState) -> RetrieverState:
        # Task 8: prefer ``state.candidates`` (post-fetch / post-score
        # intermediate). Fall back to ``state.result`` for legacy
        # composition paths still relying on the pre-Task-8 shape.
        target = state.candidates if state.candidates is not None else state.result
        if target is None:
            return state
        capped = target.items[: self.max_results]
        if state.candidates is not None:
            if isinstance(target, ChunkList):
                return replace(state, candidates=ChunkList(items=tuple(capped)))
            return replace(state, candidates=ModuleMemberList(items=tuple(capped)))
        if isinstance(target, ChunkList):
            return replace(state, result=ChunkList(items=tuple(capped)))
        return replace(state, result=ModuleMemberList(items=tuple(capped)))

    def to_dict(self) -> dict:
        d: dict = {"type": "limit"}
        if self.max_results != 8:
            d["max_results"] = self.max_results
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "LimitStep":
        return cls(max_results=data.get("max_results", 8))


__all__ = ("LimitStep",)
