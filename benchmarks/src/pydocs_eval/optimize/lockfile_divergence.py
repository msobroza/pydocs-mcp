"""Pre-freeze lockfile-divergence comparator (ADR 0020 §Pre-test re-validation trigger).

Every candidate evaluation is its own campaign with its own lockfile (ADR 0017), so
serving-side drift during the campaign is detectable by construction: before the
freeze, compare the **serving-relevant** R5 fields (ADR 0016 §Campaign mechanics)
of the final candidate's evaluation lockfile against the current environment's
lockfile. **Any divergence → re-validate the final candidate before freezing** —
numbers from a serving stack that no longer exists cannot back a frozen config, and
a model/provider change makes the val gate stale.

The serving-relevant fields (a strict subset of the full campaign identity — a
dataset-pin or cell-name change is not a *serving* divergence): host fingerprint,
provider, billing mode, the provider pin (auth / base_url / router / fallbacks /
quantization / anthropic-version / pricing snapshot), the per-cell model ids,
renderer/artifact hash, and the metric/score/taxonomy versions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "FieldDivergence",
    "LockfileDivergence",
    "compare_serving_fields",
    "serving_fields",
]


@dataclass(frozen=True, slots=True)
class FieldDivergence:
    """One serving field that changed between two lockfiles."""

    field: str
    before: object
    after: object


@dataclass(frozen=True, slots=True)
class LockfileDivergence:
    """The serving-field divergences between two lockfiles (empty ⇒ no re-validation)."""

    divergences: tuple[FieldDivergence, ...]

    @property
    def diverged(self) -> bool:
        """True iff ≥1 serving field changed — the re-validation trigger fires."""
        return bool(self.divergences)

    @property
    def fields(self) -> tuple[str, ...]:
        """The names of the diverging serving fields, in sorted order."""
        return tuple(d.field for d in self.divergences)


def serving_fields(lockfile: Mapping[str, object]) -> dict[str, object]:
    """Project a campaign lockfile dict onto its serving-relevant R5 fields.

    ``lockfile`` is a :meth:`CampaignLockfile.to_dict` payload. Model ids are the
    sorted distinct per-cell ``arm.model`` values (a model swap on any cell is a
    serving divergence).
    """
    return {
        "host": lockfile["host"],
        "provider": lockfile["provider"],
        "billing_mode": lockfile["billing_mode"],
        "provider_pin": lockfile["provider_pin"],
        "model_ids": _model_ids(lockfile["cells"]),
        "artifact_hash": lockfile["artifact_hash"],
        "versions": lockfile["versions"],
    }


def compare_serving_fields(
    before: Mapping[str, object], after: Mapping[str, object]
) -> LockfileDivergence:
    """Report every serving field that differs between two lockfile dicts.

    Deterministic (fields compared in sorted order); an empty result means the
    serving stack is unchanged and no re-validation is triggered.
    """
    a, b = serving_fields(before), serving_fields(after)
    divergences = tuple(
        FieldDivergence(field=key, before=a[key], after=b[key])
        for key in sorted(a)
        if a[key] != b[key]
    )
    return LockfileDivergence(divergences=divergences)


def _model_ids(cells: object) -> list[str]:
    """Sorted distinct ``arm.model`` ids across the lockfile's cells."""
    if not isinstance(cells, Sequence):
        raise ValueError(f"lockfile 'cells' must be a sequence, got {type(cells).__name__}")
    return sorted({str(cell["arm"]["model"]) for cell in cells})  # type: ignore[index]
