"""What the alignment set says about itself: κ per question, which way disagreements go, the owner's queue.

Every figure is per dataset and per question, where a question is a kind:
``site_0`` and ``site_1`` pool into ``site``, as Jev's thresholds do. The
labellers' Cohen's κ on a question is the ceiling for every judge's agreement
target on it; a low κ marks a question whose criteria are rewritten before any
judge is trusted on it. The direction table counts, per question, the
disagreements in which each labeller was the one favouring the answer — on the
reference-based questions an excess on the reference writer's side is
self-preference.

Example:
    >>> labeller_agreement([])
    ()
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from pydocs_eval.judge.alignment_adjudication import overruled_questions
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import (
    EscalationReason,
    LabellerSlot,
    LabelVerdict,
    favour,
)
from pydocs_eval.judge.jev_questions import kind_of
from pydocs_eval.judge.role_config import AlignmentConfig
from pydocs_eval.metrics.agreement import cohens_kappa, percent_agreement

#: One question of one dataset: ``("repoqa-qa", "needle_identified")``.
QuestionKey = tuple[str, str]
_Pair = tuple[LabelVerdict, LabelVerdict]


@dataclass(frozen=True, slots=True)
class QuestionAgreement:
    """Two raters on one question: how many items both rated, their raw agreement and κ."""

    dataset: str
    question: str
    items: int
    percent_agreement: float | None
    kappa: float | None


@dataclass(frozen=True, slots=True)
class DisagreementDirection:
    """One question's disagreements, split by which labeller favoured the answer."""

    dataset: str
    question: str
    disagreements: int
    astra_favours_answer: int
    opus_favours_answer: int


@dataclass(frozen=True, slots=True)
class OwnerQueue:
    """The items the owner sees: how many, why, and how many still wait on a ruling."""

    items: int
    by_reason: Mapping[EscalationReason, int]
    awaiting: int


@dataclass(frozen=True, slots=True)
class AuditReview:
    """One question's audit: agreed labels the owner ruled on, those wrong, and whether to review it."""

    dataset: str
    question: str
    ruled: int
    wrong: int
    review_criteria: bool


def labeller_pairs(rows: Iterable[AlignmentLabelRow]) -> dict[QuestionKey, list[_Pair]]:
    """Every ``(astra, opus)`` verdict pair, grouped by dataset and question.

    Example:
        >>> labeller_pairs([])
        {}
    """
    pairs: dict[QuestionKey, list[_Pair]] = defaultdict(list)
    for row in rows:
        if not (row.astra.answered and row.opus.answered):
            continue
        for question_id in row.questions:
            astra, opus = row.astra.labels.get(question_id), row.opus.labels.get(question_id)
            if astra is not None and opus is not None:
                pairs[_key(row, question_id)].append((astra.verdict, opus.verdict))
    return dict(pairs)


def labeller_agreement(rows: Iterable[AlignmentLabelRow]) -> tuple[QuestionAgreement, ...]:
    """The two labellers' raw agreement and Cohen's κ, per dataset and question."""
    return tuple(
        QuestionAgreement(
            dataset, question, len(pairs), percent_agreement(pairs), cohens_kappa(pairs)
        )
        for (dataset, question), pairs in sorted(labeller_pairs(rows).items())
    )


def disagreement_directions(rows: Iterable[AlignmentLabelRow]) -> tuple[DisagreementDirection, ...]:
    """Per dataset and question, which labeller favoured the answer in each disagreement."""
    counts: dict[QuestionKey, Counter[LabellerSlot | None]] = defaultdict(Counter)
    for key, pairs in labeller_pairs(rows).items():
        counts[key].update(_favouring(key[1], astra, opus) for astra, opus in pairs)
    return tuple(
        DisagreementDirection(
            dataset,
            question,
            disagreements=tally[LabellerSlot.ASTRA] + tally[LabellerSlot.OPUS],
            astra_favours_answer=tally[LabellerSlot.ASTRA],
            opus_favours_answer=tally[LabellerSlot.OPUS],
        )
        for (dataset, question), tally in sorted(counts.items())
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
    for row in rows:
        if not any(each.reason is EscalationReason.AUDIT for each in row.escalation_reasons):
            continue
        overruled = set(overruled_questions(row))
        for question_id in row.owner_decision:
            ruled[_key(row, question_id)] += 1
            wrong[_key(row, question_id)] += question_id in overruled
    return tuple(
        AuditReview(
            dataset,
            question,
            count,
            wrong[(dataset, question)],
            wrong[(dataset, question)] / count > config.audit_growth_threshold,
        )
        for (dataset, question), count in sorted(ruled.items())
    )


def _key(row: AlignmentLabelRow, question_id: str) -> QuestionKey:
    return row.dataset.value, kind_of(question_id).value


def _favouring(question: str, astra: LabelVerdict, opus: LabelVerdict) -> LabellerSlot | None:
    """The labeller whose verdict speaks more for the answer; ``None`` when they agree."""
    if astra == opus:
        return None
    return (
        LabellerSlot.ASTRA
        if favour(question, astra) > favour(question, opus)
        else LabellerSlot.OPUS
    )


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
    "QuestionKey",
    "audit_review",
    "disagreement_directions",
    "labeller_agreement",
    "labeller_pairs",
    "owner_queue",
)
