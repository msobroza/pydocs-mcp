"""DenseScorerStep — post-fusion dense re-rank via turbovec allowlist search.

Mirrors :class:`LateInteractionScorerStep` on the single-vector side: takes
the upstream (fused) candidate :class:`ChunkList`, extracts chunk ids, and
calls ``store.score(query_vector, subset_chunk_ids=ids, top_k=K)`` — the
concrete :class:`VectorScoreable` re-scores ONLY that subset via turbovec's
``IdMapIndex.search(..., allowlist=...)`` hook (no unrestricted ANN search,
no extra storage: read-path chunks never carry ``embedding`` — vectors live
only in the ``.tq`` sidecar, models.py S13).

Re-rank policy differs from :class:`LateInteractionScorerStep` in one way:
candidates PRESENT in the ``.tq`` (i.e. turbovec actually scored them) are
sorted descending by that score; candidates ABSENT from the ``.tq``
(BM25-only, multi-vector-only, or skipped by a selective-embed policy) are
NOT dropped — they keep their incoming fused order and are appended AFTER
the re-scored ones, preserving BM25 recall.

This step may run inside a :class:`ParallelStep` branch — scratch mutation
follows the "fresh dict" rule (CLAUDE.md §"RetrieverState.scratch mutation
discipline").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from pydocs_mcp.models import ChunkList, is_multi_vector
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.protocols import Embedder
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import VectorScoreable

# WHY: single source of truth for the dense re-rank cutoff. Referenced from
# the dataclass field default + to_dict (omit-when-default) + from_dict
# (fallback when YAML omits the key). Mirrors LateInteractionScorerStep's
# _DEFAULT_TOP_K precedent.
_DEFAULT_TOP_K = 100


@step_registry.register("dense_scorer")
@dataclass(frozen=True, slots=True)
class DenseScorerStep(RetrieverStep):
    """Post-fusion dense re-ranker over an upstream candidate ChunkList.

    The step is a pure scorer-and-reorder: it does not fetch new
    candidates. It expects ``state.candidates`` to be a :class:`ChunkList`
    whose items carry SQLite chunk ids (set by an upstream fetcher / fusion
    step). Member candidates carry no dense vectors — for a
    ``ModuleMemberList`` candidate the step is a no-op.

    When ``publish_to`` is set, the re-ranked output is ALSO written to
    ``state.scratch[publish_to]`` (same payload as ``state.candidates``)
    so a downstream :class:`RRFFusionStep` can fuse multiple branch
    rankings (mirrors :class:`LateInteractionScorerStep.publish_to`).
    """

    store: VectorScoreable
    embedder: Embedder
    top_k: int = _DEFAULT_TOP_K
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default="dense_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        # Member candidates carry no dense vectors — scoring is a no-op.
        if not isinstance(state.candidates, ChunkList):
            return state
        items = state.candidates.items
        if not items:
            return state

        ids = [c.id for c in items if c.id is not None]
        if not ids:
            return state

        query_text = state.query.terms.strip()
        if not query_text:
            # Empty-terms guard mirrors DenseFetcherStep: skip scoring rather
            # than embed whitespace. Real SearchQuery objects strip at
            # construction, so this only defends direct RetrieverState
            # builders — but the strip keeps the embedded text identical to
            # the fetcher's, so both share one query-cache key (W4).
            return state

        query_vec = await self.embedder.embed_query(query_text)
        if is_multi_vector(query_vec):
            # Multi-vector → degraded single-vector fallback (matches
            # DenseFetcherStep). TurboQuant persistence is single-vector
            # only today.
            query_vec = query_vec[0]
        query_vec = np.asarray(query_vec, dtype=np.float32)

        ranked = await self.store.score(
            query_vec,
            subset_chunk_ids=ids,
            top_k=self.top_k,
        )

        # Reverse-map scores onto candidates by id; only PRESENT (scored)
        # candidates take this path — absent ones are handled below.
        chunk_by_id = {c.id: c for c in items if c.id is not None}
        scored = [
            replace(chunk_by_id[cid], relevance=score, retriever_name="turboquant_dense")
            for cid, score in ranked
            if cid in chunk_by_id
        ]
        # Backend returns ranked already, but re-sort defensively so the
        # output contract is "descending by relevance" regardless of
        # backend ordering quirks (mirrors LateInteractionScorerStep).
        scored.sort(key=lambda c: c.relevance or 0.0, reverse=True)

        # Candidates turbovec did NOT score (absent from the .tq subset —
        # BM25-only, multi-vector-only, selective-embed) are NOT dropped:
        # keep their incoming fused order and sink them below the re-scored
        # set, so BM25 recall survives the dense re-rank.
        scored_ids = {cid for cid, _ in ranked}
        absent = [c for c in items if c.id not in scored_ids]

        new_candidates = ChunkList(items=tuple(scored + absent))

        # WHY: use a fresh scratch dict — this step MAY run inside a
        # ParallelStep branch, where in-place mutation of the input scratch
        # leaks across sibling branches (CLAUDE.md §"RetrieverState.scratch
        # mutation discipline").
        if self.publish_to is not None:
            new_scratch = {**state.scratch, self.publish_to: new_candidates}
            return replace(state, candidates=new_candidates, scratch=new_scratch)
        return replace(state, candidates=new_candidates)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "dense_scorer"}
        if self.top_k != _DEFAULT_TOP_K:
            d["top_k"] = self.top_k
        if self.publish_to is not None:
            d["publish_to"] = self.publish_to
        return d

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], context: BuildContext) -> DenseScorerStep:
        if context.vector_store is None:
            raise ValueError(
                "DenseScorerStep requires BuildContext.vector_store to be set. "
                "Provide it via build_retrieval_context(...) at server/CLI "
                "startup.",
            )
        if context.embedder is None:
            raise ValueError(
                "DenseScorerStep requires BuildContext.embedder to be set.",
            )
        if not isinstance(context.vector_store, VectorScoreable):
            raise ValueError(
                "DenseScorerStep requires a re-rank-capable vector_store "
                "(VectorScoreable with score()). The configured "
                "search_backend does not provide subset dense scoring — set "
                "search_backend.kind to a scoreable-capable backend in your "
                "AppConfig YAML, or remove the dense_scorer step from this "
                "pipeline.",
            )
        return cls(
            store=context.vector_store,
            embedder=context.embedder,
            top_k=int(data.get("top_k", _DEFAULT_TOP_K)),
            publish_to=data.get("publish_to"),
        )


__all__ = ("DenseScorerStep",)
