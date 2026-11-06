"""``--run-id``: an interrupted retrieval sweep resumes to the same aggregates (issue #420).

Every test drives the sweep seam — ``run_sweep_detailed`` / ``run_sweep``
in-process, ``python -m pydocs_eval.runner`` in a child — and observes the
returned outcome and the run files on disk. The in-process cases use the
named fakes in ``_resume_fakes``: a counting system that can fail on one
task, a six-task dataset, a per-task metric with uneven scores (so the
seeded bootstrap CI depends on observation order), and a tracker without
the resume capability.

The run header's commit comes from real git, pointed at a missing
directory, so the commit identity component is the
``COMMIT_SHA_ENV_VAR`` value each test sets — never whatever checkout the
suite happens to live in.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest
from pydocs_eval.leg_identity import COMPONENT_NAMES, LegIdentityMismatch
from pydocs_eval.reporting import ci_compare
from pydocs_eval.sweep import SweepOutcome, run_sweep, run_sweep_detailed
from pydocs_eval.sweep_support import COMMIT_SHA_ENV_VAR
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
    fake_task_id,
    reset_resume_fakes,
)

_TESTS_DIR = Path(__file__).parents[1]
_EVAL_SRC = _TESTS_DIR.parent / "src"
_REPOQA_FIXTURE = _TESTS_DIR / "fixtures" / "repoqa_mini.json"
_COMMIT_A = "a" * 40
_COMMIT_B = "b" * 40
_LEG = (COUNTING_SYSTEM, "baseline")
_LEG_FILE = f"{COUNTING_SYSTEM}_baseline_{FAKE_DATASET}.jsonl"
_SHA256 = re.compile(r"\b[0-9a-f]{64}\b")
_TIMESTAMPED = re.compile(rf"^{re.escape(COUNTING_SYSTEM)}_baseline_.*_\d{{8}}T\d{{6}}Z\.jsonl$")


@pytest.fixture(autouse=True)
def _isolated_fakes_and_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_resume_fakes()
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "no-such-git-dir"))
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, _COMMIT_A)
    yield
    reset_resume_fakes()


@pytest.fixture
def overlay(tmp_path: Path) -> Path:
    path = tmp_path / "configs" / "baseline.yaml"
    path.parent.mkdir()
    path.write_text("")
    return path


def _sweep_kwargs(tmp_path: Path, overlay: Path, **overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "systems": (COUNTING_SYSTEM,),
        "config_paths": (overlay,),
        "dataset_name": FAKE_DATASET,
        "tracker_kwargs": {"jsonl": {"output_dir": tmp_path / "results"}},
        "metric_specs": (TASK_SCORE_METRIC,),
        "bench_cache": False,
    }
    kwargs.update(overrides)
    return kwargs


async def _sweep(tmp_path: Path, overlay: Path, **overrides: object) -> SweepOutcome:
    return await run_sweep_detailed(**_sweep_kwargs(tmp_path, overlay, **overrides))


async def _interrupted(tmp_path: Path, overlay: Path, *, fail_at: int, run_id: str) -> None:
    """Run ``run_id`` until the counting system raises on task ``fail_at``."""
    CountingFakeSystem.fail_on_query = fake_query(fail_at)
    with pytest.raises(InjectedTaskFailure):
        await _sweep(tmp_path, overlay, run_id=run_id)
    reset_resume_fakes()


def _leg_file(tmp_path: Path, run_id: str) -> Path:
    return tmp_path / "results" / run_id / _LEG_FILE


def _records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _events(records: list[dict[str, object]], kind: str) -> list[dict[str, object]]:
    return [r for r in records if r["_event"] == kind]


def _task_ids(records: list[dict[str, object]]) -> list[object]:
    return [r["task_id"] for r in _events(records, "task")]


def _all_task_ids(count: int = FAKE_DATASET_SIZE) -> list[str]:
    return [fake_task_id(i) for i in range(count)]


def _queries(start: int, stop: int = FAKE_DATASET_SIZE) -> list[str]:
    return [fake_query(i) for i in range(start, stop)]


def _quality(aggregates: Mapping[str, tuple[float, float, float]]) -> object:
    return {k: v for k, v in aggregates.items() if not k.endswith("_seconds")}


# ── Without --run-id nothing changes ─────────────────────────────────────


async def test_without_run_id_leg_file_keeps_its_timestamped_shape(
    tmp_path: Path, overlay: Path
) -> None:
    await _sweep(tmp_path, overlay)

    (path,) = (tmp_path / "results").iterdir()
    assert _TIMESTAMPED.match(path.name), path.name
    records = _records(path)
    assert {r["_event"] for r in records} == {"run_start", "metric", "run_end"}
    assert "run_id" not in records[0]
    assert "identity" not in records[0]


# ── Refused before any leg runs ──────────────────────────────────────────


@pytest.mark.parametrize("run_id", ["", "a/b", "..", ".", "has space", "colon:id", "é"])
async def test_invalid_run_id_fails_before_any_leg_naming_value_and_shape(
    tmp_path: Path, overlay: Path, run_id: str
) -> None:
    with pytest.raises(ValueError) as excinfo:
        await _sweep(tmp_path, overlay, run_id=run_id)

    assert repr(run_id) in str(excinfo.value)
    assert "A-Z a-z 0-9 . _ -" in str(excinfo.value)
    assert CountingFakeSystem.searched == []
    assert not (tmp_path / "results").exists()


async def test_colliding_leg_names_fail_at_start(tmp_path: Path, overlay: Path) -> None:
    twin = tmp_path / "other" / overlay.name
    twin.parent.mkdir()
    twin.write_text("")

    with pytest.raises(ValueError, match="share the leg name") as excinfo:
        await _sweep(tmp_path, overlay, config_paths=(overlay, twin), run_id="collide")

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
        await _sweep(tmp_path, overlay, tracker_names=tracker_names, run_id="needs-jsonl")

    assert CountingFakeSystem.searched == []
    assert RecordingTracker.calls == []


# ── The run file ─────────────────────────────────────────────────────────


async def test_run_id_leg_is_one_stable_file_with_identity_header_and_task_records(
    tmp_path: Path, overlay: Path
) -> None:
    await _sweep(tmp_path, overlay, run_id="stable")

    path = _leg_file(tmp_path, "stable")
    assert sorted((tmp_path / "results").rglob("*.jsonl")) == [path]
    records = _records(path)
    header = records[0]
    assert header["_event"] == "run_start"
    assert header["run_id"] == "stable"
    identity = header["identity"]
    components = identity["components"]
    assert tuple(components) == COMPONENT_NAMES
    assert components["config_params"] == header["params"]
    assert components["commit"] == header["tags"]["git_sha"] == _COMMIT_A
    assert components["metric_specs"] == [TASK_SCORE_METRIC]
    assert (components["dataset"], components["dataset_revision"]) == (FAKE_DATASET, "r1")
    expected = hashlib.sha256(canonical_json(components).encode("utf-8")).hexdigest()
    assert identity["fingerprint"] == expected

    tasks = _events(records, "task")
    assert _task_ids(records) == _all_task_ids()
    assert [t["step"] for t in tasks] == list(range(FAKE_DATASET_SIZE))
    assert set(tasks[0]) >= {"scores", "index_seconds", "search_seconds", "cache_hit", "metadata"}
    # The task record is the commit point: it follows its task's metric lines.
    first_task = records.index(tasks[0])
    assert {r.get("step") for r in records[1:first_task]} == {0}


# ── Interrupt, then resume ───────────────────────────────────────────────


async def test_interrupted_sweep_resumes_to_bit_identical_aggregates(
    tmp_path: Path, overlay: Path
) -> None:
    uninterrupted = await _sweep(tmp_path, overlay, run_id="uninterrupted")
    reset_resume_fakes()
    await _interrupted(tmp_path, overlay, fail_at=3, run_id="resumed")

    resumed = await _sweep(tmp_path, overlay, run_id="resumed")

    # Only tasks 3 and later were indexed and searched again.
    assert CountingFakeSystem.searched == _queries(3)
    assert len(CountingFakeSystem.indexed) == FAKE_DATASET_SIZE - 3
    leg = resumed.legs[_LEG]
    assert [o.task_id for o in leg.observations] == _all_task_ids()
    assert leg.tasks_ran == resumed.tasks_ran == FAKE_DATASET_SIZE
    # Quality aggregates (mean + seeded bootstrap CI) are bit-identical to the
    # uninterrupted run; latency is wall-clock, so it is pinned below against
    # the recorded latencies instead.
    assert _quality(resumed.results[_LEG]) == _quality(uninterrupted.results[_LEG])

    records = _records(_leg_file(tmp_path, "resumed"))
    assert len(_events(records, "run_start")) == 1
    (resume,) = _events(records, "run_resume")
    assert resume["recorded_tasks"] == 3
    assert _task_ids(records) == _all_task_ids()
    # The first invocation's recorded observations were restored verbatim.
    recorded = {r["task_id"]: r for r in _events(records, "task")}
    for obs in leg.observations[:3]:
        assert obs.search_seconds == recorded[obs.task_id]["search_seconds"]
        assert obs.scores == recorded[obs.task_id]["scores"]
    # One final aggregate block, then run_end: it covers the whole leg.
    aggregates = [r for r in records if r["_event"] == "metric" and r["step"] is None]
    assert {r["name"]: r["value"] for r in aggregates}[f"{TASK_SCORE_METRIC}_mean"] == (
        leg.aggregates[TASK_SCORE_METRIC][0]
    )
    assert records[-1] == {"_event": "run_end", "status": "finished"}
    assert records.index(aggregates[0]) > records.index(_events(records, "task")[-1])


async def test_fully_recorded_leg_runs_nothing_and_reemits_equal_aggregates(
    tmp_path: Path, overlay: Path
) -> None:
    first = await _sweep(tmp_path, overlay, run_id="done")
    reset_resume_fakes()

    again = await _sweep(tmp_path, overlay, run_id="done")

    assert CountingFakeSystem.searched == []
    assert CountingFakeSystem.indexed == []
    assert again.results == first.results  # latency included: every value is a record
    assert again.legs[_LEG].observations == first.legs[_LEG].observations
    records = _records(_leg_file(tmp_path, "done"))
    (resume,) = _events(records, "run_resume")
    assert resume["recorded_tasks"] == FAKE_DATASET_SIZE
    assert [r["status"] for r in _events(records, "run_end")] == ["finished", "finished"]


async def test_limit_counts_recorded_tasks(tmp_path: Path, overlay: Path) -> None:
    await _sweep(tmp_path, overlay, run_id="limited", limit=2)
    reset_resume_fakes()

    outcome = await _sweep(tmp_path, overlay, run_id="limited", limit=4)

    assert CountingFakeSystem.searched == _queries(2, 4)
    assert outcome.tasks_ran == 4
    assert [o.task_id for o in outcome.legs[_LEG].observations] == _all_task_ids(4)


async def test_run_sweep_takes_run_id_like_the_flag(tmp_path: Path, overlay: Path) -> None:
    await _interrupted(tmp_path, overlay, fail_at=2, run_id="compat")

    results, tasks_ran = await run_sweep(**_sweep_kwargs(tmp_path, overlay, run_id="compat"))

    assert CountingFakeSystem.searched == _queries(2)
    assert tasks_ran == FAKE_DATASET_SIZE
    assert set(results) == {_LEG}


async def test_every_leg_reports_every_task_after_resume(tmp_path: Path, overlay: Path) -> None:
    second = overlay.with_name("variant.yaml")
    second.write_text("log_level: warning\n")
    configs = (overlay, second)
    CountingFakeSystem.fail_on_query = fake_query(4)
    with pytest.raises(InjectedTaskFailure):
        await _sweep(tmp_path, overlay, config_paths=configs, run_id="two-legs")
    reset_resume_fakes()

    outcome = await _sweep(tmp_path, overlay, config_paths=configs, run_id="two-legs")

    # Leg one resumed from task 4; leg two had never started, so it ran in full.
    assert CountingFakeSystem.searched == _queries(4) + _queries(0)
    for key in (_LEG, (COUNTING_SYSTEM, "variant")):
        assert [o.task_id for o in outcome.legs[key].observations] == _all_task_ids()


async def test_non_resumable_tracker_sees_only_new_tasks_plus_full_aggregates(
    tmp_path: Path, overlay: Path
) -> None:
    trackers = ("jsonl", RECORDING_TRACKER)
    CountingFakeSystem.fail_on_query = fake_query(2)
    with pytest.raises(InjectedTaskFailure):
        await _sweep(tmp_path, overlay, tracker_names=trackers, run_id="fanout")
    reset_resume_fakes()

    outcome = await _sweep(tmp_path, overlay, tracker_names=trackers, run_id="fanout")

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
    assert len(outcome.legs[_LEG].observations) == FAKE_DATASET_SIZE


# ── Identity ─────────────────────────────────────────────────────────────


def _change_config(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    overlay.write_text("log_level: warning\n")
    return {}


def _change_metric_specs(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return {"metric_specs": (TASK_SCORE_METRIC, "mrr")}


def _change_dataset_revision(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return {"dataset_kwargs": {"revision": "r2"}}


def _change_dataset_args(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    return {"dataset_kwargs": {"split": "dev"}}


def _change_commit(overlay: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setenv(COMMIT_SHA_ENV_VAR, _COMMIT_B)
    return {}


@pytest.mark.parametrize(
    ("change", "component"),
    [
        (_change_config, "config_params"),
        (_change_metric_specs, "metric_specs"),
        (_change_dataset_revision, "dataset_revision"),
        (_change_dataset_args, "dataset_args"),
        (_change_commit, "commit"),
    ],
    ids=["config", "metric-specs", "dataset-revision", "dataset-args", "commit"],
)
async def test_identity_change_fails_before_any_task_runs(
    tmp_path: Path,
    overlay: Path,
    monkeypatch: pytest.MonkeyPatch,
    change,
    component: str,
) -> None:
    await _interrupted(tmp_path, overlay, fail_at=2, run_id="pinned")
    path = _leg_file(tmp_path, "pinned")
    before = path.read_bytes()
    recorded = _records(path)[0]["identity"]["fingerprint"]
    overrides = change(overlay, monkeypatch)

    with pytest.raises(LegIdentityMismatch) as excinfo:
        await _sweep(tmp_path, overlay, run_id="pinned", **overrides)

    message = str(excinfo.value)
    assert "'pinned'" in message
    fingerprints = _SHA256.findall(message)
    assert len(set(fingerprints)) == 2 and recorded in fingerprints
    assert f"first differing component: {component!r}" in message
    assert CountingFakeSystem.searched == []
    assert path.read_bytes() == before


# ── Truncated trailing line ──────────────────────────────────────────────


def _truncate_last_task_record(path: Path) -> None:
    """Simulate a kill mid-write of the last task record: cut it, drop what follows."""
    lines = path.read_text().splitlines()
    last_task = max(i for i, line in enumerate(lines) if '"_event": "task"' in line)
    kept = lines[:last_task] + [lines[last_task][: len(lines[last_task]) // 2]]
    path.write_text("\n".join(kept))  # no trailing newline, as a killed writer leaves it


async def test_truncated_last_line_reruns_its_task_with_a_warning(
    tmp_path: Path, overlay: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await _interrupted(tmp_path, overlay, fail_at=4, run_id="cut")
    path = _leg_file(tmp_path, "cut")
    _truncate_last_task_record(path)  # task 3's record is now a fragment

    with caplog.at_level(logging.WARNING):
        outcome = await _sweep(tmp_path, overlay, run_id="cut")

    assert CountingFakeSystem.searched == _queries(3)
    assert any("corrupt_line_skipped" in r.getMessage() for r in caplog.records)
    lines = path.read_text().splitlines()
    fragment = next(i for i, line in enumerate(lines) if not _parses(line))
    # The records appended after the fragment start on a line of their own.
    assert json.loads(lines[fragment + 1])["_event"] == "run_resume"
    assert all(_parses(line) for i, line in enumerate(lines) if i != fragment)
    records = [json.loads(line) for line in lines if _parses(line)]
    assert _task_ids(records) == _all_task_ids()
    assert _events(records, "run_resume")[0]["recorded_tasks"] == 3
    assert [o.task_id for o in outcome.legs[_LEG].observations] == _all_task_ids()

    # A later resume skips the now mid-file fragment again and runs nothing.
    reset_resume_fakes()
    again = await _sweep(tmp_path, overlay, run_id="cut")
    assert CountingFakeSystem.searched == []
    assert again.results == outcome.results


def _parses(line: str) -> bool:
    try:
        json.loads(line)
    except ValueError:
        return False
    return True


async def test_ci_compare_reads_the_last_aggregate_of_a_resumed_file(
    tmp_path: Path, overlay: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _interrupted(tmp_path, overlay, fail_at=4, run_id="ci")
    path = _leg_file(tmp_path, "ci")
    _truncate_last_task_record(path)  # the earlier invocation died mid-line
    outcome = await _sweep(tmp_path, overlay, run_id="ci")
    mean = outcome.results[_LEG][TASK_SCORE_METRIC][0]
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps(
            {
                "system": COUNTING_SYSTEM,
                "config": "baseline",
                "dataset": FAKE_DATASET,
                "tasks_ran": FAKE_DATASET_SIZE,
                "metrics": {TASK_SCORE_METRIC: {"mean": mean}},
            }
        )
    )
    argv = ["ci_compare", "--baseline", str(baseline), "--current", str(path)]
    monkeypatch.setattr("sys.argv", [*argv, "--metric", TASK_SCORE_METRIC])

    assert ci_compare.main() == 0


async def test_ci_compare_still_refuses_a_file_whose_last_invocation_died_mid_line(
    tmp_path: Path, overlay: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _interrupted(tmp_path, overlay, fail_at=4, run_id="died")
    path = _leg_file(tmp_path, "died")
    _truncate_last_task_record(path)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(
        json.dumps({"system": "s", "config": "c", "dataset": "d", "tasks_ran": 1,
                    "metrics": {TASK_SCORE_METRIC: {"mean": 0.5}}})
    )  # fmt: skip
    argv = ["ci_compare", "--baseline", str(baseline), "--current", str(path)]
    monkeypatch.setattr("sys.argv", [*argv, "--metric", TASK_SCORE_METRIC])

    assert ci_compare.main() == 2


# ── python -m pydocs_eval.runner ─────────────────────────────────────────


def _run_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    cwd = tmp_path / "launch-cwd"
    cwd.mkdir(exist_ok=True)
    overlay = tmp_path / "cli-configs" / "baseline.yaml"
    overlay.parent.mkdir(exist_ok=True)
    overlay.write_text("")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(_EVAL_SRC), env.get("PYTHONPATH"))))
    argv = [
        sys.executable,
        "-m",
        "pydocs_eval.runner",
        "--configs",
        str(overlay),
        "--dataset",
        "repoqa",
        "--fixture",
        str(_REPOQA_FIXTURE),
        "--bench-cache",
        "off",
        "--results-dir",
        str(tmp_path / "cli-results"),
        *args,
    ]
    return subprocess.run(
        argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=600, check=False
    )


def test_cli_run_id_resumes_and_the_report_covers_every_task(tmp_path: Path) -> None:
    first = _run_cli(tmp_path, "--run-id", "cli-run", "--limit", "1")
    assert first.returncode == 0, first.stderr

    second = _run_cli(tmp_path, "--run-id", "cli-run", "--limit", "2")
    assert second.returncode == 0, second.stderr

    (path,) = (tmp_path / "cli-results" / "cli-run").glob("*.jsonl")
    assert path.name.startswith("pydocs-mcp_baseline_repoqa")
    assert not re.search(r"_\d{8}T\d{6}Z\.jsonl$", path.name)
    records = _records(path)
    (resume,) = _events(records, "run_resume")
    assert resume["recorded_tasks"] == 1
    assert len(_events(records, "task")) == 2
    assert "(2 tasks)" in second.stdout


def test_cli_invalid_run_id_fails_before_any_leg(tmp_path: Path) -> None:
    proc = _run_cli(tmp_path, "--run-id", "bad/id", "--limit", "1")

    assert proc.returncode != 0
    assert "'bad/id'" in proc.stderr
    assert not (tmp_path / "cli-results").exists()
