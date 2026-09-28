"""The reference-answer carrier: ``ReferenceAnswer`` under ``REFERENCE_ANSWER_KEY``.

A reference is written once from ground truth for the answer judge. It rides the
gold's ``extra`` mapping as a value object — never a ``str`` — because the rubric
gates and checks take every string value there as text an answer must contain,
and it is read only through ``reference_answer_of``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import (
    REFERENCE_ANSWER_KEY,
    EvalTask,
    GoldAnswer,
    ReferenceAnswer,
    reference_answer_of,
)
from pydocs_eval.optimize.fitness.ask_rubric import sample_row_for_task
from pydocs_eval.optimize.rubric.checks import Check, evaluate_check
from pydocs_eval.optimize.rubric.gates import evaluate_gate
from pydocs_eval.optimize.rubric.model import GateCheck
from tests.optimize._trajectories import make_trajectory

_REFERENCE = ReferenceAnswer(
    text="MaxSimScorer.score_all in src/needle/scoring/strategies.py sums the best matches.",
    model_id="anthropic/claude-opus-5.5:batch",
    prompt_hash="3f2a9c",
)


def test_a_reference_under_the_key_is_read_back_whole() -> None:
    gold = GoldAnswer(
        file_set=("a.py",), extra={"symbol_0": "alpha", REFERENCE_ANSWER_KEY: _REFERENCE}
    )

    assert reference_answer_of(gold) == _REFERENCE


def test_a_gold_without_a_reference_reads_none() -> None:
    assert reference_answer_of(GoldAnswer(file_set=("a.py",), extra={"symbol_0": "alpha"})) is None


@pytest.mark.parametrize(
    "value",
    [
        "MaxSimScorer.score_all sums the best matches.",
        {"text": "...", "model_id": "m", "prompt_hash": "h"},
        None,
    ],
)
def test_anything_but_a_reference_answer_under_the_key_is_refused(value: object) -> None:
    with pytest.raises(TypeError, match=REFERENCE_ANSWER_KEY) as caught:
        GoldAnswer(extra={REFERENCE_ANSWER_KEY: value})

    assert repr(value) in str(caught.value), "the message names the offending value"
    assert "ReferenceAnswer" in str(caught.value), "the message names the expected shape"


@pytest.mark.parametrize(
    ("fields", "named"),
    [
        ({"text": "", "model_id": "m", "prompt_hash": "h"}, "text"),
        ({"text": "t", "model_id": "", "prompt_hash": "h"}, "model_id"),
        ({"text": None, "model_id": "m", "prompt_hash": "h"}, "text"),
        ({"text": "t", "model_id": "m", "prompt_hash": None}, "prompt_hash"),
    ],
)
def test_a_reference_without_text_or_provenance_is_refused(
    fields: dict[str, object], named: str
) -> None:
    with pytest.raises(ValueError, match=f"ReferenceAnswer.{named}"):
        ReferenceAnswer(**fields)  # type: ignore[arg-type]


def test_a_reference_the_dataset_ships_itself_has_no_prompt_hash() -> None:
    assert ReferenceAnswer(text="t", model_id="swe-qa", prompt_hash="").prompt_hash == ""


def _task(*, reference: bool) -> EvalTask:
    extra: dict[str, object] = {"symbol_0": "alpha"}
    if reference:
        extra[REFERENCE_ANSWER_KEY] = _REFERENCE
    return EvalTask(
        task_id="example-needle-chat/q00",
        query="Where is the MaxSim score computed?",
        gold=GoldAnswer(file_set=("src/needle/scoring/strategies.py",), extra=extra),
        corpus_source=lambda: Path("unused"),
    )


_ANSWERS = (
    f"Only the reference: {_REFERENCE.text}",
    "src/needle/scoring/strategies.py defines alpha.",
    "src/needle/scoring/strategies.py, nothing else.",
    "I could not find it.",
)


@pytest.mark.parametrize("answer", _ANSWERS)
@pytest.mark.parametrize("kind", ["gold_substring", "gold_substring_all"])
def test_the_gates_score_the_same_with_or_without_a_reference(kind: str, answer: str) -> None:
    gate = GateCheck(name=kind, kind=kind, params={})
    trajectory = make_trajectory(answer=answer)

    with_reference = evaluate_gate(gate, _task(reference=True), trajectory)

    assert with_reference == evaluate_gate(gate, _task(reference=False), trajectory)


@pytest.mark.parametrize("answer", _ANSWERS)
def test_gold_recall_scores_the_same_with_or_without_a_reference(answer: str) -> None:
    check = Check(name="recall", kind="gold_recall", params={}, fail=None)
    trajectory = make_trajectory(answer=answer)

    with_reference = evaluate_check(check, _task(reference=True), trajectory).score

    assert with_reference == evaluate_check(check, _task(reference=False), trajectory).score


def test_the_rendered_prompt_never_carries_the_reference_text() -> None:
    row = sample_row_for_task(_task(reference=True))

    assert _REFERENCE.text not in str(row["rendered_prompt"])
    assert _REFERENCE.text not in str(row["question"])


def test_printing_a_gold_never_shows_the_reference_text() -> None:
    gold = GoldAnswer(extra={REFERENCE_ANSWER_KEY: _REFERENCE})

    assert _REFERENCE.text not in repr(gold)
    assert _REFERENCE.text not in f"{gold}"
    assert _REFERENCE.model_id in repr(gold), "the provenance still prints"
