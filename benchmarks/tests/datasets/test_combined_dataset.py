"""CombinedDataset tests — hermetic via injected fake member datasets (no
network, no git; the production member wiring is exercised only up to the
registry build, which constructs but never iterates the members)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_eval.datasets.base_dataset import Dataset, EvalTask, GoldAnswer
from pydocs_eval.datasets.combined import CombinedDataset
from pydocs_eval.optimize._split import partition_task_ids, task_split
from pydocs_eval.registries import dataset_registry


def _make_member_task(raw_id: str) -> EvalTask:
    return EvalTask(
        task_id=raw_id,
        query=f"q-{raw_id}",
        gold=GoldAnswer(),
        corpus_source=lambda: Path(),  # never invoked here — tasks are read for ids only
    )


@dataclass
class _FakeMemberDataset:
    """Stand-in member — yields tasks with caller-chosen (colliding) raw ids."""

    name: str
    raw_ids: tuple[str, ...]
    revision: str = "0"

    async def tasks(self) -> AsyncIterator[EvalTask]:
        for raw_id in self.raw_ids:
            yield _make_member_task(raw_id)


# The SAME raw ids in both members — prefixing must keep them disjoint.
_COLLIDING_IDS = tuple(f"t-{i:03d}" for i in range(16))


def _combined_with_fake_members() -> CombinedDataset:
    return CombinedDataset(
        members=(
            ("sweqapro", _FakeMemberDataset(name="swe-qa-pro", raw_ids=_COLLIDING_IDS)),
            ("ccv", _FakeMemberDataset(name="crosscommitvuln", raw_ids=_COLLIDING_IDS)),
        )
    )


async def test_satisfies_dataset_protocol() -> None:
    assert isinstance(CombinedDataset(), Dataset)


def test_registered_under_the_plus_name() -> None:
    ds = dataset_registry.build("swe-qa-pro+crosscommitvuln")
    assert isinstance(ds, CombinedDataset)
    assert ds.name == "swe-qa-pro+crosscommitvuln"
    assert ds.revision == "1.0"


async def test_colliding_member_ids_are_prefixed_disjoint_and_all_present() -> None:
    tasks = [t async for t in _combined_with_fake_members().tasks()]
    ids = [t.task_id for t in tasks]
    assert len(ids) == 2 * len(_COLLIDING_IDS)
    assert len(set(ids)) == len(ids)  # prefixing keeps colliding raw ids unique
    assert {i.split("/", 1)[0] for i in ids} == {"sweqapro", "ccv"}
    assert "sweqapro/t-000" in ids and "ccv/t-000" in ids  # both members' tasks appear


async def test_non_none_fixture_path_raises_naming_value_and_members() -> None:
    ds = CombinedDataset(fixture_path=Path("/tmp/combined.jsonl"))
    with pytest.raises(ValueError) as excinfo:
        _ = [t async for t in ds.tasks()]
    message = str(excinfo.value)
    assert "combined.jsonl" in message  # names the offending value
    assert "members=" in message  # points at the test seam


async def test_prefixed_ids_split_non_empty_on_both_sides() -> None:
    ids = [t.task_id async for t in _combined_with_fake_members().tasks()]
    train, holdout = partition_task_ids(ids)  # raises loudly if a side is empty
    assert train and holdout
    # Every id lands on exactly one pinned side (sha256 % 2 determinism).
    assert {task_split(i) for i in ids} == {"train", "holdout"}
