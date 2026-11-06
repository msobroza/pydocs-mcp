"""The pre-flight, before any spend: everything a run checks before its child starts.

In order, each step only when the run needs it:

1. on a GPU run, the GPU probe (``gpu_preflight``): torch must see CUDA and
   onnxruntime must list its CUDA execution provider;
2. the provider keys the kind declares, read from Secret Manager
   (``provider_secrets``) into the child's environment and nowhere else;
3. before/after's blobless product clone, holding both frozen commits
   (``product_clone``), which its plan already needs;
4. the kind's own pre-flight (the agent track's ``--preflight``, before/after's
   plan-only invocation), run like the child: from the repository, with the
   child's environment. Its output lands outside the state directory first;
   only a copy with every key value redacted is kept in the state directory
   (and so synced) and logged.

A step that fails raises ``PreflightFailed`` naming what is missing; the
supervisor ends the run ``failed`` with that detail, without starting the
child, so a paid kind spends nothing past its own pre-flight's probe. The
kind's pre-flight stops at the run's soft deadline at the latest.
"""

from __future__ import annotations

import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError
from pydocs_eval.bench_vm.child_process import start_child
from pydocs_eval.bench_vm.container_paths import RunDirectories
from pydocs_eval.bench_vm.gpu_preflight import GpuProbe, GpuProbeError, gpu_preflight_failures
from pydocs_eval.bench_vm.kind_command import kind_invocation, with_interpreter
from pydocs_eval.bench_vm.kind_flags import PRODUCT_CLONE_DIR_NAME
from pydocs_eval.bench_vm.product_clone import ProductCloneError, ensure_product_clone
from pydocs_eval.bench_vm.provider_secrets import (
    child_environment_with_provider_keys,
    read_provider_keys,
    redact_provider_keys,
)
from pydocs_eval.bench_vm.supervised_run import SupervisedRun
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event

# The agent track's pre-flight is a one-token call; before/after's plan loads
# the split (a dataset download on a cold cache). Both are minutes at most.
DEFAULT_PREFLIGHT_TIMEOUT_S = 1800.0
# The supervisor's log repeats the end of the pre-flight's output, not all of it.
_LOGGED_OUTPUT_MAX_CHARS = 16_000


class PreflightFailed(Exception):
    """The run must not start its child; the message is the run's ending detail."""


@dataclass(frozen=True, slots=True)
class PreflightPorts:
    """What the pre-flight drives: the cloud, the GPU probe, git, the kind's pre-flight.

    ``python`` is the interpreter the child runs on, which the GPU probe asks
    too. The kind's pre-flight runs at most ``timeout_s``, and never past
    ``deadline`` (a ``time.monotonic()`` instant: the run's soft deadline).
    """

    cloud: BenchCloud
    gpu_probe: Callable[[Sequence[str]], GpuProbe]
    product_repo_url: str
    python: tuple[str, ...]
    timeout_s: float
    deadline: float
    stop_grace_s: float


def run_preflight(
    run: SupervisedRun, dirs: RunDirectories, ports: PreflightPorts, base_env: Mapping[str, str]
) -> Mapping[str, str]:
    """Run every pre-flight step the run needs; returns the child's environment."""
    if run.spec.gpu is not None:
        _check_gpu(run, ports)
    provider_keys = _read_provider_keys(run, ports.cloud)
    env = child_environment_with_provider_keys(base_env, provider_keys)
    if run.spec.row.product_clone:
        _clone_product(run, dirs, ports.product_repo_url)
    if run.spec.row.preflight:
        _run_kind_preflight(run, dirs, ports, env, provider_keys)
    return env


def _check_gpu(run: SupervisedRun, ports: PreflightPorts) -> None:
    try:
        probe = ports.gpu_probe(ports.python)
    except GpuProbeError as exc:
        raise PreflightFailed(f"GPU pre-flight failed: {exc}") from None
    failures = gpu_preflight_failures(probe)
    if failures:
        raise PreflightFailed("GPU pre-flight failed: " + "; ".join(failures))
    log_supervisor_event(
        "bench_vm_gpu_preflight_passed",
        run_id=run.facts.run_id,
        torch_version=probe.torch_version,
        onnxruntime_providers=probe.onnxruntime_providers,
    )


def _read_provider_keys(run: SupervisedRun, cloud: BenchCloud) -> dict[str, str]:
    declared = run.spec.row.provider_keys
    try:
        provider_keys = read_provider_keys(cloud, run.facts.project, declared)
    except BenchCloudError as exc:
        raise PreflightFailed(f"reading the run's provider keys failed: {exc}") from None
    if declared:
        # The names only: a value never reaches a log line.
        log_supervisor_event(
            "bench_vm_provider_keys_read", run_id=run.facts.run_id, keys=list(declared)
        )
    return provider_keys


def _clone_product(run: SupervisedRun, dirs: RunDirectories, url: str) -> None:
    record = run.record
    commits = [c for c in (record.baseline_commit, record.candidate_commit) if c is not None]
    clone_dir = dirs.cache_dir / PRODUCT_CLONE_DIR_NAME
    try:
        ensure_product_clone(url, clone_dir, commits)
    except ProductCloneError as exc:
        raise PreflightFailed(f"the product clone failed: {exc}") from None
    log_supervisor_event(
        "bench_vm_product_clone_ready",
        run_id=run.facts.run_id,
        url=url,
        clone=clone_dir,
        commits=commits,
    )


def kind_preflight_argv(
    run: SupervisedRun, dirs: RunDirectories, python: tuple[str, ...]
) -> tuple[str, ...]:
    """The kind's pre-flight, its owned flags less ``preflight_omits``, then the spec's ``args``."""
    row = run.spec.row
    owned = row.owned_flag_values(kind_invocation(run, dirs))
    kept = tuple(arg for arg in owned if arg not in row.preflight_omits)
    return (*with_interpreter(row.preflight, python), *kept, *run.spec.args)


def _run_kind_preflight(
    run: SupervisedRun,
    dirs: RunDirectories,
    ports: PreflightPorts,
    env: Mapping[str, str],
    provider_keys: Mapping[str, str],
) -> None:
    argv = kind_preflight_argv(run, dirs, ports.python)
    log_path = dirs.preflight_log(run.facts.attempt)
    timeout_s = max(0.0, min(ports.timeout_s, ports.deadline - time.monotonic()))
    log_supervisor_event("bench_vm_kind_preflight_started", run_id=run.facts.run_id, argv=argv)
    # The raw output never touches the state directory: a CLI that echoes its
    # key would otherwise have it synced to GCS.
    with tempfile.TemporaryDirectory(prefix="bench-vm-preflight-") as scratch:
        raw_log = Path(scratch) / "preflight.log"
        try:
            exit_code = _wait_for_kind_preflight(argv, dirs, env, raw_log, timeout_s, ports)
        finally:
            output = _keep_redacted_output(raw_log, log_path, provider_keys)
    _log_preflight_output(run, log_path, exit_code, output)
    if exit_code != 0:
        raise PreflightFailed(
            f"the kind's pre-flight exited with exit code {exit_code}; its output: {log_path}"
        )


def _wait_for_kind_preflight(
    argv: tuple[str, ...],
    dirs: RunDirectories,
    env: Mapping[str, str],
    raw_log: Path,
    timeout_s: float,
    ports: PreflightPorts,
) -> int:
    try:
        preflight = start_child(argv, cwd=dirs.repo_dir, env=env, log_path=raw_log)
    except OSError as exc:
        raise PreflightFailed(f"the kind's pre-flight could not start: {exc}") from None
    try:
        exit_code = preflight.wait(timeout_s)
    except BaseException:
        preflight.stop(ports.stop_grace_s)  # never leave it running unsupervised
        raise
    if exit_code is None:
        stopped = preflight.stop(ports.stop_grace_s)
        raise PreflightFailed(
            f"the kind's pre-flight did not finish within {timeout_s:g}s; stopped with "
            f"exit code {stopped}"
        )
    return exit_code


def _keep_redacted_output(raw_log: Path, log_path: Path, provider_keys: Mapping[str, str]) -> str:
    """Copy the raw output into the state directory with every key value redacted."""
    raw = raw_log.read_text(encoding="utf-8", errors="replace") if raw_log.exists() else ""
    redacted = redact_provider_keys(raw, provider_keys)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(redacted, encoding="utf-8")
    return redacted


def _log_preflight_output(run: SupervisedRun, log_path: Path, exit_code: int, output: str) -> None:
    event = "bench_vm_kind_preflight_passed" if exit_code == 0 else "bench_vm_kind_preflight_failed"
    log_supervisor_event(
        event,
        run_id=run.facts.run_id,
        exit_code=exit_code,
        log=log_path,
        # Cut after redacting, so the cut never splits a key value out of reach.
        output=output[-_LOGGED_OUTPUT_MAX_CHARS:],
    )
