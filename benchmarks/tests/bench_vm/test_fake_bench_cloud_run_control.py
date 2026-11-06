"""``FakeBenchCloud``'s run-control calls behave like the real ones, so the verb tests can trust it."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.bench_vm.bench_cloud import BenchCloud, LabelledInstance
from pydocs_eval.bench_vm.vm_request import LABEL_ATTEMPT, LABEL_RUN_ID

from ._fakes import FakeBenchCloud, fake_vm_request

_PREFIX = "gs://bench-runs/run-a/"


def _cloud(tmp_path: Path) -> FakeBenchCloud:
    return FakeBenchCloud(root=tmp_path / "gcs")


def test_the_fake_satisfies_the_port(tmp_path: Path) -> None:
    cloud: BenchCloud = _cloud(tmp_path)

    assert cloud.read_object_tail(_PREFIX + "absent.log", max_bytes=8) is None


def test_a_tail_read_returns_at_most_the_last_bytes(tmp_path: Path) -> None:
    cloud = _cloud(tmp_path)
    cloud.put_object(_PREFIX + "log", b"0123456789")

    assert cloud.read_object_tail(_PREFIX + "log", max_bytes=4) == b"6789"
    assert cloud.read_object_tail(_PREFIX + "log", max_bytes=100) == b"0123456789"


def test_a_prefix_download_copies_only_that_prefix_and_keeps_local_files(tmp_path: Path) -> None:
    cloud = _cloud(tmp_path)
    cloud.put_object(_PREFIX + "run.json", b"{}")
    cloud.put_object(_PREFIX + "state/results/a.jsonl", b"a\n")
    cloud.put_object("gs://bench-runs/run-ab/run.json", b"other")
    destination = tmp_path / "local" / "run-a"
    destination.mkdir(parents=True)
    (destination / "notes.md").write_text("mine", encoding="utf-8")

    cloud.download_prefix(_PREFIX, destination)

    local = (path for path in destination.rglob("*") if path.is_file())
    assert sorted(path.relative_to(destination).as_posix() for path in local) == [
        "notes.md",
        "run.json",
        "state/results/a.jsonl",
    ]


def test_labelled_listing_names_every_live_vm_carrying_the_label(tmp_path: Path) -> None:
    cloud = _cloud(tmp_path)
    cloud.create_instance(fake_vm_request("run-a", attempt=1, zone="europe-west4-a"))
    cloud.create_instance(fake_vm_request("run-b", attempt=2, zone="europe-west4-b"))
    cloud.create_instance(fake_vm_request("run-c", attempt=1, zone="europe-west4-c"))
    cloud.vanish("bench-run-c-1")

    listed = cloud.list_labelled_instances(
        "bench-project", ("europe-west4-a", "europe-west4-b", "europe-west4-c"), label=LABEL_RUN_ID
    )

    assert [(vm.name, vm.zone, vm.labels[LABEL_ATTEMPT]) for vm in listed] == [
        ("bench-run-a-1", "europe-west4-a", "1"),
        ("bench-run-b-2", "europe-west4-b", "2"),
    ]
    assert all(isinstance(vm, LabelledInstance) for vm in listed)
