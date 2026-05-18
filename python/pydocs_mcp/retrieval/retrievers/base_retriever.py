"""Shared base / contracts for retrievers.

Re-exports the three Protocols from :mod:`pydocs_mcp.retrieval.protocols`
so each concrete-retriever file imports its contract from one obvious
place. Chunk- and member-flavored variants share the structural shape
of :class:`Retriever` but expose narrower return types.
"""
from __future__ import annotations

from pydocs_mcp.retrieval.protocols import (
    ChunkRetriever,
    ModuleMemberRetriever,
    Retriever,
)

__all__ = ("ChunkRetriever", "ModuleMemberRetriever", "Retriever")
