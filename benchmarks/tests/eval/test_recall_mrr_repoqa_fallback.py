"""Regression guard for the ``recall@k`` / ``mrr`` refactor onto
``first_relevant_rank``.

The discriminator is ``task.gold.ast_body is None``:

- RepoQA gold ALWAYS has ``ast_body`` (and NO ``resolved_chunk_ids``) ->
  relevance must come from the ast match, byte-identical to the
  pre-refactor ``find_first_match_rank(retrieved, gold.ast_body)`` call.
- DS-1000 gold NEVER has ``ast_body`` (it has ``resolved_chunk_ids``) ->
  relevance comes from the resolved set.

This file pins BOTH branches so a future change to the discriminator can't
silently break either dataset. Hermetic: no ``pydocs_mcp`` import.
"""
from __future__ import annotations

from pathlib import Path

from benchmarks.eval.ast_match import find_first_match_rank
from benchmarks.eval.datasets.base_dataset import EvalTask, GoldAnswer
from benchmarks.eval.metrics import MRR, RecallAtK
from benchmarks.eval.metrics._relevance import first_relevant_rank
from benchmarks.eval.systems.base_system import RetrievedItem

GOLD = "def f(): return 1"


def _repoqa_task() -> EvalTask:
    # ast_body set, NO resolved_chunk_ids in extra -> RepoQA branch.
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(ast_body=GOLD),
        corpus_source=lambda: Path("."),
    )


def _ds1000_task(resolved: frozenset[str]) -> EvalTask:
    # ast_body None, resolved set present -> DS-1000 branch.
    return EvalTask(
        task_id="t",
        query="q",
        gold=GoldAnswer(extra={"resolved_chunk_ids": resolved}),
        corpus_source=lambda: Path("."),
    )


def _item(rank: int, text: str) -> RetrievedItem:
    return RetrievedItem(rank=rank, text=text, source_path="p")


def _chunk_item(rank: int, chunk_id: int) -> RetrievedItem:
    return RetrievedItem(
        rank=rank, text="x", source_path="p", chunk_id=chunk_id
    )


# ── RepoQA fallback: behavior-preserving vs the raw ast match ──────────


def test_first_relevant_rank_matches_ast_match_on_repoqa() -> None:
    task = _repoqa_task()
    retrieved = (_item(1, "def other(): pass"), _item(2, GOLD))
    # The unified helper must reproduce find_first_match_rank exactly.
    assert first_relevant_rank(retrieved, task) == find_first_match_rank(
        retrieved, GOLD
    )
    assert first_relevant_rank(retrieved, task) == 2


def test_recall_repoqa_unchanged() -> None:
    task = _repoqa_task()
    retrieved = tuple(
        _item(i, "def other(): pass") for i in range(1, 5)
    ) + (_item(5, GOLD),)
    # Pre-refactor: 1.0 iff find_first_match_rank <= k. k=5 hits, k=4 misses.
    assert RecallAtK(k=5).compute(task, retrieved) == 1.0
    assert RecallAtK(k=4).compute(task, retrieved) == 0.0


def test_mrr_repoqa_unchanged() -> None:
    task = _repoqa_task()
    retrieved = (_item(1, "def other(): pass"), _item(2, GOLD))
    assert MRR().compute(task, retrieved) == 0.5


def test_repoqa_no_match_zero() -> None:
    task = _repoqa_task()
    retrieved = tuple(_item(i, "def other(): pass") for i in range(1, 4))
    assert RecallAtK(k=3).compute(task, retrieved) == 0.0
    assert MRR().compute(task, retrieved) == 0.0


# ── DS-1000 branch: relevance from the resolved set ────────────────────


def test_first_relevant_rank_uses_resolved_set_on_ds1000() -> None:
    task = _ds1000_task(frozenset({"chunk:30"}))
    retrieved = (_chunk_item(1, 10), _chunk_item(2, 20), _chunk_item(3, 30))
    assert first_relevant_rank(retrieved, task) == 3


def test_recall_uses_resolved_set_on_ds1000() -> None:
    task = _ds1000_task(frozenset({"chunk:30"}))
    retrieved = (_chunk_item(1, 10), _chunk_item(2, 20), _chunk_item(3, 30))
    assert RecallAtK(k=3).compute(task, retrieved) == 1.0
    assert RecallAtK(k=2).compute(task, retrieved) == 0.0


def test_mrr_uses_resolved_set_on_ds1000() -> None:
    task = _ds1000_task(frozenset({"chunk:20"}))
    retrieved = (_chunk_item(1, 10), _chunk_item(2, 20), _chunk_item(3, 30))
    assert MRR().compute(task, retrieved) == 0.5


def test_ds1000_empty_resolved_misses() -> None:
    task = _ds1000_task(frozenset())
    retrieved = (_chunk_item(1, 10),)
    assert first_relevant_rank(retrieved, task) is None
    assert RecallAtK(k=5).compute(task, retrieved) == 0.0
    assert MRR().compute(task, retrieved) == 0.0
