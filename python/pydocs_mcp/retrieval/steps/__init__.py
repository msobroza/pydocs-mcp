"""Retrieval-pipeline stages — one file per concrete stage.

Re-exports every stage class so existing imports
(``from pydocs_mcp.retrieval.steps import ChunkRetrievalStep``) keep
working without each call site needing to learn the submodule path.

Module layout:

- :mod:`.base_stage` — :class:`PipelineStage` Protocol (re-exported)
- :mod:`.bm25_scorer` — :class:`BM25ScorerStep`
- :mod:`.chunk_fetcher` — :class:`ChunkFetcherStep`
- :mod:`.chunk_retrieval` — :class:`ChunkRetrievalStep` (legacy adapter)
- :mod:`.module_member_retrieval` — :class:`ModuleMemberRetrievalStep`
- :mod:`.metadata_post_filter` — :class:`MetadataPostFilterStep`
- :mod:`.limit` — :class:`LimitStep`
- :mod:`.parallel` — :class:`ParallelStep`
- :mod:`.rrf` — :class:`RRFStep`
- :mod:`.conditional` — :class:`ConditionalStep`
- :mod:`.route` — :class:`RouteCase` + :class:`RouteStep`
- :mod:`.sub_pipeline` — :class:`SubPipelineStep`
- :mod:`.token_budget` — :class:`TokenBudgetStep` + ``COMPOSITE_TITLE_SENTINEL``
- :mod:`.top_k_filter` — :class:`TopKFilterStep`
"""
from __future__ import annotations

from pydocs_mcp.retrieval.steps.base_stage import PipelineStage
from pydocs_mcp.retrieval.steps.bm25_scorer import BM25ScorerStep
from pydocs_mcp.retrieval.steps.chunk_fetcher import ChunkFetcherStep
from pydocs_mcp.retrieval.steps.chunk_retrieval import ChunkRetrievalStep
from pydocs_mcp.retrieval.steps.conditional import ConditionalStep
from pydocs_mcp.retrieval.steps.limit import LimitStep
from pydocs_mcp.retrieval.steps.metadata_post_filter import MetadataPostFilterStep
from pydocs_mcp.retrieval.steps.module_member_retrieval import ModuleMemberRetrievalStep
from pydocs_mcp.retrieval.steps.parallel import ParallelStep
from pydocs_mcp.retrieval.steps.route import RouteCase, RouteStep
from pydocs_mcp.retrieval.steps.rrf import RRFStep
from pydocs_mcp.retrieval.steps.sub_pipeline import SubPipelineStep
from pydocs_mcp.retrieval.steps.token_budget import (
    COMPOSITE_TITLE_SENTINEL,
    TokenBudgetStep,
)
from pydocs_mcp.retrieval.steps.top_k_filter import TopKFilterStep

__all__ = (
    "COMPOSITE_TITLE_SENTINEL",
    "BM25ScorerStep",
    "ChunkFetcherStep",
    "ChunkRetrievalStep",
    "ConditionalStep",
    "LimitStep",
    "MetadataPostFilterStep",
    "ModuleMemberRetrievalStep",
    "ParallelStep",
    "PipelineStage",
    "RRFStep",
    "RouteCase",
    "RouteStep",
    "SubPipelineStep",
    "TokenBudgetStep",
    "TopKFilterStep",
)
