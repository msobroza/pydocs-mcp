"""``launch``: a first launch end to end, against ``FakeBenchCloud`` and a real fixture git repo."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from dataclasses import replace

import pytest

from pydocs_eval.bench_vm.commit_layout import HOST_SHUTDOWN_SCRIPT, HOST_STARTUP_SCRIPT
from pydocs_eval.bench_vm.run_kinds import RunKind
from pydocs_eval.bench_vm.run_record import RUN_RECORD_SCHEMA_VERSION, RunStatus
from pydocs_eval.bench_vm.vm_request import (
    LABEL_ATTEMPT,
    LABEL_RUN_ID,
    METADATA_ATTEMPT,
    METADATA_CACHE_BUCKET,
    METADATA_IMAGE,
    METADATA_IMAGE_DIGEST,
    METADATA_RUN_ID,
    METADATA_RUNS_BUCKET,
    METADATA_SHUTDOWN_SCRIPT,
    METADATA_STARTUP_SCRIPT,
    GpuAttachment,
    ProvisioningModel,
    TerminationAction,
)

from ._fakes import FAKE_IMAGE_DIGEST
from ._git_fixture import FIXTURE_CONFIG, FIXTURE_CORPUS_DIR
from ._launch_harness import LaunchHarness
from ._spec_dicts import retrieval_spec

Capsys = pytest.CaptureFixture[str]


def _spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(
        **{"configs": [FIXTURE_CONFIG], "corpus_dir": FIXTURE_CORPUS_DIR, **overrides}
    )


# --- the success path --------------------------------------------------------


def test_a_first_launch_prints_the_run_id_results_uri_and_status_command(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert result.exit_code == 0, result.err
    assert f"run id: {result.run_id}" in result.out
    assert f"results: gs://bench-runs/{result.run_id}/" in result.out
    assert "make bench-vm-status RUN=runs/cpu-smoke.yaml" in result.out


def test_a_first_launch_freezes_the_commit_in_a_launching_record(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec())

    record = harness.record(result.run_id)
    commit = harness.repo.head()
    assert record.schema_version == RUN_RECORD_SCHEMA_VERSION
    assert record.run_id == result.run_id
    assert record.kind is RunKind.RETRIEVAL
    assert (record.git_ref, record.commit) == ("main", commit)
    assert record.image == f"europe-west4-docker.pkg.dev/bench-project/bench/bench-cpu:{commit}"
    assert record.image_digest == FAKE_IMAGE_DIGEST
    assert (record.status, record.attempt) == (RunStatus.LAUNCHING, 1)
    (launching,) = record.history
    assert launching.status is RunStatus.LAUNCHING
    assert launching.instance == f"bench-{result.run_id}-1"
    assert launching.machine == "n2-standard-8"
    assert launching.attempt == 1


def test_the_verbatim_spec_copy_sits_next_to_the_record_and_its_digest_is_recorded(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec())

    verbatim = result.spec_path.read_bytes()
    copy = harness.cloud.object_data(f"gs://bench-runs/{result.run_id}/spec.yaml")
    assert copy == verbatim
    expected_digest = "sha256:" + hashlib.sha256(verbatim).hexdigest()
    assert harness.record(result.run_id).spec_digest == expected_digest


def test_every_record_and_spec_write_carries_a_generation_precondition(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.launch(capsys, "cpu-smoke", _spec())

    writes = [r for r in harness.cloud.requests if r.operation == "write_object"]
    assert len(writes) == 2
    assert all(write.args["if_generation_match"] == 0 for write in writes)


def test_exactly_one_vm_is_created_with_the_bench_vm_shape(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec(max_runtime="1h", disk_gb=300))

    (request,) = harness.cloud.created
    assert request.name == f"bench-{result.run_id}-1"
    assert request.project == "bench-project"
    assert request.zone == "europe-west4-a"
    assert request.machine_type == "n2-standard-8"
    assert request.labels == {LABEL_RUN_ID: result.run_id, LABEL_ATTEMPT: "1"}
    assert request.provisioning_model is ProvisioningModel.SPOT
    assert request.max_run_duration_s == 3600
    assert request.termination_action is TerminationAction.DELETE
    assert request.external_ip is False
    assert request.service_account == "bench-vm@bench-project.iam.gserviceaccount.com"
    assert request.subnetwork.endswith("/subnetworks/bench")
    assert request.boot_image == "projects/cos-cloud/global/images/cos-stable-1"
    assert (request.boot_disk_gb, request.boot_disk_auto_delete) == (300, True)
    assert request.gpu is None


def test_the_vm_metadata_names_the_run_buckets_image_and_the_commits_host_scripts(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "cpu-smoke", _spec())

    (request,) = harness.cloud.created
    commit = harness.repo.head()
    image = f"europe-west4-docker.pkg.dev/bench-project/bench/bench-cpu@{FAKE_IMAGE_DIGEST}"
    assert request.metadata == {
        METADATA_RUN_ID: result.run_id,
        METADATA_ATTEMPT: "1",
        METADATA_RUNS_BUCKET: "bench-runs",
        METADATA_CACHE_BUCKET: "bench-cache",
        METADATA_IMAGE: image,
        METADATA_IMAGE_DIGEST: FAKE_IMAGE_DIGEST,
        METADATA_STARTUP_SCRIPT: "#!/bin/sh\necho startup stand-in first\n",
        METADATA_SHUTDOWN_SCRIPT: "#!/bin/sh\necho shutdown stand-in first\n",
    }
    assert commit == harness.record(result.run_id).commit


def test_host_scripts_come_from_the_run_commit_never_the_working_tree(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.repo.write(HOST_STARTUP_SCRIPT, "#!/bin/sh\necho EDITED, NOT COMMITTED\n")
    harness.repo.write(HOST_SHUTDOWN_SCRIPT, "#!/bin/sh\necho EDITED, NOT COMMITTED\n")

    harness.launch(capsys, "cpu-smoke", _spec())

    (request,) = harness.cloud.created
    assert "EDITED" not in request.metadata[METADATA_STARTUP_SCRIPT]
    assert "EDITED" not in request.metadata[METADATA_SHUTDOWN_SCRIPT]


def test_spot_false_asks_for_standard_provisioning(harness: LaunchHarness, capsys: Capsys) -> None:
    harness.launch(capsys, "cpu-standard", _spec(spot=False))

    (request,) = harness.cloud.created
    assert request.provisioning_model is ProvisioningModel.STANDARD
    assert request.termination_action is TerminationAction.DELETE


def test_a_gpu_run_boots_the_gpu_image_with_the_cuda_flavour_and_its_gpu(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    result = harness.launch(capsys, "gpu-smoke", retrieval_spec(configs=[FIXTURE_CONFIG], gpu={}))

    assert result.exit_code == 0, result.err
    (request,) = harness.cloud.created
    assert request.machine_type == "g2-standard-8"
    assert request.boot_image == "projects/deeplearning-platform-release/global/images/dlvm-1"
    assert request.gpu == GpuAttachment(
        accelerator_type="nvidia-l4", count=1, attached_by_machine_type=True
    )
    assert "/bench-cuda:" in harness.record(result.run_id).image


def test_a_gpu_on_a_machine_type_without_bundled_gpus_is_attached_explicitly(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    spec = retrieval_spec(configs=[FIXTURE_CONFIG], gpu={}, machine_type="n2-highmem-8")
    harness.launch(capsys, "gpu-n2", spec)

    (request,) = harness.cloud.created
    assert request.gpu == GpuAttachment(
        accelerator_type="nvidia-l4", count=1, attached_by_machine_type=False
    )


def test_a_gpu_request_the_machine_types_bundled_gpus_cannot_meet_is_refused(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    spec = retrieval_spec(configs=[FIXTURE_CONFIG], gpu={"count": 2})
    result = harness.launch(capsys, "gpu-two", spec)

    assert result.exit_code == 1
    assert "g2-standard-8" in result.err
    assert "nvidia-l4 x 2" in result.err
    assert harness.writes() == []
    assert harness.cloud.created == []


def test_deployment_names_come_from_the_names_source(
    harness: LaunchHarness, capsys: Capsys
) -> None:
    harness.names.names = replace(harness.names.names, runs_bucket="other-runs")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    assert harness.names.reads == 1
    assert harness.cloud.object_data(f"gs://other-runs/{result.run_id}/run.json") is not None


def test_the_launcher_logs_structured_json_events(
    harness: LaunchHarness, capsys: Capsys, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="pydocs_eval.bench_vm")

    result = harness.launch(capsys, "cpu-smoke", _spec())

    events = [json.loads(record.getMessage()) for record in caplog.records]
    created = [event for event in events if event["event"] == "bench_vm_instance_created"]
    assert created == [
        {
            "event": "bench_vm_instance_created",
            "run_id": result.run_id,
            "instance": f"bench-{result.run_id}-1",
            "zone": "europe-west4-a",
            "attempt": 1,
        }
    ]


def test_a_spec_edited_during_the_launch_is_refused(
    harness: LaunchHarness, capsys: Capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = harness.write_spec("cpu-smoke", _spec())
    original_read_bytes = Path.read_bytes
    reads: list[Path] = []

    def read_then_edit(self: Path) -> bytes:
        data = original_read_bytes(self)
        if self == path and not reads:
            reads.append(self)
            self.write_text(self.read_text() + "spot: false\n")
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_edit)

    result = harness.launch_path(capsys, path)

    assert result.exit_code == 1
    assert "changed while" in result.err
    assert harness.writes() == []
