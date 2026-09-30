"""The reference writer's batch journal: what was submitted, stored, and deleted, by batch id.

The Batch API runs a batch for up to 24 h and has no cancel endpoint, so a
batch the writer stops waiting on — past its deadline, or lost to a crash —
still runs and bills. Every batch id is appended here the moment its submit
returns, before the first poll; the next run collects each batch submitted but
not ``settled`` (its outcomes absorbed and its rows stored) and deletes each
batch settled but not ``deleted``. One JSON line per event, flushed and synced
as it is written.

Example:
    >>> journal = ReferenceJournal(Path("writer.journal.jsonl"))  # doctest: +SKIP
    >>> journal.record_submitted(batch); journal.unsettled()  # doctest: +SKIP
    (SubmittedBatch(batch_id='batch_1', ...),)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pydocs_eval.judge.reference_writer import AskedRow, SubmittedBatch, WriterRole


class ReferenceJournalError(ValueError):
    """A journal line outside the declared shape."""


class _JournalEvent(StrEnum):
    SUBMITTED = "submitted"
    SETTLED = "settled"
    DELETED = "deleted"


@dataclass(frozen=True, slots=True)
class ReferenceJournal:
    """The append-only journal at ``path``; a missing file is an empty journal."""

    path: Path

    def record_submitted(self, batch: SubmittedBatch) -> None:
        """Record ``batch`` as running: the next run collects it unless it is settled first."""
        rows = [
            {
                "task_id": r.task_id,
                "prompt_hash": r.prompt_hash,
                "fallback_reason": r.fallback_reason,
            }
            for r in batch.rows
        ]
        self._append(_JournalEvent.SUBMITTED, batch.batch_id, role=batch.role.value, rows=rows)

    def record_settled(self, batch_ids: Iterable[str]) -> None:
        """Record each batch's outcomes as absorbed and its rows as stored."""
        for batch_id in batch_ids:
            self._append(_JournalEvent.SETTLED, batch_id)

    def record_deleted(self, batch_id: str) -> None:
        """Record ``batch_id`` as deleted upstream."""
        self._append(_JournalEvent.DELETED, batch_id)

    def unsettled(self) -> tuple[SubmittedBatch, ...]:
        """Every batch submitted and not settled, in submit order."""
        entries = self._entries()
        settled = {entry.batch_id for entry in entries if entry.event is _JournalEvent.SETTLED}
        return tuple(
            entry.batch
            for entry in entries
            if entry.batch is not None and entry.batch_id not in settled
        )

    def undeleted(self) -> tuple[str, ...]:
        """Every batch settled and not deleted, in settle order."""
        entries = self._entries()
        deleted = {entry.batch_id for entry in entries if entry.event is _JournalEvent.DELETED}
        settled = (entry.batch_id for entry in entries if entry.event is _JournalEvent.SETTLED)
        return tuple(dict.fromkeys(batch_id for batch_id in settled if batch_id not in deleted))

    def _entries(self) -> list[_JournalEntry]:
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [_entry_of(number, line) for number, line in enumerate(lines, 1) if line]

    def _append(self, event: _JournalEvent, batch_id: str, **details: object) -> None:
        line = json.dumps({"event": event.value, "batch_id": batch_id, **details}, sort_keys=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as journal:
            journal.write(f"{line}\n")
            journal.flush()
            os.fsync(journal.fileno())


@dataclass(frozen=True, slots=True)
class _JournalEntry:
    """One journal line: its event, its batch, and — for a submit — what the batch asked."""

    event: _JournalEvent
    batch_id: str
    batch: SubmittedBatch | None = None


def _entry_of(number: int, line: str) -> _JournalEntry:
    where = f"journal line {number}"
    try:
        raw = json.loads(line)
        event, batch_id = _JournalEvent(raw["event"]), str(raw["batch_id"])
        batch = _batch_of(batch_id, raw) if event is _JournalEvent.SUBMITTED else None
    except (KeyError, TypeError, ValueError) as exc:
        raise ReferenceJournalError(
            f"{where}: {exc!r}, expected an event and a batch_id, and for a submit a role "
            "and rows of task_id, prompt_hash and fallback_reason"
        ) from None
    return _JournalEntry(event, batch_id, batch)


def _batch_of(batch_id: str, raw: Mapping[str, object]) -> SubmittedBatch:
    """The submitted batch a journal line records; a malformed one raises ``KeyError``/``TypeError``."""
    listed = raw["rows"]
    if not isinstance(listed, list):
        raise TypeError(f"rows = {listed!r}, expected a list")
    rows = tuple(
        AskedRow(str(row["task_id"]), str(row["prompt_hash"]), str(row["fallback_reason"]))
        for row in listed
    )
    return SubmittedBatch(batch_id, WriterRole(str(raw["role"])), rows)


__all__ = ("ReferenceJournal", "ReferenceJournalError")
