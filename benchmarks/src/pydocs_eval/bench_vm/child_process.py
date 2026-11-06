"""The kind's CLI as a child process: started with its output kept verbatim, waited on, stopped.

The child runs in its own session, so a stop reaches its whole process group
(a runner's or campaign's own subprocesses included), not just the direct
child. Its stdout and stderr go straight to one log file, byte for byte, with
no pipe through the supervisor that could block, reorder or re-encode them.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunningChild:
    """A started child process."""

    process: subprocess.Popen[bytes]

    def wait(self, timeout_s: float) -> int | None:
        """The exit code once the child has exited, ``None`` if it still runs after ``timeout_s``."""
        try:
            return self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None

    def stop(self, grace_s: float) -> int:
        """SIGTERM the child's group, SIGKILL it after ``grace_s``; returns the exit code."""
        self._signal_group(signal.SIGTERM)
        exit_code = self.wait(grace_s)
        if exit_code is not None:
            return exit_code
        self._signal_group(signal.SIGKILL)
        return self.process.wait()

    def _signal_group(self, signum: signal.Signals) -> None:
        # The group may already be gone: the child exited between wait and signal.
        with suppress(ProcessLookupError):
            os.killpg(self.process.pid, signum)


def start_child(
    argv: Sequence[str], *, cwd: Path, env: Mapping[str, str], log_path: Path
) -> RunningChild:
    """Start ``argv`` in ``cwd`` with its output appended to ``log_path``; ``OSError`` if it cannot."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        process = subprocess.Popen(  # argv built from the kind table, never a shell
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    return RunningChild(process=process)
