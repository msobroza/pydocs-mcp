"""CentralityPriorStep — boost structurally central candidates ("god nodes").

A rerank-only step (adds no candidates, so it cannot hurt recall): it multiplies
each candidate's dense relevance by a mild prior derived from its node_scores
graph signal (PageRank by default, or in-degree), so a heavily-referenced core
API outranks an obscure leaf at near-equal dense similarity.

Reads node_scores via ``uow.node_scores.scores_for`` keyed on
``chunk.metadata['qualified_name']``. The prior is normalised within the
candidate set, so the boost is relative to the strongest candidate. Safety: a
non-Chunk / empty candidate list, or candidates with no node score, pass through
unchanged (the table is empty unless reference_graph.node_scores is enabled).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.storage.protocols import UnitOfWork

_DEFAULT_METRIC = "pagerank"
_DEFAULT_ALPHA = 0.5
_DEFAULT_NAME = "centrality_prior"
_VALID_METRICS = frozenset({"pagerank", "in_degree"})

_QNAME_KEY = "qualified_name"


@step_registry.register("centrality_prior")
@dataclass(frozen=True, slots=True)
class CentralityPriorStep(RetrieverStep):
    """Multiply candidate relevance by a normalised centrality prior."""

    uow_factory: Callable[[], UnitOfWork] = field(kw_only=True)
    metric: str = field(default=_DEFAULT_METRIC, kw_only=True)
    alpha: float = field(default=_DEFAULT_ALPHA, kw_only=True)
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

        raw = {qn: self._metric_value(s) for qn, s in scores.items()}
        peak = max(raw.values(), default=0.0)
        if peak <= 0.0:
            return state

        boosted = [self._boost(c, raw, peak) for c in candidates.items]
        boosted.sort(key=lambda c: c.relevance or 0.0, reverse=True)
        return replace(state, candidates=ChunkList(items=tuple(boosted)))

    def _metric_value(self, score: object) -> float:
        if self.metric == "in_degree":
            # log1p compresses the heavy tail of fan-in counts.
            return math.log1p(getattr(score, "in_degree", 0))
        return float(getattr(score, "pagerank", 0.0))

    def _boost(self, chunk: Chunk, raw: dict[str, float], peak: float) -> Chunk:
        qname = chunk.metadata.get(_QNAME_KEY)
        prior = raw.get(qname, 0.0) / peak if qname else 0.0
        base = chunk.relevance or 0.0
        return replace(chunk, relevance=base * (1.0 + self.alpha * prior))

    def to_dict(self) -> dict:
        d: dict = {"type": "centrality_prior"}
        if self.metric != _DEFAULT_METRIC:
            d["metric"] = self.metric
        if self.alpha != _DEFAULT_ALPHA:
            d["alpha"] = self.alpha
        if self.name != _DEFAULT_NAME:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> CentralityPriorStep:
        if context.uow_factory is None:
            raise ValueError(
                "CentralityPriorStep requires BuildContext.uow_factory. "
                "Production wiring in __main__.py / server.py sets this.",
            )
        metric = data.get("metric", _DEFAULT_METRIC)
        if metric not in _VALID_METRICS:
            raise ValueError(
                f"CentralityPriorStep.metric must be one of {sorted(_VALID_METRICS)}; got {metric!r}.",
            )
        return cls(
            uow_factory=context.uow_factory,
            metric=metric,
            alpha=data.get("alpha", _DEFAULT_ALPHA),
            name=data.get("name", _DEFAULT_NAME),
        )


__all__ = ("CentralityPriorStep",)
