"""``FakeBenchCloud``'s cache operations behave like the real ones, so the supervisor's cache tests can trust it.

The cache bucket is never overwritten: an archive upload lands only where no
object exists, and a directory mirror adds files without replacing any.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError, ListedObject

from ._fakes import FakeBenchCloud

_PREFIX = "gs://bench-cache/datasets/ds1000/"
_EXCLUDE = r".*\.tmp$"


def _write(root: Path, relative: str, data: bytes) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_the_fake_is_a_bench_cloud(tmp_path: Path) -> None:
    cloud: BenchCloud = FakeBenchCloud(root=tmp_path / "gcs")

    assert isinstance(cloud, FakeBenchCloud)


def test_an_upload_lands_where_no_object_exists(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path / "gcs")
    archive = _write(tmp_path, "entry.tar", b"archive bytes")

    uploaded = cloud.upload_object_if_absent(archive, _PREFIX + "bench-index/k.tar")

    assert uploaded is True
    assert cloud.object_data(_PREFIX + "bench-index/k.tar") == b"archive bytes"


def test_an_upload_never_overwrites_an_existing_object(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path / "gcs")
    cloud.put_object(_PREFIX + "bench-index/k.tar", b"the first run's entry")
    archive = _write(tmp_path, "entry.tar", b"another run's entry")

    uploaded = cloud.upload_object_if_absent(archive, _PREFIX + "bench-index/k.tar")

    assert uploaded is False
    assert cloud.object_data(_PREFIX + "bench-index/k.tar") == b"the first run's entry"


def test_a_scripted_upload_error_raises_and_lands_nothing(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path / "gcs")
    cloud.upload_errors[_PREFIX + "k.tar"] = BenchCloudError("uploading: 503 (scripted)")

    with pytest.raises(BenchCloudError, match="503"):
        cloud.upload_object_if_absent(_write(tmp_path, "e.tar", b"x"), _PREFIX + "k.tar")

    assert cloud.object_data(_PREFIX + "k.tar") is None


def test_a_listing_names_every_object_under_the_prefix_with_its_size(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path / "gcs")
    cloud.put_object(_PREFIX + "judge/a.json", b"{}")
    cloud.put_object(_PREFIX + "bench-index/lock/k.tar", b"12345")
    cloud.put_object("gs://bench-cache/datasets/repoqa/judge/b.json", b"other dataset")

    listed = cloud.list_objects(_PREFIX)

    assert listed == (
        ListedObject(uri=_PREFIX + "bench-index/lock/k.tar", size=5),
        ListedObject(uri=_PREFIX + "judge/a.json", size=2),
    )


def test_a_listing_of_an_empty_prefix_is_empty(tmp_path: Path) -> None:
    assert FakeBenchCloud(root=tmp_path / "gcs").list_objects(_PREFIX) == ()


def test_a_download_writes_the_object_to_the_destination(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path / "gcs")
    cloud.put_object(_PREFIX + "k.tar", b"archive bytes")
    destination = tmp_path / "staging" / "k.tar"

    cloud.download_object(_PREFIX + "k.tar", destination)

    assert destination.read_bytes() == b"archive bytes"


def test_a_download_of_a_missing_object_is_a_cloud_error_naming_it(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path / "gcs")

    with pytest.raises(BenchCloudError, match="k.tar"):
        cloud.download_object(_PREFIX + "k.tar", tmp_path / "k.tar")


def test_a_mirror_adds_new_files_and_never_replaces_an_existing_object(tmp_path: Path) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "judge"
    cloud.put_object(_PREFIX + "judge/a.json", b"the first answer")
    _write(source, "a.json", b"a different answer")
    _write(source, "b.json", b"a new answer")
    _write(source, "c.json.tmp", b"half-written")

    cloud.upload_directory_without_overwrite(source, _PREFIX + "judge/", exclude_pattern=_EXCLUDE)

    assert cloud.object_data(_PREFIX + "judge/a.json") == b"the first answer"
    assert cloud.object_data(_PREFIX + "judge/b.json") == b"a new answer"
    assert cloud.object_data(_PREFIX + "judge/c.json.tmp") is None
    assert cloud.mirrors[-1].uploaded == ("b.json",)
