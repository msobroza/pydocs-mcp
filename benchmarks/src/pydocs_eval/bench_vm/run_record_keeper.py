"""The supervisor's handle on the run record: the record it last wrote and the generation it is at.

Every write is generation-checked against the supervisor's own last write, so
if anyone else moved the record (the launcher recording ``lost``), the write
fails with ``StaleGenerationError`` instead of overwriting a newer status.

One failure must not wedge the keeper: a write can land while its answer is
lost (the upload succeeded, the generation lookup after it did not). The
keeper then holds an old generation, and its next write would look stale
forever. So a stale write is retried once at the stored generation when the
stored bytes are exactly the ones whose answer was lost: that write was ours.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError, StaleGenerationError
from pydocs_eval.bench_vm.run_record import RunRecord, run_record_bytes


@dataclass
class RunRecordKeeper:
    """The run record as of the supervisor's last read or write, and its GCS generation."""

    cloud: BenchCloud
    uri: str
    record: RunRecord
    generation: int
    # The bytes of a write that raised without saying whether it landed.
    _unconfirmed: bytes | None = field(default=None, repr=False)

    def write(self, record: RunRecord) -> None:
        """Replace the record with ``record``; raises ``BenchCloudError`` and keeps the old one."""
        data = run_record_bytes(record)
        try:
            self.generation = self._write(data)
        except StaleGenerationError:
            if not self._adopt_unconfirmed_write():
                raise
            self.generation = self._write(data)
        self.record = record

    def _write(self, data: bytes) -> int:
        try:
            generation = self.cloud.write_object(
                self.uri, data, if_generation_match=self.generation
            )
        except StaleGenerationError:
            raise
        except BenchCloudError:
            self._unconfirmed = data
            raise
        self._unconfirmed = None
        return generation

    def _adopt_unconfirmed_write(self) -> bool:
        """Move to the stored generation if it holds our unconfirmed write; else ``False``."""
        stored = self.cloud.read_object(self.uri)
        if stored is None or self._unconfirmed is None or stored.data != self._unconfirmed:
            return False
        self.generation = stored.generation
        self._unconfirmed = None
        return True
