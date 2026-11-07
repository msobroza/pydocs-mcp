"""The supervisor: one process per run, inside the run's container (``python -m pydocs_eval.bench_vm supervise``).

In order (spec "Supervisor (inside the container)"):

1. read the host facts, the run record and the spec copy, refusing an unknown
   record schema version (``supervised_run``); a run refused once the host
   facts are known writes nothing, and deletes its VM unless the spec copy
   says ``self_delete: false`` (``own_vm``);
2. record ``running`` with the host facts: machine type, zone, GPU, spot,
   image digest;
3. on a relaunch (attempt 2 on), restore the run's state from GCS into the
   same fixed path, so each kind resumes from its own ledger; a failed
   restore ends the run ``failed`` before any child, and pushes nothing;
4. run the kind's CLI as a child from the in-image repository, its owned flags
   pointing into the state and cache directories, its output kept verbatim in
   the state directory, the state synced every ``sync_interval``;
5. end the run: a final sync, then ``succeeded``, ``failed`` with the exit code,
   or ``timed_out`` after stopping the child at the soft deadline; or, on the
   shutdown signal (``shutdown_signal``), stop the child, push the ledgers
   before the rest, and record ``preempted`` or ``stopped``;
6. delete its own VM when the spec's ``self_delete`` is on and the run ended
   by itself; a preemption and the maximum run duration delete the VM without it.

The persisted caches (``persisted_caches``) restore between steps 3 and 4,
within a share of the time to the soft deadline, and push at every sync,
after the state, on a worker thread beside the wait for the child
(``supervisor_caches``); the ending is recorded without waiting for a push,
and on an ending the supervisor reached by itself the last push runs once the
ending is recorded, so a long upload never delays it. No cache transfer starts
once a shutdown is requested. The
pre-flight (``run_preflight``: the GPU probe, the kind's provider keys,
before/after's product clone, the kind's own pre-flight) runs at the start of
step 4, before the child, and a failing one ends the run ``failed`` without
starting it. The provider keys go into the child's environment only.

Every log line is one JSON object with named fields, on stderr.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError
from pydocs_eval.bench_vm.cache_layout import child_cache_environment
from pydocs_eval.bench_vm.child_process import RunningChild, start_child
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.gpu_preflight import GpuProbe, probe_image_gpu
from pydocs_eval.bench_vm.host_facts import HostFacts, read_host_facts
from pydocs_eval.bench_vm.kind_command import kind_invocation, with_interpreter
from pydocs_eval.bench_vm.own_vm import delete_own_vm, delete_refused_vm
from pydocs_eval.bench_vm.persisted_caches import PersistedCaches, TransferGate
from pydocs_eval.bench_vm.product_clone import PRODUCT_REPO_URL
from pydocs_eval.bench_vm.run_ending import FinalStatePush, RunEnding
from pydocs_eval.bench_vm.run_preflight import (
    DEFAULT_PREFLIGHT_TIMEOUT_S,
    PreflightFailed,
    PreflightPorts,
    run_preflight,
)
from pydocs_eval.bench_vm.run_record import RunRecord, RunStatus, RunTransition
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.shutdown_signal import (
    ShutdownSignal,
    shutdown_ending,
    shutdown_signal_installed,
)
from pydocs_eval.bench_vm.state_sync import StateSync
from pydocs_eval.bench_vm.supervised_run import SupervisedRun, load_supervised_run
from pydocs_eval.bench_vm.supervisor_caches import (
    DEFAULT_CACHE_RESTORE_SHARE,
    CachePushWorker,
    cache_push_gate,
    cache_restore_gate,
    push_caches_after_ending,
    restore_caches,
    write_record_update,
)
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event
from pydocs_eval.bench_vm.supervisor_refusal import SupervisorRefused

EXIT_SUCCEEDED = 0
EXIT_ENDED_OTHERWISE = 1
EXIT_REFUSED = 2
# The platform deletes the VM at max_runtime from its creation, so the soft
# deadline counts from the attempt's `launching` record (written just before
# the VM is created) and stops short of max_runtime by this margin: room for a
# periodic sync in flight at the deadline, the child's stop grace and the final
# sync (each sync attempt is capped at 5 minutes, gcloud_bench_cloud).
_FINAL_SYNC_MARGIN = timedelta(minutes=25)
_DEFAULT_STOP_GRACE_S = 60.0
# On a shutdown the VM is going away (30 s after a spot preemption notice), so
# the child gets a short stop and the time left goes to the final pushes; the
# host shutdown script's docker-stop grace leaves room for both.
_DEFAULT_SHUTDOWN_STOP_GRACE_S = 10.0
# How often the wait for the child looks at the shutdown flag.
_SHUTDOWN_POLL_S = 0.25
# The endings the supervisor reaches by itself, after which it deletes its own
# VM when the spec says so; a preemption or a stop is the platform's to end.
_SELF_DELETING_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT})


@dataclass(frozen=True, slots=True)
class SupervisorTiming:
    """How often the state syncs, when the run gives up, how long a stop may take,
    and the share of the time to the soft deadline the cache restore may start transfers in."""

    sync_interval_s: float
    soft_deadline_s: float
    stop_grace_s: float = _DEFAULT_STOP_GRACE_S
    shutdown_stop_grace_s: float = _DEFAULT_SHUTDOWN_STOP_GRACE_S
    cache_restore_share: float = DEFAULT_CACHE_RESTORE_SHARE

    @classmethod
    def for_spec(cls, spec: RunSpec, *, elapsed: timedelta) -> SupervisorTiming:
        """``sync_interval`` from the spec; the soft deadline ``max_runtime`` less the margin.

        ``elapsed`` is the time since the attempt was launched (boot and image
        pull), which the deadline has already spent. A ``max_runtime`` too
        short for the margin ends at half of it instead, which still leaves
        the other half for the stop and the final sync.
        """
        max_runtime_s = spec.max_runtime.total_seconds()
        margin_s = _FINAL_SYNC_MARGIN.total_seconds() + _DEFAULT_STOP_GRACE_S
        budget_s = max_runtime_s - margin_s if max_runtime_s > 2 * margin_s else max_runtime_s / 2
        return cls(
            sync_interval_s=spec.sync_interval.total_seconds(),
            soft_deadline_s=max(0.0, budget_s - elapsed.total_seconds()),
        )


@dataclass(frozen=True, slots=True)
class SupervisorPorts:
    """The supervisor's external I/O.

    ``child_python`` replaces the kind CLI's leading ``python`` (the image's
    interpreter by default); ``timing`` overrides the spec's when set.
    ``gpu_probe`` asks the image what torch and onnxruntime see (a GPU run
    only); ``product_repo_url`` is where before/after's product clone comes
    from; a kind pre-flight still running after ``preflight_timeout_s`` is
    stopped and fails the run.
    """

    cloud: BenchCloud
    clock: Callable[[], datetime]
    child_python: tuple[str, ...] = field(default=(sys.executable,))
    timing: SupervisorTiming | None = None
    gpu_probe: Callable[[Sequence[str]], GpuProbe] = probe_image_gpu
    product_repo_url: str = PRODUCT_REPO_URL
    preflight_timeout_s: float = DEFAULT_PREFLIGHT_TIMEOUT_S


def supervise_run(dirs: RunDirectories, ports: SupervisorPorts) -> int:
    """Supervise the run this VM serves, end to end; returns the process exit code.

    Raises ``SupervisorRefused`` (or ``BenchCloudError`` from reading the run)
    before starting a child or writing anything, after deleting the VM once
    the host facts say which it is (``own_vm``); once ``running`` is recorded,
    every failure ends the run as ``failed``, with the final sync and the
    self-delete.
    """
    with shutdown_signal_installed() as shutdown:
        # Without the host facts this VM cannot tell which instance it is: nothing to delete.
        facts = read_host_facts(ports.cloud)
        run, timing = _accept_run(facts, ports)
        sync = StateSync.for_run(ports.cloud, dirs.state_dir, run.facts.run_prefix_uri)
        caches = PersistedCaches.for_run(ports.cloud, run, dirs, log=log_supervisor_event)
        pushes = CachePushWorker(caches, run.facts.run_id)
        try:
            # The caches and the pre-flight run here too, after the restore, before the child.
            ending = _restore_state(run, sync)
            if ending is None:
                gate = cache_restore_gate(
                    timing.soft_deadline_s, timing.cache_restore_share, shutdown
                )
                restore_caches(run, caches, gate)
                # The cache restore spent part of the deadline; an injected timing stays as given.
                timing = ports.timing or _timing_since_launch(run, ports)
                ending = _run_child(run, dirs, ports, timing, sync, shutdown, pushes)
        except Exception as exc:  # whatever broke, a `running` run must still end
            detail = f"the supervisor failed while the run ran: {type(exc).__name__}: {exc}"
            ending = RunEnding(RunStatus.FAILED, None, detail)
        return _end_run(run, ending, ports, sync, pushes)


def _accept_run(facts: HostFacts, ports: SupervisorPorts) -> tuple[SupervisedRun, SupervisorTiming]:
    """Load the run and record ``running``; a refusal deletes the VM it would leave billing."""
    try:
        run = load_supervised_run(ports.cloud, facts)
        timing = ports.timing or _timing_since_launch(run, ports)
        _record_running(run, ports)
    except (SupervisorRefused, BenchCloudError):
        delete_refused_vm(ports.cloud, facts)
        raise
    return run, timing


def _timing_since_launch(run: SupervisedRun, ports: SupervisorPorts) -> SupervisorTiming:
    # The record is at `launching` (supervised_run checks it), so its last
    # transition is this attempt's launch.
    launched_at = run.record.history[-1].at
    elapsed = max(timedelta(0), ports.clock() - launched_at)
    return SupervisorTiming.for_spec(run.spec, elapsed=elapsed)


def _record_running(run: SupervisedRun, ports: SupervisorPorts) -> None:
    facts, spec = run.facts, run.spec
    gpu = "none" if spec.gpu is None else f"{spec.gpu.type}x{spec.gpu.count}"
    detail = f"gpu={gpu} spot={str(facts.spot).lower()} image_digest={facts.image_digest}"
    transition = _transition(run, ports, RunStatus.RUNNING, detail)
    try:
        run.keeper.write(lambda record: record.with_transition(transition))
    except BenchCloudError as exc:
        # Nothing ran yet; a stale record means another writer owns the run.
        raise SupervisorRefused(f"recording {RunStatus.RUNNING} failed: {exc}") from None
    log_supervisor_event(
        "bench_vm_running",
        run_id=facts.run_id,
        attempt=facts.attempt,
        instance=facts.instance,
        zone=facts.zone,
        machine_type=facts.machine_type,
        gpu=gpu,
        spot=facts.spot,
        image_digest=facts.image_digest,
    )


def _transition(
    run: SupervisedRun, ports: SupervisorPorts, status: RunStatus, detail: str
) -> RunTransition:
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


def _restore_state(run: SupervisedRun, sync: StateSync) -> RunEnding | None:
    """On a relaunch, pull the state back; a ``failed`` ending when that fails, else ``None``."""
    if run.facts.attempt == 1:
        return None
    try:
        sync.restore()
    except BenchCloudError as exc:
        detail = f"restoring the state from {sync.destination_uri} failed: {exc}"
        return RunEnding(RunStatus.FAILED, None, detail, state_push=FinalStatePush.NOTHING)
    log_supervisor_event(
        "bench_vm_state_restored", run_id=run.facts.run_id, source=sync.destination_uri
    )
    return None


# --- the child ---------------------------------------------------------------------


def child_argv(
    run: SupervisedRun, dirs: RunDirectories, python: tuple[str, ...]
) -> tuple[str, ...]:
    """The kind's CLI, its owned flags for this run, then the spec's passthrough ``args``."""
    spec = run.spec
    owned = spec.row.owned_flag_values(kind_invocation(run, dirs))
    return (*with_interpreter(spec.row.cli, python), *owned, *spec.args)


def child_environment(run: SupervisedRun, dirs: RunDirectories) -> Mapping[str, str]:
    """The container's environment plus the run's identity and directories.

    The pre-flight then drops every provider key from it and adds the ones
    the run's kind declares (``provider_secrets``).
    """
    return {
        **os.environ,
        "BENCH_RUN_ID": run.facts.run_id,
        "BENCH_ATTEMPT": str(run.facts.attempt),
        "BENCH_STATE_DIR": str(dirs.state_dir),
        "BENCH_CACHE_DIR": str(dirs.cache_dir),
        # HOME, HF_HOME and FASTEMBED_CACHE_PATH: every cache at a fixed path.
        **child_cache_environment(dirs.cache_dir),
        # Unbuffered, so the log in the state directory is current at every sync.
        "PYTHONUNBUFFERED": "1",
    }


def _run_child(
    run: SupervisedRun,
    dirs: RunDirectories,
    ports: SupervisorPorts,
    timing: SupervisorTiming,
    sync: StateSync,
    shutdown: ShutdownSignal,
    pushes: CachePushWorker,
) -> RunEnding:
    argv = child_argv(run, dirs, ports.child_python)
    dirs.cache_dir.mkdir(parents=True, exist_ok=True)
    # The VM may begin going away before the child starts (during the restore
    # or the pre-flight): end at once, before any spend.
    if shutdown.requested:
        return shutdown_ending(ports.cloud, None, timing.shutdown_stop_grace_s)
    deadline = time.monotonic() + timing.soft_deadline_s
    try:
        env = run_preflight(
            run, dirs, _preflight_ports(ports, timing, deadline), child_environment(run, dirs)
        )
    except PreflightFailed as failed:
        return RunEnding(RunStatus.FAILED, None, str(failed))
    # The pre-flight spent part of the run's time: the child gets the rest.
    remaining_s = deadline - time.monotonic()
    if remaining_s <= 0:
        detail = (
            f"soft deadline of {timing.soft_deadline_s:g}s passed during the pre-flight; "
            "the child was not started"
        )
        return RunEnding(RunStatus.TIMED_OUT, None, detail)
    timing = replace(timing, soft_deadline_s=remaining_s)
    if shutdown.requested:
        return shutdown_ending(ports.cloud, None, timing.shutdown_stop_grace_s)
    try:
        child = start_child(
            argv,
            cwd=dirs.repo_dir,
            env=env,
            log_path=dirs.child_log(run.facts.attempt),
        )
    except OSError as exc:
        return RunEnding(RunStatus.FAILED, None, f"the child could not start: {exc}")
    log_supervisor_event("bench_vm_child_started", run_id=run.facts.run_id, argv=argv)
    may_start_cache_transfer = cache_push_gate(shutdown)
    try:
        return _watch_child(
            child,
            timing,
            lambda: _periodic_sync(run, ports, sync, pushes, may_start_cache_transfer),
            lambda: (
                shutdown_ending(ports.cloud, child, timing.shutdown_stop_grace_s)
                if shutdown.requested
                else None
            ),
        )
    except BaseException:
        child.stop(timing.stop_grace_s)  # never leave the child running unsupervised
        raise


def _preflight_ports(
    ports: SupervisorPorts, timing: SupervisorTiming, deadline: float
) -> PreflightPorts:
    return PreflightPorts(
        cloud=ports.cloud,
        gpu_probe=ports.gpu_probe,
        product_repo_url=ports.product_repo_url,
        python=ports.child_python,
        timeout_s=ports.preflight_timeout_s,
        deadline=deadline,
        stop_grace_s=timing.stop_grace_s,
    )


def _watch_child(
    child: RunningChild,
    timing: SupervisorTiming,
    sync_now: Callable[[], None],
    end_on_shutdown: Callable[[], RunEnding | None],
) -> RunEnding:
    """Wait for the child, syncing every interval, until it exits, the soft deadline passes,
    or the shutdown signal arrives (``end_on_shutdown`` answers an ending then)."""
    started = time.monotonic()
    deadline = started + timing.soft_deadline_s
    next_sync = started + timing.sync_interval_s
    while True:
        wake = min(next_sync, deadline, time.monotonic() + _SHUTDOWN_POLL_S)
        exit_code = child.wait(max(0.0, wake - time.monotonic()))
        if exit_code is not None:
            return _exit_ending(exit_code)
        shutdown_ended = end_on_shutdown()
        if shutdown_ended is not None:
            return shutdown_ended
        if time.monotonic() >= deadline:
            exit_code = child.stop(timing.stop_grace_s)
            detail = (
                f"soft deadline of {timing.soft_deadline_s:g}s passed; "
                f"child stopped with exit code {exit_code}"
            )
            return RunEnding(RunStatus.TIMED_OUT, exit_code, detail)
        if time.monotonic() >= next_sync:
            sync_now()
            next_sync = time.monotonic() + timing.sync_interval_s


def _exit_ending(exit_code: int) -> RunEnding:
    if exit_code == 0:
        return RunEnding(RunStatus.SUCCEEDED, 0, "child exited with exit code 0")
    return RunEnding(RunStatus.FAILED, exit_code, f"child exited with exit code {exit_code}")


# --- syncs and the ending --------------------------------------------------------


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


def _periodic_sync(
    run: SupervisedRun,
    ports: SupervisorPorts,
    sync: StateSync,
    pushes: CachePushWorker,
    may_start_cache_transfer: TransferGate,
) -> None:
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


def _end_run(
    run: SupervisedRun,
    ending: RunEnding,
    ports: SupervisorPorts,
    sync: StateSync,
    pushes: CachePushWorker,
) -> int:
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
    transition = _transition(run, ports, ending.status, ending.detail)
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
