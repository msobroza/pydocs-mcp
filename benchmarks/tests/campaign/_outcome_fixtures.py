"""Builders shared by the three outcome test files (arm, measurement, report).

One place for the settings, the recorded trajectories, the legacy ``arm.json``
shape and the report-row reader, so the arm, the measurement and the report are
each tested against the same inputs rather than three drifting copies of them.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pydocs_eval.campaign.before_after import CommitUnderTest, CostModel, MeasurementPlan
from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    ArmTaskRecord,
    run_arm,
)
from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after_measure import ArmMetrics, TaskMeasurement, measure_arm
from pydocs_eval.trajectory.ask_outcome import TaskEnding, TaskOutcome
from pydocs_eval.trajectory.search_retrieval import score_search_calls
from pydocs_eval.trajectory.tool_usage import compute_tool_usage
from tests.trajectory._ask_traces import write_ask_trajectory

from ._fakes import ScriptedArmRunner, eval_task

CAP = 12
BASELINE = CommitUnderTest(role="baseline", sha="a" * 40, subject="before", description_tokens=100)
CANDIDATE = CommitUnderTest(role="candidate", sha="b" * 40, subject="after", description_tokens=90)
GOLD = "pkg/needle.py"
PENALISED = "turns-to-answer (penalised, exhausted = cap+1)"
ANSWERED_ONLY = "turns-to-answer (answered only)"


# --- one arm, offline -----------------------------------------------------------


def arm_settings(tmp_path: Path, **overrides: object) -> ArmSettings:
    fields: dict[str, object] = {
        "role": "baseline",
        "commit": "a" * 40,
        "workspace": str(tmp_path / "ws"),
        "model": "test-model",
        "trace_root": str(tmp_path / "traces"),
        "out_dir": str(tmp_path / "arm"),
        "max_agent_turns": CAP,
        "estimated_usd_per_rollout": 0.25,
        "cost_ceiling_usd": 10.0,
    }
    return ArmSettings(**{**fields, **overrides})  # type: ignore[arg-type]


def run_one_arm(settings: ArmSettings, runner: object, *task_ids: str) -> ArmSummary:
    """Every task through ``runner`` under ``settings``, as the arm child runs them."""
    tasks = tuple(eval_task(task_id) for task_id in task_ids)
    return asyncio.run(run_arm(settings, tasks, make_runner=lambda _s, _w: runner))


def stored_arm_json(settings: ArmSettings) -> dict[str, object]:
    """The ``arm.json`` an arm wrote, as raw JSON — the persisted contract itself."""
    return dict(json.loads((Path(settings.out_dir) / ARM_SUMMARY_FILENAME).read_text()))


# --- arm.json as runs before outcomes wrote it ---------------------------------


def legacy_row(trace_dir: Path, *, answer_chars: int, turns: int) -> dict[str, object]:
    """One ``arm.json`` task row in the exact shape the 2026-09-15 run wrote."""
    return {
        "task_id": "t1",
        "trajectory_id": trace_dir.name,
        "trace_dir": str(trace_dir),
        "gold_files": ["a.py"],
        "turns": turns,
        "wall_seconds": 3.0,
        "answer_chars": answer_chars,
    }


def write_legacy_arm(out_dir: Path, role: str, rows: list[dict[str, object]]) -> Path:
    """An arm directory holding ``rows`` in a pre-outcome ``arm.json``; return it."""
    arm_dir = out_dir / role
    arm_dir.mkdir(parents=True)
    payload = {
        "role": role,
        "commit": "a" * 40,
        "model": "m",
        "trace_root": "",
        "tasks": rows,
        "estimated_usd": 0.0,
        "halt_reason": "completed",
        "excluded": 0,
    }
    (arm_dir / ARM_SUMMARY_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    return arm_dir


# --- one measured task ------------------------------------------------------------


def needle_trace(tmp_path: Path, *, with_turns: bool = True) -> Path:
    """A search showing the Needle in turn 1, then a read of it in turn 2."""
    trace_dir = write_ask_trajectory(
        tmp_path / "traces",
        calls=[("search_codebase", {"query": "q"}, 1), ("read_file", {"path": GOLD}, 2)],
        items=[{"path": GOLD}],
    )
    if not with_turns:
        (trace_dir / "model_turns.json").unlink()
    return trace_dir


def arm_record(trace_dir: Path, **fields: object) -> ArmTaskRecord:
    """An answered 3-turn row over ``trace_dir``, unless ``fields`` say otherwise."""
    defaults: dict[str, object] = {
        "task_id": "t1",
        "trajectory_id": trace_dir.name,
        "trace_dir": str(trace_dir),
        "gold_files": [GOLD],
        "turns": 3,
        "wall_seconds": 2.5,
        "answer_chars": 10,
        "answer": "it is here",
        "outcome": TaskOutcome.ANSWERED,
    }
    return ArmTaskRecord(**{**defaults, **fields})  # type: ignore[arg-type]


def arm_summary(*records: ArmTaskRecord, max_agent_turns: int = CAP) -> ArmSummary:
    return ArmSummary(
        role="baseline",
        commit="a" * 40,
        model="m",
        trace_root="",
        tasks=list(records),
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
        max_agent_turns=max_agent_turns,
    )


def measured(record: ArmTaskRecord, *, arm_cap: int = CAP, plan_cap: int = CAP) -> TaskMeasurement:
    """``record`` measured off its trace, under its arm's cap and the plan's."""
    summary = arm_summary(record, max_agent_turns=arm_cap)
    [task] = measure_arm(
        summary, BASELINE, workspace=Path("/ws"), max_agent_turns=plan_cap
    ).per_task
    return task


# --- the report ---------------------------------------------------------------------


def report_plan(task_ids: tuple[str, ...]) -> MeasurementPlan:
    return MeasurementPlan(
        split="repoqa-qa/small_test",
        task_ids=task_ids,
        baseline=BASELINE,
        candidate=CANDIDATE,
        model="m",
        endpoint="e",
        workspace=Path("/ws"),
        max_agent_turns=CAP,
        cost=CostModel(),
        task_workspaces=TaskWorkspaces(
            root=Path("/ws/task-workspaces"), shared_workspace=Path("/ws"), shared_task_ids=task_ids
        ),
    )


def ended_task(task_id: str, outcome: TaskOutcome, turns: int, **fields: object) -> TaskMeasurement:
    """One measured task that ended ``outcome`` after ``turns`` turns, under the 12-turn cap."""
    base: dict[str, object] = {
        "task_id": task_id,
        "needless_call_rate": 0.0,
        "resurfacing": 0,
        "zero_yield": 0,
        "fan_out_where_batch": 0,
        "tool_mismatch": 0,
        "pointer_followed_rate": None,
        "parallel_calls_per_turn": 1.0,
        "batch_vs_fanout_ratio": None,
        "tool_calls_to_first_gold": None,
        "retrieval": score_search_calls((), frozenset()),
        "usage": compute_tool_usage((), workspace_root="/ws"),
        "ending": TaskEnding(outcome=outcome, turns=turns, max_agent_turns=CAP),
    }
    return TaskMeasurement(**{**base, **fields})  # type: ignore[arg-type]


def arm_of(commit: CommitUnderTest, *tasks: TaskMeasurement) -> ArmMetrics:
    return ArmMetrics(commit=commit, per_task=tasks)


def row_cells(report: str, label: str) -> list[str]:
    """The cells of the row whose label is EXACTLY ``label`` (its direction glyph aside)."""
    for line in report.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and cells[0][:-2] == label and cells[0][-1] in "↓↑·":
            return cells
    raise AssertionError(f"no row labelled {label!r} in:\n{report}")


__all__ = (
    "ANSWERED_ONLY",
    "BASELINE",
    "CANDIDATE",
    "CAP",
    "GOLD",
    "PENALISED",
    "ScriptedArmRunner",
    "arm_of",
    "arm_record",
    "arm_settings",
    "arm_summary",
    "ended_task",
    "legacy_row",
    "measured",
    "needle_trace",
    "report_plan",
    "row_cells",
    "run_one_arm",
    "stored_arm_json",
    "write_legacy_arm",
)
