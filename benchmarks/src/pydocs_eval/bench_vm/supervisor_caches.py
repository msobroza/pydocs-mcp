"""The supervisor's hooks into the persisted caches: when a transfer may start, and the record's byte counts.

``persisted_caches`` moves the bytes; this module decides when the supervisor
lets it, and writes what moved into the run record. A cache is advisory, so a
failed record write here is logged and never ends the run.
"""

from __future__ import annotations

import time
from typing import Protocol

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.bounded_command import DEFAULT_ATTEMPTS
from pydocs_eval.bench_vm.gcloud_cache_transfers import ARCHIVE_TIMEOUT_S
from pydocs_eval.bench_vm.persisted_caches import PersistedCaches, TransferGate
from pydocs_eval.bench_vm.supervised_run import SupervisedRun
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event

# The longest one cache transfer can take (an archive's timeout, every
# attempt). No cache transfer starts later than this before the soft deadline;
# what is left is pushed once the ending is recorded.
CACHE_TRANSFER_RESERVE_S = ARCHIVE_TIMEOUT_S * DEFAULT_ATTEMPTS


class ShutdownRequested(Protocol):
    """What the gate reads of the shutdown signal (``shutdown_signal.ShutdownSignal``)."""

    @property
    def requested(self) -> bool: ...


def cache_transfer_gate(
    soft_deadline_s: float, reserve_s: float, shutdown: ShutdownRequested
) -> TransferGate:
    """A transfer may start while it cannot outlast the soft deadline, and no shutdown is asked."""
    until = time.monotonic() + soft_deadline_s - reserve_s
    return lambda: time.monotonic() < until and not shutdown.requested


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


def push_caches_after_ending(run: SupervisedRun, caches: PersistedCaches) -> None:
    """The last push, once the ending is recorded; the record only gains the new byte count."""
    if caches.push():
        write_record_update(run, caches.record_counts())


__all__ = (
    "CACHE_TRANSFER_RESERVE_S",
    "cache_transfer_gate",
    "push_caches_after_ending",
    "restore_caches",
    "write_record_update",
)
