"""The served row's name and head (#317): what the working-tree pass last stamped.

The refresh queue compares a ref-driven working-tree job with it before running
the pass. Under ``serve --watch`` a checkout's file events end their quiet
period first, so their job has already indexed the new branch when the ref
watcher's job for it arrives: spec §6.8c's burst table counts that checkout as
one pass. At start-up it answers the other race: a checkout made while the
startup pass ran.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.storage.protocols import UnitOfWork


@dataclass(frozen=True, slots=True)
class ServedBranchHead:
    """The served (default) branch row: the working tree's own stamp."""

    name: str
    head_sha: str


async def read_served_branch_head(
    uow_factory: Callable[[], UnitOfWork],
) -> ServedBranchHead | None:
    """The default row's name and head; ``None`` before any working-tree stamp."""
    async with uow_factory() as uow:
        name = await uow.branches.default_branch_name()
        record = None if name is None else await uow.branches.get_branch(name)
    return None if record is None else ServedBranchHead(record.name, record.head_sha)


__all__ = ("ServedBranchHead", "read_served_branch_head")
