"""One ``label-alignment`` run: draw items from stored arms, then label, adjudicate and report.

The plan reads the arms' stored answers (``alignment_candidates``) and draws the
items the pass still needs — ``target_items`` of the pass, less those of the
pass the label file already holds — never an item already there and never a
``reserved`` question. Each drawn item's context is its task's ground truth.
Planning calls nothing; an optional Jev first pass (cached by input hash) fills
the borderline and code-vs-Jev strata.

A run then writes the drawn rows before any spend, has both labellers answer
them (``alignment_labelling``), adjudicates (``alignment_adjudication``),
rewrites the label file and deletes each batch whose rows are stored.

Example:
    >>> plan = plan_alignment_labelling(tasks, arms, request=request, judge=judge)  # doctest: +SKIP
    >>> run_alignment_labelling(plan, clients, judge=judge, writer_model=writer).cost  # doctest: +SKIP
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydocs_eval.campaign.alignment_candidates import (
    CandidateSkipReason,
    alignment_candidates,
    item_context,
    with_jev_first_pass,
)
from pydocs_eval.campaign.before_after_arm import ArmSummary
from pydocs_eval.campaign.reference_writer_run import BatchDeletionFailure
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.judge.alignment_adjudication import adjudicate, self_preferring_slot
from pydocs_eval.judge.alignment_label_file import read_alignment_labels, write_alignment_labels
from pydocs_eval.judge.alignment_labelling import (
    EndedBatch,
    LabellerClients,
    LabellingCost,
    LabellingResult,
    label_alignment_rows,
    labelling_cost,
)
from pydocs_eval.judge.alignment_pool import (
    AlignmentPool,
    DrawnItem,
    alignment_row,
    select_alignment_items,
)
from pydocs_eval.judge.alignment_report import (
    DisagreementDirection,
    OwnerQueue,
    QuestionAgreement,
    disagreement_directions,
    labeller_agreement,
    owner_queue,
)
from pydocs_eval.judge.alignment_request import labeller_request
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_sizing import AlignmentSizing, size_alignment_passes
from pydocs_eval.judge.alignment_vocabulary import AlignmentPass, LabellerSlot, LabelStatus
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.jev_requests import JudgedDataset
from pydocs_eval.judge.jev_wire import JevJudge
from pydocs_eval.judge.judge_errors import JudgeRequestError, JudgeUnavailableError
from pydocs_eval.judge.planted_injections import load_planted_injections

# A labeller entry whose request is already paid for or in flight: not to be sent again.
_SENT = frozenset({LabelStatus.ANSWERED, LabelStatus.SUBMITTED})


@dataclass(frozen=True, slots=True)
class AlignmentLabelRequest:
    """What one run is asked for: which split and pass, how many items, where, under which seed."""

    split: str
    dataset: JudgedDataset
    alignment_pass: AlignmentPass
    target_items: int
    labels_path: Path
    seed: int
    reserved: frozenset[str]
    context_lines: int


@dataclass(frozen=True, slots=True)
class AlignmentLabelPlan:
    """What a run would do: the label file's rows so far, the pool, and the items it draws."""

    request: AlignmentLabelRequest
    existing: tuple[AlignmentLabelRow, ...]
    pool: AlignmentPool
    skipped: Mapping[CandidateSkipReason, int]
    drawn: tuple[DrawnItem, ...]
    new_rows: tuple[AlignmentLabelRow, ...]
    jev_first_pass: bool
    prompt_chars: int

    @property
    def rows(self) -> tuple[AlignmentLabelRow, ...]:
        """The label file's rows once the drawn items join it."""
        return (*self.existing, *self.new_rows)

    @property
    def pass_items_held(self) -> int:
        """How many items of this run's pass the label file already holds."""
        return _items_of_pass(self.existing, self.request.alignment_pass)


@dataclass(frozen=True, slots=True)
class AlignmentLabelReport:
    """What a run did, and what the labels say so far."""

    rows: tuple[AlignmentLabelRow, ...]
    labelling: LabellingResult
    cost: LabellingCost
    agreement: tuple[QuestionAgreement, ...]
    directions: tuple[DisagreementDirection, ...]
    queue: OwnerQueue
    sizing: AlignmentSizing | None
    deleted: tuple[str, ...] = ()
    not_deleted: tuple[BatchDeletionFailure, ...] = ()

    @property
    def complete(self) -> bool:
        """Every item answered by both labellers, and every ended batch deleted."""
        return all(row.labelled_by_both for row in self.rows) and not self.not_deleted


def plan_alignment_labelling(
    tasks: Sequence[EvalTask],
    arms: Sequence[ArmSummary],
    *,
    request: AlignmentLabelRequest,
    judge: JudgeConfig,
    jev_judge: JevJudge | None = None,
) -> AlignmentLabelPlan:
    """The run over ``arms``' answers to ``tasks``; calls nothing unless ``jev_judge`` is given.

    Raises:
        AlignmentLabelFileError: the label file holds a line outside its format.
        ValueError: a drawn task lacks the gold its dataset needs.
    """
    by_id = {task.task_id: task for task in tasks}
    draw = alignment_candidates(arms, by_id, request.dataset, judge)
    candidates = draw.candidates
    if jev_judge is not None:
        candidates = with_jev_first_pass(candidates, by_id, jev_judge, judge)
    pool = AlignmentPool.of(candidates, reserved=request.reserved)
    existing = read_alignment_labels(request.labels_path)
    drawn = _draw(pool, existing, request, judge)
    new_rows = tuple(_label_row_of_drawn_item(item, by_id, request, judge) for item in drawn)
    return AlignmentLabelPlan(
        request=request,
        existing=existing,
        pool=pool,
        skipped=draw.skipped,
        drawn=drawn,
        new_rows=new_rows,
        jev_first_pass=jev_judge is not None,
        prompt_chars=_unsent_prompt_chars((*existing, *new_rows), judge),
    )


def run_alignment_labelling(
    plan: AlignmentLabelPlan,
    clients: LabellerClients,
    *,
    judge: JudgeConfig,
    writer_model: str,
    resubmit_not_submitted: bool = False,
) -> AlignmentLabelReport:
    """Carry ``plan`` out: this is the call that spends (or, with offline clients, rehearses)."""
    path = plan.request.labels_path
    write_alignment_labels(path, plan.rows)
    result = label_alignment_rows(
        plan.rows,
        clients,
        jev=judge.jev,
        persist=lambda rows: write_alignment_labels(path, rows),
        resubmit_not_submitted=resubmit_not_submitted,
    )
    labellers = tuple(block.model for block in judge.alignment.labellers)
    slot = self_preferring_slot(labellers, writer_model)
    rows = adjudicate(result.rows, config=judge.alignment, self_preferring=slot)
    write_alignment_labels(path, rows)
    deleted, not_deleted = _delete_ended(result.ended_batches, clients)
    return _report(plan, rows, result, judge, deleted, not_deleted)


def _draw(
    pool: AlignmentPool,
    existing: Sequence[AlignmentLabelRow],
    request: AlignmentLabelRequest,
    judge: JudgeConfig,
) -> tuple[DrawnItem, ...]:
    """The items the pass still needs, never one the label file already holds."""
    held = _items_of_pass(existing, request.alignment_pass)
    return select_alignment_items(
        pool,
        size=max(0, request.target_items - held),
        config=judge.alignment,
        injections=load_planted_injections(),
        seed=request.seed,
        existing=existing,
    )


def _items_of_pass(rows: Sequence[AlignmentLabelRow], alignment_pass: AlignmentPass) -> int:
    return sum(1 for row in rows if row.alignment_pass is alignment_pass)


def _report(
    plan: AlignmentLabelPlan,
    rows: tuple[AlignmentLabelRow, ...],
    result: LabellingResult,
    judge: JudgeConfig,
    deleted: tuple[str, ...],
    not_deleted: tuple[BatchDeletionFailure, ...],
) -> AlignmentLabelReport:
    cost = labelling_cost(rows)
    pilot = plan.request.alignment_pass is AlignmentPass.PILOT
    return AlignmentLabelReport(
        rows=rows,
        labelling=result,
        cost=cost,
        agreement=labeller_agreement(rows),
        directions=disagreement_directions(rows),
        queue=owner_queue(rows),
        sizing=size_alignment_passes(cost, judge.alignment) if pilot else None,
        deleted=deleted,
        not_deleted=not_deleted,
    )


def _label_row_of_drawn_item(
    item: DrawnItem,
    tasks: Mapping[str, EvalTask],
    request: AlignmentLabelRequest,
    judge: JudgeConfig,
) -> AlignmentLabelRow:
    candidate = item.candidate
    context = item_context(
        tasks[candidate.task_id],
        candidate.dataset,
        candidate.answer,
        context_lines=request.context_lines,
    )
    return alignment_row(item, context=context, judge=judge, alignment_pass=request.alignment_pass)


def _unsent_prompt_chars(rows: Sequence[AlignmentLabelRow], judge: JudgeConfig) -> int:
    """The size of every request still to send, both labellers counted."""
    total = 0
    for row in rows:
        unsent = sum(1 for slot in LabellerSlot if row.labeller(slot).status not in _SENT)
        if unsent:
            request = labeller_request(row, jev=judge.jev)
            total += unsent * sum(len(message.content) for message in request.messages)
    return total


def _delete_ended(
    ended: Sequence[EndedBatch], clients: LabellerClients
) -> tuple[tuple[str, ...], tuple[BatchDeletionFailure, ...]]:
    """Delete each ended batch upstream; one that cannot be is listed with why."""
    deleted: list[str] = []
    failed: list[BatchDeletionFailure] = []
    for batch in ended:
        try:
            clients.of(batch.slot).delete_batch(batch.batch_id)
        except (JudgeRequestError, JudgeUnavailableError) as exc:
            failed.append(BatchDeletionFailure(batch.batch_id, str(exc)))
            continue
        deleted.append(batch.batch_id)
    return tuple(deleted), tuple(failed)


__all__ = (
    "AlignmentLabelPlan",
    "AlignmentLabelReport",
    "AlignmentLabelRequest",
    "plan_alignment_labelling",
    "run_alignment_labelling",
)
