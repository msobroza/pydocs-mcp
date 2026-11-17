"""A dataset's reference answer is never a gate or check candidate (step 7d, Q24).

swe-qa tasks carry their human-written answer, and repoqa-qa tasks with a stored
row carry theirs, under ``gold.extra["reference_answer"]``. The deterministic
gates and checks read ``file_set`` plus every STRING value of ``extra`` as text
an answer must contain, so the reference rides as a value object and drops out
of that list by type. A ``params: {keys: [...]}`` naming the key could still try
to select it, so that parameter is refused — on the gate and on the check alike,
when the rubric is built and when a predicate is called directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import REFERENCE_ANSWER_KEY, EvalTask, reference_answer_of
from pydocs_eval.datasets.repo_qa import SweQaQuestionDataset
from pydocs_eval.datasets.swe_qa import SweQaDataset
from pydocs_eval.optimize.rubric.checks import Check, GoldRecall, evaluate_check
from pydocs_eval.optimize.rubric.gates import GoldSubstringAll, evaluate_gate
from pydocs_eval.optimize.rubric.model import GateCheck
from tests.optimize._trajectories import make_trajectory

_FIXTURES = Path(__file__).parents[1] / "fixtures"
_REFUSED_KEYS = {"keys": [REFERENCE_ANSWER_KEY]}


@dataclass
class _FakeRepoCache:
    """No git, no network; the one gold path the swe-qa fixture cites."""

    corpus_dir: Path = field(default=_FIXTURES / "swe_qa_corpus")

    def checkout(self, url: str, sha: str) -> Path:
        return self.corpus_dir

    def file_tree(self, url: str, sha: str) -> tuple[str, ...]:
        return ("lib/matplotlib/backends/backend_pgf.py",)


async def _swe_qa_question() -> EvalTask:
    source = SweQaDataset(
        fixture_path=_FIXTURES / "swe_qa_mini.jsonl",
        split="matplotlib",
        repo_cache=_FakeRepoCache(),
    )
    return await anext(SweQaQuestionDataset(source=source).tasks())


def _reference_text(task: EvalTask) -> str:
    reference = reference_answer_of(task.gold)
    assert reference is not None, "the fixture task carries its reference"
    return reference.text


@pytest.mark.parametrize("kind", ["gold_substring", "gold_substring_all"])
async def test_an_answer_that_only_restates_the_reference_fails_the_gates(kind: str) -> None:
    task = await _swe_qa_question()
    answer = _reference_text(task)
    assert task.gold.file_set[0] not in answer, "the fixture reference cites a bare file name"

    gate = GateCheck(name=kind, kind=kind, params={})

    assert evaluate_gate(gate, task, make_trajectory(answer=answer)) is False


async def test_gold_recall_scores_the_reference_text_as_naming_nothing() -> None:
    task = await _swe_qa_question()

    outcome = evaluate_check(
        Check(name="recall", kind="gold_recall"),
        task,
        make_trajectory(answer=_reference_text(task)),
    )

    assert outcome.score == 0.0


@pytest.mark.parametrize(
    "build",
    [
        lambda: GateCheck(name="g", kind="gold_substring_all", params=_REFUSED_KEYS),
        lambda: Check(name="c", kind="gold_recall", params=_REFUSED_KEYS),
        lambda: GateCheck(
            name="g", kind="gold_substring_all", params={"keys": ("file_set", "reference_answer")}
        ),
    ],
    ids=["gate", "check", "gate-among-other-keys"],
)
def test_a_rubric_naming_the_reference_key_is_refused_when_it_is_built(build) -> None:
    with pytest.raises(ValueError) as caught:
        build()

    assert repr(REFERENCE_ANSWER_KEY) in str(caught.value)
    assert "file_set" in str(caught.value), "the message names what keys may select"


@pytest.mark.parametrize("predicate", [GoldSubstringAll(), GoldRecall()], ids=["gate", "check"])
async def test_a_predicate_called_with_the_reference_key_refuses_it_too(predicate) -> None:
    task = await _swe_qa_question()

    with pytest.raises(ValueError, match=REFERENCE_ANSWER_KEY):
        predicate(task, make_trajectory(answer=_reference_text(task)), _REFUSED_KEYS)
