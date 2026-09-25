"""A bounded ``git`` run in a session of its own — the two network calls (#318).

``subprocess.run(timeout=…)`` kills only its direct child. ``git ls-remote``
and ``git fetch`` hand the transport to children of their own (``ssh``,
``git-remote-https``), which outlive that kill; the remote lane retries every
interval, so a hung remote would pile them up. Here the child leads a new
session: a timeout — or any failure while waiting — kills its whole process
group. The new session also leaves the transport no controlling terminal, so
``ssh`` fails instead of prompting on the user's tty (``GIT_TERMINAL_PROMPT=0``
covers git's own prompts only).

Where process groups do not exist (Windows), the direct child alone is killed,
``subprocess.run``'s behavior.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
from collections.abc import Mapping, Sequence


def run_in_own_process_group(
    argv: Sequence[str], *, stdin: bytes | None, timeout: float, env: Mapping[str, str]
) -> subprocess.CompletedProcess[bytes]:
    """``subprocess.run(argv, input=stdin, capture_output=True, timeout=timeout)``,
    except that a failure kills git's descendants too. Raises what
    ``subprocess.run`` raises (``TimeoutExpired``, ``OSError`` at start)."""
    # S603: the caller's argv, no shell — SubprocessGitRepository's guards apply.
    with subprocess.Popen(  # noqa: S603 — see the comment above
        argv,
        stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(env),
        start_new_session=True,
    ) as proc:
        try:
            stdout, stderr = proc.communicate(stdin, timeout=timeout)
        except BaseException:
            _kill_process_group(proc)
            raise
    return subprocess.CompletedProcess(list(argv), proc.returncode, stdout, stderr)


def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
    """SIGKILL the session ``proc`` leads, then reap it and drain its pipes."""
    killpg = getattr(os, "killpg", None)
    if killpg is None:
        proc.kill()
    else:
        # The group outlives git's own exit while a transport child holds it.
        with contextlib.suppress(ProcessLookupError, PermissionError):
            killpg(proc.pid, signal.SIGKILL)
    proc.communicate()


__all__ = ("run_in_own_process_group",)
