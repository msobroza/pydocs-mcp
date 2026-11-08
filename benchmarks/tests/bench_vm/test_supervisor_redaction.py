"""A key the benchmark prints reaches no disk, bucket or log: redacted before any byte is written.

The 2026-11-08 amendment, end to end through ``python -m pydocs_eval.bench_vm
supervise`` (AC-R13 to AC-R15, AC-R17, AC-R19, AC-R23). The fixture child and its
pre-flight play output scripts (``_fixture_output_script``) that print every
synthetic value they hold in every shape: first in the stream, mid-line on
stderr, twice back to back, split across a pause longer than the sync
interval, and last with no newline. While still running they scan every file
the run can write and the fake bucket; afterwards the test scans again, on
every ending. Every value is a FAKE one; leaks are reported by place.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._fixture_child import PLAN_LINE
from ._fixture_output_script import Op, raw_output
from ._redaction_oracle import WITHHELD_PARTIAL_KEY, oracle_redact
from ._spec_dicts import agent_track_spec, retrieval_spec
from ._supervise_harness import FAKE_PROVIDER_KEYS, SHORT_TIMING, SuperviseHarness

# Longer than SHORT_TIMING's sync interval, so a periodic sync runs during it.
_PAUSE_S = 0.6
_SPLIT_AT = 12
_PRINTED_PER_SCRIPT = 6
_KIND_KEY = {"agent_track": "ANTHROPIC_API_KEY", "before_after": "OPENROUTER_API_KEY"}
_TIMED_OUT = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=8.0, stop_grace_s=5.0)


def _every_shape(name: str, log_kind: str, records: Path) -> list[Op]:
    """Print ``name``'s value six times, in every shape, then scan while still alive."""
    return [
        ["key", name, 0, None],
        ["out", "\n"],
        ["out", "a line with "],
        ["err_key", name],
        ["out", " inside, on stderr\n"],
        ["key", name, 0, None],
        ["key", name, 0, None],
        ["out", "\n"],
        ["out", "split: "],
        ["key", name, 0, _SPLIT_AT],
        ["sleep", _PAUSE_S],
        ["observe", str(records / f"{log_kind}-observed.jsonl"), log_kind],
        ["key", name, _SPLIT_AT, None],
        ["out", "\n"],
        ["out", "last: "],
        ["key", name, 0, None],
        ["scan", str(records / f"{log_kind}-scan.jsonl"), log_kind, _PRINTED_PER_SCRIPT],
    ]


def _still_printing(name: str) -> list[Op]:
    """SIGTERM the supervisor, then go on printing the value until stopped."""
    return [["signal_parent"], *([["sleep", 0.05], ["key", name, 0, None], ["out", "\n"]] * 200)]


def _event_names(err: str) -> list[str]:
    return [json.loads(line)["event"] for line in err.splitlines() if line.startswith("{")]


def _lines(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _launch(harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], kind: str) -> str:
    if kind == "agent_track":
        return harness.launch_run(capsys, "agents", agent_track_spec())
    return harness.launch_before_after(capsys)


def _ending_env(ending: str, name: str, child_script: list[Op]) -> dict[str, str]:
    if ending == "failed":
        return {"FIXTURE_EXIT_CODE": "3"}
    if ending == "timed_out":
        return {"FIXTURE_TICKS": "2000", "FIXTURE_TICK_S": "0.05"}
    if ending in ("preempted", "stopped"):
        child_script += _still_printing(name)
    return {}


def _assert_scanned_clean_while_alive(records: Path, log_kind: str) -> None:
    scans = _lines(records / f"{log_kind}-scan.jsonl")
    assert scans == [{"waited": True, "found": []}], log_kind


def _assert_first_half_hidden_during_the_pause(
    records: Path, log_kind: str, value: str, synced: bool
) -> None:
    (observed,) = _lines(records / f"{log_kind}-observed.jsonl")
    local = base64.b64decode(observed["local"])
    assert local.endswith(b"split: ")
    assert value[:_SPLIT_AT].encode() not in local
    if synced:  # a periodic sync ran during the pause and uploaded the same bytes
        assert observed["synced"] is not None
        assert base64.b64decode(observed["synced"]) == local


@pytest.mark.parametrize("ending", ["succeeded", "failed", "timed_out", "preempted", "stopped"])
@pytest.mark.parametrize("kind", ["agent_track", "before_after"])
def test_a_printed_key_reaches_no_file_bucket_request_record_or_stderr(
    supervise_harness: SuperviseHarness,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    kind: str,
    ending: str,
) -> None:
    name = _KIND_KEY[kind]
    value = FAKE_PROVIDER_KEYS[name]
    records = tmp_path / "records"
    records.mkdir()
    run_id = _launch(supervise_harness, capsys, kind)
    if ending == "preempted":
        supervise_harness.launch.cloud.instance_metadata["instance/preempted"] = "TRUE"
    preflight_script = _every_shape(name, "preflight", records)
    child_script = _every_shape(name, "child", records)
    env = _ending_env(ending, name, child_script)
    synced_state = supervise_harness.launch.cloud.root / supervise_harness.state_uri(
        run_id
    ).removeprefix("gs://")
    roots = os.pathsep.join(f"{k}={v}" for k, v in supervise_harness.scan_roots().items())

    result = supervise_harness.supervise(
        capsys,
        run_id,
        timing=_TIMED_OUT if ending == "timed_out" else SHORT_TIMING,
        FIXTURE_PREFLIGHT_OUTPUT_SCRIPT=json.dumps(preflight_script),
        FIXTURE_OUTPUT_SCRIPT=json.dumps(child_script),
        FIXTURE_SCAN_ROOTS=roots,
        FIXTURE_SYNCED_STATE_DIR=str(synced_state),
        **env,
    )

    # Event names only: a failure message must never repeat a value.
    assert supervise_harness.record(run_id).status is RunStatus(ending), _event_names(result.err)
    leaked_to = [
        place
        for secret in FAKE_PROVIDER_KEYS.values()
        for place in supervise_harness.places_holding(result, secret)
    ]
    assert leaked_to == []
    for log_kind in ("preflight", "child"):
        _assert_scanned_clean_while_alive(records, log_kind)
        _assert_first_half_hidden_during_the_pause(records, log_kind, value, log_kind == "child")
    keys = {name: value}
    logs = supervise_harness.state_dir / "logs"
    expected_preflight = oracle_redact(
        (PLAN_LINE + "\n").encode() + raw_output(preflight_script, keys), keys
    )
    assert (logs / "preflight-attempt-1.log").read_bytes() == expected_preflight
    child_log = (logs / "child-attempt-1.log").read_bytes()
    printed = oracle_redact(raw_output(_every_shape(name, "child", records), keys), keys)
    assert printed.count(f"<{name}>".encode()) == _PRINTED_PER_SCRIPT
    assert child_log.startswith(printed)
    rest = child_log.removeprefix(printed)
    line = f"<{name}>\n".encode()
    assert rest == line * (len(rest) // len(line))
    synced_log = supervise_harness.launch.cloud.object_data(
        supervise_harness.state_uri(run_id) + "logs/child-attempt-1.log"
    )
    assert synced_log == child_log


# --- a stop while a value is half printed ---------------------------------------------------


def test_a_value_cut_off_by_a_stop_reaches_no_file_or_bucket_in_any_part(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    name = _KIND_KEY["agent_track"]
    partial = FAKE_PROVIDER_KEYS[name][:_SPLIT_AT]
    run_id = _launch(supervise_harness, capsys, "agent_track")
    # SIGTERM the supervisor while the child's output ends inside the value.
    script: list[Op] = [["out", "last words "], ["key", name, 0, _SPLIT_AT], ["signal_parent"]]
    script += [["sleep", 30]]

    result = supervise_harness.supervise(capsys, run_id, FIXTURE_OUTPUT_SCRIPT=json.dumps(script))

    assert supervise_harness.record(run_id).status is RunStatus.STOPPED, _event_names(result.err)
    assert supervise_harness.places_holding(result, partial) == []
    child_log = (supervise_harness.state_dir / "logs" / "child-attempt-1.log").read_bytes()
    assert child_log == b"last words " + WITHHELD_PARTIAL_KEY
    synced_log = supervise_harness.launch.cloud.object_data(
        supervise_harness.state_uri(run_id) + "logs/child-attempt-1.log"
    )
    assert synced_log == child_log


# --- AC-R17: unusable values are refused --------------------------------------------------


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        ("", "at least 20 bytes"),
        ("FAKE-too-short", "at least 20 bytes"),
        (" sk-ant-FAKE-leading-space-0123456789", "no leading or trailing whitespace"),
        ("sk-ant-FAKE-trailing-newline-0123456789\n", "no leading or trailing whitespace"),
    ],
    ids=["empty", "too-short", "leading-space", "trailing-newline"],
)
def test_an_unusable_key_value_fails_the_run_before_any_preflight_naming_only_the_key(
    supervise_harness: SuperviseHarness,
    capsys: pytest.CaptureFixture[str],
    value: str,
    shape: str,
) -> None:
    supervise_harness.launch.cloud.secrets["bench-anthropic-api-key"] = value
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())

    result = supervise_harness.supervise(capsys, run_id)

    last = supervise_harness.record(run_id).history[-1]
    assert result.exit_code == 1
    assert last.status is RunStatus.FAILED
    assert "ANTHROPIC_API_KEY" in last.detail
    assert shape in last.detail
    assert result.invocations == []  # neither the pre-flight nor the child started
    for form in {value, value.strip()} - {""}:
        leaked_to = supervise_harness.places_holding(result, form)
        assert leaked_to == []


# --- AC-R19: a log that already exists ------------------------------------------------------


_PLANTED = b"bytes this attempt's supervisor did not write\n"


def test_a_child_log_that_already_exists_fails_the_run_before_the_child_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    planted = supervise_harness.state_dir / "logs" / "child-attempt-1.log"
    planted.parent.mkdir(parents=True)
    planted.write_bytes(_PLANTED)

    result = supervise_harness.supervise(capsys, run_id)

    last = supervise_harness.record(run_id).history[-1]
    assert last.status is RunStatus.FAILED, _event_names(result.err)
    assert str(planted) in last.detail
    assert result.invocations == []
    assert planted.read_bytes() == _PLANTED


def test_a_preflight_log_that_already_exists_fails_the_run_before_the_preflight_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())
    planted = supervise_harness.state_dir / "logs" / "preflight-attempt-1.log"
    planted.parent.mkdir(parents=True)
    planted.write_bytes(_PLANTED)

    result = supervise_harness.supervise(capsys, run_id)

    last = supervise_harness.record(run_id).history[-1]
    assert last.status is RunStatus.FAILED, _event_names(result.err)
    assert str(planted) in last.detail
    assert result.invocations == []
    assert planted.read_bytes() == _PLANTED


# --- AC-R10: never stalled by a sync ---------------------------------------------------------


def test_a_child_writing_far_more_than_a_pipe_buffer_finishes_during_a_held_sync(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    done = tmp_path / "writes-finished"
    seen_during_the_hold: list[bool] = []

    def hold_the_first_sync_open() -> None:
        if seen_during_the_hold:
            return
        deadline = time.monotonic() + 3.0
        while not done.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        seen_during_the_hold.append(done.exists())

    supervise_harness.launch.cloud.sync_hook = hold_the_first_sync_open
    run_id = supervise_harness.launch_run(capsys, "agents", agent_track_spec())
    script: list[Op] = [["sleep", 0.5], ["fill", 4 << 20], ["touch", str(done)], ["sleep", 0.5]]

    result = supervise_harness.supervise(capsys, run_id, FIXTURE_OUTPUT_SCRIPT=json.dumps(script))

    assert result.exit_code == 0, _event_names(result.err)
    assert seen_during_the_hold == [True]
    assert (supervise_harness.state_dir / "logs" / "child-attempt-1.log").stat().st_size == 4 << 20
