"""Whether the escalation judge is trusted on a question (spec 9d, Q53, Round 5f).

The escalation judge labels the alignment set once per pass (its agreement
pass, under ``escalation_judge`` in the label file). On each dataset and
question its agreement with the labels that stand is compared with a floor: the
labellers' own κ on that question where it is below ``agreement_floor`` (0.90),
the ceiling no judge is asked to beat, else ``agreement_floor``. Below
``judge.alignment.min_labeller_kappa`` the question is ill-posed — its criteria
are rewritten before any judge is trusted on it — so no floor can be met there.
A question below its floor reads ``undecided`` for every in-band answer.

Which of the judge's figures meets the floor is ``judge.escalation.floor_scale``:
its percent agreement (as the spec states it, the default) or its own κ (one κ
scale on both sides — the open owner word). :func:`meets_floor` is that one
comparison. The judge's κ is reported beside the labellers' κ and flagged when
above it, never rewarded; its agreement with each labeller alone is reported
too, so a family it shares with one of them shows as a gap.

Example:
    >>> escalation_floor(0.90, 0.75, min_labeller_kappa=0.40)
    0.75
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.judge.alignment_adjudication import effective_label
from pydocs_eval.judge.alignment_report import is_low_kappa, labeller_agreement
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, QuestionKey
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelVerdict
from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.jev_questions import JevQuestionKind
from pydocs_eval.judge.jev_requests import JudgedDataset
from pydocs_eval.judge.role_config import EscalationFloorScale
from pydocs_eval.metrics.agreement import cohens_kappa, percent_agreement

_Pairs = list[tuple[LabelVerdict, LabelVerdict]]


class _ComparedWith(StrEnum):
    """What the judge's verdict on an item is paired with: the standing label, or a labeller's own."""

    LABEL = "label"
    ASTRA = LabellerSlot.ASTRA.value
    OPUS = LabellerSlot.OPUS.value


@dataclass(frozen=True, slots=True)
class EscalationTrustRow:
    """The escalation judge on one question: its figures, the labellers' κ, the floor, the verdict.

    ``items`` counts the alignment labels that stand on the question and that the
    judge also answered; ``agreement_with_astra`` / ``agreement_with_opus`` read
    each labeller's own verdicts, standing or not.
    """

    dataset: JudgedDataset
    question: JevQuestionKind
    items: int
    percent_agreement: float | None
    kappa: float | None
    labeller_kappa: float | None
    labeller_kappa_low: bool
    floor: float | None
    agreement_with_astra: float | None
    agreement_with_opus: float | None
    trusted: bool

    @property
    def kappa_above_labellers(self) -> bool:
        """Flagged, never rewarded: a judge agreeing past the labellers' own agreement."""
        if self.kappa is None or self.labeller_kappa is None:
            return False
        return self.kappa > self.labeller_kappa


def escalation_floor(
    agreement_floor: float, labeller_kappa: float | None, *, min_labeller_kappa: float
) -> float | None:
    """``min(agreement_floor, labeller_kappa)``; ``agreement_floor`` without a κ; ``None`` when ill-posed.

    Example:
        >>> escalation_floor(0.90, 0.95, min_labeller_kappa=0.40)
        0.9
    """
    if labeller_kappa is None:
        return agreement_floor
    if is_low_kappa(labeller_kappa, min_labeller_kappa):
        return None
    return min(agreement_floor, labeller_kappa)


def meets_floor(
    *,
    percent_agreement: float | None,
    kappa: float | None,
    floor: float | None,
    scale: EscalationFloorScale,
) -> bool:
    """Whether the judge's figure on ``scale`` reaches ``floor`` — the one swappable comparison.

    Example:
        >>> meets_floor(percent_agreement=0.92, kappa=0.4, floor=0.9, scale=EscalationFloorScale.PERCENT_AGREEMENT)
        True
    """
    figure = percent_agreement if scale is EscalationFloorScale.PERCENT_AGREEMENT else kappa
    return floor is not None and figure is not None and figure >= floor


def escalation_trust(
    rows: Iterable[AlignmentLabelRow], judge: JudgeConfig
) -> tuple[EscalationTrustRow, ...]:
    """The escalation judge's trust row on every question the alignment set asks."""
    every = list(rows)
    labellers = {QuestionKey(r.dataset, r.question): r.kappa for r in labeller_agreement(every)}
    pairs = _judge_pairs(every)
    keys = sorted(set(labellers) | set(pairs[_ComparedWith.LABEL]))
    return tuple(_trust_row(key, pairs, labellers.get(key), judge) for key in keys)


def _judge_pairs(
    rows: Iterable[AlignmentLabelRow],
) -> dict[_ComparedWith, dict[QuestionKey, _Pairs]]:
    """The judge's verdicts paired with the standing label, and with each labeller's own."""
    pairs: dict[_ComparedWith, dict[QuestionKey, _Pairs]] = defaultdict(lambda: defaultdict(list))
    for against, key, pair in (each for row in rows for each in _row_judge_pairs(row)):
        pairs[against][key].append(pair)
    return pairs


def _row_judge_pairs(
    row: AlignmentLabelRow,
) -> Iterator[tuple[_ComparedWith, QuestionKey, tuple[LabelVerdict, LabelVerdict]]]:
    """Each of the judge's answers on ``row`` beside the standing label and each labeller's."""
    judge = row.escalation_judge
    if judge is None or not judge.answered:
        return
    for question_id, answer in judge.labels.items():
        key = row.question_key(question_id)
        standing = effective_label(row, question_id)
        if standing is not LabelVerdict.UNDECIDED:
            yield _ComparedWith.LABEL, key, (answer.verdict, standing)
        yield from _per_labeller(row, question_id, key, answer.verdict)


def _per_labeller(
    row: AlignmentLabelRow, question_id: str, key: QuestionKey, verdict: LabelVerdict
) -> Iterator[tuple[_ComparedWith, QuestionKey, tuple[LabelVerdict, LabelVerdict]]]:
    for slot in LabellerSlot:
        own = row.labeller(slot).labels.get(question_id)
        if row.labeller(slot).answered and own is not None:
            yield _ComparedWith(slot.value), key, (verdict, own.verdict)


def _trust_row(
    key: QuestionKey,
    pairs: dict[_ComparedWith, dict[QuestionKey, _Pairs]],
    labeller_kappa: float | None,
    judge: JudgeConfig,
) -> EscalationTrustRow:
    against_label = pairs[_ComparedWith.LABEL].get(key, [])
    agreement, kappa = percent_agreement(against_label), cohens_kappa(against_label)
    min_kappa, escalation = judge.alignment.min_labeller_kappa, judge.escalation
    floor = escalation_floor(
        escalation.agreement_floor, labeller_kappa, min_labeller_kappa=min_kappa
    )
    return EscalationTrustRow(
        dataset=key.dataset,
        question=key.question,
        items=len(against_label),
        percent_agreement=agreement,
        kappa=kappa,
        labeller_kappa=labeller_kappa,
        labeller_kappa_low=is_low_kappa(labeller_kappa, min_kappa),
        floor=floor,
        agreement_with_astra=percent_agreement(pairs[_ComparedWith.ASTRA].get(key, [])),
        agreement_with_opus=percent_agreement(pairs[_ComparedWith.OPUS].get(key, [])),
        trusted=meets_floor(
            percent_agreement=agreement, kappa=kappa, floor=floor, scale=escalation.floor_scale
        ),
    )


__all__ = (
    "EscalationTrustRow",
    "escalation_floor",
    "escalation_trust",
    "meets_floor",
)
