"""Whether the escalation judge is trusted on a question (spec 9d, Q53, Round 5f).

The escalation judge labels the alignment set once per pass (its agreement
pass, under ``escalation_judge`` in the label file). On each dataset and
question its agreement with the labels that stand is compared with a floor: the
labellers' own κ on that question where it is below ``agreement_floor`` (0.90),
the ceiling no judge is asked to beat, else ``agreement_floor``. Labellers at or
below chance (κ ≤ 0) settle nothing, so no floor can be met there. A question
below its floor reads ``undecided`` for every in-band answer.

Which of the judge's figures meets the floor is ``judge.escalation.floor_scale``:
its percent agreement (as the spec states it, the default) or its own κ (one κ
scale on both sides — the open owner word). :func:`meets_floor` is that one
comparison. The judge's κ is reported beside the labellers' κ and flagged when
above it, never rewarded; its agreement with each labeller alone is reported
too, so a family it shares with one of them shows as a gap.

Example:
    >>> escalation_floor(0.90, 0.75), escalation_floor(0.90, None)
    (0.75, 0.9)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from pydocs_eval.judge.alignment_adjudication import effective_label
from pydocs_eval.judge.alignment_report import QuestionKey, labeller_agreement
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LabellerSlot, LabelVerdict
from pydocs_eval.judge.jev_questions import kind_of
from pydocs_eval.judge.role_config import EscalationConfig, EscalationFloorScale
from pydocs_eval.metrics.agreement import cohens_kappa, percent_agreement

_Pairs = list[tuple[LabelVerdict, LabelVerdict]]


@dataclass(frozen=True, slots=True)
class EscalationTrustRow:
    """The escalation judge on one question: its figures, the labellers' κ, the floor, the verdict.

    ``items`` counts the alignment labels that stand on the question and that the
    judge also answered; ``agreement_with_astra`` / ``agreement_with_opus`` read
    each labeller's own verdicts, standing or not.
    """

    dataset: str
    question: str
    items: int
    percent_agreement: float | None
    kappa: float | None
    labeller_kappa: float | None
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


def escalation_floor(agreement_floor: float, labeller_kappa: float | None) -> float | None:
    """``min(agreement_floor, labeller_kappa)``; ``agreement_floor`` without a κ; ``None`` at chance.

    Example:
        >>> escalation_floor(0.90, 0.95)
        0.9
    """
    if labeller_kappa is None:
        return agreement_floor
    if labeller_kappa <= 0.0:
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
    rows: Iterable[AlignmentLabelRow], config: EscalationConfig
) -> tuple[EscalationTrustRow, ...]:
    """The escalation judge's trust row on every question the alignment set asks."""
    every = list(rows)
    labellers = {(each.dataset, each.question): each.kappa for each in labeller_agreement(every)}
    against_label, against_astra, against_opus = _judge_pairs(every)
    keys = sorted(set(labellers) | set(against_label))
    return tuple(
        _trust_row(
            key,
            against_label.get(key, []),
            labellers.get(key),
            (against_astra.get(key, []), against_opus.get(key, [])),
            config,
        )
        for key in keys
    )


def _judge_pairs(
    rows: Iterable[AlignmentLabelRow],
) -> tuple[dict[QuestionKey, _Pairs], dict[QuestionKey, _Pairs], dict[QuestionKey, _Pairs]]:
    """The judge's verdicts paired with the standing label, with Astra's and with Opus's."""
    label: dict[QuestionKey, _Pairs] = defaultdict(list)
    by_slot: dict[LabellerSlot, dict[QuestionKey, _Pairs]] = {
        slot: defaultdict(list) for slot in LabellerSlot
    }
    for row in rows:
        judge = row.escalation_judge
        if judge is None or not judge.answered:
            continue
        for question_id, answer in judge.labels.items():
            key = (row.dataset.value, kind_of(question_id).value)
            standing = effective_label(row, question_id)
            if standing is not LabelVerdict.UNDECIDED:
                label[key].append((answer.verdict, standing))
            for slot in LabellerSlot:
                own = row.labeller(slot).labels.get(question_id)
                if row.labeller(slot).answered and own is not None:
                    by_slot[slot][key].append((answer.verdict, own.verdict))
    return dict(label), dict(by_slot[LabellerSlot.ASTRA]), dict(by_slot[LabellerSlot.OPUS])


def _trust_row(
    key: QuestionKey,
    pairs: _Pairs,
    labeller_kappa: float | None,
    per_labeller: tuple[_Pairs, _Pairs],
    config: EscalationConfig,
) -> EscalationTrustRow:
    agreement, kappa = percent_agreement(pairs), cohens_kappa(pairs)
    floor = escalation_floor(config.agreement_floor, labeller_kappa)
    with_astra, with_opus = per_labeller
    return EscalationTrustRow(
        dataset=key[0],
        question=key[1],
        items=len(pairs),
        percent_agreement=agreement,
        kappa=kappa,
        labeller_kappa=labeller_kappa,
        floor=floor,
        agreement_with_astra=percent_agreement(with_astra),
        agreement_with_opus=percent_agreement(with_opus),
        trusted=meets_floor(
            percent_agreement=agreement, kappa=kappa, floor=floor, scale=config.floor_scale
        ),
    )


__all__ = (
    "EscalationTrustRow",
    "escalation_floor",
    "escalation_trust",
    "meets_floor",
)
