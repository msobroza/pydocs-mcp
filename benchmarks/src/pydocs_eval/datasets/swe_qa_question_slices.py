"""The swe-qa-questions slices: a seeded cut across gold-size buckets (step 7d, Q13).

``swe-qa`` has no dev/test partition; its own splits are per-REPO. The framing
over it must not stratify by repo either (Q13: 48 repos over 50 rows), since
almost every repo stratum would be a single row, and a stratified cut of
single rows is a random cut with extra steps. So the framing stratifies by
how many files the gold cites — ``1``, ``2-3`` or ``4+`` — which is the axis a
completeness measurement actually varies along, and every bucket keeps its
share in ``dev`` and ``test``.

The cut is ``datasets/_split.stratified_split`` (the repoqa / DS-1000
precedent), so the determinism contract and the ``small_*`` apportionment are
the shared ones. ``all`` is the whole framed corpus, unchanged.

Example:
    >>> [bucket.value for bucket in SWE_QA_QUESTION_SLICES]
    ['1', '2-3', '4+']
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from ._split import stratified_split
from .base_dataset import EvalTask

# The smallest gold of the two upper buckets.
_TWO_FILES = 2
_FOUR_FILES = 4


class GoldSizeBucket(StrEnum):
    """How many files a task's gold cites — the stratum every slice is cut across."""

    ONE = "1"
    TWO_TO_THREE = "2-3"
    FOUR_OR_MORE = "4+"


#: The buckets the swe-qa-questions slices stratify by, in order.
SWE_QA_QUESTION_SLICES: tuple[GoldSizeBucket, ...] = tuple(GoldSizeBucket)


def gold_size_bucket(task: EvalTask) -> GoldSizeBucket:
    """The bucket of ``task``'s gold file count.

    Raises:
        ValueError: the gold cites no file, so the task has no bucket.

    Example:
        >>> gold_size_bucket(task)  # doctest: +SKIP
        <GoldSizeBucket.TWO_TO_THREE: '2-3'>
    """
    size = len(task.gold.file_set)
    if size < 1:
        raise ValueError(
            f"task {task.task_id!r} has a gold of {size} file(s), expected at least one "
            "cited file to bucket it by"
        )
    if size < _TWO_FILES:
        return GoldSizeBucket.ONE
    return GoldSizeBucket.TWO_TO_THREE if size < _FOUR_FILES else GoldSizeBucket.FOUR_OR_MORE


def swe_qa_question_slice(
    tasks: Sequence[EvalTask], *, split: str, seed: int, dev_fraction: float
) -> list[EvalTask]:
    """The ``split`` slice of the framed ``tasks``, stratified by gold-size bucket.

    The sort key is the task id, so the membership is independent of the
    order the source yields its rows in.
    """
    return stratified_split(
        list(tasks),
        split=split,
        dev_fraction=dev_fraction,
        seed=seed,
        stratum_of=gold_size_bucket,
        sort_key=lambda task: task.task_id,
    )


__all__ = [
    "SWE_QA_QUESTION_SLICES",
    "GoldSizeBucket",
    "gold_size_bucket",
    "swe_qa_question_slice",
]
