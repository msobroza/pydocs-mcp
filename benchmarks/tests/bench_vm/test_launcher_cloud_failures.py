"""``launch`` against cloud failures: zone stockouts, other create errors, stale generations."""

from __future__ import annotations

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.run_spec_file import load_run_spec

from ._git_fixture import FIXTURE_CONFIG
from ._launch_harness import LaunchHarness
from ._spec_dicts import retrieval_spec

Capsys = pytest.CaptureFixture[str]


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(configs=[FIXTURE_CONFIG], **overrides)


def test_a_stockout_moves_the_launch_to_the_next_terraform_zone(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.stockout_zones = {"europe-west4-a"}

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 0, result.err
    assert harness.cloud.create_attempts() == ["europe-west4-a", "europe-west4-b"]
    assert [request.zone for request in harness.cloud.created] == ["europe-west4-b"]
    assert harness.record(result.run_id).status is RunStatus.LAUNCHING


def test_the_specs_zone_is_tried_first_then_the_regions_zones(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.stockout_zones = {"europe-west4-c"}

    result = harness.launch(capsys, "cpu-smoke", _spec(zone="europe-west4-c"))

    assert result.exit_code == 0, result.err
    assert harness.cloud.create_attempts() == ["europe-west4-c", "europe-west4-a"]


def test_any_other_create_error_fails_the_launch_and_records_failed(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.create_errors["europe-west4-a"] = BenchCloudError("QUOTA_EXCEEDED: GPUS_ALL")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 1
    assert harness.cloud.create_attempts() == ["europe-west4-a"]
    assert harness.cloud.created == []
    assert "QUOTA_EXCEEDED" in result.err
    record = harness.record(result.run_id)
    assert [t.status for t in record.history] == [RunStatus.LAUNCHING, RunStatus.FAILED]
    assert record.status is RunStatus.FAILED
    assert "QUOTA_EXCEEDED" in record.history[-1].detail
    assert record.history[-1].zone == "europe-west4-a"


def test_a_stockout_in_every_zone_records_failed_naming_the_zones(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    zones = {"europe-west4-a", "europe-west4-b", "europe-west4-c"}
    harness.cloud.stockout_zones = set(zones)

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 1
    assert sorted(harness.cloud.create_attempts()) == sorted(zones)
    record = harness.record(result.run_id)
    assert record.status is RunStatus.FAILED
    assert all(zone in record.history[-1].detail for zone in zones)


def test_the_failed_write_uses_the_generation_of_the_launching_write(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.create_errors["europe-west4-a"] = BenchCloudError("boom")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    record_writes = [
        r.args["if_generation_match"]
        for r in harness.cloud.requests
        if r.operation == "write_object" and r.args["uri"] == harness.record_uri(result.run_id)
    ]
    assert record_writes[0] == 0
    assert record_writes[1] != 0


def test_a_stale_generation_fails_the_record_write_and_creates_no_vm(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    path = harness.write_spec("cpu-smoke", _spec())
    uri = harness.record_uri(load_run_spec(path).run_id)
    harness.cloud.stale_write_uris.add(uri)

    result = harness.launch_path(capsys, path)

    assert result.exit_code == 1
    assert uri in result.err
    assert "generation" in result.err
    assert harness.cloud.created == []
    assert harness.cloud.object_data(uri) is None


def test_an_unexpected_error_after_the_record_is_written_still_records_failed(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.create_errors["europe-west4-a"] = OSError("metadata scratch dir is full")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 1
    assert "metadata scratch dir is full" in result.err
    assert harness.record(result.run_id).status is RunStatus.FAILED


def test_a_zone_without_the_machine_type_is_skipped_before_anything_is_written(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.machine_types_missing_in = {"europe-west4-b"}
    harness.cloud.stockout_zones = {"europe-west4-a"}

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 0, result.err
    assert harness.cloud.create_attempts() == ["europe-west4-a", "europe-west4-c"]


def test_no_zone_offering_the_machine_type_is_refused_before_any_write(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.cloud.machine_types_missing_in = {"europe-west4-a", "europe-west4-b", "europe-west4-c"}

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 1
    assert "n2-standard-8" in result.err
    assert harness.writes() == []
    assert harness.cloud.created == []
