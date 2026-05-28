"""Pin NDCGAtK: binary-relevance DCG over the top-k retrieved, normalized
by the ideal DCG of ``min(k, |gt|)`` relevant items.

Relevance is the unified ``is_relevant`` predicate, so DS-1000 tasks score
via ``resolved_chunk_ids`` and RepoQA tasks via the ast_body fallback. The
``|gt|`` denominator uses the SAME ``ast_body is None`` discriminator the
predicate does (1 for RepoQA, ``len(resolved_chunk_ids)`` for DS-1000).

The ``n_gt == 0`` guard returns 0.0 BEFORE IDCG so an injected empty
resolved set (pydocs-on-RepoQA) or a store-less DS-1000 task never divides
by zero. Hermetic: no ``pydocs_mcp`` import.
"""
from __future__ import annotations

import math
from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.metrics import NDCGAtK
from benchmarks.eval.systems.base_system import RetrievedItem


def _ds1000_task(resolved: frozenset[str]) -> EvalTask:
    # ast_body is None -> DS-1000 branch; |gt| = len(resolved set).
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"resolved_chunk_ids": resolved}),
        corpus_source=lambda: Path(),
    )


def _repoqa_task(body: str | None) -> EvalTask:
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(ast_body=body),
        corpus_source=lambda: Path(),
    )


def _item(rank: int, chunk_id: int) -> RetrievedItem:
    return RetrievedItem(
        rank=rank, text="x", source_path="p", chunk_id=chunk_id
    )


def test_hit_at_rank_1_is_perfect() -> None:
    # Single GT, single hit at the top -> DCG == IDCG -> 1.0.
    task = _ds1000_task(frozenset({"chunk:1"}))
    retrieved = (_item(1, 1),)
    assert NDCGAtK(k=10).compute(task, retrieved) == 1.0


def test_single_hit_at_rank_10() -> None:
    # n_gt = 1 -> IDCG = 1/log2(2) = 1. DCG = 1/log2(10+1).
    task = _ds1000_task(frozenset({"chunk:99"}))
    retrieved = tuple(_item(i, i) for i in range(1, 10)) + (_item(10, 99),)
    expected = (1.0 / math.log2(11)) / (1.0 / math.log2(2))
    assert NDCGAtK(k=10).compute(task, retrieved) == expected


def test_three_of_five_multi_gold_idcg_normalization() -> None:
    # 5 ground-truth chunks (n_gt=5). Hits land at ranks 1, 3, 4 (chunk
    # ids 10, 30, 40 are in the resolved set; 20, 50 are not).
    resolved = frozenset({"chunk:10", "chunk:30", "chunk:40",
                          "chunk:60", "chunk:70"})
    task = _ds1000_task(resolved)
    retrieved = (
        _item(1, 10),   # hit
        _item(2, 20),   # miss
        _item(3, 30),   # hit
        _item(4, 40),   # hit
        _item(5, 50),   # miss
    )
    dcg = (
        1.0 / math.log2(2)   # rank 1
        + 1.0 / math.log2(4)  # rank 3
        + 1.0 / math.log2(5)  # rank 4
    )
    # IDCG over min(k=10, n_gt=5) = 5 ideal positions.
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, 6))
    assert NDCGAtK(k=10).compute(task, retrieved) == dcg / idcg


def test_ndcg_clamped_to_one_when_relevant_key_repeats() -> None:
    # One ground-truth chunk (n_gt=1 -> IDCG = 1/log2(2) = 1). The SAME
    # relevant key (chunk:10) lands at ranks 1 AND 2, so an unclamped DCG
    # double-counts it (1/log2(2) + 1/log2(3) = 1.63) and NDCG would breach
    # the [0,1] bound. The clamp pins it back to 1.0. Real FTS retrieval
    # yields distinct rows, so this is a defensive bound, not a hot path.
    task = _ds1000_task(frozenset({"chunk:10"}))
    retrieved = (_item(1, 10), _item(2, 10), _item(3, 99))
    assert NDCGAtK(k=10).compute(task, retrieved) == 1.0
    assert NDCGAtK(k=10).compute(task, retrieved) <= 1.0


def test_k_truncates_below_hit_rank() -> None:
    # The only hit is at rank 4 but k=3 -> nothing in the top-k -> 0.0.
    task = _ds1000_task(frozenset({"chunk:40"}))
    retrieved = (_item(1, 1), _item(2, 2), _item(3, 3), _item(4, 40))
    assert NDCGAtK(k=3).compute(task, retrieved) == 0.0


def test_n_gt_zero_empty_resolved_returns_0_0() -> None:
    # WHY: pydocs-on-RepoQA gets an injected EMPTY resolved set with
    # ast_body None-equivalent; n_gt guard must fire BEFORE IDCG to avoid
    # 0/0.
    task = _ds1000_task(frozenset())
    retrieved = (_item(1, 1),)
    assert NDCGAtK(k=10).compute(task, retrieved) == 0.0


def test_empty_retrieved_returns_0_0() -> None:
    task = _ds1000_task(frozenset({"chunk:1"}))
    assert NDCGAtK(k=10).compute(task, ()) == 0.0


def test_repoqa_fallback_hit_at_rank_1() -> None:
    # ast_body present -> n_gt = 1, relevance via ast match.
    gold = "def f(): return 1"
    task = _repoqa_task(gold)
    retrieved = (RetrievedItem(rank=1, text=gold, source_path="p"),)
    assert NDCGAtK(k=10).compute(task, retrieved) == 1.0


def test_instance_name_includes_k() -> None:
    assert NDCGAtK(k=10).name == "ndcg@10"
    assert NDCGAtK(k=5).name == "ndcg@5"
