"""RRFFusionStep + RRFResultFuser — multi-list reciprocal-rank fusion (spec §5.6).

Replaces the previous single-list re-scorer (formerly ``RRFStep``, registry
key ``"reciprocal_rank_fusion"``). Reads N ranked Chunk lists from
``state.scratch[<branch_name>.ranked]`` (each parallel branch publishes its
ranking via ``TopKFilterStep.publish_to``), computes RRF score per item as
``sum(1 / (k + rank_in_list_i))`` across lists, sorts descending, emits the
fused ranking via ``state.candidates``.

``RRFResultFuser`` is the standalone fuser the hybrid retriever composes
with — separating the math from the pipeline plumbing so the same logic
can be reused by code paths that aren't structured as RetrieverSteps.

Reference: Cormack, Clarke, Buettcher 2009 — *Reciprocal Rank Fusion
outperforms Condorcet and individual Rank Learning Methods*.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import ClassVar

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import (
    BuildContext,
    step_registry,
    step_to_yaml_dict,
    yaml_kwargs,
)
from pydocs_mcp.retrieval.steps._constants import DEFAULT_BRANCH_KEYS

# WHY: literature default for RRF (Cormack et al. 2009). Single source of
# truth — referenced from RRFResultFuser default, RRFFusionStep field
# default, and from_dict fallback. Bumping touches one line, not three.
_DEFAULT_K = 60


def _validate_k(k: int, *, cls_name: str) -> None:
    """Reject k <= 0 — ``_rrf_fuse`` divides by ``k + rank`` with rank

    starting at 0 (``enumerate``), so k=0 raises ``ZeroDivisionError`` on
    the very first item and negative k crashes once rank reaches ``-k``.
    ``k: 0`` is a plausible YAML experiment (the classic RRF formula
    assumes rank starts at 1, so "pure reciprocal rank" tuning sets k=0)
    — fail loudly at construction time instead of query time, matching
    every other step's from_dict-validation philosophy.
    """
    if k < 1:
        raise ValueError(
            f"{cls_name}: k must be >= 1 (got {k!r}) — k=0 or negative divides by zero in RRF scoring"
        )


def _rrf_fuse(
    ranked_lists: Sequence[Sequence[Chunk]],
    *,
    k: int,
    limit: int | None = None,
) -> tuple[Chunk, ...]:
    """Reciprocal-rank fusion.

    Returns ranked Chunks with ``relevance`` overwritten by the RRF score.
    Items are de-duped by ``id``; chunks with ``id is None`` are dropped
    (no stable dedupe key — silently skipping is safer than letting them
    inflate scores under ``id()`` collisions across lists). Callers MUST
    NOT rely on the relative ordering of dropped sentinel chunks across
    multiple invocations: only the surviving ranked chunks have stable
    ordering.

    Asymmetry with :class:`WeightedScoreInterpolationStep` is intentional
    (spec S31): RRF silently skips both ``id is None`` chunks AND missing
    branches (graceful degradation under upstream filtering noise), while
    the weighted step raises :class:`KeyError` for a missing branch. RRF
    composes additively, so a missing branch just lowers a chunk's total
    without corrupting the ranking; the weighted step needs every
    configured branch present to form a well-defined linear combination.

    First-seen wins on the stored representative so retriever_name /
    relevance from the earliest branch survives the merge — matches the
    convention ParallelStep enforces.
    """
    scores: dict[int, float] = {}
    representatives: dict[int, Chunk] = {}
    for ranked in ranked_lists:
        for rank, chunk in enumerate(ranked):
            if chunk.id is None:
                continue
            scores[chunk.id] = scores.get(chunk.id, 0.0) + 1.0 / (k + rank)
            representatives.setdefault(chunk.id, chunk)
    fused = [replace(representatives[chunk_id], relevance=scores[chunk_id]) for chunk_id in scores]
    fused.sort(key=lambda c: c.relevance or 0.0, reverse=True)
    if limit is not None:
        fused = fused[:limit]
    return tuple(fused)


@dataclass(frozen=True, slots=True)
class RRFResultFuser:
    """Standalone reciprocal-rank fusion fuser.

    The math-only counterpart to RRFFusionStep, for code paths that
    aren't structured as pipeline steps. The shipped hybrid path fuses
    at the pipeline level (parallel retrieval + RRF); a native
    ``hybrid_search`` exists only when a single backend fuses
    server-side.
    """

    k: int = _DEFAULT_K

    def __post_init__(self) -> None:
        _validate_k(self.k, cls_name=type(self).__name__)

    async def fuse(
        self,
        ranked_lists: Sequence[Sequence[Chunk]],
        *,
        limit: int,
    ) -> tuple[Chunk, ...]:
        return _rrf_fuse(ranked_lists, k=self.k, limit=limit)


@step_registry.register("rrf_fusion")
@dataclass(frozen=True, slots=True)
class RRFFusionStep(RetrieverStep):
    """Multi-list RRF fusion step.

    Reads named scratch keys (``branch_keys``), each of which holds either
    a bare ``tuple[Chunk, ...]`` or any object with an ``.items``
    attribute (e.g., ChunkList). Writes the fused ranking to
    ``state.candidates`` as a ChunkList. Returns the input state unchanged
    when no branch produced output — keeps the pipeline robust to early
    short-circuits in parallel branches.

    **Lenient on missing branches** — quietly the inverse of
    :class:`~pydocs_mcp.retrieval.steps.weighted_score_interpolation.WeightedScoreInterpolationStep`,
    which raises :class:`KeyError` instead. The asymmetry is intentional
    (spec S31): RRF wants resilience to upstream filtering noise; the
    weighted step needs every branch present to form the linear blend.
    """

    k: int = field(default=_DEFAULT_K, kw_only=True)
    branch_keys: tuple[str, ...] = field(
        default=DEFAULT_BRANCH_KEYS,
        kw_only=True,
    )
    name: str = field(default="rrf_fusion", kw_only=True)
    # WHY name is excluded: this step has never serialized ``name`` — the
    # parity tests pin that drift; unifying it across steps is a follow-up.
    _YAML_KEYS: ClassVar[tuple[str, ...]] = ("k", "branch_keys")

    def __post_init__(self) -> None:
        _validate_k(self.k, cls_name=type(self).__name__)

    async def run(self, state: RetrieverState) -> RetrieverState:
        ranked_lists: list[tuple[Chunk, ...]] = []
        for key in self.branch_keys:
            payload = state.scratch.get(key)
            if payload is None:
                continue
            items = tuple(payload.items) if hasattr(payload, "items") else tuple(payload)
            if items:
                ranked_lists.append(items)
        if not ranked_lists:
            return state
        fused = _rrf_fuse(ranked_lists, k=self.k)
        return replace(state, candidates=ChunkList(items=fused))

    def to_dict(self) -> dict:
        return step_to_yaml_dict(self, type_name="rrf_fusion", keys=self._YAML_KEYS)

    @classmethod
    def from_dict(cls, data: dict, context: BuildContext) -> RRFFusionStep:
        return cls(**yaml_kwargs(data, cls, cls._YAML_KEYS))


__all__ = ("RRFFusionStep", "RRFResultFuser")
