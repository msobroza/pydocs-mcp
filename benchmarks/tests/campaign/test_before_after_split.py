"""Which slices ``before-after --split`` loads, and how a refused one reads.

The plan must fail with a named ``MeasurementPlanError`` — the error the command
turns into exit 2 — never a traceback, so a slice a dataset refuses is mapped at
the one place both arms load tasks through.
"""

from __future__ import annotations

from functools import partial
from pathlib import Path

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_split import (
    UNPARTITIONED_DATASETS,
    load_split_tasks,
    slice_names_for,
)
from pydocs_eval.datasets._split import VALID_SPLITS
from pydocs_eval.datasets.bug_localization import LcaBugLocDataset
from pydocs_eval.datasets.repo_qa import SweQaQuestionDataset
from pydocs_eval.datasets.swe_qa_question_slices import GoldSizeBucket, gold_size_bucket
from pydocs_eval.registries import dataset_registry

from ..datasets._swe_qa_fakes import FakeSweQaSource

_FIXTURES = Path(__file__).parents[1] / "fixtures"


async def test_the_chat_dev_slice_loads_in_dataset_order() -> None:
    tasks = await load_split_tasks("example-needle-chat/dev")

    assert [task.task_id for task in tasks] == [f"example-needle-chat/q{i:02d}" for i in range(10)]


async def test_a_slice_the_chat_dataset_refuses_is_a_plan_error_naming_it() -> None:
    # small_dev passes the global split vocabulary but is not a chat slice.
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks("example-needle-chat/small_dev")

    assert "small_dev" in str(caught.value)
    assert "example-needle-chat" in str(caught.value)


@pytest.mark.parametrize(
    ("slice_name", "count"), [("test", 20), ("reserved", 10), ("held_out", 30), ("all", 40)]
)
async def test_every_chat_slice_loads_through_before_after(slice_name: str, count: int) -> None:
    tasks = await load_split_tasks(f"example-needle-chat/{slice_name}")

    assert len(tasks) == count


def test_the_chat_dataset_answers_its_own_slice_names() -> None:
    assert slice_names_for("example-needle-chat") == (
        "dev",
        "test",
        "reserved",
        "held_out",
        "all",
    )


def test_no_other_dataset_changes_its_slice_names() -> None:
    own_names = {"example-needle-chat", *UNPARTITIONED_DATASETS}
    others = [name for name in dataset_registry.names() if name not in own_names]

    assert others, "the registry is populated"
    assert {name: slice_names_for(name) for name in others} == dict.fromkeys(others, VALID_SPLITS)


async def test_reserved_is_still_refused_by_name_on_a_dataset_without_it() -> None:
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks("repoqa-qa/reserved")

    assert "'reserved'" in str(caught.value)
    assert "repoqa-qa" in str(caught.value)


# --- the datasets without a split field answer ``/all`` only ---------------


def _lca_from_its_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """``lca-bug-loc`` built from its offline fixture wherever the registry builds it."""
    fixture_backed = partial(LcaBugLocDataset, fixture_path=_FIXTURES / "lca_bug_loc_mini.jsonl")
    monkeypatch.setitem(dataset_registry._items, "lca-bug-loc", fixture_backed)  # type: ignore[arg-type]


def test_the_unpartitioned_datasets_are_lca_bug_loc_and_crosscommitvuln() -> None:
    assert frozenset({"lca-bug-loc", "crosscommitvuln"}) == UNPARTITIONED_DATASETS


@pytest.mark.parametrize("name", sorted(UNPARTITIONED_DATASETS))
def test_an_unpartitioned_dataset_answers_all_only(name: str) -> None:
    assert slice_names_for(name) == ("all",)


async def test_crosscommitvuln_all_loads_every_vendored_task() -> None:
    tasks = await load_split_tasks("crosscommitvuln/all")

    assert len(tasks) == 25
    assert all(task.metadata["repo"] and task.metadata["commit"] for task in tasks)


async def test_lca_bug_loc_all_loads_every_task(monkeypatch: pytest.MonkeyPatch) -> None:
    _lca_from_its_fixture(monkeypatch)

    tasks = await load_split_tasks("lca-bug-loc/all")

    assert [task.record_id for task in tasks] == [
        "thealgorithms/python/295/289",
        "pydantic/pydantic/4212/4189",
        "streamlit/streamlit/501/498",
    ]


@pytest.mark.parametrize(
    "spec", ["lca-bug-loc/dev", "lca-bug-loc/small_test", "crosscommitvuln/test"]
)
async def test_any_other_slice_of_an_unpartitioned_dataset_is_refused_by_name(spec: str) -> None:
    name, _, slice_name = spec.partition("/")

    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks(spec)

    message = str(caught.value)
    assert f"{slice_name!r}" in message and f"{name!r}" in message
    assert "['all']" in message


# --- swe-qa-questions is sliceable, by gold-size bucket ---------------------


def _swe_qa_questions_over_the_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """``swe-qa-questions`` framed over the offline fake source wherever it is built."""
    fake_backed = partial(SweQaQuestionDataset, source=FakeSweQaSource())
    monkeypatch.setitem(dataset_registry._items, "swe-qa-questions", fake_backed)  # type: ignore[arg-type]


@pytest.mark.parametrize("slice_name", ["dev", "test", "small_dev", "small_test", "all"])
async def test_every_swe_qa_questions_slice_loads_with_every_bucket(
    monkeypatch: pytest.MonkeyPatch, slice_name: str
) -> None:
    _swe_qa_questions_over_the_fake(monkeypatch)

    tasks = await load_split_tasks(f"swe-qa-questions/{slice_name}")

    assert {gold_size_bucket(task) for task in tasks} == set(GoldSizeBucket)


async def test_a_swe_qa_questions_slice_is_the_same_on_a_second_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _swe_qa_questions_over_the_fake(monkeypatch)

    first = await load_split_tasks("swe-qa-questions/dev")

    assert [t.task_id for t in first] == [
        t.task_id for t in await load_split_tasks("swe-qa-questions/dev")
    ]


async def test_swe_qa_questions_dev_and_test_partition_the_whole_corpus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _swe_qa_questions_over_the_fake(monkeypatch)

    dev = {t.task_id for t in await load_split_tasks("swe-qa-questions/dev")}
    test = {t.task_id for t in await load_split_tasks("swe-qa-questions/test")}

    assert not dev & test
    assert dev | test == {t.task_id for t in await load_split_tasks("swe-qa-questions/all")}


async def test_a_dataset_with_no_split_field_outside_the_set_names_what_would_load() -> None:
    # swe-bench-verified-loc has no split field and is not a whole-corpus dataset.
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks("swe-bench-verified-loc/all")

    message = str(caught.value)
    assert "'swe-bench-verified-loc'" in message and "'all'" in message
    assert str(sorted(UNPARTITIONED_DATASETS)) in message
    assert "swe-qa-questions" in message
