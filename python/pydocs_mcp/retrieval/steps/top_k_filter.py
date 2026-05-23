"""TopKFilterStep — uniform top-K cutoff for chunk and member pipelines.

Single responsibility: keep the top K candidates by ``relevance``
descending. If no candidate carries a relevance value (e.g., no scorer
ran upstream — :class:`MemberFetcherStep` produces unscored results
from LIKE), falls back to source order and takes the first K.

Works for both :class:`ChunkList` and :class:`ModuleMemberList` —
they share the ``items`` + ``relevance`` shape.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry


@stage_registry.register("top_k_filter")
@dataclass(frozen=True, slots=True)
class TopKFilterStep(RetrieverStep):
    """Top-K cutoff step. Works uniformly for chunks and members."""

    k: int = field(default=50, kw_only=True)
    name: str = field(default="top_k_filter", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        items = state.candidates.items
        if not items:
            return state
        # Sort by relevance desc when at least one candidate has it set,
        # otherwise preserve source order (LIKE results have no rank).
        has_relevance = any(
            getattr(c, "relevance", None) is not None for c in items
        )
        if has_relevance:
            sorted_items = tuple(
                sorted(items, key=lambda c: c.relevance or 0.0, reverse=True)
            )
        else:
            sorted_items = tuple(items)
        new_items = sorted_items[: self.k]
        if isinstance(state.candidates, ChunkList):
            return replace(state, candidates=ChunkList(items=new_items))
        if isinstance(state.candidates, ModuleMemberList):
            return replace(state, candidates=ModuleMemberList(items=new_items))
        return state

    def to_dict(self) -> dict:
        d: dict = {"type": "top_k_filter"}
        if self.k != 50:
            d["k"] = self.k
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "TopKFilterStep":
        return cls(k=data.get("k", 50))


__all__ = ("TopKFilterStep",)
