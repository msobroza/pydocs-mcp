"""The supervisor's handle on the run record: the record it last wrote and the generation it is at.

Every write is generation-checked against the supervisor's own last write, so
if anyone else moved the record (the launcher recording ``lost``), the write
fails with ``StaleGenerationError`` instead of overwriting a newer status.

One failure must not wedge the keeper: a write can land while its answer is
lost (the upload succeeded, the generation lookup after it did not). The
keeper then holds an old generation, and its next write would look stale
forever. So a stale write is retried once at the stored generation when the
stored bytes are exactly the ones whose answer was lost: that write was ours.

A write is a change to the record, not a whole record, so the retry applies
the change to the write that landed. Built from the record as last confirmed
instead, a cache-count update after an ending whose answer was lost would
write ``running`` back over the recorded ending, and a relaunch would run a
succeeded run again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError, StaleGenerationError
from pydocs_eval.bench_vm.run_record import RunRecord, run_record_bytes

RecordChange = Callable[[RunRecord], RunRecord]


@dataclass
class RunRecordKeeper:
    """The run record as of the supervisor's last read or write, and its GCS generation."""

    cloud: BenchCloud
    uri: str
    record: RunRecord
    generation: int
    # A written record whose write raised without saying whether it landed.
    _unconfirmed: RunRecord | None = field(default=None, repr=False)

    def write(self, change: RecordChange) -> None:
        """Store ``change(record)``; raises ``BenchCloudError`` and keeps the old record."""
        record = change(self.record)
        try:
            self.generation = self._write(record)
        except StaleGenerationError:
            if not self._adopt_unconfirmed_write():
                raise
            record = change(self.record)
            self.generation = self._write(record)
        self.record = record

    def _write(self, record: RunRecord) -> int:
        try:
            generation = self.cloud.write_object(
                self.uri, run_record_bytes(record), if_generation_match=self.generation
            )
        except StaleGenerationError:
            raise
        except BenchCloudError:
            self._unconfirmed = record
            raise
        self._unconfirmed = None
        return generation

    def _adopt_unconfirmed_write(self) -> bool:
        """Move to the stored record if it is our unconfirmed write; else ``False``."""
        stored = self.cloud.read_object(self.uri)
        unconfirmed = self._unconfirmed
        if stored is None or unconfirmed is None or stored.data != run_record_bytes(unconfirmed):
            return False
        self.record, self.generation = unconfirmed, stored.generation
        self._unconfirmed = None
        return True
