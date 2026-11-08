"""A supervisor that must not run its run: it starts nothing, writes nothing, and does not leave its VM billing.

A refused or unreadable run is refused before any child starts and before any
record write. Once the host facts are read the VM knows which instance it is,
so it deletes itself, unless the spec copy it can read says
``self_delete: false``; otherwise it would idle until ``max_runtime``, the
platform's backstop. With no host facts it cannot tell which VM it is, and
deletes nothing.
"""

from __future__ import annotations

import json

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import SPEC_COPY_OBJECT_NAME, RunStatus

from ._spec_dicts import retrieval_spec
from ._supervise_harness import SuperviseHarness, SuperviseResult


def _own_vm(harness: SuperviseHarness) -> tuple[str, str, str]:
    vm = harness.vm
    return (vm.project, vm.zone, vm.name)


def _record_bytes(harness: SuperviseHarness, run_id: str) -> bytes | None:
    return harness.launch.cloud.object_data(harness.launch.record_uri(run_id))


def _with_record_status(harness: SuperviseHarness, run_id: str, status: RunStatus) -> None:
    """Move the record as the launcher does when it gives up on its own create (``failed``)."""
    cloud, uri = harness.launch.cloud, harness.launch.record_uri(run_id)
    payload = json.loads(cloud.object_data(uri) or b"")
    cloud.put_object(uri, json.dumps({**payload, "status": status}).encode())


def _assert_nothing_ran(result: SuperviseResult) -> None:
    assert result.exit_code == 2, result.err
    assert not result.started_path.exists()


# --- refused with the host facts known: the VM deletes itself -------------------------


def test_an_attempt_whose_record_moved_on_deletes_its_own_vm_and_writes_nothing(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    # Every create attempt timed out, so the launcher recorded `failed`; one VM came up anyway.
    _with_record_status(supervise_harness, run_id, RunStatus.FAILED)
    before = _record_bytes(supervise_harness, run_id)

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    assert "expected launching" in result.err
    assert supervise_harness.launch.cloud.deleted == [_own_vm(supervise_harness)]
    assert _record_bytes(supervise_harness, run_id) == before
    assert supervise_harness.launch.cloud.syncs == []


def test_a_refused_attempt_keeps_its_vm_when_the_spec_copy_says_self_delete_false(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = retrieval_spec(self_delete=False)
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", spec)
    _with_record_status(supervise_harness, run_id, RunStatus.FAILED)

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    assert supervise_harness.launch.cloud.deleted == []
    assert "bench_vm_refused_vm_kept" in result.err


def test_a_refusal_whose_spec_copy_is_unreadable_deletes_the_vm(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec(self_delete=False))
    spec_uri = supervise_harness.launch.record_uri(run_id).rsplit("/", 1)[0] + "/"
    spec_uri += SPEC_COPY_OBJECT_NAME
    supervise_harness.launch.cloud.read_errors[spec_uri] = BenchCloudError("503 (scripted)")

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    # Unreadable, the copy's `self_delete: false` is unknown: the default (delete) holds.
    assert supervise_harness.launch.cloud.deleted == [_own_vm(supervise_harness)]


def test_a_run_record_that_cannot_be_read_deletes_the_vm(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    record_uri = supervise_harness.launch.record_uri(run_id)
    supervise_harness.launch.cloud.read_errors[record_uri] = BenchCloudError(
        f"reading {record_uri}: 3 attempts, last: HTTPError 503 (scripted)"
    )

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    assert json.loads(result.err.strip().splitlines()[-1])["event"] == "bench_vm_supervisor_failed"
    assert supervise_harness.launch.cloud.deleted == [_own_vm(supervise_harness)]


def test_an_unknown_record_schema_version_is_refused_before_anything_runs(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    cloud, uri = supervise_harness.launch.cloud, supervise_harness.launch.record_uri(run_id)
    payload = json.loads(cloud.object_data(uri) or b"")
    cloud.put_object(uri, json.dumps({**payload, "schema_version": 99}).encode())
    writes_before = len([r for r in cloud.requests if r.operation == "write_object"])

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    assert "got 99, expected 1" in result.err
    assert len([r for r in cloud.requests if r.operation == "write_object"]) == writes_before
    assert cloud.syncs == []
    assert cloud.deleted == [_own_vm(supervise_harness)]


def test_a_run_already_past_launching_is_refused(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.supervise(capsys, run_id)
    (supervise_harness.root / "child-started.json").unlink()
    before = _record_bytes(supervise_harness, run_id)

    again = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(again)
    assert "succeeded" in again.err
    assert _record_bytes(supervise_harness, run_id) == before


def test_a_stale_running_write_is_refused_before_the_child_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.launch.cloud.stale_write_uris.add(supervise_harness.launch.record_uri(run_id))

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    assert "stale write" in result.err
    assert supervise_harness.record(run_id).status is RunStatus.LAUNCHING
    # Another writer owns the run now: this VM has nothing left to do.
    assert supervise_harness.launch.cloud.deleted == [_own_vm(supervise_harness)]


# --- no host facts: the VM cannot tell which instance it is ----------------------------


def test_a_vm_without_its_run_metadata_is_refused_naming_the_key(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    del supervise_harness.launch.cloud.instance_metadata["instance/attributes/bench-run-id"]

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    assert "bench-run-id" in result.err
    assert supervise_harness.launch.cloud.deleted == []


def test_a_cloud_error_while_reading_the_host_facts_is_logged_and_starts_nothing(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.launch.cloud.metadata_errors["instance/zone"] = BenchCloudError(
        "reading instance metadata instance/zone: 3 attempts, last: Failed to connect"
    )

    result = supervise_harness.supervise(capsys, run_id)

    _assert_nothing_ran(result)
    event = json.loads(result.err.strip().splitlines()[-1])
    assert event["event"] == "bench_vm_supervisor_failed"
    assert "instance/zone" in event["detail"]
    assert supervise_harness.launch.cloud.deleted == []
