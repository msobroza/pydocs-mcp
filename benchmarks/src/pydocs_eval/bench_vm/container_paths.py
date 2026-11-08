"""The fixed absolute paths inside a bench VM's container, and the run directories built on them.

Fixed paths are a correctness requirement, not a convenience: the bench index
cache hashes the resolved corpus path and the product's per-project cache name
hashes the project path, so a cache restored on another VM is valid only at
the same paths. The repository path is fixed too, so a spec's ``corpus_dir``
resolves the same on every VM.

The host startup script (``infra/bench-vm/host/startup.sh``) mounts the run's
state and cache directories at ``STATE_DIR`` and ``CACHE_DIR``; a test pins
the two sides together.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

# The repository at the run's commit, baked into the image (Dockerfile.cpu /
# Dockerfile.cuda WORKDIR; infra/bench-vm/docker/smoke.py checks it).
REPO_DIR = Path("/opt/pydocs-mcp")
STATE_DIR = Path("/bench/state")
CACHE_DIR = Path("/bench/cache")
# The supervisor's logs, under the state directory; a relaunch never restores them.
LOGS_DIR_NAME = "logs"


@dataclass(frozen=True, slots=True)
class RunDirectories:
    """Where one run's child runs from and writes to; the fixed paths unless a test moves them."""

    state_dir: Path = STATE_DIR
    cache_dir: Path = CACHE_DIR
    repo_dir: Path = REPO_DIR

    def child_log(self, attempt: int) -> Path:
        """The child's redacted output, one new file per attempt so attempts never interleave."""
        return self.state_dir / LOGS_DIR_NAME / f"child-attempt-{attempt}.log"

    def preflight_log(self, attempt: int) -> Path:
        """The kind pre-flight's redacted output (agent track ``--preflight``, before/after's plan)."""
        return self.state_dir / LOGS_DIR_NAME / f"preflight-attempt-{attempt}.log"
