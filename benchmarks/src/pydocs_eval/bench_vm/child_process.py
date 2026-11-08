"""A process the supervisor runs for the benchmark: started with its output redacted, waited on, stopped.

The kind's CLI (the child) and the kind's own pre-flight both run here. Each
runs in its own session, so a stop reaches its whole process group (a
runner's or campaign's own subprocesses included), not just the direct
process.

Its stdout and stderr share one pipe, stderr joined to stdout, so the log
keeps the order in which the kernel received the writes. The supervisor reads
that pipe on a thread of its own (``output_pump``), masks every provider key
value the run's kind declares (``ProviderKeyRedactor``) and appends the rest,
byte for byte, to a log it creates new: no raw byte reaches any file (ADR
0026, amendment 2026-11-08). This replaced a log the process wrote directly,
which kept a key a CLI printed. The design answers why the output once
avoided the supervisor: the pump's own thread never blocks the process, one
pipe never reorders it, and bytes are never re-encoded.

A log that already exists is refused before the process starts: one attempt's
log never mixes with bytes this supervisor did not write. The exit code is
reported once the process has exited and its log is closed, or once
``OUTPUT_DRAIN_BOUND_S`` has passed with a descendant still holding the pipe,
which then loses its later output.
"""

from __future__ import annotations

import os
import signal
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.bench_vm.output_pump import OutputPump
from pydocs_eval.bench_vm.provider_key_redactor import ProviderKeyRedactor

# How long the output may still drain once the process has exited; within
# the shutdown budget (a spot VM's 30-second notice).
OUTPUT_DRAIN_BOUND_S = 5.0
_NEW_LOG_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND | os.O_CLOEXEC


@dataclass(frozen=True, slots=True)
class RunningChild:
    """A started process and the pump writing its redacted output to its log."""

    process: subprocess.Popen[bytes]
    pump: OutputPump

    def wait(self, timeout_s: float) -> int | None:
        """The exit code once the process has exited and its log is closed, ``None`` if it
        still runs after ``timeout_s``."""
        try:
            exit_code = self.process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            return None
        self.pump.finish_within(OUTPUT_DRAIN_BOUND_S)
        return exit_code

    def stop(self, grace_s: float) -> int:
        """SIGTERM the group, SIGKILL it after ``grace_s``, drain the log; returns the exit code."""
        self._signal_group(signal.SIGTERM)
        exit_code = self.wait(grace_s)
        if exit_code is not None:
            return exit_code
        self._signal_group(signal.SIGKILL)
        exit_code = self.process.wait()
        self.pump.finish_within(OUTPUT_DRAIN_BOUND_S)
        return exit_code

    def output_tail(self) -> bytes:
        """The last bytes written to the log, at most the ``tail_bytes`` asked at the start."""
        return self.pump.tail

    def _signal_group(self, signum: signal.Signals) -> None:
        # The group may already be gone: the process exited between wait and signal.
        with suppress(ProcessLookupError):
            os.killpg(self.process.pid, signum)


def start_child(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    log_path: Path,
    provider_keys: Mapping[str, str],
    tail_bytes: int = 0,
) -> RunningChild:
    """Start ``argv`` in ``cwd``, its output redacted of ``provider_keys`` into the new ``log_path``.

    ``FileExistsError`` (naming the log) when the log already exists, before
    the process starts; any other ``OSError`` if it cannot start. A
    ``ValueError`` naming the key for an empty value: the pre-flight refuses
    every unusable value before any process starts (``provider_secrets``).
    """
    redactor = ProviderKeyRedactor(provider_keys)  # a fresh one per stream, never primed
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(log_path, _NEW_LOG_FLAGS, 0o644)
    try:
        process = subprocess.Popen(  # argv built from the kind table, never a shell
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        os.close(log_fd)
        raise
    assert process.stdout is not None  # stdout=PIPE
    try:
        pump = OutputPump(process.stdout, log_fd, log_path, redactor, tail_bytes)
        pump.start()
    except BaseException:
        _abandon(process, log_fd)  # nobody would read its pipe or ever stop it
        raise
    return RunningChild(process=process, pump=pump)


def _abandon(process: subprocess.Popen[bytes], log_fd: int) -> None:
    """Kill and reap a process whose output has no pump, and close its pipe and log."""
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    process.wait()
    if process.stdout is not None:
        process.stdout.close()
    os.close(log_fd)
