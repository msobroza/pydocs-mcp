"""campaign/before-after — how each task ended, recorded by the arm and reported.

Budget exhaustion, timeouts, starved replies and empty answers used to be
invisible: an arm booked LangGraph's canned apology as a 12-turn answer. These
pin the chain end to end — what an arm writes into ``arm.json``, how an
``arm.json`` written before outcomes existed is read back, and the rows the
report prints from both.

Every trace here is written by the PRODUCT recorder, so the arm reads the file
shapes a paid run produces; the harness runner itself is a named fake.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import (
    REPORTED_METRICS,
    CommitUnderTest,
    CostModel,
    MeasurementPlan,
    render_plan,
)
from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    ArmTaskRecord,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.campaign.before_after_corpora import TaskWorkspaces
from pydocs_eval.campaign.before_after_measure import ArmMetrics, TaskMeasurement, measure_arm
from pydocs_eval.campaign.before_after_report import render_report
from pydocs_eval.campaign.before_after_rows import REPORT_ROWS
from pydocs_eval.metrics.aggregate import (
    mcnemar_from_pairs,
    mean_with_bootstrap_ci,
    percentile,
)
from pydocs_eval.trajectory.ask_outcome import ASK_BUDGET_EXHAUSTED_REPLY, TaskEnding, TaskOutcome
from pydocs_eval.trajectory.search_retrieval import score_search_calls
from pydocs_eval.trajectory.tool_usage import compute_tool_usage
from tests.trajectory._ask_traces import write_ask_trajectory

from ._fakes import RecordedTrajectory, ScriptedArmRunner, eval_task, recorded_trajectory

_CAP = 12
_BASELINE = CommitUnderTest(role="baseline", sha="a" * 40, subject="before", description_tokens=100)
_CANDIDATE = CommitUnderTest(role="candidate", sha="b" * 40, subject="after", description_tokens=90)
_GOLD = "pkg/needle.py"


def _settings(tmp_path: Path, **overrides: object) -> ArmSettings:
    fields: dict[str, object] = {
        "role": "baseline",
        "commit": "a" * 40,
        "workspace": str(tmp_path / "ws"),
        "model": "test-model",
        "trace_root": str(tmp_path / "traces"),
        "out_dir": str(tmp_path / "arm"),
        "max_agent_turns": _CAP,
        "estimated_usd_per_rollout": 0.25,
        "cost_ceiling_usd": 10.0,
    }
    return ArmSettings(**{**fields, **overrides})  # type: ignore[arg-type]


def _run(settings: ArmSettings, runner: ScriptedArmRunner, *task_ids: str) -> ArmSummary:
    tasks = tuple(eval_task(task_id) for task_id in task_ids)
    return asyncio.run(run_arm(settings, tasks, make_runner=lambda _s, _w: runner))


def _stored_rows(settings: ArmSettings) -> list[dict[str, object]]:
    payload = json.loads((Path(settings.out_dir) / ARM_SUMMARY_FILENAME).read_text())
    return list(payload["tasks"])


# --- what a new arm writes --------------------------------------------------


def test_a_new_arm_records_the_answer_its_outcome_its_calls_and_near_cap(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    run = replace(
        recorded_trajectory(tmp_path / "traces", answer="It is `a.py`.", turns=11),
        tool_call_count=3,
    )

    _run(settings, ScriptedArmRunner({"t1": run}), "t1")

    [row] = _stored_rows(settings)
    assert row["answer"] == "It is `a.py`."
    assert row["answer_chars"] == len("It is `a.py`.")
    assert row["outcome"] == "answered"
    assert row["tool_calls"] == 3
    assert row["near_cap"] is True
    assert (
        json.loads((Path(settings.out_dir) / ARM_SUMMARY_FILENAME).read_text())["max_agent_turns"]
        == _CAP
    )


def test_the_answer_text_round_trips_through_arm_json(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    answer = "Line one.\nNot confirmed: the caller — été → `pkg/b.py`."
    run = recorded_trajectory(tmp_path / "traces", answer=answer, turns=3)

    _run(settings, ScriptedArmRunner({"t1": run}), "t1")

    [task] = read_arm_summary(Path(settings.out_dir)).tasks
    assert task.answer == answer
    assert task.outcome is TaskOutcome.ANSWERED
    assert task.near_cap is False


def test_an_old_products_exhausted_run_is_booked_once_as_budget_exhausted(
    tmp_path: Path,
) -> None:
    """Before issue #371 the canned apology comes back as the answer at the cap."""
    settings = _settings(tmp_path)
    run = recorded_trajectory(tmp_path / "traces", answer=ASK_BUDGET_EXHAUSTED_REPLY, turns=_CAP)
    runner = ScriptedArmRunner({"t1": run})

    summary = _run(settings, runner, "t1")

    assert runner.seen == ["t1"]
    [task] = summary.tasks
    assert task.outcome is TaskOutcome.BUDGET_EXHAUSTED
    assert task.turns == _CAP


def test_a_flagged_exhausted_run_with_its_trace_is_booked_as_budget_exhausted(
    tmp_path: Path,
) -> None:
    """Issue #371's shape: the flag set, the apology dropped, the trace kept."""
    settings = _settings(tmp_path)
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=_CAP, budget_exhausted=True)
    runner = ScriptedArmRunner({"t1": run})

    [task] = _run(settings, runner, "t1").tasks

    assert runner.seen == ["t1"]
    assert (task.outcome, task.turns) == (TaskOutcome.BUDGET_EXHAUSTED, _CAP)


def test_a_timed_out_run_with_its_trace_reads_timeout_not_an_empty_answer(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=4, timed_out=True)

    [task] = _run(settings, ScriptedArmRunner({"t1": run}), "t1").tasks

    assert task.outcome is TaskOutcome.TIMEOUT


def test_an_empty_reply_cut_at_length_is_a_starved_reply_while_thinking_is_on(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, llm_block={"params": {"thinking": "auto"}})
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=5, last_finish_reason="length")

    [task] = _run(settings, ScriptedArmRunner({"t1": run}), "t1").tasks

    assert task.outcome is TaskOutcome.STARVED_REPLY


@pytest.mark.parametrize("thinking", ["off", False])
def test_an_arm_that_turns_thinking_off_books_the_same_reply_as_unanswered(
    tmp_path: Path, thinking: object
) -> None:
    """``False`` is what a YAML 1.1 loader makes of a bare ``thinking: off``."""
    settings = _settings(tmp_path, llm_block={"params": {"thinking": thinking}})
    run = recorded_trajectory(tmp_path / "traces", answer="", turns=5, last_finish_reason="length")

    [task] = _run(settings, ScriptedArmRunner({"t1": run}), "t1").tasks

    assert task.outcome is TaskOutcome.UNANSWERED_EMPTY


# --- what counts as a completed rollout ---------------------------------------


def test_a_run_whose_trace_file_never_landed_is_infra_never_booked(tmp_path: Path) -> None:
    """An id is not a trace: a run is complete only when its events FILE exists."""
    settings = _settings(tmp_path)
    missing = RecordedTrajectory(
        trajectory_id="abc", trace_dir=tmp_path / "traces" / "abc", answer="x", turns=2
    )
    runner = ScriptedArmRunner({"t1": missing})

    summary = _run(settings, runner, "t1")

    assert summary.tasks == []
    assert summary.excluded == 1
    # Infra is retried once, then excluded — exactly the traceless-raise path.
    assert runner.seen == ["t1", "t1"]


def test_a_traceless_run_at_the_working_directory_is_infra(tmp_path: Path) -> None:
    """``Path()`` — the no-trajectory trace dir — exists; it is still no trace."""
    settings = _settings(tmp_path)
    traceless = RecordedTrajectory(trajectory_id="", trace_dir=Path(), answer="", turns=13)

    summary = _run(settings, ScriptedArmRunner({"t1": traceless}), "t1")

    assert summary.tasks == []
    assert summary.excluded == 1


# --- reading an arm.json written before outcomes existed ----------------------


def _legacy_arm_json(tmp_path: Path) -> Path:
    """An ``arm.json`` in the exact shape the 2026-09-15 run wrote."""
    arm_dir = tmp_path / "legacy"
    arm_dir.mkdir()
    rows = [
        {
            "answer_chars": 47,
            "gold_files": ["a.py"],
            "task_id": "t1",
            "trace_dir": "/traces/t1",
            "trajectory_id": "t1",
            "turns": 12,
            "wall_seconds": 30.0,
        }
    ]
    payload = {
        "commit": "a" * 40,
        "estimated_usd": 0.1,
        "excluded": 0,
        "halt_reason": "completed",
        "model": "m",
        "role": "baseline",
        "tasks": rows,
        "trace_root": "/traces",
    }
    (arm_dir / ARM_SUMMARY_FILENAME).write_text(json.dumps(payload), encoding="utf-8")
    return arm_dir


def test_a_legacy_arm_json_loads_every_row_as_unrecorded(tmp_path: Path) -> None:
    summary = read_arm_summary(_legacy_arm_json(tmp_path))

    [task] = summary.tasks
    assert task.outcome is TaskOutcome.UNRECORDED
    assert (task.answer, task.tool_calls, task.near_cap) == ("", -1, False)
    assert summary.max_agent_turns == 0


def test_an_unknown_outcome_in_arm_json_is_refused_by_name(tmp_path: Path) -> None:
    arm_dir = _legacy_arm_json(tmp_path)
    path = arm_dir / ARM_SUMMARY_FILENAME
    payload = json.loads(path.read_text())
    payload["tasks"][0]["outcome"] = "gave_up"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="'gave_up'.*budget_exhausted"):
        read_arm_summary(arm_dir)


# --- the measurement: outcome, turns and the penalty --------------------------


def _trace(tmp_path: Path, *, with_turns: bool = True) -> Path:
    """One recorded trajectory whose first call surfaces the Needle in turn 1."""
    trace_dir = write_ask_trajectory(
        tmp_path / "traces",
        calls=[("search_codebase", {"query": "q"}, 1), ("read_file", {"path": _GOLD}, 2)],
        items=[{"path": _GOLD}],
    )
    if not with_turns:
        (trace_dir / "model_turns.json").unlink()
    return trace_dir


def _record(trace_dir: Path, **fields: object) -> ArmTaskRecord:
    defaults: dict[str, object] = {
        "task_id": "t1",
        "trajectory_id": trace_dir.name,
        "trace_dir": str(trace_dir),
        "gold_files": [_GOLD],
        "turns": 3,
        "wall_seconds": 2.5,
        "answer_chars": 10,
        "answer": "it is here",
        "outcome": TaskOutcome.ANSWERED,
    }
    return ArmTaskRecord(**{**defaults, **fields})  # type: ignore[arg-type]


def _measured(
    record: ArmTaskRecord, *, arm_cap: int = _CAP, plan_cap: int = _CAP
) -> TaskMeasurement:
    summary = ArmSummary(
        role="baseline",
        commit="a" * 40,
        model="m",
        trace_root="",
        tasks=[record],
        estimated_usd=0.0,
        halt_reason="completed",
        excluded=0,
        max_agent_turns=arm_cap,
    )
    [task] = measure_arm(
        summary, _BASELINE, workspace=Path("/ws"), max_agent_turns=plan_cap
    ).per_task
    return task


def test_an_answered_task_is_charged_its_own_turns_in_both_means(tmp_path: Path) -> None:
    task = _measured(_record(_trace(tmp_path), turns=3))

    assert (task.ending.outcome, task.ending.turns, task.wall_seconds) == (
        TaskOutcome.ANSWERED,
        3,
        2.5,
    )
    assert task.ending.turns_to_answer_penalised == 3
    assert task.ending.turns_to_answer_answered_only == 3
    assert (task.ending.answered_within_budget, task.ending.budget_exhausted) == (1, 0)
    assert task.ending.near_cap is False


def test_an_answered_task_at_the_cap_is_near_cap_and_charged_only_its_turns(
    tmp_path: Path,
) -> None:
    """The 2026-09-15 candidate's 2099-character, 12-turn answer."""
    task = _measured(_record(_trace(tmp_path), turns=12, answer_chars=2099))

    assert task.ending.outcome is TaskOutcome.ANSWERED
    assert task.ending.near_cap is True
    assert task.ending.turns_to_answer_penalised == 12


@pytest.mark.parametrize(
    "outcome",
    [
        TaskOutcome.BUDGET_EXHAUSTED,
        TaskOutcome.EXHAUSTED_FINALIZED,
        TaskOutcome.STARVED_REPLY,
        TaskOutcome.TIMEOUT,
        TaskOutcome.UNANSWERED_EMPTY,
    ],
)
def test_an_unanswered_task_counts_the_budget_plus_one_and_leaves_the_answered_mean(
    tmp_path: Path, outcome: TaskOutcome
) -> None:
    task = _measured(_record(_trace(tmp_path), turns=5, outcome=outcome))

    assert task.ending.turns_to_answer_penalised == _CAP + 1
    assert task.ending.turns_to_answer_answered_only is None
    assert task.ending.answered_within_budget == 0
    # The raw graph count is never touched by the penalty.
    assert task.ending.turns == 5


@pytest.mark.parametrize(
    ("outcome", "exhausted"),
    [
        (TaskOutcome.BUDGET_EXHAUSTED, 1),
        (TaskOutcome.EXHAUSTED_FINALIZED, 1),
        (TaskOutcome.TIMEOUT, 0),
        (TaskOutcome.UNANSWERED_EMPTY, 0),
    ],
)
def test_budget_exhaustion_counts_a_finalized_answer_too(
    tmp_path: Path, outcome: TaskOutcome, exhausted: int
) -> None:
    assert (
        _measured(_record(_trace(tmp_path), outcome=outcome)).ending.budget_exhausted == exhausted
    )


def test_a_legacy_exhausted_row_is_back_filled_against_the_plans_cap(tmp_path: Path) -> None:
    """The 2026-09-15 baseline row: 47 characters at 12 turns, no cap in its arm.json."""
    legacy = _record(
        _trace(tmp_path), turns=12, answer_chars=47, answer="", outcome=TaskOutcome.UNRECORDED
    )

    task = _measured(legacy, arm_cap=0, plan_cap=_CAP)

    assert task.ending.outcome is TaskOutcome.BUDGET_EXHAUSTED
    assert task.ending.turns_to_answer_penalised == _CAP + 1
    assert (task.ending.budget_exhausted, task.ending.near_cap) == (1, True)


def test_the_arms_own_cap_wins_over_the_plans(tmp_path: Path) -> None:
    """A variant arm ran under ITS budget; the penalty is that budget + 1."""
    record = _record(_trace(tmp_path), turns=5, outcome=TaskOutcome.BUDGET_EXHAUSTED)

    task = _measured(record, arm_cap=5, plan_cap=_CAP)

    assert task.ending.turns_to_answer_penalised == 6
    assert task.ending.near_cap is True


def test_an_unrecorded_row_with_no_cap_anywhere_drops_out_of_every_turn_mean(
    tmp_path: Path,
) -> None:
    legacy = _record(_trace(tmp_path), turns=12, answer_chars=47, outcome=TaskOutcome.UNRECORDED)

    task = _measured(legacy, arm_cap=0, plan_cap=0)

    assert task.ending.outcome is TaskOutcome.UNRECORDED
    assert task.ending.turns_to_answer_penalised is None
    assert task.ending.turns_to_answer_answered_only is None
    assert (task.ending.budget_exhausted, task.ending.answered_within_budget) == (None, None)
    # Still a measured graph count: the raw turns row keeps it.
    assert task.ending.turns == 12


# --- the measurement: after the Needle ----------------------------------------


def test_the_needle_reach_numbers_come_off_the_recorded_trace(tmp_path: Path) -> None:
    """The search surfaces the Needle at call 1 (turn 1); the read is call 2; turn 3 answers."""
    task = _measured(_record(_trace(tmp_path), turns=3))

    assert task.turns_after_first_gold == 2
    assert task.calls_after_first_gold == 1
    assert task.tool_calls_to_first_gold_read == 2
    assert task.calls_after_first_gold_read == 0


def test_turns_after_needle_is_undefined_without_recorded_turns(tmp_path: Path) -> None:
    """An arm whose product wrote no model-turn sidecar cannot say which turn it was."""
    task = _measured(_record(_trace(tmp_path, with_turns=False), turns=3))

    assert task.turns_after_first_gold is None
    # The call-based numbers read the calls themselves and stay measured.
    assert task.calls_after_first_gold == 1
    assert task.calls_after_first_gold_read == 0


def test_finalize_format_failures_are_reserved_and_undefined(tmp_path: Path) -> None:
    assert _measured(_record(_trace(tmp_path))).finalize_format_failures is None


# --- the report: the outcome and turn rows ------------------------------------


def _plan(task_ids: tuple[str, ...]) -> MeasurementPlan:
    return MeasurementPlan(
        split="repoqa-qa/small_test",
        task_ids=task_ids,
        baseline=_BASELINE,
        candidate=_CANDIDATE,
        model="m",
        endpoint="e",
        workspace=Path("/ws"),
        max_agent_turns=_CAP,
        cost=CostModel(),
        task_workspaces=TaskWorkspaces(
            root=Path("/ws/task-workspaces"), shared_workspace=Path("/ws"), shared_task_ids=task_ids
        ),
    )


def _task(task_id: str, outcome: TaskOutcome, turns: int, **fields: object) -> TaskMeasurement:
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
        "ending": TaskEnding(outcome=outcome, turns=turns, max_agent_turns=_CAP),
    }
    return TaskMeasurement(**{**base, **fields})  # type: ignore[arg-type]


def _arm(commit: CommitUnderTest, *tasks: TaskMeasurement) -> ArmMetrics:
    return ArmMetrics(commit=commit, per_task=tasks)


def _cells(report: str, label: str) -> list[str]:
    """The cells of the row whose label is EXACTLY ``label`` (its direction glyph aside)."""
    for line in report.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and cells[0][:-2] == label and cells[0][-1] in "↓↑·":
            return cells
    raise AssertionError(f"no row labelled {label!r} in:\n{report}")


_PENALISED = "turns-to-answer (penalised, exhausted = cap+1)"
_ANSWERED_ONLY = "turns-to-answer (answered only)"


def test_every_unanswered_task_counts_13_in_the_penalised_mean_and_none_in_the_other() -> None:
    arm = _arm(
        _BASELINE,
        _task("t1", TaskOutcome.ANSWERED, 4),
        _task("t2", TaskOutcome.BUDGET_EXHAUSTED, 12),
        _task("t3", TaskOutcome.STARVED_REPLY, 6),
        _task("t4", TaskOutcome.TIMEOUT, 2),
        _task("t5", TaskOutcome.UNANSWERED_EMPTY, 3),
    )

    report = render_report(_plan(("t1", "t2", "t3", "t4", "t5")), [arm, arm])

    # (4 + 13 + 13 + 13 + 13) / 5: every unanswered task is charged the budget + 1.
    assert _cells(report, _PENALISED)[1].startswith("11.200 ")
    assert _cells(report, _ANSWERED_ONLY)[1] == "4 [4, 4]"
    assert _cells(report, _PENALISED)[5] == "5"
    assert _cells(report, _ANSWERED_ONLY)[5] == "1"
    # ... and each appears in the outcome tally.
    for outcome in ("budget_exhausted", "starved_reply", "timeout", "unanswered_empty"):
        assert _cells(report, f"outcome: {outcome}")[1] == "1", outcome
    assert _cells(report, "outcome: answered")[1] == "1"


def test_the_penalised_row_leads_the_turn_block() -> None:
    labels = [row.label for row in REPORT_ROWS]

    turn_block = labels[labels.index(_PENALISED) :]
    assert turn_block[:4] == [_PENALISED, _ANSWERED_ONLY, "turns after needle", "turns (per task)"]
    # The outcome rows come first of all; the headline block follows them.
    assert labels.index("budget-exhausted rate") < labels.index(_PENALISED)


def test_a_finalized_answer_counts_13_and_is_not_answered_within_budget() -> None:
    arm = _arm(_BASELINE, _task("t1", TaskOutcome.EXHAUSTED_FINALIZED, 12))

    report = render_report(_plan(("t1",)), [arm, arm])

    assert _cells(report, _PENALISED)[1] == "13 [13, 13]"
    assert _cells(report, "answered-within-budget rate")[1] == "0 [0, 0]"
    assert _cells(report, "budget-exhausted rate")[1] == "1 [1, 1]"
    assert _cells(report, _ANSWERED_ONLY)[1] == "n/a"


def test_the_budget_exhausted_rate_is_mcnemars_paired_test() -> None:
    exhausted = {"t1": 1, "t2": 1, "t3": 1, "t4": 0}
    baseline = _arm(
        _BASELINE,
        *(
            _task(t, TaskOutcome.BUDGET_EXHAUSTED if v else TaskOutcome.ANSWERED, 12)
            for t, v in exhausted.items()
        ),
    )
    candidate = _arm(_CANDIDATE, *(_task(t, TaskOutcome.ANSWERED, 5) for t in exhausted))

    cells = _cells(
        render_report(_plan(tuple(exhausted)), [baseline, candidate]), "budget-exhausted rate"
    )

    *_, delta, p_value, (_, low, high) = mcnemar_from_pairs(dict.fromkeys(exhausted, 0), exhausted)
    assert cells[3] == f"{delta:+.3f} [{low:+.3f}, {high:+.3f}]"
    assert cells[4] == f"{p_value:.3g}"
    mean, low, high = mean_with_bootstrap_ci([1.0, 1.0, 1.0, 0.0])
    assert cells[1] == f"{mean:.3f} [{low:.3f}, {high:.0f}]"
    assert cells[2] == "0 [0, 0]"


def test_an_unrecorded_task_is_dropped_from_the_means_and_counted_in_the_tally() -> None:
    arm = _arm(
        _BASELINE,
        _task("t1", TaskOutcome.ANSWERED, 4),
        replace(
            _task("t2", TaskOutcome.ANSWERED, 9), ending=TaskEnding(TaskOutcome.UNRECORDED, 9, 0)
        ),
    )

    report = render_report(_plan(("t1", "t2")), [arm, arm])

    assert _cells(report, _PENALISED)[1] == "4 [4, 4]"
    assert _cells(report, "budget-exhausted rate")[5] == "1"
    assert _cells(report, "outcome: unrecorded")[1] == "1"
    # The raw graph count still measures it.
    assert _cells(report, "turns (per task)")[5] == "2"


def test_the_tail_row_prints_each_arms_p90_and_their_difference() -> None:
    before = [1, 1, 2, 2, 3, 4, 9, 11]
    after = [1, 1, 1, 2, 2, 2, 3, 17]
    baseline = _arm(
        _BASELINE,
        *(
            _task(f"t{i}", TaskOutcome.ANSWERED, 4, calls_after_first_gold=v)
            for i, v in enumerate(before)
        ),
    )
    candidate = _arm(
        _CANDIDATE,
        *(
            _task(f"t{i}", TaskOutcome.ANSWERED, 4, calls_after_first_gold=v)
            for i, v in enumerate(after)
        ),
    )

    report = render_report(_plan(tuple(f"t{i}" for i in range(8))), [baseline, candidate])

    cells = _cells(report, "calls after first gold (p90)")
    assert percentile(before, 0.9) == pytest.approx(9.6)
    assert percentile(after, 0.9) == pytest.approx(7.2)
    assert cells[1:] == ["9.600", "7.200", "-2.400", "n/a", "n/a"]
    assert _cells(report, "calls after first gold (total)")[1:3] == ["33", "29"]


def test_the_near_cap_count_and_the_reserved_finalize_row() -> None:
    arm = _arm(
        _BASELINE,
        _task("t1", TaskOutcome.ANSWERED, 12),
        _task("t2", TaskOutcome.ANSWERED, 11),
        _task("t3", TaskOutcome.ANSWERED, 10),
    )

    report = render_report(_plan(("t1", "t2", "t3")), [arm, arm])

    assert _cells(report, "near cap")[1] == "2"
    # Nothing finalizes yet: undefined, never a measured zero.
    assert _cells(report, "finalize format failures")[1:3] == ["n/a", "n/a"]


def test_the_uncached_rows_read_not_available_without_a_usage_sidecar() -> None:
    measured = _task("t1", TaskOutcome.ANSWERED, 4, input_tokens=1000, cached_tokens=600)
    unmetered = _task("t1", TaskOutcome.ANSWERED, 4)

    report = render_report(_plan(("t1",)), [_arm(_BASELINE, unmetered), _arm(_CANDIDATE, measured)])

    assert _cells(report, "uncached tokens in (total)")[1:4] == ["n/a", "400", "n/a"]
    assert _cells(report, "uncached tokens in (per turn)")[1:3] == ["n/a", "100 [100, 100]"]


def test_wall_seconds_are_a_paired_row() -> None:
    before = _arm(_BASELINE, _task("t1", TaskOutcome.ANSWERED, 4, wall_seconds=30.0))
    after = _arm(_CANDIDATE, _task("t1", TaskOutcome.ANSWERED, 4, wall_seconds=20.0))

    cells = _cells(render_report(_plan(("t1",)), [before, after]), "wall seconds (per task)")

    assert cells[1:4] == ["30 [30, 30]", "20 [20, 20]", "-10.000 [-10.000, -10.000]"]


# --- the report: what it says about the rows ------------------------------------


def test_the_report_explains_the_13_and_the_outcome_rows() -> None:
    arm = _arm(_BASELINE, _task("t1", TaskOutcome.ANSWERED, 4))

    report = render_report(_plan(("t1",)), [arm, arm])

    assert "the budget + 1 = 13 turns" in report
    assert "`exhausted_finalized`" in report and "`starved_reply`" in report


def test_the_provenance_prints_each_arms_outcome_tally() -> None:
    baseline = _arm(
        _BASELINE,
        _task("t1", TaskOutcome.ANSWERED, 4),
        _task("t2", TaskOutcome.BUDGET_EXHAUSTED, 12),
    )
    candidate = _arm(
        _CANDIDATE, _task("t1", TaskOutcome.ANSWERED, 3), _task("t2", TaskOutcome.ANSWERED, 12)
    )

    report = render_report(_plan(("t1", "t2")), [baseline, candidate])

    assert "- outcomes, baseline: budget_exhausted 1, answered 1 (near cap: 1)" in report
    assert "- outcomes, candidate: answered 2 (near cap: 1)" in report


def test_an_arm_without_usage_sidecars_is_named_in_the_header(tmp_path: Path) -> None:
    unmetered = measure_arm(
        ArmSummary(
            role="baseline",
            commit="a" * 40,
            model="m",
            trace_root="",
            tasks=[_record(_trace(tmp_path, with_turns=False))],
            estimated_usd=0.0,
            halt_reason="completed",
            excluded=0,
        ),
        _BASELINE,
        workspace=Path("/ws"),
        max_agent_turns=_CAP,
    )

    report = render_report(_plan(("t1",)), [unmetered, unmetered])

    assert "- spend, baseline: 1 of 1 answered task(s) recorded NO usage sidecar" in report
    assert "`parallel calls per turn` and `turns after needle` read `n/a`" in report


# --- the plan promises exactly the rows -----------------------------------------


def test_the_plan_promises_exactly_the_rows_the_report_prints() -> None:
    assert (*(row.label for row in REPORT_ROWS), "description tokens") == REPORTED_METRICS


def test_the_plan_lists_every_promised_row() -> None:
    text = render_plan(_plan(("t1",)))

    for label in (_PENALISED, "budget-exhausted rate", "calls after first gold (p90)"):
        assert f"  - {label}\n" in text


# --- re-rendering a finished run written before outcomes existed ---------------


def _write_legacy_arm(
    out_dir: Path, role: str, trace_dir: Path, *, answer_chars: int, turns: int
) -> None:
    """One arm's ``arm.json`` exactly as a run before this change wrote it."""
    arm_dir = out_dir / role
    arm_dir.mkdir(parents=True)
    row = {
        "task_id": "t1",
        "trajectory_id": trace_dir.name,
        "trace_dir": str(trace_dir),
        "gold_files": ["a.py"],
        "turns": turns,
        "wall_seconds": 3.0,
        "answer_chars": answer_chars,
    }
    payload = {
        "role": role,
        "commit": "a" * 40,
        "model": "m",
        "trace_root": "",
        "tasks": [row],
        "estimated_usd": 0.0,
        "halt_reason": "completed",
        "excluded": 0,
    }
    (arm_dir / ARM_SUMMARY_FILENAME).write_text(json.dumps(payload), encoding="utf-8")


def test_report_only_back_fills_a_legacy_arm_against_the_plans_budget(
    tmp_path: Path, stub_command: object
) -> None:
    """The plan's budget (4 turns in this stub) is the only cap an old arm.json has."""
    from pydocs_eval.campaign.__main__ import main

    from ._fakes import before_after_argv, git_repo_with_two_descriptions

    repo = git_repo_with_two_descriptions(tmp_path)
    out_dir = tmp_path / "out"
    trace = write_ask_trajectory(
        tmp_path / "traces", calls=[("search_codebase", {"query": "q"}, 1)]
    )
    _write_legacy_arm(out_dir, "baseline", trace, answer_chars=47, turns=4)
    _write_legacy_arm(out_dir, "candidate", trace, answer_chars=300, turns=4)

    assert main(before_after_argv(tmp_path, repo, "--report-only")) == 0

    report = (out_dir / "before_after.md").read_text()
    assert _cells(report, "outcome: budget_exhausted")[1:3] == ["1", "0"]
    assert _cells(report, _PENALISED)[1:3] == ["5 [5, 5]", "4 [4, 4]"]
    assert "- outcomes, baseline: budget_exhausted 1 (near cap: 1)" in report


def test_a_typed_budget_error_raised_with_defaults_stays_infra_through_the_wrapper(
    tmp_path: Path,
) -> None:
    """The real timeout wrapper, a product error carrying no trace: retried, then excluded."""
    from pydocs_eval.optimize.ask_binding import TimeoutBoundedAskRunner
    from pydocs_mcp.harness.core.run_contract import TurnBudgetExceededError

    settings = _settings(tmp_path)
    inner = ScriptedArmRunner({"t1": TurnBudgetExceededError(turn_limit=_CAP)})
    wrapped = TimeoutBoundedAskRunner(inner=inner, task_timeout_seconds=60.0, max_agent_turns=_CAP)

    summary = asyncio.run(run_arm(settings, (eval_task("t1"),), make_runner=lambda _s, _w: wrapped))

    assert summary.tasks == []
    assert summary.excluded == 1
    assert inner.seen == ["t1", "t1"]
    queue = (Path(settings.out_dir) / "queue.jsonl").read_text(encoding="utf-8")
    assert "infra retry" in queue and "infra excluded" in queue
