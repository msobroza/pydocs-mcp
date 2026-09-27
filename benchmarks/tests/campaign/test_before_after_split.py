"""Which slices ``before-after --split`` loads, and how a refused one reads.

The plan must fail with a named ``MeasurementPlanError`` — the error the command
turns into exit 2 — never a traceback, so a slice a dataset refuses is mapped at
the one place both arms load tasks through.
"""

from __future__ import annotations

import pytest

from pydocs_eval.campaign.before_after import MeasurementPlanError
from pydocs_eval.campaign.before_after_split import load_split_tasks


async def test_the_chat_dev_slice_loads_in_dataset_order() -> None:
    tasks = await load_split_tasks("example-needle-chat/dev")

    assert [task.task_id for task in tasks] == [f"example-needle-chat/q{i:02d}" for i in range(10)]


async def test_a_slice_the_chat_dataset_refuses_is_a_plan_error_naming_it() -> None:
    # small_dev passes the global split vocabulary but is not a chat slice.
    with pytest.raises(MeasurementPlanError) as caught:
        await load_split_tasks("example-needle-chat/small_dev")

    assert "small_dev" in str(caught.value)
    assert "example-needle-chat" in str(caught.value)
