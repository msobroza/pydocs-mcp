"""``GcloudBenchCloud``'s cache operations: listing, never-overwriting uploads, mirrors, restores.

Nothing here runs ``gcloud``: a ``ScriptedCommandRunner`` replays its answers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, ListedObject
from pydocs_eval.bench_vm.bounded_command import CompletedCommand
from pydocs_eval.bench_vm.gcloud_bench_cloud import GcloudBenchCloud

from ._fakes import ScriptedCommandRunner, failed, ok

_PREFIX = "gs://bench-cache/datasets/ds1000/"
_ARCHIVE_URI = _PREFIX + "bench-index/uv-lock-0123-cpu/" + "a" * 64 + ".tar"
_EXCLUDE = r".*\.tmp$"
_PRECONDITION = (
    "ERROR: HTTPError 412: At least one of the pre-conditions you specified did not hold."
)
_NO_MATCH = (
    f"ERROR: (gcloud.storage.objects.list) The following URLs matched no objects: {_PREFIX}**"
)


def _cloud(*script: CompletedCommand | str) -> tuple[GcloudBenchCloud, ScriptedCommandRunner]:
    runner = ScriptedCommandRunner(script=list(script))
    return GcloudBenchCloud(runner=runner, sleep=lambda _s: None), runner


# --- listing ---------------------------------------------------------------------


def test_a_listing_reads_every_object_under_the_prefix_with_its_size() -> None:
    answer = [
        {"name": "datasets/ds1000/judge/b.json", "size": "2"},
        {"name": "datasets/ds1000/bench-index/k.tar", "size": 4096},
    ]
    cloud, runner = _cloud(ok(json.dumps(answer)))

    listed = cloud.list_objects(_PREFIX)

    assert runner.calls[0].argv[:4] == ("gcloud", "storage", "objects", "list")
    assert runner.calls[0].argv[4] == _PREFIX + "**"
    assert listed == (
        ListedObject(uri=_PREFIX + "bench-index/k.tar", size=4096),
        ListedObject(uri=_PREFIX + "judge/b.json", size=2),
    )


def test_a_listing_of_a_prefix_with_no_objects_is_empty() -> None:
    cloud, _ = _cloud(failed(_NO_MATCH))

    assert cloud.list_objects(_PREFIX) == ()


def test_a_listing_with_an_unreadable_answer_is_a_cloud_error_naming_the_prefix() -> None:
    cloud, _ = _cloud(ok("not json"))

    with pytest.raises(BenchCloudError, match=re.escape(_PREFIX)):
        cloud.list_objects(_PREFIX)


# --- the never-overwriting upload ------------------------------------------------


def test_an_upload_carries_the_does_not_exist_precondition(tmp_path: Path) -> None:
    archive = tmp_path / "entry.tar"
    archive.write_bytes(b"x")
    cloud, runner = _cloud(ok())

    assert cloud.upload_object_if_absent(archive, _ARCHIVE_URI) is True

    argv = runner.calls[0].argv
    assert argv[:5] == ("gcloud", "storage", "cp", str(archive), _ARCHIVE_URI)
    assert "--if-generation-match=0" in argv


def test_an_upload_onto_an_existing_object_reports_it_and_overwrites_nothing(
    tmp_path: Path,
) -> None:
    cloud, runner = _cloud(failed(_PRECONDITION))

    assert cloud.upload_object_if_absent(tmp_path / "entry.tar", _ARCHIVE_URI) is False
    assert len(runner.calls) == 1


def test_an_upload_retries_a_transient_error_within_its_timeout(tmp_path: Path) -> None:
    cloud, runner = _cloud("timeout", failed("ERROR: HTTPError 503: backendError"), ok())

    assert cloud.upload_object_if_absent(tmp_path / "entry.tar", _ARCHIVE_URI) is True
    assert len(runner.calls) == 3
    assert all(0 < call.timeout_s <= 3600 for call in runner.calls)


def test_a_failed_upload_is_a_cloud_error_naming_the_object(tmp_path: Path) -> None:
    cloud, _ = _cloud(failed("ERROR: 403 caller does not have storage.objects.create"))

    with pytest.raises(BenchCloudError, match=re.escape(_ARCHIVE_URI)):
        cloud.upload_object_if_absent(tmp_path / "entry.tar", _ARCHIVE_URI)


# --- download and mirror ---------------------------------------------------


def test_a_download_copies_the_object_to_the_destination(tmp_path: Path) -> None:
    cloud, runner = _cloud(ok())
    destination = tmp_path / "staging" / "k.tar"

    cloud.download_object(_ARCHIVE_URI, destination)

    assert runner.calls[0].argv[:5] == ("gcloud", "storage", "cp", _ARCHIVE_URI, str(destination))
    assert destination.parent.is_dir()


def test_a_failed_download_is_a_cloud_error_naming_the_object(tmp_path: Path) -> None:
    cloud, _ = _cloud(failed("ERROR: 404 No such object"))

    with pytest.raises(BenchCloudError, match=re.escape(_ARCHIVE_URI)):
        cloud.download_object(_ARCHIVE_URI, tmp_path / "k.tar")


def test_a_mirror_is_a_recursive_no_clobber_rsync_that_skips_the_excluded_files(
    tmp_path: Path,
) -> None:
    cloud, runner = _cloud(ok())

    cloud.upload_directory_without_overwrite(tmp_path, _PREFIX + "judge/", exclude_pattern=_EXCLUDE)

    argv = runner.calls[0].argv
    assert argv[:5] == ("gcloud", "storage", "rsync", str(tmp_path), _PREFIX + "judge/")
    assert {"--recursive", "--no-clobber", f"--exclude={_EXCLUDE}"} <= set(argv)
    assert not any("delete" in arg for arg in argv)


def test_a_failed_mirror_is_a_cloud_error_naming_the_destination(tmp_path: Path) -> None:
    cloud, _ = _cloud(failed("ERROR: 403 caller does not have storage.objects.create"))

    with pytest.raises(BenchCloudError, match=re.escape(_PREFIX + "judge/")):
        cloud.upload_directory_without_overwrite(
            tmp_path, _PREFIX + "judge/", exclude_pattern=_EXCLUDE
        )
