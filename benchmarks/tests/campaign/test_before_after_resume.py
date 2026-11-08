"""A killed arm, resumed from its queue ledger, keeps every task it already finished.

The ledger skips DONE tasks on a restart, so before this fix a resumed arm's
``arm.json`` listed only the tasks of its LAST process: everything a killed
first process finished — paid for, traced — vanished from the report. Now each
answered task is checkpointed into ``arm.json`` as it lands, and a restart seeds
from it, dropping (and logging) only rows whose trace no longer holds its
server-events sidecar.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm import (
    ARM_SUMMARY_FILENAME,
    ArmSettings,
    ArmSummary,
    read_arm_summary,
    run_arm,
)
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME

from ._fakes import FakeHarnessRunner, eval_task
from .test_before_after_arm_records import arm_settings

_TASKS = tuple(eval_task(task_id) for task_id in ("t1", "t2", "t3"))


class ArmKilled(BaseException):
    """What a SIGKILL looks like from inside the arm: nothing below it gets to clean up."""


@dataclass
class KilledAtTask(FakeHarnessRunner):
    """Answers every task until ``kill_at``, then the process dies mid-rollout."""

    kill_at: str = ""

    async def run(self, sample: Mapping[str, object], guidance: Mapping[str, str]) -> object:
        if sample["record_id"] == self.kill_at:
            raise ArmKilled(self.kill_at)
        return await FakeHarnessRunner.run(self, sample, guidance)


def _killed_at_t3(settings: ArmSettings) -> None:
    runner = KilledAtTask(Path(settings.trace_root), kill_at="t3")
    with pytest.raises(ArmKilled):
        asyncio.run(run_arm(settings, _TASKS, make_runner=lambda s, w: runner))


def _resumed(settings: ArmSettings) -> tuple[ArmSummary, FakeHarnessRunner]:
    runner = FakeHarnessRunner(Path(settings.trace_root))
    summary = asyncio.run(run_arm(settings, _TASKS, make_runner=lambda s, w: runner))
    return summary, runner


def test_a_resumed_arm_keeps_the_tasks_its_ledger_already_finished(tmp_path: Path) -> None:
    settings = arm_settings(tmp_path)
    _killed_at_t3(settings)

    summary, runner = _resumed(settings)

    assert [sample["record_id"] for sample in runner.samples] == ["t3"]
    assert [task.task_id for task in summary.tasks] == ["t1", "t2", "t3"]
    assert summary.halt_reason == "completed"
    assert read_arm_summary(Path(settings.out_dir)) == summary


def test_a_killed_arm_has_already_recorded_every_task_it_finished(tmp_path: Path) -> None:
    """Checkpointed per task: a kill loses at most the rollout that was running."""
    settings = arm_settings(tmp_path)

    _killed_at_t3(settings)

    checkpoint = read_arm_summary(Path(settings.out_dir))
    assert [task.task_id for task in checkpoint.tasks] == ["t1", "t2"]
    assert checkpoint.halt_reason == "in_progress"


def test_a_row_whose_trace_lost_its_sidecar_is_dropped_and_logged(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    settings = arm_settings(tmp_path)
    _killed_at_t3(settings)
    lost = read_arm_summary(Path(settings.out_dir)).tasks[0]
    (Path(lost.trace_dir) / SERVER_EVENTS_FILENAME).unlink()

    with caplog.at_level(logging.WARNING):
        summary, runner = _resumed(settings)

    assert [task.task_id for task in summary.tasks] == ["t2", "t3"]
    assert [sample["record_id"] for sample in runner.samples] == ["t3"]
    (event,) = [json.loads(r.getMessage()) for r in caplog.records if "dropped" in r.getMessage()]
    assert event["event"] == "before_after_resume_dropped_untraced_rows"
    assert event["task_ids"] == ["t1"] and event["role"] == settings.role


def test_a_finished_arm_run_again_answers_nothing_and_keeps_every_row(tmp_path: Path) -> None:
    settings = arm_settings(tmp_path)
    first, _ = _resumed(settings)

    again, runner = _resumed(settings)

    assert runner.samples == []
    assert again.tasks == first.tasks


def test_rows_of_another_role_are_never_seeded(tmp_path: Path) -> None:
    """A summary is one arm's: a stray ``arm.json`` of the other role seeds nothing."""
    settings = arm_settings(tmp_path)
    _killed_at_t3(settings)
    path = Path(settings.out_dir) / ARM_SUMMARY_FILENAME
    payload = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**payload, "role": "baseline"}), encoding="utf-8")

    with pytest.raises(MeasurementPlanError, match=r"role 'baseline'.*'candidate'"):
        _resumed(settings)
