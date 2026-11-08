"""The supervisor's hooks into the persisted caches: when a transfer may start, where pushes run, the record's byte counts.

``persisted_caches`` moves the bytes; this module decides when the supervisor
lets it, and writes what moved into the run record. A cache is advisory, so a
failed record write here is logged and never ends the run.

**The restore** runs before the child, so it delays the run by what it takes.
It starts no transfer once ``cache_restore_share`` of the time to the soft
deadline is spent, so the child keeps the rest. A transfer is sized by its own
timeouts, never by a reserve: reserving an archive's worst case (its timeout,
every attempt: 90 minutes) closed the restore on every run shorter than about
two hours, both smoke specs included. A hung transfer is bounded by those
timeouts, and past them by ``max_runtime``.

**The pushes** run on a worker thread (``CachePushWorker``), one at a time,
beside the wait for the child: an upload of several GB never holds up the
shutdown poll, the state syncs or the soft deadline. The ending is recorded
without waiting for one; the push after the ending waits for it first. No
transfer starts once a shutdown is requested, the push after the ending's
included. The known cost: a push in flight at the ending runs beside the final
state push and the record write, and after a shutdown it shares the VM's last
seconds with them; one that outlasts ``max_runtime`` is cut off with the VM,
after the ending is recorded, and an archive upload is absent-or-whole.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Protocol

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.persisted_caches import PersistedCaches, TransferGate
from pydocs_eval.bench_vm.supervised_run import SupervisedRun
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event

# The share of the time to the soft deadline in which the restore may start transfers.
DEFAULT_CACHE_RESTORE_SHARE = 0.5


class ShutdownRequested(Protocol):
    """What the gates read of the shutdown signal (``shutdown_signal.ShutdownSignal``)."""

    @property
    def requested(self) -> bool: ...


def cache_restore_gate(
    soft_deadline_s: float, share: float, shutdown: ShutdownRequested
) -> TransferGate:
    """A restore transfer may start within ``share`` of the time to the soft deadline, until a shutdown."""
    until = time.monotonic() + soft_deadline_s * share
    return lambda: time.monotonic() < until and not shutdown.requested


def cache_push_gate(shutdown: ShutdownRequested) -> TransferGate:
    """A push transfer may start until a shutdown is asked: those seconds belong to the state."""
    return lambda: not shutdown.requested


def write_record_update(run: SupervisedRun, update: dict[str, object]) -> None:
    """Write the record with ``update``; a failure is logged, and retried by the next write."""
    try:
        run.keeper.write(lambda record: record.model_copy(update=update))
    except BenchCloudError as exc:
        log_supervisor_event(
            "bench_vm_record_write_failed", run_id=run.facts.run_id, detail=str(exc)
        )


def restore_caches(run: SupervisedRun, caches: PersistedCaches, gate: TransferGate) -> None:
    """Restore before the child; the record gains the bytes restored."""
    if caches.restore(may_start_transfer=gate):
        write_record_update(run, caches.record_counts())


@dataclass
class CachePushWorker:
    """The mid-run cache pushes, one at a time on a worker thread.

    Only the supervisor's thread starts, collects and waits; the worker only
    pushes, so the run record is never written from two threads. Every push,
    the one after the ending too, asks ``gate`` before each transfer starts.
    """

    caches: PersistedCaches
    run_id: str
    gate: TransferGate
    _worker: threading.Thread | None = field(default=None, repr=False)
    _moved: bool = False

    def start(self) -> None:
        """Start a push unless one is in flight (the next sync tries again)."""
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._push, name="bench-vm-cache-push", daemon=True)
        self._worker.start()

    def collect(self) -> bool:
        """Whether a push finished since the last collect moved bytes; ``False`` while one runs."""
        if self._worker is not None and self._worker.is_alive():
            return False
        moved, self._moved = self._moved, False
        return moved

    def wait(self) -> bool:
        """Wait for the push in flight (its transfers' timeouts bound it); then ``collect``."""
        if self._worker is not None:
            self._worker.join()
        return self.collect()

    def push_the_rest(self) -> bool:
        """Push on this thread, once none is in flight; whether it moved bytes."""
        return self.caches.push(may_start_transfer=self.gate) > 0

    def _push(self) -> None:
        try:
            self._moved = self.push_the_rest() or self._moved
        except Exception as exc:  # advisory: a worker must never die silently
            detail = f"{type(exc).__name__}: {exc}"
            log_supervisor_event("bench_vm_cache_push_failed", run_id=self.run_id, detail=detail)


def push_caches_after_ending(
    run: SupervisedRun, pushes: CachePushWorker, *, push_the_rest: bool
) -> None:
    """Once the ending is recorded: wait for the push in flight, push the rest, record the counts.

    ``push_the_rest`` is off after a shutdown, whose VM is going away; the
    record then only gains what the push in flight moved. A shutdown that
    arrives during the wait closes the gate, so the rest is deferred too.
    """
    moved = pushes.wait()
    if push_the_rest:
        moved = pushes.push_the_rest() or moved
    if moved:
        write_record_update(run, pushes.caches.record_counts())


__all__ = (
    "DEFAULT_CACHE_RESTORE_SHARE",
    "CachePushWorker",
    "cache_push_gate",
    "cache_restore_gate",
    "push_caches_after_ending",
    "restore_caches",
    "write_record_update",
)
