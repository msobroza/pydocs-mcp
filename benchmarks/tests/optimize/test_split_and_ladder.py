"""Deterministic train/holdout split + fitness-ladder value objects (plan Task 5)."""

from __future__ import annotations

import hashlib

import pytest

from benchmarks.optimize._split import partition_task_ids, task_split
from benchmarks.optimize.ladder import FitnessLadder, Rung


def test_split_predicate_is_the_pinned_sha256_mod2() -> None:
    for tid in ("swe-qa-pro:0001", "swe-qa-pro:0002", "anything"):
        expected = (
            "train" if int(hashlib.sha256(tid.encode()).hexdigest(), 16) % 2 == 0 else "holdout"
        )
        assert task_split(tid) == expected


def test_partition_errors_clearly_when_one_side_empty() -> None:
    one_sided = [t for t in (f"t{i}" for i in range(50)) if task_split(t) == "train"][:4]
    with pytest.raises(ValueError, match="holdout"):
        partition_task_ids(one_sided)


def test_ladder_rungs_and_survivor_selection() -> None:
    ladder = FitnessLadder(
        rungs=(
            Rung("paired_agent", max_tasks=6, survivors=4),
            Rung("paired_agent", max_tasks=24, survivors=1),
        )
    )
    scored = {"a": 0.3, "b": 0.1, "c": float("-inf"), "d": 0.2, "e": 0.25}
    assert ladder.rungs[0].select_survivors(scored) == ("a", "e", "d", "b")  # -inf never survives
