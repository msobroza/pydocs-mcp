"""The two-process ``git <producer> | git <consumer>`` pipe of the patch-id reads (spec §6.2).

``git log -p`` over 200 landings streams about 21 MB on this repository. The
producer's stdout is handed to the consumer's stdin at the OS level, so Python
only ever holds the consumer's small output: the intermediate diff is never
buffered here, and never capped either (a diff cut mid-stream changes its patch
id silently). Both processes share ONE deadline. On a timeout, a failure to
start, or any other exception, both are killed and reaped before the error
leaves this module; exit statuses are left to the caller, which knows which
side's failure to report.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
from contextlib import ExitStack
from dataclasses import dataclass
from typing import IO, Any

from pydocs_mcp.git.env import patch_text_child_env
from pydocs_mcp.git.errors import translate_git_start_failures, translate_git_timeout

# What Popen accepts for a child's stdin / stderr: a file, a descriptor or a
# ``subprocess`` constant.
_Stream = int | IO[Any] | None


@dataclass(frozen=True, slots=True)
class PipeOutcome:
    """Both exit statuses and stderr streams, plus the consumer's stdout."""

    stdout: bytes
    producer_status: int
    producer_stderr: bytes
    consumer_status: int
    consumer_stderr: bytes


def run_git_pipe(
    producer: tuple[str, ...], consumer: tuple[str, ...], *, timeout: float
) -> PipeOutcome:
    """Run ``producer | consumer`` to completion inside ``timeout`` seconds overall."""
    env = patch_text_child_env()  # only patch-id text flows through this pipe
    with ExitStack() as cleanup:
        # A file, not a pipe: nobody drains the producer's stderr while the
        # consumer is read, and a full stderr pipe would stall the producer.
        producer_errors = cleanup.enter_context(tempfile.TemporaryFile())
        first = _start(cleanup, producer, env, stdin=subprocess.DEVNULL, stderr=producer_errors)
        second = _start(cleanup, consumer, env, stdin=first.stdout, stderr=subprocess.PIPE)
        _release_read_end(first.stdout)
        stdout, consumer_stderr = _wait_for_both(
            first, second, (*producer, "|", *consumer), timeout
        )
        producer_errors.seek(0)
        return PipeOutcome(
            stdout, first.returncode, producer_errors.read(), second.returncode, consumer_stderr
        )


def _start(
    cleanup: ExitStack,
    argv: tuple[str, ...],
    env: dict[str, str],
    *,
    stdin: _Stream,
    stderr: _Stream,
) -> subprocess.Popen[bytes]:
    """Spawn ``argv``; ``cleanup`` kills it (if still running) and reaps it on the way out."""
    # S603: no shell; the adapter refuses option-like caller values before building argv.
    with translate_git_start_failures(argv):
        proc = subprocess.Popen(  # noqa: S603 — see the comment above
            argv, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr, env=env
        )
    cleanup.enter_context(proc)  # closes its pipes and waits for it
    cleanup.callback(_kill_if_running, proc)  # LIFO: runs before that wait
    return proc


def _release_read_end(stream: IO[bytes] | None) -> None:
    """Drop the parent's copy of the pipe's read end.

    The consumer holds its own copy now; without this, a producer whose
    consumer quit early would block on a full pipe instead of seeing SIGPIPE.
    """
    if stream is not None:
        stream.close()


def _wait_for_both(
    first: subprocess.Popen[bytes],
    second: subprocess.Popen[bytes],
    pipeline: tuple[str, ...],
    timeout: float,
) -> tuple[bytes, bytes]:
    """The consumer's ``(stdout, stderr)``, once both processes exited inside one deadline."""
    deadline = time.monotonic() + timeout
    with translate_git_timeout(pipeline, timeout):
        stdout, stderr = second.communicate(timeout=timeout)
        first.wait(timeout=max(0.0, deadline - time.monotonic()))
    return stdout, stderr


def _kill_if_running(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is None:
        proc.kill()


__all__ = ("PipeOutcome", "run_git_pipe")
