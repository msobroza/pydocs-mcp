"""CLI: ``pydocs-eval-compute-metrics <trace-dir>`` — recompute every derived
metric from a directory of merged trajectories (ADR 0009–0012).

The trace-dir holds one subdirectory per trajectory; each subdir carries the
merged canonical stream ``events.jsonl`` (Task 2 producer output) plus a
``facts.json`` naming the gold + eval facts the metric layer needs. The command
orchestrates the read-only pipeline — attribution → metrics → ground-truth
outcome → single derived record (R3) — and writes, under ``--out``:

- ``trajectories/<trajectory_id>.json`` — the per-trajectory :class:`DerivedRecord`
  as canonical JSON (machine-readable, byte-stable on rerun), stamped with the R2
  identity (schema/score/taxonomy versions + artifact hash + run-config ref).
- ``aggregate.json`` — the run aggregate (FitnessReport-compatible) plus the
  run-level identity stamps and a per-trajectory index (machine-readable).
- ``report.txt`` — the same numbers as a human-scannable table.

Every output is a pure function of the immutable trace inputs, so a delete +
rerun regenerates byte-identical files (recomputability, R1).

Exit codes:
    0 — metrics computed and written.
    2 — input error (missing trace-dir / subdir facts, unparseable stream).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# WHY: import through the ``pydocs_eval.trajectory`` package (its ``__init__``
# re-exports these) rather than reaching only into leaf modules — importing the
# package fires the package initializer, which is the seam that would populate
# any decorator registry on the CLI entry-point path (the trackers-registry
# trap: a console script that imports only leaves leaves registries empty).
from pydocs_eval.trajectory import (
    DerivedRecord,
    GroundTruthOutcome,
    RunAggregate,
    canonical_json,
    compute_derived_record,
    infra_outcome,
    no_report_outcome,
    outcome_from_report,
    patch_apply_failed_outcome,
    run_aggregate,
    run_config_hash,
)
from pydocs_eval.trajectory.attribution import attribute_trajectory, load_events, load_header
from pydocs_eval.trajectory.metrics import compute_metrics
from pydocs_eval.trajectory.schema import LoopEvent, ToolEvent, TrajectoryError

_EVENTS_FILENAME = "events.jsonl"
_FACTS_FILENAME = "facts.json"
_OUT_SUBDIR = "derived"
_REQUIRED_FACT_KEYS = ("trajectory_id", "instance_id", "workspace_root", "gold_files")
_VALID_OUTCOME_KINDS = ("infra", "patch_apply_failed", "no_report")


class ComputeMetricsError(Exception):
    """A trace-dir input problem the operator must fix (maps to exit code 2)."""


@dataclass(frozen=True, slots=True)
class TrajectoryFacts:
    """The gold + eval facts one trajectory needs, parsed from ``facts.json``.

    ``report`` is a swebench-style per-instance eval report; when absent,
    ``outcome_kind`` selects a degenerate ground-truth outcome (ADR 0012).
    """

    trajectory_id: str
    instance_id: str
    workspace_root: str
    gold_files: frozenset[str]
    gold_line_map: dict[str, frozenset[int]]
    final_patch_files: frozenset[str]
    gold_f2p: frozenset[str]
    gold_p2p: frozenset[str]
    turn_cap: int | None
    patch_bytes: int
    cost_usd: float
    report: dict[str, Any] | None
    outcome_kind: str | None


def load_facts(facts_path: Path) -> TrajectoryFacts:
    """Parse + validate one trajectory's ``facts.json`` (typed errors on defect)."""
    raw = _read_json(facts_path)
    missing = [key for key in _REQUIRED_FACT_KEYS if key not in raw]
    if missing:
        raise ComputeMetricsError(
            f"{facts_path}: missing keys {missing!r}, expected all of {_REQUIRED_FACT_KEYS!r}"
        )
    return _facts_from_raw(raw)


def _read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, mapping IO / decode failures to a typed CLI error."""
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ComputeMetricsError(f"{path}: unreadable JSON ({exc})") from exc
    return data


def _facts_from_raw(raw: dict[str, Any]) -> TrajectoryFacts:
    """Build the typed :class:`TrajectoryFacts` from the validated raw dict."""
    line_map = raw.get("gold_line_map", {})
    turn_cap = raw.get("turn_cap")
    return TrajectoryFacts(
        trajectory_id=str(raw["trajectory_id"]),
        instance_id=str(raw["instance_id"]),
        workspace_root=str(raw["workspace_root"]),
        gold_files=frozenset(raw["gold_files"]),
        gold_line_map={k: frozenset(v) for k, v in line_map.items()},
        final_patch_files=frozenset(raw.get("final_patch_files", [])),
        gold_f2p=frozenset(raw.get("gold_f2p", [])),
        gold_p2p=frozenset(raw.get("gold_p2p", [])),
        turn_cap=None if turn_cap is None else int(turn_cap),
        patch_bytes=int(raw.get("patch_bytes", 0)),
        cost_usd=float(raw.get("cost_usd", 0.0)),
        report=raw.get("report"),
        outcome_kind=raw.get("outcome_kind"),
    )


def _build_outcome(facts: TrajectoryFacts) -> GroundTruthOutcome:
    """Select the ground-truth outcome: a real report, else a degenerate kind."""
    if facts.report is not None:
        return outcome_from_report(
            facts.instance_id, facts.report, gold_f2p=facts.gold_f2p, gold_p2p=facts.gold_p2p
        )
    kind = facts.outcome_kind
    if kind == "infra":
        return infra_outcome(facts.instance_id)
    if kind == "patch_apply_failed":
        return patch_apply_failed_outcome(facts.instance_id)
    if kind in (None, "no_report"):
        return no_report_outcome(facts.instance_id)
    raise ComputeMetricsError(
        f"facts outcome_kind={kind!r}, expected one of {_VALID_OUTCOME_KINDS!r} or a report block"
    )


def compute_trajectory(traj_dir: Path) -> DerivedRecord:
    """Recompute one trajectory's derived record from its immutable trace files."""
    facts = load_facts(traj_dir / _FACTS_FILENAME)
    try:
        return _derive(traj_dir, facts)
    except (OSError, ValueError, TrajectoryError) as exc:
        raise ComputeMetricsError(f"trajectory {traj_dir.name!r}: {exc}") from exc


def _derive(traj_dir: Path, facts: TrajectoryFacts) -> DerivedRecord:
    """Run the read-only attribution → metrics → derived-record pipeline (R3)."""
    events_path = traj_dir / _EVENTS_FILENAME
    header = load_header(events_path)
    events = load_events(events_path)
    tools = tuple(e for e in events if isinstance(e, ToolEvent))
    loops = tuple(e for e in events if isinstance(e, LoopEvent))
    attribution = attribute_trajectory(
        events, final_patch_files=facts.final_patch_files, workspace_root=facts.workspace_root
    )
    outcome = _build_outcome(facts)
    metrics = compute_metrics(
        attribution=attribution,
        tool_events=tools,
        loop_events=loops,
        gold_files=facts.gold_files,
        gold_line_map=facts.gold_line_map,
        gold_f2p=facts.gold_f2p,
        gold_p2p=facts.gold_p2p,
        outcome=outcome,
        cost_usd=facts.cost_usd,
        workspace_root=facts.workspace_root,
    )
    return compute_derived_record(
        trajectory_id=facts.trajectory_id,
        instance_id=facts.instance_id,
        metrics=metrics,
        attribution=attribution,
        outcome=outcome,
        events=events,
        gold_files=facts.gold_files,
        gold_f2p=facts.gold_f2p,
        final_patch_files=facts.final_patch_files,
        patch_bytes=facts.patch_bytes,
        turn_cap=facts.turn_cap,
        cost_usd=facts.cost_usd,
        schema_version=header.schema_version,
        artifact_hash=header.artifact_hash,
        run_config_ref=run_config_hash(header.run_config),
    )


def discover_trajectories(trace_dir: Path) -> list[Path]:
    """List the trajectory subdirs (those carrying a ``facts.json``), sorted."""
    if not trace_dir.is_dir():
        raise ComputeMetricsError(f"trace-dir {str(trace_dir)!r} is not a directory")
    dirs = [p for p in sorted(trace_dir.iterdir()) if (p / _FACTS_FILENAME).is_file()]
    if not dirs:
        raise ComputeMetricsError(
            f"trace-dir {str(trace_dir)!r}: no trajectory subdir with a {_FACTS_FILENAME!r}"
        )
    return dirs


def compute_run(trace_dir: Path) -> list[DerivedRecord]:
    """Compute every trajectory's derived record, ordered by ``trajectory_id``."""
    records = [compute_trajectory(d) for d in discover_trajectories(trace_dir)]
    return sorted(records, key=lambda r: r.trajectory_id)


def _aggregate_doc(aggregate: RunAggregate, records: list[DerivedRecord]) -> dict[str, object]:
    """Machine-readable aggregate: the run rollup + identity stamps + a per-traj index.

    The R2 identity stamps are lifted to run level (§5.6): ``schema_version`` /
    ``score_version`` / ``taxonomy_version`` are the run's producing versions, and
    ``artifact_hashes`` / ``run_config_refs`` are the sorted DISTINCT sets across the
    run's trajectories — a heterogeneous run lists every hash/ref rather than
    silently picking one, so cross-artifact contamination is visible, not hidden.
    """
    return {
        "run": aggregate.to_fitness_report_dict(),
        "infra_excluded": aggregate.infra_excluded,
        "n_trajectories": len(records),
        "score_version": records[0].score_version,
        "taxonomy_version": records[0].taxonomy_version,
        "schema_version": records[0].schema_version,
        "artifact_hashes": sorted({r.artifact_hash for r in records}),
        "run_config_refs": sorted({r.run_config_ref for r in records}),
        "trajectories": [_index_row(r) for r in records],
    }


def _index_row(record: DerivedRecord) -> dict[str, object]:
    """One compact per-trajectory row for the aggregate index."""
    return {
        "trajectory_id": record.trajectory_id,
        "instance_id": record.instance_id,
        "hard": record.hard,
        "soft": record.soft,
        "label": record.label,
        "cost_usd": record.cost_usd,
    }


def render_report(aggregate: RunAggregate, records: list[DerivedRecord]) -> str:
    """Human-scannable text report (deterministic: sorted keys, fixed precision)."""
    lines = [
        "pydocs-eval trajectory metrics",
        f"trajectories: {len(records)} "
        f"(graded {aggregate.n_samples}, infra-excluded {aggregate.infra_excluded})",
        f"aggregate soft score: {aggregate.score:.4f}",
        f"aggregate cost: ${aggregate.cost_usd:.4f}",
        "components:",
        *(f"  {k}: {aggregate.components[k]:.4f}" for k in sorted(aggregate.components)),
        "per-trajectory:",
        *(_report_row(r) for r in records),
    ]
    return "\n".join(lines) + "\n"


def _report_row(record: DerivedRecord) -> str:
    """One aligned per-trajectory line in the human report."""
    return (
        f"  {record.trajectory_id}  hard={record.hard} soft={record.soft:.4f}  "
        f"label={record.label}  cost=${record.cost_usd:.4f}  ({record.instance_id})"
    )


@dataclass(frozen=True, slots=True)
class WrittenPaths:
    """The three output paths, so callers (and tests) can assert file presence."""

    out_dir: Path
    trajectory_files: tuple[Path, ...]
    aggregate_file: Path
    report_file: Path


def write_outputs(out_dir: Path, records: list[DerivedRecord]) -> WrittenPaths:
    """Write per-trajectory JSON, ``aggregate.json``, and ``report.txt``."""
    traj_dir = out_dir / "trajectories"
    traj_dir.mkdir(parents=True, exist_ok=True)
    traj_files = tuple(_write_record(traj_dir, r) for r in records)
    aggregate = run_aggregate(records)
    aggregate_file = out_dir / "aggregate.json"
    aggregate_file.write_text(canonical_json(_aggregate_doc(aggregate, records)) + "\n", "utf-8")
    report_file = out_dir / "report.txt"
    report_file.write_text(render_report(aggregate, records), encoding="utf-8")
    return WrittenPaths(out_dir, traj_files, aggregate_file, report_file)


def _write_record(traj_dir: Path, record: DerivedRecord) -> Path:
    """Write one derived record as canonical JSON and return its path."""
    path = traj_dir / f"{record.trajectory_id}.json"
    path.write_text(canonical_json(record.to_dict()) + "\n", encoding="utf-8")
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydocs-eval-compute-metrics",
        description="Recompute trajectory metrics from a trace-dir of merged trajectories.",
    )
    parser.add_argument("trace_dir", type=Path, help="directory of per-trajectory subdirs")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default: <trace-dir>/derived); trace inputs stay immutable",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``pydocs-eval-compute-metrics`` console script."""
    args = _build_parser().parse_args(argv)
    try:
        records = compute_run(args.trace_dir)
        out_dir = args.out or (args.trace_dir / _OUT_SUBDIR)
        written = write_outputs(out_dir, records)
    except ComputeMetricsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {len(records)} trajectory record(s) to {written.out_dir}")
    return 0


if __name__ == "__main__":  # pragma: no cover - module-run convenience
    sys.exit(main())
