"""Pin Precision1: 1.0 iff the rank-1 retrieved item is relevant under the
unified ``is_relevant`` predicate, else 0.0.

Routes through ``is_relevant`` so it covers both DS-1000 (resolved set)
and RepoQA (ast_body fallback) with no per-metric branching. Hermetic: no
``pydocs_mcp`` import.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.metrics import Precision1
from benchmarks.eval.systems.base_system import RetrievedItem


def _ds1000_task(resolved: frozenset[str]) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"resolved_chunk_ids": resolved}),
        corpus_source=lambda: Path("."),
    )


def _repoqa_task(body: str | None) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(ast_body=body),
        corpus_source=lambda: Path("."),
    )


def _item(rank: int, chunk_id: int) -> RetrievedItem:
    return RetrievedItem(
        rank=rank, text="x", source_path="p", chunk_id=chunk_id
    )


def test_top1_relevant_via_resolved_set_returns_1_0() -> None:
    task = _ds1000_task(frozenset({"chunk:5"}))
    retrieved = (_item(1, 5), _item(2, 6))
    assert Precision1().compute(task, retrieved) == 1.0


def test_top1_irrelevant_returns_0_0() -> None:
    # The hit is at rank 2; precision@1 only looks at rank 1.
    task = _ds1000_task(frozenset({"chunk:6"}))
    retrieved = (_item(1, 5), _item(2, 6))
    assert Precision1().compute(task, retrieved) == 0.0


def test_empty_retrieved_returns_0_0() -> None:
    task = _ds1000_task(frozenset({"chunk:5"}))
    assert Precision1().compute(task, ()) == 0.0


def test_repoqa_top1_match_returns_1_0() -> None:
    gold = "def f(): return 1"
    task = _repoqa_task(gold)
    retrieved = (RetrievedItem(rank=1, text=gold, source_path="p"),)
    assert Precision1().compute(task, retrieved) == 1.0


def test_repoqa_top1_no_match_returns_0_0() -> None:
    gold = "def f(): return 1"
    task = _repoqa_task(gold)
    retrieved = (RetrievedItem(rank=1, text="def g(): return 2",
                               source_path="p"),)
    assert Precision1().compute(task, retrieved) == 0.0


def test_name_is_precision_at_1() -> None:
    assert Precision1().name == "precision@1"
