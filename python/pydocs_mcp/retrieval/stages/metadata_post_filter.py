"""MetadataPostFilterStage — apply ``SearchQuery.post_filter`` in memory.

The filter is parsed via ``format_registry[state.query.post_filter_format]``,
so the same ``{field: value}`` / ``{field: {op: value}}`` shapes accepted
by retrievers are accepted here — only the evaluation happens on
already-fetched items instead of being pushed down into SQL (spec §5.8,
AC #13).

Composite results from :class:`TokenBudgetStage` carry the
``COMPOSITE_TITLE_SENTINEL`` marker and are bypassed so the budgeted
answer chunk never gets dropped by title-based filters (AC #34).
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.models import (
    ChunkFilterField,
    ChunkList,
    ModuleMemberList,
)
from pydocs_mcp.retrieval.pipeline import PipelineState
from pydocs_mcp.retrieval.serialization import BuildContext, stage_registry
from pydocs_mcp.retrieval.stages.token_budget import COMPOSITE_TITLE_SENTINEL
from pydocs_mcp.storage.filters import (
    All,
    FieldEq,
    FieldIn,
    FieldLike,
    Filter,
    format_registry,
)


@stage_registry.register("metadata_post_filter")
@dataclass(frozen=True, slots=True)
class MetadataPostFilterStage:
    name: str = "metadata_post_filter"

    async def run(self, state: PipelineState) -> PipelineState:
        if state.query.post_filter is None:
            return state
        if state.result is None:
            return state
        tree = format_registry[state.query.post_filter_format].parse(state.query.post_filter)

        def _keep(item) -> bool:
            if _is_composite(item):
                return True
            return _evaluate(tree, item)

        kept = tuple(item for item in state.result.items if _keep(item))
        if isinstance(state.result, ChunkList):
            return replace(state, result=ChunkList(items=kept))
        return replace(state, result=ModuleMemberList(items=kept))

    def to_dict(self) -> dict:
        return {"type": "metadata_post_filter"}

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> "MetadataPostFilterStage":
        return cls()


def _evaluate(f: Filter, item) -> bool:
    if isinstance(f, All):
        return all(_evaluate(c, item) for c in f.clauses)
    if isinstance(f, FieldEq):
        return _field_value(item, f.field) == f.value
    if isinstance(f, FieldIn):
        return _field_value(item, f.field) in f.values
    if isinstance(f, FieldLike):
        v = _field_value(item, f.field) or ""
        return f.substring.lower() in str(v).lower()
    raise NotImplementedError(f"evaluator: {type(f).__name__}")


def _is_composite(item) -> bool:
    if not hasattr(item, "metadata"):
        return False
    return item.metadata.get(ChunkFilterField.TITLE.value) == COMPOSITE_TITLE_SENTINEL


def _field_value(item, field_name: str):
    # For Chunk/ModuleMember, every useful metadata key lives in ``metadata``.
    if hasattr(item, "metadata"):
        return item.metadata.get(field_name)
    return None


__all__ = ("MetadataPostFilterStage",)
