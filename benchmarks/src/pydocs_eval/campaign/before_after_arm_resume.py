"""A killed arm, restarted, keeps every task it already finished.

The campaign ledger (``queue.jsonl``) never re-dispatches a DONE task, which is
the point of resume — but the arm's summary used to be built from the answers
held in memory by the LAST process only, so a restart wrote an ``arm.json``
missing every task a killed first process had finished (and paid for).

Two halves close that gap:

- the arm checkpoints ``arm.json`` after every answered task (halt reason
  :data:`ARM_IN_PROGRESS`), BEFORE the ledger marks the task DONE — so a DONE
  task always has its row on disk;
- a (re)started arm seeds its answers from that file with
  :func:`rows_to_resume`, dropping — and logging — any row whose trace
  directory no longer holds its server-events sidecar, since nothing could
  measure it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm_records import (
    ARM_SUMMARY_FILENAME,
    ArmTaskRecord,
    read_arm_summary,
)
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME, trace_recorded

# The halt reason a checkpoint carries: the arm had not finished when it was written.
ARM_IN_PROGRESS = "in_progress"

# The structured event a resume emits for the rows it could not keep.
_DROPPED_UNTRACED_ROWS_EVENT = "before_after_resume_dropped_untraced_rows"

log = logging.getLogger("pydocs-eval.campaign.before-after-arm")


def rows_to_resume(out_dir: Path, role: str) -> dict[str, ArmTaskRecord]:
    """The answered rows an earlier process of this arm left in ``out_dir``, by task id.

    ``{}`` when no ``arm.json`` is there yet (a fresh arm).

    Raises:
        MeasurementPlanError: the ``arm.json`` there is another role's — seeding
            it would mix two arms' answers into one column.
    """
    if not (out_dir / ARM_SUMMARY_FILENAME).is_file():
        return {}
    summary = read_arm_summary(out_dir)
    if summary.role != role:
        raise MeasurementPlanError(
            f"{out_dir / ARM_SUMMARY_FILENAME} is role {summary.role!r}, but this arm is "
            f"{role!r}; give each arm its own directory"
        )
    kept = [row for row in summary.tasks if trace_recorded(row.trajectory_id, Path(row.trace_dir))]
    _log_dropped(role, [row for row in summary.tasks if row not in kept])
    return {row.task_id: row for row in kept}


def _log_dropped(role: str, dropped: Sequence[ArmTaskRecord]) -> None:
    """Name the finished tasks a resume could not keep, and why."""
    if not dropped:
        return
    log.warning(
        json.dumps(
            {
                "event": _DROPPED_UNTRACED_ROWS_EVENT,
                "role": role,
                "task_ids": [row.task_id for row in dropped],
                "reason": f"trace directory holds no {SERVER_EVENTS_FILENAME}",
            }
        )
    )


__all__ = ("ARM_IN_PROGRESS", "rows_to_resume")
