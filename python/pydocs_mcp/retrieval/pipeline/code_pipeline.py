"""``CodeRetrieverPipeline`` — YAML-loadable linear async pipeline.

A ``CodeRetrieverPipeline`` is a tuple of ``RetrieverStep``s threaded
linearly: each step receives the state produced by the previous step.

The class subclasses :class:`RetrieverStep` so it can be slotted as a
nested step inside a ``RouteStep`` / ``ParallelStep`` / ``ConditionalStep``
— the polymorphism is a direct nominal subtype rather than the previous
structural ``PipelineStage`` Protocol.

``CodeRetrieverPipeline.run`` is polymorphic in input: legacy callers
pass a :class:`SearchQuery` (a fresh state is constructed); composed
callers pass an incoming :class:`RetrieverState` (which is threaded —
not reset — through the inner steps). This is what lets the same class
be the top-level pipeline of a service AND a nested step inside another
pipeline.

Sibling of :class:`RetrieverPipeline` in :mod:`pydocs_mcp.retrieval.pipeline.base`:
- :class:`RetrieverPipeline` is the sklearn-shaped ``(name, step)`` tuple
  class with no YAML support — used for typed in-code construction.
- :class:`CodeRetrieverPipeline` is the YAML-loadable form with a
  ``to_dict`` / ``from_dict`` round-trip and a ``stages: tuple[RetrieverStep, ...]``
  shape — used by config.py and the shipped pipeline blueprints.

Both subclass :class:`RetrieverStep`, so they compose uniformly under
``RouteStep`` and friends.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.retrieval.pipeline.base import RetrieverStep
from pydocs_mcp.retrieval.pipeline.state import RetrieverState

if TYPE_CHECKING:
    from pydocs_mcp.models import SearchQuery
    from pydocs_mcp.retrieval.serialization import BuildContext


# Stops a malicious / recursive YAML from blowing the Python stack when
# nested-pipeline chains (``type: sub_pipeline`` decoder, Pipeline-as-Step)
# nest deeply. 32 levels is already far more than any legitimate pipeline
# the project ships.
_MAX_PIPELINE_DEPTH = 32


class PipelineLoadError(PydocsMCPError, ValueError):
    """Raised by :meth:`CodeRetrieverPipeline.from_dict` on YAML schema violations.

    The YAML schema uses ``steps:`` (each step requires a ``name:``). This
    error surfaces clear migration diagnostics rather than letting a
    misshapen YAML produce a confusing low-level ``KeyError`` deep in the
    stage registry.
    """


@dataclass(frozen=True, slots=True)
class CodeRetrieverPipeline(RetrieverStep):
    """Linear async pipeline of ``RetrieverStep``s; runs them in order.

    Doubles as a ``RetrieverStep`` itself — calling ``run(state)`` threads
    an incoming ``RetrieverState`` through the steps, while ``run(query)``
    creates a fresh state from the ``SearchQuery``. This polymorphism lets
    nested pipelines compose directly under a ``RouteStep`` without an
    adapter class.
    """

    stages: tuple[RetrieverStep, ...] = ()

    async def run(self, query_or_state: SearchQuery | RetrieverState) -> RetrieverState:
        # Polymorphic entry: legacy callers pass a SearchQuery; nested use
        # (Pipeline-as-Step) passes an incoming RetrieverState that must be
        # threaded — not reset — through the inner stages.
        if isinstance(query_or_state, RetrieverState):
            state = query_or_state
        else:
            state = RetrieverState(query=query_or_state)
        for stage in self.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        # Emit the ``steps:`` schema. Each step's ``to_dict`` returns
        # ``{"type": ..., ...flat-params}``; we wrap into the new
        # ``{name, type, params}`` shape so the round-trip lands on
        # ``CodeRetrieverPipeline.from_dict`` cleanly.
        return {
            "name": self.name,
            "steps": [
                _step_to_dict(stage, idx) for idx, stage in enumerate(self.stages)
            ],
        }

    @classmethod
    def from_dict(
        cls,
        data: dict,
        context: BuildContext,
        _depth: int = 0,
    ) -> CodeRetrieverPipeline:
        if _depth > _MAX_PIPELINE_DEPTH:
            raise ValueError(
                f"pipeline nesting exceeds max depth of {_MAX_PIPELINE_DEPTH}"
            )
        # Reject the legacy ``stages:`` schema with a migration error so
        # users get a clear diagnostic instead of a confusing "missing
        # 'steps' key" KeyError.
        if "stages" in data:
            raise PipelineLoadError(
                "'stages:' key is no longer accepted "
                "(retrieval-pipeline-refactor). "
                "Use 'steps:' with a 'name:' per step. "
                "See pipelines/chunk_search.yaml for the canonical shape."
            )
        if "steps" not in data:
            raise PipelineLoadError(
                "pipeline YAML missing required 'steps:' key "
                f"(pipeline name={data.get('name', '<unnamed>')!r}). "
                "See pipelines/chunk_search.yaml for the canonical shape."
            )
        steps_data = data["steps"]
        stages: list = []
        for idx, step in enumerate(steps_data):
            if "name" not in step:
                raise PipelineLoadError(
                    f"pipeline step #{idx} missing required 'name:' "
                    f"(pipeline name={data.get('name', '<unnamed>')!r}, "
                    f"type={step.get('type', '<missing>')!r}). "
                    "Every step in a 'steps:' list must declare a unique 'name'."
                )
            stages.append(_step_from_dict(step, context, _depth=_depth))
        return cls(name=data["name"], stages=tuple(stages))


def _step_to_dict(stage: object, idx: int) -> dict:
    """Wrap a step's ``to_dict()`` shape into the new ``steps:`` entry shape.

    Step-level ``to_dict`` returns ``{"type": ..., ...flat-params}``; the
    pipeline-level schema is ``{"name": ..., "type": ..., "params": {...flat-params}}``.
    """
    raw = stage.to_dict() if hasattr(stage, "to_dict") else {"type": "unknown"}
    name = getattr(stage, "name", None) or f"step_{idx}"
    type_name = raw.pop("type", "unknown")
    return {"name": name, "type": type_name, "params": raw}


def _step_from_dict(
    step: dict, context: BuildContext, _depth: int,
) -> object:
    """Build a single stage from the ``{name, type, params}`` entry shape.

    Backward-compat for ``to_dict`` consumers that expect flat fields:
    merges ``params`` back into the entry before handing it to the stage
    registry. Per-stage ``from_dict`` keeps reading flat keys (e.g.
    ``data["max_results"]``), so registering new step types didn't require
    touching every existing decoder.
    """
    params = step.get("params") or {}
    if not isinstance(params, dict):
        raise PipelineLoadError(
            f"step {step.get('name', '<unnamed>')!r}: 'params' must be a mapping; "
            f"got {type(params).__name__}"
        )
    # WHY: merge params back into top-level so registered ``from_dict``
    # implementations can read e.g. ``data["max_results"]`` without
    # having to learn the nested-params shape.
    merged: dict = dict(params)
    merged["type"] = step["type"]
    return context.step_registry.build(merged, context, _depth=_depth)


__all__ = ("CodeRetrieverPipeline", "PipelineLoadError")
