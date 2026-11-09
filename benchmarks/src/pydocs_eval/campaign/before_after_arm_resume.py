"""A killed arm, restarted, keeps every task it already finished.

The campaign ledger (``queue.jsonl``) never re-dispatches a DONE task, which is
the point of resume — but the arm's summary used to be built from the answers
held in memory by the LAST process only, so a restart wrote an ``arm.json``
missing every task a killed first process had finished (and paid for).

Two halves close that gap:

- the arm checkpoints ``arm.json`` after every answered task (halt reason
  ``HaltReason.IN_PROGRESS``), BEFORE the ledger marks the task DONE — so a DONE
  task always has its row on disk;
- a (re)started arm seeds its answers from that file with
  :func:`rows_to_resume`, dropping — and logging — any row whose trace
  directory no longer holds its server-events sidecar, since nothing could
  measure it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Collection, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_arm_records import (
    ARM_SUMMARY_FILENAME,
    ArmSummary,
    ArmTaskRecord,
    read_arm_summary,
)
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME, trace_recorded

# The structured event a resume emits for the rows it could not keep.
_DROPPED_UNTRACED_ROWS_EVENT = "before_after_resume_dropped_untraced_rows"

# How many foreign task ids a refusal names: enough to recognise the other draw.
_FOREIGN_IDS_NAMED = 5

log = logging.getLogger("pydocs-eval.campaign.before-after-arm")


@dataclass(frozen=True, slots=True)
class ArmRunIdentity:
    """What rows must have been answered under to be seeded into this arm."""

    role: str
    commit: str
    model: str
    settings_digest: str


def rows_to_resume(
    out_dir: Path, expected: ArmRunIdentity, task_ids: Collection[str]
) -> dict[str, ArmTaskRecord]:
    """The answered rows an earlier process of THIS arm left in ``out_dir``, by task id.

    ``{}`` when no ``arm.json`` is there yet (a fresh arm). ``task_ids`` are the
    tasks this process carries; the parent checks the same before it writes
    anything (:func:`refuse_rows_of_another_run`), so this is the backstop.

    Raises:
        MeasurementPlanError: the ``arm.json`` there was answered by another
            role, commit, model or settings digest, or over tasks this arm does
            not carry — see :func:`refuse_rows_of_another_run`.
    """
    if not (out_dir / ARM_SUMMARY_FILENAME).is_file():
        return {}
    summary = read_arm_summary(out_dir)
    refuse_rows_of_another_run(summary, expected, task_ids, out_dir)
    kept = [row for row in summary.tasks if trace_recorded(row.trajectory_id, Path(row.trace_dir))]
    _log_dropped(expected.role, [row for row in summary.tasks if row not in kept])
    return {row.task_id: row for row in kept}


def refuse_rows_of_another_run(
    summary: ArmSummary, expected: ArmRunIdentity, task_ids: Collection[str], out_dir: Path
) -> None:
    """Refuse to resume rows another run answered: its identity, or its task set.

    A row outside ``task_ids`` (another ``--split``, a shorter ``--limit``, another
    draw) would be dropped by the arm's first checkpoint, losing a paid answer. A
    longer ``--limit`` keeps every recorded row inside the task set, so it resumes.

    Raises:
        MeasurementPlanError: naming each differing field with both values, or
            the count of foreign rows and up to five of their ids.
    """
    _refuse_foreign_rows(summary, expected, out_dir)
    _refuse_rows_outside_the_tasks(summary, task_ids, out_dir)


def _refuse_rows_outside_the_tasks(
    summary: ArmSummary, task_ids: Collection[str], out_dir: Path
) -> None:
    carried = set(task_ids)
    foreign = [row.task_id for row in summary.tasks if row.task_id not in carried]
    if not foreign:
        return
    named = ", ".join(repr(task_id) for task_id in foreign[:_FOREIGN_IDS_NAMED])
    more = ", …" if len(foreign) > _FOREIGN_IDS_NAMED else ""
    raise MeasurementPlanError(
        f"{out_dir / ARM_SUMMARY_FILENAME} holds rows for {len(foreign)} task(s) outside "
        f"this arm's tasks ({named}{more}); an arm's tasks are the plan's --split, --limit "
        "and draw: resume it under the same, or give this run a fresh --out"
    )


def _refuse_foreign_rows(summary: ArmSummary, expected: ArmRunIdentity, out_dir: Path) -> None:
    mismatches = [
        f"{field.name}: recorded {getattr(summary, field.name)!r}, "
        f"this arm {getattr(expected, field.name)!r}"
        for field in fields(ArmRunIdentity)
        if getattr(summary, field.name) != getattr(expected, field.name)
    ]
    if mismatches:
        raise MeasurementPlanError(
            f"{out_dir / ARM_SUMMARY_FILENAME} holds rows another run answered "
            f"({'; '.join(mismatches)}); resume it under the settings it ran, or give "
            "this run a fresh --out"
        )


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


__all__ = ("ArmRunIdentity", "refuse_rows_of_another_run", "rows_to_resume")
