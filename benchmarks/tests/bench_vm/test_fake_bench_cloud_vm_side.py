"""``FakeBenchCloud``'s VM-side calls behave like the real ones, so supervisor tests can trust it."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, BenchCloudError

from ._fakes import FakeBenchCloud

_STATE_URI = "gs://bench-runs/r/state/"
_EXCLUDE = r".*\.tmp$"


def _write(root: Path, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_a_sync_uploads_only_what_changed_since_the_last_one(tmp_path: Path) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    _write(source, "ledger.jsonl", "a\n")
    _write(source, "logs/child.log", "line\n")
    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)
    _write(source, "ledger.jsonl", "a\nb\n")

    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)

    assert cloud.syncs[0].uploaded == ("ledger.jsonl", "logs/child.log")
    assert cloud.syncs[1].uploaded == ("ledger.jsonl",)
    assert cloud.object_data(_STATE_URI + "ledger.jsonl") == b"a\nb\n"


def test_a_sync_skips_excluded_files(tmp_path: Path) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    _write(source, "report.md.tmp", "half")

    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)

    assert cloud.object_data(_STATE_URI + "report.md.tmp") is None


def test_a_sync_never_deletes_what_the_source_no_longer_has(tmp_path: Path) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    _write(source, "old.jsonl", "kept\n")
    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)
    (source / "old.jsonl").unlink()

    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)

    assert cloud.object_data(_STATE_URI + "old.jsonl") == b"kept\n"


def test_metadata_reads_answer_from_the_scripted_metadata_and_none_otherwise(
    tmp_path: Path,
) -> None:
    cloud = FakeBenchCloud(root=tmp_path, instance_metadata={"instance/name": "bench-r-1"})

    assert cloud.read_instance_metadata("instance/name") == "bench-r-1"
    assert cloud.read_instance_metadata("instance/attributes/absent") is None


def test_a_delete_is_recorded(tmp_path: Path) -> None:
    cloud: BenchCloud = FakeBenchCloud(root=tmp_path)

    cloud.delete_instance("bench-project", "europe-west4-a", "bench-r-1")

    assert isinstance(cloud, FakeBenchCloud)
    assert cloud.deleted == [("bench-project", "europe-west4-a", "bench-r-1")]


def test_a_restore_pulls_every_object_under_the_prefix_into_the_directory(
    tmp_path: Path,
) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    _write(source, "ledger.jsonl", "a\n")
    _write(source, "results/run.jsonl", "r\n")
    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)
    cloud.put_object("gs://bench-runs/other/state/ledger.jsonl", b"not this run\n")
    destination = tmp_path / "relaunched" / "state"

    cloud.restore_directory(_STATE_URI, destination)

    assert sorted(p.relative_to(destination).as_posix() for p in destination.rglob("*.jsonl")) == [
        "ledger.jsonl",
        "results/run.jsonl",
    ]
    assert (destination / "results" / "run.jsonl").read_text(encoding="utf-8") == "r\n"
    assert cloud.restores == [(_STATE_URI, destination)]


def test_a_restore_from_an_empty_prefix_restores_nothing(tmp_path: Path) -> None:
    cloud, destination = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"

    cloud.restore_directory(_STATE_URI, destination)

    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_a_scripted_restore_error_raises_and_restores_nothing(tmp_path: Path) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    _write(source, "ledger.jsonl", "a\n")
    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)
    cloud.restore_errors[_STATE_URI] = BenchCloudError("restoring: 403 (scripted)")
    destination = tmp_path / "relaunched"

    with pytest.raises(BenchCloudError, match="403"):
        cloud.restore_directory(_STATE_URI, destination)

    assert not (destination / "ledger.jsonl").exists()


def test_a_restore_skips_the_objects_its_exclude_pattern_matches(tmp_path: Path) -> None:
    cloud, source = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    _write(source, "ledger.jsonl", "a\n")
    _write(source, "logs/child-attempt-1.log", "attempt 1\n")
    cloud.sync_directory(source, _STATE_URI, exclude_pattern=_EXCLUDE)
    destination = tmp_path / "relaunched"

    cloud.restore_directory(_STATE_URI, destination, exclude_pattern=r"logs/.*")

    assert [p.name for p in destination.rglob("*") if p.is_file()] == ["ledger.jsonl"]
