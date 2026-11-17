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
compared. A finished run must also show what it is checked for: some leg that
a relaunch reopened mid-way.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.kind_flags import RESULTS_DIR_NAME
from pydocs_eval.bench_vm.local_reference import local_reference_dir
from pydocs_eval.bench_vm.state_sync import STATE_OBJECT_PREFIX
from pydocs_eval.reporting.aggregate_blocks import (
    AggregateBlock,
    AggregatePairing,
    LegFileEvent,
    compare_aggregate_blocks,
    events_of,
    final_aggregate_block,
    pair_aggregate_blocks,
    task_ids,
)
from pydocs_eval.sweep import LATENCY_KEYS
from pydocs_eval.trackers.run_file_lines import read_run_file_lines

# The ``run_start`` fields that name a leg (``JsonlExperimentTracker``'s header).
_LEG_HEADER_FIELDS = ("system", "config_name", "dataset")
# The run tag naming the product commit (``sweep_support``: git, else the image's variable).
_COMMIT_TAG = "git_sha"


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
    """One leg file: its leg, its run tags (commit, platform, Python), how many task
    records it holds, after how many recorded tasks each relaunch reopened it, and its
    final aggregate block (empty if none)."""

    path: Path
    key: LegKey | None
    tags: Mapping[str, str]
    task_record_count: int
    resumed_after: tuple[int, ...]
    block: AggregateBlock

    @property
    def commit(self) -> str | None:
        return self.tags.get(_COMMIT_TAG)

    @property
    def resumed_mid_leg(self) -> bool:
        """A relaunch reopened the leg with some of its tasks recorded and some still to run."""
        return any(1 <= recorded < self.task_record_count for recorded in self.resumed_after)


@dataclass(frozen=True, slots=True)
class SideRole:
    """What one side of the comparison is, as its messages and listing say it."""

    possessive: str
    when_empty: str
    when_unfinished: str
    # Only the run's ``--run-id`` legs hold task records; the plain local leg never does.
    lists_task_records: bool


RUN_SIDE = SideRole(
    possessive="the run's",
    when_empty="pull it first with make bench-vm-pull",
    when_unfinished="relaunch the run with make bench-vm, then pull it again",
    lists_task_records=True,
)
LOCAL_SIDE = SideRole(
    possessive="the local reference's",
    when_empty="run the command local-command prints first",
    when_unfinished=(
        "run the command local-command prints to the end, into an empty results directory"
    ),
    lists_task_records=False,
)


@dataclass(frozen=True, slots=True)
class ComparedSide:
    """One side's leg files: every file read, and the readings of each leg."""

    role: SideRole
    results_dir: Path
    readings: tuple[LegFileReading, ...]
    legs: Mapping[LegKey, tuple[LegFileReading, ...]]


@dataclass(frozen=True, slots=True)
class LegVerdict:
    """One leg's two files and how their final blocks compare (values are ``(run, local)``)."""

    key: LegKey
    run: LegFileReading
    local: LegFileReading
    pairing: AggregatePairing
    failures: tuple[str, ...]

    @property
    def ran_one_commit(self) -> bool:
        """Both sides ran the same commit; otherwise the comparison is not only about the resume."""
        return self.run.commit == self.local.commit


@dataclass(frozen=True, slots=True)
class RunComparison:
    """Every leg of a run against the local reference; identical when nothing failed.

    ``run`` and ``local`` hold every leg file read on each side, paired or not,
    so a pull taken mid-run still shows how far the run got.
    """

    run: ComparedSide
    local: ComparedSide
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

    A side with no leg files, a headerless file, or two files for one leg is
    reported on its own and nothing is paired: its layout is fixed first.
    """
    run = read_compared_side(pulled_results_dir(pulled_dir, run_id), RUN_SIDE)
    local = read_compared_side(local_results_dir(local_dir, run_id), LOCAL_SIDE)
    problems = (*_side_problems(run), *_side_problems(local))
    legs, failures = ((), problems) if problems else _compare_legs(run, local)
    return RunComparison(run=run, local=local, legs=legs, failures=failures)


class LegFileUnreadable(ValueError):
    """A ``*.jsonl`` under a results directory that cannot be read as UTF-8 text."""


def read_compared_side(results_dir: Path, role: SideRole) -> ComparedSide:
    """Every ``*.jsonl`` under ``results_dir``, in path order, grouped by the leg it names."""
    readings = tuple(_read_leg_file(path) for path in sorted(results_dir.rglob("*.jsonl")))
    legs: dict[LegKey, tuple[LegFileReading, ...]] = {}
    for reading in readings:
        if reading.key is not None:
            legs[reading.key] = (*legs.get(reading.key, ()), reading)
    return ComparedSide(role=role, results_dir=results_dir, readings=readings, legs=legs)


def _read_leg_file(path: Path) -> LegFileReading:
    try:
        lines = read_run_file_lines(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise LegFileUnreadable(
            f"got an unreadable leg file {path} ({exc}), "
            "expected a UTF-8 JSONL file the runner wrote"
        ) from None
    # A line that is not a JSON object (a write cut short) is skipped, as the
    # JSONL tracker skips it on resume.
    return _leg_file_reading(path, [line.record for line in lines if line.record is not None])


def _leg_file_reading(path: Path, records: Sequence[Mapping[str, object]]) -> LegFileReading:
    header = next(iter(events_of(records, LegFileEvent.RUN_START)), None)
    resumes = (r.get("recorded_tasks") for r in events_of(records, LegFileEvent.RUN_RESUME))
    return LegFileReading(
        path=path,
        key=_leg_key(header),
        tags=_run_tags(header),
        task_record_count=len(task_ids(records)),
        resumed_after=tuple(recorded for recorded in resumes if isinstance(recorded, int)),
        block=tuple(final_aggregate_block(records)),
    )


def _run_tags(header: Mapping[str, object] | None) -> dict[str, str]:
    tags = header.get("tags") if header is not None else None
    return {str(k): str(v) for k, v in tags.items()} if isinstance(tags, Mapping) else {}


def _leg_key(header: Mapping[str, object] | None) -> LegKey | None:
    """The leg a ``run_start`` header names; ``None`` when it names no complete leg."""
    if header is None:
        return None
    system, config_name, dataset = (header.get(name) for name in _LEG_HEADER_FIELDS)
    if not (isinstance(system, str) and isinstance(config_name, str) and isinstance(dataset, str)):
        return None
    return LegKey(system, config_name, dataset)


# --- problems with a side's layout, reported before anything is paired ----------


def _side_problems(side: ComparedSide) -> tuple[str, ...]:
    """Nothing to compare on a side is a failure, never a vacuous pass; nor is an ambiguous leg."""
    if not side.readings:
        return (
            f"got no leg files under {side.results_dir}, expected {side.role.possessive}; "
            f"{side.role.when_empty}",
        )
    return (*_headerless_files(side), *_legs_with_two_files(side))


def _headerless_files(side: ComparedSide) -> tuple[str, ...]:
    fields = f"{', '.join(_LEG_HEADER_FIELDS[:-1])} and {_LEG_HEADER_FIELDS[-1]}"
    return tuple(
        f"got no run_start header naming {fields} in {reading.path}, expected a leg file "
        "the runner wrote; a runner stopped before its first line leaves one empty"
        for reading in side.readings
        if reading.key is None
    )


def _legs_with_two_files(side: ComparedSide) -> tuple[str, ...]:
    return tuple(
        f"leg {key}: got {len(found)} leg files under {side.results_dir} "
        f"({', '.join(str(reading.path) for reading in found)}), expected one; "
        "move the extra aside"
        for key, found in side.legs.items()
        if len(found) > 1
    )


# --- pairing and judging the legs ----------------------------------------------


def _compare_legs(
    run: ComparedSide, local: ComparedSide
) -> tuple[tuple[LegVerdict, ...], tuple[str, ...]]:
    """Each leg found on both sides judged, and every failure: one-sided legs first."""
    keys = sorted(run.legs.keys() | local.legs.keys(), key=str)
    verdicts = tuple(
        _judge_leg(key, run, local) for key in keys if key in run.legs and key in local.legs
    )
    failures = (
        *(_one_sided(key, run, local) for key in keys if (key in run.legs) != (key in local.legs)),
        *(failure for verdict in verdicts for failure in verdict.failures),
        *_no_resume(run),
    )
    return verdicts, failures


def _one_sided(key: LegKey, run: ComparedSide, local: ComparedSide) -> str:
    found, missing = (run, local) if key in run.legs else (local, run)
    return (
        f"leg {key}: got no leg file under {missing.results_dir}, "
        f"expected one to match {found.role.possessive} {found.legs[key][0].path}"
    )


def _judge_leg(key: LegKey, run: ComparedSide, local: ComparedSide) -> LegVerdict:
    run_leg, local_leg = run.legs[key][0], local.legs[key][0]
    unfinished = (*_unfinished(run, run_leg), *_unfinished(local, local_leg))
    failures = unfinished or compare_aggregate_blocks(run_leg.block, local_leg.block, LATENCY_KEYS)
    return LegVerdict(
        key=key,
        run=run_leg,
        local=local_leg,
        pairing=pair_aggregate_blocks(run_leg.block, local_leg.block, LATENCY_KEYS),
        failures=tuple(f"leg {key}: {failure}" for failure in failures),
    )


def _unfinished(side: ComparedSide, reading: LegFileReading) -> tuple[str, ...]:
    """A leg cut short has no final block yet: a run stopped, preempted or still running when
    pulled, or a local reference that crashed or was interrupted."""
    if reading.block:
        return ()
    role = side.role
    records = f" ({reading.task_record_count} task records)" if role.lists_task_records else ""
    return (
        f"got no final aggregate block in {role.possessive} {reading.path}{records}, "
        f"expected a finished leg; {role.when_unfinished}",
    )


def _no_resume(run: ComparedSide) -> tuple[str, ...]:
    """A finished run must show the relaunch it is checked for: some leg resumed mid-way.

    A run still cut short is already named as unfinished, so it is not judged here.
    """
    readings = [found[0] for found in run.legs.values()]
    if not all(reading.block for reading in readings):
        return ()
    if any(reading.resumed_mid_leg for reading in readings):
        return ()
    return (
        "got no leg of the run resumed mid-way, expected a run_resume after 1 to N-1 of a "
        "leg's N task records; the stop came after the leg finished, or the relaunch did "
        "not restore the run's state",
    )
