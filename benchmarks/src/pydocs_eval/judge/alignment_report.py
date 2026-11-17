"""What the alignment set says about itself: κ per question, which way disagreements go, the owner's queue.

Every figure is per dataset and per question, where a question is a kind:
``site_0`` and ``site_1`` pool into ``site``, as Jev's thresholds do. The
labellers' Cohen's κ on a question is the ceiling for every judge's agreement
target on it; below ``judge.alignment.min_labeller_kappa`` the question is
ill-posed, and its criteria are rewritten before any judge is trusted on it
(:func:`is_low_kappa`). The direction table counts, per question, the
disagreements in which each labeller was the one favouring the answer — on the
reference-based questions an excess on the reference writer's side is
self-preference.

Example:
    >>> labeller_agreement([])
    ()
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from pydocs_eval.judge.alignment_adjudication import is_audited, overruled_questions
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, QuestionKey
from pydocs_eval.judge.alignment_vocabulary import (
    EscalationReason,
    LabellerSlot,
    LabelVerdict,
    favour,
)
from pydocs_eval.judge.jev_questions import JevQuestionKind
from pydocs_eval.judge.jev_requests import JudgedDataset
from pydocs_eval.judge.role_config import AlignmentConfig
from pydocs_eval.metrics.agreement import cohens_kappa, percent_agreement

_Pair = tuple[LabelVerdict, LabelVerdict]


@dataclass(frozen=True, slots=True)
class QuestionAgreement:
    """Two raters on one question: how many items both rated, their raw agreement and κ."""

    dataset: JudgedDataset
    question: JevQuestionKind
    items: int
    percent_agreement: float | None
    kappa: float | None


@dataclass(frozen=True, slots=True)
class DisagreementDirection:
    """One question's disagreements, split by which labeller favoured the answer."""

    dataset: JudgedDataset
    question: JevQuestionKind
    disagreements: int
    astra_favours_answer: int
    opus_favours_answer: int


@dataclass(frozen=True, slots=True)
class OwnerQueue:
    """The items the owner sees: how many, why, and how many still wait on a ruling."""

    items: int
    by_reason: dict[EscalationReason, int]
    awaiting: int


@dataclass(frozen=True, slots=True)
class AuditReview:
    """One question's audit: agreed labels the owner ruled on, those wrong, and whether to review it."""

    dataset: JudgedDataset
    question: JevQuestionKind
    ruled: int
    wrong: int
    review_criteria: bool


def is_low_kappa(kappa: float | None, min_labeller_kappa: float) -> bool:
    """Whether the labellers' κ marks a question ill-posed; an undefined κ (one category) does not.

    Example:
        >>> is_low_kappa(0.35, 0.40), is_low_kappa(None, 0.40)
        (True, False)
    """
    return kappa is not None and kappa < min_labeller_kappa


def labeller_pairs(rows: Iterable[AlignmentLabelRow]) -> dict[QuestionKey, list[_Pair]]:
    """Every ``(astra, opus)`` verdict pair, grouped by dataset and question.

    Example:
        >>> labeller_pairs([])
        {}
    """
    pairs: dict[QuestionKey, list[_Pair]] = defaultdict(list)
    for key, pair in (each for row in rows for each in _row_pairs(row)):
        pairs[key].append(pair)
    return dict(pairs)


def labeller_agreement(rows: Iterable[AlignmentLabelRow]) -> tuple[QuestionAgreement, ...]:
    """The two labellers' raw agreement and Cohen's κ, per dataset and question."""
    return tuple(
        QuestionAgreement(
            key.dataset, key.question, len(pairs), percent_agreement(pairs), cohens_kappa(pairs)
        )
        for key, pairs in sorted(labeller_pairs(rows).items())
    )


def disagreement_directions(rows: Iterable[AlignmentLabelRow]) -> tuple[DisagreementDirection, ...]:
    """Per dataset and question, which labeller favoured the answer in each disagreement."""
    return tuple(
        _direction(key, Counter(_favouring(key.question, astra, opus) for astra, opus in pairs))
        for key, pairs in sorted(labeller_pairs(rows).items())
    )


def owner_queue(rows: Iterable[AlignmentLabelRow]) -> OwnerQueue:
    """The items with a reason to reach the owner, counted once per reason they carry."""
    routed = [row for row in rows if row.escalation_reasons]
    by_reason: Counter[EscalationReason] = Counter()
    for row in routed:
        by_reason.update({each.reason for each in row.escalation_reasons})
    awaiting = sum(1 for row in routed if _awaits_ruling(row))
    return OwnerQueue(len(routed), dict(by_reason), awaiting)


def audit_review(
    rows: Iterable[AlignmentLabelRow], config: AlignmentConfig
) -> tuple[AuditReview, ...]:
    """Per question, the audited labels the owner ruled on and how many proved wrong.

    A question past ``audit_growth_threshold`` wrong is flagged for its criteria
    to be reviewed.
    """
    ruled: Counter[QuestionKey] = Counter()
    wrong: Counter[QuestionKey] = Counter()
    for row in (row for row in rows if is_audited(row)):
        overruled = set(overruled_questions(row))
        ruled.update(row.question_key(qid) for qid in row.owner_decision)
        wrong.update(row.question_key(qid) for qid in row.owner_decision if qid in overruled)
    return tuple(
        AuditReview(
            key.dataset,
            key.question,
            count,
            wrong[key],
            wrong[key] / count > config.audit_growth_threshold,
        )
        for key, count in sorted(ruled.items())
    )


def _row_pairs(row: AlignmentLabelRow) -> Iterator[tuple[QuestionKey, _Pair]]:
    """``row``'s verdict pair on every question both labellers answered."""
    if not row.labelled_by_both:
        return
    for question_id in row.questions:
        astra, opus = row.astra.labels.get(question_id), row.opus.labels.get(question_id)
        if astra is not None and opus is not None:
            yield row.question_key(question_id), (astra.verdict, opus.verdict)


def _direction(key: QuestionKey, tally: Counter[LabellerSlot | None]) -> DisagreementDirection:
    astra, opus = tally[LabellerSlot.ASTRA], tally[LabellerSlot.OPUS]
    return DisagreementDirection(key.dataset, key.question, astra + opus, astra, opus)


def _favouring(
    question: JevQuestionKind, astra: LabelVerdict, opus: LabelVerdict
) -> LabellerSlot | None:
    """The labeller whose verdict speaks more for the answer; ``None`` when they agree."""
    if astra == opus:
        return None
    if favour(question.value, astra) > favour(question.value, opus):
        return LabellerSlot.ASTRA
    return LabellerSlot.OPUS


def _awaits_ruling(row: AlignmentLabelRow) -> bool:
    """Whether some question a reason holds has no owner verdict yet."""
    held = {
        question_id
        for each in row.escalation_reasons
        for question_id in ((each.question,) if each.question else row.questions)
    }
    return not held <= set(row.owner_decision)


__all__ = (
    "AuditReview",
    "DisagreementDirection",
    "OwnerQueue",
    "QuestionAgreement",
    "audit_review",
    "disagreement_directions",
    "is_low_kappa",
    "labeller_agreement",
    "labeller_pairs",
    "owner_queue",
)
