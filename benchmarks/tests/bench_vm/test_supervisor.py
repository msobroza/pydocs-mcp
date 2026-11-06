"""The supervisor end to end: a launched run, the fake cloud, and a real child process.

Each test launches a spec through the real launcher, boots its VM on the fake
metadata server, and runs ``python -m pydocs_eval.bench_vm supervise`` with
``_fixture_child.py`` as the kind's process. What is observed is what the
outside sees: the run record's history, the requests the cloud received, the
child's argv, environment and output, and the exit code.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.run_spec import RunSpec
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._fixture_child import LEDGER_NAME, TEMPORARY_NAME, VERBATIM_STDERR, VERBATIM_STDOUT
from ._spec_dicts import retrieval_spec
from ._supervise_harness import SuperviseHarness

_TIMEOUT_TIMING = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=0.8, stop_grace_s=5.0)


def _operations_on(harness: SuperviseHarness, run_id: str) -> list[str]:
    """``sync`` for each state sync, ``record`` for each write of the run record, in order."""
    record_uri = harness.launch.record_uri(run_id)
    names = []
    for request in harness.launch.cloud.requests:
        if request.operation == "sync_directory":
            names.append("sync")
        elif request.operation == "write_object" and request.args["uri"] == record_uri:
            names.append("record")
    return names


# --- the success path ------------------------------------------------------------


def test_a_run_whose_child_exits_zero_goes_running_then_succeeded(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id)

    record = supervise_harness.record(run_id)
    assert result.exit_code == 0, result.err
    assert [t.status for t in record.history] == [
        RunStatus.LAUNCHING,
        RunStatus.RUNNING,
        RunStatus.SUCCEEDED,
    ]
    assert (record.status, record.exit_code) == (RunStatus.SUCCEEDED, 0)
    assert record.last_sync_at is not None


def test_running_records_the_host_facts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec(spot=False))

    supervise_harness.supervise(capsys, run_id)

    running = supervise_harness.record(run_id).history[1]
    vm = supervise_harness.vm
    assert (running.instance, running.zone, running.machine) == (
        vm.name,
        vm.zone,
        "n2-standard-8",
    )
    assert running.attempt == 1
    assert "gpu=none" in running.detail
    assert "spot=false" in running.detail
    assert f"image_digest={vm.metadata['bench-image-digest']}" in running.detail


def test_a_gpu_run_records_its_gpu_and_spot(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "gpu-smoke", retrieval_spec(gpu={}))

    supervise_harness.supervise(capsys, run_id)

    running = supervise_harness.record(run_id).history[1]
    assert running.machine == "g2-standard-8"
    assert "gpu=nvidia-l4x1" in running.detail
    assert "spot=true" in running.detail


def test_the_child_runs_from_the_repository_with_the_run_in_its_environment(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id)

    started = result.started
    assert started["cwd"] == str(supervise_harness.repo_dir)
    assert started["env"] == {
        "BENCH_RUN_ID": run_id,
        "BENCH_ATTEMPT": "1",
        "BENCH_STATE_DIR": str(supervise_harness.state_dir),
        "BENCH_CACHE_DIR": str(supervise_harness.cache_dir),
    }
    # Unbuffered, so the log in the state directory is current at every sync.
    assert started["pythonunbuffered"] == "1"
    state = supervise_harness.state_dir
    assert started["argv"][-6:] == [
        "--run-id",
        run_id,
        "--results-dir",
        str(state / "results"),
        "--report",
        str(state / "report.md"),
    ]


def test_state_syncs_on_the_injected_interval_while_the_child_runs(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    supervise_harness.supervise(capsys, run_id, FIXTURE_TICKS="8", FIXTURE_TICK_S="0.15")

    syncs = supervise_harness.launch.cloud.syncs
    # 1.2 s of child at a 0.2 s interval: several periodic syncs plus the final one.
    assert len(syncs) >= 4
    assert {sync.destination for sync in syncs} == {supervise_harness.state_uri(run_id)}
    assert {sync.source for sync in syncs} == {supervise_harness.state_dir}
    # Incremental: the growing ledger is uploaded again and again, mid-run.
    ledger_uploads = sum(LEDGER_NAME in sync.uploaded for sync in syncs)
    assert ledger_uploads >= 3


def test_a_finished_run_requests_its_own_vm_delete(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    supervise_harness.supervise(capsys, run_id)

    vm = supervise_harness.vm
    assert supervise_harness.launch.cloud.deleted == [(vm.project, vm.zone, vm.name)]
    operations = supervise_harness.launch.cloud.operations()
    assert operations[-1] == "delete_instance"


def test_self_delete_false_requests_no_delete(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec(self_delete=False))

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 0
    assert supervise_harness.record(run_id).status is RunStatus.SUCCEEDED
    assert supervise_harness.launch.cloud.deleted == []


# --- the state directory -----------------------------------------------------------


def test_the_childs_output_is_kept_verbatim_and_synced(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    supervise_harness.supervise(capsys, run_id)

    log = supervise_harness.state_dir / "logs" / "child-attempt-1.log"
    assert log.read_bytes() == VERBATIM_STDERR + VERBATIM_STDOUT
    synced = supervise_harness.launch.cloud.object_data(
        supervise_harness.state_uri(run_id) + "logs/child-attempt-1.log"
    )
    assert synced == VERBATIM_STDERR + VERBATIM_STDOUT


def test_the_sync_skips_temporary_files(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    supervise_harness.supervise(capsys, run_id, FIXTURE_TICKS="1")

    assert (supervise_harness.state_dir / TEMPORARY_NAME).exists()
    state_uri = supervise_harness.state_uri(run_id)
    cloud = supervise_harness.launch.cloud
    assert cloud.object_data(state_uri + TEMPORARY_NAME) is None
    assert cloud.object_data(state_uri + LEDGER_NAME) == b'{"task": 0}\n'


# --- the other endings ---------------------------------------------------------------


def test_a_failing_child_ends_failed_with_its_exit_code_after_a_final_sync(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id, FIXTURE_EXIT_CODE="3")

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert (record.status, record.exit_code) == (RunStatus.FAILED, 3)
    assert "exit code 3" in record.history[-1].detail
    assert _operations_on(supervise_harness, run_id)[-2:] == ["sync", "record"]
    assert len(supervise_harness.launch.cloud.deleted) == 1


def test_the_soft_deadline_stops_the_child_and_ends_timed_out_after_a_final_sync(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(
        capsys, run_id, timing=_TIMEOUT_TIMING, FIXTURE_TICKS="200", FIXTURE_TICK_S="0.1"
    )

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert record.status is RunStatus.TIMED_OUT
    assert result.signals == ["stopped"]
    assert record.exit_code == 143
    assert _operations_on(supervise_harness, run_id)[-2:] == ["sync", "record"]
    assert len(supervise_harness.launch.cloud.deleted) == 1


def test_a_child_that_ignores_the_stop_is_killed_after_the_grace(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    timing = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=0.5, stop_grace_s=0.3)

    supervise_harness.supervise(
        capsys,
        run_id,
        timing=timing,
        FIXTURE_TICKS="200",
        FIXTURE_TICK_S="0.1",
        FIXTURE_IGNORE_SIGTERM="1",
    )

    record = supervise_harness.record(run_id)
    assert record.status is RunStatus.TIMED_OUT
    assert record.exit_code == -9


# --- refusals ------------------------------------------------------------------------


def test_an_unknown_record_schema_version_is_refused_before_anything_runs(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    cloud, uri = supervise_harness.launch.cloud, supervise_harness.launch.record_uri(run_id)
    payload = json.loads(cloud.object_data(uri) or b"")
    cloud.put_object(uri, json.dumps({**payload, "schema_version": 99}).encode())
    writes_before = len([r for r in cloud.requests if r.operation == "write_object"])

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 2
    assert "got 99, expected 1" in result.err
    assert not result.started_path.exists()
    assert len([r for r in cloud.requests if r.operation == "write_object"]) == writes_before
    assert cloud.syncs == []
    assert cloud.deleted == []


def test_a_vm_without_its_run_metadata_is_refused_naming_the_key(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    del supervise_harness.launch.cloud.instance_metadata["instance/attributes/bench-run-id"]

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 2
    assert "bench-run-id" in result.err
    assert not result.started_path.exists()


def test_a_run_already_past_launching_is_refused(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.supervise(capsys, run_id)
    Path(supervise_harness.root / "child-started.json").unlink()

    again = supervise_harness.supervise(capsys, run_id)

    assert again.exit_code == 2
    assert "succeeded" in again.err
    assert not again.started_path.exists()


# --- timing ----------------------------------------------------------------------------


def test_the_default_timing_syncs_every_sync_interval_and_ends_before_max_runtime() -> None:
    spec = RunSpec.model_validate(retrieval_spec(max_runtime="6h", sync_interval="5m"))

    timing = SupervisorTiming.for_spec(spec, elapsed=timedelta(0))

    assert timing.sync_interval_s == 300
    # The margin leaves room for the stop grace and the final sync before the
    # platform deletes the VM at max_runtime.
    assert timing.soft_deadline_s + timing.stop_grace_s < 6 * 3600
    assert timing.soft_deadline_s >= 5 * 3600


def test_a_short_max_runtime_still_leaves_a_soft_deadline() -> None:
    spec = RunSpec.model_validate(retrieval_spec(max_runtime="10m", sync_interval="1m"))

    timing = SupervisorTiming.for_spec(spec, elapsed=timedelta(0))

    assert 0 < timing.soft_deadline_s < 600


# --- failures the supervisor must survive ---------------------------------------------


def test_a_cloud_error_while_reading_the_run_is_logged_and_starts_nothing(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.launch.cloud.metadata_errors["instance/zone"] = BenchCloudError(
        "reading instance metadata instance/zone: 3 attempts, last: Failed to connect"
    )

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 2
    event = json.loads(result.err.strip().splitlines()[-1])
    assert event["event"] == "bench_vm_supervisor_failed"
    assert "instance/zone" in event["detail"]
    assert not result.started_path.exists()


def test_a_stale_running_write_is_refused_before_the_child_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.launch.cloud.stale_write_uris.add(supervise_harness.launch.record_uri(run_id))

    result = supervise_harness.supervise(capsys, run_id)

    assert result.exit_code == 2
    assert "stale write" in result.err
    assert not result.started_path.exists()
    assert supervise_harness.record(run_id).status is RunStatus.LAUNCHING


def test_a_child_that_cannot_start_ends_failed_and_still_deletes_the_vm(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())

    result = supervise_harness.supervise(capsys, run_id, child_python=("/nonexistent/bin/python",))

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert (record.status, record.exit_code) == (RunStatus.FAILED, None)
    assert "could not start" in record.history[-1].detail
    assert len(supervise_harness.launch.cloud.deleted) == 1


def test_an_unexpected_error_after_running_still_ends_the_run_and_deletes_the_vm(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.cache_dir.parent.mkdir(parents=True)
    supervise_harness.cache_dir.write_text("a file where the cache directory goes")

    result = supervise_harness.supervise(capsys, run_id)

    record = supervise_harness.record(run_id)
    assert result.exit_code == 1
    assert record.status is RunStatus.FAILED
    assert "the supervisor failed" in record.history[-1].detail
    assert _operations_on(supervise_harness, run_id)[-2:] == ["sync", "record"]
    assert len(supervise_harness.launch.cloud.deleted) == 1


def test_the_soft_deadline_counts_from_the_launch_not_from_the_supervisor() -> None:
    spec = RunSpec.model_validate(retrieval_spec(max_runtime="6h", sync_interval="5m"))

    fresh = SupervisorTiming.for_spec(spec, elapsed=timedelta(0))
    after_boot = SupervisorTiming.for_spec(spec, elapsed=timedelta(minutes=20))

    assert fresh.soft_deadline_s - after_boot.soft_deadline_s == 20 * 60
    late = SupervisorTiming.for_spec(spec, elapsed=timedelta(hours=7))
    assert late.soft_deadline_s == 0


# --- a relaunched attempt ---------------------------------------------------------------


def test_a_relaunched_attempt_is_supervised_as_its_own_attempt_with_its_own_log(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "cpu-smoke", retrieval_spec())
    supervise_harness.supervise(capsys, run_id, FIXTURE_EXIT_CODE="3")
    supervise_harness.relaunch(capsys, "cpu-smoke")

    result = supervise_harness.supervise(capsys, run_id, FIXTURE_EXIT_CODE="0")

    record = supervise_harness.record(run_id)
    assert result.exit_code == 0, result.err
    assert [(t.status, t.attempt) for t in record.history] == [
        (RunStatus.LAUNCHING, 1),
        (RunStatus.RUNNING, 1),
        (RunStatus.FAILED, 1),
        (RunStatus.LAUNCHING, 2),
        (RunStatus.RUNNING, 2),
        (RunStatus.SUCCEEDED, 2),
    ]
    assert result.started["env"]["BENCH_ATTEMPT"] == "2"
    logs = supervise_harness.state_dir / "logs"
    assert sorted(p.name for p in logs.iterdir()) == ["child-attempt-1.log", "child-attempt-2.log"]
