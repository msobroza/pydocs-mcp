"""Discriminative-subset builder — rule (i): target-fails ∧ reference-solves (ADR 0013).

The discriminative subset is the dev-side instance list Phase 4's optimizer signal is
measured on: ``{ i ∈ dev : target fails i ∧ reference model solves i }``. It is a PURE
function of (baseline-results dir, dev split, rule config); its output is tagged with the
target model ID + ``subset_version`` and REBUILT whenever the target changes (a subset
built for one target is never silently reused for another). Sizes round DOWN to a multiple
of 12 so GEPA (minibatch 3) and skillopt (minibatch 4) both tile without a ragged tail.

Baseline-results schema (forward-declared — D4's campaign runner is the producer; ADR
0014/0016): one JSONL row per ``(model, instance_id)`` resolve verdict::

    {"model": "claude-haiku-4-5", "instance_id": "conan-io__conan-1", "resolved": false}

This module reads that schema; it is NOT populated in Phase 3 (real baselines are D4's
output), only unit-tested against a synthetic fixture.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Both Phase 4 optimizer minibatch defaults (GEPA 3, skillopt 4) tile a multiple of 12.
_TILE_MULTIPLE = 12


@dataclass(frozen=True, slots=True)
class SubsetRuleConfig:
    """Target/reference models, subset version, and the multiple-of-12 size band."""

    target_model: str
    reference_model: str
    subset_version: str = "v1"
    # ADR 0013: the discriminative subset targets the 40–80 band for minibatch iteration.
    max_size: int = 72  # largest multiple of 12 within the 40–80 band
    min_size: int = _TILE_MULTIPLE
    tile_multiple: int = _TILE_MULTIPLE

    @property
    def tag(self) -> str:
        """``<target_model>:<subset_version>`` — the rebuild-on-target-change identity."""
        return f"{self.target_model}:{self.subset_version}"


@dataclass(frozen=True, slots=True)
class DiscriminativeSubset:
    """The sized, tagged discriminative instance list + its provenance."""

    instance_ids: tuple[str, ...]
    target_model: str
    reference_model: str
    subset_version: str
    candidate_count: int  # qualifying instances before multiple-of-12 sizing

    @property
    def tag(self) -> str:
        return f"{self.target_model}:{self.subset_version}"

    def to_dict(self) -> dict[str, object]:
        return {
            "tag": self.tag,
            "target_model": self.target_model,
            "reference_model": self.reference_model,
            "subset_version": self.subset_version,
            "candidate_count": self.candidate_count,
            "size": len(self.instance_ids),
            "instance_ids": list(self.instance_ids),
        }


def load_verdicts(baseline_dir: Path) -> dict[str, dict[str, bool]]:
    """Read all ``*.jsonl`` baseline results under ``baseline_dir`` → model → id → resolved.

    Raises on an empty directory — building a subset from no baselines is a caller error
    (the discriminative rule is undefined without target AND reference verdicts).
    """
    files = sorted(baseline_dir.glob("*.jsonl"))
    if not files:
        raise ValueError(f"no baseline result files: {baseline_dir} has no *.jsonl")
    verdicts: dict[str, dict[str, bool]] = {}
    for path in files:
        for line in path.read_text().splitlines():
            if line.strip():
                _record_verdict(verdicts, json.loads(line))
    return verdicts


def _record_verdict(verdicts: dict[str, dict[str, bool]], row: dict[str, object]) -> None:
    model = str(row["model"])
    verdicts.setdefault(model, {})[str(row["instance_id"])] = bool(row["resolved"])


def build_discriminative_subset(
    baseline_dir: Path,
    dev_instance_ids: list[str],
    config: SubsetRuleConfig,
) -> DiscriminativeSubset:
    """Build the discriminative subset from baseline verdicts over the dev split."""
    verdicts = load_verdicts(baseline_dir)
    target = _model_verdicts(verdicts, config.target_model)
    reference = _model_verdicts(verdicts, config.reference_model)
    candidates = sorted(
        iid for iid in dev_instance_ids if target.get(iid) is False and reference.get(iid) is True
    )
    sized = _size_to_tile(candidates, config)
    return DiscriminativeSubset(
        instance_ids=tuple(sized),
        target_model=config.target_model,
        reference_model=config.reference_model,
        subset_version=config.subset_version,
        candidate_count=len(candidates),
    )


def _model_verdicts(verdicts: dict[str, dict[str, bool]], model: str) -> dict[str, bool]:
    if model not in verdicts:
        raise ValueError(f"no baseline verdicts for model {model!r}; have {sorted(verdicts)}")
    return verdicts[model]


def _size_to_tile(candidates: list[str], config: SubsetRuleConfig) -> list[str]:
    """Truncate deterministically to the largest multiple of ``tile_multiple`` in-band.

    Returns empty when fewer than ``min_size`` candidates qualify — a partial tile would
    leave GEPA/skillopt with a ragged final minibatch, so it is better to surface "not
    enough signal yet" than to emit an untileable list.
    """
    usable = min(len(candidates), config.max_size)
    sized = (usable // config.tile_multiple) * config.tile_multiple
    if sized < config.min_size:
        return []
    return candidates[:sized]
