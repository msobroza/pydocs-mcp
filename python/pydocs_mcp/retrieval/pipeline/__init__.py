"""Retrieval-pipeline abstractions.

Exposes:

- :class:`RetrieverStep` — the ABC every retrieval-pipeline step subclasses.
- :class:`RetrieverPipeline` — sklearn-shaped ``(name, step)`` pipeline class
  for typed in-code construction.
- :class:`CodeRetrieverPipeline` — YAML-loadable linear pipeline form with
  ``to_dict`` / ``from_dict`` and a ``stages: tuple[RetrieverStep, ...]``
  shape. Subclass of :class:`RetrieverStep`, composes uniformly under
  ``RouteStep`` / ``ParallelStep`` / ``ConditionalStep``.
- :class:`RetrieverState` — immutable state threaded through both forms.
- :class:`PerCallConnectionProvider` — default ``ConnectionProvider`` adapter.
- ``PipelineState`` — historical alias for :class:`RetrieverState`, kept so
  existing predicates / tests that imported the name keep working.
"""
from pydocs_mcp.retrieval.pipeline.base import RetrieverPipeline, RetrieverStep
from pydocs_mcp.retrieval.pipeline.code_pipeline import (
    _MAX_PIPELINE_DEPTH,
    CodeRetrieverPipeline,
    PipelineLoadError,
)
from pydocs_mcp.retrieval.pipeline.connection import PerCallConnectionProvider
from pydocs_mcp.retrieval.pipeline.state import RetrieverState

# Historical name. Pipelines that ship in the project all use
# ``RetrieverState`` directly; the alias keeps third-party predicates and
# the legacy test matrix readable without churning every import site.
PipelineState = RetrieverState

__all__ = (
    "CodeRetrieverPipeline",
    "PerCallConnectionProvider",
    "PipelineLoadError",
    "PipelineState",
    "RetrieverPipeline",
    "RetrieverState",
    "RetrieverStep",
    "_MAX_PIPELINE_DEPTH",
)
