"""Retrieval subpackage — async pipelines, steps, registries.

Public API surface. Concrete class re-exports live in submodules; users
typically construct pipelines inline, or load them from YAML via config.py.

Importing this package eagerly loads ``steps``, ``formatters`` and
``route_predicates`` so their ``@registry.register`` decorators fire and
the shared registries are populated (spec AC #30).
"""

from pydocs_mcp.retrieval import formatters as _formatters  # noqa: F401
from pydocs_mcp.retrieval import route_predicates as _route_predicates  # noqa: F401

# Side-effect imports — populate the stage/formatter/predicate registries
# at package import time so bare ``import pydocs_mcp.retrieval`` is a
# sufficient precondition for config-driven pipeline assembly.
from pydocs_mcp.retrieval import steps as _steps  # noqa: F401
from pydocs_mcp.retrieval.pipeline import (
    CodeRetrieverPipeline,
    PerCallConnectionProvider,
    PipelineState,
    RetrieverPipeline,
    RetrieverState,
    RetrieverStep,
)
from pydocs_mcp.retrieval.protocols import (
    ConnectionProvider,
    ResultFormatter,
)
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    ComponentRegistry,
    formatter_registry,
    step_registry,
)

__all__ = [
    "BuildContext",
    "CodeRetrieverPipeline",
    "ComponentRegistry",
    "ConnectionProvider",
    "PerCallConnectionProvider",
    "PipelineState",
    "ResultFormatter",
    "RetrieverPipeline",
    "RetrieverState",
    "RetrieverStep",
    "formatter_registry",
    "step_registry",
]
