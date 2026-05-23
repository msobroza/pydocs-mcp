"""Concrete retrievers — one file per strategy (spec §5.7).

Each retriever consumes its dependencies via :class:`BuildContext` and
registers itself with :data:`retriever_registry` at import time:

- :mod:`.bm25_chunk` — :class:`Bm25ChunkRetriever` (FTS / BM25 chunks)
- :mod:`.like_member` — :class:`LikeMemberRetriever` (LIKE-substring
  search over ``module_members``)

Shared scope-splitting and schema-validation helpers live in
:mod:`._shared` so the two metadata-aware retrievers (BM25 + LIKE)
enforce the same policy.

Task 7: the ``PipelineChunkRetriever`` / ``PipelineModuleMemberRetriever``
pipeline-adapter retrievers were deleted — services consume the YAML
pipeline directly via ``chunk_pipeline.run(query)`` (no Retriever
shim). Retrievers slated for removal alongside ``CodeRetrieverPipeline``
in Task 9.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.retrievers.base_retriever import (
    ChunkRetriever,
    ModuleMemberRetriever,
    Retriever,
)
from pydocs_mcp.retrieval.retrievers.bm25_chunk import Bm25ChunkRetriever
from pydocs_mcp.retrieval.retrievers.like_member import LikeMemberRetriever

__all__ = (
    "Bm25ChunkRetriever",
    "ChunkRetriever",
    "LikeMemberRetriever",
    "ModuleMemberRetriever",
    "Retriever",
)
