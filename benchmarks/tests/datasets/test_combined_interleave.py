"""CombinedDataset must interleave members, not drain them serially (review H3).

Consumers truncate: `run_agent_track` breaks at `cfg.max_tasks` (default 48) and
the optimize fitness cuts on budget. Draining swe-qa-pro (~260 tasks) before the
first ccv task therefore made the smaller member invisible to every truncated
run — while the report still carried the combined name.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydocs_eval.datasets.base_dataset import EvalTask, GoldAnswer
from pydocs_eval.datasets.combined import CombinedDataset


@dataclass
class _Member:
    prefix: str
    count: int
    name: str = "fake"
    revision: str = "1.0"

    def tasks(self):
        return self._iter()

    async def _iter(self):
        for i in range(self.count):
            yield EvalTask(
                task_id=f"{i}",
                query="q",
                gold=GoldAnswer(),
                corpus_source=lambda: None,  # type: ignore[arg-type]
            )


def _combined(big: int = 260, small: int = 12) -> CombinedDataset:
    return CombinedDataset(
        members=(("sweqapro", _Member("sweqapro", big)), ("ccv", _Member("ccv", small)))
    )


def _ids(ds: CombinedDataset) -> list[str]:
    async def collect():
        return [t.task_id async for t in ds.tasks()]

    return asyncio.run(collect())


def test_every_task_is_still_yielded_exactly_once() -> None:
    ids = _ids(_combined())
    assert len(ids) == 272
    assert len(set(ids)) == 272
    assert sum(1 for i in ids if i.startswith("ccv/")) == 12


def test_a_truncated_run_still_sees_the_smaller_member() -> None:
    """The regression: max_tasks=48 admitted zero ccv tasks."""
    ids = _ids(_combined())
    for cutoff in (2, 12, 48):
        head = ids[:cutoff]
        assert any(i.startswith("ccv/") for i in head), f"no ccv task in the first {cutoff}"


def test_the_smaller_member_appears_within_the_first_positions() -> None:
    """Round-robin: both members are represented from the very start."""
    assert {i.split("/")[0] for i in _ids(_combined())[:2]} == {"sweqapro", "ccv"}


def test_exhausted_members_do_not_stall_the_others() -> None:
    """The small member runs out first; the rest must still be yielded."""
    ids = _ids(_combined(big=10, small=2))
    assert len(ids) == 12
    assert ids[-1].startswith("sweqapro/")


def test_prefixes_are_still_applied() -> None:
    assert all("/" in i for i in _ids(_combined(big=3, small=3)))
