"""TopKFilterStep — uniform top-K cutoff for chunk and member pipelines.

Single responsibility: keep the top K candidates by ``relevance``
descending. If no candidate carries a relevance value (e.g., no scorer
ran upstream — :class:`MemberFetcherStep` produces unscored results
from LIKE), falls back to source order and takes the first K.

Works for both :class:`ChunkList` and :class:`ModuleMemberList` —
they share the ``items`` + ``relevance`` shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from pydocs_mcp.models import ChunkList, ModuleMemberList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry

# WHY: single source of truth for the top-K cutoff. Referenced from the
# dataclass field default + to_dict (omit-when-default) + from_dict
# (fallback when YAML omits the key).
_DEFAULT_K = 50


@step_registry.register("top_k_filter")
@dataclass(frozen=True, slots=True)
class TopKFilterStep(RetrieverStep):
    """Top-K cutoff step. Works uniformly for chunks and members.

    When ``publish_to`` is set, the ranked output is ALSO written to
    ``state.scratch[publish_to]`` (same payload as ``state.candidates``).
    This is how parallel branches publish their rankings for
    :class:`RRFFusionStep` to consume (spec §5.8, AC-20).
    """

    k: int = field(default=_DEFAULT_K, kw_only=True)
    # WHY: optional scratch-publish key for the parallel-branch / RRF
    # hand-off. Default None preserves the legacy single-pipeline
    # behavior — no scratch mutation. Set to e.g. ``"bm25.ranked"`` to
    # hand the ranked list to a downstream :class:`RRFFusionStep`.
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default="top_k_filter", kw_only=True)

    async def run(self, state: RetrieverState) -> RetrieverState:
        if state.candidates is None:
            return state
        items = state.candidates.items
        if not items:
            return state
        # Sort by relevance desc when at least one candidate has it set,
        # otherwise preserve source order (LIKE results have no rank).
        has_relevance = any(getattr(c, "relevance", None) is not None for c in items)
        if has_relevance:
            sorted_items = tuple(sorted(items, key=lambda c: c.relevance or 0.0, reverse=True))
        else:
            sorted_items = tuple(items)
        new_items = sorted_items[: self.k]
        if isinstance(state.candidates, ChunkList):
            new_candidates: ChunkList | ModuleMemberList = ChunkList(items=new_items)
        elif isinstance(state.candidates, ModuleMemberList):
            new_candidates = ModuleMemberList(items=new_items)
        else:
            return state
        if self.publish_to is not None:
            # Use ``dataclasses.replace`` with a fresh scratch dict so the
            # caller's input ``state.scratch`` is never aliased through.
            # Required for safe composition inside ``ParallelStep``:
            # without this, a TopKFilterStep inside a branch would mutate
            # the branch's input scratch (already a copy) but the in-place
            # writes would still violate the narrowed "no in-place
            # mutation" contract for any step that may run in parallel.
            # See RetrieverState docstring §"Mutation contract".
            new_scratch = {**state.scratch, self.publish_to: new_candidates}
            return replace(
                state,
                candidates=new_candidates,
                scratch=new_scratch,
            )
        return replace(state, candidates=new_candidates)

    def to_dict(self) -> dict:
        d: dict = {"type": "top_k_filter"}
        if self.k != _DEFAULT_K:
            d["k"] = self.k
        if self.publish_to is not None:
            d["publish_to"] = self.publish_to
        return d

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> TopKFilterStep:
        return cls(
            k=data.get("k", _DEFAULT_K),
            publish_to=data.get("publish_to"),
        )


__all__ = ("TopKFilterStep",)
