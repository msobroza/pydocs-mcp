"""The alignment set's closed vocabularies: verdicts, confidence, labeller slots, statuses, reasons.

Every value here is written into the alignment label file (``alignment_label_file``)
as its ``.value``, so renaming a member's value is a format change.

Example:
    >>> verdicts_for("completeness")[-1], verdicts_for("site_2")
    (<LabelVerdict.L3: 'L3'>, (<LabelVerdict.TRUE: 'true'>, <LabelVerdict.FALSE: 'false'>))
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.judge.jev_questions import SCORE_KINDS, JevQuestionKind, kind_of


class LabelConfidence(StrEnum):
    """How sure a labeller says it is of one verdict; ``LOW`` sends the item to the owner."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LabelVerdict(StrEnum):
    """A verdict on one question: yes/no for a Noul, a level for a Score, or ``undecided``.

    A labeller or the escalation judge answers with one of :data:`NOUL_VERDICTS`
    or :data:`LEVEL_VERDICTS`; ``UNDECIDED`` is what an item reads when no label
    stands (a disagreement, an escalation still with the owner, a judge not trusted).
    """

    TRUE = "true"
    FALSE = "false"
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    UNDECIDED = "undecided"


#: The verdicts a yes/no question is answered with.
NOUL_VERDICTS = (LabelVerdict.TRUE, LabelVerdict.FALSE)
#: The verdicts a levelled question is answered with, lowest first (``completeness`` L0–L3).
LEVEL_VERDICTS = (LabelVerdict.L0, LabelVerdict.L1, LabelVerdict.L2, LabelVerdict.L3)

#: The questions whose verdict compares the answer with the reference answer, which
#: Opus wrote: on them an Opus-only agreement never counts (spec 9d, self-preference).
REFERENCE_BASED_KINDS = frozenset(
    {JevQuestionKind.CONTRADICTS_REFERENCE, JevQuestionKind.COMPLETENESS}
)


# The yes/no verdict that speaks for the answer, per question: it names the gold,
# addresses no grader, contradicts no reference.
_FAVOURABLE_NOUL = {
    JevQuestionKind.NEEDLE_IDENTIFIED: LabelVerdict.TRUE,
    JevQuestionKind.SITE: LabelVerdict.TRUE,
    JevQuestionKind.GOLD_FILE: LabelVerdict.TRUE,
    JevQuestionKind.ADDRESSES_GRADER: LabelVerdict.FALSE,
    JevQuestionKind.CONTRADICTS_REFERENCE: LabelVerdict.FALSE,
}


def verdicts_for(question_id: str) -> tuple[LabelVerdict, ...]:
    """The verdicts question ``question_id`` may be answered with.

    Example:
        >>> [verdict.value for verdict in verdicts_for("needle_identified")]
        ['true', 'false']
    """
    return LEVEL_VERDICTS if kind_of(question_id) in SCORE_KINDS else NOUL_VERDICTS


def favour(question_id: str, verdict: LabelVerdict) -> int:
    """How strongly ``verdict`` speaks for the answer on ``question_id``: a level's rank, else 0 or 1.

    Which of two disagreeing judges "favoured the answer" is the one with the
    higher value — the direction the self-preference check reads.

    Example:
        >>> favour("contradicts_reference", LabelVerdict.FALSE), favour("completeness", LabelVerdict.L2)
        (1, 2)
    """
    if verdict in LEVEL_VERDICTS:
        return LEVEL_VERDICTS.index(verdict)
    return int(verdict is _FAVOURABLE_NOUL.get(kind_of(question_id)))


@dataclass(frozen=True, slots=True)
class QuestionLabel:
    """One labeller's answer to one question: its verdict, one line of evidence, its confidence."""

    verdict: LabelVerdict
    evidence: str
    confidence: LabelConfidence


class LabellerSlot(StrEnum):
    """The two blind labellers, by the label-file key their answers live under.

    ``ASTRA`` is ``judge.alignment.labellers[0]`` and ``OPUS`` is
    ``judge.alignment.labellers[1]``, the spec's names for the owner's two pins;
    each entry records the model that actually answered.
    """

    ASTRA = "astra"
    OPUS = "opus"


class LabelStatus(StrEnum):
    """Where one labeller's answer to one item stands."""

    #: Not asked yet: the next run asks it.
    PENDING = "pending"
    #: Asked in a batch whose id is recorded: collected by that id, never resubmitted.
    SUBMITTED = "submitted"
    ANSWERED = "answered"
    #: The job failed or its answer was unusable: the next run asks it again.
    FAILED = "failed"
    #: The batch submit failed and may have run anyway: asked again only on request.
    NOT_SUBMITTED = "not_submitted"


class AlignmentStratum(StrEnum):
    """Why an item was drawn into the alignment set (spec 9d's four strata)."""

    BORDERLINE = "borderline"
    CODE_VS_JEV = "code_vs_jev"
    PLANTED_INJECTION = "planted_injection"
    RANDOM = "random"


class EscalationReason(StrEnum):
    """Why an item goes to the owner: a disagreement, or one of Q59's conditions on an agreed item."""

    DISAGREEMENT = "disagreement"
    #: A disagreement on a reference-based question where the reference writer's own
    #: family was the labeller that favoured the answer: it never makes the label.
    SELF_PREFERENCE = "self_preference"
    #: Both labellers contradict the deterministic ``needle cited`` check.
    CODE_CHECK = "code_check"
    LOW_CONFIDENCE = "low_confidence"
    #: A planted injection neither labeller flagged: wrong by construction.
    PLANTED_INJECTION_PASSED = "planted_injection_passed"
    #: The random audit of agreed items.
    AUDIT = "audit"


class AlignmentPass(StrEnum):
    """Which labelling run an alignment item belongs to."""

    PILOT = "pilot"
    PASS_1 = "pass_1"
    PASS_2 = "pass_2"


__all__ = (
    "LEVEL_VERDICTS",
    "NOUL_VERDICTS",
    "REFERENCE_BASED_KINDS",
    "AlignmentPass",
    "AlignmentStratum",
    "EscalationReason",
    "LabelConfidence",
    "LabelStatus",
    "LabelVerdict",
    "LabellerSlot",
    "QuestionLabel",
    "favour",
    "verdicts_for",
)
