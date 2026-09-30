"""What ``write-references`` prints: the plan before any spend, and what a run did."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.campaign.reference_writer_run import (
    BatchDeletionFailure,
    ReferenceRunReport,
    ReferenceWriterPlan,
)
from pydocs_eval.campaign.reference_writer_text import render_plan, render_report
from pydocs_eval.datasets.base_dataset import ReferenceAnswer
from pydocs_eval.datasets.reference_answers import ReferenceRow
from pydocs_eval.judge.reference_journal import ReferenceJournal
from pydocs_eval.judge.reference_writer import (
    AskedRow,
    ReferenceGap,
    ReferenceGapKind,
    ReferenceWriteResult,
    SubmittedBatch,
    WriterRole,
)
from pydocs_eval.judge.role_config import ReferenceWriterConfig

_WRITER = ReferenceWriterConfig(
    model="anthropic/claude-opus-5.5:batch", fallback_model="anthropic/claude-sonnet-5:batch"
)


def _report(
    *gaps: ReferenceGap,
    kept_open: tuple[str, ...] = (),
    lost: tuple[str, ...] = (),
    not_deleted: tuple[BatchDeletionFailure, ...] = (),
) -> ReferenceRunReport:
    rows = (
        ReferenceRow("t1", ReferenceAnswer("x", "anthropic/claude-opus-5.5", "h")),
        ReferenceRow("t2", ReferenceAnswer("y", "anthropic/claude-sonnet-5", "h"), "expired"),
    )
    result = ReferenceWriteResult(
        rows=rows,
        gaps=gaps,
        ended_batches=("b1",),
        kept_open=kept_open,
        cost_usd=0.4321,
        uncosted_answers=1,
    )
    return ReferenceRunReport(result, deleted=("b1",), not_deleted=not_deleted, lost=lost)


def test_the_plan_names_the_counts_the_pins_and_the_open_batches(tmp_path: Path) -> None:
    batch = SubmittedBatch("b0", WriterRole.PRIMARY, (AskedRow("t3", "h"),))
    plan = ReferenceWriterPlan(
        tasks=(),
        stored=frozenset({"t1"}),
        sources=(),
        prompt_chars=4000,
        to_collect=(batch,),
        to_delete=("b9",),
        out=tmp_path / "refs.jsonl",
        journal=ReferenceJournal(tmp_path / "j.jsonl"),
    )

    text = render_plan("repoqa-qa/small_dev", plan, _WRITER)

    assert "stored already: 1, to write: 0" in text
    assert "anthropic/claude-opus-5.5:batch at high" in text
    assert "about 1000 tokens" in text
    assert "batches to collect first: b0 (1 rows)" in text
    assert "batches to delete first: b9" in text


def test_the_report_counts_the_rows_by_writer_and_prices_the_run() -> None:
    text = render_report(_report())

    assert "written: 2 (writer 1, fallback 1)" in text
    assert "$0.4321 (1 answers unpriced)" in text
    assert "batches deleted: b1" in text


def test_a_failed_submit_warns_that_it_may_have_been_accepted() -> None:
    gap = ReferenceGap("t3", ReferenceGapKind.NOT_SUBMITTED, "batch submit failed: timed out")

    text = render_report(_report(gap))

    assert "t3 [not_submitted]" in text
    assert "may have been accepted" in text
    assert "GET /api/v1/batches" in text


def test_a_batch_still_running_says_how_to_collect_it() -> None:
    gap = ReferenceGap("t3", ReferenceGapKind.STILL_RUNNING, "still running", batch_id="b5")

    text = render_report(_report(gap))

    assert "t3 [still_running] batch b5" in text
    assert "re-run this command to collect" in text


def test_kept_lost_and_undeleted_batches_are_each_named() -> None:
    failure = BatchDeletionFailure("b7", "HTTP 409: still processing")

    text = render_report(_report(kept_open=("b6",), lost=("b8",), not_deleted=(failure,)))

    assert "kept open for the tasks another run covers: b6" in text
    assert "lost upstream (tasks asked again): b8" in text
    assert "not deleted b7: HTTP 409: still processing" in text
