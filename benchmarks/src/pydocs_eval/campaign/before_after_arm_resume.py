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
  measure it. :class:`ArmWorkQueue` then queues every DONE task left without a
  row again, so the arm answers it rather than finishing short.
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
from pydocs_eval.campaign.ledger import (
    LEDGER_FILENAME,
    CampaignLedger,
    LedgerRecord,
    WorkItem,
    WorkState,
)
from pydocs_eval.trajectory.server_capture import SERVER_EVENTS_FILENAME, trace_recorded

# The structured event a resume emits for the rows it could not keep.
_DROPPED_UNTRACED_ROWS_EVENT = "before_after_resume_dropped_untraced_rows"

# The structured event a resume emits for the DONE tasks it queues again, and
# the reason both it and their QUEUED ledger lines carry.
_REQUEUED_UNTRACED_ROWS_EVENT = "before_after_resume_requeued_untraced_rows"
_REQUEUED_DETAIL = (
    f"requeued: done, but no answered row (its trace lost {SERVER_EVENTS_FILENAME}, "
    f"or {ARM_SUMMARY_FILENAME} lost the row)"
)

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


@dataclass(frozen=True, slots=True)
class ArmWorkQueue:
    """One arm's view of its campaign ledger: built once, shared by the resume and the loop.

    One cell per arm: the ledger's ``(cell, instance)`` key then keeps the two
    arms' rows apart even when they share one file.
    """

    ledger: CampaignLedger
    role: str
    task_ids: tuple[str, ...]

    @classmethod
    def open(cls, out_dir: Path, role: str, task_ids: Sequence[str]) -> ArmWorkQueue:
        return cls(CampaignLedger(path=out_dir / LEDGER_FILENAME), role, tuple(task_ids))

    def work_items(self) -> list[WorkItem]:
        return [self._item(task_id) for task_id in self.task_ids]

    def resumed_rows(self, out_dir: Path, expected: ArmRunIdentity) -> dict[str, ArmTaskRecord]:
        """:func:`rows_to_resume`, then every DONE task left without a row queued again."""
        answered = rows_to_resume(out_dir, expected, self.task_ids)
        self._requeue_done_tasks_without_rows(answered)
        return answered

    def excluded(self) -> int:
        """This arm's EXCLUDED tasks, whichever process excluded them."""
        return sum(self._state(task_id) == WorkState.EXCLUDED for task_id in self.task_ids)

    def _requeue_done_tasks_without_rows(self, answered: Collection[str]) -> None:
        """ADR 0014's DONE is "trace present + metrics computable": a rowless DONE is not.

        The ledger never re-dispatches DONE, so a dropped or lost row would leave
        the arm ``completed`` short of the plan. The QUEUED line is one attempt
        later on purpose: the ledger counts spend once per ``(cell, instance,
        attempt, state)``, so a re-run DONE at the old attempt would never reach
        the R6 ceiling.
        """
        rowless = [
            task_id
            for task_id in self.task_ids
            if task_id not in answered and self._state(task_id) == WorkState.DONE
        ]
        for task_id in rowless:
            self.ledger.record(self._requeued(task_id))
        _log_requeued(self.role, rowless)

    def _requeued(self, task_id: str) -> LedgerRecord:
        return LedgerRecord(
            cell=self.role,
            instance_id=task_id,
            state=WorkState.QUEUED,
            attempt=self.ledger.attempt_count(self._item(task_id)) + 1,
            cost_usd=0.0,
            detail=_REQUEUED_DETAIL,
        )

    def _state(self, task_id: str) -> WorkState | None:
        latest = self.ledger.latest(self._item(task_id))
        return None if latest is None else latest.state

    def _item(self, task_id: str) -> WorkItem:
        return WorkItem(cell=self.role, instance_id=task_id)


def _log_requeued(role: str, task_ids: Sequence[str]) -> None:
    """Name the DONE tasks a resume answers again: the operator pays for each twice."""
    if not task_ids:
        return
    log.warning(
        json.dumps(
            {
                "event": _REQUEUED_UNTRACED_ROWS_EVENT,
                "role": role,
                "task_ids": list(task_ids),
                "reason": _REQUEUED_DETAIL,
            }
        )
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


__all__ = ("ArmRunIdentity", "ArmWorkQueue", "refuse_rows_of_another_run", "rows_to_resume")
