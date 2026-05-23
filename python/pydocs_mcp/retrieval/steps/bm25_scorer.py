"""BM25ScorerStep — normalize FTS5 rank into positive relevance scores.

Single responsibility: flip the sign of FTS5's negative BM25 rank so
``relevance`` is a positive "higher = better" score that downstream
steps (:class:`TopKFilterStep`, :class:`RRFStep`, …) can sort on
uniformly.

Future PR-B3.1 ``DenseScorerStep`` will operate in the same shape: read
candidates, assign normalized scores, write candidates back. Splitting
score from fetch is what makes that composition possible.

Member candidates (LIKE-fetched, see :class:`MemberFetcherStep` in
Task 5) carry no BM25 rank, so this step is a no-op for them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydocs_mcp.models import Chunk, ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep


@dataclass(frozen=True, slots=True)
class BM25ScorerStep(RetrieverStep):
    """Score normalization step for chunk pipelines."""

    name: str = field(default="bm25_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        # Member candidates aren't BM25-scorable (LIKE doesn't produce ranks).
        if isinstance(state.candidates, ModuleMemberList):
            return state
        # ChunkList: flip the sign of FTS5's negative BM25 rank.
        new_items = tuple(
            Chunk(
                text=c.text,
                id=c.id,
                relevance=(-c.relevance) if c.relevance is not None else None,
                retriever_name=c.retriever_name,
                metadata=dict(c.metadata),
            )
            for c in state.candidates.items
        )
        return replace(state, candidates=ChunkList(items=new_items))


__all__ = ("BM25ScorerStep",)
