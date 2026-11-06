"""The supervisor's record keeper and state sync, against ``FakeBenchCloud``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from pydocs_eval.bench_vm.bench_cloud import BenchCloudError, StaleGenerationError
from pydocs_eval.bench_vm.run_kinds import RunKind
from pydocs_eval.bench_vm.run_record import (
    RUN_RECORD_SCHEMA_VERSION,
    RunRecord,
    RunStatus,
    RunTransition,
    run_record_bytes,
)
from pydocs_eval.bench_vm.run_record_keeper import RunRecordKeeper
from pydocs_eval.bench_vm.state_sync import STATE_SYNC_EXCLUDE_PATTERN, StateSync

from ._fakes import FakeBenchCloud

_URI = "gs://bench-runs/r-0123abcd/run.json"
_AT = datetime(2026, 11, 6, 9, 0, tzinfo=UTC)


def _record(status: RunStatus = RunStatus.LAUNCHING) -> RunRecord:
    transition = RunTransition(
        status=status, at=_AT, attempt=1, instance="bench-r-1", zone=None, machine=None
    )
    return RunRecord(
        schema_version=RUN_RECORD_SCHEMA_VERSION,
        run_id="r-0123abcd",
        spec_digest="sha256:" + "0" * 64,
        kind=RunKind.RETRIEVAL,
        git_ref="main",
        commit="a" * 40,
        image="repo/bench-cpu:" + "a" * 40,
        image_digest="sha256:" + "ab" * 32,
        status=status,
        attempt=1,
        history=(transition,),
    )


def _keeper(cloud: FakeBenchCloud) -> RunRecordKeeper:
    record = _record()
    generation = cloud.put_object(_URI, run_record_bytes(record))
    return RunRecordKeeper(cloud=cloud, uri=_URI, record=record, generation=generation)


def test_a_write_whose_answer_was_lost_does_not_wedge_the_next_one(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path)
    keeper = _keeper(cloud)
    synced = keeper.record.model_copy(update={"last_sync_at": _AT})
    cloud.lost_answer_uris.add(_URI)
    with pytest.raises(BenchCloudError):
        keeper.write(synced)

    ended = synced.model_copy(update={"status": RunStatus.SUCCEEDED, "exit_code": 0})
    keeper.write(ended)

    assert cloud.object_data(_URI) == run_record_bytes(ended)


def test_a_record_another_writer_moved_still_refuses_the_write(tmp_path: Path) -> None:
    cloud = FakeBenchCloud(root=tmp_path)
    keeper = _keeper(cloud)
    lost = _record(RunStatus.LOST)
    cloud.put_object(_URI, run_record_bytes(lost))

    with pytest.raises(StaleGenerationError):
        keeper.write(keeper.record.model_copy(update={"last_sync_at": _AT}))

    assert cloud.object_data(_URI) == run_record_bytes(lost)


def test_the_state_sync_skips_before_after_product_worktrees(tmp_path: Path) -> None:
    cloud, state = FakeBenchCloud(root=tmp_path / "gcs"), tmp_path / "state"
    (state / "before-after" / "worktrees" / "abc" / "python").mkdir(parents=True)
    (state / "before-after" / "worktrees" / "abc" / "python" / "x.py").write_text("x = 1\n")
    (state / "before-after" / "queue.jsonl").write_text('{"task": 1}\n')
    sync = StateSync.for_run(cloud, state, "gs://bench-runs/r-0123abcd/")

    sync.push()

    assert cloud.syncs[0].uploaded == ("before-after/queue.jsonl",)


def test_the_exclude_pattern_survives_gcloud_list_flag_splitting() -> None:
    # gcloud may split a list-valued flag on commas; one comma would cut the regex.
    assert "," not in STATE_SYNC_EXCLUDE_PATTERN
