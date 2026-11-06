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

The state restore on a relaunch, the caches and the pre-flight slot in between
steps 2 and 3, before the child starts; the shutdown signal is a fourth way to
end step 3. Each lands with its own change.

Every log line is one JSON object with named fields, on stderr.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError
from pydocs_eval.bench_vm.child_process import RunningChild, start_child
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.kind_flags import KindInvocation
from pydocs_eval.bench_vm.run_record import RunRecord, RunStatus, RunTransition
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.state_sync import StateSync
from pydocs_eval.bench_vm.supervised_run import SupervisedRun, load_supervised_run

EXIT_SUCCEEDED = 0
EXIT_ENDED_OTHERWISE = 1
EXIT_REFUSED = 2
# The deadline counts from the supervisor's start, but the platform deletes the
# VM at max_runtime from its creation; the margin covers the boot and the image
# pull before the supervisor starts, and the final sync after the child stops.
_FINAL_SYNC_MARGIN = timedelta(minutes=15)
_DEFAULT_STOP_GRACE_S = 60.0
# The image's interpreter; the kind table's CLIs start with it.
_KIND_CLI_INTERPRETER = "python"


@dataclass(frozen=True, slots=True)
class SupervisorTiming:
    """How often the state syncs, when the run gives up, and how long a stop may take."""

    sync_interval_s: float
    soft_deadline_s: float
    stop_grace_s: float = _DEFAULT_STOP_GRACE_S

    @classmethod
    def for_spec(cls, spec: RunSpec) -> SupervisorTiming:
        """``sync_interval`` from the spec; the soft deadline ``max_runtime`` less the margin.

        A ``max_runtime`` too short for the margin ends at half of it instead,
        which still leaves the other half for the stop and the final sync.
        """
        max_runtime_s = spec.max_runtime.total_seconds()
        margin_s = _FINAL_SYNC_MARGIN.total_seconds() + _DEFAULT_STOP_GRACE_S
        soft_s = max_runtime_s - margin_s if max_runtime_s > 2 * margin_s else max_runtime_s / 2
        return cls(sync_interval_s=spec.sync_interval.total_seconds(), soft_deadline_s=soft_s)


@dataclass(frozen=True, slots=True)
class SupervisorPorts:
    """The supervisor's external I/O.

    ``child_python`` replaces the kind CLI's leading ``python`` (the image's
    interpreter by default); ``timing`` overrides the spec's when set.
    """

    cloud: BenchCloud
    clock: Callable[[], datetime]
    child_python: tuple[str, ...] = field(default=(sys.executable,))
    timing: SupervisorTiming | None = None


@dataclass(frozen=True, slots=True)
class RunEnding:
    """How the child's run ended: the status to record, the exit code, and why."""

    status: RunStatus
    exit_code: int | None
    detail: str


def log_supervisor_event(event: str, **fields: object) -> None:
    print(json.dumps({"event": event, **fields}, default=str), file=sys.stderr, flush=True)


def supervise_run(dirs: RunDirectories, ports: SupervisorPorts) -> int:
    """Supervise the run this VM serves, end to end; returns the process exit code.

    Raises ``SupervisorRefused`` before writing anything or starting a child.
    """
    run = load_supervised_run(ports.cloud)
    _record_running(run, ports)
    # Restore (relaunch), caches and the pre-flight run here, before the child.
    sync = StateSync.for_run(ports.cloud, dirs.state_dir, run.facts.run_prefix_uri)
    timing = ports.timing or SupervisorTiming.for_spec(run.spec)
    ending = _run_child(run, dirs, ports, timing, sync)
    return _end_run(run, ending, ports, sync)


def _record_running(run: SupervisedRun, ports: SupervisorPorts) -> None:
    facts, spec = run.facts, run.spec
    gpu = "none" if spec.gpu is None else f"{spec.gpu.type}x{spec.gpu.count}"
    detail = f"gpu={gpu} spot={str(facts.spot).lower()} image_digest={facts.image_digest}"
    run.keeper.write(run.record.with_transition(_transition(run, ports, RunStatus.RUNNING, detail)))
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
    spec, record = run.spec, run.record
    cli = spec.row.cli
    if cli[0] == _KIND_CLI_INTERPRETER:
        cli = (*python, *cli[1:])
    invocation = KindInvocation(
        run_id=record.run_id,
        dataset=spec.dataset,
        split=spec.split,
        configs=spec.configs,
        corpus_dir=spec.corpus_dir,
        baseline_commit=record.baseline_commit,
        candidate_commit=record.candidate_commit,
        max_usd=spec.max_usd,
        gpu=spec.gpu is not None,
        state_dir=dirs.state_dir,
        cache_dir=dirs.cache_dir,
    )
    return (*cli, *spec.row.owned_flag_values(invocation), *spec.args)


def child_environment(run: SupervisedRun, dirs: RunDirectories) -> Mapping[str, str]:
    """The container's environment plus the run's identity and directories."""
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
    try:
        child = start_child(
            argv,
            cwd=dirs.repo_dir,
            env=child_environment(run, dirs),
            log_path=dirs.child_log(run.facts.attempt),
        )
    except OSError as exc:
        return RunEnding(RunStatus.FAILED, None, f"the child could not start: {exc}")
    log_supervisor_event("bench_vm_child_started", run_id=run.facts.run_id, argv=argv)
    return _watch_child(child, timing, lambda: _periodic_sync(run, ports, sync))


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
