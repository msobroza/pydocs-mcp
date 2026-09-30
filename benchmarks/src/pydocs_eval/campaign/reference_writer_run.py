"""One ``write-references`` run: plan it for free, then collect, write, store and delete (judge 9c).

The plan reads the stored rows and the batch journal and builds what the writer
would be shown for every task still without a reference; it calls nothing.

A run then settles what earlier runs left open before it buys anything new:
it deletes each batch an earlier run stored but could not delete, and collects
each batch an earlier run left running, by id — a slow batch is never
re-submitted, so it is never paid for twice. It then writes the remaining
tasks, stores the rows, records each ended batch as settled and deletes it
upstream (OpenRouter otherwise keeps a batch's inputs and results for 30 days).

Example:
    >>> plan = plan_reference_writing(tasks, out=out, journal=journal, context_lines=5)  # doctest: +SKIP
    >>> run_reference_writing(plan, clients, retries=2, extensions=(".py",)).result.gaps  # doctest: +SKIP
    ()
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.reference_answers import read_reference_rows, write_reference_rows
from pydocs_eval.judge.judge_errors import JudgeRequestError, JudgeUnavailableError
from pydocs_eval.judge.reference_journal import ReferenceJournal
from pydocs_eval.judge.reference_prompt import reference_prompt
from pydocs_eval.judge.reference_sources import (
    ReferenceSource,
    corpus_file_reader,
    repoqa_reference_source,
)
from pydocs_eval.judge.reference_writer import (
    CollectedBatch,
    ReferenceWriteResult,
    SubmittedBatch,
    WriterClients,
    write_reference_answers,
)

# OpenRouter's answer for a batch id it no longer holds.
_NOT_FOUND = 404


@dataclass(frozen=True, slots=True)
class ReferenceWriterPlan:
    """What a run would do: the tasks, those already stored, and the journal's open batches.

    ``sources`` are the tasks still without a reference, as the writer would see
    them; ``prompt_chars`` is the size of their first prompts.
    """

    tasks: tuple[EvalTask, ...]
    stored: frozenset[str]
    sources: tuple[ReferenceSource, ...]
    prompt_chars: int
    to_collect: tuple[SubmittedBatch, ...]
    to_delete: tuple[str, ...]
    out: Path
    journal: ReferenceJournal


@dataclass(frozen=True, slots=True)
class BatchDeletionFailure:
    """A batch that could not be deleted upstream, and why; the next run tries again."""

    batch_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class ReferenceRunReport:
    """What a run did: the writer's result, the batches it deleted or could not, those lost."""

    result: ReferenceWriteResult
    deleted: tuple[str, ...]
    not_deleted: tuple[BatchDeletionFailure, ...]
    lost: tuple[str, ...] = ()


def plan_reference_writing(
    tasks: Sequence[EvalTask], *, out: Path, journal: ReferenceJournal, context_lines: int
) -> ReferenceWriterPlan:
    """The run over ``tasks`` storing into ``out``; builds every source, calls nothing.

    Raises:
        ValueError: a task that is not a repoqa-qa task (no single gold path, symbol or span).
    """
    stored = frozenset(row.task_id for row in read_reference_rows(out))
    sources = tuple(
        _repoqa_source(task, context_lines) for task in tasks if task.task_id not in stored
    )
    return ReferenceWriterPlan(
        tasks=tuple(tasks),
        stored=stored,
        sources=sources,
        prompt_chars=sum(_first_prompt_chars(source) for source in sources),
        to_collect=journal.unsettled(),
        to_delete=journal.undeleted(),
        out=out,
        journal=journal,
    )


def run_reference_writing(
    plan: ReferenceWriterPlan,
    clients: WriterClients,
    *,
    retries: int,
    extensions: Sequence[str],
) -> ReferenceRunReport:
    """Carry ``plan`` out: this is the call that spends.

    Example:
        >>> report = run_reference_writing(plan, clients, retries=2, extensions=(".py",))  # doctest: +SKIP
        >>> report.result.gaps, report.not_deleted  # doctest: +SKIP
        ((), ())
    """
    earlier_deleted, earlier_failed = _delete_batches(plan.to_delete, clients, plan.journal)
    collected, lost = _collect_open_batches(plan, clients)
    result = write_reference_answers(
        plan.sources,
        clients,
        retries=retries,
        extensions=extensions,
        on_submitted=plan.journal.record_submitted,
        collected=collected,
        already_written=plan.stored,
    )
    write_reference_rows(plan.out, result.rows)
    plan.journal.record_settled(result.ended_batches)
    deleted, failed = _delete_batches(result.ended_batches, clients, plan.journal)
    return ReferenceRunReport(result, earlier_deleted + deleted, earlier_failed + failed, lost)


def _repoqa_source(task: EvalTask, context_lines: int) -> ReferenceSource:
    with corpus_file_reader(task) as read_file:
        return repoqa_reference_source(task, read_file, context_lines=context_lines)


def _first_prompt_chars(source: ReferenceSource) -> int:
    return sum(len(message.content) for message in reference_prompt(source).request.messages)


def _collect_open_batches(
    plan: ReferenceWriterPlan, clients: WriterClients
) -> tuple[list[CollectedBatch], tuple[str, ...]]:
    """Every batch an earlier run left open, read by id — never submitted again."""
    collected: list[CollectedBatch] = []
    lost: list[str] = []
    for batch in plan.to_collect:
        outcome = _collect_one(batch, clients, plan.journal)
        if outcome is None:
            lost.append(batch.batch_id)
        else:
            collected.append(outcome)
    return collected, tuple(lost)


def _collect_one(
    batch: SubmittedBatch, clients: WriterClients, journal: ReferenceJournal
) -> CollectedBatch | None:
    """``batch`` read by id, or ``None`` once it is recorded lost.

    A batch OpenRouter answers 404 for is gone (its 30-day retention ran out):
    its tasks are asked again. Any other refusal raises, the batch kept in the
    journal for the next run.
    """
    task_ids = [row.task_id for row in batch.rows]
    try:
        return CollectedBatch(batch, clients.of(batch.role).collect(batch.batch_id, task_ids))
    except JudgeRequestError as exc:
        if exc.status_code != _NOT_FOUND:
            raise
    journal.record_lost(batch.batch_id)
    return None


def _delete_batches(
    batch_ids: Sequence[str], clients: WriterClients, journal: ReferenceJournal
) -> tuple[tuple[str, ...], tuple[BatchDeletionFailure, ...]]:
    """Delete each batch upstream; one that cannot be stays in the journal for the next run."""
    deleted: list[str] = []
    failed: list[BatchDeletionFailure] = []
    for batch_id in batch_ids:
        try:
            clients.primary.delete_batch(batch_id)
        except (JudgeRequestError, JudgeUnavailableError) as exc:
            failed.append(BatchDeletionFailure(batch_id, str(exc)))
            continue
        journal.record_deleted(batch_id)
        deleted.append(batch_id)
    return tuple(deleted), tuple(failed)


__all__ = (
    "BatchDeletionFailure",
    "ReferenceRunReport",
    "ReferenceWriterPlan",
    "plan_reference_writing",
    "run_reference_writing",
)
