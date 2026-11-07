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
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import timedelta

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.cache_layout import child_cache_environment
from pydocs_eval.bench_vm.child_process import RunningChild, start_child
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.host_facts import HostFacts, read_host_facts
from pydocs_eval.bench_vm.kind_command import kind_invocation, with_interpreter
from pydocs_eval.bench_vm.own_vm import delete_refused_vm
from pydocs_eval.bench_vm.persisted_caches import PersistedCaches
from pydocs_eval.bench_vm.run_ending import FinalStatePush, RunEnding
from pydocs_eval.bench_vm.run_preflight import (
    PreflightFailed,
    PreflightPorts,
    run_preflight,
)
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.shutdown_signal import (
    ShutdownSignal,
    shutdown_ending,
    shutdown_signal_installed,
)
from pydocs_eval.bench_vm.state_sync import StateSync
from pydocs_eval.bench_vm.supervised_run import SupervisedRun, load_supervised_run
from pydocs_eval.bench_vm.supervisor_caches import (
    CachePushWorker,
    cache_push_gate,
    cache_restore_gate,
    restore_caches,
)
from pydocs_eval.bench_vm.supervisor_ending import (
    EXIT_ENDED_OTHERWISE,
    EXIT_REFUSED,
    EXIT_SUCCEEDED,
    end_run,
    periodic_sync,
    run_transition,
)
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event
from pydocs_eval.bench_vm.supervisor_ports import SupervisorPorts, SupervisorTiming
from pydocs_eval.bench_vm.supervisor_refusal import SupervisorRefused

# How often the wait for the child looks at the shutdown flag.
_SHUTDOWN_POLL_S = 0.25


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
        return end_run(run, ending, ports, sync, pushes)


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
    transition = run_transition(run, ports, RunStatus.RUNNING, detail)
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
            lambda: periodic_sync(run, ports, sync, pushes, may_start_cache_transfer),
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


__all__ = (
    "EXIT_ENDED_OTHERWISE",
    "EXIT_REFUSED",
    "EXIT_SUCCEEDED",
    "SupervisorPorts",
    "SupervisorTiming",
    "child_argv",
    "child_environment",
    "log_supervisor_event",
    "supervise_run",
)
