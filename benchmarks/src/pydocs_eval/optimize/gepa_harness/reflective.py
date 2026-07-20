"""Facts-only reflective-dataset builder — GEPA's verified record schema (ADR 0019 §1).

gepa 0.1.4's ``make_reflective_dataset`` returns ``component_name → list of
JSON-serializable records`` in the recommended
``{Inputs, Generated Outputs, Feedback}`` schema (``core/adapter.py``). Phase 2
already produces the ``Feedback`` payload — the rule-based, bounded fact string
computed ONCE by ``compute_derived_record`` and projected through
``consumers.gepa_pair`` (ADR 0012's single-source rule). This module maps one
:class:`InstanceTrajectory` (an evaluated exemplar's projected score + feedback)
onto one such record, per component the proposer asked to update.

Facts-only is the DEFAULT (ADR 0019 §Decision 1). The verbatim result-blob
excerpt (option ii) is a flag, default OFF: when enabled, records additionally
carry a ``Result Excerpt`` pulled VERBATIM from the content-addressed blob store
through the injected :data:`ExcerptFn` — a mechanical byte slice, NEVER a
summary (a summary is model interpretation smuggled into the evidence record,
the fabricated-feedback anti-pattern R6 forbids). Raw transcripts are out.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

__all__ = ["ExcerptFn", "InstanceTrajectory", "ReflectiveConfig", "build_reflective_dataset"]

# Maps an ``instance_id`` to a VERBATIM excerpt of that rollout's result blob
# (a mechanical byte slice from ``blobs/<sha256>`` — never a summary, ADR 0019).
ExcerptFn = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class InstanceTrajectory:
    """One evaluated exemplar the reflector will learn from (GEPA ``Trajectory``).

    ``score`` is the Phase 2 shaped soft score and ``feedback`` the bounded
    Phase 2 fact string, both projected by ``consumers.gepa_pair`` — never
    re-derived here. ``valid`` is ``False`` for the trajectory GEPA sees when a
    candidate was firewall-rejected before any rollout, so ``feedback`` then
    carries the validity violation instead of a rollout fact.
    """

    instance_id: str
    score: float
    feedback: str
    valid: bool = True


@dataclass(frozen=True, slots=True)
class ReflectiveConfig:
    """What the reflector is shown (ADR 0019 §Decision 1).

    ``include_result_excerpts`` gates option (ii); when on, ``excerpt_fn`` MUST
    be supplied (the excerpts are its verbatim output). ``max_records`` caps
    exemplars per component and, when it bites, subsamples with a seeded RNG
    (``rng_seed``) per GEPA's determinism contract.

    Raises:
        ValueError: if ``include_result_excerpts`` is set without an
            ``excerpt_fn`` (there is no verbatim source to slice), or if
            ``max_records`` is not positive.
    """

    include_result_excerpts: bool = False
    excerpt_fn: ExcerptFn | None = None
    max_records: int | None = None
    rng_seed: int = 0

    def __post_init__(self) -> None:
        if self.include_result_excerpts and self.excerpt_fn is None:
            raise ValueError(
                "include_result_excerpts=True requires excerpt_fn, got None; "
                "excerpts are verbatim blob-store byte slices, never summaries (ADR 0019 §ii)"
            )
        if self.max_records is not None and self.max_records <= 0:
            raise ValueError(
                f"max_records must be positive or None, got {self.max_records!r}; "
                "it caps exemplars per component before seeded subsampling"
            )


def build_reflective_dataset(
    sections: Mapping[str, str],
    trajectories: Sequence[InstanceTrajectory],
    components: Sequence[str],
    config: ReflectiveConfig,
) -> dict[str, list[dict[str, object]]]:
    """Map each component to its facts-only reflective records (ADR 0019 §1).

    For every component the proposer requested, emit one record per (subsampled)
    trajectory in GEPA's ``{Inputs, Generated Outputs, Feedback}`` schema plus the
    ``score`` / ``trace_id`` extra keys the library permits. ``Generated Outputs``
    is the candidate's CURRENT text for that component — the surface the reflector
    is being asked to rewrite.
    """
    chosen = _subsample(trajectories, config)
    return {name: [_record(name, sections, traj, config) for traj in chosen] for name in components}


def _record(
    component: str,
    sections: Mapping[str, str],
    traj: InstanceTrajectory,
    config: ReflectiveConfig,
) -> dict[str, object]:
    """One exemplar record; ``Result Excerpt`` appended verbatim only when flagged."""
    record: dict[str, object] = {
        "Inputs": {"instance": traj.instance_id},
        "Generated Outputs": sections.get(component, ""),
        "Feedback": traj.feedback,
        "score": traj.score,
        "trace_id": traj.instance_id,
    }
    if config.include_result_excerpts and config.excerpt_fn is not None:
        # WHY verbatim: the excerpt is a mechanical byte slice of the stored
        # result blob (ADR 0019 §ii) — summarizing it would smuggle model
        # interpretation into the evidence record, the R6 anti-pattern.
        record["Result Excerpt"] = config.excerpt_fn(traj.instance_id)
    return record


def _subsample(
    trajectories: Sequence[InstanceTrajectory], config: ReflectiveConfig
) -> list[InstanceTrajectory]:
    """Return all trajectories, or a seeded-RNG sample when ``max_records`` bites."""
    items = list(trajectories)
    if config.max_records is None or len(items) <= config.max_records:
        return items
    # Seeded RNG keeps subsampled runs reproducible (GEPA determinism contract).
    return random.Random(config.rng_seed).sample(items, config.max_records)
