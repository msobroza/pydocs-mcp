"""Pin JsonlExperimentTracker: one file per run; one JSON line per
``open_run`` / ``log_metric`` / ``log_artifact`` / ``close_run`` call.
Each line carries an ``_event`` discriminator so the file is a
self-describing stream — readers tail it without external schema."""

from __future__ import annotations

import json
from pathlib import Path

from pydocs_eval.serialization import tracker_registry
from pydocs_eval.trackers import JsonlExperimentTracker


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _open(
    tmp_path: Path, *, dataset: str = "repoqa@v1"
) -> tuple[JsonlExperimentTracker, object, Path]:
    tracker = JsonlExperimentTracker(output_dir=tmp_path)
    handle = tracker.open_run(
        system="pydocs",
        config_name="defaults",
        dataset=dataset,
        params={"k": "5"},
        tags={"git_sha": "abc"},
    )
    # WHY: the tracker chose the path; tests must locate the resulting
    # file via the output_dir glob rather than hard-coding the timestamp.
    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    return tracker, handle, files[0]


def test_open_run_writes_header_record(tmp_path: Path) -> None:
    tracker, handle, path = _open(tmp_path)
    tracker.close_run(handle, status="finished")

    records = _read_jsonl(path)
    header = records[0]
    assert header["_event"] == "run_start"
    assert header["system"] == "pydocs"
    assert header["config_name"] == "defaults"
    assert header["dataset"] == "repoqa@v1"
    assert header["params"] == {"k": "5"}
    assert header["tags"] == {"git_sha": "abc"}
    # WHY: timestamp documents when the run started — required for
    # ordering JSONL files across the results dir.
    assert "ts" in header


def test_log_metric_appends_one_record(tmp_path: Path) -> None:
    tracker, handle, path = _open(tmp_path)
    tracker.log_metric(handle, "recall@5", 0.42, step=3)
    tracker.close_run(handle, status="finished")

    metric_record = _read_jsonl(path)[1]
    assert metric_record == {
        "_event": "metric",
        "name": "recall@5",
        "value": 0.42,
        "step": 3,
    }


def test_log_artifact_appends_one_record(tmp_path: Path) -> None:
    tracker, handle, path = _open(tmp_path)
    artifact = tmp_path / "report.json"
    artifact.write_text("{}")
    tracker.log_artifact(handle, artifact, name="report")
    tracker.close_run(handle, status="finished")

    artifact_record = _read_jsonl(path)[1]
    assert artifact_record == {
        "_event": "artifact",
        "name": "report",
        "path": str(artifact),
    }


def test_log_artifact_defaults_name_to_filename(tmp_path: Path) -> None:
    tracker, handle, path = _open(tmp_path)
    artifact = tmp_path / "report.json"
    artifact.write_text("{}")
    tracker.log_artifact(handle, artifact)
    tracker.close_run(handle, status="finished")

    artifact_record = _read_jsonl(path)[1]
    assert artifact_record["name"] == "report.json"


def test_close_run_finished_appends_end_record(tmp_path: Path) -> None:
    tracker, handle, path = _open(tmp_path)
    tracker.close_run(handle, status="finished")

    records = _read_jsonl(path)
    assert records[-1] == {"_event": "run_end", "status": "finished"}


def test_close_run_failed_appends_end_record(tmp_path: Path) -> None:
    tracker, handle, path = _open(tmp_path)
    tracker.close_run(handle, status="failed")

    records = _read_jsonl(path)
    assert records[-1] == {"_event": "run_end", "status": "failed"}


def test_close_run_is_idempotent(tmp_path: Path) -> None:
    # WHY: the runner's try/finally may call close_run twice on the failure
    # path — a second close must not raise.
    tracker, handle, _path = _open(tmp_path)
    tracker.close_run(handle, status="failed")
    tracker.close_run(handle, status="failed")  # must not raise


def test_dataset_slug_replaces_at_sign(tmp_path: Path) -> None:
    # WHY: ``@`` in filenames trips POSIX filename heuristics in some tools;
    # the slug must rewrite it to ``_at_`` so ``repoqa@v1`` lands as a clean
    # filename.
    _tracker, _handle, path = _open(tmp_path, dataset="repoqa@v1")
    assert "@" not in path.name
    assert "_at_" in path.name


def test_registered_in_tracker_registry() -> None:
    tracker = tracker_registry.build("jsonl")
    assert isinstance(tracker, JsonlExperimentTracker)
    # WHY: tracker.name is the registry key — runners log it into run tags
    # so the JSONL file stays self-describing.
    assert tracker.name == "jsonl"
