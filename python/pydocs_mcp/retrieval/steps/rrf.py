"""RRFStep — score items by 1/(k+rank), sort descending."""
from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry


@stage_registry.register("reciprocal_rank_fusion")
@dataclass(frozen=True, slots=True)
class RRFStep(RetrieverStep):
    k: int = 60
    name: str = "reciprocal_rank_fusion"

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.result is None or not state.result.items:
            return state
        # Score by 1/(k+rank), keyed by item id (fall back to id(item)).
        # First-seen wins on the stored representative so retriever_name /
        # relevance from the earliest branch survives the merge (AC #33).
        scores: dict = {}
        items_by_key: dict = {}
        for rank, item in enumerate(state.result.items):
            key = item.id if item.id is not None else id(item)
            scores[key] = scores.get(key, 0.0) + 1.0 / (self.k + rank)
            items_by_key.setdefault(key, item)

        sorted_keys = sorted(scores.keys(), key=lambda k_: scores[k_], reverse=True)
        sorted_items = tuple(items_by_key[k_] for k_ in sorted_keys)
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=sorted_items))
        return replace(state, result=ModuleMemberList(items=sorted_items))

    def to_dict(self) -> dict:
        d: dict = {"type": "reciprocal_rank_fusion"}
        if self.k != 60:
            d["k"] = self.k
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "RRFStep":
        return cls(k=data.get("k", 60))


__all__ = ("RRFStep",)
