"""The shutdown ending: the host shutdown script's ``docker stop`` reaches the supervisor as SIGTERM.

The fixture child sends the supervisor (its parent, the test process) the
SIGTERM after a few ledger lines, as ``docker stop`` would, and keeps writing
until it is stopped. What is observed: the run record, the order in which the
files reached GCS, the requests the cloud received, and the exit code.
"""

from __future__ import annotations

import signal

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._fixture_child import LEDGER_NAME, MANIFEST_NAME
from ._spec_dicts import retrieval_spec
from ._supervise_harness import SuperviseHarness, SuperviseResult

_PREEMPTED_FLAG = "instance/preempted"
# No periodic sync before the signal, so the shutdown's pushes are the only ones.
_NO_PERIODIC_SYNC = SupervisorTiming(sync_interval_s=60.0, soft_deadline_s=20.0, stop_grace_s=5.0)
_CHILD_LOG = "logs/child-attempt-1.log"


def _shut_down(
    harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], *, preempted: str | None
) -> tuple[str, SuperviseResult]:
    run_id = harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    if preempted is not None:
        harness.launch.cloud.instance_metadata[_PREEMPTED_FLAG] = preempted
    result = harness.supervise(
        capsys,
        run_id,
        timing=_NO_PERIODIC_SYNC,
        FIXTURE_TICKS="400",
        FIXTURE_TICK_S="0.05",
        FIXTURE_SIGNAL_PARENT_AFTER="3",
    )
    assert result.unhandled_sigterms == 0
    return run_id, result


def test_a_preempted_shutdown_stops_the_child_and_ends_preempted(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, result = _shut_down(supervise_harness, capsys, preempted="TRUE")

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert [t.status for t in record.history] == [
        RunStatus.LAUNCHING,
        RunStatus.RUNNING,
        RunStatus.PREEMPTED,
    ]
    assert result.signals == ["stopped"]
    assert record.exit_code == 143
    assert "preempted" in record.history[-1].detail
    assert record.last_sync_at is not None


def test_a_preempted_shutdown_pushes_the_ledgers_before_the_rest(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, _ = _shut_down(supervise_harness, capsys, preempted="TRUE")

    first, second = supervise_harness.launch.cloud.syncs
    assert set(first.uploaded) == {LEDGER_NAME, MANIFEST_NAME}
    assert not {LEDGER_NAME, MANIFEST_NAME} & set(second.uploaded)
    assert _CHILD_LOG in second.uploaded
    # What GCS holds is the ledger as the stopped child left it.
    state_uri = supervise_harness.state_uri(run_id)
    local = (supervise_harness.state_dir / LEDGER_NAME).read_bytes()
    assert supervise_harness.launch.cloud.object_data(state_uri + LEDGER_NAME) == local


def test_a_shutdown_pushes_the_state_before_it_records_the_ending(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id, _ = _shut_down(supervise_harness, capsys, preempted="TRUE")

    cloud = supervise_harness.launch.cloud
    record_uri = supervise_harness.launch.record_uri(run_id)
    # A sync's own uploads are writes too; only the record's count here.
    tail = [
        "record" if r.operation == "write_object" else r.operation
        for r in cloud.requests
        if r.operation == "sync_directory"
        or (r.operation == "write_object" and r.args["uri"] == record_uri)
    ]
    assert tail[-3:] == ["sync_directory", "sync_directory", "record"]


def test_a_preempted_run_requests_no_self_delete(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    _shut_down(supervise_harness, capsys, preempted="TRUE")

    assert supervise_harness.launch.cloud.deleted == []
    assert "delete_instance" not in supervise_harness.launch.cloud.operations()


@pytest.mark.parametrize("flag", ["FALSE", None])
def test_a_shutdown_without_the_preempted_flag_ends_stopped(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], flag: str | None
) -> None:
    run_id, result = _shut_down(supervise_harness, capsys, preempted=flag)

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert record.status is RunStatus.STOPPED
    assert result.signals == ["stopped"]
    assert len(supervise_harness.launch.cloud.syncs) == 2
    assert supervise_harness.launch.cloud.deleted == []


def test_the_preempted_flag_is_read_after_the_signal(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    _shut_down(supervise_harness, capsys, preempted="TRUE")

    cloud = supervise_harness.launch.cloud
    reads = [
        index
        for index, r in enumerate(cloud.requests)
        if r.operation == "read_instance_metadata" and r.args["path"] == _PREEMPTED_FLAG
    ]
    first_sync = cloud.operations().index("sync_directory")
    assert len(reads) == 1
    assert reads[0] < first_sync


def test_an_unreadable_preempted_flag_ends_stopped_and_says_so(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.launch.cloud.metadata_errors[_PREEMPTED_FLAG] = BenchCloudError(
        "reading instance metadata instance/preempted: 3 attempts, last: Failed to connect"
    )
    run_id, _ = _shut_down(supervise_harness, capsys, preempted=None)

    record = supervise_harness.record(run_id)
    assert record.status is RunStatus.STOPPED
    assert "preempted flag" in record.history[-1].detail
    assert "Failed to connect" in record.history[-1].detail


def test_a_child_that_ignores_the_shutdown_is_killed_after_the_shutdown_grace(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    timing = SupervisorTiming(
        sync_interval_s=60.0, soft_deadline_s=20.0, stop_grace_s=30.0, shutdown_stop_grace_s=0.3
    )

    result = supervise_harness.supervise(
        capsys,
        run_id,
        timing=timing,
        FIXTURE_TICKS="400",
        FIXTURE_TICK_S="0.05",
        FIXTURE_SIGNAL_PARENT_AFTER="3",
        FIXTURE_IGNORE_SIGTERM="1",
    )

    record = supervise_harness.record(run_id)
    assert result.unhandled_sigterms == 0
    assert record.status is RunStatus.STOPPED
    assert record.exit_code == -9


def test_the_supervisor_puts_back_the_sigterm_handler_it_found(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    before = signal.getsignal(signal.SIGTERM)

    supervise_harness.supervise(capsys, run_id)

    assert signal.getsignal(signal.SIGTERM) is before
