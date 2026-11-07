"""The supervisor's settings and external I/O: its timing, and the ports it drives.

``SupervisorTiming.for_spec`` derives the timing from the run spec; a test
injects a short one through ``SupervisorPorts.timing``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.gpu_preflight import GpuProbe, probe_image_gpu
from pydocs_eval.bench_vm.product_clone import PRODUCT_REPO_URL
from pydocs_eval.bench_vm.run_preflight import DEFAULT_PREFLIGHT_TIMEOUT_S
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.supervisor_caches import DEFAULT_CACHE_RESTORE_SHARE

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
