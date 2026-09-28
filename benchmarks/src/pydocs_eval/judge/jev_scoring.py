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
from pydocs_eval.judge.jev_client import JevJudge
from pydocs_eval.judge.jev_questions import JevQuestionKind, kind_of
from pydocs_eval.judge.jev_requests import JevRequestPlan
from pydocs_eval.judge.jev_wire import JevAnswer, NoulAnswer, ScoreAnswer
from pydocs_eval.judge.openrouter_http import JudgeUnavailableError
from pydocs_eval.judge.roles import jev_model
from pydocs_eval.judge.thresholds import (
    CompletenessLevel,
    DatasetThresholds,
    JevVerdict,
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


def score_answer(plan: JevRequestPlan, *, judge: JevJudge, config: JudgeConfig) -> JevScore:
    """Score ``plan``'s answer with ``judge`` under ``config``'s thresholds for its dataset.

    Raises:
        MissingThresholdsError: a question of the plan has no fitted block, before any call.
        JudgeConfigError: ``judge.jev.model`` is empty.
        ValueError: the plan carries the audit question, which never scores.
        JudgeModelMismatchError: the model that answered is not the pin.
    """
    kinds = {kind_of(question_id) for question_id in plan.question_ids}
    if JevQuestionKind.COMMITTED_FUNCTION in kinds:
        raise ValueError("committed_function is the alignment audit, never a scoring question")
    thresholds = thresholds_for(
        config.thresholds, jev_model=jev_model(config), dataset=plan.dataset.value, kinds=kinds
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
        if kind is JevQuestionKind.COMPLETENESS:
            continue
        verdict = _noul_verdict(answers.get(question_id), thresholds, kind)
        if kind is JevQuestionKind.CONTRADICTS_REFERENCE:
            verdicts[AGREEMENT] = _AGREEMENT_OF[verdict]
        else:
            verdicts[question_id] = verdict
    return verdicts


def _noul_verdict(
    answer: JevAnswer | None, thresholds: DatasetThresholds, kind: JevQuestionKind
) -> JevVerdict:
    if not isinstance(answer, NoulAnswer):
        return JevVerdict.UNDEFINED
    return noul_verdict(answer.probability, thresholds.bands[kind])


def _completeness(
    answers: Mapping[str, JevAnswer], thresholds: DatasetThresholds
) -> CompletenessLevel:
    answer = answers.get(JevQuestionKind.COMPLETENESS.value)
    if not isinstance(answer, ScoreAnswer) or thresholds.completeness is None:
        return CompletenessLevel.UNDEFINED
    return completeness_level(answer, thresholds.completeness)


def _log_outage(plan: JevRequestPlan, part: int, exc: JudgeUnavailableError) -> None:
    fields = {"event": "jev_outage", "dataset": plan.dataset.value, "part": part + 1}
    log.warning(json.dumps({**fields, "parts": len(plan.requests), "reason": str(exc)}))


__all__ = ("AGREEMENT", "CompletenessLevel", "JevScore", "JevVerdict", "score_answer")
