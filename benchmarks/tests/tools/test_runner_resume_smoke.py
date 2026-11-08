"""Unit tests for the pure decision helpers of benchmarks/scripts/runner_resume_smoke.py.

The end-to-end path (a real ``python -m pydocs_eval.runner`` killed mid-leg,
resumed under the same run id, compared with an uninterrupted control) is
the script itself; these tests pin the helpers that decide pass/fail so a
refactor cannot make the gate vacuous. The script is not an installed
module — it is loaded from its file path (issues #416 / #420).
"""

from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts" / "runner_resume_smoke.py"
_MINI_FIXTURE = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"


def _load_smoke() -> ModuleType:
    spec = importlib.util.spec_from_file_location("runner_resume_smoke", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # WHY register first: ``@dataclass`` resolves its module through
    # ``sys.modules`` while the class body executes.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


smoke = _load_smoke()


def _task(task_id: str, step: int) -> dict[str, object]:
    return {"_event": "task", "task_id": task_id, "step": step, "scores": {}}


def _metric(name: str, value: float, step: int | None) -> dict[str, object]:
    return {"_event": "metric", "name": name, "value": value, "step": step}


def _aggregates(mrr_mean: float = 0.9, latency: float = 0.5) -> list[dict[str, object]]:
    return [
        _metric("mrr_mean", mrr_mean, None),
        _metric("mrr_ci_low", 0.7, None),
        _metric("search_seconds_p50", latency, None),
    ]


def _lines(records: list[dict[str, object]]) -> str:
    return "".join(json.dumps(r) + "\n" for r in records)


IDS = [f"t{i}" for i in range(5)]


def _killed_records(recorded: int) -> list[dict[str, object]]:
    records: list[dict[str, object]] = [{"_event": "run_start"}]
    for step in range(recorded):
        records += [_metric("mrr", 1.0, step), _task(IDS[step], step)]
    return records


def _resumed_records(recorded: int, *, resume_count: int | None = None) -> list[dict[str, object]]:
    records = _killed_records(recorded)
    count = recorded if resume_count is None else resume_count
    records.append({"_event": "run_resume", "recorded_tasks": count})
    for step in range(recorded, len(IDS)):
        records += [_metric("mrr", 1.0, step), _task(IDS[step], step)]
    return [*records, *_aggregates(), {"_event": "run_end", "status": "finished"}]


def _killed(recorded: int):
    return smoke.parse_leg_file(_lines(_killed_records(recorded)))


def _resumed(records: list[dict[str, object]]):
    return smoke.parse_leg_file(_lines(records))


# ── derive_fixture ───────────────────────────────────────────────────────


def test_derive_fixture_multiplies_tasks_with_distinct_repo_names() -> None:
    source = json.loads(_MINI_FIXTURE.read_text())

    derived = smoke.derive_fixture(source, copies=3)

    repos = [entry["repo"] for entry in derived["python"]]
    assert len(repos) == 3 * len(source["python"])
    assert len(set(repos)) == len(repos)
    assert smoke.count_fixture_tasks(derived) == 3 * smoke.count_fixture_tasks(source)


def test_derive_fixture_keeps_the_original_first_and_leaves_the_source_untouched() -> None:
    source = json.loads(_MINI_FIXTURE.read_text())
    snapshot = json.dumps(source, sort_keys=True)

    derived = smoke.derive_fixture(source, copies=2)

    assert derived["python"][: len(source["python"])] == source["python"]
    assert json.dumps(source, sort_keys=True) == snapshot


def test_derive_fixture_rejects_zero_copies() -> None:
    with pytest.raises(ValueError, match="copies"):
        smoke.derive_fixture({"python": []}, copies=0)


# ── parsing the leg file ─────────────────────────────────────────────────


def test_parse_leg_file_skips_a_truncated_last_line_and_counts_it() -> None:
    text = _lines(_killed_records(2)) + '{"_event": "task", "task_'

    leg = smoke.parse_leg_file(text)

    assert smoke.task_ids(leg.records) == ["t0", "t1"]
    assert leg.corrupt_lines == 1


def test_count_task_records_counts_only_complete_task_lines() -> None:
    text = _lines(_killed_records(3)) + '{"_event": "task"'

    assert smoke.count_task_records(text) == 3


def test_records_after_last_resume_is_the_second_invocation() -> None:
    records = _resumed_records(2)

    second = smoke.records_after_last_resume(records)

    assert smoke.task_ids(second) == ["t2", "t3", "t4"]


def test_records_after_last_resume_is_empty_without_a_resume() -> None:
    assert smoke.records_after_last_resume(_killed_records(2)) == []


def test_final_aggregate_block_is_the_trailing_stepless_metrics() -> None:
    block = smoke.final_aggregate_block(_resumed_records(2))

    assert [name for name, _ in block] == ["mrr_mean", "mrr_ci_low", "search_seconds_p50"]


# ── the kill window ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("recorded", "inside"), [(0, False), (1, True), (4, True), (5, False)])
def test_kill_landed_in_window_needs_some_but_not_all_tasks(recorded: int, inside: bool) -> None:
    assert smoke.kill_landed_in_window(recorded, total=5) is inside


def test_check_kill_passes_a_sigkilled_mid_leg_file() -> None:
    leg = smoke.parse_leg_file(_lines(_killed_records(2)))

    assert smoke.check_kill(-signal.SIGKILL, leg, total=5) == []


def test_check_kill_fails_a_process_that_was_not_sigkilled() -> None:
    leg = smoke.parse_leg_file(_lines(_killed_records(2)))

    (failure,) = smoke.check_kill(0, leg, total=5)
    assert "SIGKILL" in failure


def test_check_kill_fails_when_every_task_was_already_recorded() -> None:
    leg = smoke.parse_leg_file(_lines(_killed_records(5)))

    (failure,) = smoke.check_kill(-signal.SIGKILL, leg, total=5)
    assert "outside the window" in failure


def test_check_kill_fails_a_leg_that_reached_run_end() -> None:
    records = [*_killed_records(2), {"_event": "run_end", "status": "finished"}]

    failures = smoke.check_kill(-signal.SIGKILL, smoke.parse_leg_file(_lines(records)), total=5)
    assert any("run_end" in f for f in failures)


# ── the resumed leg ──────────────────────────────────────────────────────


def test_check_resume_passes_when_only_the_unrecorded_tasks_ran() -> None:
    leg = smoke.parse_leg_file(_lines(_resumed_records(2)))

    assert smoke.check_resume(_killed(2), leg, control_ids=IDS) == []


def test_check_resume_fails_a_wrong_recorded_task_count() -> None:
    leg = smoke.parse_leg_file(_lines(_resumed_records(2, resume_count=1)))

    failures = smoke.check_resume(_killed(2), leg, control_ids=IDS)
    assert any("recorded_tasks" in f for f in failures)


def test_check_resume_fails_when_a_recorded_task_ran_again() -> None:
    records = _resumed_records(2)
    at = records.index({"_event": "run_resume", "recorded_tasks": 2}) + 1
    records[at:at] = [_metric("mrr", 1.0, 1), _task("t1", 1)]

    failures = smoke.check_resume(_killed(2), smoke.parse_leg_file(_lines(records)), IDS)
    assert any("second invocation ran" in f for f in failures)
    assert any("re-scored" in f for f in failures)


def test_check_resume_fails_a_leg_without_a_finished_run_end() -> None:
    records = _resumed_records(2)[:-1]

    failures = smoke.check_resume(_killed(2), smoke.parse_leg_file(_lines(records)), IDS)
    assert any("run_end" in f for f in failures)


def test_check_resume_fails_when_the_recorded_tasks_are_not_a_dataset_prefix() -> None:
    swapped = _resumed([{"_event": "run_start"}, _task("t1", 0), _task("t0", 1)])

    failures = smoke.check_resume(swapped, _resumed(_resumed_records(2)), control_ids=IDS)
    assert any("prefix" in f for f in failures)


@pytest.mark.parametrize("resumes", [0, 2])
def test_check_resume_needs_exactly_one_run_resume_record(resumes: int) -> None:
    records = [r for r in _resumed_records(2) if r["_event"] != "run_resume"]
    marker = {"_event": "run_resume", "recorded_tasks": 2}
    records[5:5] = [marker] * resumes

    failures = smoke.check_resume(_killed(2), _resumed(records), IDS)
    assert any(f"exactly one run_resume record, got {resumes}" in f for f in failures)


def test_check_resume_tolerates_the_killed_legs_own_truncated_tail() -> None:
    tail = '{"_event": "metric", "na'
    killed = smoke.parse_leg_file(_lines(_killed_records(2)) + tail)
    resumed_text = _lines(_killed_records(2)) + tail + "\n" + _lines(_resumed_records(2)[5:])

    assert smoke.check_resume(killed, smoke.parse_leg_file(resumed_text), IDS) == []


def test_check_resume_fails_a_corrupt_line_the_resume_appended() -> None:
    resumed_text = _lines(_resumed_records(2)[:-2]) + "{broken\n" + _lines(_resumed_records(2)[-2:])

    failures = smoke.check_resume(_killed(2), smoke.parse_leg_file(resumed_text), IDS)
    assert any("non-JSON" in f for f in failures)


# ── the aggregate comparison ─────────────────────────────────────────────


def _block(records: list[dict[str, object]]) -> list[tuple[str, float]]:
    return smoke.final_aggregate_block([*records, {"_event": "run_end", "status": "finished"}])


def test_compare_aggregate_blocks_ignores_wall_clock_latency() -> None:
    resumed, control = _block(_aggregates(latency=0.1)), _block(_aggregates(latency=0.2))

    assert smoke.compare_aggregate_blocks(resumed, control, smoke.LATENCY_KEYS) == []


def test_compare_aggregate_blocks_fails_on_one_ulp_of_quality_drift() -> None:
    drifted = 0.9 + 2**-53
    assert drifted != 0.9
    resumed, control = _block(_aggregates(mrr_mean=drifted)), _block(_aggregates())

    (failure,) = smoke.compare_aggregate_blocks(resumed, control, smoke.LATENCY_KEYS)
    assert "mrr_mean" in failure


def test_compare_aggregate_blocks_fails_on_a_different_metric_list() -> None:
    resumed, control = _block(_aggregates()[:2]), _block(_aggregates())

    (failure,) = smoke.compare_aggregate_blocks(resumed, control, smoke.LATENCY_KEYS)
    assert "names" in failure


def test_compare_aggregate_blocks_fails_on_an_empty_block() -> None:
    assert smoke.compare_aggregate_blocks([], [], smoke.LATENCY_KEYS) != []


def test_compare_aggregate_blocks_fails_an_empty_resumed_block() -> None:
    (failure,) = smoke.compare_aggregate_blocks([], _block(_aggregates()), smoke.LATENCY_KEYS)
    assert "names" in failure


def test_final_aggregate_block_stops_at_a_per_task_metric() -> None:
    records = [_metric("mrr", 0.25, 4), *_aggregates(), {"_event": "run_end"}]

    block = smoke.final_aggregate_block(records)

    assert "mrr" not in [name for name, _ in block]
    assert len(block) == len(_aggregates())


# ── the child command ────────────────────────────────────────────────────


def test_build_runner_argv_is_identical_but_for_the_run_id(tmp_path: Path) -> None:
    def argv(run_id: str) -> list[str]:
        return smoke.build_runner_argv(
            "/py",
            config=tmp_path / "c.yaml",
            fixture=tmp_path / "f.json",
            results_dir=tmp_path / "r",
            run_id=run_id,
        )

    first, second = argv("a"), argv("b")
    assert first[:3] == ["/py", "-m", "pydocs_eval.runner"]
    assert first[first.index("--run-id") + 1] == "a"
    assert [x for x in first if x != "a"] == [x for x in second if x != "b"]
    assert first[first.index("--results-dir") + 1] == str(tmp_path / "r")


def test_build_child_env_blocks_the_network_and_sandboxes_the_cache(tmp_path: Path) -> None:
    env = _child_env(tmp_path, {"PYTHONPATH": "/elsewhere", "HOME": "/h"})

    assert env["HF_HUB_OFFLINE"] == "1"
    assert env["HTTPS_PROXY"] == env["HTTP_PROXY"] == env["https_proxy"] == smoke.DEAD_PROXY
    assert env["PYDOCS_CACHE_DIR"] == str(tmp_path / "work" / "index-cache")
    assert env["TMPDIR"] == str(tmp_path / "work" / "tmp")
    paths = env["PYTHONPATH"].split(os.pathsep)
    assert paths[:2] == [str(tmp_path / "python"), str(tmp_path / "benchmarks" / "src")]
    assert paths[-1] == "/elsewhere"
    assert env["HOME"] == "/h"


def _child_env(tmp_path: Path, base: dict[str, str]) -> dict[str, str]:
    return smoke.build_child_env(
        base, repo_root=tmp_path, workdir=tmp_path / "work", model_cache=tmp_path / "models"
    )


def test_build_child_env_drops_operator_app_config_overlays(tmp_path: Path) -> None:
    env = _child_env(tmp_path, {"PYDOCS_EMBEDDING__PROVIDER": "openai"})

    assert "PYDOCS_EMBEDDING__PROVIDER" not in env


def test_build_child_env_pins_the_model_cache_unless_the_operator_set_one(tmp_path: Path) -> None:
    assert _child_env(tmp_path, {})[smoke.FASTEMBED_CACHE_VAR] == str(tmp_path / "models")
    own = {smoke.FASTEMBED_CACHE_VAR: "/models/mine"}
    assert _child_env(tmp_path, own)[smoke.FASTEMBED_CACHE_VAR] == "/models/mine"


# ── up-front validation ──────────────────────────────────────────────────


def test_load_derived_fixture_refuses_a_kill_after_with_no_mid_leg_window() -> None:
    total = smoke.count_fixture_tasks(smoke.load_derived_fixture(2, 1))

    with pytest.raises(smoke.SmokeFailure, match=f"1..{total - 1}"):
        smoke.load_derived_fixture(2, total)
