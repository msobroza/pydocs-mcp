"""The threshold sweep: how far Jev can be trusted alone, read against the alignment labels.

For a yes/no question, each review band from ``judge.alignment.band_start``
(0.3/0.7) outward is scored on the items whose alignment label stands: the
share Jev decides alone (its probability outside the band) and how often that
verdict agrees with the label. A wider band decides less and agrees more; the
owner picks the block committed under ``judge.thresholds``. For ``completeness``
each confidence gate is scored the same way, the level cut at the midpoints
(``thresholds.completeness_level``) and compared as a level — never averaged.

Example:
    >>> [row.threshold.low for row in sweep_noul_bands([(0.9, True)], band_start=(0.3, 0.7))][:3]
    [0.3, 0.25, 0.2]
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass

from pydocs_eval.judge.alignment_adjudication import effective_label
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow, QuestionKey
from pydocs_eval.judge.alignment_vocabulary import LEVEL_VERDICTS, NOUL_VERDICTS, LabelVerdict
from pydocs_eval.judge.jev_wire import JevAnswer, NoulAnswer, ScoreAnswer
from pydocs_eval.judge.thresholds import (
    CompletenessLevel,
    JevVerdict,
    NoulBand,
    ScoreGate,
    completeness_level,
    noul_verdict,
)

# How far each step widens the band, on both edges.
_BAND_STEP = 0.05
# The confidence gates a Score is swept over: 0.0, 0.1, ..., 0.9.
_SCORE_GATES = tuple(round(tenth / 10, 1) for tenth in range(10))
# Band edges are rounded so 0.3 - 0.05 reads 0.25, not 0.24999999999999997.
_EDGE_DIGITS = 6


@dataclass(frozen=True, slots=True)
class SweepRow:
    """One threshold (a review band, or a confidence gate on a Score) scored on the labelled answers.

    Of ``items`` answers whose label stands, Jev decides ``auto_decided`` alone
    under ``threshold``, and ``agreeing`` of those agree with the label.
    """

    threshold: NoulBand | ScoreGate
    items: int
    auto_decided: int
    agreeing: int

    @property
    def auto_decided_share(self) -> float:
        return self.auto_decided / self.items if self.items else 0.0

    @property
    def agreement(self) -> float | None:
        return self.agreeing / self.auto_decided if self.auto_decided else None


@dataclass(frozen=True, slots=True)
class SweepPairs:
    """Jev's answers beside the labels that stand, per dataset and question."""

    nouls: Mapping[QuestionKey, Sequence[tuple[float, bool]]]
    scores: Mapping[QuestionKey, Sequence[tuple[ScoreAnswer, CompletenessLevel]]]


def sweep_noul_bands(
    pairs: Sequence[tuple[float, bool]], *, band_start: tuple[float, float]
) -> tuple[SweepRow, ...]:
    """Each band from ``band_start`` outward to 0/1, scored on ``(probability, label)`` pairs."""
    return tuple(_band_row(band, pairs) for band in _bands_outward(band_start))


def sweep_score_gates(
    pairs: Sequence[tuple[ScoreAnswer, CompletenessLevel]],
) -> tuple[SweepRow, ...]:
    """Each confidence gate from 0.0 to 0.9, scored on ``(Jev's answer, label level)`` pairs."""
    return tuple(_gate_row(ScoreGate(min_confidence=gate), pairs) for gate in _SCORE_GATES)


def first_band_reaching(rows: Sequence[SweepRow], agreement: float) -> NoulBand | None:
    """The first band, from the start outward, whose agreement reaches ``agreement``: it decides the most.

    Example:
        >>> first_band_reaching([], 0.9) is None
        True
    """
    for row in rows:
        reached = row.agreement is not None and row.agreement >= agreement
        if reached and isinstance(row.threshold, NoulBand):
            return row.threshold
    return None


def sweep_pairs(
    rows: Iterable[AlignmentLabelRow], answers: Mapping[str, Mapping[str, JevAnswer]]
) -> SweepPairs:
    """Jev's answer (``answers[item_id][question_id]``) beside each label that stands, per question."""
    nouls: dict[QuestionKey, list[tuple[float, bool]]] = defaultdict(list)
    scores: dict[QuestionKey, list[tuple[ScoreAnswer, CompletenessLevel]]] = defaultdict(list)
    for key, answer, label in _answers_beside_labels(rows, answers):
        if isinstance(answer, NoulAnswer) and label in NOUL_VERDICTS:
            nouls[key].append((answer.probability, label is LabelVerdict.TRUE))
        if isinstance(answer, ScoreAnswer) and label in LEVEL_VERDICTS:
            scores[key].append((answer, CompletenessLevel(label.value)))
    return SweepPairs(dict(nouls), dict(scores))


def _answers_beside_labels(
    rows: Iterable[AlignmentLabelRow], answers: Mapping[str, Mapping[str, JevAnswer]]
) -> Iterator[tuple[QuestionKey, JevAnswer, LabelVerdict]]:
    """Each of Jev's answers on an item, with its question's key and the item's label there."""
    for row in rows:
        for question_id, answer in answers.get(row.item_id, {}).items():
            yield row.question_key(question_id), answer, effective_label(row, question_id)


def _bands_outward(start: tuple[float, float]) -> list[NoulBand]:
    low, high = start
    bands: list[NoulBand] = []
    step = 0
    while not bands or (bands[-1].low > 0.0 or bands[-1].high < 1.0):
        widened_low = round(max(0.0, low - step * _BAND_STEP), _EDGE_DIGITS)
        widened_high = round(min(1.0, high + step * _BAND_STEP), _EDGE_DIGITS)
        bands.append(NoulBand(low=widened_low, high=widened_high))
        step += 1
    return bands


def _band_row(band: NoulBand, pairs: Sequence[tuple[float, bool]]) -> SweepRow:
    verdicts = [(noul_verdict(probability, band), label) for probability, label in pairs]
    decided = [
        (verdict is JevVerdict.TRUE, label)
        for verdict, label in verdicts
        if verdict is not JevVerdict.IN_BAND
    ]
    agreeing = sum(1 for said, label in decided if said == label)
    return SweepRow(band, len(pairs), len(decided), agreeing)


def _gate_row(gate: ScoreGate, pairs: Sequence[tuple[ScoreAnswer, CompletenessLevel]]) -> SweepRow:
    levels = [(completeness_level(answer, gate), label) for answer, label in pairs]
    decided = [
        (level, label) for level, label in levels if level is not CompletenessLevel.UNDEFINED
    ]
    agreeing = sum(1 for level, label in decided if level is label)
    return SweepRow(gate, len(pairs), len(decided), agreeing)


__all__ = (
    "SweepPairs",
    "SweepRow",
    "first_band_reaching",
    "sweep_noul_bands",
    "sweep_pairs",
    "sweep_score_gates",
)
