"""A cache push in flight never holds up the run: state syncs and the shutdown poll go on beside it.

An archive or a mirror upload can take many minutes. The fake cloud holds the
next cache upload (``hold_next_upload``) and records how many state syncs ran
beside it, so these tests see whether the supervisor kept watching the child
while the upload ran.
"""

from __future__ import annotations

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
    cloud = supervise_harness.launch.cloud
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    # The upload is held until the run's ending is recorded (or the hold's limit).
    seen_while_held: list[RunStatus] = []

    def ending_recorded(_syncs_beside: int) -> bool:
        status = supervise_harness.record(run_id).status
        if status is RunStatus.RUNNING:
            return False
        seen_while_held.append(status)
        return True

    cloud.hold_next_upload = ending_recorded

    result = supervise_harness.supervise(
        capsys,
        run_id,
        timing=_TIMING,
        FIXTURE_CACHES="downloads",
        FIXTURE_TICKS="200",
        FIXTURE_TICK_S="0.1",
        # A second after the caches are written: a sync has started their push by then.
        FIXTURE_SIGNAL_PARENT_AFTER="10",
    )

    assert supervise_harness.record(run_id).status is RunStatus.STOPPED, result.err
    assert result.signals == ["stopped"]
    # The ending was recorded while the upload was still in flight.
    assert seen_while_held == [RunStatus.STOPPED]
