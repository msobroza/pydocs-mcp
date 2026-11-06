"""The run's state directory, mirrored to ``gs://<runs-bucket>/<run-id>/state/``.

The sync is incremental (only what changed uploads), never deletes in GCS, and
skips temporary files: atomic writers' ``*.tmp`` siblings, downloads in flight
and lock files, which are either about to be renamed over a real file or
meaningless once their writer is gone. It also skips before/after's product
worktrees, full source checkouts the campaign re-creates on every run. Every ledger the kinds resume from is
append-only and tolerates a truncated last line, and every report is written
atomically, so a sync taken mid-run is always safe to restore.

A sync pushes the whole directory in one call. The shutdown path pushes the
files a resume needs first (``push_resume_files``: every JSONL and JSON file,
which is what the kinds resume from: the runner's run files, the agent track's
ledger, before/after's ``queue.jsonl`` and the manifests its resume checks,
such as ``campaign.lock.json``), then the rest with ``push``. A relaunch pulls
the state back into the same fixed path with ``restore`` before its child starts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud
from pydocs_eval.bench_vm.kind_flags import BEFORE_AFTER_WORKTREES_DIR

STATE_OBJECT_PREFIX = "state/"
# A Python regex over each file's state-relative POSIX path (``re.match``).
STATE_SYNC_EXCLUDE_PATTERN = (
    r".*(\.tmp|\.partial|\.incomplete|\.lock|-journal|~)$"
    # A directory being built under a temporary name (``<key>.<pid>.tmp/…``).
    r"|.*\.tmp/.*"
    # Product checkouts: re-created from the clone, never resumed from.
    rf"|{re.escape(BEFORE_AFTER_WORKTREES_DIR)}/.*"
)

# Everything but the ledgers, run files and manifests, for the shutdown's first push.
_NOT_A_RESUME_FILE_PATTERN = r"(?!.*\.jsonl?$)"


@dataclass(frozen=True, slots=True)
class StateSync:
    """Push one run's state directory to its GCS prefix."""

    cloud: BenchCloud
    state_dir: Path
    destination_uri: str

    @classmethod
    def for_run(cls, cloud: BenchCloud, state_dir: Path, run_prefix_uri: str) -> StateSync:
        return cls(
            cloud=cloud, state_dir=state_dir, destination_uri=run_prefix_uri + STATE_OBJECT_PREFIX
        )

    def push(self) -> None:
        """Upload every new or changed non-temporary file; raises ``BenchCloudError``."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cloud.sync_directory(
            self.state_dir, self.destination_uri, exclude_pattern=STATE_SYNC_EXCLUDE_PATTERN
        )

    def push_resume_files(self) -> None:
        """Upload only the new or changed ledgers and run files; raises ``BenchCloudError``."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.cloud.sync_directory(
            self.state_dir,
            self.destination_uri,
            exclude_pattern=f"{_NOT_A_RESUME_FILE_PATTERN}|{STATE_SYNC_EXCLUDE_PATTERN}",
        )

    def restore(self) -> None:
        """Pull the run's synced state into the state directory; raises ``BenchCloudError``."""
        self.cloud.restore_directory(self.destination_uri, self.state_dir)
