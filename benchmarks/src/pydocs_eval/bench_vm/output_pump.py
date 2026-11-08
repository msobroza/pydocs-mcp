"""The output pump: one process's output pipe, redacted, appended to its log, on a thread of its own.

ADR 0026, amendment 2026-11-08. The pump reads the pipe in fixed-size chunks,
feeds each to the stream's ``ProviderKeyRedactor`` and appends what it returns
to the log with a direct, unbuffered write, so at every sync the file holds
everything but the redactor's hold-back. No raw byte reaches any file. It
never splits the stream into lines; its memory is one read, the hold-back and
the tail it keeps for the caller to log (``tail_bytes``, 0 for the child).

Running beside the supervisor's main loop, it never leaves the pipe unread
while a sync takes minutes. At the end, the caller gives it a drain bound
(``finish_within``): past it, the pump writes what is already in the pipe,
the redactor's rest, closes the log and its end of the pipe, and logs
``bench_vm_output_cut_off``; a descendant still holding the pipe then gets a
broken pipe on its next write. A write the log refuses (a full disk) makes the
pump discard the rest of the stream, still reading so the benchmark never
blocks, and log one ``bench_vm_output_discarded`` event with the byte count.
Discarded bytes are written nowhere else.
"""

from __future__ import annotations

import os
import select
import threading
from pathlib import Path
from typing import IO

from pydocs_eval.bench_vm.provider_key_redactor import ProviderKeyRedactor
from pydocs_eval.bench_vm.supervisor_events import log_supervisor_event

# One pipe read; a pipe buffer on Linux.
OUTPUT_READ_SIZE = 1 << 16
# How often the pump looks at its stop flag while the pipe is quiet.
_POLL_S = 0.1
# Past the drain bound, what is already in the pipe still lands: a writer
# blocks on a full pipe, so that is at most a pipe buffer, bounded here
# against one that keeps writing.
_FINAL_DRAIN_MAX_BYTES = 16 * OUTPUT_READ_SIZE


class OutputPump:
    """Pump ``pipe`` through ``redactor`` into the log open at ``log_fd``; owns and closes both."""

    def __init__(
        self,
        pipe: IO[bytes],
        log_fd: int,
        log_path: Path,
        redactor: ProviderKeyRedactor,
        tail_bytes: int,
    ) -> None:
        self._pipe, self._log_fd, self._log_path = pipe, log_fd, log_path
        self._redactor, self._tail_bytes = redactor, tail_bytes
        self._tail = bytearray()
        self._discarded = 0
        self._write_error: str | None = None
        self._stop = threading.Event()
        # poll, not select: select refuses a descriptor above FD_SETSIZE (1024).
        self._readable = select.poll()
        self._readable.register(pipe.fileno(), select.POLLIN)
        self._thread = threading.Thread(
            target=self._run, name=f"output-pump-{log_path.name}", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def finish_within(self, drain_bound_s: float) -> None:
        """Return once the log is closed: at the pipe's end, or ``drain_bound_s`` from now."""
        self._thread.join(drain_bound_s)
        if self._thread.is_alive():
            self._stop.set()
            self._thread.join()

    @property
    def tail(self) -> bytes:
        """The last ``tail_bytes`` bytes written to the log: redacted, read once it is closed."""
        return bytes(self._tail)

    def _run(self) -> None:
        try:
            reached_end = self._pump_until_end_or_stop() or self._drain_what_is_ready()
            # Cut off or not, this is the stream's end: a held partial value is
            # written as it was (spec "Out of Scope": a value cut off by the end
            # of the stream). Its rest can never follow: the pipe closes below,
            # so a descendant's next write fails with a broken pipe.
            self._write(self._redactor.finish())
        finally:
            self._pipe.close()
            os.close(self._log_fd)
        self._log_what_was_lost(reached_end)

    def _pump_until_end_or_stop(self) -> bool:
        """``True`` at the pipe's end, ``False`` once stopped first."""
        fd = self._pipe.fileno()
        while not self._stop.is_set():
            if self._readable.poll(_POLL_S * 1000) and not self._pump_one_read(fd):
                return True
        return False

    def _drain_what_is_ready(self) -> bool:
        fd, drained = self._pipe.fileno(), 0
        while drained < _FINAL_DRAIN_MAX_BYTES and self._readable.poll(0):
            if not self._pump_one_read(fd):
                return True
            drained += OUTPUT_READ_SIZE
        return False

    def _pump_one_read(self, fd: int) -> bool:
        chunk = os.read(fd, OUTPUT_READ_SIZE)
        if not chunk:
            return False
        self._write(self._redactor.feed(chunk))
        return True

    def _write(self, data: bytes) -> None:
        view = memoryview(data)
        try:
            while view and self._write_error is None:
                written = os.write(self._log_fd, view)
                self._keep_tail(view[:written])
                view = view[written:]
        except OSError as exc:
            self._write_error = exc.strerror or type(exc).__name__
        self._discarded += len(view)

    def _keep_tail(self, written: memoryview) -> None:
        if not self._tail_bytes:
            return
        self._tail += written
        del self._tail[: max(0, len(self._tail) - self._tail_bytes)]

    def _log_what_was_lost(self, reached_end: bool) -> None:
        if not reached_end:
            log_supervisor_event("bench_vm_output_cut_off", log=self._log_path)
        if self._write_error is not None:
            log_supervisor_event(
                "bench_vm_output_discarded",
                log=self._log_path,
                discarded_bytes=self._discarded,
                error=self._write_error,
            )
