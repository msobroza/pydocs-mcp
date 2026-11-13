"""Adjudication: the labellers' agreement is the label, and what goes to the owner (Q58, Q59).

On every question both labellers answered, the same verdict is the agreed label
and different verdicts send the item to the owner with both rationales. An
agreed item still goes to the owner when both labellers contradict the code
check, either reports ``low`` confidence, it is a planted injection neither
flagged (wrong by construction), or it falls in the random audit of agreed
items. The audit draws each item on its own fixed coin (its id, hashed), so a
grown share only adds items and an audited item stays audited; the share grows
in proportion once more than ``audit_growth_threshold`` of the audited items the
owner ruled on prove wrong (at 10% wrong, a 5% threshold doubles it).

On the reference-based questions (``contradicts_reference``, ``completeness``)
the labeller of the reference writer's family is not independent of the
reference: a disagreement where it is the one favouring the answer is booked
``self_preference`` as well — an Opus-only agreement never makes the label.

An item the owner sees reads ``undecided`` on each question its reasons hold
(all of them for an item-wide reason) until ``owner_decision`` names it.

Example:
    >>> adjudicate([], config=AlignmentConfig(), self_preferring=LabellerSlot.OPUS)
    ()
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace

from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, OwnerEscalation
from pydocs_eval.judge.alignment_vocabulary import (
    REFERENCE_BASED_KINDS,
    EscalationReason,
    LabelConfidence,
    LabellerSlot,
    LabelVerdict,
    QuestionLabel,
    favour,
)
from pydocs_eval.judge.jev_questions import JevQuestionKind, kind_of
from pydocs_eval.judge.model_ids import model_family
from pydocs_eval.judge.role_config import AlignmentConfig

# The audit's coin for an item: its id hashed under this salt, read as a uniform
# draw in [0, 1). Fixed, so the draw never moves between runs.
_AUDIT_SALT = "alignment-audit"
_DRAW_BYTES = 8
_DRAW_SPAN = 1 << (8 * _DRAW_BYTES)
_ADDRESSES_GRADER = JevQuestionKind.ADDRESSES_GRADER.value


def self_preferring_slot(labeller_models: Sequence[str], writer_model: str) -> LabellerSlot | None:
    """The labeller of the reference writer's family, or ``None`` when neither is.

    Example:
        >>> self_preferring_slot(("openai/a:batch", "anthropic/b:batch"), "anthropic/w:batch")
        <LabellerSlot.OPUS: 'opus'>
    """
    for slot, model in zip(LabellerSlot, labeller_models, strict=True):
        if model and model_family(model) == model_family(writer_model):
            return slot
    return None


def adjudicate(
    rows: Iterable[AlignmentLabelRow],
    *,
    config: AlignmentConfig,
    self_preferring: LabellerSlot | None,
) -> tuple[AlignmentLabelRow, ...]:
    """Every row both labellers answered, settled: its agreed labels and why the owner sees it.

    Rows still waiting on a labeller are returned unchanged. The audit share is
    read from the rows themselves (:func:`audit_share`).
    """
    settled = [_settled(row, self_preferring) for row in rows]
    share = audit_share(settled, config)
    return tuple(_audited(row, share) for row in settled)


def effective_label(row: AlignmentLabelRow, question_id: str) -> LabelVerdict:
    """The label ``row`` reads on ``question_id``: the owner's, else an unheld agreement, else undecided.

    The audit holds nothing: an audited item is a random clean agreement, whose
    label stands unless the owner rules otherwise. Every other reason holds its
    question (or, with no question, the whole item) until the owner rules.

    Example:
        >>> effective_label(row, "needle_identified")  # doctest: +SKIP
        <LabelVerdict.TRUE: 'true'>
    """
    decided = row.owner_decision.get(question_id)
    if decided is not None:
        return decided
    if any(_holds(each, question_id) for each in row.escalation_reasons):
        return LabelVerdict.UNDECIDED
    return row.agreed.get(question_id, LabelVerdict.UNDECIDED)


def _holds(reason: OwnerEscalation, question_id: str) -> bool:
    """Whether ``reason`` withholds the agreed label of ``question_id`` until the owner rules."""
    return reason.reason is not EscalationReason.AUDIT and reason.question in ("", question_id)


def audit_share(rows: Iterable[AlignmentLabelRow], config: AlignmentConfig) -> float:
    """``agreed_audit_share``, grown in proportion once the audited-wrong rate passes the threshold.

    The rate is over the audited items the owner has ruled on: an item is wrong
    when a verdict of the owner's differs from its agreed label.

    Example:
        >>> audit_share([], AlignmentConfig())
        0.1
    """
    ruled = [row for row in rows if is_audited(row) and row.owner_decision]
    if not ruled:
        return config.agreed_audit_share
    wrong_rate = sum(1 for row in ruled if _overruled(row)) / len(ruled)
    if wrong_rate <= config.audit_growth_threshold:
        return config.agreed_audit_share
    return min(1.0, config.agreed_audit_share * wrong_rate / config.audit_growth_threshold)


def overruled_questions(row: AlignmentLabelRow) -> tuple[str, ...]:
    """The questions on which the owner ruled against ``row``'s agreed label."""
    return tuple(
        question_id
        for question_id, verdict in row.owner_decision.items()
        if row.agreed.get(question_id) not in (None, LabelVerdict.UNDECIDED, verdict)
    )


def _overruled(row: AlignmentLabelRow) -> bool:
    return bool(overruled_questions(row))


def is_audited(row: AlignmentLabelRow) -> bool:
    """Whether ``row`` was drawn into the random audit of agreed items."""
    return any(each.reason is EscalationReason.AUDIT for each in row.escalation_reasons)


def _settled(row: AlignmentLabelRow, self_preferring: LabellerSlot | None) -> AlignmentLabelRow:
    if not row.labelled_by_both:
        return row
    agreed: dict[str, LabelVerdict] = {}
    reasons: list[OwnerEscalation] = []
    for question_id in row.questions:
        verdict, question_reasons = _question_settled(row, question_id, self_preferring)
        agreed[question_id] = verdict
        reasons.extend(question_reasons)
    reasons.extend(_code_check_reasons(row, agreed))
    reasons.extend(_planted_reasons(row, agreed))
    kept_audits = [each for each in row.escalation_reasons if each.reason is EscalationReason.AUDIT]
    return replace(row, agreed=agreed, escalation_reasons=tuple(reasons + kept_audits))


def _question_settled(
    row: AlignmentLabelRow, question_id: str, self_preferring: LabellerSlot | None
) -> tuple[LabelVerdict, list[OwnerEscalation]]:
    """``question_id``'s agreed verdict and the reasons it alone sends to the owner."""
    answers = {slot: row.labeller(slot).labels.get(question_id) for slot in LabellerSlot}
    reasons = _low_confidence(question_id, answers.values())
    astra, opus = answers[LabellerSlot.ASTRA], answers[LabellerSlot.OPUS]
    if astra is not None and opus is not None and astra.verdict == opus.verdict:
        return astra.verdict, reasons
    reasons.insert(0, OwnerEscalation(EscalationReason.DISAGREEMENT, question_id))
    if _self_preference(question_id, answers, self_preferring):
        reasons.insert(1, OwnerEscalation(EscalationReason.SELF_PREFERENCE, question_id))
    return LabelVerdict.UNDECIDED, reasons


def _low_confidence(
    question_id: str, answers: Iterable[QuestionLabel | None]
) -> list[OwnerEscalation]:
    low = any(each is not None and each.confidence is LabelConfidence.LOW for each in answers)
    return [OwnerEscalation(EscalationReason.LOW_CONFIDENCE, question_id)] if low else []


def _self_preference(
    question_id: str,
    answers: Mapping[LabellerSlot, QuestionLabel | None],
    self_preferring: LabellerSlot | None,
) -> bool:
    """Whether the writer's-family labeller is the one favouring the answer on a reference question."""
    if self_preferring is None or kind_of(question_id) not in REFERENCE_BASED_KINDS:
        return False
    mine, theirs = answers[self_preferring], answers[self_preferring.other]
    if mine is None:
        return False
    return theirs is None or favour(question_id, mine.verdict) > favour(question_id, theirs.verdict)


def _code_check_reasons(
    row: AlignmentLabelRow, agreed: Mapping[str, LabelVerdict]
) -> list[OwnerEscalation]:
    """A question both labellers agree on against what the deterministic check says."""
    return [
        OwnerEscalation(EscalationReason.CODE_CHECK, question_id)
        for question_id in row.code_check.verdicts
        if row.code_check.contradicts(question_id, _says_yes(agreed.get(question_id)))
    ]


def _says_yes(verdict: LabelVerdict | None) -> bool | None:
    """A yes/no verdict as a flag; ``None`` for anything else (a level, undecided, absent)."""
    if verdict is LabelVerdict.TRUE:
        return True
    return False if verdict is LabelVerdict.FALSE else None


def _planted_reasons(
    row: AlignmentLabelRow, agreed: Mapping[str, LabelVerdict]
) -> list[OwnerEscalation]:
    """A planted injection both labellers passed: the whole item, since it may have steered them."""
    if row.planted_injection and agreed.get(_ADDRESSES_GRADER) is LabelVerdict.FALSE:
        return [OwnerEscalation(EscalationReason.PLANTED_INJECTION_PASSED)]
    return []


def _audited(row: AlignmentLabelRow, share: float) -> AlignmentLabelRow:
    """``row`` with the audit added when it is a clean agreement whose coin falls under ``share``."""
    if is_audited(row) or not _cleanly_agreed(row) or _audit_draw(row.item_id) >= share:
        return row
    audit = OwnerEscalation(EscalationReason.AUDIT)
    return replace(row, escalation_reasons=(*row.escalation_reasons, audit))


def _cleanly_agreed(row: AlignmentLabelRow) -> bool:
    """Both labellers agree on every question and nothing else sends the item to the owner."""
    agreed = [row.agreed.get(question_id) for question_id in row.questions]
    settled = bool(agreed) and LabelVerdict.UNDECIDED not in agreed and None not in agreed
    return settled and not row.escalation_reasons


def _audit_draw(item_id: str) -> float:
    digest = hashlib.sha256(f"{_AUDIT_SALT}:{item_id}".encode()).digest()
    return int.from_bytes(digest[:_DRAW_BYTES], "big") / _DRAW_SPAN


__all__ = (
    "adjudicate",
    "audit_share",
    "effective_label",
    "is_audited",
    "overruled_questions",
    "self_preferring_slot",
)
