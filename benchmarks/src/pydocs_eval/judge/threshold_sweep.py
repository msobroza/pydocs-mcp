"""The threshold sweep: how far Jev can be trusted alone, read against the alignment labels.

For a yes/no question, each review band from ``judge.alignment.band_start``
(0.3/0.7) outward is scored on the items whose alignment label stands: the
share Jev decides alone (its probability outside the band) and how often that
verdict agrees with the label. A wider band decides less and agrees more; the
owner picks the block committed under ``judge.thresholds``. For ``completeness``
each confidence gate is scored the same way, the level cut at the midpoints
(``thresholds.completeness_level``) and compared as a level — never averaged.

Example:
    >>> [row.band.low for row in sweep_noul_bands([(0.9, True)], band_start=(0.3, 0.7))][:3]
    [0.3, 0.25, 0.2]
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydocs_eval.judge.alignment_adjudication import effective_label
from pydocs_eval.judge.alignment_report import QuestionKey
from pydocs_eval.judge.alignment_rows import AlignmentLabelRow
from pydocs_eval.judge.alignment_vocabulary import LEVEL_VERDICTS, LabelVerdict
from pydocs_eval.judge.jev_questions import kind_of
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
class BandSweepRow:
    """One review band: of ``items`` labelled answers, Jev decides ``auto_decided`` and ``agreeing`` agree."""

    band: NoulBand
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
class GateSweepRow:
    """One confidence gate on a Score: the share it lets Jev decide, and how often its level is the label."""

    gate: ScoreGate
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

    nouls: dict[QuestionKey, list[tuple[float, bool]]] = field(default_factory=dict)
    scores: dict[QuestionKey, list[tuple[ScoreAnswer, CompletenessLevel]]] = field(
        default_factory=dict
    )


def sweep_noul_bands(
    pairs: Sequence[tuple[float, bool]], *, band_start: tuple[float, float]
) -> tuple[BandSweepRow, ...]:
    """Each band from ``band_start`` outward to 0/1, scored on ``(probability, label)`` pairs."""
    return tuple(_band_row(band, pairs) for band in _bands_outward(band_start))


def sweep_score_gates(
    pairs: Sequence[tuple[ScoreAnswer, CompletenessLevel]],
) -> tuple[GateSweepRow, ...]:
    """Each confidence gate from 0.0 to 0.9, scored on ``(Jev's answer, label level)`` pairs."""
    return tuple(_gate_row(ScoreGate(min_confidence=gate), pairs) for gate in _SCORE_GATES)


def first_band_reaching(rows: Sequence[BandSweepRow], agreement: float) -> NoulBand | None:
    """The first band, from the start outward, whose agreement reaches ``agreement``: it decides the most.

    Example:
        >>> first_band_reaching([], 0.9) is None
        True
    """
    for row in rows:
        if row.agreement is not None and row.agreement >= agreement:
            return row.band
    return None


def sweep_pairs(
    rows: Iterable[AlignmentLabelRow], answers: Mapping[str, Mapping[str, JevAnswer]]
) -> SweepPairs:
    """Jev's answer (``answers[item_id][question_id]``) beside each label that stands, per question."""
    pairs = SweepPairs()
    for row in rows:
        for question_id, answer in answers.get(row.item_id, {}).items():
            _add_pair(pairs, row, question_id, answer)
    return pairs


def _add_pair(
    pairs: SweepPairs, row: AlignmentLabelRow, question_id: str, answer: JevAnswer
) -> None:
    label = effective_label(row, question_id)
    key = (row.dataset.value, kind_of(question_id).value)
    if isinstance(answer, NoulAnswer) and label in (LabelVerdict.TRUE, LabelVerdict.FALSE):
        pairs.nouls.setdefault(key, []).append((answer.probability, label is LabelVerdict.TRUE))
    elif isinstance(answer, ScoreAnswer) and label in LEVEL_VERDICTS:
        pairs.scores.setdefault(key, []).append((answer, CompletenessLevel(label.value)))


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


def _band_row(band: NoulBand, pairs: Sequence[tuple[float, bool]]) -> BandSweepRow:
    verdicts = [(noul_verdict(probability, band), label) for probability, label in pairs]
    decided = [(verdict is JevVerdict.TRUE, label) for verdict, label in verdicts if verdict is not JevVerdict.IN_BAND]
    agreeing = sum(1 for said, label in decided if said == label)
    return BandSweepRow(band, len(pairs), len(decided), agreeing)


def _gate_row(gate: ScoreGate, pairs: Sequence[tuple[ScoreAnswer, CompletenessLevel]]) -> GateSweepRow:
    levels = [(completeness_level(answer, gate), label) for answer, label in pairs]
    decided = [(level, label) for level, label in levels if level is not CompletenessLevel.UNDEFINED]
    agreeing = sum(1 for level, label in decided if level is label)
    return GateSweepRow(gate, len(pairs), len(decided), agreeing)


__all__ = (
    "BandSweepRow",
    "GateSweepRow",
    "SweepPairs",
    "first_band_reaching",
    "sweep_noul_bands",
    "sweep_pairs",
    "sweep_score_gates",
)
