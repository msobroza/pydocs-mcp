"""The child-process starter: real processes, real pipes, the redacted log (spec AC-R7 to AC-R12).

``start_child`` reads the process's one output pipe on a thread of its own,
redacts every known provider key value, and appends the result to a log it
creates new. These tests cover what the supervisor seam cannot isolate:
memory, promptness, order, the drain bound and a log that refuses writes.
Every value is a synthetic FAKE one.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

import pydocs_eval
from pydocs_eval.bench_vm.child_process import OUTPUT_DRAIN_BOUND_S, RunningChild, start_child
from pydocs_eval.bench_vm import child_process
from pydocs_eval.bench_vm.output_pump import OUTPUT_READ_SIZE, OutputPump

from ._fixture_output_script import HOLDER_EARLY, Op, raw_output
from ._redaction_oracle import oracle_redact

_SCRIPT = Path(__file__).with_name("_fixture_output_script.py")
_DRIVER = Path(__file__).with_name("_fixture_start_child_driver.py")
_KEYS = {
    "ANTHROPIC_API_KEY": "sk-ant-FAKE-6c0e2b94-pump-redacts-me",
    "OPENROUTER_API_KEY": "sk-or-FAKE-1d7f3a58-pump-redacts-me",
}
_A, _B = "<ANTHROPIC_API_KEY>", "<OPENROUTER_API_KEY>"
# The pump's own traced memory: one read, the hold-back, the redacted copy.
_MEMORY_CEILING = 16 * OUTPUT_READ_SIZE


def _start(
    tmp_path: Path, script: Sequence[Op], keys: Mapping[str, str] = _KEYS
) -> tuple[RunningChild, Path]:
    log = tmp_path / "logs" / "child-attempt-1.log"
    child = start_child(
        [sys.executable, str(_SCRIPT), json.dumps(script)],
        cwd=tmp_path,
        env={**os.environ, **keys},
        log_path=log,
        provider_keys=keys,
    )
    return child, log


def _eventually(check: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.02)
    return check()


def _events(err: str, name: str) -> list[dict[str, object]]:
    lines = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    return [line for line in lines if line.get("event") == name]


# --- AC-R7: bounded memory, linear time --------------------------------------------------


def _one_line_of(size: int) -> tuple[list[Op], bytes]:
    """``size`` bytes with no newline: values at known offsets, one split across writes."""
    quarter = size // 4
    script: list[Op] = [
        ["fill", quarter],
        ["key", "ANTHROPIC_API_KEY", 0, 9],
        ["key", "ANTHROPIC_API_KEY", 9, None],
        ["fill", quarter],
        ["key", "OPENROUTER_API_KEY", 0, None],
        ["fill", quarter],
        ["key", "ANTHROPIC_API_KEY", 0, 30],
        ["key", "ANTHROPIC_API_KEY", 30, None],
        ["fill", quarter],
    ]
    expected = b"x" * quarter + f"{_A}".encode() + b"x" * quarter + f"{_B}".encode()
    return script, expected + b"x" * quarter + f"{_A}".encode() + b"x" * quarter


@pytest.mark.parametrize("mebibytes", [8, 64])
def test_a_huge_line_with_no_newline_is_redacted_in_bounded_memory(
    tmp_path: Path, mebibytes: int
) -> None:
    script, expected = _one_line_of(mebibytes << 20)
    started = time.monotonic()
    tracemalloc.start()
    try:
        child, log = _start(tmp_path, script)
        tracemalloc.reset_peak()
        exit_code = child.wait(120.0)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert exit_code == 0
    assert peak < _MEMORY_CEILING, f"peak {peak} bytes for {mebibytes} MiB"
    # A per-byte Python loop would not get through 64 MiB in this time.
    assert time.monotonic() - started < 60.0
    assert log.read_bytes() == expected


# --- AC-R8: prompt, and nothing early ------------------------------------------------------


def test_a_line_that_cannot_begin_a_value_reaches_the_log_at_once(tmp_path: Path) -> None:
    line = b"step 1/3 done, nothing secret here\n"
    child, log = _start(tmp_path, [["out", line.decode()], ["sleep", 3]])

    assert _eventually(lambda: log.read_bytes() == line, timeout_s=2.0)
    assert child.wait(10.0) == 0


def test_the_first_half_of_a_value_stays_out_of_the_log_during_a_pause(tmp_path: Path) -> None:
    script: list[Op] = [
        ["out", "text before the half "],
        ["key", "ANTHROPIC_API_KEY", 0, 14],
        ["sleep", 2],
        ["key", "ANTHROPIC_API_KEY", 14, None],
        ["out", "\n"],
    ]
    child, log = _start(tmp_path, script)

    assert _eventually(lambda: log.read_bytes() == b"text before the half ", timeout_s=1.5)
    time.sleep(0.3)
    assert log.read_bytes() == b"text before the half "
    assert child.wait(10.0) == 0
    assert log.read_bytes() == f"text before the half {_A}\n".encode()


# --- AC-R9: order ---------------------------------------------------------------------------


@pytest.mark.parametrize("keys", [{}, _KEYS], ids=["keyless", "keyed"])
def test_alternating_stdout_and_stderr_writes_reach_the_log_in_write_order(
    tmp_path: Path, keys: dict[str, str]
) -> None:
    script: list[Op] = []
    for index in range(20):
        script += [["out", f"out {index}\n"], ["err", f"err {index}\n"]]
    child, log = _start(tmp_path, script, keys)

    assert child.wait(10.0) == 0
    assert log.read_bytes() == raw_output(script, {})


def test_a_keyed_stream_keeps_every_other_byte_as_written(tmp_path: Path) -> None:
    script: list[Op] = [
        ["out_bytes", "\xff\xfe \x00 \r\n \xc3\xa9 "],
        ["err_key", "OPENROUTER_API_KEY"],
        ["key", "ANTHROPIC_API_KEY", 0, None],
        ["key", "ANTHROPIC_API_KEY", 0, None],
        ["out", "sk-ant-FAKE no newline"],
    ]
    child, log = _start(tmp_path, script)

    assert child.wait(10.0) == 0
    assert log.read_bytes() == oracle_redact(raw_output(script, _KEYS), _KEYS)
    assert log.read_bytes().count(_A.encode()) == 2


# --- AC-R10: never stalled -----------------------------------------------------------------


def test_a_child_writing_far_more_than_a_pipe_buffer_finishes_while_the_caller_waits_not(
    tmp_path: Path,
) -> None:
    done = tmp_path / "writes-finished"
    child, log = _start(tmp_path, [["fill", 4 << 20], ["touch", str(done)], ["sleep", 1]])

    # The caller does nothing with the child: the pump alone drains the pipe.
    assert _eventually(done.exists, timeout_s=5.0)
    assert child.wait(10.0) == 0
    assert log.stat().st_size == 4 << 20


# --- AC-R11: the drain bound ---------------------------------------------------------------


def test_a_descendant_holding_the_pipe_is_cut_off_at_the_drain_bound(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = tmp_path / "holder.txt"
    # The descendant writes again well after the bound has cut it off.
    late_s = OUTPUT_DRAIN_BOUND_S + 2.0
    script: list[Op] = [["out", "child before exit\n"], ["holder", str(record), late_s]]
    started = time.monotonic()
    child, log = _start(tmp_path, script)

    exit_code = child.wait(30.0)

    assert exit_code == 0
    assert time.monotonic() - started < OUTPUT_DRAIN_BOUND_S + 2.0
    assert log.read_bytes() == b"child before exit\n" + HOLDER_EARLY
    cut_off = _events(capsys.readouterr().err, "bench_vm_output_cut_off")
    assert [event["log"] for event in cut_off] == [str(log)]
    assert _eventually(record.exists, timeout_s=OUTPUT_DRAIN_BOUND_S + 5.0)
    assert record.read_text(encoding="utf-8") == "broken pipe\n"
    assert log.read_bytes() == b"child before exit\n" + HOLDER_EARLY


# --- AC-R12: a failed write ----------------------------------------------------------------


def _drive(mode: str, log: Path, script: list[Op]) -> subprocess.CompletedProcess[str]:
    """``start_child`` in a driver process under ``mode`` (``_fixture_start_child_driver``)."""
    log.parent.mkdir(parents=True, exist_ok=True)
    src = str(Path(pydocs_eval.__file__).resolve().parents[1])
    return subprocess.run(
        [sys.executable, str(_DRIVER), mode, str(log), json.dumps(script)],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONPATH": src},
        check=False,
    )


def test_a_log_that_refuses_writes_discards_the_output_and_the_child_runs_on(
    tmp_path: Path,
) -> None:
    log = tmp_path / "logs" / "child-attempt-1.log"

    driven = _drive("full-disk", log, [["fill", 2 << 20], ["out", "the end\n"], ["exit", 7]])

    assert driven.returncode == 0, driven.stderr
    assert json.loads(driven.stdout) == {"exit_code": 7}
    discarded = _events(driven.stderr, "bench_vm_output_discarded")
    assert len(discarded) == 1
    assert discarded[0]["log"] == str(log)
    assert discarded[0]["discarded_bytes"] == (2 << 20) + len("the end\n") - log.stat().st_size
    assert "xxxx" not in driven.stderr  # no output byte in the event


def test_a_pipe_descriptor_above_select_s_limit_is_still_pumped(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "child-attempt-1.log"

    driven = _drive("many-fds", log, [["fill", 1 << 20], ["out", "the end\n"]])

    assert driven.returncode == 0, driven.stderr
    assert json.loads(driven.stdout) == {"exit_code": 0}
    assert log.read_bytes() == b"x" * (1 << 20) + b"the end\n"


# --- a pump that cannot start ---------------------------------------------------------------


class _UnstartablePump(OutputPump):
    """An ``OutputPump`` whose thread cannot start, as when the system has no thread left."""

    def start(self) -> None:
        raise RuntimeError("can't start new thread (scripted)")


def test_a_pump_that_cannot_start_leaves_no_process_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(child_process, "OutputPump", _UnstartablePump)
    survived = tmp_path / "survived"

    with pytest.raises(RuntimeError, match="can't start new thread"):
        _start(tmp_path, [["sleep", 1.0], ["touch", str(survived)]])

    time.sleep(1.5)
    assert not survived.exists()


# --- the log is created new --------------------------------------------------------------


def test_a_log_that_already_exists_is_refused_before_the_process_starts(tmp_path: Path) -> None:
    log = tmp_path / "logs" / "child-attempt-1.log"
    log.parent.mkdir(parents=True)
    log.write_bytes(b"bytes from somewhere else\n")
    started = tmp_path / "started"

    with pytest.raises(FileExistsError, match="child-attempt-1.log"):
        _start(tmp_path, [["touch", str(started)]])

    time.sleep(0.2)
    assert not started.exists()
    assert log.read_bytes() == b"bytes from somewhere else\n"
