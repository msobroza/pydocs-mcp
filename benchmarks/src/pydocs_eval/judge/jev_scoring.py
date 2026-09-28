"""``score``: one answer's Jev rows, from its request plan, the Jev judge and the thresholds.

The thresholds are looked up first, so a question without a fitted block
refuses before anything is paid for. Each row is then Jev's verdict outside the
question's review band, ``in_band`` inside it (the escalation judge's to
decide), and ``undefined`` wherever Jev was not asked or did not answer — an
answer over the cap, an outage, a question with no reference yet. A missing
answer is never booked as a no. ``contradicts_reference`` is reported as
``agreement``: true when the answer contradicts nothing in the reference.

Example:
    >>> score_answer(plan, judge=FakeJevJudgeClient(scripted={}), config=config).outage  # doctest: +SKIP
    True
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass

from pydocs_eval.judge.config import JudgeConfig
from pydocs_eval.judge.jev_questions import LOCATED_KINDS, SCORE_KINDS, JevQuestionKind, kind_of
from pydocs_eval.judge.jev_requests import JevRequestPlan
from pydocs_eval.judge.jev_wire import JevAnswer, JevJudge, NoulAnswer, ScoreAnswer
from pydocs_eval.judge.judge_errors import JudgeUnavailableError
from pydocs_eval.judge.roles import jev_model
from pydocs_eval.judge.thresholds import (
    CompletenessLevel,
    DatasetThresholds,
    JevVerdict,
    NoulBand,
    completeness_level,
    noul_verdict,
    thresholds_for,
)

log = logging.getLogger(__name__)

#: The row ``contradicts_reference`` is reported as (never "grounding").
AGREEMENT = "agreement"

_AGREEMENT_OF = {
    JevVerdict.TRUE: JevVerdict.FALSE,
    JevVerdict.FALSE: JevVerdict.TRUE,
    JevVerdict.IN_BAND: JevVerdict.IN_BAND,
    JevVerdict.UNDEFINED: JevVerdict.UNDEFINED,
}
_DECIDED = frozenset({JevVerdict.TRUE, JevVerdict.FALSE})


@dataclass(frozen=True, slots=True)
class JevScore:
    """One answer's Jev rows: every Noul verdict by row name, and its completeness level.

    ``outage`` says a request got no answer; ``request_parts`` records a split.
    """

    verdicts: Mapping[str, JevVerdict]
    completeness: CompletenessLevel
    answer_over_cap: bool
    outage: bool
    request_parts: int

    @property
    def gold_location_recall(self) -> float | None:
        """The share of gold sites (or files) Jev says the answer points to.

        The Jev twin of the code's ``gold_site_coverage``, computed from the
        thresholded verdicts. ``None`` without per-location questions, or while
        any of them is in the band or undefined.
        """
        located = [
            verdict
            for question_id, verdict in self.verdicts.items()
            if question_id != AGREEMENT and kind_of(question_id) in LOCATED_KINDS
        ]
        if not located or not set(located) <= _DECIDED:
            return None
        return located.count(JevVerdict.TRUE) / len(located)


def score_answer(plan: JevRequestPlan, *, judge: JevJudge, config: JudgeConfig) -> JevScore:
    """Score ``plan``'s answer with ``judge`` under ``config``'s thresholds for its dataset.

    Example:
        >>> score_answer(plan, judge=FakeJevJudgeClient(scripted={}), config=config).outage  # doctest: +SKIP
        True

    Raises:
        MissingThresholdsError: a question of the plan has no fitted block, before any call.
        JudgeConfigError: ``judge.jev.model`` is empty.
        ValueError: the plan carries the audit question, which never scores.
        JudgeModelMismatchError: the model that answered is not the pin.
    """
    kinds = {kind_of(question_id) for question_id in plan.question_ids}
    if JevQuestionKind.COMMITTED_FUNCTION in kinds:
        raise ValueError("committed_function is the alignment audit, never a scoring question")
    pinned = jev_model(config)
    thresholds = thresholds_for(
        config.thresholds, jev_model=pinned, dataset=plan.dataset.value, kinds=kinds
    )
    answers, outage = _ask(plan, judge)
    return JevScore(
        verdicts=_verdicts(plan, answers, thresholds),
        completeness=_completeness(answers, thresholds),
        answer_over_cap=plan.answer_over_cap,
        outage=outage,
        request_parts=len(plan.requests),
    )


def _ask(plan: JevRequestPlan, judge: JevJudge) -> tuple[dict[str, JevAnswer], bool]:
    """Every answer Jev gave across the plan's requests, and whether a request got none."""
    answers: dict[str, JevAnswer] = {}
    outage = False
    for part, request in enumerate(plan.requests):
        try:
            answers |= judge.judge(request).answers
        except JudgeUnavailableError as exc:
            outage = True
            _log_outage(plan, part, exc)
    return answers, outage


def _verdicts(
    plan: JevRequestPlan, answers: Mapping[str, JevAnswer], thresholds: DatasetThresholds
) -> dict[str, JevVerdict]:
    """A verdict per Noul the plan asks, with ``agreement`` always present."""
    verdicts = {AGREEMENT: JevVerdict.UNDEFINED}
    for question_id in plan.question_ids:
        kind = kind_of(question_id)
        if kind in SCORE_KINDS:
            continue
        verdict = _verdict_or_undefined(answers.get(question_id), thresholds.bands[kind])
        if kind is JevQuestionKind.CONTRADICTS_REFERENCE:
            verdicts[AGREEMENT] = _AGREEMENT_OF[verdict]
        else:
            verdicts[question_id] = verdict
    return verdicts


def _verdict_or_undefined(answer: JevAnswer | None, band: NoulBand) -> JevVerdict:
    """Jev's verdict on a Noul it answered; ``undefined`` when it gave none."""
    if not isinstance(answer, NoulAnswer):
        return JevVerdict.UNDEFINED
    return noul_verdict(answer.probability, band)


def _completeness(
    answers: Mapping[str, JevAnswer], thresholds: DatasetThresholds
) -> CompletenessLevel:
    answer = answers.get(JevQuestionKind.COMPLETENESS.value)
    gate = thresholds.gates.get(JevQuestionKind.COMPLETENESS)
    if not isinstance(answer, ScoreAnswer) or gate is None:
        return CompletenessLevel.UNDEFINED
    return completeness_level(answer, gate)


def _log_outage(plan: JevRequestPlan, part: int, exc: Exception) -> None:
    fields = {
        "event": "jev_outage",
        "dataset": plan.dataset.value,
        "part": part + 1,
        "parts": len(plan.requests),
        "reason": str(exc),
    }
    log.warning(json.dumps(fields))


__all__ = ("AGREEMENT", "CompletenessLevel", "JevScore", "JevVerdict", "score_answer")
