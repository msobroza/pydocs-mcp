"""WorkingTreeManifestBuilder (spec §6.3 step 1, §6.14 items 1/5/7)."""

from __future__ import annotations

import logging
from pathlib import Path

from pydocs_mcp.application.branch_manifest import (
    BranchManifest,
    BranchManifestBuilder,
    NoBranchManifestBuilder,
    WorkingTreeManifestBuilder,
    branch_display_name,
    project_relative_path,
)
from pydocs_mcp.application.protocols import ExtractionResult
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchIndexSource, FileChangeKind
from tests._fakes import FakeGitRepository


def test_project_relative_path_is_posix_and_symlink_preserving(tmp_path: Path) -> None:
    assert project_relative_path(str(tmp_path / "pkg" / "a.py"), tmp_path) == "pkg/a.py"
    assert project_relative_path("/elsewhere/x.py", tmp_path) == "/elsewhere/x.py"


def test_branch_display_name_rules() -> None:
    assert branch_display_name("feature/x", "a" * 40) == "feature/x"
    assert branch_display_name(None, "8783c8c1234") == "detached-8783c8c"
    assert branch_display_name(None, None) == NON_GIT_BRANCH_NAME


def test_extraction_result_defaults_discovered_paths_to_empty() -> None:
    assert ExtractionResult.__dataclass_fields__["discovered_paths"].default == ()


async def test_builder_uses_index_blobs_and_hashes_only_dirty_files(tmp_path: Path) -> None:
    git = FakeGitRepository(
        branch="main",
        head="b" * 40,
        tracked={"pkg/a.py": "blob-a", "pkg/b.py": "blob-b-old"},
        changes={"pkg/b.py": FileChangeKind.MODIFIED, "pkg/c.py": FileChangeKind.ADDED},
        hashes={"pkg/b.py": "blob-b-new", "pkg/c.py": "blob-c"},
    )
    builder = WorkingTreeManifestBuilder(git_repository_for=lambda root: git, pipeline_hash="p")
    paths = [str(tmp_path / p) for p in ("pkg/a.py", "pkg/b.py", "pkg/c.py")]
    manifest = await builder.build(tmp_path, paths)
    assert isinstance(builder, BranchManifestBuilder)
    assert manifest == BranchManifest(
        name="main",
        head_sha="b" * 40,
        source=BranchIndexSource.WORKING_TREE,
        pipeline_hash="p",
        worktree_path=str(tmp_path),
        files=(
            _file("main", "pkg/a.py", "blob-a"),
            _file("main", "pkg/b.py", "blob-b-new"),
            _file("main", "pkg/c.py", "blob-c"),
        ),
    )
    assert git.hashed_paths == ["pkg/b.py", "pkg/c.py"]  # unchanged tracked files never re-hashed


async def test_builder_without_git_yields_sentinel_branch_and_blank_blobs(tmp_path: Path) -> None:
    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: NullGitRepository(), pipeline_hash="p"
    )
    manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None
    assert manifest.name == NON_GIT_BRANCH_NAME and manifest.head_sha == ""
    assert manifest.files == (_file(NON_GIT_BRANCH_NAME, "a.py", ""),)


async def test_builder_degrades_and_logs_when_git_fails(tmp_path: Path, caplog) -> None:
    git = FakeGitRepository(branch="main", head="b" * 40, fail=True)
    builder = WorkingTreeManifestBuilder(git_repository_for=lambda root: git, pipeline_hash="p")
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None and manifest.name == NON_GIT_BRANCH_NAME
    assert manifest.files[0].blob_sha == ""
    assert "git_manifest_unavailable" in caplog.text


async def test_null_builder_returns_none(tmp_path: Path) -> None:
    assert await NoBranchManifestBuilder().build(tmp_path, []) is None


def _file(branch: str, path: str, blob: str):
    from pydocs_mcp.storage.branch_records import BranchFile

    return BranchFile(branch=branch, path=path, blob_sha=blob)
