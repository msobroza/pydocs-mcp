"""Pin PassAt1Needle: AST-match on the top-1 retrieved item only. RepoQA's
"needle-in-the-haystack" pass criterion (spec §4.11)."""
from __future__ import annotations

from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.metrics import PassAt1Needle
from benchmarks.eval.systems.base_system import RetrievedItem


def _task(body: str | None) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(ast_body=body),
        corpus_source=lambda: Path("."),
    )


def _item(rank: int, text: str) -> RetrievedItem:
    return RetrievedItem(rank=rank, text=text, source_path="x.py")


GOLD = "def f(): return 1"


def test_top_1_matches_returns_1_0() -> None:
    assert PassAt1Needle().compute(_task(GOLD), (_item(1, GOLD),)) == 1.0


def test_top_1_does_not_match_returns_0_0() -> None:
    retrieved = (
        _item(1, "def other(): pass"),
        _item(2, GOLD),  # WHY: rank-2 match must NOT count for pass@1.
    )
    assert PassAt1Needle().compute(_task(GOLD), retrieved) == 0.0


def test_empty_retrieved_returns_0_0() -> None:
    assert PassAt1Needle().compute(_task(GOLD), ()) == 0.0
