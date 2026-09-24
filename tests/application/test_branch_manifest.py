"""WorkingTreeManifestBuilder (spec §6.3 step 1, §6.14 items 1/5/7)."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

from pydocs_mcp.application.branch_manifest import (
    BranchManifest,
    BranchManifestBuilder,
    NoBranchManifestBuilder,
    WorkingTreeManifestBuilder,
    branch_display_name,
    project_relative_path,
)
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.extraction_cache import file_extraction_cache_key
from pydocs_mcp.application.protocols import ExtractionResult, GitRepository
from pydocs_mcp.extraction.config import ChunkingConfig, TextSectionConfig
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.null_repository import NullGitRepository
from pydocs_mcp.models import NON_GIT_BRANCH_NAME, BranchIndexSource, FileChangeKind
from pydocs_mcp.retrieval.config import ReferenceCaptureConfig
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
        extraction_cache_key=builder.current_extraction_cache_key(),
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


HEAD, TIP, MB = "b" * 40, "d" * 40, "e" * 40


def _base_git(**kw) -> FakeGitRepository:
    return FakeGitRepository(branch="feature/x", head=HEAD, tracked={"a.py": "blob-a"}, **kw)


def _main_base(git: GitRepository) -> BaseBranch | None:
    return BaseBranch("main", TIP, "refs/remotes/origin/main")


async def test_builder_stamps_the_resolved_base_and_its_merge_base(tmp_path: Path) -> None:
    git = _base_git(merge_bases={frozenset((TIP, HEAD)): MB})
    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: git, pipeline_hash="p", base_resolver=_main_base
    )
    manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None
    assert (manifest.base_name, manifest.merge_base_sha, manifest.base_tip_sha) == (
        "main",
        MB,
        TIP,
    )
    assert manifest.files[0].blob_sha == "blob-a"


async def test_an_orphan_branch_stamps_an_empty_merge_base(tmp_path: Path) -> None:
    # Spec §6.5: no common ancestor with the base stores merge_base_sha = "".
    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: _base_git(), pipeline_hash="p", base_resolver=_main_base
    )
    manifest = await builder.build(tmp_path, [])
    assert manifest is not None
    assert (manifest.base_name, manifest.merge_base_sha) == ("main", "")


async def test_the_default_resolver_stamps_no_base(tmp_path: Path) -> None:
    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: _base_git(), pipeline_hash="p"
    )
    manifest = await builder.build(tmp_path, [])
    assert manifest is not None
    assert (manifest.base_name, manifest.merge_base_sha, manifest.base_tip_sha) == (
        None,
        None,
        None,
    )


async def test_a_root_without_a_head_commit_never_resolves_a_base(tmp_path: Path) -> None:
    # No git (or git off, or an unborn branch): nothing to anchor, so the
    # resolver is not asked — it would only log "no base" on every pass.
    calls: list[GitRepository] = []

    def _spy(git: GitRepository) -> BaseBranch | None:
        calls.append(git)
        return _main_base(git)

    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: NullGitRepository(), pipeline_hash="p", base_resolver=_spy
    )
    manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None and manifest.base_name is None and calls == []


async def test_a_git_failure_while_resolving_the_base_drops_only_the_base(
    tmp_path: Path, caplog
) -> None:
    # A merge-base timeout must not degrade the whole manifest to the non-git
    # sentinel: that would retire the real branch row and drop the cache keys.
    def _failing(git: GitRepository) -> BaseBranch | None:
        raise GitCommandError(("git", "merge-base"), "timed out")

    builder = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: _base_git(), pipeline_hash="p", base_resolver=_failing
    )
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        manifest = await builder.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None
    assert (manifest.name, manifest.head_sha, manifest.files[0].blob_sha) == (
        "feature/x",
        HEAD,
        "blob-a",
    )
    assert manifest.base_name is None and manifest.merge_base_sha is None
    (event,) = (json.loads(r.getMessage()) for r in caplog.records)
    # One event, naming the repository as ``git_manifest_unavailable`` does.
    assert event["event"] == "base_branch_unavailable"
    assert event["root"] == str(tmp_path)
    assert "timed out" in event["error"]


async def test_null_builder_returns_none(tmp_path: Path) -> None:
    assert await NoBranchManifestBuilder().build(tmp_path, []) is None


async def test_the_manifest_carries_the_extraction_key_of_the_builder_s_settings(
    tmp_path: Path,
) -> None:
    """#309: every manifest names the key its cache rows go under — the
    pipeline hash plus the per-file extraction identity of its settings."""
    stock = WorkingTreeManifestBuilder(
        git_repository_for=lambda root: _base_git(), pipeline_hash="p"
    )
    tuned = replace(stock, chunking=ChunkingConfig(text_section=TextSectionConfig(window_lines=7)))
    manifest = await stock.build(tmp_path, [str(tmp_path / "a.py")])
    assert manifest is not None
    assert manifest.extraction_cache_key == file_extraction_cache_key(
        "p", chunking=ChunkingConfig(), reference_capture=ReferenceCaptureConfig()
    )
    assert tuned.current_extraction_cache_key() != manifest.extraction_cache_key
    no_git = replace(stock, git_repository_for=lambda root: NullGitRepository())
    assert (await no_git.build(tmp_path, [])).extraction_cache_key == manifest.extraction_cache_key


def _file(branch: str, path: str, blob: str):
    from pydocs_mcp.storage.branch_records import BranchFile

    return BranchFile(branch=branch, path=path, blob_sha=blob)
