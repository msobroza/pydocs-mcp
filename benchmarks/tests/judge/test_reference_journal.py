"""The writer's batch journal: every batch id recorded the moment it is submitted.

The Batch API has no cancel endpoint, so a batch the run stops waiting on — or
loses to a crash — still runs and bills. The journal is how the next run finds
it: a batch submitted and not settled is collected by id, one settled and not
deleted is deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.judge.reference_journal import ReferenceJournal, ReferenceJournalError
from pydocs_eval.judge.reference_writer import AskedRow, SubmittedBatch, WriterRole

# Every journal below stamps its submits at this time, so a read-back batch carries it.
_NOW = 1_790_773_417.0
_BATCH = SubmittedBatch(
    "batch_1",
    WriterRole.PRIMARY,
    (AskedRow("t1", "aa" * 32), AskedRow("t2", "bb" * 32)),
    submitted_at=_NOW,
)
_FALLBACK = SubmittedBatch(
    "batch_2",
    WriterRole.FALLBACK,
    (AskedRow("t3", "cc" * 32, "batch_0 ended expired"),),
    submitted_at=_NOW,
)


def _journal(path: Path) -> ReferenceJournal:
    return ReferenceJournal(path, clock=lambda: _NOW)


def test_a_submitted_batch_is_on_disk_at_once_and_unsettled(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "writer.journal.jsonl")

    journal.record_submitted(_BATCH)

    assert len(journal.path.read_text(encoding="utf-8").splitlines()) == 1
    assert journal.unsettled() == (_BATCH,)
    assert journal.undeleted() == ()


def test_a_settled_batch_waits_for_its_deletion(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "writer.journal.jsonl")
    journal.record_submitted(_BATCH)
    journal.record_submitted(_FALLBACK)

    journal.record_settled(["batch_1"])

    assert journal.unsettled() == (_FALLBACK,)
    assert journal.undeleted() == ("batch_1",)


def test_a_deleted_batch_leaves_the_journal_s_to_do_lists(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "writer.journal.jsonl")
    journal.record_submitted(_BATCH)
    journal.record_settled(["batch_1"])

    journal.record_deleted("batch_1")

    assert (journal.unsettled(), journal.undeleted()) == ((), ())


def test_a_fresh_journal_holds_nothing(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "nested" / "writer.journal.jsonl")

    assert (journal.unsettled(), journal.undeleted()) == ((), ())


def test_a_second_journal_on_the_same_file_reads_the_first_one_s_batches(tmp_path: Path) -> None:
    path = tmp_path / "writer.journal.jsonl"
    _journal(path).record_submitted(_FALLBACK)

    assert _journal(path).unsettled() == (_FALLBACK,)


def test_a_malformed_line_is_refused_naming_it(tmp_path: Path) -> None:
    path = tmp_path / "writer.journal.jsonl"
    path.write_text('{"event": "submitted"}\n', encoding="utf-8")

    with pytest.raises(ReferenceJournalError, match="line 1"):
        _journal(path).unsettled()


def test_a_lost_batch_is_neither_collected_nor_deleted_again(tmp_path: Path) -> None:
    journal = _journal(tmp_path / "writer.journal.jsonl")
    journal.record_submitted(_BATCH)

    journal.record_lost("batch_1")

    assert (journal.unsettled(), journal.undeleted()) == ((), ())


def test_a_submitted_batch_keeps_the_time_it_was_submitted(tmp_path: Path) -> None:
    journal = ReferenceJournal(tmp_path / "writer.journal.jsonl", clock=lambda: 1790773417.0)

    journal.record_submitted(_BATCH)

    (batch,) = journal.unsettled()
    assert batch.submitted_at == 1790773417.0
