"""The child process of the supervisor tests: a real process standing in for a kind's CLI.

The supervisor runs it in place of the image's ``python``, so it receives the
kind's argv after the interpreter (``-m pydocs_eval.runner --configs …``). It:

- writes what it was started with (argv, the bench environment, the working
  directory) as JSON to ``$FIXTURE_RECORD``;
- writes a few lines to stdout and stderr, bytes the log must keep verbatim;
- appends ``$FIXTURE_TICKS`` lines to ``fixture-ledger.jsonl`` in the state
  directory, one every ``$FIXTURE_TICK_S`` seconds, beside a ``.tmp`` file the
  sync must skip;
- on SIGTERM appends ``stopped`` to the record's ``.signals`` file and exits
  143, unless ``$FIXTURE_IGNORE_SIGTERM`` is ``1``;
- otherwise exits ``$FIXTURE_EXIT_CODE``.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType

VERBATIM_STDOUT = b"fixture stdout \xc3\xa9\r\nno newline at the end"
VERBATIM_STDERR = b"fixture stderr line\n"
LEDGER_NAME = "fixture-ledger.jsonl"
TEMPORARY_NAME = "report.md.tmp"
BENCH_ENV_PREFIX = "BENCH_"


def _on_sigterm(_signum: int, _frame: FrameType | None) -> None:
    with Path(os.environ["FIXTURE_RECORD"] + ".signals").open("a", encoding="utf-8") as signals:
        signals.write("stopped\n")
    sys.exit(143)


def _record_start() -> None:
    started = {
        "argv": sys.argv[1:],
        "env": {k: v for k, v in os.environ.items() if k.startswith(BENCH_ENV_PREFIX)},
        "pythonunbuffered": os.environ.get("PYTHONUNBUFFERED"),
        "cwd": str(Path.cwd()),
    }
    Path(os.environ["FIXTURE_RECORD"]).write_text(json.dumps(started), encoding="utf-8")


def _write_output() -> None:
    sys.stderr.buffer.write(VERBATIM_STDERR)
    sys.stderr.flush()
    sys.stdout.buffer.write(VERBATIM_STDOUT)
    sys.stdout.flush()


def _tick(state_dir: Path) -> None:
    (state_dir / TEMPORARY_NAME).write_text("half-written", encoding="utf-8")
    ticks = int(os.environ.get("FIXTURE_TICKS", "0"))
    tick_s = float(os.environ.get("FIXTURE_TICK_S", "0"))
    for index in range(ticks):
        with (state_dir / LEDGER_NAME).open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps({"task": index}) + "\n")
        time.sleep(tick_s)


def main() -> int:
    ignore = os.environ.get("FIXTURE_IGNORE_SIGTERM") == "1"
    signal.signal(signal.SIGTERM, signal.SIG_IGN if ignore else _on_sigterm)
    _record_start()
    _write_output()
    _tick(Path(os.environ["BENCH_STATE_DIR"]))
    return int(os.environ.get("FIXTURE_EXIT_CODE", "0"))


if __name__ == "__main__":
    sys.exit(main())
