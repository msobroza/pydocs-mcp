"""One ``label-alignment`` run: draw items from stored arms, then label, adjudicate and report.

The plan reads the arms' stored answers (``arm.json``) against their split's
tasks, keeps every answer the judges can read — a recorded, non-empty answer
within ``judge.jev.max_answer_chars`` to a task of the split — and draws the
items the label file does not hold yet, the ``reserved`` questions never
among them. Each drawn item's context is its task's ground truth: the gold, the
needle's code with ``reference_writer.context_lines`` around it (or each chat
site's span), and the task's reference answer. Planning calls nothing; an
optional Jev first pass (cached by input hash) fills the borderline and
code-vs-Jev strata.

A run then writes the drawn rows before any spend, has both labellers answer
them (``alignment_labelling``), adjudicates (``alignment_adjudication``),
rewrites the label file and deletes each batch whose rows are stored.

Example:
    >>> plan = plan_alignment_labelling(tasks, arms, request=request, judge=judge)  # doctest: +SKIP
    >>> run_alignment_labelling(plan, clients, judge=judge, writer_model=writer).cost  # doctest: +SKIP
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from pydocs_eval.campaign.before_after_answers import needle_sites_of
from pydocs_eval.campaign.before_after_arm import ArmSummary
from pydocs_eval.datasets.base_dataset import EvalTask, reference_answer_of
from pydocs_eval.datasets.example_needle_chat import gold_sites_of
from pydocs_eval.judge.alignment_adjudication import adjudicate, self_preferring_slot
from pydocs_eval.judge.alignment_label_file import read_alignment_labels, write_alignment_labels
from pydocs_eval.judge.alignment_labelling import (
    LabellerClients,
    LabellingCost,
    LabellingResult,
    label_alignment_rows,
    labelling_cost,
)
from pydocs_eval.judge.alignment_pool import (
    AlignmentCandidate,
    AlignmentPool,
    DrawnItem,
    alignment_row,
    code_check_of,
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
from pydocs_eval.judge.alignment_request import alignment_plan, labeller_request
from pydocs_eval.judge.alignment_rows import AlignmentItemContext, AlignmentLabelRow, AnswerSource
from pydocs_eval.judge.alignment_sizing import AlignmentSizing, size_alignment_passes
from pydocs_eval.judge.alignment_vocabulary import AlignmentPass, LabellerSlot, LabelStatus
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.jev_scoring import calibration_answers
from pydocs_eval.judge.jev_wire import JevJudge, NoulAnswer
from pydocs_eval.judge.judge_errors import JudgeRequestError, JudgeUnavailableError
from pydocs_eval.judge.planted_injections import load_planted_injections
from pydocs_eval.judge.reference_sources import (
    ShownCode,
    chat_reference_source,
    corpus_file_reader,
    repoqa_reference_source,
)
from pydocs_eval.judge.roles import jev_model

# Why a stored answer is not a candidate, as the plan counts it.
_NOT_IN_SPLIT = "task not in the split"
_NO_ANSWER = "no stored answer"
_OVER_CAP = "answer over judge.jev.max_answer_chars"
# What a commit is shortened to in an item's source.
_COMMIT_CHARS = 8


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
    skipped: Mapping[str, int]
    drawn: tuple[DrawnItem, ...]
    new_rows: tuple[AlignmentLabelRow, ...]
    jev_first_pass: bool
    prompt_chars: int

    @property
    def rows(self) -> tuple[AlignmentLabelRow, ...]:
        """The label file's rows once the drawn items join it."""
        return (*self.existing, *self.new_rows)


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
    not_deleted: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    @property
    def complete(self) -> bool:
        """Every item answered by both labellers, and every ended batch deleted."""
        both = all(row.astra.answered and row.opus.answered for row in self.rows)
        return both and not self.not_deleted


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
    candidates, skipped = _candidates(arms, by_id, request.dataset, judge)
    if jev_judge is not None:
        candidates = [_with_jev_probabilities(c, by_id, jev_judge, judge) for c in candidates]
    pool = AlignmentPool.of(candidates, reserved=request.reserved)
    existing = read_alignment_labels(request.labels_path)
    drawn = select_alignment_items(
        pool,
        size=max(0, request.target_items - len(existing)),
        config=judge.alignment,
        injections=load_planted_injections(),
        seed=request.seed,
        existing=existing,
    )
    new_rows = tuple(_row_of(item, by_id, request, judge) for item in drawn)
    return AlignmentLabelPlan(
        request=request,
        existing=existing,
        pool=pool,
        skipped=dict(skipped),
        drawn=drawn,
        new_rows=new_rows,
        jev_first_pass=jev_judge is not None,
        prompt_chars=_prompt_chars((*existing, *new_rows), judge),
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

    def persist(rows: Sequence[AlignmentLabelRow]) -> None:
        write_alignment_labels(path, rows)

    persist(plan.rows)
    result = label_alignment_rows(
        plan.rows,
        clients,
        jev=judge.jev,
        persist=persist,
        resubmit_not_submitted=resubmit_not_submitted,
    )
    labellers = tuple(block.model for block in judge.alignment.labellers)
    rows = adjudicate(
        result.rows,
        config=judge.alignment,
        self_preferring=self_preferring_slot(labellers, writer_model),
    )
    persist(rows)
    deleted, not_deleted = _delete_ended(result.ended_batches, clients)
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


def judged_sites_of(task: EvalTask, dataset: JudgedDataset) -> tuple[JudgedSite, ...]:
    """``task``'s gold as the judges state it: chat sites with their spans, else path and symbol."""
    if dataset is JudgedDataset.EXAMPLE_NEEDLE_CHAT:
        return tuple(JudgedSite(site.path, site.symbol, site.span) for site in gold_sites_of(task))
    return tuple(JudgedSite(site.path, site.symbol) for site in needle_sites_of(task))


def item_context(
    task: EvalTask, dataset: JudgedDataset, answer: str, *, context_lines: int
) -> AlignmentItemContext:
    """Everything a judge of ``answer`` is shown, read from ``task``'s own corpus."""
    return replace(
        _light_context(task, dataset, answer),
        shown_code=_shown_code(task, dataset, context_lines),
    )


def _light_context(task: EvalTask, dataset: JudgedDataset, answer: str) -> AlignmentItemContext:
    """The context without the code at the gold: all a Jev request reads."""
    reference = reference_answer_of(task.gold)
    return AlignmentItemContext(
        dataset=dataset,
        question=task.query,
        gold=judged_sites_of(task, dataset),
        shown_code=(),
        reference_answer=reference.text if reference is not None else None,
        agent_answer=answer,
    )


def _shown_code(task: EvalTask, dataset: JudgedDataset, context_lines: int) -> tuple[ShownCode, ...]:
    if dataset is JudgedDataset.SWE_QA_QUESTIONS:
        return ()
    with corpus_file_reader(task) as read_file:
        if dataset is JudgedDataset.REPOQA_QA:
            return repoqa_reference_source(task, read_file, context_lines=context_lines).code
        return chat_reference_source(task, read_file).code


def _candidates(
    arms: Sequence[ArmSummary],
    tasks: Mapping[str, EvalTask],
    dataset: JudgedDataset,
    judge: JudgeConfig,
) -> tuple[list[AlignmentCandidate], Counter[str]]:
    """Every stored answer the judges can read, and why each other one was skipped."""
    candidates: list[AlignmentCandidate] = []
    skipped: Counter[str] = Counter()
    for arm in arms:
        source = f"{arm.role}@{arm.commit[:_COMMIT_CHARS]}"
        for record in arm.tasks:
            task = tasks.get(record.task_id)
            reason = _skip_reason(task, record.answer, record.outcome.is_recorded, judge)
            if reason or task is None:
                skipped[reason] += 1
                continue
            gold = judged_sites_of(task, dataset)
            candidates.append(
                AlignmentCandidate(
                    dataset=dataset,
                    task_id=task.task_id,
                    source=AnswerSource(arm=source, trajectory_id=record.trajectory_id),
                    answer=record.answer,
                    code_check=code_check_of(
                        dataset, gold, record.answer, extensions=judge.jev.citation_extensions
                    ),
                    jev_probabilities={},
                )
            )
    return candidates, skipped


def _skip_reason(task: EvalTask | None, answer: str, recorded: bool, judge: JudgeConfig) -> str:
    if task is None:
        return _NOT_IN_SPLIT
    if not recorded or not answer.strip():
        return _NO_ANSWER
    if len(answer) > judge.jev.max_answer_chars:
        return _OVER_CAP
    return ""


def _with_jev_probabilities(
    candidate: AlignmentCandidate,
    tasks: Mapping[str, EvalTask],
    jev_judge: JevJudge,
    judge: JudgeConfig,
) -> AlignmentCandidate:
    """``candidate`` with Jev's first-pass yes-probabilities on every question it would be asked."""
    context = _light_context(tasks[candidate.task_id], candidate.dataset, candidate.answer)
    plan = alignment_plan(context, jev=judge.jev)
    answers = calibration_answers(plan, judge=jev_judge, config=judge)
    probabilities = {qid: a.probability for qid, a in answers.items() if isinstance(a, NoulAnswer)}
    return replace(candidate, jev_probabilities=probabilities)


def _row_of(
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
    labellers = tuple(block.model for block in judge.alignment.labellers)
    return alignment_row(
        item,
        context=context,
        jev_model=jev_model(judge),
        labeller_models=(labellers[0], labellers[1]),
        jev=judge.jev,
    )


def _prompt_chars(rows: Sequence[AlignmentLabelRow], judge: JudgeConfig) -> int:
    """The size of every request still to send, both labellers counted."""
    total = 0
    for row in rows:
        size = sum(len(m.content) for m in labeller_request(row, jev=judge.jev).messages)
        total += size * sum(1 for slot in LabellerSlot if not _asked_or_answered(row, slot))
    return total


def _asked_or_answered(row: AlignmentLabelRow, slot: LabellerSlot) -> bool:
    return row.labeller(slot).status in (LabelStatus.ANSWERED, LabelStatus.SUBMITTED)


def _delete_ended(
    ended: Sequence[tuple[str, str]], clients: LabellerClients
) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    """Delete each ended batch upstream; one that cannot be is listed with why."""
    deleted: list[str] = []
    failed: list[tuple[str, str]] = []
    for slot, batch_id in ended:
        try:
            clients.of(LabellerSlot(slot)).delete_batch(batch_id)
        except (JudgeRequestError, JudgeUnavailableError) as exc:
            failed.append((batch_id, str(exc)))
            continue
        deleted.append(batch_id)
    return tuple(deleted), tuple(failed)


__all__ = (
    "AlignmentLabelPlan",
    "AlignmentLabelReport",
    "AlignmentLabelRequest",
    "item_context",
    "judged_sites_of",
    "plan_alignment_labelling",
    "run_alignment_labelling",
)
