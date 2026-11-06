"""``GcloudBenchCloud``'s image build and VM listing: the argv, the answers, the waits, the retries.

Nothing here runs ``gcloud``: a ``ScriptedCommandRunner`` replays its answers,
and a ``ManualClock`` stands in for the monotonic clock and the sleep between polls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, ImageBuildError, ListedInstance
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud
from pydocs_eval.bench_vm.image_build_request import ImageBuildRequest

from ._fakes import ScriptedCommandRunner, failed, ok

_IMAGE = "europe-west4-docker.pkg.dev/bench-project/bench/bench-cpu:" + "c" * 40
_LOG_URL = "https://console.cloud.google.com/cloud-build/builds;region=europe-west4/b-1"


@dataclass
class ManualClock:
    """A monotonic clock that only moves when the adapter sleeps."""

    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _cloud(*script: object) -> tuple[GcloudBenchCloud, ScriptedCommandRunner, ManualClock]:
    runner = ScriptedCommandRunner(script=list(script))
    clock = ManualClock()
    cloud = GcloudBenchCloud(runner=runner, sleep=clock.sleep, monotonic=clock.monotonic)
    return cloud, runner, clock


def _request(tmp_path: Path, *, wait_timeout_s: float = 9000.0) -> ImageBuildRequest:
    return ImageBuildRequest(
        project="bench-project",
        region="europe-west4",
        image=_IMAGE,
        source_dir=tmp_path / "source",
        config=tmp_path / "cloudbuild.yaml",
        substitutions={
            "_COMMIT": "c" * 40,
            "_FLAVOUR": "cpu",
            "_REPOSITORY": "europe-west4-docker.pkg.dev/bench-project/bench",
        },
        wait_timeout_s=wait_timeout_s,
    )


def _build(status: str, build_id: str = "b-1") -> str:
    return json.dumps({"id": build_id, "status": status, "logUrl": _LOG_URL})


# --- image build -------------------------------------------------------------------


def test_a_build_is_submitted_async_in_the_region_with_the_commits_config(tmp_path: Path) -> None:
    cloud, runner, _ = _cloud(ok(_build("QUEUED")), ok(_build("SUCCESS")))

    cloud.build_image(_request(tmp_path))

    submit = runner.calls[0].argv
    assert submit[:4] == ("gcloud", "builds", "submit", str(tmp_path / "source"))
    assert f"--config={tmp_path / 'cloudbuild.yaml'}" in submit
    assert "--region=europe-west4" in submit
    assert "--project=bench-project" in submit
    assert "--async" in submit
    assert (
        "--substitutions=_COMMIT=" + "c" * 40 + ",_FLAVOUR=cpu,"
        "_REPOSITORY=europe-west4-docker.pkg.dev/bench-project/bench"
    ) in submit
    describe = runner.calls[1].argv
    assert describe[:4] == ("gcloud", "builds", "describe", "b-1")
    assert "--region=europe-west4" in describe


def test_the_build_is_polled_until_it_succeeds(tmp_path: Path) -> None:
    cloud, runner, clock = _cloud(
        ok(_build("QUEUED")), ok(_build("WORKING")), ok(_build("WORKING")), ok(_build("SUCCESS"))
    )

    cloud.build_image(_request(tmp_path))

    assert [call.argv[2] for call in runner.calls] == ["submit", "describe", "describe", "describe"]
    assert clock.now > 0


@pytest.mark.parametrize("status", ["FAILURE", "INTERNAL_ERROR", "TIMEOUT", "CANCELLED", "EXPIRED"])
def test_a_build_ending_any_way_but_success_names_the_build_and_its_log(
    tmp_path: Path, status: str
) -> None:
    cloud, _, _ = _cloud(ok(_build("QUEUED")), ok(_build(status)))

    with pytest.raises(ImageBuildError) as raised:
        cloud.build_image(_request(tmp_path))

    error = raised.value
    assert (error.build_id, error.log_url, error.status) == ("b-1", _LOG_URL, status)
    assert "b-1" in str(error)
    assert _LOG_URL in str(error)
    assert _IMAGE in str(error)


def test_the_wait_for_a_build_has_a_timeout(tmp_path: Path) -> None:
    cloud, runner, clock = _cloud(ok(_build("QUEUED")), ok(_build("WORKING")))

    with pytest.raises(ImageBuildError) as raised:
        cloud.build_image(_request(tmp_path, wait_timeout_s=120.0))

    assert raised.value.build_id == "b-1"
    assert "120" in str(raised.value)
    assert _LOG_URL in str(raised.value)
    assert clock.now >= 120.0
    assert len(runner.calls) < 20


def test_a_rejected_submission_is_a_build_error_carrying_gclouds_words(tmp_path: Path) -> None:
    cloud, _, _ = _cloud(failed("ERROR: (gcloud.builds.submit) INVALID_ARGUMENT: bad config"))

    with pytest.raises(ImageBuildError, match="INVALID_ARGUMENT: bad config") as raised:
        cloud.build_image(_request(tmp_path))

    assert raised.value.build_id is None


def test_a_transient_describe_error_is_retried(tmp_path: Path) -> None:
    cloud, runner, _ = _cloud(
        ok(_build("QUEUED")), failed("ERROR: HTTPError 503"), ok(_build("SUCCESS"))
    )

    cloud.build_image(_request(tmp_path))

    assert len(runner.calls) == 3


def test_every_build_call_has_a_timeout(tmp_path: Path) -> None:
    cloud, runner, _ = _cloud(ok(_build("QUEUED")), ok(_build("SUCCESS")))

    cloud.build_image(_request(tmp_path))

    assert all(0 < call.timeout_s < 9000.0 for call in runner.calls)


def test_a_submit_answer_without_a_build_id_is_a_cloud_error(tmp_path: Path) -> None:
    cloud, _, _ = _cloud(ok(json.dumps({"status": "QUEUED"})))

    with pytest.raises(BenchCloudError, match="build id"):
        cloud.build_image(_request(tmp_path))


# --- VM listing --------------------------------------------------------------------


def _instance(name: str, zone: str, run_id: str, status: str = "RUNNING") -> dict[str, object]:
    return {
        "name": name,
        "zone": f"https://www.googleapis.com/compute/v1/projects/bench-project/zones/{zone}",
        "status": status,
        "labels": {"bench-run-id": run_id, "bench-attempt": "1"},
    }


def test_instances_are_listed_by_label_across_the_zones() -> None:
    answer = [_instance("bench-r-1", "europe-west4-b", "r", status="STOPPING")]
    cloud, runner, _ = _cloud(ok(json.dumps(answer)))

    listed = cloud.list_instances(
        "bench-project", ("europe-west4-a", "europe-west4-b"), label="bench-run-id", value="r"
    )

    assert listed == (ListedInstance(name="bench-r-1", zone="europe-west4-b", status="STOPPING"),)
    argv = runner.calls[0].argv
    assert argv[:4] == ("gcloud", "compute", "instances", "list")
    assert "--project=bench-project" in argv
    assert "--zones=europe-west4-a,europe-west4-b" in argv
    assert "--filter=labels.bench-run-id=r" in argv
    assert runner.calls[0].timeout_s > 0


def test_listing_keeps_only_exact_label_matches() -> None:
    answer = [
        _instance("bench-r-1", "europe-west4-a", "r"),
        _instance("bench-rx-1", "europe-west4-a", "rx"),
    ]
    cloud, _, _ = _cloud(ok(json.dumps(answer)))

    listed = cloud.list_instances(
        "bench-project", ("europe-west4-a",), label="bench-run-id", value="r"
    )

    assert [instance.name for instance in listed] == ["bench-r-1"]


def test_an_empty_listing_is_no_instances_and_errors_are_cloud_errors() -> None:
    cloud, _, _ = _cloud(ok("[]"))
    assert cloud.list_instances("p", ("z",), label="bench-run-id", value="r") == ()

    broken, _, _ = _cloud(failed("ERROR: (gcloud.compute.instances.list) PERMISSION_DENIED"))
    with pytest.raises(BenchCloudError, match="PERMISSION_DENIED"):
        broken.list_instances("p", ("z",), label="bench-run-id", value="r")


def test_a_transient_listing_error_is_retried() -> None:
    cloud, runner, _ = _cloud(failed("ERROR: HTTPError 503"), ok("[]"))

    assert cloud.list_instances("p", ("z",), label="bench-run-id", value="r") == ()
    assert len(runner.calls) == 2


def test_an_unrecognised_build_status_fails_at_once_naming_it(tmp_path: Path) -> None:
    cloud, runner, _ = _cloud(ok(_build("QUEUED")), ok(_build("SOMETHING_NEW")))

    with pytest.raises(BenchCloudError, match="SOMETHING_NEW"):
        cloud.build_image(_request(tmp_path))

    assert len(runner.calls) == 2


def test_a_build_that_outlives_the_wait_is_cancelled(tmp_path: Path) -> None:
    cloud, runner, _ = _cloud(ok(_build("QUEUED")), ok(_build("WORKING")))

    with pytest.raises(ImageBuildError, match="cancelled"):
        cloud.build_image(_request(tmp_path, wait_timeout_s=30.0))

    cancel = runner.calls[-1].argv
    assert cancel[:4] == ("gcloud", "builds", "cancel", "b-1")
    assert "--region=europe-west4" in cancel
