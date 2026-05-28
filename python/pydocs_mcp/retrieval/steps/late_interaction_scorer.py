"""LateInteractionScorerStep — MaxSim re-ranker (Decision C).

Bridges SQLite (candidate-id subset) and the multi-vector backend
(MaxSim scoring): takes the upstream candidate :class:`ChunkList`,
extracts chunk ids, calls
``uow.multi_vectors.score(query, subset_chunk_ids=ids, top_k=K)`` —
the concrete :class:`MultiVectorStore` translates ``chunk_id`` →
``plaid_doc_id`` through the ``chunk_multi_vector_ids`` SQLite mapping,
scores within the subset, and returns ``(chunk_id, score)`` pairs.
Reverse-maps onto the candidates by id, updates ``relevance``,
re-sorts, and truncates to ``top_k``.

This step may run inside a :class:`ParallelStep` branch — scratch
mutation follows the "fresh dict" rule
(CLAUDE.md §"RetrieverState.scratch mutation discipline").
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.models import ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import MultiVectorEmbedder, UnitOfWork

# WHY: single source of truth for the late-interaction cutoff.
# Referenced from the dataclass field default + to_dict (omit-when-default)
# + from_dict (fallback when YAML omits the key).
_DEFAULT_TOP_K = 100


@step_registry.register("late_interaction_scorer")
@dataclass(frozen=True, slots=True)
class LateInteractionScorerStep(RetrieverStep):
    """MaxSim re-ranker over an upstream candidate ChunkList.

    The step is a pure scorer-and-filter: it does not fetch new
    candidates. It expects ``state.candidates`` to be a
    :class:`ChunkList` whose items carry SQLite chunk ids (set by an
    upstream fetcher / scorer step). Members carry no multi-vector
    embeddings — for a :class:`ModuleMemberList` candidate the step is
    a no-op.

    When ``publish_to`` is set, the re-ranked output is ALSO written to
    ``state.scratch[publish_to]`` (same payload as ``state.candidates``)
    so a downstream :class:`RRFFusionStep` can fuse multiple branch
    rankings (mirrors the :class:`TopKFilterStep.publish_to` contract).
    """

    embedder: MultiVectorEmbedder
    uow_factory: Callable[[], UnitOfWork]
    top_k: int = _DEFAULT_TOP_K
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default="late_interaction_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        # Members carry no multi-vector embeddings — late-interaction
        # only applies to chunk candidates.
        if not isinstance(state.candidates, ChunkList):
            return state
        if not state.candidates.items:
            return state

        ids = [c.id for c in state.candidates.items if c.id is not None]
        if not ids:
            return state

        query_emb = await self.embedder.embed_query(state.query.terms)

        async with self.uow_factory() as uow:
            ranked = await uow.multi_vectors.score(
                query_embedding=query_emb,
                subset_chunk_ids=ids,
                top_k=self.top_k,
            )

        # Reverse-map scores onto candidates by id; preserve only the
        # scored chunks (the backend may legitimately drop a subset id
        # that has no persisted multi-vector embedding, e.g. an ingestion
        # mismatch — we treat that as "not scored").
        chunk_by_id = {c.id: c for c in state.candidates.items if c.id is not None}
        scored = [
            replace(
                chunk_by_id[cid],
                relevance=score,
                retriever_name="late_interaction",
            )
            for cid, score in ranked
            if cid in chunk_by_id
        ]
        # Backend returns ranked already, but re-sort defensively so the
        # output contract is "descending by relevance" regardless of
        # backend ordering quirks.
        scored.sort(key=lambda c: c.relevance or 0.0, reverse=True)
        new_candidates = ChunkList(items=tuple(scored))

        # WHY: use a fresh scratch dict — this step MAY run inside a
        # :class:`ParallelStep` branch, where in-place mutation of the
        # input scratch leaks across sibling branches
        # (CLAUDE.md §"RetrieverState.scratch mutation discipline").
        if self.publish_to is not None:
            new_scratch = {**state.scratch, self.publish_to: new_candidates}
            return replace(state, candidates=new_candidates, scratch=new_scratch)
        return replace(state, candidates=new_candidates)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "late_interaction_scorer"}
        if self.top_k != _DEFAULT_TOP_K:
            d["top_k"] = self.top_k
        if self.publish_to is not None:
            d["publish_to"] = self.publish_to
        return d

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        context: BuildContext,
    ) -> LateInteractionScorerStep:
        if context.multi_vector_embedder is None:
            raise ValueError(
                "LateInteractionScorerStep requires "
                "BuildContext.multi_vector_embedder. Set "
                "``late_interaction.enabled: true`` in your AppConfig YAML "
                "and ensure the composition root constructs the embedder.",
            )
        if context.uow_factory is None:
            raise ValueError(
                "LateInteractionScorerStep requires BuildContext.uow_factory "
                "to be set so the step can open a UoW and call "
                "``uow.multi_vectors.score(...)``.",
            )
        return cls(
            embedder=context.multi_vector_embedder,
            uow_factory=context.uow_factory,
            top_k=int(data.get("top_k", _DEFAULT_TOP_K)),
            publish_to=data.get("publish_to"),
        )


__all__ = ("LateInteractionScorerStep",)
