"""Branch-dimension vocabulary + records (spec §6.1, §6.14 item 4)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import (
    NON_GIT_BRANCH_NAME,
    BranchIndexSource,
    BranchSlice,
    BranchStatus,
    FileChangeKind,
)
from pydocs_mcp.storage.branch_records import (
    BranchFile,
    BranchRecord,
    ChunkMembership,
    FileExtraction,
)


def test_branch_vocabularies_are_str_enums_with_lowercase_values() -> None:
    assert BranchStatus.ACTIVE == "active"
    assert {s.value for s in BranchStatus} == {"active", "inactive", "merged", "deleted"}
    assert {s.value for s in BranchIndexSource} == {"working_tree", "git_objects"}
    assert {s.value for s in BranchSlice} == {"tree", "diff"}
    assert {k.value for k in FileChangeKind} == {
        "unchanged",
        "added",
        "modified",
        "renamed",
        "deleted",
    }


def test_non_git_sentinel_cannot_be_a_git_ref_name() -> None:
    # git check-ref-format forbids spaces, so no real branch can collide.
    assert " " in NON_GIT_BRANCH_NAME


def test_records_are_frozen_and_carry_defaults() -> None:
    rec = BranchRecord(
        name="main",
        head_sha="abc1234",
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        indexed_at=1.0,
        last_used_at=1.0,
    )
    assert rec.status is BranchStatus.ACTIVE and rec.is_default is False
    with pytest.raises(FrozenInstanceError):
        rec.name = "other"  # type: ignore[misc]
    bf = BranchFile(branch="main", path="pkg/a.py", blob_sha="b1")
    assert bf.change_kind is FileChangeKind.UNCHANGED
    cm = ChunkMembership(branch="main", chunk_id=7, source_path="pkg/a.py")
    assert cm.slice is BranchSlice.TREE and cm.changed is False
    fe = FileExtraction(
        blob_sha="b1",
        path="pkg/a.py",
        pipeline_hash="p",
        chunk_spans="[[7, 1, 3]]",
        created_at=1.0,
    )
    assert fe.tree_json is None


def test_git_command_error_carries_argv_reason_and_stderr() -> None:
    err = GitCommandError(("git", "-C", "/p", "status"), "timeout after 30s", "fatal: x")
    assert isinstance(err, PydocsMCPError) and isinstance(err, RuntimeError)
    assert "status" in str(err) and "timeout after 30s" in str(err) and "fatal: x" in str(err)
    assert err.argv == ("git", "-C", "/p", "status")
