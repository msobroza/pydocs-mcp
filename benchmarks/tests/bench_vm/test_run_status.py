"""``status [SPEC]`` (``make bench-vm-status``): one run's record and log tail, or every live bench VM.

Driven through the entry point against ``FakeBenchCloud``; the runs are
launched for real first, and the harness stands in for the supervisor's
record writes and log syncs.
"""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.run_record import RunStatus

from ._fakes import FAKE_IMAGE_DIGEST, fake_vm_request
from ._launch_harness import LaunchHarness
from ._run_control_harness import launch_fixture_run, put_child_log, run_control
from ._spec_dicts import write_spec

Capsys = pytest.CaptureFixture[str]


def test_status_prints_the_history_commit_image_machine_and_the_log_tail(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)
    harness.supervisor_records(launched.run_id, RunStatus.RUNNING, vm_gone=False)
    put_child_log(harness, launched.run_id, 1, "indexing task 1\nscoring task 1\n")
    record = harness.record(launched.run_id)

    result = run_control(harness, capsys, "status", str(launched.spec_path))

    assert result.exit_code == 0, result.err
    assert launched.run_id in result.out
    assert record.commit in result.out
    assert record.image in result.out
    assert FAKE_IMAGE_DIGEST in result.out
    assert "n2-standard-8" in result.out
    for transition in record.history:
        row = next(line for line in result.out.splitlines() if transition.at.isoformat() in line)
        assert str(transition.status) in row
        assert f"attempt {transition.attempt}" in row
        assert str(transition.instance) in row
        assert transition.detail in row
    running = next(line for line in result.out.splitlines() if "running (scripted" in line)
    assert "europe-west4-a" in running
    assert result.out.rstrip().endswith("indexing task 1\nscoring task 1")


def test_the_history_comes_before_the_log_tail(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    put_child_log(harness, launched.run_id, 1, "child output\n")

    out = run_control(harness, capsys, "status", str(launched.spec_path)).out

    assert out.index("launching") < out.index("child output")


def test_status_prints_only_the_tail_of_a_long_log(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    lines = [f"log line {number:05d}" for number in range(1, 5001)]
    put_child_log(harness, launched.run_id, 1, "\n".join(lines) + "\n")

    out = run_control(harness, capsys, "status", str(launched.spec_path)).out

    assert "log line 05000" in out
    assert "log line 00001" not in out
    assert "log line 04900" not in out
    reads = [r for r in harness.cloud.requests if r.operation == "read_object_tail"]
    assert reads, "the log must be read by its tail, not downloaded whole"


def test_status_tails_the_current_attempts_log(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    put_child_log(harness, launched.run_id, 1, "first attempt output\n")
    harness.supervisor_records(launched.run_id, RunStatus.PREEMPTED)
    relaunched = harness.launch_path(capsys, launched.spec_path)
    assert relaunched.exit_code == 0, relaunched.err
    put_child_log(harness, launched.run_id, 2, "second attempt output\n")

    out = run_control(harness, capsys, "status", str(launched.spec_path)).out

    assert "second attempt output" in out
    assert "first attempt output" not in out
    assert "child-attempt-2.log" in out


def test_status_says_when_no_log_has_been_synced_yet(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    launched = launch_fixture_run(harness, capsys)

    result = run_control(harness, capsys, "status", str(launched.spec_path))

    assert result.exit_code == 0, result.err
    assert "no log synced yet" in result.out
    assert "child-attempt-1.log" in result.out


def test_status_names_the_runs_live_vm_or_none(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    instance = harness.record(launched.run_id).history[-1].instance

    live = run_control(harness, capsys, "status", str(launched.spec_path)).out
    harness.supervisor_records(launched.run_id, RunStatus.SUCCEEDED)
    gone = run_control(harness, capsys, "status", str(launched.spec_path)).out

    assert f"live VM: {instance}" in live
    assert "live VM: none" in gone


def test_status_of_a_run_never_launched_is_refused_naming_its_run_id(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    path = write_spec(
        harness.specs_dir,
        "never-launched",
        {"git_ref": "main", "kind": "retrieval", "configs": ["c.yaml"]},
    )

    result = run_control(harness, capsys, "status", str(path))

    assert result.exit_code == 1
    assert "never-launched-" in result.err
    assert "no run record" in result.err
    assert "make bench-vm RUN=runs/never-launched.yaml" in result.err


def test_status_of_an_invalid_spec_prints_its_field_errors(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    path = write_spec(
        harness.specs_dir, "typo", {"git_ref": "main", "kind": "retrieval", "confgs": []}
    )

    result = run_control(harness, capsys, "status", str(path))

    assert result.exit_code == 1
    assert "confgs" in result.err
    assert harness.cloud.requests == []


def test_status_refuses_an_unreadable_run_record(harness: LaunchHarness, capsys: Capsys) -> None:
    launched = launch_fixture_run(harness, capsys)
    harness.cloud.put_object(harness.record_uri(launched.run_id), b'{"schema_version": 99}')

    result = run_control(harness, capsys, "status", str(launched.spec_path))

    assert result.exit_code == 1
    assert "schema_version" in result.err


# --- no run named: every live bench VM ---------------------------------------------


def test_status_without_a_run_lists_every_live_bench_vm(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.create_instance(fake_vm_request("run-a", attempt=1, zone="europe-west4-a"))
    harness.cloud.create_instance(fake_vm_request("run-b", attempt=3, zone="europe-west4-c"))
    harness.cloud.create_instance(fake_vm_request("run-ended", attempt=1, zone="europe-west4-b"))
    harness.cloud.vanish("bench-run-ended-1")

    result = run_control(harness, capsys, "status")

    assert result.exit_code == 0, result.err
    rows = [line.split() for line in result.out.splitlines()]
    assert ["run-a", "1", "europe-west4-a", "RUNNING", "bench-run-a-1"] in rows
    assert ["run-b", "3", "europe-west4-c", "RUNNING", "bench-run-b-3"] in rows
    assert "run-ended" not in result.out
    listing = next(r for r in harness.cloud.requests if r.operation == "list_labelled_instances")
    assert listing.args["zones"] == harness.names.names.zones


def test_status_without_a_run_says_when_no_bench_vm_is_live(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = run_control(harness, capsys, "status")

    assert result.exit_code == 0, result.err
    assert "no live bench VM" in result.out
