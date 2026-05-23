"""LimitStep — cap the result item count at ``max_results``."""
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
        if state.result is None:
            return state
        capped = state.result.items[: self.max_results]
        if isinstance(state.result, ChunkList):
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
