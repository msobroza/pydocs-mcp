"""Retrieval-pipeline steps — one file per concrete step.

Re-exports every step class so existing imports
(``from pydocs_mcp.retrieval.steps import LimitStep``) keep
working without each call site needing to learn the submodule path.

Module layout:

- :mod:`.bm25_scorer` — :class:`BM25ScorerStep`
- :mod:`.chunk_fetcher` — :class:`ChunkFetcherStep`
- :mod:`.dense_fetcher` — :class:`DenseFetcherStep`
- :mod:`.member_fetcher` — :class:`MemberFetcherStep`
- :mod:`.metadata_post_filter` — :class:`MetadataPostFilterStep`
- :mod:`.limit` — :class:`LimitStep`
- :mod:`.parallel` — :class:`ParallelStep`
- :mod:`.pre_filter` — :class:`PreFilterStep` + :class:`PreFilterResult`
- :mod:`.rrf_fusion` — :class:`RRFFusionStep` + :class:`RRFResultFuser`
- :mod:`.conditional` — :class:`ConditionalStep`
- :mod:`.route` — :class:`RouteCase` + :class:`RouteStep`
- :mod:`.sub_pipeline` — ``sub_pipeline`` YAML decoder (no class — returns
  a bare nested ``CodeRetrieverPipeline`` since pipelines subclass
  :class:`RetrieverStep` directly)
- :mod:`.token_budget` — :class:`TokenBudgetStep` + ``COMPOSITE_TITLE_SENTINEL``
- :mod:`.top_k_filter` — :class:`TopKFilterStep`

Every step subclasses :class:`~pydocs_mcp.retrieval.pipeline.RetrieverStep`.
The legacy ``PipelineStage`` Protocol re-export module (``base_stage.py``)
went away in Task 9 alongside the Protocol itself.
"""
from __future__ import annotations

# Side-effect import: register the "sub_pipeline" YAML decoder so existing
# YAML keeps loading. The module exports no public symbols.
from pydocs_mcp.retrieval.steps import sub_pipeline as _sub_pipeline  # noqa: F401
from pydocs_mcp.retrieval.steps.bm25_scorer import BM25ScorerStep
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.conditional import ConditionalStep
from pydocs_mcp.retrieval.steps.dense_fetcher import DenseFetcherStep
from pydocs_mcp.retrieval.steps.limit import LimitStep
from pydocs_mcp.retrieval.steps.member_fetcher import MemberFetcherStep
from pydocs_mcp.retrieval.steps.metadata_post_filter import MetadataPostFilterStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep
from pydocs_mcp.retrieval.steps.pre_filter import PreFilterResult, PreFilterStep
from pydocs_mcp.retrieval.steps.route import RouteCase, RouteStep
from pydocs_mcp.retrieval.steps.rrf_fusion import RRFFusionStep, RRFResultFuser
from pydocs_mcp.retrieval.steps.token_budget import (
    COMPOSITE_TITLE_SENTINEL,
    TokenBudgetStep,
)
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep

__all__ = (
    "COMPOSITE_TITLE_SENTINEL",
    "BM25ScorerStep",
    "ChunkFetcherStep",
    "ConditionalStep",
    "DenseFetcherStep",
    "LimitStep",
    "MemberFetcherStep",
    "MetadataPostFilterStep",
    "ParallelStep",
    "PreFilterResult",
    "PreFilterStep",
    "RRFFusionStep",
    "RRFResultFuser",
    "RouteCase",
    "RouteStep",
    "TokenBudgetStep",
    "TopKFilterStep",
)
