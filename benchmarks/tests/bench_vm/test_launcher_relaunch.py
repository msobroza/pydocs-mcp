"""Re-running ``launch`` over an existing run record: relaunch an ended run, refuse a final or live one.

Driven through the entry point against ``FakeBenchCloud`` and the real fixture
git repository. The harness stands in for the supervisor: it records the
run's ending and makes its VM vanish (or keeps it alive).
"""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.commit_layout import HOST_STARTUP_SCRIPT
from pydocs_eval.bench_vm.run_record import RunStatus, run_record_bytes
from pydocs_eval.bench_vm.run_spec_file import load_run_spec
from pydocs_eval.bench_vm.vm_request import (
    LABEL_ATTEMPT,
    LABEL_RUN_ID,
    METADATA_ATTEMPT,
    METADATA_STARTUP_SCRIPT,
)

from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR, write_run_commit_layout
from ._launch_harness import LaunchHarness, LaunchResult
from ._spec_dicts import retrieval_spec

Capsys = pytest.CaptureFixture[str]
_RELAUNCHABLE = (
    RunStatus.PREEMPTED,
    RunStatus.FAILED,
    RunStatus.TIMED_OUT,
    RunStatus.STOPPED,
    RunStatus.LOST,
)
_ZONES = ("europe-west4-a", "europe-west4-b", "europe-west4-c")


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


def _first_launch(harness: LaunchHarness, capsys: Capsys) -> LaunchResult:
    first = harness.launch(capsys, "cpu-smoke", _spec())
    assert first.exit_code == 0, first.err
    return first


def _move_the_branch(harness: LaunchHarness) -> str:
    """Push a newer commit to ``main``, which the spec's ``git_ref`` names."""
    write_run_commit_layout(harness.repo, marker="second")
    moved = harness.repo.commit("the branch moved on")
    harness.repo.push_main()
    harness.publish_images(moved)
    return moved


def _record_writes(harness: LaunchHarness, run_id: str) -> list[int]:
    return [
        int(str(r.args["if_generation_match"]))
        for r in harness.cloud.requests
        if r.operation == "write_object" and r.args["uri"] == harness.record_uri(run_id)
    ]


def _assert_refused_without_writing(harness: LaunchHarness, result: LaunchResult) -> None:
    assert result.exit_code == 1
    assert "launch refused" in result.err
    assert harness.writes() == []
    assert "create_instance" not in harness.cloud.operations()


# --- relaunch from every status that allows one --------------------------------


@pytest.mark.parametrize("status", _RELAUNCHABLE)
def test_a_relaunch_reuses_the_frozen_commit_after_the_branch_moved_as_attempt_two(
    harness: LaunchHarness, capsys: Capsys, status: RunStatus
) -> None:
    first = _first_launch(harness, capsys)
    frozen = harness.repo.head()
    harness.supervisor_records(first.run_id, status)
    moved = _move_the_branch(harness)
    harness.forget_requests()

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 0, second.err
    assert second.run_id == first.run_id
    record = harness.record(first.run_id)
    assert record.commit == frozen != moved
    assert (record.status, record.attempt) == (RunStatus.LAUNCHING, 2)
    assert [t.status for t in record.history] == [RunStatus.LAUNCHING, status, RunStatus.LAUNCHING]
    relaunched = record.history[-1]
    assert (relaunched.attempt, relaunched.instance) == (2, f"bench-{first.run_id}-2")
    assert f"/bench-cpu:{frozen}" in record.image


def test_the_relaunched_vm_is_named_and_labelled_for_its_attempt_with_the_frozen_commits_scripts(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)
    _move_the_branch(harness)
    harness.forget_requests()

    harness.launch_path(capsys, first.spec_path)

    (vm,) = harness.cloud.created
    assert vm.name == f"bench-{first.run_id}-2"
    assert vm.labels == {LABEL_RUN_ID: first.run_id, LABEL_ATTEMPT: "2"}
    assert vm.metadata[METADATA_ATTEMPT] == "2"
    assert vm.metadata[METADATA_STARTUP_SCRIPT] == "#!/bin/sh\necho startup stand-in first\n"


def test_a_relaunch_writes_the_record_over_the_generation_it_read_and_keeps_the_spec_copy(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.STOPPED)
    generation = harness.record_generation(first.run_id)
    copy_uri = f"gs://bench-runs/{first.run_id}/spec.yaml"
    spec_copy = harness.cloud.object_data(copy_uri)
    harness.forget_requests()

    harness.launch_path(capsys, first.spec_path)

    assert _record_writes(harness, first.run_id) == [generation]
    copy_writes = [
        r.args["if_generation_match"]
        for r in harness.cloud.requests
        if r.operation == "write_object" and r.args["uri"] == copy_uri
    ]
    assert copy_writes in ([], [0])
    assert harness.cloud.object_data(copy_uri) == spec_copy


def test_relaunching_again_increments_the_attempt_again(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)
    harness.launch_path(capsys, first.spec_path)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)

    third = harness.launch_path(capsys, first.spec_path)

    assert third.exit_code == 0, third.err
    record = harness.record(first.run_id)
    assert record.attempt == 3
    assert harness.cloud.created[-1].name == f"bench-{first.run_id}-3"
    assert [t.attempt for t in record.history] == [1, 1, 2, 2, 3]


def test_a_relaunch_record_drops_the_ended_attempts_exit_code(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.FAILED)
    ended = harness.record(first.run_id).model_copy(update={"exit_code": 1})
    harness.cloud.put_object(harness.record_uri(first.run_id), run_record_bytes(ended))

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 0, second.err
    record = harness.record(first.run_id)
    assert (record.status, record.attempt) == (RunStatus.LAUNCHING, 2)
    # Attempt 1's exit code is not attempt 2's: the new attempt has none yet.
    assert record.exit_code is None
    assert [t.status for t in record.history[-2:]] == [RunStatus.FAILED, RunStatus.LAUNCHING]


def test_a_relaunch_whose_image_was_cleaned_up_rebuilds_it_from_the_frozen_commit(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    frozen = harness.repo.head()
    harness.supervisor_records(first.run_id, RunStatus.FAILED)
    _move_the_branch(harness)
    harness.cloud.images.clear()
    harness.forget_requests()

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 0, second.err
    (build,) = harness.cloud.builds
    assert build.archive_commit == frozen
    assert harness.record(first.run_id).image_digest == harness.cloud.images[build.request.image]


def test_a_relaunch_that_cannot_create_its_vm_records_failed_at_its_own_attempt(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)
    harness.cloud.stockout_zones = set(_ZONES)

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 1
    record = harness.record(first.run_id)
    assert (record.status, record.attempt) == (RunStatus.FAILED, 2)


# --- refusals ------------------------------------------------------------------


def test_a_succeeded_run_is_final_and_refused_naming_its_results(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.SUCCEEDED)
    harness.forget_requests()

    second = harness.launch_path(capsys, first.spec_path)

    _assert_refused_without_writing(harness, second)
    assert "succeeded" in second.err
    assert f"gs://bench-runs/{first.run_id}/" in second.err
    assert harness.cloud.created == []


@pytest.mark.parametrize("status", [*_RELAUNCHABLE, RunStatus.LAUNCHING, RunStatus.RUNNING])
def test_a_run_with_a_live_vm_is_refused_naming_the_instance_and_its_zone(
    harness: LaunchHarness, capsys: Capsys, status: RunStatus
) -> None:
    first = _first_launch(harness, capsys)
    if status is not RunStatus.LAUNCHING:
        harness.supervisor_records(first.run_id, status, vm_gone=False)
    harness.cloud.requests.clear()

    second = harness.launch_path(capsys, first.spec_path)

    _assert_refused_without_writing(harness, second)
    assert f"bench-{first.run_id}-1" in second.err
    assert "europe-west4-a" in second.err
    assert len(harness.cloud.created) == 1


def test_live_vms_are_looked_up_by_the_run_id_label_across_the_regions_zones(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)
    harness.forget_requests()

    harness.launch_path(capsys, first.spec_path)

    (listing,) = [r for r in harness.cloud.requests if r.operation == "list_instances"]
    assert listing.args == {
        "project": "bench-project",
        "zones": _ZONES,
        "label": LABEL_RUN_ID,
        "value": first.run_id,
    }


def test_an_unreadable_run_record_is_refused_naming_it(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    path = harness.write_spec("cpu-smoke", _spec())
    run_id = load_run_spec(path).run_id
    harness.cloud.put_object(harness.record_uri(run_id), b"{not json")
    harness.cloud.requests.clear()

    result = harness.launch_path(capsys, path)

    _assert_refused_without_writing(harness, result)
    assert harness.record_uri(run_id) in result.err


# --- a record whose VM vanished ------------------------------------------------


@pytest.mark.parametrize("status", [RunStatus.LAUNCHING, RunStatus.RUNNING])
def test_a_launching_or_running_record_without_a_vm_is_recorded_lost_then_relaunched(
    harness: LaunchHarness, capsys: Capsys, status: RunStatus
) -> None:
    first = _first_launch(harness, capsys)
    if status is RunStatus.RUNNING:
        harness.supervisor_records(first.run_id, status, vm_gone=False)
    harness.cloud.vanish()
    stale_generation = harness.record_generation(first.run_id)
    harness.forget_requests()

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 0, second.err
    record = harness.record(first.run_id)
    statuses = [t.status for t in record.history]
    assert statuses[-3:] == [status, RunStatus.LOST, RunStatus.LAUNCHING]
    lost = record.history[-2]
    assert lost.attempt == 1
    assert lost.instance == f"bench-{first.run_id}-1"
    assert "no VM" in lost.detail
    assert str(status) in lost.detail
    lost_generation, relaunch_generation = _record_writes(harness, first.run_id)
    assert lost_generation == stale_generation
    assert relaunch_generation != stale_generation
    assert record.attempt == 2


def test_a_stale_generation_on_the_lost_write_fails_the_launch_and_creates_no_vm(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.cloud.vanish()
    harness.forget_requests()
    harness.cloud.stale_write_uris.add(harness.record_uri(first.run_id))

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 1
    assert "generation" in second.err
    assert harness.cloud.created == []
    assert harness.record(first.run_id).status is RunStatus.LAUNCHING


def test_a_relaunch_checks_the_inputs_at_the_frozen_commit(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.FAILED)
    # The moved branch drops the startup script; the frozen commit still has it.
    harness.repo.remove(HOST_STARTUP_SCRIPT)
    harness.repo.commit("drop the startup script")
    harness.repo.push_main()

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 0, second.err


def test_a_relaunch_uploads_the_spec_copy_a_failed_first_attempt_never_wrote(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    path = harness.write_spec("cpu-smoke", _spec())
    run_id = load_run_spec(path).run_id
    copy_uri = f"gs://bench-runs/{run_id}/spec.yaml"
    harness.cloud.stale_write_uris.add(copy_uri)
    first = harness.launch_path(capsys, path)
    assert first.exit_code == 1
    assert harness.cloud.object_data(copy_uri) is None

    second = harness.launch_path(capsys, path)

    assert second.exit_code == 0, second.err
    assert harness.cloud.object_data(copy_uri) == path.read_bytes()


def test_the_live_vm_check_runs_after_a_relaunchs_image_build(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.supervisor_records(first.run_id, RunStatus.PREEMPTED)
    harness.cloud.images.clear()
    harness.forget_requests()

    harness.launch_path(capsys, first.spec_path)

    operations = harness.cloud.operations()
    assert operations.index("build_image") < operations.index("list_instances")


def test_a_record_moved_by_another_writer_before_the_lost_write_says_so(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    first = _first_launch(harness, capsys)
    harness.cloud.vanish()
    harness.cloud.stale_write_uris.add(harness.record_uri(first.run_id))

    second = harness.launch_path(capsys, first.spec_path)

    assert second.exit_code == 1
    assert "another writer" in second.err
    assert "launch again" in second.err
