"""LimitStep — cap the item count, and report what the cap dropped.

Two caps meet here. ``SearchQuery.max_results`` is the caller's own (the MCP
``search_codebase(limit=…)``, already bounded by ``search.output.max_limit``);
``LimitStep.max_results`` is the deployment default a YAML preset configures,
applied when the query carries no cap of its own.

Task 8: operates on ``state.candidates`` (the intermediate ranked list) when
present, falling back to ``state.result`` for backward compatibility with code
that hasn't migrated to the candidates/result split.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList, PipelineResultItem, SearchQuery
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps._constants import LIMIT_DROPPED_SCRATCH_KEY

# WHY: single source of truth for the legacy AC max-results cap.
# Referenced from the dataclass field default + to_dict (omit-when-default)
# + from_dict (fallback when YAML omits the key).
_DEFAULT_MAX_RESULTS = 8


def rows_dropped_by_limit(state: RetrieverState) -> int:
    """How many ranked rows this pipeline's limit step cut (0 when none).

    The read side of :data:`LIMIT_DROPPED_SCRATCH_KEY`, so an application
    service can mark a capped listing as partial without knowing the scratch
    convention.

    Example: ``rows_dropped_by_limit(await pipeline.run(query))``.
    """
    dropped = state.scratch.get(LIMIT_DROPPED_SCRATCH_KEY, 0)
    return dropped if isinstance(dropped, int) else 0


@step_registry.register("limit")
@dataclass(frozen=True, slots=True)
class LimitStep(RetrieverStep):
    max_results: int = _DEFAULT_MAX_RESULTS
    name: str = "limit"

    async def run(self, state: RetrieverState) -> RetrieverState:
        # Task 8: prefer ``state.candidates`` (post-fetch / post-score
        # intermediate). Fall back to ``state.result`` for legacy
        # composition paths still relying on the pre-Task-8 shape.
        target = state.candidates if state.candidates is not None else state.result
        if target is None:
            return state
        kept = _capped_keeping_list_type(target, self._cap(state.query))
        scratch = _scratch_with_drop(state.scratch, len(target.items) - len(kept.items))
        if state.candidates is not None:
            return replace(state, candidates=kept, scratch=scratch)
        return replace(state, result=kept, scratch=scratch)

    def _cap(self, query: SearchQuery) -> int:
        """The caller's cap when it has one, else this step's configured default."""
        return self.max_results if query.max_results is None else query.max_results

    def to_dict(self) -> dict:
        d: dict = {"type": "limit"}
        if self.max_results != _DEFAULT_MAX_RESULTS:
            d["max_results"] = self.max_results
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> LimitStep:
        return cls(max_results=data.get("max_results", _DEFAULT_MAX_RESULTS))


def _capped_keeping_list_type(source: PipelineResultItem, cap: int) -> PipelineResultItem:
    """``source`` capped at ``cap``, wrapped in the list type it arrived as."""
    capped = tuple(source.items[:cap])
    if isinstance(source, ChunkList):
        return ChunkList(items=capped)
    return ModuleMemberList(items=capped)


def _scratch_with_drop(scratch: dict[str, object], dropped: int) -> dict[str, object]:
    """A NEW scratch carrying the drop count — never an in-place write, because
    a limit step may run inside a ``ParallelStep`` branch, where an in-place
    write leaks into the sibling branches (CLAUDE.md §scratch discipline)."""
    if dropped <= 0:
        return scratch
    return {**scratch, LIMIT_DROPPED_SCRATCH_KEY: dropped}


__all__ = ("LimitStep", "rows_dropped_by_limit")
