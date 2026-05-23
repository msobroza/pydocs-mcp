"""Pipeline primitives: PipelineState, CodeRetrieverPipeline, PerCallConnectionProvider.

Task 8 (sub-PR #5c retrieval-pipeline-refactor): ``PipelineState`` is now an
alias for :class:`~pydocs_mcp.retrieval.pipeline.RetrieverState`. The two
types are otherwise structurally identical (``query`` / ``result`` /
``duration_ms``), so making the legacy name resolve to the new class lets
existing stages that read ``state.candidates`` (e.g. ``ChunkFetcherStep``)
compose alongside legacy stages that read ``state.result`` (e.g.
``RRFStep``) inside the same ``CodeRetrieverPipeline``. Task 9 deletes
this module entirely once ``CodeRetrieverPipeline`` is replaced by
``RetrieverPipeline``.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.models import SearchQuery
from pydocs_mcp.retrieval.pipeline.state import RetrieverState

if TYPE_CHECKING:
    from pydocs_mcp.retrieval.protocols import PipelineStage
    from pydocs_mcp.retrieval.serialization import BuildContext


# Task 8: ``PipelineState`` is now an alias for ``RetrieverState`` so both
# legacy steps (``state.result``) and new steps (``state.candidates``) can
# read/write the same state object as Task 8's YAML schema flip composes
# them in one pipeline.
PipelineState = RetrieverState


# Stops a malicious / recursive YAML from blowing the Python stack when
# nested-pipeline chains (``type: sub_pipeline`` decoder, Pipeline-as-Stage)
# nest deeply. 32 levels is already far more than any legitimate pipeline
# the project ships.
_MAX_PIPELINE_DEPTH = 32


class PipelineLoadError(ValueError):
    """Raised by :meth:`CodeRetrieverPipeline.from_dict` on YAML schema violations.

    Task 8 (retrieval-pipeline-refactor): the YAML schema was flipped from
    ``stages:`` to ``steps:`` (each step requires a ``name:``). This error
    surfaces clear migration diagnostics rather than letting a misshapen
    YAML produce a confusing low-level ``KeyError`` deep in the stage
    registry.
    """


@dataclass(frozen=True, slots=True)
class CodeRetrieverPipeline:
    """Linear async pipeline of PipelineStages; runs them in order.

    Doubles as a ``PipelineStage`` itself — calling ``run(state)`` threads
    an incoming ``PipelineState`` through the stages, while ``run(query)``
    creates a fresh state from the ``SearchQuery``. This polymorphism lets
    nested pipelines compose directly under a ``RouteStep`` without an
    adapter class.
    """

    name: str
    stages: tuple["PipelineStage", ...]

    async def run(self, query_or_state: "SearchQuery | RetrieverState") -> RetrieverState:
        # Polymorphic entry: legacy callers pass a SearchQuery; nested use
        # (Pipeline-as-Stage) passes an incoming RetrieverState that must be
        # threaded — not reset — through the inner stages.
        if isinstance(query_or_state, RetrieverState):
            state = query_or_state
        else:
            state = RetrieverState(query=query_or_state)
        for stage in self.stages:
            state = await stage.run(state)
        return state

    def to_dict(self) -> dict:
        # Task 8: emit the new ``steps:`` schema. Each step's ``to_dict``
        # returns ``{"type": ..., ...flat-params}``; we wrap into the new
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
        context: "BuildContext",
        _depth: int = 0,
    ) -> "CodeRetrieverPipeline":
        if _depth > _MAX_PIPELINE_DEPTH:
            raise ValueError(
                f"pipeline nesting exceeds max depth of {_MAX_PIPELINE_DEPTH}"
            )
        # Task 8: reject the legacy ``stages:`` schema with a migration
        # error so users get a clear diagnostic instead of a confusing
        # "missing 'steps' key" KeyError.
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
    """Wrap a legacy ``stage.to_dict()`` shape into the new ``steps:`` entry shape.

    Legacy ``to_dict`` returns ``{"type": ..., ...flat-params}``; the new
    schema is ``{"name": ..., "type": ..., "params": {...flat-params}}``.
    """
    raw = stage.to_dict() if hasattr(stage, "to_dict") else {"type": "unknown"}
    name = getattr(stage, "name", None) or f"step_{idx}"
    type_name = raw.pop("type", "unknown")
    return {"name": name, "type": type_name, "params": raw}


def _step_from_dict(
    step: dict, context: "BuildContext", _depth: int,
) -> object:
    """Build a single stage from the new ``{name, type, params}`` entry shape.

    Backward-compat for legacy ``to_dict`` consumers that expect flat
    fields: merges ``params`` back into the entry before handing it to
    the stage registry. Per-stage ``from_dict`` keeps reading flat keys
    (e.g. ``data["max_results"]``), so registering new step types didn't
    require touching every existing decoder.
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
    return context.stage_registry.build(merged, context, _depth=_depth)


@dataclass(frozen=True, slots=True)
class PerCallConnectionProvider:
    """Default ConnectionProvider — opens/closes a fresh SQLite conn per acquire()."""

    cache_path: Path

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[sqlite3.Connection]:
        connection = await asyncio.to_thread(self._open)
        try:
            yield connection
        finally:
            await asyncio.to_thread(connection.close)

    def _open(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.cache_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn
