"""Value objects for the optimize layer (spec §D3, §D4).

Frozen, slotted dataclasses — every optimize step passes these around as
immutable records. Budget defaults live in module-level ``_DEFAULT_*``
constants so a single edit re-tunes every construction site (no literal
repeated across field defaults, YAML, and tests).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

# WHY: single source of truth for the conservative budget ceiling — the run
# config YAML and tests reference these values; the field defaults below are
# the canonical Python encoding.
_DEFAULT_MAX_TRIALS = 20
_DEFAULT_MAX_USD = 40.0
_DEFAULT_WALL_TIMEOUT = 14400.0  # 4 hours — a paid run is manual and bounded.
# WHY 200: judge calls are the dominant per-sample cost of the ask_rubric
# fitness; the ceiling is enforced predictively inside the fitness (spec
# §3.4.4) as a count-based sibling of max_usd.
_DEFAULT_MAX_JUDGE_CALLS = 200


@dataclass(frozen=True, slots=True)
class FitnessReport:
    """One fitness evaluation's outcome (spec §D3).

    ``score`` is the weighted fractional-reduction sum (higher = better);
    ``components`` records each raw mean + fraction so reports stay
    interpretable; ``cost_usd`` / ``n_samples`` pin what the score cost.
    """

    score: float
    components: Mapping[str, float]
    cost_usd: float
    n_samples: int


@dataclass(frozen=True, slots=True)
class OptimizationBudget:
    """Hard ceilings on a single optimize run (spec §D4)."""

    max_trials: int = _DEFAULT_MAX_TRIALS
    max_usd: float = _DEFAULT_MAX_USD
    wall_timeout_seconds: float = _DEFAULT_WALL_TIMEOUT
    max_judge_calls: int = _DEFAULT_MAX_JUDGE_CALLS


@dataclass(frozen=True, slots=True)
class Trial:
    """One candidate's journey through the ladder (spec §D4).

    ``rung_scores`` is the score at each rung reached; ``violations`` is the
    ``validate()`` output (empty tuple == the candidate passed the firewall).
    """

    fingerprint: str
    rung_scores: tuple[float, ...]
    cost_usd: float
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Provenance:
    """Audit trail pinning what produced a result (spec §D4).

    Recorded so a landed proposal is reproducible months later: which seed,
    which dataset revision, which models, which optimizer. ``rubric_hash``
    pins the exact objective an ask_rubric run scored against (spec §3.6);
    ``None`` for fitnesses with a fixed in-code objective.
    """

    seed_fingerprint: str
    dataset_revision: str
    model_ids: tuple[str, ...]
    optimizer: str
    rubric_hash: str | None = None


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """The optimizer's return value (spec §D4).

    A rejected search is still information — ``accepted=False`` with both
    holdout scores is a first-class outcome, not an error. ``proposal_diff``
    is the human-landable diff (empty when nothing beat the seed).
    """

    best: object | None
    accepted: bool
    trials: tuple[Trial, ...]
    total_usd: float
    provenance: Provenance
    seed_holdout: float | None = None
    candidate_holdout: float | None = None
    proposal_diff: str = ""
