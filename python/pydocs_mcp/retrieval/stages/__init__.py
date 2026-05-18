"""Retrieval-pipeline stages — one file per concrete stage.

Re-exports every stage class so existing imports
(``from pydocs_mcp.retrieval.stages import ChunkRetrievalStage``) keep
working without each call site needing to learn the submodule path.

Module layout:

- :mod:`.base_stage` — :class:`PipelineStage` Protocol (re-exported)
- :mod:`.chunk_retrieval` — :class:`ChunkRetrievalStage`
- :mod:`.module_member_retrieval` — :class:`ModuleMemberRetrievalStage`
- :mod:`.metadata_post_filter` — :class:`MetadataPostFilterStage`
- :mod:`.limit` — :class:`LimitStage`
- :mod:`.parallel_retrieval` — :class:`ParallelRetrievalStage`
- :mod:`.reciprocal_rank_fusion` — :class:`ReciprocalRankFusionStage`
- :mod:`.conditional` — :class:`ConditionalStage`
- :mod:`.route` — :class:`RouteCase` + :class:`RouteStage`
- :mod:`.sub_pipeline` — :class:`SubPipelineStage`
- :mod:`.token_budget` — :class:`TokenBudgetStage` + ``COMPOSITE_TITLE_SENTINEL``
"""
from __future__ import annotations

from pydocs_mcp.retrieval.stages.base_stage import PipelineStage
from pydocs_mcp.retrieval.stages.chunk_retrieval import ChunkRetrievalStage
from pydocs_mcp.retrieval.stages.conditional import ConditionalStage
from pydocs_mcp.retrieval.stages.limit import LimitStage
from pydocs_mcp.retrieval.stages.metadata_post_filter import MetadataPostFilterStage
from pydocs_mcp.retrieval.stages.module_member_retrieval import ModuleMemberRetrievalStage
from pydocs_mcp.retrieval.stages.parallel_retrieval import ParallelRetrievalStage
from pydocs_mcp.retrieval.stages.reciprocal_rank_fusion import ReciprocalRankFusionStage
from pydocs_mcp.retrieval.stages.route import RouteCase, RouteStage
from pydocs_mcp.retrieval.stages.sub_pipeline import SubPipelineStage
from pydocs_mcp.retrieval.stages.token_budget import (
    COMPOSITE_TITLE_SENTINEL,
    TokenBudgetStage,
)

__all__ = (
    "COMPOSITE_TITLE_SENTINEL",
    "ChunkRetrievalStage",
    "ConditionalStage",
    "LimitStage",
    "MetadataPostFilterStage",
    "ModuleMemberRetrievalStage",
    "ParallelRetrievalStage",
    "PipelineStage",
    "ReciprocalRankFusionStage",
    "RouteCase",
    "RouteStage",
    "SubPipelineStage",
    "TokenBudgetStage",
)
