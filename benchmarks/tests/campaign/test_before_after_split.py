"""Which slices ``before-after --split`` loads, and how a refused one reads.

The plan must fail with a named ``MeasurementPlanError`` — the error the command
turns into exit 2 — never a traceback, so a slice a dataset refuses is mapped at
the one place both arms load tasks through.
"""

from __future__ import annotations

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_split import load_split_tasks, slice_names_for
from pydocs_eval.datasets._split import VALID_SPLITS
from pydocs_eval.registries import dataset_registry


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
    others = [name for name in dataset_registry.names() if name != "example-needle-chat"]

    assert others, "the registry is populated"
    assert {name: slice_names_for(name) for name in others} == dict.fromkeys(others, VALID_SPLITS)


async def test_reserved_is_still_refused_by_name_on_a_dataset_without_it() -> None:
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks("repoqa-qa/reserved")

    assert "'reserved'" in str(caught.value)
    assert "repoqa-qa" in str(caught.value)
