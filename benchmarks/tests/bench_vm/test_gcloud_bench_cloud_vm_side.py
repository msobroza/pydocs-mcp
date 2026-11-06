"""``GcloudBenchCloud``'s VM-side calls: the state sync, metadata-server reads, self-delete.

Nothing here runs ``gcloud`` or ``curl``: a ``ScriptedCommandRunner`` replays their answers.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.bounded_command import CompletedCommand
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud

from ._fakes import ScriptedCommandRunner, failed, ok

_STATE_URI = "gs://bench-runs/cpu-smoke-0123abcd/state/"
_EXCLUDE = r".*\.tmp$"
_CURL_404 = "curl: (22) The requested URL returned error: 404"


def _cloud(*script: CompletedCommand | str) -> tuple[GcloudBenchCloud, ScriptedCommandRunner]:
    runner = ScriptedCommandRunner(script=list(script))
    return GcloudBenchCloud(runner=runner, sleep=lambda _s: None), runner


# --- directory sync -------------------------------------------------------------


def test_a_directory_sync_is_a_recursive_rsync_that_skips_the_excluded_files(
    tmp_path: Path,
) -> None:
    cloud, runner = _cloud(ok())

    cloud.sync_directory(tmp_path, _STATE_URI, exclude_pattern=_EXCLUDE)

    argv = runner.calls[0].argv
    assert argv[:5] == ("gcloud", "storage", "rsync", str(tmp_path), _STATE_URI)
    assert "--recursive" in argv
    assert f"--exclude={_EXCLUDE}" in argv


def test_a_directory_sync_never_deletes_in_gcs(tmp_path: Path) -> None:
    cloud, runner = _cloud(ok())

    cloud.sync_directory(tmp_path, _STATE_URI, exclude_pattern=_EXCLUDE)

    assert not any("delete" in arg for arg in runner.calls[0].argv)


def test_a_failed_sync_is_a_cloud_error_naming_the_destination(tmp_path: Path) -> None:
    cloud, _ = _cloud(failed("ERROR: 403 caller does not have storage.objects.create"))

    with pytest.raises(BenchCloudError, match=re.escape(_STATE_URI)):
        cloud.sync_directory(tmp_path, _STATE_URI, exclude_pattern=_EXCLUDE)


def test_a_sync_retries_a_transient_error(tmp_path: Path) -> None:
    cloud, runner = _cloud(failed("ERROR: HTTPError 503: backendError"), ok())

    cloud.sync_directory(tmp_path, _STATE_URI, exclude_pattern=_EXCLUDE)

    assert len(runner.calls) == 2


# --- instance metadata ----------------------------------------------------------


def test_a_metadata_read_asks_the_metadata_server_with_its_flavor_header() -> None:
    cloud, runner = _cloud(ok("cpu-smoke-0123abcd"))

    value = cloud.read_instance_metadata("instance/attributes/bench-run-id")

    assert value == "cpu-smoke-0123abcd"
    argv = runner.calls[0].argv
    assert argv[0] == "curl"
    assert "Metadata-Flavor: Google" in argv
    assert argv[-1] == (
        "http://metadata.google.internal/computeMetadata/v1/instance/attributes/bench-run-id"
    )


def test_a_metadata_key_the_server_does_not_have_reads_as_none() -> None:
    cloud, _ = _cloud(failed(_CURL_404, returncode=22))

    assert cloud.read_instance_metadata("instance/attributes/absent") is None


def test_an_unreachable_metadata_server_is_retried_then_a_cloud_error() -> None:
    unreachable = failed("curl: (7) Failed to connect to metadata.google.internal", returncode=7)
    cloud, runner = _cloud(unreachable)

    with pytest.raises(BenchCloudError, match="instance/zone"):
        cloud.read_instance_metadata("instance/zone")

    assert len(runner.calls) == 3


def test_a_metadata_read_has_a_timeout_on_the_call_and_in_curl() -> None:
    cloud, runner = _cloud(ok("x"))

    cloud.read_instance_metadata("instance/name")

    assert runner.calls[0].timeout_s > 0
    assert "--max-time" in runner.calls[0].argv


# --- instance delete ------------------------------------------------------------


def test_an_instance_delete_names_the_instance_its_zone_and_project() -> None:
    cloud, runner = _cloud(ok())

    cloud.delete_instance("bench-project", "europe-west4-b", "bench-cpu-smoke-0123abcd-1")

    argv = runner.calls[0].argv
    assert argv[:5] == ("gcloud", "compute", "instances", "delete", "bench-cpu-smoke-0123abcd-1")
    assert "--zone=europe-west4-b" in argv
    assert "--project=bench-project" in argv
    assert "--quiet" in argv


def test_a_delete_retried_after_a_transient_error_accepts_not_found() -> None:
    cloud, runner = _cloud(
        failed("ERROR: HTTPError 503"),
        failed("ERROR: The resource 'bench-cpu-smoke-0123abcd-1' was not found"),
    )

    cloud.delete_instance("bench-project", "europe-west4-b", "bench-cpu-smoke-0123abcd-1")

    assert len(runner.calls) == 2


def test_a_failed_delete_is_a_cloud_error_naming_the_instance() -> None:
    cloud, _ = _cloud(failed("ERROR: Required 'compute.instances.delete' permission"))

    with pytest.raises(BenchCloudError, match="bench-cpu-smoke-0123abcd-1"):
        cloud.delete_instance("bench-project", "europe-west4-b", "bench-cpu-smoke-0123abcd-1")
