"""Shaped score — a versioned weighted sum over rule-computed components (ADR 0012).

Every component is a "goodness in [0,1]" fact from the trace + parsed eval
outputs; the shaped score is their **weight-normalized average**, so the
per-example ``soft`` is guaranteed to land in [0,1] regardless of the weights.
That fixed range is deliberate: GEPA sums minibatch scores for acceptance but
means valset scores for Pareto selection, so an unbounded scale would weight the
two pressures inconsistently (ADR 0012).

Weights + ``score_version`` live in ``configs/score_weights.yaml``; defaults are
sane-but-uncalibrated (calibration DEFERRED TO PHASE 3). ``score_version`` is
stamped into every emitted record so a score is always traceable to the weights
that produced it. This module is the single source of the score components (R3);
no second implementation may exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any

import yaml

from pydocs_eval.trajectory.eval_report import GroundTruthOutcome
from pydocs_eval.trajectory.metrics import TrajectoryMetrics

_CONFIG_PACKAGE = "pydocs_eval.trajectory.configs"
_WEIGHTS_RESOURCE = "score_weights.yaml"

# The six shaped-score components, in a fixed order (stable component-dict keys).
_COMPONENT_KEYS = (
    "localization_recall",
    "evidence_yield",
    "patch_applies",
    "f2p_fraction",
    "p2p_clean",
    "budget_headroom",
)


@dataclass(frozen=True, slots=True)
class ScoreWeights:
    """The versioned shaped-score weights loaded from ``score_weights.yaml``."""

    version: int
    weights: dict[str, float]


def _validate_weights(raw: Any) -> dict[str, float]:
    """Coerce the YAML weights map, raising with context on a missing/bad key."""
    if not isinstance(raw, dict):
        raise ValueError(f"weights must be a mapping: got {raw!r}, expected {{component: float}}")
    out: dict[str, float] = {}
    for key in _COMPONENT_KEYS:
        value = raw.get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(
                f"weight {key!r} must be a number: got {value!r} in {_WEIGHTS_RESOURCE}"
            )
        out[key] = float(value)
    return out


@lru_cache(maxsize=1)
def load_score_weights() -> ScoreWeights:
    """Load + cache the shipped shaped-score weights (``configs/score_weights.yaml``)."""
    text = files(_CONFIG_PACKAGE).joinpath(_WEIGHTS_RESOURCE).read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    version = data.get("score_version")
    if not isinstance(version, int):
        raise ValueError(f"score_version must be an int: got {version!r} in {_WEIGHTS_RESOURCE}")
    return ScoreWeights(version=version, weights=_validate_weights(data.get("weights")))


def _budget_headroom(num_turns: int, turn_cap: int | None) -> float:
    """Turn-budget headroom in [0,1]: ``1 - min(1, turns / cap)``; ``1.0`` if no cap.

    Higher = more headroom left. A null turn cap (no recorded ceiling) yields full
    headroom so an un-capped run is never penalized on a cap it never had.
    """
    if not turn_cap:
        return 1.0
    return 1.0 - min(1.0, num_turns / turn_cap)


def score_components(
    metrics: TrajectoryMetrics, outcome: GroundTruthOutcome, *, turn_cap: int | None
) -> dict[str, float]:
    """Compute the six goodness-in-[0,1] shaped-score components from parsed facts.

    Each component is a fact, not a judgment: localization recall (gold files
    surfaced), evidence yield (``1 - wasted_read_ratio``), patch-applies (0/1),
    F2P pass fraction, P2P-clean (0/1), and turn-budget headroom.
    """
    return {
        "localization_recall": metrics.gold_file_recall,
        "evidence_yield": 1.0 - metrics.wasted_read_ratio,
        "patch_applies": 1.0 if outcome.patch_applied else 0.0,
        "f2p_fraction": metrics.f2p_fraction,
        "p2p_clean": 1.0 if metrics.p2p_regression_count == 0 else 0.0,
        "budget_headroom": _budget_headroom(metrics.turns, turn_cap),
    }


def shaped_soft_score(components: dict[str, float], weights: ScoreWeights) -> float:
    """Weight-normalized average of the components → the per-example ``soft`` ∈ [0,1].

    ``sum(w_i * c_i) / sum(w_i)``; with every ``c_i`` ∈ [0,1] the result is in
    [0,1] for any non-negative weights. All-zero weights raise (an unusable
    config), carrying the offending version.
    """
    total_weight = sum(weights.weights.values())
    if total_weight <= 0:
        raise ValueError(
            f"shaped-score weights sum to {total_weight!r} (score_version "
            f"{weights.version}); expected a positive total"
        )
    weighted = sum(weights.weights[k] * components[k] for k in _COMPONENT_KEYS)
    return weighted / total_weight


@dataclass(frozen=True, slots=True)
class ShapedScore:
    """One trajectory's shaped score + its components + the ``score_version``."""

    soft: float
    components: dict[str, float]
    score_version: int


def compute_shaped_score(
    metrics: TrajectoryMetrics,
    outcome: GroundTruthOutcome,
    *,
    turn_cap: int | None,
    weights: ScoreWeights | None = None,
) -> ShapedScore:
    """Assemble the :class:`ShapedScore` (components + soft + version) for a trajectory."""
    resolved_weights = weights or load_score_weights()
    components = score_components(metrics, outcome, turn_cap=turn_cap)
    return ShapedScore(
        soft=shaped_soft_score(components, resolved_weights),
        components=components,
        score_version=resolved_weights.version,
    )
