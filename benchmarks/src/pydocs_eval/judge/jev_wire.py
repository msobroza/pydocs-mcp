"""The System One wire format: typed questions over named-JSON state, and the answers they get.

A request carries every question about one answer, keyed by an id Jev never
sees (the TypeSafe API reference). A Noul asks whether a condition holds and is
answered with the probability of yes; a Score places the state on ordered
levels and is answered with a probability-weighted position and a confidence; a
Choice picks one option and is answered with the distribution over all of them.

Example:
    >>> JevRequest(state={}, questions={}).body("jev-1.13")
    {'model': 'jev-1.13', 'state': {}, 'questions': {}}
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydocs_eval.judge.judge_errors import JudgeResponseError
from pydocs_eval.judge.openrouter_http import usage_cost
from pydocs_eval.trajectory.blob_store import canonical_json

#: What an instruction or a criterion may be: plain text, or structure that
#: names the data it refers to (``{"gold_site": {...}, "question": "..."}``).
Instructions = str | Mapping[str, object]


class JevQuestionType(StrEnum):
    """The three System One question types, as the wire spells them."""

    NOUL = "noul"
    SCORE = "score"
    CHOICE = "choice"


@dataclass(frozen=True, slots=True)
class NoulQuestion:
    """Whether a condition holds: ``when_true`` and ``when_false`` say what yes and no mean."""

    instructions: Instructions
    when_true: str
    when_false: str

    def to_wire(self) -> dict[str, object]:
        criteria = {"true": self.when_true, "false": self.when_false}
        return {
            "type": JevQuestionType.NOUL.value,
            "instructions": self.instructions,
            "criteria": criteria,
        }


@dataclass(frozen=True, slots=True)
class ScoreQuestion:
    """A position on ``levels``, lowest first; each level is judged on its own."""

    instructions: Instructions
    levels: tuple[str, ...]

    def to_wire(self) -> dict[str, object]:
        return {
            "type": JevQuestionType.SCORE.value,
            "instructions": self.instructions,
            "criteria": list(self.levels),
        }


@dataclass(frozen=True, slots=True)
class ChoiceQuestion:
    """One of ``options``; an option's description may be ``None`` when its name says it all."""

    instructions: Instructions
    options: Mapping[str, str | None]

    def to_wire(self) -> dict[str, object]:
        return {
            "type": JevQuestionType.CHOICE.value,
            "instructions": self.instructions,
            "criteria": dict(self.options),
        }


JevQuestion = NoulQuestion | ScoreQuestion | ChoiceQuestion


@dataclass(frozen=True, slots=True)
class JevRequest:
    """Every question asked about one state; the model is the client's pin, added at send time."""

    state: Mapping[str, object]
    questions: Mapping[str, JevQuestion]

    def body(self, model: str) -> dict[str, object]:
        """The ``systemone`` request body under ``model``: model, state, then questions."""
        questions = {question_id: q.to_wire() for question_id, q in self.questions.items()}
        return {"model": model, "state": dict(self.state), "questions": questions}


def jev_cache_key(body: Mapping[str, object]) -> str:
    """The input hash a Jev answer is cached under: its whole body, the model pin included.

    Example:
        >>> len(jev_cache_key(JevRequest(state={}, questions={}).body("jev-1.13")))
        64
    """
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class NoulAnswer:
    """The probability that the Noul's condition holds."""

    probability: float


@dataclass(frozen=True, slots=True)
class ScoreAnswer:
    """The probability-weighted position on the levels, and each level's probability by index."""

    score: float
    confidence: float
    probabilities: Mapping[str, float]


@dataclass(frozen=True, slots=True)
class ChoiceAnswer:
    """The most probable option, and every option's probability."""

    choice: str
    confidence: float
    probabilities: Mapping[str, float]


JevAnswer = NoulAnswer | ScoreAnswer | ChoiceAnswer


@dataclass(frozen=True, slots=True)
class JevResponse:
    """What Jev answered, by question id, and which model answered it."""

    served_model: str
    answers: Mapping[str, JevAnswer]
    cost_usd: float | None = None


def parse_jev_response(payload: object) -> JevResponse:
    """A ``systemone`` response body, read into typed answers.

    Its errors quote the offending value unredacted: the client that knows the
    bearer redacts them before they leave it.

    Example:
        >>> parse_jev_response({"model": "jev-1.13", "answers": {"x": {"type": "noul", "noul": 0.9}}})
        JevResponse(served_model='jev-1.13', answers={'x': NoulAnswer(probability=0.9)}, cost_usd=None)

    Raises:
        JudgeResponseError: a field the API reference requires is missing or mistyped.
    """
    body = _mapping(payload, "response")
    answers = _mapping(body.get("answers"), "answers")
    return JevResponse(
        served_model=_text(body.get("model"), "model"),
        answers={
            str(question_id): _answer(question_id, raw) for question_id, raw in answers.items()
        },
        cost_usd=usage_cost(body),
    )


def _answer(question_id: object, raw: object) -> JevAnswer:
    answer = _mapping(raw, f"answers[{question_id!r}]")
    kind = answer.get("type")
    if kind == JevQuestionType.NOUL:
        return NoulAnswer(probability=_number(answer.get("noul"), f"{question_id}.noul"))
    if kind == JevQuestionType.SCORE:
        return ScoreAnswer(
            score=_number(answer.get("score"), f"{question_id}.score"),
            confidence=_number(answer.get("confidence"), f"{question_id}.confidence"),
            probabilities=_probabilities(answer.get("probabilities"), question_id),
        )
    if kind == JevQuestionType.CHOICE:
        return ChoiceAnswer(
            choice=_text(answer.get("choice"), f"{question_id}.choice"),
            confidence=_number(answer.get("confidence"), f"{question_id}.confidence"),
            probabilities=_probabilities(answer.get("probabilities"), question_id),
        )
    raise JudgeResponseError(
        f"answers[{question_id!r}].type = {kind!r}, expected noul, score or choice"
    )


def _probabilities(raw: object, question_id: object) -> dict[str, float]:
    where = f"{question_id}.probabilities"
    return {str(key): _number(value, where) for key, value in _mapping(raw, where).items()}


def _mapping(value: object, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise JudgeResponseError(f"{where} = {value!r}, expected a JSON object")
    return value


def _number(value: object, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise JudgeResponseError(f"{where} = {value!r}, expected a number")
    return float(value)


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise JudgeResponseError(f"{where} = {value!r}, expected a non-empty string")
    return value


__all__ = (
    "ChoiceAnswer",
    "ChoiceQuestion",
    "Instructions",
    "JevAnswer",
    "JevQuestion",
    "JevQuestionType",
    "JevRequest",
    "JevResponse",
    "NoulAnswer",
    "NoulQuestion",
    "ScoreAnswer",
    "ScoreQuestion",
    "jev_cache_key",
    "parse_jev_response",
)
