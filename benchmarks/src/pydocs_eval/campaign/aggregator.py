"""Cross-cell campaign aggregator — a consumer, not a producer (ADR 0016 item 4).

Each cell is one ``compute-metrics`` run → one ``aggregate.json`` carrying a
per-trajectory ``{trajectory_id, instance_id, hard, soft, label, cost_usd}``
index plus the distinct ``artifact_hashes`` and ``infra_excluded`` count. This
layer READS those, pairs cells by ``instance_id``, and computes cross-cell
paired deltas via the shipped I2 helpers (``mcnemar_from_pairs`` =
``paired_bootstrap_ci`` CI + exact McNemar p). It re-derives NOTHING — no
scores, no taxonomy, no infra carve-out — the single-source metric rule stands
(``consumers.py``).

Two hard errors, both R4/R5 correctness gates: a cell whose ``aggregate.json``
lists more than one ``artifact_hash`` (heterogeneous corpus/config within a
cell) is rejected at load; an instance-list mismatch between two paired cells is
raised by ``mcnemar_from_pairs`` (identical instance lists are the paired
design's premise). The output is the campaign report skeleton: paired deltas +
CIs + McNemar p per contrast, strata breakdowns, the cost layer, and the
separately-counted infra totals (R8).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.metrics.aggregate import mcnemar_from_pairs


@dataclass(frozen=True, slots=True)
class CellAggregate:
    """One cell's per-instance view, loaded from its ``aggregate.json`` index."""

    name: str
    hard: Mapping[str, int]
    soft: Mapping[str, float]
    cost: Mapping[str, float]
    label: Mapping[str, str]
    infra_excluded: int
    artifact_hashes: tuple[str, ...]

    @property
    def total_cost(self) -> float:
        return sum(self.cost.values())


def load_cell_aggregate(name: str, aggregate_path: Path) -> CellAggregate:
    """Parse a cell's ``aggregate.json`` into a :class:`CellAggregate`.

    Raises:
        ValueError: if the cell lists >1 ``artifact_hash`` (heterogeneous corpus
            within a cell — R5 forbids mixing) or a duplicate ``instance_id``
            (a cell has exactly one rollout per instance).
        FileNotFoundError: if ``aggregate_path`` is absent.
    """
    if not aggregate_path.is_file():
        raise FileNotFoundError(f"cell {name!r} aggregate.json missing: {aggregate_path}")
    doc = json.loads(aggregate_path.read_text(encoding="utf-8"))
    hashes = tuple(doc.get("artifact_hashes", ()))
    if len(hashes) > 1:
        raise ValueError(
            f"cell {name!r} has heterogeneous artifact_hashes {list(hashes)!r}; a cell "
            "must run one corpus/config (R5) — split it into separate cells"
        )
    return _build_cell(name, doc, hashes)


# The Phase 2 taxonomy label excluded from resolve aggregates (ADR 0012/0016 R8);
# such rows are counted separately (``infra_excluded``), never paired.
_INFRA_LABEL = "infra_error"


def _build_cell(name: str, doc: dict, hashes: tuple[str, ...]) -> CellAggregate:
    hard: dict[str, int] = {}
    soft: dict[str, float] = {}
    cost: dict[str, float] = {}
    label: dict[str, str] = {}
    for row in doc.get("trajectories", ()):
        iid = str(row["instance_id"])
        if iid in hard:
            raise ValueError(
                f"cell {name!r} has duplicate instance_id {iid!r} — one rollout/instance"
            )
        # Infra-labeled rows are excluded from the paired resolve arrays (R8) —
        # they are the separately-counted ``infra_excluded``, not a comparison
        # unit; keeping them would corrupt the paired 2×2 and the instance-list
        # identity the McNemar test presupposes.
        if str(row["label"]) == _INFRA_LABEL:
            continue
        hard[iid] = int(row["hard"])
        soft[iid] = float(row["soft"])
        cost[iid] = float(row["cost_usd"])
        label[iid] = str(row["label"])
    return CellAggregate(
        name=name,
        hard=hard,
        soft=soft,
        cost=cost,
        label=label,
        infra_excluded=int(doc.get("infra_excluded", 0)),
        artifact_hashes=hashes,
    )


@dataclass(frozen=True, slots=True)
class ContrastResult:
    """One paired cell contrast: the 2×2 counts, resolve delta, CI, and p (ADR 0016)."""

    name: str
    cell_a: str
    cell_b: str
    b: int
    c: int
    n: int
    delta: float
    mcnemar_p: float
    ci_low: float
    ci_high: float
    cost_a: float
    cost_b: float
    infra_a: int
    infra_b: int

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "cell_a": self.cell_a,
            "cell_b": self.cell_b,
            "discordant": {"b": self.b, "c": self.c, "n": self.n},
            "resolve_delta": self.delta,
            "mcnemar_p": self.mcnemar_p,
            "paired_ci_95": [self.ci_low, self.ci_high],
            "cost": {"cell_a_usd": self.cost_a, "cell_b_usd": self.cost_b},
            "infra_excluded": {"cell_a": self.infra_a, "cell_b": self.infra_b},
        }


def paired_contrast(
    name: str, a: CellAggregate, b: CellAggregate, *, seed: int = 0
) -> ContrastResult:
    """Pair cells ``a`` and ``b`` by ``instance_id`` → a :class:`ContrastResult`.

    Delegates the statistics to ``mcnemar_from_pairs`` (which raises on any
    instance-list mismatch — the paired design's premise, R4). ``a`` is the
    "treatment" arm: ``b`` counts A-only resolves, ``c`` B-only, ``delta`` the
    A−B resolve delta.
    """
    b_ct, c_ct, n, delta, p, (_, ci_low, ci_high) = mcnemar_from_pairs(a.hard, b.hard, seed=seed)
    return ContrastResult(
        name=name,
        cell_a=a.name,
        cell_b=b.name,
        b=b_ct,
        c=c_ct,
        n=n,
        delta=delta,
        mcnemar_p=p,
        ci_low=ci_low,
        ci_high=ci_high,
        cost_a=a.total_cost,
        cost_b=b.total_cost,
        infra_a=a.infra_excluded,
        infra_b=b.infra_excluded,
    )


def _restrict(cell: CellAggregate, ids: Sequence[str]) -> CellAggregate:
    """A view of ``cell`` limited to ``ids`` (for a per-stratum sub-contrast)."""
    keep = set(ids)
    return CellAggregate(
        name=cell.name,
        hard={i: cell.hard[i] for i in ids},
        soft={i: cell.soft[i] for i in ids},
        cost={i: cell.cost[i] for i in ids},
        label={i: cell.label[i] for i in ids},
        infra_excluded=sum(1 for i in cell.label if i in keep and cell.label[i] == "infra_error"),
        artifact_hashes=cell.artifact_hashes,
    )


def strata_contrasts(
    name: str,
    a: CellAggregate,
    b: CellAggregate,
    stratum_of: Mapping[str, str],
    *,
    seed: int = 0,
) -> dict[str, ContrastResult]:
    """One paired contrast per stratum (ADR 0016 §Statistics strata breakdown).

    ``stratum_of`` maps ``instance_id → stratum key`` (e.g. repo, or
    ``difficulty.files`` single/multi via :func:`difficulty_stratum`); the
    shared instance list is grouped and a sub-contrast computed per stratum.
    Deterministic key order (sorted).
    """
    groups: dict[str, list[str]] = {}
    for iid in sorted(a.hard):
        groups.setdefault(stratum_of.get(iid, "unknown"), []).append(iid)
    return {
        stratum: paired_contrast(
            f"{name}::{stratum}", _restrict(a, ids), _restrict(b, ids), seed=seed
        )
        for stratum, ids in sorted(groups.items())
    }


def difficulty_stratum(difficulty_files: int) -> str:
    """``difficulty.files`` → the ADR 0013 single/multi-file stratum label."""
    return "single_file" if difficulty_files <= 1 else "multi_file"


@dataclass(frozen=True, slots=True)
class NamedContrast:
    """A contrast request: a label plus the treatment/control cell names."""

    name: str
    treatment: str
    control: str


def campaign_report(
    campaign_id: str,
    cells: Mapping[str, CellAggregate],
    contrasts: Sequence[NamedContrast],
    *,
    stratum_of: Mapping[str, str] | None = None,
    seed: int = 0,
) -> dict[str, object]:
    """Assemble the campaign report skeleton (ADR 0016 §Output artifacts).

    Emits paired deltas + CIs + McNemar p per named contrast, an optional
    per-stratum breakdown, the cost layer, and the per-cell infra counts (R8).
    A pure function of the loaded cell aggregates — byte-stable on rerun.
    """
    results = [_contrast_block(cells, c, stratum_of, seed) for c in contrasts]
    return {
        "campaign_id": campaign_id,
        "cells": {name: _cell_summary(cell) for name, cell in sorted(cells.items())},
        "contrasts": results,
    }


def _contrast_block(
    cells: Mapping[str, CellAggregate],
    contrast: NamedContrast,
    stratum_of: Mapping[str, str] | None,
    seed: int,
) -> dict[str, object]:
    a, b = cells[contrast.treatment], cells[contrast.control]
    result = paired_contrast(contrast.name, a, b, seed=seed).to_dict()
    if stratum_of is not None:
        strata = strata_contrasts(contrast.name, a, b, stratum_of, seed=seed)
        result["strata"] = {k: v.to_dict() for k, v in strata.items()}
    return result


def _cell_summary(cell: CellAggregate) -> dict[str, object]:
    return {
        "n": len(cell.hard),
        "resolved": sum(cell.hard.values()),
        "total_cost_usd": cell.total_cost,
        "infra_excluded": cell.infra_excluded,
        "artifact_hashes": list(cell.artifact_hashes),
    }
