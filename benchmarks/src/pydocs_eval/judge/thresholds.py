"""Jev's thresholds: ``judge.thresholds.<jev-model>.<dataset>.<question>``, and what they decide.

A Noul question's block is its review band (``low``, ``high``): at or below
``low`` the answer is no, at or above ``high`` yes, and in between it is in the
band, where Jev's verdict does not stand. The ``completeness`` Score's block is
its confidence gate (``min_confidence``): below it the level is ``undefined``,
at or above it the probability-weighted score is cut at the level midpoints —
a level, never an average. Blocks are fitted per Jev model version, dataset and
question, so a table without the block for a question cannot score it.

Example:
    >>> noul_verdict(0.9, NoulBand(low=0.3, high=0.7))
    <JevVerdict.TRUE: 'true'>
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pydocs_eval.judge.jev_questions import JevQuestionKind
from pydocs_eval.judge.jev_wire import ScoreAnswer

_TABLE_KEY = "judge.thresholds"

_Block = TypeVar("_Block")
_Shape = TypeVar("_Shape", "NoulBand", "ScoreGate")


class NoulBand(BaseModel):
    """A Noul question's review band: no at or below ``low``, yes at or above ``high``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    low: float = Field(ge=0.0, le=1.0)
    high: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.low > self.high:
            raise ValueError(f"review band low={self.low} > high={self.high}, expected low <= high")
        return self


class ScoreGate(BaseModel):
    """A Score question's gate: a level stands only at ``min_confidence`` or above."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    min_confidence: float = Field(ge=0.0, le=1.0)


#: ``<jev-model>`` → ``<dataset>`` → ``<question>`` → its block, as ``judge.yaml`` holds it.
ThresholdTable = dict[str, dict[str, dict[str, NoulBand | ScoreGate]]]


class MissingThresholdsError(Exception):
    """A question has no fitted block for this Jev model and dataset, so it cannot be scored."""


class JevVerdict(StrEnum):
    """One Noul question's verdict; only ``TRUE`` and ``FALSE`` are Jev's to give."""

    TRUE = "true"
    FALSE = "false"
    IN_BAND = "in_band"
    UNDEFINED = "undefined"


class CompletenessLevel(StrEnum):
    """The ``completeness`` level an answer reached, or ``undefined``."""

    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    UNDEFINED = "undefined"


_LEVELS = (CompletenessLevel.L0, CompletenessLevel.L1, CompletenessLevel.L2, CompletenessLevel.L3)
_MIDPOINTS = tuple(index + 0.5 for index in range(len(_LEVELS) - 1))


@dataclass(frozen=True, slots=True)
class DatasetThresholds:
    """The blocks one dataset's questions are scored with, under one Jev model."""

    bands: Mapping[JevQuestionKind, NoulBand]
    completeness: ScoreGate | None = None


def thresholds_for(
    table: Mapping[str, Mapping[str, Mapping[str, NoulBand | ScoreGate]]],
    *,
    jev_model: str,
    dataset: str,
    kinds: Iterable[JevQuestionKind],
) -> DatasetThresholds:
    """The block of every question kind in ``kinds``, for ``dataset`` under ``jev_model``.

    Raises:
        MissingThresholdsError: a block is absent or of the wrong shape, named by its key.
    """
    model_key = f"{_TABLE_KEY}.{jev_model}"
    blocks = _required(_required(table, model_key, jev_model), f"{model_key}.{dataset}", dataset)
    bands: dict[JevQuestionKind, NoulBand] = {}
    completeness: ScoreGate | None = None
    for kind in sorted(set(kinds)):
        key = f"{model_key}.{dataset}.{kind.value}"
        block = _required(blocks, key, kind.value)
        if kind is JevQuestionKind.COMPLETENESS:
            completeness = _shaped(block, ScoreGate, key)
        else:
            bands[kind] = _shaped(block, NoulBand, key)
    return DatasetThresholds(bands=bands, completeness=completeness)


def noul_verdict(probability: float, band: NoulBand) -> JevVerdict:
    """Jev's verdict on a Noul outside ``band``; ``IN_BAND`` inside it."""
    if probability >= band.high:
        return JevVerdict.TRUE
    if probability <= band.low:
        return JevVerdict.FALSE
    return JevVerdict.IN_BAND


def completeness_level(answer: ScoreAnswer, gate: ScoreGate) -> CompletenessLevel:
    """The level ``answer`` reached, cut at the midpoints; ``undefined`` below the gate.

    Example:
        >>> completeness_level(ScoreAnswer(1.6, 0.9, {}), ScoreGate(min_confidence=0.5))
        <CompletenessLevel.L2: 'L2'>
    """
    if answer.confidence < gate.min_confidence:
        return CompletenessLevel.UNDEFINED
    return _LEVELS[bisect_right(_MIDPOINTS, answer.score)]


def _required(blocks: Mapping[str, _Block], key: str, name: str) -> _Block:
    if name not in blocks:
        raise MissingThresholdsError(
            f"{key} is missing: scoring refuses it until an alignment pass fits it"
        )
    return blocks[name]


def _shaped(block: NoulBand | ScoreGate, shape: type[_Shape], key: str) -> _Shape:
    if not isinstance(block, shape):
        fields = ", ".join(shape.model_fields)
        raise MissingThresholdsError(f"{key} is {block!r}, expected a block of {fields}")
    return block


__all__ = (
    "CompletenessLevel",
    "DatasetThresholds",
    "JevVerdict",
    "MissingThresholdsError",
    "NoulBand",
    "ScoreGate",
    "ThresholdTable",
    "completeness_level",
    "noul_verdict",
    "thresholds_for",
)
