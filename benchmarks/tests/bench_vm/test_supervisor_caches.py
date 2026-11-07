"""The persisted caches, end to end: a launched run, the fake cache bucket, and a real child process.

What is observed is what the outside sees: the objects in the cache bucket and
the archives' members, the files the child found in the fixed cache paths when
it started, the child's environment and argv, and the run record's cache bytes.
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError
from pydocs_eval.bench_vm.run_record import RunStatus
from pydocs_eval.bench_vm.supervisor import SupervisorTiming

from ._fixture_cache_producer import (
    CANONICAL_DB_NAME,
    CORPUS_SLUG,
    INDEX_ROW,
    MARKED_BUNDLE,
    UNMARKED_BUNDLE,
    UNPROMOTED_KEY,
)
from ._git_fixture import FIXTURE_CORPUS_DIR
from ._spec_dicts import before_after_spec, retrieval_spec
from ._supervise_harness import SuperviseHarness, SuperviseResult

_BUCKET = "gs://bench-cache/"
_UV_LOCK = b"version = 1\n# the image's lock\n"
_KEY = "a" * 64
# Short intervals; the restore may start transfers for the first half of the 20 s.
_CACHE_TIMING = SupervisorTiming(sync_interval_s=0.2, soft_deadline_s=20.0, stop_grace_s=5.0)


def _deps_partition(lock: bytes = _UV_LOCK, flavour: str = "cpu") -> str:
    return f"uv-lock-{hashlib.sha256(lock).hexdigest()[:16]}-{flavour}"


def _index_uri(dataset: str = "ds1000", key: str = _KEY, partition: str | None = None) -> str:
    partition = partition or _deps_partition()
    return f"{_BUCKET}datasets/{dataset}/bench-index/{partition}/{key}.tar"


def _tar(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _members(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {
            member.name: archive.extractfile(member).read()  # type: ignore[union-attr]
            for member in archive.getmembers()
            if member.isfile()
        }


def _rows(db_bytes: bytes, tmp_path: Path) -> list[str]:
    path = tmp_path / "unpacked.sqlite"
    path.write_bytes(db_bytes)
    with sqlite3.connect(path) as connection:
        return [row[0] for row in connection.execute("SELECT text FROM chunks")]


def _cache_objects(harness: SuperviseHarness) -> list[str]:
    root = harness.launch.cloud.root / "bench-cache"
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _ds1000_spec(**overrides: object) -> dict[str, object]:
    return retrieval_spec(dataset="ds1000", corpus_dir=FIXTURE_CORPUS_DIR, **overrides)


def _supervise(
    harness: SuperviseHarness,
    capsys: pytest.CaptureFixture[str],
    run_id: str,
    caches: str,
    exit_code: int = 0,
    push_wait_s: float = 15.0,
    timing: SupervisorTiming = _CACHE_TIMING,
) -> SuperviseResult:
    (harness.repo_dir / "uv.lock").parent.mkdir(parents=True, exist_ok=True)
    (harness.repo_dir / "uv.lock").write_bytes(_UV_LOCK)
    return harness.supervise(
        capsys,
        run_id,
        timing=timing,
        FIXTURE_CACHES=caches,
        FIXTURE_INDEX_KEY=_KEY,
        FIXTURE_GCS_ROOT=str(harness.launch.cloud.root),
        FIXTURE_TICKS="3",
        FIXTURE_TICK_S="0.2",
        FIXTURE_EXIT_CODE=str(exit_code),
        # How long the child keeps its index live waiting for the push; 0 where none may happen.
        FIXTURE_PUSH_WAIT_S=str(push_wait_s),
    )


# --- where the child's caches live ---------------------------------------------


def test_the_child_keeps_every_cache_under_the_fixed_cache_directory(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    started = _supervise(supervise_harness, capsys, run_id, "").started

    home = supervise_harness.cache_dir / "home"
    assert started["cache_env"] == {
        "HOME": str(home),
        "HF_HOME": str(home / ".cache" / "huggingface"),
        "FASTEMBED_CACHE_PATH": str(home / ".cache" / "fastembed"),
    }
    argv = started["argv"]
    assert argv[argv.index("--bench-cache") + 1] == "on"


# --- restore at boot -----------------------------------------------------------


def test_the_run_s_partitions_restore_into_the_fixed_paths_before_the_child_starts(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    archive = _tar({f"{_KEY}/index.sqlite": b"db", f"{_KEY}/index.tq": b"tq"})
    cloud.put_object(_index_uri(), archive)
    cloud.put_object(_index_uri(key="b" * 64, partition=_deps_partition(b"other")), b"x" * 9)
    cloud.put_object(_BUCKET + "datasets/ds1000/downloads/pydocs-mcp/ds1000/d.jsonl", b"{}\n")
    cloud.put_object(_BUCKET + "datasets/ds1000/judge/k.json", b'{"v": 1}')
    cloud.put_object(_BUCKET + "shared/huggingface/hub/w.bin", b"weights")
    cloud.put_object(_BUCKET + "shared/fastembed/m.onnx", b"onnx")
    cloud.put_object(_BUCKET + "datasets/repoqa/judge/other.json", b"{}")
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    result = _supervise(supervise_harness, capsys, run_id, "")

    assert result.exit_code == 0, result.err
    assert result.started["cache_files"] == [
        "home/.cache/fastembed/m.onnx",
        "home/.cache/huggingface/hub/w.bin",
        "home/.cache/pydocs-mcp/ds1000/d.jsonl",
        f"home/.pydocs-mcp/bench/{_KEY}/index.sqlite",
        f"home/.pydocs-mcp/bench/{_KEY}/index.tq",
        "home/.pydocs-mcp/bench/jev/k.json",
    ]
    restored = len(archive) + len(b"{}\n") + len(b'{"v": 1}') + len(b"weights") + len(b"onnx")
    assert supervise_harness.record(run_id).cache_bytes_restored == restored


def test_an_image_with_other_dependencies_never_restores_the_reference_index(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    cloud.put_object(
        _index_uri(partition=_deps_partition(flavour="cuda")), _tar({f"{_KEY}/x": b"x"})
    )
    cloud.put_object(
        _index_uri(partition=_deps_partition(b"older lock")), _tar({f"{_KEY}/x": b"x"})
    )
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    result = _supervise(supervise_harness, capsys, run_id, "")

    assert result.started["cache_files"] == []
    assert supervise_harness.record(run_id).cache_bytes_restored == 0


def test_a_failed_restore_is_logged_and_the_run_still_runs(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    cloud.put_object(_BUCKET + "shared/huggingface/hub/w.bin", b"weights")
    cloud.restore_errors[_BUCKET + "shared/huggingface/"] = BenchCloudError("403 (scripted)")
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    result = _supervise(supervise_harness, capsys, run_id, "")

    assert result.exit_code == 0, result.err
    assert supervise_harness.record(run_id).status is RunStatus.SUCCEEDED
    assert "bench_vm_cache_restore_failed" in result.err


# --- pushes at each sync ---------------------------------------------------------


def test_a_promoted_index_entry_is_pushed_as_one_archive_of_its_live_database(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    result = _supervise(supervise_harness, capsys, run_id, "bench-index")

    assert result.exit_code == 0, result.err
    index_objects = [name for name in _cache_objects(supervise_harness) if "bench-index" in name]
    assert index_objects == [_index_uri().removeprefix(_BUCKET)]
    members = _members(supervise_harness.launch.cloud.object_data(_index_uri()) or b"")
    assert sorted(members) == [f"{_KEY}/index.sqlite", f"{_KEY}/index.tq"]
    # Packed from a consistent snapshot: the row still in the WAL is in it.
    assert _rows(members[f"{_KEY}/index.sqlite"], tmp_path) == [INDEX_ROW]
    assert UNPROMOTED_KEY not in " ".join(_cache_objects(supervise_harness))


def test_an_entry_already_in_the_bucket_is_never_uploaded_again(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    first = _tar({f"{_KEY}/index.sqlite": b"the first run's entry"})
    cloud.put_object(_index_uri(), first)
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    _supervise(supervise_harness, capsys, run_id, "bench-index")

    assert cloud.object_data(_index_uri()) == first
    uploads = [r.args["uri"] for r in cloud.requests if r.operation == "upload_object_if_absent"]
    assert _index_uri() not in uploads


def test_bench_index_entries_from_per_task_corpora_are_never_pushed(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "repoqa", retrieval_spec(dataset="repoqa"))

    result = _supervise(supervise_harness, capsys, run_id, "bench-index,downloads", push_wait_s=0)

    assert result.exit_code == 0, result.err
    objects = _cache_objects(supervise_harness)
    assert not [name for name in objects if "bench-index" in name or _KEY in name]
    assert "datasets/repoqa/judge/" + "c" * 64 + ".json" in objects


def test_download_caches_mirror_as_plain_directories_without_partial_files(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    result = _supervise(supervise_harness, capsys, run_id, "downloads")

    assert result.exit_code == 0, result.err
    assert _cache_objects(supervise_harness) == [
        "datasets/ds1000/downloads/pydocs-mcp/ds1000/ds1000.jsonl",
        "datasets/ds1000/judge/" + "c" * 64 + ".json",
        "shared/fastembed/model.onnx",
        "shared/huggingface/hub/models--x/weights.bin",
    ]
    pushed = len(b'{"task": 1}\n') + len(b'{"verdict": "pass"}') + len(b"onnx") + len(b"weights")
    assert supervise_harness.record(run_id).cache_bytes_pushed == pushed


def test_a_mirror_never_replaces_a_file_already_in_the_bucket(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    uri = _BUCKET + "shared/fastembed/model.onnx"
    cloud.put_object(uri, b"the first run's weights")
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())
    # The restore brought the first run's file down; the child then rewrites it.
    _supervise(supervise_harness, capsys, run_id, "downloads")

    assert cloud.object_data(uri) == b"the first run's weights"


@pytest.mark.parametrize("failing", ["the mirror's restore", "the partition's listing"])
def test_a_mirror_counts_only_the_files_it_added_when_its_restore_failed(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], failing: str
) -> None:
    cloud = supervise_harness.launch.cloud
    cloud.put_object(_BUCKET + "shared/fastembed/model.onnx", b"onnx")
    if failing == "the mirror's restore":
        cloud.restore_errors[_BUCKET + "shared/fastembed/"] = BenchCloudError("503 (scripted)")
    else:  # the push then lists the mirror itself
        cloud.list_errors[_BUCKET + "shared/"] = BenchCloudError("503 (scripted)")
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    # The child downloads the weights again; the no-clobber mirror skips them.
    result = _supervise(supervise_harness, capsys, run_id, "downloads")

    assert result.exit_code == 0, result.err
    pushed = len(b'{"task": 1}\n') + len(b'{"verdict": "pass"}') + len(b"weights")
    assert supervise_harness.record(run_id).cache_bytes_pushed == pushed


def test_the_restore_s_listing_is_the_only_one_a_run_makes(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    # Nothing in the bucket yet: the restore pulls nothing, the pushes upload everything.
    result = _supervise(supervise_harness, capsys, run_id, "downloads")

    assert result.exit_code == 0, result.err
    listed = [
        r.args["prefix"]
        for r in supervise_harness.launch.cloud.requests
        if r.operation == "list_objects"
    ]
    assert listed == [_BUCKET + "shared/", _BUCKET + "datasets/ds1000/"]


def test_a_failed_push_is_logged_and_the_run_still_succeeds(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    cloud.upload_errors[_index_uri()] = BenchCloudError("503 (scripted)")
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    result = _supervise(supervise_harness, capsys, run_id, "bench-index", push_wait_s=0)

    assert result.exit_code == 0, result.err
    assert supervise_harness.record(run_id).status is RunStatus.SUCCEEDED
    assert "bench_vm_cache_push_failed" in result.err


def test_the_restore_starts_no_transfer_once_its_share_of_the_deadline_is_spent(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    cloud.put_object(_index_uri(key="b" * 64), _tar({"b" * 64 + "/index.sqlite": b"db"}))
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())
    # No share of the soft deadline for the restore: its window is closed at once.
    closed = SupervisorTiming(
        sync_interval_s=0.2, soft_deadline_s=20.0, stop_grace_s=5.0, cache_restore_share=0.0
    )

    result = _supervise(
        supervise_harness, capsys, run_id, "bench-index", push_wait_s=0, timing=closed
    )

    assert result.exit_code == 0, result.err
    assert result.started["cache_files"] == []  # the restore was deferred
    assert "bench_vm_cache_transfer_deferred" in result.err
    # The pushes are not the restore's: the run's own entry still reaches the bucket.
    assert cloud.object_data(_index_uri()) is not None


def test_the_count_write_after_an_ending_whose_answer_was_lost_keeps_the_ending(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    cloud = supervise_harness.launch.cloud
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())
    record_uri = supervise_harness.launch.record_uri(run_id)
    # The ending lands, but its answer is lost (the describe after the upload failed).
    cloud.lost_answer_when[record_uri] = lambda data: (
        json.loads(data)["status"] == RunStatus.SUCCEEDED
    )
    # No periodic sync: every cache byte is pushed after the ending, then counted.
    no_sync = SupervisorTiming(sync_interval_s=60.0, soft_deadline_s=20.0, stop_grace_s=5.0)

    _supervise(supervise_harness, capsys, run_id, "downloads", push_wait_s=0, timing=no_sync)

    assert record_uri not in cloud.lost_answer_when  # the ending's answer was lost
    record = supervise_harness.record(run_id)
    # A succeeded run stays final: the count write extends the ending, never `running`.
    assert [t.status for t in record.history][-1] is RunStatus.SUCCEEDED
    assert record.status is RunStatus.SUCCEEDED
    assert record.exit_code == 0
    assert record.cache_bytes_pushed > 0


def test_a_hub_cache_uploads_each_weight_once_through_its_snapshot(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    hub = supervise_harness.cache_dir / "home" / ".cache" / "huggingface" / "hub" / "models--x"
    blob = hub / "blobs" / ("d" * 40)
    blob.parent.mkdir(parents=True)
    blob.write_bytes(b"weights")
    (hub / "snapshots" / "rev").mkdir(parents=True)
    (hub / "snapshots" / "rev" / "model.bin").symlink_to(blob)
    run_id = supervise_harness.launch_run(capsys, "ds1000", _ds1000_spec())

    _supervise(supervise_harness, capsys, run_id, "")

    hub_objects = [name for name in _cache_objects(supervise_harness) if "models--x" in name]
    assert hub_objects == ["shared/huggingface/hub/models--x/snapshots/rev/model.bin"]
    assert supervise_harness.record(run_id).cache_bytes_pushed == len(b"weights")


# --- before/after workspaces -----------------------------------------------------


def _before_after_run(harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]) -> str:
    from ._git_fixture import FIXTURE_CONFIG, run_git

    repo = harness.launch.repo
    run_git(repo.path, "tag", "v-old", repo.head())
    spec = before_after_spec(configs=[FIXTURE_CONFIG], baseline_ref="v-old", candidate_ref="main")
    return harness.launch_run(capsys, "before-after", spec)


def test_a_before_after_corpus_is_pushed_once_its_bundle_is_marked_assembled(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    run_id = _before_after_run(supervise_harness, capsys)

    result = _supervise(supervise_harness, capsys, run_id, "before-after")

    assert result.exit_code == 0, result.err
    uri = f"{_BUCKET}datasets/repoqa/before-after/{MARKED_BUNDLE}.tar"
    archives = [name for name in _cache_objects(supervise_harness) if "before-after" in name]
    assert archives == [uri.removeprefix(_BUCKET)]
    assert UNMARKED_BUNDLE not in " ".join(_cache_objects(supervise_harness))
    members = _members(supervise_harness.launch.cloud.object_data(uri) or b"")
    bundle = f"bundles/{MARKED_BUNDLE}"
    assert sorted(members) == [
        f"{bundle}/.bundle-assembled.json",
        f"{bundle}/{CANONICAL_DB_NAME}",
        f"{bundle}/{Path(CANONICAL_DB_NAME).with_suffix('.tq').name}",
        f"sources/{CORPUS_SLUG}/a.py",
    ]
    # The bundle's database is the canonical build, never the copy a serve wrote to.
    assert _rows(members[f"{bundle}/{CANONICAL_DB_NAME}"], tmp_path) == []


def test_a_relaunch_on_a_fresh_vm_restores_the_bundle_at_the_same_paths(
    supervise_harness: SuperviseHarness, capsys: pytest.CaptureFixture[str]
) -> None:
    run_id = _before_after_run(supervise_harness, capsys)
    first = _supervise(supervise_harness, capsys, run_id, "before-after", exit_code=1)
    assert supervise_harness.record(run_id).status is RunStatus.FAILED, first.err
    pushed = supervise_harness.record(run_id).cache_bytes_pushed
    assert pushed > 0
    shutil.rmtree(supervise_harness.cache_dir)  # the next VM starts with an empty disk
    supervise_harness.relaunch(capsys, "before-after")

    second = _supervise(supervise_harness, capsys, run_id, "")

    workspace = "before-after/workspace/task-workspaces"
    assert {
        f"{workspace}/sources/{CORPUS_SLUG}/a.py",
        f"{workspace}/bundles/{MARKED_BUNDLE}/{CANONICAL_DB_NAME}",
        f"{workspace}/bundles/{MARKED_BUNDLE}/.bundle-assembled.json",
    } <= set(second.started["cache_files"])
    after = supervise_harness.record(run_id)
    assert after.cache_bytes_restored > 0
    assert after.cache_bytes_pushed == pushed  # nothing new: nothing pushed twice
