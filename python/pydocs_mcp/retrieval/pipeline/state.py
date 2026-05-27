"""RetrieverState — immutable typed state threaded through a RetrieverPipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.models import (
        ChunkList,
        ModuleMemberList,
        PipelineResultItem,
        SearchQuery,
    )


@dataclass(frozen=True, slots=True)
class RetrieverState:
    """Immutable state threaded through a RetrieverPipeline's steps.

    Steps are pure: each step takes a state and returns a NEW state
    (typically via ``dataclasses.replace``), never mutates in place.

    Step input/output contracts:
    - Fetcher steps (``ChunkFetcherStep``, ``MemberFetcherStep``):
      read ``query``, write ``candidates``.
    - Scorer steps (``BM25ScorerStep``, future ``DenseScorerStep``):
      read+write ``candidates`` (assign / update ``relevance`` per item).
    - Filter steps (``TopKFilterStep``, ``MetadataPostFilterStep``):
      read+write ``candidates`` (trim / reorder).
    - Renderer steps (``TokenBudgetStep``):
      read ``candidates``, write ``result``.
    """
    query: "SearchQuery"
    candidates: "ChunkList | ModuleMemberList | None" = None
    result: "PipelineResultItem | None" = None
    duration_ms: float = 0.0
    # WHY: free-form per-step scratch. The dict is mutable even inside a
    # frozen dataclass (frozen=True forbids field reassignment, not deep
    # mutation). Convention: keys are ``<step_name>.<field>`` so collisions
    # are detectable. Intentional escape hatch for cross-step coordination
    # that doesn't merit a typed field (RRF intermediate scores, debug
    # breadcrumbs).
    #
    # Mutation contract (narrowed):
    #
    # - **Sequential steps MAY** mutate ``state.scratch`` in place — they
    #   run one at a time on a single state instance and don't race.
    # - **Steps that may run inside a :class:`ParallelStep` branch MUST**
    #   produce a NEW scratch dict via ``dataclasses.replace(state,
    #   scratch={**state.scratch, key: value})`` instead of writing to
    #   ``state.scratch[key]`` directly. ``ParallelStep`` gives each
    #   branch a copy of the input scratch, but ``dataclasses.replace``
    #   on its own does NOT copy the scratch dict — an in-place write on
    #   the returned state still aliases the branch's input dict (and a
    #   sibling step in the same branch could observe that intermediate
    #   write through the alias). Using ``replace(scratch=...)``
    #   eliminates the aliasing entirely. The :class:`ParallelStep`
    #   merge helper (``_merge_branch_results``) also always returns a
    #   fresh dict, so the caller's input ``state.scratch`` is never
    #   mutated by a parallel fan-out.
    scratch: dict[str, object] = field(default_factory=dict)
