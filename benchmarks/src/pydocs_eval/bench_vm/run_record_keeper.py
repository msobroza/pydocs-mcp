"""The supervisor's handle on the run record: the record it last wrote and the generation it is at.

Every write is generation-checked against the supervisor's own last write, so
if anyone else moved the record (the launcher recording ``lost``), the write
fails with ``StaleGenerationError`` instead of overwriting a newer status.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.run_record import RunRecord, run_record_bytes


@dataclass
class RunRecordKeeper:
    """The run record as of the supervisor's last read or write, and its GCS generation."""

    cloud: BenchCloud
    uri: str
    record: RunRecord
    generation: int

    def write(self, record: RunRecord) -> None:
        """Replace the record with ``record``; raises ``BenchCloudError`` and keeps the old one."""
        self.generation = self.cloud.write_object(
            self.uri, run_record_bytes(record), if_generation_match=self.generation
        )
        self.record = record
