"""A cache push in flight never holds up the run: state syncs and the shutdown poll go on beside it.

An archive or a mirror upload can take many minutes. The fake cloud holds the
next cache upload (``hold_next_upload``) and records how many state syncs ran
beside it, so these tests see whether the supervisor kept watching the child
while the upload ran. A shutdown is sent from inside the held upload, as the
host's ``docker stop`` would, so it always arrives while the upload runs.
"""

from __future__ import annotations

import os
import signal

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._spec_dicts import retrieval_spec
from ._supervise_harness import SuperviseHarness

_TIMING = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=20.0, stop_grace_s=5.0)


def _finished_uploads(harness: SuperviseHarness) -> list[int]:
    """The state syncs that ran beside each cache upload, in the order the uploads finished."""
    return [
        int(str(request.args["syncs_beside"]))
        for request in harness.launch.cloud.requests
        if request.operation == "cache_upload_finished"
    ]


def _send_the_supervisor_sigterm() -> None:
    os.kill(os.getpid(), signal.SIGTERM)


def test_state_syncs_go_on_while_a_cache_push_is_in_flight(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    supervise_harness.launch.cloud.hold_next_upload = lambda syncs_beside: syncs_beside >= 3
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(
        capsys,
        run_id,
        timing=_TIMING,
        FIXTURE_CACHES="downloads",
        FIXTURE_TICKS="20",
        FIXTURE_TICK_S="0.1",
    )

    assert result.exit_code == 0, result.err
    assert supervise_harness.record(run_id).status is RunStatus.SUCCEEDED
    assert _finished_uploads(supervise_harness)[0] >= 3


def test_a_shutdown_ends_the_run_while_a_cache_push_is_in_flight(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    seen_while_held: list[RunStatus] = []
    signalled = False

    def shutdown_then_wait_for_the_ending(_syncs_beside: int) -> bool:
        nonlocal signalled
        if not signalled:
            signalled = True
            _send_the_supervisor_sigterm()
        status = supervise_harness.record(run_id).status
        if status is RunStatus.RUNNING:
            return False
        seen_while_held.append(status)
        return True

    supervise_harness.launch.cloud.hold_next_upload = shutdown_then_wait_for_the_ending

    result = supervise_harness.supervise(
        capsys,
        run_id,
        timing=_TIMING,
        FIXTURE_CACHES="downloads",
        FIXTURE_TICKS="200",
        FIXTURE_TICK_S="0.1",
    )

    assert supervise_harness.record(run_id).status is RunStatus.STOPPED, result.err
    assert result.signals == ["stopped"]
    # The ending was recorded while the upload was still in flight.
    assert seen_while_held == [RunStatus.STOPPED]


def test_a_shutdown_during_the_wait_after_the_ending_starts_no_new_push(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    def ending_then_shutdown(_syncs_beside: int) -> bool:
        if supervise_harness.record(run_id).status is RunStatus.RUNNING:
            return False
        _send_the_supervisor_sigterm()  # the VM starts going away after the ending
        return True

    supervise_harness.launch.cloud.hold_next_upload = ending_then_shutdown

    result = supervise_harness.supervise(
        capsys,
        run_id,
        timing=_TIMING,
        FIXTURE_CACHES="downloads",
        FIXTURE_TICKS="10",
        FIXTURE_TICK_S="0.1",
    )

    assert supervise_harness.record(run_id).status is RunStatus.SUCCEEDED, result.err
    # Only the upload already in flight finished; the rest were deferred, never started.
    assert len(_finished_uploads(supervise_harness)) == 1
    assert "bench_vm_cache_transfer_deferred" in result.err
