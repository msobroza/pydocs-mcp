"""Pin the JSONL tracker's named run (issue #420): the reopen uses the run the caller read.

``recorded_run`` parses the leg file once; ``open_named_run`` takes that
``RecordedRun`` instead of parsing the file a second time (finding F14), so
the ``run_resume`` count is the one the identity check saw, even if the file
changed in between.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.leg_identity import LegIdentity
from pydocs_eval.task_observation import TaskObservation
from pydocs_eval.trackers import JsonlExperimentTracker

_RUN_ID = "named"
_IDENTITY = LegIdentity(components={"config": "c"}, fingerprint="f" * 64)


def _observation(task_id: str) -> TaskObservation:
    return TaskObservation(
        task_id=task_id,
        scores={"mrr": 1.0},
        index_seconds=0.1,
        search_seconds=0.2,
        cache_hit=False,
        metadata={},
    )


def _open(tracker: JsonlExperimentTracker, run_key: str, recorded: object) -> object:
    return tracker.open_named_run(
        run_id=_RUN_ID,
        run_key=run_key,
        system="s",
        config_name="c",
        dataset="d@r1",
        params={},
        tags={},
        identity=_IDENTITY,
        recorded=recorded,
    )


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_reopen_counts_the_tasks_of_the_run_it_was_given(tmp_path: Path) -> None:
    tracker = JsonlExperimentTracker(output_dir=tmp_path)
    run_key = tracker.run_key(system="s", config_name="c", dataset_name="d")
    first = _open(tracker, run_key, None)
    tracker.record_task(first, _observation("t0"), step=0)
    tracker.close_run(first, status="failed")
    recorded = tracker.recorded_run(run_id=_RUN_ID, run_key=run_key)
    assert recorded is not None and list(recorded.tasks) == ["t0"]
    path = tmp_path / _RUN_ID / f"{run_key}.jsonl"
    # A record lands between the read and the reopen; the reopen must not re-read.
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"_event": "task", **_observation("t1").to_record(step=1)}) + "\n")

    handle = _open(tracker, run_key, recorded)
    tracker.close_run(handle, status="finished")

    (resume,) = [r for r in _records(path) if r["_event"] == "run_resume"]
    assert resume["recorded_tasks"] == 1


def test_no_recorded_run_creates_the_file_with_its_header(tmp_path: Path) -> None:
    tracker = JsonlExperimentTracker(output_dir=tmp_path)
    run_key = tracker.run_key(system="s", config_name="c", dataset_name="d")

    handle = _open(tracker, run_key, None)
    tracker.close_run(handle, status="finished")

    records = _records(tmp_path / _RUN_ID / f"{run_key}.jsonl")
    assert records[0]["_event"] == "run_start"
    assert records[0]["identity"] == {"components": {"config": "c"}, "fingerprint": "f" * 64}
    assert [r["_event"] for r in records] == ["run_start", "run_end"]
