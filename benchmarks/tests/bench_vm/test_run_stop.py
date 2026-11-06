"""``stop SPEC`` (``make bench-vm-stop``): delete exactly the run's live VM, so the run ends ``stopped``.

Driven through the entry point against ``FakeBenchCloud``. Deleting the VM is
all the verb does: the VM's shutdown path gives the supervisor its grace
period to sync and record ``stopped``, which the harness stands in for.
"""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import RunStatus

from ._fakes import fake_vm_request
from ._launch_harness import LaunchHarness
from ._run_control_harness import launch_fixture_run, run_control
from ._spec_dicts import write_spec

Capsys = pytest.CaptureFixture[str]


def test_stop_deletes_exactly_the_runs_live_vm(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    harness.supervisor_records(launched.run_id, RunStatus.RUNNING, vm_gone=False)
    vm = harness.cloud.created[-1]
    harness.cloud.create_instance(fake_vm_request("another-run", attempt=1, zone=vm.zone))

    result = run_control(harness, capsys, "stop", str(launched.spec_path))

    assert result.exit_code == 0, result.err
    assert harness.cloud.deleted == [(vm.project, vm.zone, vm.name)]
    assert vm.name in result.out
    assert "stopped" in result.out
    assert "make bench-vm RUN=runs/cpu-smoke.yaml" in result.out


def test_stopping_a_vm_whose_supervisor_never_started_says_the_next_launch_records_it_lost(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)

    result = run_control(harness, capsys, "stop", str(launched.spec_path))

    assert result.exit_code == 0, result.err
    assert "launching" in result.out
    assert "lost" in result.out
    assert "supervisor records stopped" not in result.out


def test_stopping_a_kept_vm_of_an_ended_run_says_its_record_stays(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)
    harness.supervisor_records(launched.run_id, RunStatus.FAILED, vm_gone=False)

    result = run_control(harness, capsys, "stop", str(launched.spec_path))

    assert result.exit_code == 0, result.err
    assert len(harness.cloud.deleted) == 1
    assert "already ended failed" in result.out
    assert "supervisor records stopped" not in result.out


def test_stop_writes_no_record_the_supervisor_records_stopped(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)
    before = harness.record_generation(launched.run_id)

    run_control(harness, capsys, "stop", str(launched.spec_path))

    assert harness.record_generation(launched.run_id) == before


def test_a_stopped_run_relaunches_as_its_next_attempt(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)
    run_control(harness, capsys, "stop", str(launched.spec_path))
    harness.supervisor_records(launched.run_id, RunStatus.STOPPED)

    relaunched = harness.launch_path(capsys, launched.spec_path)

    assert relaunched.exit_code == 0, relaunched.err
    assert harness.record(launched.run_id).attempt == 2


@pytest.mark.parametrize("status", [RunStatus.PREEMPTED, RunStatus.SUCCEEDED, RunStatus.STOPPED])
def test_a_run_with_no_live_vm_is_refused_naming_its_status(
    harness: LaunchHarness, capsys: Capsys, status: RunStatus
) -> None:
    launched = launch_fixture_run(harness, capsys)
    harness.supervisor_records(launched.run_id, status)

    result = run_control(harness, capsys, "stop", str(launched.spec_path))

    assert result.exit_code == 1
    assert f"status {status}" in result.err
    assert launched.run_id in result.err
    assert harness.cloud.deleted == []


def test_a_run_never_launched_is_refused(harness: LaunchHarness, capsys: Capsys) -> None:
    path = write_spec(
        harness.specs_dir, "never", {"git_ref": "main", "kind": "retrieval", "configs": ["c.yaml"]}
    )

    result = run_control(harness, capsys, "stop", str(path))

    assert result.exit_code == 1
    assert "no run record" in result.err
    assert harness.cloud.deleted == []


def test_the_live_vm_is_looked_up_by_run_id_across_the_regions_zones(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)

    run_control(harness, capsys, "stop", str(launched.spec_path))

    listing = next(r for r in harness.cloud.requests if r.operation == "list_instances")
    assert listing.args["value"] == launched.run_id
    assert set(harness.names.names.zones) <= set(listing.args["zones"])  # type: ignore[arg-type]


def test_a_failed_delete_is_reported_naming_the_vm(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    vm = harness.cloud.created[-1]
    harness.cloud.delete_errors[vm.name] = BenchCloudError("deleting it failed (scripted)")

    result = run_control(harness, capsys, "stop", str(launched.spec_path))

    assert result.exit_code == 1
    assert "stop failed" in result.err
    assert "scripted" in result.err


def test_stop_needs_a_run_spec(harness: LaunchHarness, capsys: Capsys) -> None:
    with pytest.raises(SystemExit) as exited:
        run_control(harness, capsys, "stop")

    assert exited.value.code == 2
