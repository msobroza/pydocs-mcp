"""``--run-id``: an interrupted retrieval sweep resumes to the same aggregates (issue #420).

Every test drives the sweep seam in-process and observes the returned outcome
and the run files on disk; ``_resume_sweep`` holds the drivers and readers,
``_resume_fakes`` the named fakes. The identity refusals, damaged run files
and the CLI live in the sibling ``test_runner_resume_*`` files.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydocs_eval.leg_identity import COMPONENT_NAMES
from pydocs_eval.sweep import run_sweep
from pydocs_eval.trajectory.blob_store import canonical_json

from ._resume_fakes import (
    COUNTING_SYSTEM,
    FAKE_DATASET,
    FAKE_DATASET_SIZE,
    RECORDING_TRACKER,
    TASK_SCORE_METRIC,
    CountingFakeSystem,
    InjectedTaskFailure,
    RecordingTracker,
    fake_query,
    reset_resume_fakes,
)
from ._resume_sweep import (
    COMMIT_A,
    LEG,
    TIMESTAMPED,
    all_task_ids,
    events_of,
    interrupted,
    leg_file,
    quality_of,
    queries,
    read_records,
    sweep,
    sweep_kwargs,
    task_ids_of,
)

# ── Without --run-id nothing changes ─────────────────────────────────────


async def test_without_run_id_leg_file_keeps_its_timestamped_shape(
    tmp_path: Path, overlay: Path
) -> None:
    await sweep(tmp_path, overlay)

    (path,) = (tmp_path / "results").iterdir()
    assert TIMESTAMPED.match(path.name), path.name
    records = read_records(path)
    assert {r["_event"] for r in records} == {"run_start", "metric", "run_end"}
    assert "run_id" not in records[0]
    assert "identity" not in records[0]


# ── Refused before any leg runs ──────────────────────────────────────────


@pytest.mark.parametrize("run_id", ["", "a/b", "..", ".", "has space", "colon:id", "é"])
async def test_invalid_run_id_fails_before_any_leg_naming_value_and_shape(
    tmp_path: Path, overlay: Path, run_id: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        await sweep(tmp_path, overlay, run_id=run_id)

    assert repr(run_id) in str(excinfo.value)
    assert "A-Z a-z 0-9 . _ -" in str(excinfo.value)
    assert CountingFakeSystem.searched == []
    assert not (tmp_path / "results").exists()


async def test_colliding_leg_names_fail_at_start(tmp_path: Path, overlay: Path) -> None:
    twin = tmp_path / "other" / overlay.name
    twin.parent.mkdir()
    twin.write_text("")

    with pytest.raises(ValueError, match="share the leg name") as excinfo:
        await sweep(tmp_path, overlay, config_paths=(overlay, twin), run_id="collide")

    assert str(twin) in str(excinfo.value)
    assert CountingFakeSystem.searched == []
    assert not (tmp_path / "results").exists()


@pytest.mark.parametrize(
    "tracker_names",
    [(), (RECORDING_TRACKER,), ("jsonl", "jsonl")],
    ids=["no-tracker", "non-resumable-only", "two-resumable"],
)
async def test_run_id_needs_exactly_one_resumable_tracker(
    tmp_path: Path, overlay: Path, tracker_names: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError, match="exactly one tracker with the resume capability"):
        await sweep(tmp_path, overlay, tracker_names=tracker_names, run_id="needs-jsonl")

    assert CountingFakeSystem.searched == []
    assert RecordingTracker.calls == []


# ── The run file ─────────────────────────────────────────────────────────


async def test_run_id_leg_is_one_stable_file_with_identity_header_and_task_records(
    tmp_path: Path, overlay: Path
) -> None:
    await sweep(tmp_path, overlay, run_id="stable")

    path = leg_file(tmp_path, "stable")
    assert sorted((tmp_path / "results").rglob("*.jsonl")) == [path]
    records = read_records(path)
    header = records[0]
    assert header["_event"] == "run_start"
    assert header["run_id"] == "stable"
    identity = header["identity"]
    components = identity["components"]
    assert tuple(components) == COMPONENT_NAMES
    assert components["config_params"] == header["params"]
    assert components["commit"] == header["tags"]["git_sha"] == COMMIT_A
    assert components["metric_specs"] == [TASK_SCORE_METRIC]
    assert (components["dataset"], components["dataset_revision"]) == (FAKE_DATASET, "r1")
    expected = hashlib.sha256(canonical_json(components).encode("utf-8")).hexdigest()
    assert identity["fingerprint"] == expected

    tasks = events_of(records, "task")
    assert task_ids_of(records) == all_task_ids()
    assert [t["step"] for t in tasks] == list(range(FAKE_DATASET_SIZE))
    assert set(tasks[0]) >= {"scores", "index_seconds", "search_seconds", "cache_hit", "metadata"}
    # The task record is the commit point: it follows its task's metric lines.
    first_task = records.index(tasks[0])
    assert {r.get("step") for r in records[1:first_task]} == {0}


# ── Interrupt, then resume ───────────────────────────────────────────────


async def test_interrupted_sweep_resumes_to_bit_identical_aggregates(
    tmp_path: Path, overlay: Path
) -> None:
    uninterrupted = await sweep(tmp_path, overlay, run_id="uninterrupted")
    reset_resume_fakes()
    await interrupted(tmp_path, overlay, fail_at=3, run_id="resumed")

    resumed = await sweep(tmp_path, overlay, run_id="resumed")

    # Only tasks 3 and later were indexed and searched again.
    assert CountingFakeSystem.searched == queries(3)
    assert len(CountingFakeSystem.indexed) == FAKE_DATASET_SIZE - 3
    leg = resumed.legs[LEG]
    assert [o.task_id for o in leg.observations] == all_task_ids()
    assert leg.tasks_ran == resumed.tasks_ran == FAKE_DATASET_SIZE
    # Quality aggregates (mean + seeded bootstrap CI) are bit-identical to the
    # uninterrupted run; latency is wall-clock, so it is pinned below against
    # the recorded latencies instead.
    assert quality_of(resumed.results[LEG]) == quality_of(uninterrupted.results[LEG])

    records = read_records(leg_file(tmp_path, "resumed"))
    assert len(events_of(records, "run_start")) == 1
    (resume,) = events_of(records, "run_resume")
    assert resume["recorded_tasks"] == 3
    assert task_ids_of(records) == all_task_ids()
    # The first invocation's recorded observations were restored verbatim.
    recorded = {r["task_id"]: r for r in events_of(records, "task")}
    for obs in leg.observations[:3]:
        assert obs.search_seconds == recorded[obs.task_id]["search_seconds"]
        assert obs.scores == recorded[obs.task_id]["scores"]
    # One final aggregate block, then run_end: it covers the whole leg.
    aggregates = [r for r in records if r["_event"] == "metric" and r["step"] is None]
    assert {r["name"]: r["value"] for r in aggregates}[f"{TASK_SCORE_METRIC}_mean"] == (
        leg.aggregates[TASK_SCORE_METRIC][0]
    )
    assert records[-1] == {"_event": "run_end", "status": "finished"}
    assert records.index(aggregates[0]) > records.index(events_of(records, "task")[-1])


async def test_fully_recorded_leg_runs_nothing_and_reemits_equal_aggregates(
    tmp_path: Path, overlay: Path
) -> None:
    first = await sweep(tmp_path, overlay, run_id="done")
    reset_resume_fakes()

    again = await sweep(tmp_path, overlay, run_id="done")

    assert CountingFakeSystem.searched == []
    assert CountingFakeSystem.indexed == []
    assert again.results == first.results  # latency included: every value is a record
    assert again.legs[LEG].observations == first.legs[LEG].observations
    records = read_records(leg_file(tmp_path, "done"))
    (resume,) = events_of(records, "run_resume")
    assert resume["recorded_tasks"] == FAKE_DATASET_SIZE
    assert [r["status"] for r in events_of(records, "run_end")] == ["finished", "finished"]


async def test_limit_counts_recorded_tasks(tmp_path: Path, overlay: Path) -> None:
    await sweep(tmp_path, overlay, run_id="limited", limit=2)
    reset_resume_fakes()

    outcome = await sweep(tmp_path, overlay, run_id="limited", limit=4)

    assert CountingFakeSystem.searched == queries(2, 4)
    assert outcome.tasks_ran == 4
    assert [o.task_id for o in outcome.legs[LEG].observations] == all_task_ids(4)


async def test_run_sweep_takes_run_id_like_the_flag(tmp_path: Path, overlay: Path) -> None:
    await interrupted(tmp_path, overlay, fail_at=2, run_id="compat")

    results, tasks_ran = await run_sweep(**sweep_kwargs(tmp_path, overlay, run_id="compat"))

    assert CountingFakeSystem.searched == queries(2)
    assert tasks_ran == FAKE_DATASET_SIZE
    assert set(results) == {LEG}


async def test_every_leg_reports_every_task_after_resume(tmp_path: Path, overlay: Path) -> None:
    second = overlay.with_name("variant.yaml")
    second.write_text("log_level: warning\n")
    configs = (overlay, second)
    CountingFakeSystem.fail_on_query = fake_query(4)
    with pytest.raises(InjectedTaskFailure):
        await sweep(tmp_path, overlay, config_paths=configs, run_id="two-legs")
    reset_resume_fakes()

    outcome = await sweep(tmp_path, overlay, config_paths=configs, run_id="two-legs")

    # Leg one resumed from task 4; leg two had never started, so it ran in full.
    assert CountingFakeSystem.searched == queries(4) + queries(0)
    for key in (LEG, (COUNTING_SYSTEM, "variant")):
        assert [o.task_id for o in outcome.legs[key].observations] == all_task_ids()


async def test_non_resumable_tracker_sees_only_new_tasks_plus_full_aggregates(
    tmp_path: Path, overlay: Path
) -> None:
    trackers = ("jsonl", RECORDING_TRACKER)
    CountingFakeSystem.fail_on_query = fake_query(2)
    with pytest.raises(InjectedTaskFailure):
        await sweep(tmp_path, overlay, tracker_names=trackers, run_id="fanout")
    reset_resume_fakes()

    outcome = await sweep(tmp_path, overlay, tracker_names=trackers, run_id="fanout")

    metric_calls = [c for c in RecordingTracker.calls if c[0] == "log_metric"]
    assert {step for _, _, step in metric_calls if step is not None} == {2, 3, 4, 5}
    per_task_scores = [c for c in metric_calls if c[1] == TASK_SCORE_METRIC]
    assert [step for _, _, step in per_task_scores] == [2, 3, 4, 5]
    aggregate_names = {name for _, name, step in metric_calls if step is None}
    assert aggregate_names == {
        f"{TASK_SCORE_METRIC}_mean",
        f"{TASK_SCORE_METRIC}_ci_low",
        f"{TASK_SCORE_METRIC}_ci_high",
        "indexing_seconds_p50",
        "indexing_seconds_p95",
        "indexing_seconds_p99",
        "search_seconds_p50",
        "search_seconds_p95",
        "search_seconds_p99",
    }
    assert RecordingTracker.calls[-1] == ("close_run", "finished", None)
    assert len(outcome.legs[LEG].observations) == FAKE_DATASET_SIZE
