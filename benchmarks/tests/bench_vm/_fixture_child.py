"""The child process of the supervisor tests: a real process standing in for a kind's CLI.

The supervisor runs it in place of the image's ``python``, so it receives the
kind's argv after the interpreter (``-m pydocs_eval.runner --configs …``). It:

- appends what it was started with (argv, the bench environment, the
  provider keys it received, the cache environment, the working directory,
  every file already in the cache directory) as one JSON line to
  ``$FIXTURE_RECORD.runs.jsonl``, pre-flight or not;
- when started as a kind's pre-flight (``--preflight``, or before/after
  without ``--confirm-spend``), prints ``PLAN_LINE``, sleeps
  ``$FIXTURE_PREFLIGHT_SLEEP_S`` and exits ``$FIXTURE_PREFLIGHT_EXIT_CODE``;
  with ``$FIXTURE_PREFLIGHT_ECHO_KEYS`` set to ``1`` it first prints each
  provider key it received followed by ``$FIXTURE_PREFLIGHT_PAD`` dots, the way
  a careless CLI might echo its key into its output; with
  ``$FIXTURE_PREFLIGHT_OUTPUT_SCRIPT`` set it plays that output script
  (``_fixture_output_script``) after ``PLAN_LINE`` instead;
- otherwise reads ``fixture-ledger.jsonl`` in the state directory the way the
  kinds read their ledgers (one record per line, a truncated last line
  dropped) and writes the same JSON, plus the task ids already recorded, to
  ``$FIXTURE_RECORD``;
- writes a few lines to stdout and stderr, bytes the log must keep verbatim,
  or plays ``$FIXTURE_OUTPUT_SCRIPT`` instead when it is set;
- in either script, ``["scan", record, log_kind, n]`` waits (at most
  ``SCAN_WAIT_S``) until the process's own log (``child`` or ``preflight``)
  holds ``n`` placeholders, then scans every file under the labelled roots of
  ``$FIXTURE_SCAN_ROOTS`` (``label=path`` entries, ``os.pathsep``-separated)
  for each provider key value it holds, and appends ``{"waited", "found"}``
  (places, never values) to ``record``; ``["observe", record, log_kind]``
  appends the log's bytes and those of its synced copy under
  ``$FIXTURE_SYNCED_STATE_DIR`` (base64, ``null`` when absent);
- writes into the caches what ``$FIXTURE_CACHES`` names
  (``_fixture_cache_producer``);
- writes ``fixture.lock.json``, a manifest a resume would check;
- appends one ledger line per task id below ``$FIXTURE_TICKS`` that is not
  recorded yet, one every ``$FIXTURE_TICK_S`` seconds, beside a ``.tmp`` file
  the sync must skip;
- after ``$FIXTURE_SIGNAL_PARENT_AFTER`` new lines (0: never), sends its parent,
  the supervisor, SIGTERM, as the host shutdown script's ``docker stop`` does,
  appends ``sigterm`` to the record's ``.parent-signals`` file, and keeps
  appending until it is stopped;
- on SIGTERM appends ``stopped`` to the record's ``.signals`` file and exits
  143, unless ``$FIXTURE_IGNORE_SIGTERM`` is ``1``;
- otherwise exits ``$FIXTURE_EXIT_CODE``.
"""

from __future__ import annotations

import base64
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
# A JSON file a resume checks, as the campaign's campaign.lock.json.
MANIFEST_NAME = "fixture.lock.json"
TEMPORARY_NAME = "report.md.tmp"
BENCH_ENV_PREFIX = "BENCH_"
PROVIDER_KEY_SUFFIX = "_API_KEY"
CACHE_ENV_NAMES = ("HOME", "HF_HOME", "FASTEMBED_CACHE_PATH")
PLAN_LINE = "fixture plan: 2 arms, 3 tasks, estimated $0.42"
SCAN_WAIT_S = 10.0


def _on_sigterm(_signum: int, _frame: FrameType | None) -> None:
    with Path(os.environ["FIXTURE_RECORD"] + ".signals").open("a", encoding="utf-8") as signals:
        signals.write("stopped\n")
    sys.exit(143)


def _note_parent_signalled() -> None:
    path = Path(os.environ["FIXTURE_RECORD"] + ".parent-signals")
    with path.open("a", encoding="utf-8") as sent:
        sent.write("sigterm\n")


def recorded_task_ids(ledger: Path) -> list[int]:
    """The task ids of the complete lines; a truncated last line is cut off the file."""
    if not ledger.exists():
        return []
    data = ledger.read_bytes()
    complete = data[: data.rfind(b"\n") + 1]
    if complete != data:
        ledger.write_bytes(complete)
    return [json.loads(line)["task"] for line in complete.splitlines()]


def _files_under(root: Path) -> list[str]:
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _started() -> dict[str, object]:
    return {
        "argv": sys.argv[1:],
        "env": {k: v for k, v in os.environ.items() if k.startswith(BENCH_ENV_PREFIX)},
        "provider_keys": {k: v for k, v in os.environ.items() if k.endswith(PROVIDER_KEY_SUFFIX)},
        "cache_env": {name: os.environ.get(name) for name in CACHE_ENV_NAMES},
        "pythonunbuffered": os.environ.get("PYTHONUNBUFFERED"),
        "cwd": str(Path.cwd()),
        "cache_files": _files_under(Path(os.environ["BENCH_CACHE_DIR"])),
    }


def _is_preflight(argv: list[str]) -> bool:
    return "--preflight" in argv or ("before-after" in argv and "--confirm-spend" not in argv)


def _own_log(log_kind: str) -> Path:
    attempt = os.environ["BENCH_ATTEMPT"]
    return Path(os.environ["BENCH_STATE_DIR"]) / "logs" / f"{log_kind}-attempt-{attempt}.log"


def _wait_for_placeholders(log: Path, count: int) -> bool:
    deadline = time.monotonic() + SCAN_WAIT_S
    while time.monotonic() < deadline:
        if log.exists() and log.read_bytes().count(PROVIDER_KEY_SUFFIX.encode() + b">") >= count:
            return True
        time.sleep(0.02)
    return False


def _scan_roots() -> dict[str, Path]:
    entries = os.environ.get("FIXTURE_SCAN_ROOTS", "").split(os.pathsep)
    return {label: Path(path) for label, _, path in (e.partition("=") for e in entries if e)}


def _scan_while_alive(record: str, log_kind: str, count: str) -> None:
    # Run as a script, this directory is on sys.path (the tests import this module too).
    from _leak_scan import places_holding

    waited = _wait_for_placeholders(_own_log(log_kind), int(count))
    values = [v for k, v in os.environ.items() if k.endswith(PROVIDER_KEY_SUFFIX)]
    found = [p for v in values for p in places_holding(_scan_roots(), v.encode("utf-8"))]
    _append_json(record, {"waited": waited, "found": found})


def _observe_log(record: str, log_kind: str) -> None:
    local = _own_log(log_kind)
    synced = Path(os.environ["FIXTURE_SYNCED_STATE_DIR"]) / "logs" / local.name
    encoded = {
        name: base64.b64encode(path.read_bytes()).decode() if path.exists() else None
        for name, path in (("local", local), ("synced", synced))
    }
    _append_json(record, encoded)


def _append_json(record: str, payload: dict[str, object]) -> None:
    with Path(record).open("a", encoding="utf-8") as out:
        out.write(json.dumps(payload) + "\n")


def _play_from(variable: str) -> int:
    from _fixture_output_script import play

    script = json.loads(os.environ[variable])
    return play(script, {"scan": _scan_while_alive, "observe": _observe_log})


def _run_preflight() -> int:
    print(PLAN_LINE, flush=True)
    if os.environ.get("FIXTURE_PREFLIGHT_OUTPUT_SCRIPT"):
        _play_from("FIXTURE_PREFLIGHT_OUTPUT_SCRIPT")
    elif os.environ.get("FIXTURE_PREFLIGHT_ECHO_KEYS") == "1":
        pad = "." * int(os.environ.get("FIXTURE_PREFLIGHT_PAD", "0"))
        for name, value in os.environ.items():
            if name.endswith(PROVIDER_KEY_SUFFIX):
                print(f"{value}{pad}", flush=True)
    time.sleep(float(os.environ.get("FIXTURE_PREFLIGHT_SLEEP_S", "0")))
    return int(os.environ.get("FIXTURE_PREFLIGHT_EXIT_CODE", "0"))


def _write_output() -> None:
    sys.stderr.buffer.write(VERBATIM_STDERR)
    sys.stderr.flush()
    sys.stdout.buffer.write(VERBATIM_STDOUT)
    sys.stdout.flush()


def _tick(state_dir: Path, recorded: list[int]) -> None:
    (state_dir / TEMPORARY_NAME).write_text("half-written", encoding="utf-8")
    (state_dir / MANIFEST_NAME).write_text('{"fixture": true}', encoding="utf-8")
    ticks = int(os.environ.get("FIXTURE_TICKS", "0"))
    tick_s = float(os.environ.get("FIXTURE_TICK_S", "0"))
    signal_after = int(os.environ.get("FIXTURE_SIGNAL_PARENT_AFTER", "0"))
    pending = (i for i in range(ticks) if i not in recorded)
    for written, index in enumerate(pending, start=1):
        with (state_dir / LEDGER_NAME).open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps({"task": index}) + "\n")
        if written == signal_after:
            os.kill(os.getppid(), signal.SIGTERM)
            _note_parent_signalled()
        time.sleep(tick_s)


def _produce_caches() -> None:
    # Imported only when asked: the producer pulls in the eval suite, whose
    # import time a child that writes no cache must not pay. Run as a script,
    # this directory is on sys.path; the eval suite of THIS checkout goes
    # first, whatever the interpreter's own install points at.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from _fixture_cache_producer import produce_caches

    produce_caches()


def main() -> int:
    ignore = os.environ.get("FIXTURE_IGNORE_SIGTERM") == "1"
    signal.signal(signal.SIGTERM, signal.SIG_IGN if ignore else _on_sigterm)
    started = _started()
    with Path(os.environ["FIXTURE_RECORD"] + ".runs.jsonl").open("a", encoding="utf-8") as runs:
        runs.write(json.dumps(started) + "\n")
    if _is_preflight(sys.argv[1:]):
        return _run_preflight()
    # Only the kind's real run reads (and may repair) the ledger, never its pre-flight.
    state_dir = Path(os.environ["BENCH_STATE_DIR"])
    recorded = recorded_task_ids(state_dir / LEDGER_NAME)
    record = json.dumps({**started, "recorded_at_start": recorded})
    Path(os.environ["FIXTURE_RECORD"]).write_text(record, encoding="utf-8")
    if os.environ.get("FIXTURE_OUTPUT_SCRIPT"):
        _play_from("FIXTURE_OUTPUT_SCRIPT")
    else:
        _write_output()
    if os.environ.get("FIXTURE_CACHES"):
        _produce_caches()
    _tick(state_dir, recorded)
    return int(os.environ.get("FIXTURE_EXIT_CODE", "0"))


if __name__ == "__main__":
    sys.exit(main())
