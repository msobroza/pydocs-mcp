"""The shutdown signal: how the supervisor learns its VM is going away, and the ending it records.

The host shutdown script (``infra/bench-vm/host/shutdown.sh``) runs when the VM
is preempted, stopped or deleted, and ``docker stop``s the supervisor's
container with a grace period: Docker sends the container's PID 1, the
supervisor, SIGTERM. The child runs in its own session, so only the
supervisor sees it; it then decides how the child stops.

The handler only sets a flag (a signal handler may run between any two
bytecodes of the main thread, so it must not write records or start
pushes); the supervisor's wait loop polls it. On the signal the supervisor
reads the metadata server's preempted flag, stops the child within a short
grace, and ends ``preempted`` when the flag is set, ``stopped`` otherwise.

The known limit of polling: a call already in flight when the signal arrives
(a periodic sync, the relaunch's restore, the soft deadline's stop) finishes
first. Interrupting it instead could cut a generation-checked record write in
half. An incremental sync normally takes seconds; when one outlives a spot
VM's 30-second notice, the record stays ``running`` with no VM, which the
launcher's relaunch rules record as ``lost`` and relaunch from the last
completed sync.
"""

from __future__ import annotations

import signal
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import FrameType

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError
from pydocs_eval.bench_vm.child_process import RunningChild
from pydocs_eval.bench_vm.run_ending import FinalStatePush, RunEnding
from pydocs_eval.bench_vm.run_record import RunStatus

# ``TRUE`` once Compute Engine has begun preempting this spot VM.
_INSTANCE_PREEMPTED = "instance/preempted"


@dataclass(frozen=True, slots=True)
class ShutdownSignal:
    """Set once SIGTERM reached the supervisor; never cleared."""

    _received: threading.Event = field(default_factory=threading.Event)

    @property
    def requested(self) -> bool:
        return self._received.is_set()

    def on_sigterm(self, _signum: int, _frame: FrameType | None) -> None:
        self._received.set()


@contextmanager
def shutdown_signal_installed() -> Iterator[ShutdownSignal]:
    """Catch SIGTERM for the block, then put back the handler that was there.

    Held through the final push too: a second ``docker stop`` must not kill
    the supervisor halfway through it.
    """
    shutdown = ShutdownSignal()
    previous = signal.signal(signal.SIGTERM, shutdown.on_sigterm)
    try:
        yield shutdown
    finally:
        signal.signal(signal.SIGTERM, previous)


def shutdown_ending(cloud: BenchCloud, child: RunningChild | None, grace_s: float) -> RunEnding:
    """Read the preempted flag, stop the child (when one runs), and say how the run ended."""
    preempted, flag_detail = _read_preempted_flag(cloud)
    exit_code = None if child is None else child.stop(grace_s)
    stopped = (
        "no child had started" if exit_code is None else f"child stopped with exit code {exit_code}"
    )
    return RunEnding(
        status=RunStatus.PREEMPTED if preempted else RunStatus.STOPPED,
        exit_code=exit_code,
        detail=f"shutdown signal; {flag_detail}; {stopped}",
        state_push=FinalStatePush.RESUME_FILES_FIRST,
    )


def _read_preempted_flag(cloud: BenchCloud) -> tuple[bool, str]:
    try:
        value = cloud.read_instance_metadata(_INSTANCE_PREEMPTED)
    except BenchCloudError as exc:
        # Only the accounting depends on it; the flush must go on regardless.
        return False, f"preempted flag unreadable ({exc}), recorded as a stop"
    if (value or "").strip().upper() == "TRUE":
        return True, "preempted flag set"
    return False, "preempted flag unset"
