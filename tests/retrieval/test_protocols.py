"""Protocol smoke tests."""
from __future__ import annotations

from pydocs_mcp.retrieval.protocols import (
    ChunkRetriever,
    ConnectionProvider,
    ModuleMemberRetriever,
    PipelineStage,
    ResultFormatter,
    Retriever,
)


def test_protocol_imports():
    assert hasattr(Retriever, "__mro__")
    assert hasattr(PipelineStage, "__mro__")
    assert hasattr(ConnectionProvider, "__mro__")
    assert hasattr(ResultFormatter, "__mro__")


def test_chunk_retriever_subtypes_retriever():
    assert Retriever in ChunkRetriever.__mro__


def test_module_member_retriever_subtypes_retriever():
    assert Retriever in ModuleMemberRetriever.__mro__
