"""#316 on real repositories: squash, merge-commit and rebase-merge landings are
detected through the subprocess adapter; a deleted branch's rows outlive the
grace window by exactly its length; the landing stream covers only new
landings after the first run; a checked-out branch is never retired.

The bundle's branch rows are seeded the way the branch pass (plan Task 11)
will write them — a ``git_objects`` row per indexed branch beside the
checked-out working-tree row — and maintenance runs through the same
composition root ``pydocs-mcp index`` uses (``build_branch_maintenance``).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from pydocs_mcp.__main__ import main as _cli_main
from pydocs_mcp.application.branch_maintenance import BranchMaintenance, MaintenanceReport
from pydocs_mcp.application.branch_policy import resolve_base_branch
from pydocs_mcp.application.branch_retirement import RetirementPolicy
from pydocs_mcp.db import cache_path_for_project, open_index_database
from pydocs_mcp.git.subprocess_repository import SubprocessGitRepository
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchStatus,
    Chunk,
    LandingKind,
    LandingStep,
    MergeEvidence,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.retrieval.config.git_models import GitConfig
from pydocs_mcp.storage.branch_records import BranchFile, BranchRecord, ChunkMembership
from pydocs_mcp.storage.factories import build_branch_maintenance, build_sqlite_uow_factory
from tests._git_sandbox import commit_text, isolate_git_config, requires_git, run_git

pytestmark = requires_git

DAY = 86400.0
T0 = 1_800_000_000.0


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    isolate_git_config(monkeypatch, tmp_path / "home")


def _sha(root: Path, ref: str) -> str:
    return run_git(root, "rev-parse", ref)


def _land_branches(root: Path) -> None:
    """``main`` lands ``feature/s`` by squash, ``feature/m`` by a merge commit
    and ``feature/r`` by a rebase (cherry-picks); ``open`` never lands."""
    run_git(root, "checkout", "-q", "-b", "feature/s")
    commit_text(root, "s1.py", "s = 1\n", "s1")
    commit_text(root, "s2.py", "s = 2\n", "s2")
    run_git(root, "checkout", "-q", "main")
    run_git(root, "merge", "--squash", "-q", "feature/s")
    run_git(root, "commit", "-q", "-m", "feature/s (#1)")
    run_git(root, "checkout", "-q", "-b", "feature/m")
    commit_text(root, "m.py", "m = 1\n", "m1")
    run_git(root, "checkout", "-q", "main")
    run_git(root, "merge", "--no-ff", "-q", "-m", "merge feature/m", "feature/m")
    run_git(root, "checkout", "-q", "-b", "feature/r")
    commit_text(root, "r1.py", "r = 1\n", "r1")
    commit_text(root, "r2.py", "r = 2\n", "r2")
    run_git(root, "checkout", "-q", "main")
    # The base moves first, so the rebased copies get new parents (and shas):
    # picked onto the fork point itself they would BE the branch's commits.
    commit_text(root, "hotfix.py", "h = 1\n", "hotfix")
    run_git(root, "cherry-pick", "feature/r~1", "feature/r")
    run_git(root, "checkout", "-q", "-b", "open")
    commit_text(root, "o.py", "o = 1\n", "o1")
    run_git(root, "checkout", "-q", "main")
    run_git(root, "branch", "fresh", "main~1")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    run_git(root, "init", "-q", "-b", "main")
    commit_text(root, "a.py", "a = 1\n", "one")
    commit_text(root, "a.py", "a = 2\n", "two")
    _land_branches(root)
    return root


def _db(tmp_path: Path, root: Path) -> Path:
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    db = cache / cache_path_for_project(root).name
    open_index_database(db).close()
    return db


def _branch_row(root: Path, name: str) -> BranchRecord:
    return BranchRecord(name, _sha(root, name), BranchIndexSource.GIT_OBJECTS, "p", T0, T0)


async def _seed(db: Path, root: Path, *names: str) -> None:
    """The checked-out ``main`` row plus one row, one file and one chunk per name."""
    async with build_sqlite_uow_factory(db)() as uow:
        await uow.branches.upsert_branch(
            BranchRecord(
                "main",
                _sha(root, "main"),
                BranchIndexSource.WORKING_TREE,
                "p",
                T0,
                T0,
                is_default=True,
                worktree_path=str(root),
            )
        )
        for name in names:
            await uow.branches.upsert_branch(_branch_row(root, name))
            chunk = Chunk.from_test_inputs(
                package=PROJECT_PACKAGE_NAME, module=name, title=name, text=name
            )
            (chunk_id,) = await uow.chunks.insert_returning_ids((chunk,))
            await uow.branch_chunks.replace_membership(
                name, [ChunkMembership(name, chunk_id, "x.py")]
            )
            await uow.branches.replace_files(name, [BranchFile(name, "x.py", "blob")])
        await uow.commit()


async def _row(db: Path, name: str) -> BranchRecord | None:
    async with build_sqlite_uow_factory(db)() as uow:
        return await uow.branches.get_branch(name)


async def _owned_rows(db: Path, name: str) -> int:
    async with build_sqlite_uow_factory(db)() as uow:
        return await uow.branch_chunks.count_for_branch(name) + await uow.branches.count_files(name)


_LANDED = ("feature/s", "feature/m", "feature/r", "open", "fresh")


async def test_every_landing_shape_is_detected_with_its_landing_sha(
    tmp_path: Path, repo: Path
) -> None:
    db = _db(tmp_path, repo)
    await _seed(db, repo, *_LANDED)
    report = await build_branch_maintenance(AppConfig.load(), db, repo).run(now=T0)
    assert sorted(report.merged) == ["feature/m", "feature/r", "feature/s"]
    squash = await _row(db, "feature/s")
    # AC: the squash is found by patch id with the squash commit as its landing,
    # and (O18) merged_into keeps the base NAME beside the landing sha.
    assert squash.status is BranchStatus.MERGED and squash.merged_into == "main"
    assert squash.merge_evidence is MergeEvidence.PATCH_ID_MATCH
    assert squash.landing_sha == _sha(repo, "main~4")
    merged = await _row(db, "feature/m")
    assert merged.merge_evidence is MergeEvidence.ANCESTOR
    assert merged.landing_sha == _sha(repo, "main~3")
    rebased = await _row(db, "feature/r")
    assert rebased.merge_evidence is MergeEvidence.REBASE_PATCH_ID_MATCH
    assert rebased.landing_sha == _sha(repo, "main")
    snapshot = await _row(db, rebased.landing_sha)
    assert snapshot.landing_kind is LandingKind.LINEAR_SNAPSHOT
    assert snapshot.merge_base_sha == _sha(repo, "main~2")  # the hotfix just before r1'
    unit = await _row(db, squash.landing_sha)
    assert unit.landing_kind is LandingKind.SINGLE_COMMIT and unit.merged_into is None
    # Never landed, and cut from the base without a commit of its own (#316 c).
    assert (await _row(db, "open")).status is BranchStatus.ACTIVE
    assert (await _row(db, "fresh")).status is BranchStatus.ACTIVE
    assert (await _row(db, "main")).status is BranchStatus.ACTIVE  # the base itself (#316 a)


async def test_a_squash_merged_branch_deleted_before_the_pass_retires_as_merged(
    tmp_path: Path, repo: Path
) -> None:
    """The usual flow: the PR is squash-merged, then ``git branch -D`` runs before
    the next pass. The stored head is still in the object database, so the row
    retires MERGED with its landing — not DELETED — and the unit exists."""
    db = _db(tmp_path, repo)
    await _seed(db, repo, "feature/s")
    run_git(repo, "branch", "-D", "feature/s")
    report = await build_branch_maintenance(AppConfig.load(), db, repo).run(now=T0)
    assert report == MaintenanceReport(merged=("feature/s",))
    squash = await _row(db, "feature/s")
    assert squash.status is BranchStatus.MERGED and squash.merged_into == "main"
    assert squash.landing_sha == _sha(repo, "main~4")
    assert (await _row(db, squash.landing_sha)).landing_kind is LandingKind.SINGLE_COMMIT


def test_the_listing_shows_the_base_and_the_short_landing_sha(
    tmp_path: Path, repo: Path, capsys, monkeypatch
) -> None:
    # Synchronous: the CLI runs its own event loop.
    db = _db(tmp_path, repo)
    asyncio.run(_seed(db, repo, "feature/s"))
    asyncio.run(build_branch_maintenance(AppConfig.load(), db, repo).run(now=T0))
    monkeypatch.setattr(
        "sys.argv", ["pydocs-mcp", "branches", str(repo), "--cache-dir", str(db.parent)]
    )
    assert _cli_main() == 0
    out = capsys.readouterr().out
    short = _sha(repo, "main~4")[:7]
    assert f"merged into main @{short}" in out
    assert _sha(repo, "main~4") not in out  # the landing unit row is not a branch line


async def test_a_deleted_branch_keeps_its_rows_for_the_grace_window_only(
    tmp_path: Path, repo: Path
) -> None:
    db = _db(tmp_path, repo)
    await _seed(db, repo, "open")
    run_git(repo, "branch", "-D", "open")
    maintenance = build_branch_maintenance(AppConfig.load(), db, repo)
    assert await maintenance.run(now=T0) == MaintenanceReport(deleted=("open",))
    gone = await _row(db, "open")
    assert gone.status is BranchStatus.DELETED and gone.purge_after == T0 + 7 * DAY
    assert await maintenance.run(now=T0 + 7 * DAY - 1) == MaintenanceReport()
    assert await _owned_rows(db, "open") == 2  # inside the window: every row survives
    assert await maintenance.run(now=T0 + 7 * DAY) == MaintenanceReport(purged=("open",))
    assert await _owned_rows(db, "open") == 0
    assert (await _row(db, "open")).status is BranchStatus.DELETED  # the tombstone stays


@dataclass
class LandingStreamCounter:
    """The real adapter, with every ``first_parent_landings`` (patch-id) call recorded."""

    inner: SubprocessGitRepository
    streamed: list[tuple[str, int]] = field(default_factory=list)

    def first_parent_landings(
        self, base_tip: str, *, max_count: int, stop_at: str | None = None
    ) -> tuple[LandingStep, ...]:
        self.streamed.append((base_tip, max_count))
        return self.inner.first_parent_landings(base_tip, max_count=max_count, stop_at=stop_at)

    def __getattr__(self, name: str) -> object:
        return getattr(self.inner, name)


async def test_detection_streams_the_lookback_once_then_only_new_landings(
    tmp_path: Path, repo: Path
) -> None:
    db = _db(tmp_path, repo)
    await _seed(db, repo, "open")  # a live candidate that never lands
    git = LandingStreamCounter(SubprocessGitRepository(project_root=repo))
    maintenance = BranchMaintenance(
        git=git,  # type: ignore[arg-type]
        uow_factory=build_sqlite_uow_factory(db),
        base_resolver=lambda g: resolve_base_branch(g, GitConfig()),
        policy=RetirementPolicy(7, True, True),
        lookback=200,
    )
    await maintenance.run(now=T0)
    first_parent_steps = len(run_git(repo, "rev-list", "--first-parent", "main").split())
    assert git.streamed == [(_sha(repo, "main"), first_parent_steps)]
    await maintenance.run(now=T0 + 1)
    assert len(git.streamed) == 1  # every landing id was cached: no patch-id work at all
    commit_text(repo, "late.py", "late = 1\n", "late landing")
    await maintenance.run(now=T0 + 2)
    assert git.streamed[1:] == [(_sha(repo, "main"), 1)]


async def test_a_branch_checked_out_in_a_worktree_is_retired_only_once_released(
    tmp_path: Path, repo: Path
) -> None:
    db = _db(tmp_path, repo)
    await _seed(db, repo, "feature/s")
    worktree = tmp_path / "wt"
    run_git(repo, "worktree", "add", "-q", str(worktree), "feature/s")
    maintenance = build_branch_maintenance(AppConfig.load(), db, repo)
    assert await maintenance.run(now=T0) == MaintenanceReport()
    run_git(repo, "worktree", "remove", str(worktree))
    assert await maintenance.run(now=T0 + 1) == MaintenanceReport(merged=("feature/s",))
