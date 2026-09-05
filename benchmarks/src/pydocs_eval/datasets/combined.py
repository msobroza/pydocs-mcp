"""Combined swe-qa-pro + crosscommitvuln dataset (crosscommitvuln design §6.4).

Unions the two member iterators with task_id prefixing (``sweqapro/…``,
``ccv/…``) so ids stay disjoint and the sha256-pinned optimizer split
(``optimize/_split.py``) sees one merged pool. Satisfies the ``Dataset``
Protocol by attribute + method; plugs into the shipped ``skillopt``
optimizer path with zero changes to existing fitness/gate code.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from pathlib import Path

from ..registries import dataset_registry
from .base_dataset import Dataset, EvalTask


@dataset_registry.register("swe-qa-pro+crosscommitvuln")
@dataclass
class CombinedDataset:
    """Union of swe-qa-pro + crosscommitvuln with disjoint prefixed task ids."""

    name: str = "swe-qa-pro+crosscommitvuln"
    revision: str = "1.0"
    # WHY: accepted for registry-build signature parity ONLY —
    # optimize/__main__.py builds every dataset with fixture_path=…; a
    # non-None value raises in _members because the two members read
    # incompatible JSONL shapes (design §6.4).
    fixture_path: Path | None = None
    members: tuple[tuple[str, Dataset], ...] = ()

    def tasks(self) -> AsyncIterator[EvalTask]:
        """Protocol method — a plain ``def`` returning the async generator."""
        return self._iter_tasks()

    async def _iter_tasks(self) -> AsyncIterator[EvalTask]:
        """Round-robin the members so ANY prefix of the stream is representative.

        WHY not member-by-member: every consumer truncates. ``run_agent_track``
        breaks at ``max_tasks`` (default 48) and the optimize fitness cuts on
        budget, so draining swe-qa-pro (~260 tasks) before the first ccv task
        made the smaller member invisible to any truncated run — which still
        reported itself under the combined name. Interleaving costs nothing and
        makes the head of the stream carry every member.

        Members that run out simply drop out; the remainder keep yielding.
        """
        iterators = [(prefix, aiter(ds.tasks())) for prefix, ds in self._members()]
        while iterators:
            for entry in list(iterators):
                prefix, iterator = entry
                try:
                    task = await anext(iterator)
                except StopAsyncIteration:
                    iterators.remove(entry)
                    continue
                yield replace(task, task_id=f"{prefix}/{task.task_id}")

    def _members(self) -> tuple[tuple[str, Dataset], ...]:
        if self.members:  # test injection of fakes
            return self.members
        if self.fixture_path is not None:  # combined fixture dry-run unsupported by design
            raise ValueError(
                "CombinedDataset does not support a top-level fixture_path "
                f"(got {self.fixture_path!r}): its members read incompatible JSONL shapes. "
                "Inject fake members= for tests, or fixture-test each member loader separately."
            )
        return (  # production: each member resolves its own data
            ("sweqapro", dataset_registry.build("swe-qa-pro")),
            ("ccv", dataset_registry.build("crosscommitvuln")),
        )
