"""CommunityDiversityStep — diversify results across reference-graph communities.

A rerank-only step: greedy MMR that, while keeping high-relevance hits, penalises
picking another candidate from a community (Louvain cluster, from node_scores)
already represented in the picks. On a broad query this spreads the top-k across
the distinct subsystems that touch it instead of returning K near-duplicates from
one module.

Reads community ids via ``uow.node_scores.scores_for`` keyed on
``chunk.metadata['qualified_name']``. Community ``-1`` (unassigned) or a missing
score is treated as "no community" — never penalised. Safety: a non-Chunk /
empty candidate list passes through unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import UnitOfWork

_DEFAULT_LAMBDA = 0.5
_DEFAULT_NAME = "community_diversity"

_QNAME_KEY = "qualified_name"
_NO_COMMUNITY = -1


@step_registry.register("community_diversity")
@dataclass(frozen=True, slots=True)
class CommunityDiversityStep(RetrieverStep):
    """Greedy MMR reorder that spreads results across graph communities."""

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    penalty: float = field(default=_DEFAULT_LAMBDA, kw_only=True)
    name: str = field(default=_DEFAULT_NAME, kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        candidates = state.candidates
        if not isinstance(candidates, ChunkList) or not candidates.items:
            return state
        qnames = [q for c in candidates.items if (q := c.metadata.get(_QNAME_KEY))]
        if not qnames:
            return state
        async with self.uow_factory() as uow:
            scores = await uow.node_scores.scores_for(qnames)
        if not scores:
            return state

        community_of = {qn: getattr(s, "community", _NO_COMMUNITY) for qn, s in scores.items()}
        reordered = self._mmr(candidates.items, community_of)
        return replace(state, candidates=ChunkList(items=tuple(reordered)))

    def _community(self, chunk: Chunk, community_of: dict[str, int]) -> int:
        qname = chunk.metadata.get(_QNAME_KEY)
        return community_of.get(qname, _NO_COMMUNITY) if qname else _NO_COMMUNITY

    def _mmr(self, items: tuple[Chunk, ...], community_of: dict[str, int]) -> list[Chunk]:
        # Greedy: repeatedly take the candidate maximising
        # relevance - penalty*(community already picked?). -1 communities never
        # collide (each is treated as unique), so unscored hits aren't demoted.
        remaining = list(items)
        picked: list[Chunk] = []
        seen_communities: set[int] = set()
        while remaining:
            best_idx = 0
            best_adj = float("-inf")
            for i, chunk in enumerate(remaining):
                comm = self._community(chunk, community_of)
                collide = comm != _NO_COMMUNITY and comm in seen_communities
                adj = (chunk.relevance or 0.0) - (self.penalty if collide else 0.0)
                if adj > best_adj:
                    best_adj = adj
                    best_idx = i
            chosen = remaining.pop(best_idx)
            comm = self._community(chosen, community_of)
            if comm != _NO_COMMUNITY:
                seen_communities.add(comm)
            picked.append(chosen)
        return picked

    def to_dict(self) -> dict:
        d: dict = {"type": "community_diversity"}
        if self.penalty != _DEFAULT_LAMBDA:
            d["penalty"] = self.penalty
        if self.name != _DEFAULT_NAME:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> CommunityDiversityStep:
        if context.uow_factory is None:
            raise ValueError(
                "CommunityDiversityStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this.",
            )
        return cls(
            uow_factory=context.uow_factory,
            penalty=data.get("penalty", _DEFAULT_LAMBDA),
            name=data.get("name", _DEFAULT_NAME),
        )


__all__ = ("CommunityDiversityStep",)
