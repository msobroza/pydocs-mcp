"""The supervisor: one process per run, inside the run's container (``python -m pydocs_eval.bench_vm supervise``).

In order (spec "Supervisor (inside the container)"):

1. read the host facts, the run record and the spec copy, refusing an unknown
   record schema version (``supervised_run``);
2. record ``running`` with the host facts: machine type, zone, GPU, spot,
   image digest;
3. run the kind's CLI as a child from the in-image repository, its owned flags
   pointing into the state and cache directories, its output kept verbatim in
   the state directory, the state synced every ``sync_interval``;
4. end the run: a final sync, then ``succeeded``, ``failed`` with the exit code,
   or ``timed_out`` after stopping the child at the soft deadline;
5. delete its own VM when the spec's ``self_delete`` is on.

The state restore on a relaunch and the caches slot in between steps 2 and 3;
the shutdown signal is a fourth way to end step 3. Each lands with its own
change. The pre-flight (``run_preflight``: the GPU probe, the kind's provider
keys, before/after's product clone, the kind's own pre-flight) runs at the
start of step 3, before the child, and a failing one ends the run ``failed``
without starting it. The provider keys go into the child's environment only.

Every log line is one JSON object with named fields, on stderr.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError
from pydocs_eval.bench_vm.child_process import RunningChild, start_child
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.gpu_preflight import GpuProbe, probe_image_gpu
from pydocs_eval.bench_vm.kind_command import kind_invocation, with_interpreter
from pydocs_eval.bench_vm.product_clone import PRODUCT_REPO_URL
from pydocs_eval.bench_vm.run_preflight import (
    DEFAULT_PREFLIGHT_TIMEOUT_S,
    PreflightFailed,
    PreflightPorts,
    run_preflight,
)
from pydocs_eval.bench_vm.run_record import RunRecord, RunStatus, RunTransition
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.state_sync import StateSync
from pydocs_eval.bench_vm.supervised_run import SupervisedRun, load_supervised_run
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


@dataclass(frozen=True, slots=True)
class SupervisorTiming:
    """How often the state syncs, when the run gives up, and how long a stop may take."""

    sync_interval_s: float
    soft_deadline_s: float
    stop_grace_s: float = _DEFAULT_STOP_GRACE_S

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
    gpu_probe: Callable[[], GpuProbe] = probe_image_gpu
    product_repo_url: str = PRODUCT_REPO_URL
    preflight_timeout_s: float = DEFAULT_PREFLIGHT_TIMEOUT_S


@dataclass(frozen=True, slots=True)
class RunEnding:
    """How the child's run ended: the status to record, the exit code, and why."""

    status: RunStatus
    exit_code: int | None
    detail: str


def supervise_run(dirs: RunDirectories, ports: SupervisorPorts) -> int:
    """Supervise the run this VM serves, end to end; returns the process exit code.

    Raises ``SupervisorRefused`` (or ``BenchCloudError`` from reading the run)
    before starting a child; once ``running`` is recorded, every failure ends
    the run as ``failed``, with the final sync and the self-delete.
    """
    run = load_supervised_run(ports.cloud)
    timing = ports.timing or _timing_since_launch(run, ports)
    _record_running(run, ports)
    # Restore (relaunch), caches and the pre-flight run here, before the child.
    sync = StateSync.for_run(ports.cloud, dirs.state_dir, run.facts.run_prefix_uri)
    try:
        ending = _run_child(run, dirs, ports, timing, sync)
    except Exception as exc:  # whatever broke, a `running` run must still end
        detail = f"the supervisor failed while the run ran: {type(exc).__name__}: {exc}"
        ending = RunEnding(RunStatus.FAILED, None, detail)
    return _end_run(run, ending, ports, sync)


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
    running = run.record.with_transition(_transition(run, ports, RunStatus.RUNNING, detail))
    try:
        run.keeper.write(running)
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
        # Unbuffered, so the log in the state directory is current at every sync.
        "PYTHONUNBUFFERED": "1",
    }


def _run_child(
    run: SupervisedRun,
    dirs: RunDirectories,
    ports: SupervisorPorts,
    timing: SupervisorTiming,
    sync: StateSync,
) -> RunEnding:
    argv = child_argv(run, dirs, ports.child_python)
    dirs.cache_dir.mkdir(parents=True, exist_ok=True)
    preflight_started = time.monotonic()
    try:
        env = run_preflight(
            run, dirs, _preflight_ports(ports, timing), child_environment(run, dirs)
        )
    except PreflightFailed as failed:
        return RunEnding(RunStatus.FAILED, None, str(failed))
    # The pre-flight spent part of the run's time: the child gets the rest.
    spent_s = time.monotonic() - preflight_started
    timing = replace(timing, soft_deadline_s=max(0.0, timing.soft_deadline_s - spent_s))
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
    try:
        return _watch_child(child, timing, lambda: _periodic_sync(run, ports, sync))
    except BaseException:
        child.stop(timing.stop_grace_s)  # never leave the child running unsupervised
        raise


def _preflight_ports(ports: SupervisorPorts, timing: SupervisorTiming) -> PreflightPorts:
    return PreflightPorts(
        cloud=ports.cloud,
        gpu_probe=ports.gpu_probe,
        product_repo_url=ports.product_repo_url,
        python=ports.child_python,
        timeout_s=min(ports.preflight_timeout_s, timing.soft_deadline_s),
        stop_grace_s=timing.stop_grace_s,
    )


def _watch_child(
    child: RunningChild, timing: SupervisorTiming, sync_now: Callable[[], None]
) -> RunEnding:
    """Wait for the child, syncing every interval, until it exits or the soft deadline passes."""
    started = time.monotonic()
    deadline = started + timing.soft_deadline_s
    next_sync = started + timing.sync_interval_s
    while True:
        exit_code = child.wait(max(0.0, min(next_sync, deadline) - time.monotonic()))
        if exit_code is not None:
            return _exit_ending(exit_code)
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


def _push_state(run: SupervisedRun, ports: SupervisorPorts, sync: StateSync) -> datetime | None:
    """Push the state; the sync time, or ``None`` when the push failed (logged, never fatal)."""
    try:
        sync.push()
    except BenchCloudError as exc:
        log_supervisor_event("bench_vm_sync_failed", run_id=run.facts.run_id, detail=str(exc))
        return None
    synced_at = ports.clock()
    log_supervisor_event(
        "bench_vm_state_synced", run_id=run.facts.run_id, destination=sync.destination_uri
    )
    return synced_at


def _periodic_sync(run: SupervisedRun, ports: SupervisorPorts, sync: StateSync) -> None:
    # A failed sync or record write mid-run is logged and retried at the next
    # interval: one transient GCS error must not end hours of benchmark.
    synced_at = _push_state(run, ports, sync)
    if synced_at is None:
        return
    try:
        run.keeper.write(run.record.model_copy(update={"last_sync_at": synced_at}))
    except BenchCloudError as exc:
        log_supervisor_event(
            "bench_vm_record_write_failed", run_id=run.facts.run_id, detail=str(exc)
        )


def _end_run(run: SupervisedRun, ending: RunEnding, ports: SupervisorPorts, sync: StateSync) -> int:
    synced_at = _push_state(run, ports, sync)
    recorded = _record_ending(run, ending, ports, synced_at)
    log_supervisor_event(
        "bench_vm_run_ended",
        run_id=run.facts.run_id,
        status=ending.status,
        exit_code=ending.exit_code,
        detail=ending.detail,
        recorded=recorded,
    )
    if run.spec.self_delete:
        _delete_own_vm(run, ports)
    succeeded = recorded and ending.status is RunStatus.SUCCEEDED
    return EXIT_SUCCEEDED if succeeded else EXIT_ENDED_OTHERWISE


def _record_ending(
    run: SupervisedRun, ending: RunEnding, ports: SupervisorPorts, synced_at: datetime | None
) -> bool:
    ended: RunRecord = run.record.with_transition(
        _transition(run, ports, ending.status, ending.detail)
    )
    update: dict[str, object] = {"exit_code": ending.exit_code}
    if synced_at is not None:
        update["last_sync_at"] = synced_at
    try:
        run.keeper.write(ended.model_copy(update=update))
    except BenchCloudError as exc:
        log_supervisor_event(
            "bench_vm_record_write_failed", run_id=run.facts.run_id, detail=str(exc)
        )
        return False
    return True


def _delete_own_vm(run: SupervisedRun, ports: SupervisorPorts) -> None:
    facts = run.facts
    log_supervisor_event(
        "bench_vm_self_delete_requested", run_id=facts.run_id, instance=facts.instance
    )
    try:
        ports.cloud.delete_instance(facts.project, facts.zone, facts.instance)
    except BenchCloudError as exc:
        # The platform's maximum run duration still deletes the VM at max_runtime.
        log_supervisor_event(
            "bench_vm_self_delete_failed",
            run_id=facts.run_id,
            instance=facts.instance,
            detail=str(exc),
        )
