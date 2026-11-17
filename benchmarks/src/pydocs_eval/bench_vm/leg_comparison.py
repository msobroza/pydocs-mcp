"""Compare a pulled run's final aggregates with a local, uninterrupted run of the same spec.

The stop-and-resume acceptance asks a run stopped mid-leg and relaunched to end
with the same aggregates as an uninterrupted run of its spec. The run side is
what ``pull`` downloaded: ``<pulled results dir>/<run id>/state/results/``,
the runner's ``--run-id`` leg files. The local side is what ``local-command``
pointed the plain runner at: ``<local results dir>/<run id>/results/``, one
timestamped file per leg. Both are read from the local disk only.

Legs pair by their ``run_start`` header (system, config, dataset), never by
file name, since the two layouts name files differently. Each pair's final
aggregate blocks (``reporting.aggregate_blocks``, the reading the resume smoke
uses) must name the same metrics and agree bit for bit on every value except
the wall-clock latencies (``sweep.LATENCY_KEYS``), which are reported but not
compared.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.kind_flags import RESULTS_DIR_NAME
from pydocs_eval.bench_vm.local_reference import local_reference_dir
from pydocs_eval.bench_vm.state_sync import STATE_OBJECT_PREFIX
from pydocs_eval.reporting.aggregate_blocks import (
    AggregatePairing,
    compare_aggregate_blocks,
    final_aggregate_block,
    pair_aggregate_blocks,
    task_ids,
)
from pydocs_eval.sweep import LATENCY_KEYS
from pydocs_eval.trackers.run_file_lines import read_run_file_lines

AggregateBlock = tuple[tuple[str, float], ...]


@dataclass(frozen=True, slots=True)
class LegKey:
    """A leg as its ``run_start`` header names it."""

    system: str
    config_name: str
    dataset: str

    def __str__(self) -> str:
        return f"{self.system} / {self.config_name} / {self.dataset}"


@dataclass(frozen=True, slots=True)
class LegFileReading:
    """One leg file: its leg, its run tags (commit, platform, Python), its task records and
    its final aggregate block (empty if none)."""

    path: Path
    key: LegKey | None
    tags: Mapping[str, str]
    task_records: int
    block: AggregateBlock


@dataclass(frozen=True, slots=True)
class LegVerdict:
    """One leg's two files and how their final blocks compare (values are ``(run, local)``)."""

    key: LegKey
    run: LegFileReading
    local: LegFileReading
    pairing: AggregatePairing
    failures: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RunComparison:
    """Every leg of a run against the local reference; identical when nothing failed.

    ``run_files`` and ``local_files`` are every leg file read on each side,
    paired or not, so a pull taken mid-run still shows how far the run got.
    """

    run_results_dir: Path
    local_results_dir: Path
    run_files: tuple[LegFileReading, ...]
    local_files: tuple[LegFileReading, ...]
    legs: tuple[LegVerdict, ...]
    failures: tuple[str, ...]

    @property
    def identical(self) -> bool:
        return not self.failures


def pulled_results_dir(pulled_dir: Path, run_id: str) -> Path:
    """Where ``pull`` put the run's leg files: its state directory's ``results/``."""
    return pulled_dir / run_id / STATE_OBJECT_PREFIX / RESULTS_DIR_NAME


def local_results_dir(local_dir: Path, run_id: str) -> Path:
    """Where ``local-command`` pointed the runner's leg files."""
    return local_reference_dir(local_dir, run_id) / RESULTS_DIR_NAME


def compare_with_local_reference(run_id: str, pulled_dir: Path, local_dir: Path) -> RunComparison:
    """Read both sides' leg files and judge every leg.

    A side with no leg files, or with two files for one leg, is reported on
    its own and nothing is paired: its layout is fixed first.
    """
    run = _read_side(pulled_results_dir(pulled_dir, run_id), _RUN_SIDE)
    local = _read_side(local_results_dir(local_dir, run_id), _LOCAL_SIDE)
    problems = (*_side_problems(run), *_side_problems(local))
    legs, failures = ((), problems) if problems else _compare_legs(run, local)
    return RunComparison(
        run_results_dir=run.results_dir,
        local_results_dir=local.results_dir,
        run_files=run.readings,
        local_files=local.readings,
        legs=legs,
        failures=failures,
    )


def read_leg_files(results_dir: Path) -> tuple[LegFileReading, ...]:
    """Every ``*.jsonl`` under ``results_dir``, in path order."""
    return tuple(_read_leg_file(path) for path in sorted(results_dir.rglob("*.jsonl")))


def _read_leg_file(path: Path) -> LegFileReading:
    # A line that is not a JSON object (a write cut short) is skipped, as the
    # JSONL tracker skips it on resume.
    records = [line.record for line in read_run_file_lines(path) if line.record is not None]
    header = next((r for r in records if r.get("_event") == "run_start"), None)
    tags = header.get("tags") if header is not None else None
    return LegFileReading(
        path=path,
        key=_leg_key(header),
        tags={str(k): str(v) for k, v in tags.items()} if isinstance(tags, Mapping) else {},
        task_records=len(task_ids(records)),
        block=tuple(final_aggregate_block(records)),
    )


def _leg_key(header: Mapping[str, object] | None) -> LegKey | None:
    if header is None:
        return None
    return LegKey(str(header["system"]), str(header["config_name"]), str(header["dataset"]))


@dataclass(frozen=True, slots=True)
class _SideRole:
    """How messages name a side, and what to do when it has no leg files."""

    owner: str
    when_empty: str


_RUN_SIDE = _SideRole("the run's", "pull it first with make bench-vm-pull")
_LOCAL_SIDE = _SideRole("the local reference's", "run the command local-command prints first")


@dataclass(frozen=True, slots=True)
class _Side:
    """One side's leg files, grouped by leg."""

    role: _SideRole
    results_dir: Path
    readings: tuple[LegFileReading, ...]
    legs: Mapping[LegKey, tuple[LegFileReading, ...]]


def _read_side(results_dir: Path, role: _SideRole) -> _Side:
    readings = read_leg_files(results_dir)
    legs: dict[LegKey, tuple[LegFileReading, ...]] = {}
    for reading in readings:
        if reading.key is not None:
            legs[reading.key] = (*legs.get(reading.key, ()), reading)
    return _Side(role, results_dir, readings, legs)


def _side_problems(side: _Side) -> tuple[str, ...]:
    """Nothing to compare on a side is a failure, never a vacuous pass; nor is an ambiguous leg."""
    if not side.readings:
        return (
            f"got no leg files under {side.results_dir}, expected {side.role.owner}; "
            f"{side.role.when_empty}",
        )
    headerless = tuple(
        f"got no run_start header in {reading.path}, expected a leg file the runner wrote; "
        "a runner stopped before its first line leaves one empty"
        for reading in side.readings
        if reading.key is None
    )
    ambiguous = tuple(
        f"leg {key}: got {len(found)} leg files under {side.results_dir} "
        f"({', '.join(str(reading.path) for reading in found)}), expected one; "
        "move the extra aside"
        for key, found in side.legs.items()
        if len(found) > 1
    )
    return (*headerless, *ambiguous)


def _compare_legs(run: _Side, local: _Side) -> tuple[tuple[LegVerdict, ...], tuple[str, ...]]:
    """Each leg found on both sides judged, and every failure: one-sided legs first."""
    keys = sorted(run.legs.keys() | local.legs.keys(), key=str)
    verdicts = tuple(
        _judge_leg(key, run.legs[key][0], local.legs[key][0])
        for key in keys
        if key in run.legs and key in local.legs
    )
    failures = (
        *(_one_sided(key, run, local) for key in keys if (key in run.legs) != (key in local.legs)),
        *(failure for verdict in verdicts for failure in verdict.failures),
    )
    return verdicts, failures


def _one_sided(key: LegKey, run: _Side, local: _Side) -> str:
    found, missing = (run, local) if key in run.legs else (local, run)
    return (
        f"leg {key}: got no leg file under {missing.results_dir}, "
        f"expected one to match {found.role.owner} {found.legs[key][0].path}"
    )


def _judge_leg(key: LegKey, run: LegFileReading, local: LegFileReading) -> LegVerdict:
    failures = _unfinished(run, local) or compare_aggregate_blocks(
        run.block, local.block, LATENCY_KEYS
    )
    return LegVerdict(
        key=key,
        run=run,
        local=local,
        pairing=pair_aggregate_blocks(run.block, local.block, LATENCY_KEYS),
        failures=tuple(f"leg {key}: {failure}" for failure in failures),
    )


def _unfinished(run: LegFileReading, local: LegFileReading) -> list[str]:
    """A leg cut short has no final block yet: a run stopped, preempted or still running when
    pulled, or a local reference that crashed or was interrupted."""
    failures = []
    if not run.block:
        failures.append(
            f"got no final aggregate block in the run's {run.path} ({run.task_records} task "
            "records), expected a finished leg; relaunch the run with make bench-vm, then pull "
            "it again"
        )
    if not local.block:
        failures.append(
            f"got no final aggregate block in the local reference's {local.path}, expected a "
            "finished leg; run the command local-command prints to the end, into an empty "
            "results directory"
        )
    return failures
