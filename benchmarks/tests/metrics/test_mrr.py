"""Pin MRR: reciprocal rank of the first AST-match (1-indexed). 0 if no
match. See https://en.wikipedia.org/wiki/Mean_reciprocal_rank."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics import MRR
from pydocs_eval.systems.base_system import RetrievedItem


def _task(body: str | None) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(ast_body=body),
        corpus_source=lambda: Path(),
    )


def _item(rank: int, text: str) -> RetrievedItem:
    return RetrievedItem(rank=rank, text=text, source_path="x.py")


GOLD = "def f(): return 1"


def test_rank_1_returns_1_0() -> None:
    assert MRR().compute(_task(GOLD), (_item(1, GOLD),)) == 1.0


def test_rank_2_returns_0_5() -> None:
    retrieved = (_item(1, "def other(): pass"), _item(2, GOLD))
    assert MRR().compute(_task(GOLD), retrieved) == 0.5


def test_no_match_returns_0_0() -> None:
    retrieved = tuple(_item(i, "def other(): pass") for i in range(1, 4))
    assert MRR().compute(_task(GOLD), retrieved) == 0.0


def test_picks_first_match_when_multiple() -> None:
    # WHY: MRR is defined as the *first* hit's reciprocal rank; a later
    # duplicate must not improve or worsen the score.
    retrieved = (
        _item(1, "def other(): pass"),
        _item(2, GOLD),
        _item(3, GOLD),
    )
    assert MRR().compute(_task(GOLD), retrieved) == 0.5
