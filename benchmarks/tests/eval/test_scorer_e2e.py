"""Scorer composition over the bundled fixture tasks — no runner.

Per-metric isolation: if ``test_integration_oracle.py`` /
``test_integration_empty.py`` fail and these pass, the bug is in the
runner; if these fail too, the bug is in a metric. Walks the fixture
directly, hand-crafts ``retrieved`` tuples for each task, and asserts
the per-metric contribution one metric at a time.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from benchmarks.eval.datasets.base_dataset import EvalTask
from benchmarks.eval.datasets.repoqa import RepoQADataset
from benchmarks.eval.metrics import MRR, PassAt1Needle, RecallAtK
from benchmarks.eval.metrics.base_metric import Scorer
from benchmarks.eval.systems.base_system import RetrievedItem

_FIXTURE = Path(__file__).parent / "fixtures" / "repoqa_mini.json"


def _load_fixture_tasks() -> list[EvalTask]:
    """Consume the Dataset Protocol — the same path the runner walks.

    Decouples this test from the on-disk JSON shape so future schema
    changes only touch the loader.
    """

    async def _collect() -> list[EvalTask]:
        dataset = RepoQADataset(fixture_path=_FIXTURE)
        return [t async for t in dataset.tasks()]

    return asyncio.run(_collect())


def _oracle_retrieved(task: EvalTask) -> tuple[RetrievedItem, ...]:
    """Gold as rank-1 hit. Every metric must collapse to 1.0."""
    return (
        RetrievedItem(
            rank=1,
            text=task.gold.ast_body or "",
            source_path="<oracle>",
        ),
    )


def _gold_at_rank_3_retrieved(task: EvalTask) -> tuple[RetrievedItem, ...]:
    """Gold at rank 3. Pins MRR = 1/3, recall@1 = 0, recall@5 = 1."""
    decoys = tuple(
        RetrievedItem(rank=i, text=f"def decoy_{i}(): pass", source_path="x.py")
        for i in range(1, 3)
    )
    gold = RetrievedItem(
        rank=3,
        text=task.gold.ast_body or "",
        source_path="<gold>",
    )
    return decoys + (gold,)


def test_scorer_oracle_retrieval_scores_one_per_task() -> None:
    # WHY: pin every metric individually to 1.0 on every fixture task with
    # the canonical "gold at rank 1" pattern. If any metric returns < 1.0
    # here, the bug is in that metric — not in the runner or aggregator.
    scorer = Scorer(
        metrics=(
            RecallAtK(k=1),
            RecallAtK(k=5),
            RecallAtK(k=10),
            MRR(),
            PassAt1Needle(),
        )
    )
    tasks = _load_fixture_tasks()
    assert len(tasks) == 5

    for task in tasks:
        scores = scorer.score(task, _oracle_retrieved(task))
        assert scores["recall@1"] == 1.0
        assert scores["recall@5"] == 1.0
        assert scores["recall@10"] == 1.0
        assert scores["mrr"] == 1.0
        assert scores["pass@1-needle"] == 1.0


def test_scorer_empty_retrieval_scores_zero_per_task() -> None:
    # WHY: empty retrieval = no signal; every metric must read 0.0. The
    # mirror of the oracle test, scoped to the Scorer rather than the
    # runner.
    scorer = Scorer(
        metrics=(
            RecallAtK(k=1),
            RecallAtK(k=5),
            RecallAtK(k=10),
            MRR(),
            PassAt1Needle(),
        )
    )
    tasks = _load_fixture_tasks()

    for task in tasks:
        scores = scorer.score(task, ())
        assert scores["recall@1"] == 0.0
        assert scores["recall@5"] == 0.0
        assert scores["recall@10"] == 0.0
        assert scores["mrr"] == 0.0
        assert scores["pass@1-needle"] == 0.0


def test_scorer_gold_at_rank_3_pins_each_metric() -> None:
    # WHY: rank-3 retrieval discriminates between metrics that share
    # 1.0/0.0 outputs in the oracle/empty cases. recall@1 = 0 (gold past
    # cutoff), recall@5 = 1 (gold inside cutoff), mrr = 1/3, pass@1 = 0
    # (top-1 is a decoy). Catches a metric that ignores its k slice or
    # swaps recall@k for recall@∞.
    scorer = Scorer(
        metrics=(
            RecallAtK(k=1),
            RecallAtK(k=5),
            MRR(),
            PassAt1Needle(),
        )
    )
    tasks = _load_fixture_tasks()

    for task in tasks:
        scores = scorer.score(task, _gold_at_rank_3_retrieved(task))
        assert scores["recall@1"] == 0.0
        assert scores["recall@5"] == 1.0
        assert scores["mrr"] == 1.0 / 3.0
        assert scores["pass@1-needle"] == 0.0
