"""DenseScorerStep — overwrite candidate.relevance with cosine similarity (AC-18).

Mirrors BM25ScorerStep's shape:
- Reads state.candidates (no DB access).
- Embeds query via the injected Embedder.
- For each candidate, computes cosine sim between query_vec and
  candidate.embedding using numpy linalg.
- Writes the scores back to state.candidates with updated relevance.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np

from pydocs_mcp.models import ChunkList, is_multi_vector
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import Embedder


def _cosine_sim(u: np.ndarray, v: np.ndarray) -> float:
    nu = float(np.linalg.norm(u))
    nv = float(np.linalg.norm(v))
    if nu == 0.0 or nv == 0.0:
        return 0.0
    return float(np.dot(u, v) / (nu * nv))


@step_registry.register("dense_scorer")
@dataclass(frozen=True, slots=True)
class DenseScorerStep(RetrieverStep):
    """Score normalization step for dense (vector-search) chunk pipelines.

    Mirrors :class:`BM25ScorerStep` on the dense side: read candidates,
    embed the query, compute cosine similarity per candidate vector, and
    write the scores back. ``ModuleMemberList`` candidates carry no
    embeddings so this step is a no-op for them.

    Multi-vector embeddings (ColBERT-style ``list[np.ndarray]``) collapse
    to the first token-vector at the boundary — matches
    :class:`DenseFetcherStep`'s contract since TurboQuant persistence is
    single-vector only today.
    """

    embedder: Embedder = field(default=None)  # type: ignore[assignment]
    name: str = field(default="dense_scorer", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        # Member candidates carry no embeddings — scoring is a no-op for them.
        if not isinstance(state.candidates, ChunkList):
            return state
        if not state.candidates.items:
            return state

        query_vec = await self.embedder.embed_query(state.query.terms)
        if is_multi_vector(query_vec):
            # Multi-vector → degraded single-vector fallback (matches
            # DenseFetcherStep). A future PR adding multi-vector
            # persistence can flip this without changing the contract.
            query_vec = query_vec[0]
        query_vec = np.asarray(query_vec, dtype=np.float32)

        scored = []
        for c in state.candidates.items:
            if c.embedding is None:
                # No embedding to score against — pass the candidate through
                # unchanged so the pipeline doesn't drop it silently.
                scored.append(c)
                continue
            chunk_vec = c.embedding[0] if is_multi_vector(c.embedding) else c.embedding
            chunk_vec = np.asarray(chunk_vec, dtype=np.float32)
            score = _cosine_sim(query_vec, chunk_vec)
            scored.append(replace(c, relevance=score))
        return replace(state, candidates=ChunkList(items=tuple(scored)))

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "DenseScorerStep":
        if context.embedder is None:
            raise ValueError(
                "DenseScorerStep requires BuildContext.embedder to be set.",
            )
        return cls(embedder=context.embedder)

    def to_dict(self) -> dict:
        return {"type": "dense_scorer"}


__all__ = ("DenseScorerStep",)
