"""Relaunch rules: what the launcher does when the run already has a record.

Re-running ``make bench-vm`` over an existing record (spec "Relaunch rules"):

- ``succeeded`` is final: refused, naming where its results are;
- a run whose VM still exists is refused, naming the instance and its zone, so
  two machines never write one run's state; VMs are found by their run-id
  label across the region's zones;
- a ``launching`` or ``running`` record with no VM (a host crash, a shutdown
  script that ran out of time) is recorded ``lost`` first, through a
  generation-checked write;
- ``preempted``, ``failed``, ``timed_out``, ``stopped`` and ``lost`` relaunch as
  attempt n+1 on the frozen commits.

Nothing relaunches on its own: this runs only when the owner re-runs the command.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from pydocs_eval.bench_vm.bench_cloud import (
    BenchCloud,
    BenchCloudError,
    ListedInstance,
    StaleGenerationError,
)
from pydocs_eval.bench_vm.launch_events import log_launch_event
from pydocs_eval.bench_vm.launch_plan import PriorRun
from pydocs_eval.bench_vm.launch_refusal import LaunchRefused
from pydocs_eval.bench_vm.run_id import MAX_ATTEMPT
from pydocs_eval.bench_vm.run_record import (
    RunRecord,
    RunRecordError,
    RunStatus,
    RunTransition,
    parse_run_record,
    run_record_bytes,
)
from pydocs_eval.bench_vm.vm_request import LABEL_RUN_ID

RELAUNCHABLE_STATUSES = frozenset(
    {
        RunStatus.PREEMPTED,
        RunStatus.FAILED,
        RunStatus.TIMED_OUT,
        RunStatus.STOPPED,
        RunStatus.LOST,
    }
)
# The statuses that promise a VM; without one, the run is lost.
_STATUSES_WITH_A_VM = frozenset({RunStatus.LAUNCHING, RunStatus.RUNNING})


def read_prior_run(cloud: BenchCloud, uri: str) -> PriorRun | None:
    """The run's record, ``None`` on a first launch; an unreadable record is refused."""
    stored = cloud.read_object(uri)
    if stored is None:
        return None
    try:
        record = parse_run_record(stored.data, source=uri)
    except RunRecordError as exc:
        raise LaunchRefused(
            f"existing run record at {uri} is unreadable: {exc}; "
            "expected a record this launcher can relaunch from"
        ) from None
    return PriorRun(record=record, generation=stored.generation)


def refuse_unrelaunchable(prior: PriorRun, results_uri: str) -> None:
    """Refuse a succeeded run, a status with no relaunch rule, and a run at its last attempt."""
    record = prior.record
    if record.status not in RELAUNCHABLE_STATUSES | _STATUSES_WITH_A_VM | {RunStatus.SUCCEEDED}:
        raise LaunchRefused(
            f"run {record.run_id}: got status {record.status}, expected one of "
            f"{', '.join(sorted(RELAUNCHABLE_STATUSES))} to relaunch; this launcher has no "
            "relaunch rule for it"
        )
    if record.status is RunStatus.SUCCEEDED:
        raise LaunchRefused(
            f"run {record.run_id}: got status succeeded at attempt {record.attempt}, expected a "
            f"run that has not succeeded; a succeeded run is final, its results are at "
            f"{results_uri}; change the spec to start a new run"
        )
    if record.attempt >= MAX_ATTEMPT:
        raise LaunchRefused(
            f"run {record.run_id}: got attempt {record.attempt}, expected fewer than "
            f"{MAX_ATTEMPT} attempts (the VM name bench-<run-id>-<attempt> carries at most "
            f"{len(str(MAX_ATTEMPT))} digits)"
        )


def refuse_live_vm(cloud: BenchCloud, project: str, zones: Sequence[str], run_id: str) -> None:
    """Refuse when any VM labelled with the run id still exists in ``zones``."""
    live = cloud.list_instances(project, zones, label=LABEL_RUN_ID, value=run_id)
    if not live:
        return
    first = live[0]
    raise LaunchRefused(
        f"run {run_id}: got a live VM ({_instances_text(live)}), expected none, so that two "
        "machines never write one run's state; wait for it to end, or delete it "
        f"(gcloud compute instances delete {first.name} --zone={first.zone}), then launch again"
    )


def _instances_text(instances: Sequence[ListedInstance]) -> str:
    return "; ".join(
        f"instance {instance.name} in zone {instance.zone}, status {instance.status}"
        for instance in instances
    )


def record_lost_if_vm_vanished(
    cloud: BenchCloud,
    prior: PriorRun,
    *,
    uri: str,
    zones: Sequence[str],
    clock: Callable[[], datetime],
) -> PriorRun:
    """A ``launching``/``running`` record whose VM is gone becomes ``lost``; others pass through.

    Call it only after ``refuse_live_vm`` found no VM.
    """
    record = prior.record
    if record.status not in _STATUSES_WITH_A_VM:
        return prior
    lost = record.with_transition(_lost_transition(record, zones, clock()))
    try:
        generation = cloud.write_object(
            uri, run_record_bytes(lost), if_generation_match=prior.generation
        )
    except StaleGenerationError as exc:
        raise BenchCloudError(
            f"run {record.run_id}: the record changed while this launch read it ({exc}): "
            "another writer (the run's supervisor or another launch) moved it; "
            "launch again to act on its new status"
        ) from None
    except BenchCloudError as exc:
        raise BenchCloudError(f"recording run {record.run_id} lost failed: {exc}") from None
    log_launch_event(
        "bench_vm_run_lost", run_id=record.run_id, attempt=record.attempt, was=record.status
    )
    return PriorRun(record=lost, generation=generation)


def _lost_transition(record: RunRecord, zones: Sequence[str], at: datetime) -> RunTransition:
    last = record.history[-1]
    return RunTransition(
        status=RunStatus.LOST,
        at=at,
        attempt=record.attempt,
        instance=last.instance,
        zone=last.zone,
        machine=last.machine,
        detail=(
            f"no VM labelled {LABEL_RUN_ID}={record.run_id} in {', '.join(zones)}; the record "
            f"said {record.status} at attempt {record.attempt}"
        ),
    )
