"""Retrieval subpackage — async pipelines, retrievers, stages, registries.

Public API surface. Concrete class re-exports live in submodules; users
typically construct pipelines inline, or load them from YAML via config.py.

Importing this package eagerly loads ``stages``, ``retrievers``,
``formatters`` and ``predicates`` so their ``@registry.register`` decorators
fire and the shared registries are populated (spec AC #30).
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

# Side-effect imports — populate the stage/retriever/formatter/predicate
# registries at package import time so bare ``import pydocs_mcp.retrieval``
# is a sufficient precondition for config-driven pipeline assembly.
from pydocs_mcp.retrieval import stages as _stages  # noqa: F401, E402
from pydocs_mcp.retrieval import retrievers as _retrievers  # noqa: F401, E402
from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401, E402
from pydocs_mcp.retrieval import predicates as _predicates  # noqa: F401, E402

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
