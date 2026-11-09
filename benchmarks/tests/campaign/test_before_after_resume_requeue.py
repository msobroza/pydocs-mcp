"""A resumed arm answers again every DONE task it holds no row for, and counts old exclusions.

ADR 0014's DONE means "trace present + metrics computable". A DONE task whose
row was dropped (its trace lost ``server_events.jsonl``) or lost (``arm.json``
went missing) breaks that, and the ledger never re-dispatches a DONE task — so
the arm used to finish ``completed`` with fewer tasks than the plan. The resume
now appends a QUEUED line one attempt later (so the re-run's spend is a new
spend identity, never deduplicated away) and logs one event naming them.

Exclusions are read from the ledger too, so those an earlier process made
survive a resume, in every checkpoint and in the final summary.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.campaign.ledger import (
    LEDGER_FILENAME,
    CampaignLedger,
    LedgerRecord,
    WorkItem,
    WorkState,
)
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME

from ._fakes import FakeHarnessRunner, eval_task
from .test_before_after_arm_records import arm_settings
from .test_before_after_resume import ArmKilled, KilledAtTask

_REQUEUED_EVENT = "before_after_resume_requeued_untraced_rows"
# ``arm_settings``' booking per rollout: what the ledger's spend sums.
_USD_PER_ROLLOUT = 0.25


def _run_over(
    settings: ArmSettings, task_ids: tuple[str, ...], runner: FakeHarnessRunner | None = None
) -> tuple[ArmSummary, FakeHarnessRunner]:
    runner = runner or FakeHarnessRunner(Path(settings.trace_root))
    tasks = tuple(eval_task(task_id) for task_id in task_ids)
    summary = asyncio.run(run_arm(settings, tasks, make_runner=lambda s, w: runner))
    return summary, runner


def _ledger(settings: ArmSettings) -> CampaignLedger:
    return CampaignLedger(path=Path(settings.out_dir) / LEDGER_FILENAME)


def _requeue_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    events = [json.loads(record.getMessage()) for record in caplog.records]
    return [event for event in events if event.get("event") == _REQUEUED_EVENT]


def test_a_resumed_arm_reruns_a_done_task_whose_trace_was_lost(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = arm_settings(tmp_path)
    first, _ = _run_over(settings, ("a", "b"))
    (Path(first.tasks[1].trace_dir) / SERVER_EVENTS_FILENAME).unlink()

    with caplog.at_level(logging.WARNING):
        summary, runner = _run_over(settings, ("a", "b"))

    assert [sample["record_id"] for sample in runner.samples] == ["b"]
    assert [task.task_id for task in summary.tasks] == ["a", "b"]
    assert read_arm_summary(Path(settings.out_dir)).halt_reason == "completed"
    latest = _ledger(settings).latest(WorkItem(cell=settings.role, instance_id="b"))
    assert latest is not None and (latest.state, latest.attempt) == (WorkState.DONE, 1)
    # a, b and b again: the re-run is a new spend identity, so it counts.
    assert _ledger(settings).total_spend() == pytest.approx(3 * _USD_PER_ROLLOUT)
    (event,) = _requeue_events(caplog)
    assert (event["role"], event["task_ids"]) == (settings.role, ["b"])


def test_a_resumed_arm_reruns_every_done_task_whose_row_was_lost(tmp_path: Path) -> None:
    """No ``arm.json`` at all: every DONE task in the ledger has lost its row."""
    settings = arm_settings(tmp_path)
    _run_over(settings, ("a", "b"))
    (Path(settings.out_dir) / ARM_SUMMARY_FILENAME).unlink()

    summary, runner = _run_over(settings, ("a", "b"))

    assert [sample["record_id"] for sample in runner.samples] == ["a", "b"]
    assert [task.task_id for task in summary.tasks] == ["a", "b"]


def test_a_resume_with_every_row_in_place_requeues_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = arm_settings(tmp_path)
    _run_over(settings, ("a", "b"))

    with caplog.at_level(logging.WARNING):
        _summary, runner = _run_over(settings, ("a", "b"))

    assert runner.samples == [] and _requeue_events(caplog) == []


def _excluded_by_an_earlier_process(settings: ArmSettings, task_id: str) -> None:
    _ledger(settings).record(
        LedgerRecord(
            cell=settings.role,
            instance_id=task_id,
            state=WorkState.EXCLUDED,
            attempt=1,
            cost_usd=_USD_PER_ROLLOUT,
            detail="infra excluded",
        )
    )


def test_a_resumed_arm_counts_exclusions_from_the_earlier_process(tmp_path: Path) -> None:
    settings = arm_settings(tmp_path)
    _excluded_by_an_earlier_process(settings, "c")

    summary, runner = _run_over(settings, ("a", "b", "c"))

    assert [sample["record_id"] for sample in runner.samples] == ["a", "b"]
    assert summary.excluded == 1


def test_a_checkpoint_carries_the_earlier_processs_exclusions(tmp_path: Path) -> None:
    settings = arm_settings(tmp_path)
    _excluded_by_an_earlier_process(settings, "c")
    killed = KilledAtTask(Path(settings.trace_root), kill_at="b")

    with pytest.raises(ArmKilled):
        _run_over(settings, ("a", "b", "c"), runner=killed)

    checkpoint = read_arm_summary(Path(settings.out_dir))
    assert (checkpoint.halt_reason, checkpoint.excluded) == ("in_progress", 1)
