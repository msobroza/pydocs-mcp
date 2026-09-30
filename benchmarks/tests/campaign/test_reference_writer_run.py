"""One ``write-references`` run end to end, offline: plan, collect, write, store, delete.

The plan spends nothing. A run first deletes what an earlier run stored but
could not delete, then collects every batch an earlier run left running (by
id, never re-submitted), then writes the tasks still without a reference,
stores the rows, marks each ended batch settled in the journal and deletes it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import EvalTask, ReferenceAnswer
from pydocs_eval.datasets.reference_answers import (
    ReferenceRow,
    read_reference_rows,
    write_reference_rows,
)
from pydocs_eval.datasets.repo_qa import RepoQaQuestionDataset
from pydocs_eval.datasets.repoqa import RepoQADataset
from pydocs_eval.gold_extensions import GOLD_FILE_EXTENSIONS
from pydocs_eval.campaign.reference_writer_run import (
    ReferenceRunReport,
    ReferenceWriterPlan,
    plan_reference_writing,
    run_reference_writing,
)
from pydocs_eval.judge.chat_wire import ChatFailureKind
from pydocs_eval.judge.judge_errors import JudgeRequestError
from pydocs_eval.judge.openrouter_chat import FakeChatReply, FakeOpenRouterChatClient
from pydocs_eval.judge.reference_journal import ReferenceJournal
from pydocs_eval.judge.reference_writer import ReferenceGapKind, WriterClients

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "repoqa_mini.json"
_OPUS = "anthropic/claude-opus-5.5"


async def _collect(tasks: AsyncIterator[EvalTask]) -> tuple[EvalTask, ...]:
    return tuple([task async for task in tasks])


@pytest.fixture
async def tasks() -> tuple[EvalTask, ...]:
    dataset = RepoQaQuestionDataset(source=RepoQADataset(fixture_path=_FIXTURE))
    return (await _collect(dataset.tasks()))[:2]


def _good(task: EvalTask) -> dict[str, object]:
    path, symbol = task.gold.file_set[0], task.gold.extra["symbol"]
    return {"answer": f"`{path}` — `{symbol}` (lines 3–6)\n\n`{symbol}` computes it."}


def _clients(
    scripted: dict[str, FakeChatReply | list[FakeChatReply]],
) -> tuple[FakeOpenRouterChatClient, WriterClients]:
    opus = FakeOpenRouterChatClient(scripted=scripted, served_model=_OPUS)
    sonnet = FakeOpenRouterChatClient(scripted={}, served_model="anthropic/claude-sonnet-5")
    return opus, WriterClients(primary=opus, fallback=sonnet)


def _plan(tasks: tuple[EvalTask, ...], tmp_path: Path) -> ReferenceWriterPlan:
    return plan_reference_writing(
        tasks,
        out=tmp_path / "refs.jsonl",
        journal=ReferenceJournal(tmp_path / "refs.journal.jsonl"),
        context_lines=1,
    )


def _run(plan: ReferenceWriterPlan, clients: WriterClients) -> ReferenceRunReport:
    return run_reference_writing(plan, clients, retries=2, extensions=GOLD_FILE_EXTENSIONS)


async def test_the_plan_names_what_the_run_would_write_and_calls_nothing(
    tasks: tuple[EvalTask, ...], tmp_path: Path
) -> None:
    stored = ReferenceRow(tasks[0].task_id, ReferenceAnswer("x", "m", "h"))
    write_reference_rows(tmp_path / "refs.jsonl", [stored])

    plan = _plan(tasks, tmp_path)

    assert plan.stored == frozenset({tasks[0].task_id})
    assert [source.task_id for source in plan.sources] == [tasks[1].task_id]
    assert (plan.to_collect, plan.to_delete) == ((), ())
    assert plan.prompt_chars > 0


async def test_a_run_stores_every_row_then_settles_and_deletes_its_batch(
    tasks: tuple[EvalTask, ...], tmp_path: Path
) -> None:
    opus, clients = _clients({task.task_id: _good(task) for task in tasks})
    plan = _plan(tasks, tmp_path)

    report = _run(plan, clients)

    rows = read_reference_rows(tmp_path / "refs.jsonl")
    assert [row.task_id for row in rows] == sorted(task.task_id for task in tasks)
    assert all(row.reference.model_id == _OPUS for row in rows)
    assert (report.deleted, report.not_deleted) == (("fake_batch_1",), ())
    assert opus.deleted == ["fake_batch_1"]
    journal = plan.journal
    assert (journal.unsettled(), journal.undeleted()) == ((), ())


async def test_a_batch_still_running_is_listed_then_collected_by_the_next_run(
    tasks: tuple[EvalTask, ...], tmp_path: Path
) -> None:
    first, second = tasks
    scripted: dict[str, FakeChatReply | list[FakeChatReply]] = {
        first.task_id: [ChatFailureKind.STILL_RUNNING, _good(first)],
        second.task_id: _good(second),
    }
    opus, clients = _clients(scripted)

    report = _run(_plan(tasks, tmp_path), clients)

    (gap,) = report.result.gaps
    assert (gap.task_id, gap.kind, gap.batch_id) == (
        first.task_id,
        ReferenceGapKind.STILL_RUNNING,
        "fake_batch_1",
    )
    assert opus.deleted == [], "a batch with a row still running is never deleted"
    assert [row.task_id for row in read_reference_rows(tmp_path / "refs.jsonl")] == [second.task_id]

    again = _plan(tasks, tmp_path)
    assert [batch.batch_id for batch in again.to_collect] == ["fake_batch_1"]
    report = _run(again, clients)

    assert report.result.gaps == ()
    assert len(opus.batches) == 1, "the running batch was collected, not bought again"
    stored = read_reference_rows(tmp_path / "refs.jsonl")
    assert sorted(row.task_id for row in stored) == sorted(task.task_id for task in tasks)
    assert opus.deleted == ["fake_batch_1"]


class _RefusingDeletes(FakeOpenRouterChatClient):
    """A client whose every delete is refused, as OpenRouter refuses a running batch."""

    def delete_batch(self, batch_id: str) -> None:
        raise JudgeRequestError("HTTP 409: still processing", status_code=409)


async def test_a_batch_that_could_not_be_deleted_is_retried_by_the_next_run(
    tasks: tuple[EvalTask, ...], tmp_path: Path
) -> None:
    refusing = _RefusingDeletes(scripted={t.task_id: _good(t) for t in tasks}, served_model=_OPUS)
    clients = WriterClients(primary=refusing, fallback=refusing)

    report = _run(_plan(tasks, tmp_path), clients)

    assert report.deleted == ()
    assert [(batch_id, "409" in why) for batch_id, why in report.not_deleted] == [
        ("fake_batch_1", True)
    ]
    again = _plan(tasks, tmp_path)
    assert again.to_delete == ("fake_batch_1",)
    assert again.sources == (), "every row is stored, so nothing is asked again"

    opus, clients = _clients({})
    report = _run(again, clients)

    assert (opus.deleted, opus.batches) == (["fake_batch_1"], [])
