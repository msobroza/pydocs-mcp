"""``GcloudBenchCloud``: the gcloud argv it sends, how it reads gcloud's answers, timeouts and retries.

Nothing here runs ``gcloud``: a ``ScriptedCommandRunner`` replays its answers.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from pydocs_eval.bench_vm.bench_cloud import (
    BenchCloudError,
    MachineTypeFacts,
    StaleGenerationError,
    StoredObject,
    ZoneStockoutError,
)
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.vm_request import (
    GpuAttachment,
    ProvisioningModel,
    TerminationAction,
    VmCreateRequest,
)

from ._fakes import ScriptedCommandRunner, failed, ok

_URI = "gs://bench-runs/cpu-smoke-0123abcd/run.json"
_NOT_FOUND = "ERROR: (gcloud.storage.objects.describe) The following URLs matched no objects"


def _cloud(*script: object) -> tuple[GcloudBenchCloud, ScriptedCommandRunner]:
    runner = ScriptedCommandRunner(script=list(script))
    return GcloudBenchCloud(runner=runner, sleep=lambda _s: None), runner


def _request(**overrides: object) -> VmCreateRequest:
    request = VmCreateRequest(
        project="bench-project",
        zone="europe-west4-a",
        name="bench-cpu-smoke-0123abcd-1",
        machine_type="n2-standard-8",
        provisioning_model=ProvisioningModel.SPOT,
        max_run_duration_s=3600,
        termination_action=TerminationAction.DELETE,
        external_ip=False,
        service_account="bench-vm@bench-project.iam.gserviceaccount.com",
        subnetwork="projects/bench-project/regions/europe-west4/subnetworks/bench",
        boot_image="projects/cos-cloud/global/images/cos-stable-1",
        boot_disk_gb=250,
        boot_disk_auto_delete=True,
        gpu=None,
        labels={"bench-run-id": "cpu-smoke-0123abcd", "bench-attempt": "1"},
        metadata={"bench-run-id": "cpu-smoke-0123abcd", "startup-script": "#!/bin/sh\necho a,b\n"},
    )
    return replace(request, **overrides)


# --- objects -------------------------------------------------------------------


def test_reading_an_absent_object_returns_none() -> None:
    cloud, runner = _cloud(failed(_NOT_FOUND))

    assert cloud.read_object(_URI) is None
    assert len(runner.calls) == 1


def test_reading_an_object_pins_the_generation_it_described() -> None:
    cloud, runner = _cloud(ok(json.dumps({"generation": "1700000000000017"})), ok("{}"))

    stored = cloud.read_object(_URI)

    assert stored == StoredObject(data=b"{}", generation=1700000000000017)
    assert runner.calls[0].argv[:4] == ("gcloud", "storage", "objects", "describe")
    assert f"{_URI}#1700000000000017" in runner.calls[1].argv


def test_a_write_sends_the_bytes_on_stdin_under_a_generation_precondition() -> None:
    cloud, runner = _cloud(ok(), ok(json.dumps({"generation": "42"})))

    generation = cloud.write_object(_URI, b"payload", if_generation_match=0)

    assert generation == 42
    write = runner.calls[0]
    assert write.argv[:3] == ("gcloud", "storage", "cp")
    assert "-" in write.argv
    assert _URI in write.argv
    assert "--if-generation-match=0" in write.argv
    assert write.stdin == b"payload"


def test_a_failed_precondition_raises_stale_generation_naming_the_object() -> None:
    cloud, _ = _cloud(failed("ERROR: HTTPError 412: At least one of the pre-conditions failed"))

    with pytest.raises(StaleGenerationError) as raised:
        cloud.write_object(_URI, b"x", if_generation_match=7)

    assert raised.value.uri == _URI
    assert raised.value.expected == 7


# --- machine types and images ------------------------------------------------------


def test_machine_type_facts_are_read_from_the_compute_api_answer() -> None:
    answer = {
        "name": "g2-standard-8",
        "guestCpus": 8,
        "memoryMb": 32768,
        "architecture": "X86_64",
        "accelerators": [{"guestAcceleratorType": "nvidia-l4", "guestAcceleratorCount": 1}],
    }
    cloud, runner = _cloud(ok(json.dumps(answer)))

    facts = cloud.describe_machine_type("bench-project", "europe-west4-a", "g2-standard-8")

    assert facts == MachineTypeFacts(
        name="g2-standard-8",
        architecture="X86_64",
        guest_cpus=8,
        memory_mb=32768,
        bundled_accelerators=(("nvidia-l4", 1),),
    )
    argv = runner.calls[0].argv
    assert argv[:4] == ("gcloud", "compute", "machine-types", "describe")
    assert "--zone=europe-west4-a" in argv
    assert "--project=bench-project" in argv


def test_an_unknown_machine_type_has_no_facts() -> None:
    cloud, _ = _cloud(
        failed(
            "ERROR: (gcloud.compute.machine-types.describe) Could not fetch "
            "resource:\n - The resource 'projects/p/zones/z/machineTypes/x' "
            "was not found"
        )
    )

    assert cloud.describe_machine_type("p", "europe-west4-a", "x") is None


def test_an_image_digest_is_read_from_its_tag_and_a_missing_tag_is_none() -> None:
    digest = "sha256:" + "c" * 64
    cloud, runner = _cloud(ok(json.dumps({"image_summary": {"digest": digest}})))
    image = "europe-west4-docker.pkg.dev/p/bench/bench-cpu:" + "a" * 40

    assert cloud.find_image_digest(image) == digest
    assert runner.calls[0].argv[:5] == ("gcloud", "artifacts", "docker", "images", "describe")

    missing, _ = _cloud(
        failed(
            "ERROR: (gcloud.artifacts.docker.images.describe) NOT_FOUND: "
            "Requested entity was not found."
        )
    )
    assert missing.find_image_digest(image) is None


# --- VM creation -------------------------------------------------------------------


def test_the_create_argv_carries_the_bench_vm_shape() -> None:
    cloud, runner = _cloud(ok())

    cloud.create_instance(_request())

    call = runner.calls[0]
    argv = call.argv
    assert argv[:5] == ("gcloud", "compute", "instances", "create", "bench-cpu-smoke-0123abcd-1")
    for flag in (
        "--project=bench-project",
        "--zone=europe-west4-a",
        "--machine-type=n2-standard-8",
        "--provisioning-model=SPOT",
        "--max-run-duration=3600s",
        "--instance-termination-action=DELETE",
        "--no-address",
        "--service-account=bench-vm@bench-project.iam.gserviceaccount.com",
        "--scopes=cloud-platform",
        "--subnet=projects/bench-project/regions/europe-west4/subnetworks/bench",
        "--image=projects/cos-cloud/global/images/cos-stable-1",
        "--boot-disk-size=250GB",
        "--boot-disk-auto-delete",
        "--labels=bench-run-id=cpu-smoke-0123abcd,bench-attempt=1",
    ):
        assert flag in argv
    assert not any(arg.startswith("--accelerator") for arg in argv)
    assert call.metadata_files == {
        "bench-run-id": "cpu-smoke-0123abcd",
        "startup-script": "#!/bin/sh\necho a,b\n",
    }


def test_a_gpu_the_machine_type_does_not_carry_is_attached_explicitly() -> None:
    cloud, runner = _cloud(ok(), ok())

    cloud.create_instance(_request(gpu=GpuAttachment("nvidia-l4", 1, False)))
    cloud.create_instance(_request(gpu=GpuAttachment("nvidia-l4", 1, True)))

    explicit, bundled = (call.argv for call in runner.calls)
    assert "--accelerator=type=nvidia-l4,count=1" in explicit
    assert "--maintenance-policy=TERMINATE" in explicit
    assert not any(arg.startswith("--accelerator") for arg in bundled)
    assert "--maintenance-policy=TERMINATE" in bundled


def test_standard_provisioning_keeps_the_max_run_duration_and_delete() -> None:
    cloud, runner = _cloud(ok())

    cloud.create_instance(_request(provisioning_model=ProvisioningModel.STANDARD))

    argv = runner.calls[0].argv
    assert "--provisioning-model=STANDARD" in argv
    assert "--max-run-duration=3600s" in argv
    assert "--instance-termination-action=DELETE" in argv


def test_a_zone_capacity_error_is_a_stockout() -> None:
    cloud, _ = _cloud(
        failed(
            "ERROR: (gcloud.compute.instances.create) Could not fetch resource:"
            "\n - The zone 'projects/p/zones/europe-west4-a' does not have enough"
            " resources available to fulfill the request. "
            "ZONE_RESOURCE_POOL_EXHAUSTED"
        )
    )

    with pytest.raises(ZoneStockoutError) as raised:
        cloud.create_instance(_request())

    assert raised.value.zone == "europe-west4-a"


def test_any_other_create_error_is_a_plain_cloud_error() -> None:
    cloud, _ = _cloud(failed("ERROR: Quota 'GPUS_ALL_REGIONS' exceeded. Limit: 0.0 globally."))

    with pytest.raises(BenchCloudError, match="GPUS_ALL_REGIONS") as raised:
        cloud.create_instance(_request())

    assert not isinstance(raised.value, ZoneStockoutError)


def test_a_create_retried_after_a_transient_error_accepts_already_exists() -> None:
    cloud, runner = _cloud(
        failed("ERROR: HTTPError 503: Service Unavailable"),
        failed("ERROR: The resource 'projects/p/zones/z/instances/bench-x-1' already exists"),
    )

    cloud.create_instance(_request())

    assert len(runner.calls) == 2


# --- timeouts, retries, identity ---------------------------------------------------


def test_transient_errors_are_retried_a_bounded_number_of_times() -> None:
    answer = json.dumps({"image_summary": {"digest": "sha256:" + "d" * 64}})
    cloud, runner = _cloud("timeout", failed("ERROR: HTTPError 503"), ok(answer))

    assert cloud.find_image_digest("r/bench-cpu:abc") == "sha256:" + "d" * 64
    assert len(runner.calls) == 3

    always, always_runner = _cloud("timeout")
    with pytest.raises(BenchCloudError, match="timed out"):
        always.find_image_digest("r/bench-cpu:abc")
    assert len(always_runner.calls) == 3


def test_every_call_has_a_timeout() -> None:
    cloud, runner = _cloud(ok(json.dumps({"generation": "1"})), ok("x"), ok())

    cloud.read_object(_URI)
    cloud.create_instance(_request())

    assert all(call.timeout_s > 0 for call in runner.calls)


def test_gcloud_runs_as_the_operator_session_never_a_key_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/key.json")
    monkeypatch.setenv("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE", "/tmp/key.json")
    cloud, runner = _cloud(ok(json.dumps({"generation": "1"})), ok("x"), ok())

    cloud.read_object(_URI)
    cloud.create_instance(_request())

    for call in runner.calls:
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in call.env
        assert "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE" not in call.env
        assert not any("key" in arg.lower() and "file" in arg.lower() for arg in call.argv)


def test_a_retried_write_whose_first_attempt_landed_is_not_stale() -> None:
    cloud, runner = _cloud(
        failed("ERROR: HTTPError 503: Service Unavailable"),
        failed("ERROR: HTTPError 412: At least one of the pre-conditions failed"),
        ok(json.dumps({"generation": "99"})),
        ok("payload"),
    )

    assert cloud.write_object(_URI, b"payload", if_generation_match=0) == 99
    assert f"{_URI}#99" in runner.calls[-1].argv


def test_a_retried_write_that_finds_other_bytes_is_stale() -> None:
    cloud, _ = _cloud(
        failed("ERROR: HTTPError 503: Service Unavailable"),
        failed("ERROR: HTTPError 412: At least one of the pre-conditions failed"),
        ok(json.dumps({"generation": "99"})),
        ok("someone else's"),
    )

    with pytest.raises(StaleGenerationError):
        cloud.write_object(_URI, b"payload", if_generation_match=0)


def test_a_missing_gcloud_binary_is_a_cloud_error_not_a_crash() -> None:
    def no_gcloud(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("gcloud")

    cloud = GcloudBenchCloud(runner=no_gcloud, sleep=lambda _s: None)  # type: ignore[arg-type]

    with pytest.raises(BenchCloudError, match="gcloud"):
        cloud.find_image_digest("r/bench-cpu:abc")
