"""``FakeBenchCloud``'s VM-side calls behave like the real ones, so supervisor tests can trust it."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud

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
