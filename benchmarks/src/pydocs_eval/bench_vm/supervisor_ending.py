"""The supervisor's state syncs and its ending: push the state, record the ending, delete the VM.

Every sync pushes the state, then lets the next cache push start
(``supervisor_caches``). At the ending the state is pushed as the
``RunEnding`` says, the ending is recorded, the cache pushes finish, and the
VM is deleted when the spec asks and the run ended by itself (``own_vm``).
A failed push or record write is logged and never fatal.
"""

from __future__ import annotations

from datetime import datetime

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.own_vm import delete_own_vm
from pydocs_eval.bench_vm.persisted_caches import TransferGate
from pydocs_eval.bench_vm.run_ending import FinalStatePush, RunEnding
from pydocs_eval.bench_vm.run_record import RunRecord, RunStatus, RunTransition
from pydocs_eval.bench_vm.state_sync import StateSync
from pydocs_eval.bench_vm.supervised_run import SupervisedRun
from pydocs_eval.bench_vm.supervisor_caches import (
    CachePushWorker,
    push_caches_after_ending,
    write_record_update,
)
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event
from pydocs_eval.bench_vm.supervisor_ports import SupervisorPorts

EXIT_SUCCEEDED = 0
EXIT_ENDED_OTHERWISE = 1
EXIT_REFUSED = 2
# The endings the supervisor reaches by itself, after which it deletes its own
# VM when the spec says so; a preemption or a stop is the platform's to end.
_SELF_DELETING_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT})


def run_transition(
    run: SupervisedRun, ports: SupervisorPorts, status: RunStatus, detail: str
) -> RunTransition:
    """A transition of this attempt on this VM, at the supervisor's clock."""
    facts = run.facts
    return RunTransition(
        status=status,
        at=ports.clock(),
        attempt=facts.attempt,
        instance=facts.instance,
        zone=facts.zone,
        machine=facts.machine_type,
        detail=detail,
    )


def _push_state(
    run: SupervisedRun,
    ports: SupervisorPorts,
    sync: StateSync,
    *,
    resume_files_only: bool = False,
) -> datetime | None:
    """Push the state; the sync time, or ``None`` when the push failed (logged, never fatal)."""
    files = "resume_files" if resume_files_only else "all"
    try:
        (sync.push_resume_files if resume_files_only else sync.push)()
    except BenchCloudError as exc:
        log_supervisor_event(
            "bench_vm_sync_failed", run_id=run.facts.run_id, files=files, detail=str(exc)
        )
        return None
    synced_at = ports.clock()
    log_supervisor_event(
        "bench_vm_state_synced",
        run_id=run.facts.run_id,
        files=files,
        destination=sync.destination_uri,
    )
    return synced_at


def _final_push(
    run: SupervisedRun, ports: SupervisorPorts, sync: StateSync, push: FinalStatePush
) -> datetime | None:
    """The ending's push; the time of the full push, ``None`` when it failed or never ran."""
    if push is FinalStatePush.NOTHING:
        return None
    if push is FinalStatePush.RESUME_FILES_FIRST:
        # A failure here still leaves the full push, which carries these files too.
        _push_state(run, ports, sync, resume_files_only=True)
    return _push_state(run, ports, sync)


def periodic_sync(
    run: SupervisedRun,
    ports: SupervisorPorts,
    sync: StateSync,
    pushes: CachePushWorker,
    may_start_cache_transfer: TransferGate,
) -> None:
    """One sync mid-run: the state, then the cache push, then the record's sync time and counts."""
    # A failed sync or record write mid-run is logged and retried at the next
    # interval: one transient GCS error must not end hours of benchmark.
    synced_at = _push_state(run, ports, sync)
    # The counts of the push that finished since the last sync; then the next
    # push starts, after the state, which a resume needs first.
    moved = pushes.collect()
    pushes.start(may_start_cache_transfer)
    update: dict[str, object] = pushes.caches.record_counts() if moved else {}
    if synced_at is not None:
        update["last_sync_at"] = synced_at
    if update:
        write_record_update(run, update)


def end_run(
    run: SupervisedRun,
    ending: RunEnding,
    ports: SupervisorPorts,
    sync: StateSync,
    pushes: CachePushWorker,
) -> int:
    """Push the state, record ``ending``, finish the cache pushes, delete the VM; the exit code."""
    synced_at = _final_push(run, ports, sync, ending.state_push)
    recorded = _record_ending(run, ending, ports, synced_at, pushes.caches.record_counts())
    log_supervisor_event(
        "bench_vm_run_ended",
        run_id=run.facts.run_id,
        status=ending.status,
        exit_code=ending.exit_code,
        detail=ending.detail,
        recorded=recorded,
    )
    # A preemption or a stop leaves seconds, which belong to the state: no new push.
    push_caches_after_ending(run, pushes, push_the_rest=ending.status in _SELF_DELETING_STATUSES)
    if run.spec.self_delete and ending.status in _SELF_DELETING_STATUSES:
        delete_own_vm(ports.cloud, run.facts)
    succeeded = recorded and ending.status is RunStatus.SUCCEEDED
    return EXIT_SUCCEEDED if succeeded else EXIT_ENDED_OTHERWISE


def _record_ending(
    run: SupervisedRun,
    ending: RunEnding,
    ports: SupervisorPorts,
    synced_at: datetime | None,
    cache_counts: dict[str, object],
) -> bool:
    transition = run_transition(run, ports, ending.status, ending.detail)
    update: dict[str, object] = {"exit_code": ending.exit_code, **cache_counts}
    if synced_at is not None:
        update["last_sync_at"] = synced_at

    def ended(record: RunRecord) -> RunRecord:
        return record.with_transition(transition).model_copy(update=update)

    try:
        run.keeper.write(ended)
    except BenchCloudError as exc:
        log_supervisor_event(
            "bench_vm_record_write_failed", run_id=run.facts.run_id, detail=str(exc)
        )
        return False
    return True
