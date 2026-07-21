"""Attribution ↔ hand-label agreement measurement (ADR 0011 validation gate,
action item 7).

The gate: before the attribution metrics ship, 10–20 trajectories are hand-
labeled from the model-visible (blob-dereferenced) transcript, and the algorithm
is compared against them on (i) the used-file set and (ii) first-surface credit,
per-trajectory macro-averaged, at a committed **≥ 0.90 exact-agreement** bar.

This module implements the measurement (see the ``validate_trajectory_dir`` /
``validate_directory`` orchestrators). The real validation ran 2026-07-21 over
the 12 captured rollouts under
``benchmarks/tests/trajectory/fixtures/trajectories/real/`` and passed at
1.000/1.000 (used-file and first-surface macro agreement) — the numbers that
filled ADR 0011's Validation results and dropped its status qualifier.

Two agreement scores plus one directional tally:

- **used-file agreement** — Jaccard of the algorithm's ``used_files`` vs the
  label's; both-empty is perfect agreement.
- **first-surface agreement** — fraction of the label's tracked gold files where
  the algorithm's first-touch credit names the same tool.
- **budget-elided surfaced credit** — the ADR's second directional bias made
  measurable: how often first-touch credit went to a ``search_codebase`` row a
  labeler (reading the text body) did NOT credit to search — the proxy for a
  budget-elided items-beyond-text over-count.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydocs_eval.trajectory.attribution import Attribution, attribute_trajectory, load_events
from pydocs_eval.trajectory.schema import TrajectorySchemaError

# ADR 0011's committed small-sample bar. Single source; the gate reads it here.
AGREEMENT_THRESHOLD = 0.90

# The tool whose items[] can enumerate rows the token-budgeted text elided
# (ADR 0010/0011 search items-beyond-text) — the budget-elided credit proxy.
_SEARCH_TOOL = "search_codebase"


@dataclass(frozen=True, slots=True)
class TrajectoryLabel:
    """One trajectory's hand labels (the model-visible ground truth).

    ``used_files`` are the files the labeler judged genuinely edited;
    ``first_surface`` maps each tracked gold file to the tool the labeler saw
    surface it first (in the model-visible text). Paths are workspace-relative
    POSIX, matching :mod:`path_normalizer`'s normal form.
    """

    trajectory_id: str
    used_files: frozenset[str]
    first_surface: dict[str, str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrajectoryLabel:
        tid = data.get("trajectory_id")
        if not isinstance(tid, str):
            raise TrajectorySchemaError(
                f"label missing str 'trajectory_id': got {tid!r} in {data!r}"
            )
        return cls(
            trajectory_id=tid,
            used_files=frozenset(_str_list(data.get("used_files", ()), "used_files")),
            first_surface=_str_map(data.get("first_surface", {})),
        )


def _str_list(value: object, field_name: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise TrajectorySchemaError(f"label {field_name!r} must be a list: got {value!r}")
    return [str(item) for item in value]


def _str_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise TrajectorySchemaError(f"label 'first_surface' must be an object: got {value!r}")
    return {str(k): str(v) for k, v in value.items()}


@dataclass(frozen=True, slots=True)
class LabelAgreement:
    """Per-trajectory agreement of the algorithm against one hand label."""

    trajectory_id: str
    used_file_agreement: float
    first_surface_agreement: float
    budget_elided_credit: int


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """|a ∩ b| / |a ∪ b|; two empty sets agree perfectly (``1.0``)."""
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def _first_surface_agreement(first_touch: dict[str, str], label: dict[str, str]) -> float:
    """Fraction of the label's tracked files whose first-touch tool matches."""
    if not label:
        return 1.0
    matches = sum(1 for path, tool in label.items() if first_touch.get(path) == tool)
    return matches / len(label)


def _budget_elided_credit(first_touch: dict[str, str], label: dict[str, str]) -> int:
    """Gold files the algorithm credits to search but the labeler does not.

    Proxy for the search items-beyond-text over-count: first-touch credited a
    ``search_codebase`` row for a file the model-visible text (the labeler's
    source) did not attribute to search — so the algorithm surfaced it via items
    the token budget elided from the body.
    """
    return sum(
        1
        for path, tool in first_touch.items()
        if tool == _SEARCH_TOOL and path in label and label[path] != _SEARCH_TOOL
    )


def compare_one(attribution: Attribution, label: TrajectoryLabel) -> LabelAgreement:
    """Score one algorithm attribution against one hand label."""
    return LabelAgreement(
        trajectory_id=label.trajectory_id,
        used_file_agreement=_jaccard(attribution.used_files, label.used_files),
        first_surface_agreement=_first_surface_agreement(
            attribution.first_touch, label.first_surface
        ),
        budget_elided_credit=_budget_elided_credit(attribution.first_touch, label.first_surface),
    )


@dataclass(frozen=True, slots=True)
class AggregateAgreement:
    """Macro-averaged agreement across the labeled trajectory set (the gate)."""

    trajectories: int
    used_file_agreement: float
    first_surface_agreement: float
    budget_elided_credit: int
    per_trajectory: tuple[LabelAgreement, ...] = field(default=())

    @property
    def meets_threshold(self) -> bool:
        """Both macro averages ≥ :data:`AGREEMENT_THRESHOLD` (ADR 0011)."""
        return (
            self.used_file_agreement >= AGREEMENT_THRESHOLD
            and self.first_surface_agreement >= AGREEMENT_THRESHOLD
        )


def macro_average(agreements: Sequence[LabelAgreement]) -> AggregateAgreement:
    """Per-trajectory macro-average of both agreement scores + the elided tally.

    An empty set yields perfect agreement over zero trajectories (the gate is
    vacuously met — there is nothing to disagree with yet).
    """
    n = len(agreements)
    if n == 0:
        return AggregateAgreement(0, 1.0, 1.0, 0, ())
    used = sum(a.used_file_agreement for a in agreements) / n
    first = sum(a.first_surface_agreement for a in agreements) / n
    elided = sum(a.budget_elided_credit for a in agreements)
    return AggregateAgreement(n, used, first, elided, tuple(agreements))


def validate_trajectory_dir(traj_dir: Path) -> LabelAgreement:
    """Attribute one trajectory folder and compare it to its ``labels.json``.

    The folder must hold ``events.jsonl``, ``labels.json``, and a ``meta.json``
    carrying ``final_patch_files`` (list) and ``workspace_root`` (str). This is
    the per-trajectory unit the ``validate_directory`` orchestrator maps over.
    """
    meta = json.loads((traj_dir / "meta.json").read_text(encoding="utf-8"))
    label = TrajectoryLabel.from_dict(
        json.loads((traj_dir / "labels.json").read_text(encoding="utf-8"))
    )
    events = load_events(traj_dir / "events.jsonl")
    attribution = attribute_trajectory(
        events,
        final_patch_files=frozenset(
            _str_list(meta.get("final_patch_files", ()), "final_patch_files")
        ),
        workspace_root=_require_str(meta, "workspace_root"),
    )
    return compare_one(attribution, label)


def _require_str(meta: dict[str, Any], key: str) -> str:
    value = meta.get(key)
    if not isinstance(value, str):
        raise TrajectorySchemaError(f"meta {key!r} must be a str: got {value!r} in {meta!r}")
    return value


def validate_directory(root: Path) -> AggregateAgreement:
    """Run the whole validation gate over every trajectory folder under ``root``.

    The ONE documented command (once ``fixtures/real/labels`` exists):

        PYTHONPATH=benchmarks/src python -c \\
          "from pathlib import Path; from pydocs_eval.trajectory.compare_labels \\
           import validate_directory; print(validate_directory(Path('<real-dir>')))"

    Each immediate subdirectory containing a ``labels.json`` is treated as one
    trajectory; folders without labels are skipped.
    """
    dirs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "labels.json").exists())
    return macro_average([validate_trajectory_dir(d) for d in dirs])


def load_labels(labels_path: Path) -> tuple[TrajectoryLabel, ...]:
    """Parse a JSON array of hand labels into ``TrajectoryLabel`` records."""
    raw = json.loads(labels_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TrajectorySchemaError(f"labels file must be a JSON array: got {type(raw).__name__}")
    return tuple(TrajectoryLabel.from_dict(_require_obj(item)) for item in raw)


def _require_obj(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise TrajectorySchemaError(f"each label must be an object: got {item!r}")
    return item


def format_report(aggregate: AggregateAgreement) -> Iterable[str]:
    """Yield human-readable summary lines for the aggregate (CLI/log helper)."""
    yield f"trajectories: {aggregate.trajectories}"
    yield f"used-file agreement (macro): {aggregate.used_file_agreement:.3f}"
    yield f"first-surface agreement (macro): {aggregate.first_surface_agreement:.3f}"
    yield f"budget-elided surfaced credit: {aggregate.budget_elided_credit}"
    yield f"meets >= {AGREEMENT_THRESHOLD} bar: {aggregate.meets_threshold}"
