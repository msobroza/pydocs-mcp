"""Retrieval subpackage — async pipelines, retrievers, stages, registries.

Public API surface. Concrete class re-exports live in submodules; users
typically construct pipelines inline, or load them from YAML via config.py.
"""
from pydocs_mcp.retrieval.pipeline import (
    CodeRetrieverPipeline,
    PerCallConnectionProvider,
    PipelineState,
)
from pydocs_mcp.retrieval.protocols import (
    ChunkRetriever,
    ConnectionProvider,
    ModuleMemberRetriever,
    PipelineStage,
    ResultFormatter,
    Retriever,
)
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    ComponentRegistry,
    formatter_registry,
    retriever_registry,
    stage_registry,
)

__all__ = [
    "BuildContext",
    "ChunkRetriever",
    "CodeRetrieverPipeline",
    "ComponentRegistry",
    "ConnectionProvider",
    "ModuleMemberRetriever",
    "PerCallConnectionProvider",
    "PipelineStage",
    "PipelineState",
    "ResultFormatter",
    "Retriever",
    "formatter_registry",
    "retriever_registry",
    "stage_registry",
]
