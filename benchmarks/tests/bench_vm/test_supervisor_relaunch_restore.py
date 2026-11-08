"""A relaunch restores the run's state before the child starts, and the child resumes from its ledger.

Attempt 1 is stopped mid-ledger by a shutdown; the relaunch boots a fresh VM
(empty state directory), and attempt 2's supervisor pulls the state back from
GCS before the fixture child starts. The child skips the task ids its ledger
already records, as each kind does with its own ledger. The restore leaves the
supervisor's logs out (ADR 0026, amendment 2026-11-08): earlier attempts' logs
stay in the bucket and are never read back, redacted twice or uploaded again.
"""

from __future__ import annotations

import json

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._fixture_child import LEDGER_NAME, VERBATIM_STDERR, VERBATIM_STDOUT
from ._leak_scan import files_under
from ._spec_dicts import retrieval_spec
from ._supervise_harness import SuperviseHarness

_TASKS = 40
_SENTINEL = b"SENTINEL-written-by-an-earlier-attempt"
_PLANTED_VALUE = "sk-ant-FAKE-77e0c3d1-planted-in-an-earlier-log"
_TIMING = SupervisorTiming(sync_interval_s=60.0, soft_deadline_s=20.0, stop_grace_s=5.0)


def _ledger_ids(data: bytes) -> list[int]:
    return [json.loads(line)["task"] for line in data.splitlines()]


def _stopped_first_attempt(harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]) -> str:
    run_id = harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    first = harness.supervise(
        capsys,
        run_id,
        timing=_TIMING,
        FIXTURE_TICKS=str(_TASKS),
        FIXTURE_TICK_S="0.05",
        FIXTURE_SIGNAL_PARENT_AFTER="3",
    )
    assert harness.record(run_id).status is RunStatus.STOPPED, first.err
    assert first.parent_sigterms_sent == 1
    harness.launch.cloud.vanish()  # the stopped VM is gone
    return run_id


def _relaunch_on_a_fresh_vm(harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]) -> None:
    harness.relaunch(capsys, "cpu-smoke")
    harness.boot_a_fresh_vm()


def test_a_first_attempt_restores_nothing(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    supervise_harness.supervise(capsys, run_id)

    assert "restore_directory" not in supervise_harness.launch.cloud.operations()


def test_a_relaunch_restores_the_state_before_the_child_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _stopped_first_attempt(supervise_harness, capsys)
    synced = supervise_harness.launch.cloud.object_data(
        supervise_harness.state_uri(run_id) + LEDGER_NAME
    )
    assert synced is not None
    _relaunch_on_a_fresh_vm(supervise_harness, capsys)

    result = supervise_harness.supervise(
        capsys, run_id, timing=_TIMING, FIXTURE_TICKS=str(_TASKS), FIXTURE_TICK_S="0"
    )

    assert result.exit_code == 0, result.err
    # Attempt 1's shutdown stays with attempt 1: attempt 2 runs to its end undisturbed.
    assert result.parent_sigterms_sent == 0
    assert supervise_harness.launch.cloud.restores == [
        (supervise_harness.state_uri(run_id), supervise_harness.state_dir)
    ]
    assert result.started["recorded_at_start"] == _ledger_ids(synced)
    assert result.started["env"]["BENCH_ATTEMPT"] == "2"


def test_the_resumed_child_completes_its_ledger_with_each_task_once(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _stopped_first_attempt(supervise_harness, capsys)
    _relaunch_on_a_fresh_vm(supervise_harness, capsys)

    second = supervise_harness.supervise(
        capsys, run_id, timing=_TIMING, FIXTURE_TICKS=str(_TASKS), FIXTURE_TICK_S="0"
    )

    assert second.parent_sigterms_sent == 0
    record = supervise_harness.record(run_id)
    assert [(t.status, t.attempt) for t in record.history] == [
        (RunStatus.LAUNCHING, 1),
        (RunStatus.RUNNING, 1),
        (RunStatus.STOPPED, 1),
        (RunStatus.LAUNCHING, 2),
        (RunStatus.RUNNING, 2),
        (RunStatus.SUCCEEDED, 2),
    ]
    synced = supervise_harness.launch.cloud.object_data(
        supervise_harness.state_uri(run_id) + LEDGER_NAME
    )
    assert synced is not None
    assert sorted(_ledger_ids(synced)) == list(range(_TASKS))
    # Attempt 1's log stays in the bucket only; the VM holds attempt 2's alone.
    logs = supervise_harness.state_dir / "logs"
    assert sorted(p.name for p in logs.iterdir()) == ["child-attempt-2.log"]
    state_uri = supervise_harness.state_uri(run_id)
    assert supervise_harness.launch.cloud.object_data(state_uri + "logs/child-attempt-1.log")


def test_a_restored_ledger_with_a_truncated_last_line_resumes_to_a_complete_ledger(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _stopped_first_attempt(supervise_harness, capsys)
    cloud, ledger_uri = (
        supervise_harness.launch.cloud,
        supervise_harness.state_uri(run_id) + LEDGER_NAME,
    )
    # A sync that caught the child mid-line.
    cloud.put_object(ledger_uri, b'{"task": 0}\n{"task": 1}\n{"ta')
    _relaunch_on_a_fresh_vm(supervise_harness, capsys)

    result = supervise_harness.supervise(
        capsys, run_id, timing=_TIMING, FIXTURE_TICKS=str(_TASKS), FIXTURE_TICK_S="0"
    )

    assert result.parent_sigterms_sent == 0
    assert result.started["recorded_at_start"] == [0, 1]
    synced = cloud.object_data(ledger_uri)
    assert synced is not None
    assert _ledger_ids(synced) == list(range(_TASKS))


def test_a_failed_restore_ends_failed_before_the_child_and_pushes_nothing(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _stopped_first_attempt(supervise_harness, capsys)
    cloud = supervise_harness.launch.cloud
    ledger_uri = supervise_harness.state_uri(run_id) + LEDGER_NAME
    before = cloud.object_data(ledger_uri)
    _relaunch_on_a_fresh_vm(supervise_harness, capsys)
    started = supervise_harness.root / "child-started.json"
    started.unlink()
    cloud.restore_errors[supervise_harness.state_uri(run_id)] = BenchCloudError(
        "restoring: 3 attempts, last: HTTPError 503 (scripted)"
    )
    syncs_before = len(cloud.syncs)

    result = supervise_harness.supervise(capsys, run_id, timing=_TIMING)

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert record.status is RunStatus.FAILED
    assert "restoring the state" in record.history[-1].detail
    assert not started.exists()
    # A state that was never restored is never pushed over the good copy.
    assert len(cloud.syncs) == syncs_before
    assert cloud.object_data(ledger_uri) == before
    assert len(cloud.deleted) == 1


def test_a_relaunch_never_brings_back_or_replays_an_earlier_attempts_logs(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _stopped_first_attempt(supervise_harness, capsys)
    cloud, state_uri = supervise_harness.launch.cloud, supervise_harness.state_uri(run_id)
    planted = {
        f"{state_uri}logs/child-attempt-1.log": _SENTINEL + b"\n" + _PLANTED_VALUE.encode(),
        f"{state_uri}logs/preflight-attempt-1.log": _PLANTED_VALUE.encode() + _SENTINEL,
    }
    for uri, data in planted.items():
        cloud.put_object(uri, data)
    _relaunch_on_a_fresh_vm(supervise_harness, capsys)
    syncs_before = len(cloud.syncs)

    result = supervise_harness.supervise(
        capsys, run_id, timing=_TIMING, FIXTURE_TICKS=str(_TASKS), FIXTURE_TICK_S="0"
    )

    assert result.exit_code == 0, result.err
    # The restore brought no log; the new attempt's log holds only its own output.
    logs = supervise_harness.state_dir / "logs"
    assert sorted(p.name for p in logs.iterdir()) == ["child-attempt-2.log"]
    assert (logs / "child-attempt-2.log").read_bytes() == VERBATIM_STDERR + VERBATIM_STDOUT
    # No sync of attempt 2 uploads another attempt's log, and the earlier objects stay as they were.
    uploaded = [name for sync in cloud.syncs[syncs_before:] for name in sync.uploaded]
    assert {name for name in uploaded if name.startswith("logs/")} == {"logs/child-attempt-2.log"}
    assert {uri: cloud.object_data(uri) for uri in planted} == planted
    # Neither the sentinel nor the planted value appears in anything attempt 2 produced.
    record = supervise_harness.record(run_id)
    produced = [path.read_bytes() for path in files_under(supervise_harness.state_dir)]
    produced += [cloud.object_data(state_uri + name) or b"" for name in uploaded]
    produced += [result.err.encode(), json.dumps([t.detail for t in record.history]).encode()]
    for needle in (_SENTINEL, _PLANTED_VALUE.encode()):
        assert not any(needle in data for data in produced)
