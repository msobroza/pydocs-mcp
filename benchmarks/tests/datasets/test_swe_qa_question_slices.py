"""swe-qa-questions slices: stratified by gold-size bucket, never by repo (step 7d, Q13).

``swe-qa`` has no dev/test partition and only slices by REPO, and a repo
stratum would be one or two rows deep (Q13). The framing over it therefore
cuts its own ``dev`` / ``test`` / ``small_dev`` /
``small_test`` across gold-size buckets (``1``, ``2-3``, ``4+`` cited files),
seeded so the membership is stable. The source here is a named fake: offline,
with known gold sizes spread over several repos.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from pydocs_eval.datasets._split import VALID_SPLITS
from pydocs_eval.datasets.base_dataset import EvalTask
from pydocs_eval.datasets.repo_qa import SweQaQuestionDataset
from pydocs_eval.datasets.swe_qa_question_slices import (
    SWE_QA_QUESTION_SLICES,
    GoldSizeBucket,
    gold_size_bucket,
)

from ._swe_qa_fakes import GOLD_SIZES, REPOS, FakeSweQaSource, sized_gold_task


async def _slice(split: str) -> list[EvalTask]:
    dataset = SweQaQuestionDataset(source=FakeSweQaSource(), split=split)
    return [task async for task in dataset.tasks()]


def _buckets(tasks: list[EvalTask]) -> Counter[GoldSizeBucket]:
    return Counter(gold_size_bucket(task) for task in tasks)


def test_the_buckets_are_one_two_to_three_and_four_or_more() -> None:
    assert [bucket.value for bucket in SWE_QA_QUESTION_SLICES] == ["1", "2-3", "4+"]


@pytest.mark.parametrize(
    ("size", "bucket"),
    [(1, "1"), (2, "2-3"), (3, "2-3"), (4, "4+"), (11, "4+")],
)
def test_a_task_falls_in_the_bucket_of_its_gold_size(size: int, bucket: str) -> None:
    assert gold_size_bucket(sized_gold_task(0, size)) == bucket


def test_a_task_without_gold_files_has_no_bucket_and_says_so() -> None:
    with pytest.raises(ValueError) as caught:
        gold_size_bucket(sized_gold_task(7, 0))

    assert "'swe_qa/sympy/7'" in str(caught.value)
    assert "at least one" in str(caught.value)


@pytest.mark.parametrize("split", VALID_SPLITS)
async def test_every_slice_loads_and_holds_every_bucket(split: str) -> None:
    tasks = await _slice(split)

    assert set(_buckets(tasks)) == set(GoldSizeBucket)


async def test_all_is_the_whole_framed_corpus_in_source_order() -> None:
    tasks = await _slice("all")

    assert [task.record_id for task in tasks] == [
        f"swe_qa/{REPOS[i % len(REPOS)]}/{i}" for i in range(len(GOLD_SIZES))
    ]


async def test_dev_takes_a_fifth_of_every_bucket_and_test_the_rest() -> None:
    dev, test = await _slice("dev"), await _slice("test")

    assert _buckets(dev) == {"1": 2, "2-3": 2, "4+": 2}
    assert _buckets(test) == {"1": 10, "2-3": 8, "4+": 6}
    assert not {t.task_id for t in dev} & {t.task_id for t in test}


async def test_the_small_slices_are_subsets_of_their_halves() -> None:
    dev_ids = {t.task_id for t in await _slice("dev")}
    test_ids = {t.task_id for t in await _slice("test")}

    assert {t.task_id for t in await _slice("small_dev")} <= dev_ids
    assert {t.task_id for t in await _slice("small_test")} <= test_ids


@pytest.mark.parametrize("split", ["dev", "test", "small_dev", "small_test"])
async def test_a_slice_is_the_same_on_every_build(split: str) -> None:
    first = [task.task_id for task in await _slice(split)]

    assert first == [task.task_id for task in await _slice(split)]


async def test_the_membership_does_not_depend_on_the_order_rows_arrive_in() -> None:
    @dataclass
    class _ReversedSource(FakeSweQaSource):
        async def tasks(self) -> AsyncIterator[EvalTask]:
            for index, size in reversed(list(enumerate(GOLD_SIZES))):
                yield sized_gold_task(index, size)

    dataset = SweQaQuestionDataset(source=_ReversedSource(), split="dev")
    reversed_dev = {task.task_id async for task in dataset.tasks()}

    assert reversed_dev == {task.task_id for task in await _slice("dev")}


def test_an_unknown_slice_is_refused_at_construction_with_the_valid_ones() -> None:
    with pytest.raises(ValueError) as caught:
        SweQaQuestionDataset(source=FakeSweQaSource(), split="reserved")

    assert "'reserved'" in str(caught.value)
    assert "small_dev" in str(caught.value)


def test_the_default_slice_is_the_whole_corpus() -> None:
    assert SweQaQuestionDataset().split == "all"
