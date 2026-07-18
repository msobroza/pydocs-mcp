"""Pin RecallAtK: 1.0 iff any of the top-k retrieved AST-matches gold; 0.0
otherwise. Boundary at exactly rank k. Tolerant to whitespace + comments
(inherited from ast_equivalent). Empty retrieved and missing gold both
degrade to 0.0 so a partial dataset never aborts a run."""

from __future__ import annotations

from pathlib import Path

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.metrics import RecallAtK
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


def test_gold_at_rank_1_with_k1_returns_1_0() -> None:
    metric = RecallAtK(k=1)
    assert metric.compute(_task(GOLD), (_item(1, GOLD),)) == 1.0


def test_gold_at_rank_5_with_k5_returns_1_0() -> None:
    metric = RecallAtK(k=5)
    retrieved = tuple(_item(i, "def other(): pass") for i in range(1, 5)) + (_item(5, GOLD),)
    assert metric.compute(_task(GOLD), retrieved) == 1.0


def test_gold_at_rank_6_with_k5_returns_0_0() -> None:
    metric = RecallAtK(k=5)
    retrieved = tuple(_item(i, "def other(): pass") for i in range(1, 6)) + (_item(6, GOLD),)
    assert metric.compute(_task(GOLD), retrieved) == 0.0


def test_gold_absent_returns_0_0() -> None:
    metric = RecallAtK(k=3)
    retrieved = tuple(_item(i, "def other(): pass") for i in range(1, 4))
    assert metric.compute(_task(GOLD), retrieved) == 0.0


def test_ast_equivalent_with_whitespace_matches() -> None:
    metric = RecallAtK(k=1)
    retrieved = (_item(1, "def f():\n    return 1  # explanation\n"),)
    assert metric.compute(_task(GOLD), retrieved) == 1.0


def test_empty_retrieved_returns_0_0() -> None:
    metric = RecallAtK(k=5)
    assert metric.compute(_task(GOLD), ()) == 0.0


def test_gold_ast_body_none_returns_0_0() -> None:
    # WHY: future datasets (SWE-bench-style file-list golds) leave ast_body
    # unset; the metric must degrade cleanly instead of crashing.
    metric = RecallAtK(k=1)
    assert metric.compute(_task(None), (_item(1, GOLD),)) == 0.0


def test_instance_name_is_recall_at_k_with_k_value() -> None:
    # WHY: aggregation keys by metric name; k must appear in the name so
    # recall@1 and recall@5 don't collide in the same run.
    assert RecallAtK(k=1).name == "recall@1"
    assert RecallAtK(k=5).name == "recall@5"
