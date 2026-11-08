"""A careless CLI's output, scripted: what a fixture process writes, in which pieces, and when.

Run as a script (``python _fixture_output_script.py '<json script>'``) it plays
the script and exits with its ``exit`` op's code (0 by default). The fixture
child (``_fixture_child``) plays one from its environment the same way. A
script is a JSON list of ops:

- ``["out", text]`` / ``["err", text]``: the text's UTF-8 bytes on stdout /
  stderr, flushed;
- ``["out_bytes", text]``: the text's Latin-1 bytes on stdout (any byte value);
- ``["key", NAME, start, end]``: bytes ``[start:end]`` of the environment's
  ``NAME`` value on stdout (``end`` null: to the end); ``["err_key", NAME]``:
  the whole value on stderr;
- ``["fill", n]``: ``n`` bytes of ``x`` on stdout, in writes of at most 1 MiB;
- ``["sleep", s]``, ``["touch", path]``, ``["exit", code]``;
- ``["holder", record_path, delay_s]``: start a descendant in a session of its
  own that inherits the output pipe, writes ``HOLDER_EARLY`` at once, sleeps
  ``delay_s``, then tries ``HOLDER_LATE`` and appends ``broken pipe`` or
  ``written`` to ``record_path``; ``["holder", record_path, delay_s, NAME, end]``
  writes the first ``end`` bytes of ``NAME``'s value right after
  ``HOLDER_EARLY``, so the drain bound cuts the stream off inside a value;
- ``["signal_parent"]``: SIGTERM the parent (the supervisor), as ``docker stop`` does.

``raw_output`` says what a script writes, so a test can compute the log it
expects. Every value it handles is a synthetic one from the environment.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

HOLDER_EARLY = b"holder early\n"
HOLDER_LATE = b"holder late\n"
_FILL_WRITE = 1 << 20

Op = Sequence[Any]


def raw_output(script: Sequence[Op], env: Mapping[str, str]) -> bytes:
    """The bytes the script writes to stdout and stderr, in order (a holder's excluded)."""
    pieces = [_bytes_of(op, env) for op in script]
    return b"".join(piece for piece in pieces if piece is not None)


def _bytes_of(op: Op, env: Mapping[str, str]) -> bytes | None:
    kind = op[0]
    if kind in ("out", "err"):
        return str(op[1]).encode("utf-8")
    if kind == "out_bytes":
        return str(op[1]).encode("latin-1")
    if kind == "key":
        return env[op[1]].encode("utf-8")[op[2] : op[3]]
    if kind == "err_key":
        return env[op[1]].encode("utf-8")
    if kind == "fill":
        return b"x" * int(op[1])
    return None


def _write(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def _fill(size: int) -> None:
    for start in range(0, size, _FILL_WRITE):
        _write(1, b"x" * min(_FILL_WRITE, size - start))


def _start_holder(record_path: str, delay_s: float, name: str = "", end: int = 0) -> None:
    subprocess.Popen(
        [sys.executable, __file__, "--holder", record_path, str(delay_s), name, str(end)],
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def _hold_the_pipe(record_path: str, delay_s: float, name: str, end: int) -> int:
    _write(1, HOLDER_EARLY + (os.environ[name].encode("utf-8")[:end] if name else b""))
    time.sleep(delay_s)
    try:
        _write(1, HOLDER_LATE)
        outcome = "written"
    except BrokenPipeError:
        outcome = "broken pipe"
    with Path(record_path).open("a", encoding="utf-8") as record:
        record.write(outcome + "\n")
    return 0


def _actions() -> dict[str, Callable[..., None]]:
    env = os.environ
    return {
        "out": lambda text: _write(1, text.encode("utf-8")),
        "err": lambda text: _write(2, text.encode("utf-8")),
        "out_bytes": lambda text: _write(1, text.encode("latin-1")),
        "key": lambda name, start, end: _write(1, env[name].encode("utf-8")[start:end]),
        "err_key": lambda name: _write(2, env[name].encode("utf-8")),
        "fill": lambda size: _fill(int(size)),
        "sleep": lambda seconds: time.sleep(float(seconds)),
        "touch": lambda path: Path(path).touch(),
        "holder": lambda path, delay, *partial: _start_holder(path, float(delay), *partial),
        "signal_parent": lambda: os.kill(os.getppid(), signal.SIGTERM),
    }


def play(script: Sequence[Op], extra: Mapping[str, Callable[..., None]] | None = None) -> int:
    """Play ``script``; returns its ``exit`` op's code, 0 without one."""
    actions = {**_actions(), **(extra or {})}
    for op in script:
        if op[0] == "exit":
            return int(op[1])
        actions[op[0]](*op[1:])
    return 0


if __name__ == "__main__":
    if sys.argv[1] == "--holder":
        sys.exit(_hold_the_pipe(sys.argv[2], float(sys.argv[3]), sys.argv[4], int(sys.argv[5])))
    sys.exit(play(json.loads(sys.argv[1])))
