"""WeightedScoreInterpolationStep — alternative fusion to RRFFusionStep.

Min-max normalizes each branch's scores to [0, 1], then blends via
``score_final = sum(weights[i] * norm_score_i)``. Unlike RRF (which
discards score magnitude), this preserves it — useful when one
retriever is dramatically stronger than the other on a given query.

Reads from the same ``state.scratch[<branch>.ranked]`` keys
RRFFusionStep uses, so it drops in as a YAML swap.

**Strict on missing branches:** every key in ``branch_keys`` MUST be
present in ``state.scratch`` at run time, or :class:`KeyError` is
raised with a diagnostic listing the missing key + the available
scratch keys. This catches pipeline-configuration bugs (e.g., the
upstream :class:`TopKFilterStep` forgot to ``publish_to`` the matching
name) at the boundary instead of silently producing degraded rankings.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from pydocs_mcp.models import Chunk, ChunkList
from pydocs_mcp.retrieval.pipeline import RetrieverState, RetrieverStep
from pydocs_mcp.retrieval.serialization import BuildContext, step_registry
from pydocs_mcp.retrieval.steps._constants import DEFAULT_BRANCH_KEYS

# WHY: equal-weight 50/50 between BM25 and dense is the literature-standard
# starting point for two-branch hybrid retrieval. Single source of truth —
# referenced from the field default, to_dict diff, and from_dict fallback.
_DEFAULT_WEIGHTS: tuple[float, ...] = (0.5, 0.5)

# WHY: floating-point tolerance on the weights-sum check. Sums of
# config-supplied floats accumulate rounding error (e.g. 0.1 + 0.2 + 0.7
# != 1.0 exactly), so a strict == comparison would reject legal configs.
_WEIGHT_SUM_TOLERANCE = 1e-6

_DEFAULT_NAME = "weighted_score_interpolation"


@step_registry.register("weighted_score_interpolation")
@dataclass(frozen=True, slots=True)
class WeightedScoreInterpolationStep(RetrieverStep):
    """Linear-blend fusion across N branches with min-max normalization.

    For each branch ``i``, scores are min-max normalized to [0, 1] across
    that branch's candidates; the final score per chunk is
    ``sum(weights[i] * norm_score_i)`` summed over the branches that
    contained the chunk.

    **Branches must be present.** If any key in ``branch_keys`` is
    absent from ``state.scratch``, :class:`KeyError` is raised with a
    diagnostic listing the missing key and the available scratch keys.
    This is louder than :class:`RRFFusionStep`'s graceful skip on
    purpose (spec S31): a missing branch usually means an upstream
    pipeline misconfiguration (e.g., ``TopKFilterStep`` forgot to
    ``publish_to`` the matching name), and silently degrading the
    fusion would hide the bug behind worse retrieval quality. RRF can
    afford to skip because its reciprocal-rank sum composes additively;
    the weighted blend cannot, because dropping a configured branch
    changes the effective weight distribution across the survivors.

    Reads ``state.scratch[<branch>.ranked]`` keys (same convention RRF
    uses) — each branch payload is either a :class:`ChunkList` (has
    ``.items``) or a bare ``tuple[Chunk, ...]``.
    """

    weights: tuple[float, ...] = field(default=_DEFAULT_WEIGHTS, kw_only=True)
    branch_keys: tuple[str, ...] = field(default=DEFAULT_BRANCH_KEYS, kw_only=True)
    publish_to: str | None = field(default=None, kw_only=True)
    name: str = field(default=_DEFAULT_NAME, kw_only=True)

    def __post_init__(self) -> None:
        if len(self.weights) != len(self.branch_keys):
            raise ValueError(
                f"WeightedScoreInterpolationStep: len(weights)="
                f"{len(self.weights)} != len(branch_keys)={len(self.branch_keys)}",
            )

    async def run(self, state: RetrieverState) -> RetrieverState:
        # Accumulate per-chunk-id weighted normalized scores across branches.
        # For each branch: min-max normalize scores in that branch, then
        # weight by self.weights[i].
        #
        # WHY raise on missing keys: a branch_key declared in the YAML
        # but absent from scratch at run time is almost certainly a
        # pipeline-config bug (upstream forgot to publish_to a matching
        # name, or the YAML's branch_keys typoed an existing key).
        # Silent skip would let the bug ship — wrong retrieval, no
        # visible failure. Raising with a diagnostic lists the missing
        # key + the actual scratch keys so the cause is one error line.
        missing = [k for k in self.branch_keys if k not in state.scratch]
        if missing:
            raise KeyError(
                f"WeightedScoreInterpolationStep: branch_keys "
                f"{sorted(missing)!r} not in state.scratch. "
                f"Available scratch keys: {sorted(state.scratch)!r}. "
                f"Check that upstream TopKFilterStep (or equivalent) "
                f"uses publish_to=<branch_key> to expose its ranking.",
            )
        accumulated: dict[int, float] = {}
        first_seen: dict[int, Chunk] = {}
        for weight, key in zip(self.weights, self.branch_keys, strict=True):
            branch = state.scratch[key]
            items = tuple(branch.items) if hasattr(branch, "items") else tuple(branch)
            if not items:
                continue
            scores = [float(c.relevance) if c.relevance is not None else 0.0 for c in items]
            lo = min(scores)
            hi = max(scores)
            # WHY: when all scores in a branch are equal (single-item
            # branch, or genuine tie), the natural min-max interpretation
            # is degenerate. We treat every item as "top of branch" → 1.0
            # so that a single-result branch still contributes its full
            # weight to its one chunk. The alternative (normalize to 0)
            # would silently zero out useful signal whenever a branch
            # returned exactly one hit or all hits tied.
            span = hi - lo
            for chunk, raw in zip(items, scores, strict=True):
                if chunk.id is None:
                    continue
                normed = (raw - lo) / span if span > 0.0 else 1.0
                accumulated[chunk.id] = accumulated.get(chunk.id, 0.0) + weight * normed
                first_seen.setdefault(chunk.id, chunk)

        if not accumulated:
            return state

        fused = sorted(
            (replace(first_seen[cid], relevance=score) for cid, score in accumulated.items()),
            key=lambda c: c.relevance or 0.0,
            reverse=True,
        )
        ranked = ChunkList(items=tuple(fused))

        new_scratch = dict(state.scratch)
        if self.publish_to is not None:
            new_scratch[self.publish_to] = ranked
        return replace(state, candidates=ranked, scratch=new_scratch)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": "weighted_score_interpolation"}
        if self.weights != _DEFAULT_WEIGHTS:
            out["weights"] = list(self.weights)
        if self.branch_keys != DEFAULT_BRANCH_KEYS:
            out["branch_keys"] = list(self.branch_keys)
        if self.publish_to is not None:
            out["publish_to"] = self.publish_to
        if self.name != _DEFAULT_NAME:
            out["name"] = self.name
        return out

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        context: BuildContext,
    ) -> WeightedScoreInterpolationStep:
        weights = tuple(data.get("weights", _DEFAULT_WEIGHTS))
        if abs(sum(weights) - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                f"WeightedScoreInterpolationStep weights must sum to ~1.0 "
                f"(tol {_WEIGHT_SUM_TOLERANCE}); got {weights} -> {sum(weights)}",
            )
        return cls(
            weights=weights,
            branch_keys=tuple(data.get("branch_keys", DEFAULT_BRANCH_KEYS)),
            publish_to=data.get("publish_to"),
            name=data.get("name", _DEFAULT_NAME),
        )


__all__ = ("WeightedScoreInterpolationStep",)
