"""The served row's name and head (#317): what the refresh queue compares a
ref-driven working-tree job against before it runs the pass."""

from __future__ import annotations

from pydocs_mcp.application.served_branch_head import ServedBranchHead, read_served_branch_head
from pydocs_mcp.models import BranchIndexSource
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import InMemoryBranchStore, make_fake_uow_factory

A, B = "a" * 40, "b" * 40


def _row(name: str, head: str, *, is_default: bool) -> BranchRecord:
    source = BranchIndexSource.WORKING_TREE if is_default else BranchIndexSource.GIT_OBJECTS
    return BranchRecord(name, head, source, "p", 1.0, 1.0, is_default=is_default)


async def test_the_served_row_is_the_working_tree_stamp_not_a_git_objects_row() -> None:
    branches = InMemoryBranchStore()
    branches.records.update(
        {
            "feature/x": _row("feature/x", B, is_default=False),
            "main": _row("main", A, is_default=True),
        }
    )
    served = await read_served_branch_head(make_fake_uow_factory(branches=branches))
    assert served == ServedBranchHead("main", A)


async def test_a_bundle_with_no_served_row_reads_none() -> None:
    branches = InMemoryBranchStore()
    branches.records["feature/x"] = _row("feature/x", B, is_default=False)
    assert await read_served_branch_head(make_fake_uow_factory(branches=branches)) is None
