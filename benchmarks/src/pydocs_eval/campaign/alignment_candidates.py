"""The stored answers an alignment draw may take, read from arm summaries against their split's tasks.

A candidate is an answer the judges can read: a recorded, non-empty answer
within ``judge.jev.max_answer_chars`` to a task of the split; every other one is
counted under why it was skipped. A candidate's code check is computed here,
before any judge; an optional Jev first pass adds Jev's raw yes-probabilities,
which the borderline and code-vs-Jev strata read. An item's context — what
every judge of it is shown — is its task's ground truth: the gold, the code at
the gold (the needle's body with ``context_lines`` around it, or each chat
site's span) and the task's reference answer, read through ``reference_answer_of``.

Example:
    >>> draw = alignment_candidates(arms, tasks_by_id, JudgedDataset.REPOQA_QA, judge)  # doctest: +SKIP
    >>> dict(draw.skipped)  # doctest: +SKIP
    {<CandidateSkipReason.NO_STORED_ANSWER: 'no stored answer'>: 1}
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum

from pydocs_eval.campaign.before_after_answers import needle_sites_of
from pydocs_eval.campaign.before_after_arm import ArmSummary, ArmTaskRecord
from pydocs_eval.datasets.base_dataset import EvalTask, reference_answer_of
from pydocs_eval.datasets.example_needle_chat import gold_sites_of
from pydocs_eval.judge.alignment_pool import AlignmentCandidate, code_check_of
from pydocs_eval.judge.alignment_request import alignment_plan
from pydocs_eval.judge.alignment_rows import AlignmentItemContext, AnswerSource
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.jev_requests import JudgedDataset, JudgedSite
from pydocs_eval.judge.jev_scoring import calibration_answers
from pydocs_eval.judge.jev_wire import JevJudge, NoulAnswer
from pydocs_eval.judge.reference_sources import (
    ShownCode,
    chat_reference_source,
    corpus_file_reader,
    repoqa_reference_source,
)

# What a commit is shortened to in an item's source.
_COMMIT_CHARS = 8


class CandidateSkipReason(StrEnum):
    """Why a stored answer is not an alignment candidate."""

    NOT_IN_SPLIT = "task not in the split"
    NO_STORED_ANSWER = "no stored answer"
    OVER_ANSWER_CAP = "answer over judge.jev.max_answer_chars"


@dataclass(frozen=True, slots=True)
class CandidateDraw:
    """Every candidate the arms hold, and how many answers were skipped, by why."""

    candidates: tuple[AlignmentCandidate, ...]
    skipped: Mapping[CandidateSkipReason, int]


def alignment_candidates(
    arms: Sequence[ArmSummary],
    tasks: Mapping[str, EvalTask],
    dataset: JudgedDataset,
    judge: JudgeConfig,
) -> CandidateDraw:
    """Every stored answer in ``arms`` the judges can read, with its code check."""
    candidates: list[AlignmentCandidate] = []
    skipped: Counter[CandidateSkipReason] = Counter()
    for source, record in _stored_answers(arms):
        task = tasks.get(record.task_id)
        reason = _skip_reason(task, record, judge)
        if reason is not None or task is None:
            skipped[reason or CandidateSkipReason.NOT_IN_SPLIT] += 1
            continue
        candidates.append(_candidate(task, record, source, dataset, judge))
    return CandidateDraw(tuple(candidates), dict(skipped))


def judged_sites_of(task: EvalTask, dataset: JudgedDataset) -> tuple[JudgedSite, ...]:
    """``task``'s gold as the judges state it: chat sites with their spans, else path and symbol."""
    if dataset is JudgedDataset.EXAMPLE_NEEDLE_CHAT:
        return tuple(JudgedSite(site.path, site.symbol, site.span) for site in gold_sites_of(task))
    return tuple(JudgedSite(site.path, site.symbol) for site in needle_sites_of(task))


def item_context(
    task: EvalTask, dataset: JudgedDataset, answer: str, *, context_lines: int
) -> AlignmentItemContext:
    """Everything a judge of ``answer`` is shown, the code read from ``task``'s own corpus."""
    return replace(
        _context_without_code(task, dataset, answer),
        shown_code=_gold_code_of_task(task, dataset, context_lines),
    )


def with_jev_first_pass(
    candidates: Sequence[AlignmentCandidate],
    tasks: Mapping[str, EvalTask],
    jev_judge: JevJudge,
    judge: JudgeConfig,
) -> tuple[AlignmentCandidate, ...]:
    """Each candidate with Jev's raw yes-probability on every question it would be asked."""
    return tuple(_with_jev_probabilities(c, tasks, jev_judge, judge) for c in candidates)


def _stored_answers(arms: Sequence[ArmSummary]) -> Iterator[tuple[AnswerSource, ArmTaskRecord]]:
    for arm in arms:
        arm_name = f"{arm.role}@{arm.commit[:_COMMIT_CHARS]}"
        for record in arm.tasks:
            yield AnswerSource(arm=arm_name, trajectory_id=record.trajectory_id), record


def _skip_reason(
    task: EvalTask | None, record: ArmTaskRecord, judge: JudgeConfig
) -> CandidateSkipReason | None:
    if task is None:
        return CandidateSkipReason.NOT_IN_SPLIT
    if not record.outcome.is_recorded or not record.answer.strip():
        return CandidateSkipReason.NO_STORED_ANSWER
    if len(record.answer) > judge.jev.max_answer_chars:
        return CandidateSkipReason.OVER_ANSWER_CAP
    return None


def _candidate(
    task: EvalTask,
    record: ArmTaskRecord,
    source: AnswerSource,
    dataset: JudgedDataset,
    judge: JudgeConfig,
) -> AlignmentCandidate:
    gold = judged_sites_of(task, dataset)
    extensions = judge.jev.citation_extensions
    return AlignmentCandidate(
        dataset=dataset,
        task_id=task.task_id,
        source=source,
        answer=record.answer,
        code_check=code_check_of(dataset, gold, record.answer, extensions=extensions),
        jev_probabilities={},
    )


def _context_without_code(
    task: EvalTask, dataset: JudgedDataset, answer: str
) -> AlignmentItemContext:
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


def _gold_code_of_task(
    task: EvalTask, dataset: JudgedDataset, context_lines: int
) -> tuple[ShownCode, ...]:
    """The code at ``task``'s gold, from its materialized corpus; none where gold names files only."""
    if dataset is JudgedDataset.SWE_QA_QUESTIONS:
        return ()
    with corpus_file_reader(task) as read_file:
        if dataset is JudgedDataset.REPOQA_QA:
            return repoqa_reference_source(task, read_file, context_lines=context_lines).code
        return chat_reference_source(task, read_file).code


def _with_jev_probabilities(
    candidate: AlignmentCandidate,
    tasks: Mapping[str, EvalTask],
    jev_judge: JevJudge,
    judge: JudgeConfig,
) -> AlignmentCandidate:
    context = _context_without_code(tasks[candidate.task_id], candidate.dataset, candidate.answer)
    answers = calibration_answers(
        alignment_plan(context, jev=judge.jev), judge=jev_judge, config=judge
    )
    probabilities = {qid: a.probability for qid, a in answers.items() if isinstance(a, NoulAnswer)}
    return replace(candidate, jev_probabilities=probabilities)


__all__ = (
    "CandidateDraw",
    "CandidateSkipReason",
    "alignment_candidates",
    "item_context",
    "judged_sites_of",
    "with_jev_first_pass",
)
