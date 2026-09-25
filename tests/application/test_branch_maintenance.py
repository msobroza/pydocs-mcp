"""The maintenance driver (spec §6.8a, §6.5 start-up re-check; #316): detection,
deletion retirement and the grace purge in one transaction, over fakes, plus
the atomicity case on a real SQLite bundle."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_maintenance import (
    BranchMaintenance,
    BranchVerbRunner,
    MaintenanceReport,
    NullBranchMaintenance,
)
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.branch_retirement import BranchVerb, BranchVerbError, RetirementPolicy
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.db import open_index_database
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import (
    PROJECT_PACKAGE_NAME,
    BranchIndexSource,
    BranchStatus,
    Chunk,
    LandingStep,
    MergeEvidence,
)
from pydocs_mcp.storage.branch_records import BranchRecord, ChunkMembership
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from tests._fakes import FakeGitRepository, make_fake_uow_factory

TIP, SQUASH, MB, FEATURE, LANDED = "a" * 40, "b" * 40, "c" * 40, "d" * 40, "e" * 40
DAY = 86400.0
POLICY = RetirementPolicy(grace_days=7, auto_retire_merged=True, auto_retire_deleted=True)
BASE = BaseBranch("main", TIP, None)


@dataclass
class RecordingBaseResolver:
    """``resolve_base_branch`` stand-in that counts how often it was asked."""

    base: BaseBranch | None = BASE
    calls: int = 0

    def __call__(self, git: GitRepository) -> BaseBranch | None:
        self.calls += 1
        return self.base


@dataclass
class RecordingFulltextRebuild:
    calls: int = 0

    async def __call__(self) -> None:
        self.calls += 1


@dataclass
class PatchIdFailsGitRepository(FakeGitRepository):
    """A git that fails half way through detection — after the landing stream,
    and after the verdict of every candidate before the failing head."""

    failing_head: str = FEATURE
    failures: list[str] = field(default_factory=list)

    def patch_id(self, base_sha: str, ref: str) -> str:
        if ref != self.failing_head:
            return super().patch_id(base_sha, ref)
        self.failures.append(ref)
        raise GitCommandError(("git", "diff"), "exit 128", "fatal: simulated")


def _row(name: str, **overrides: object) -> BranchRecord:
    return BranchRecord(name, FEATURE, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **overrides)


def _checked_out_main() -> BranchRecord:
    return BranchRecord(
        "main",
        TIP,
        BranchIndexSource.WORKING_TREE,
        "p",
        1.0,
        1.0,
        is_default=True,
        worktree_path="/repo",
    )


def _squash_git(
    cls: type[FakeGitRepository] = FakeGitRepository, **extra: object
) -> FakeGitRepository:
    return cls(
        refs={"refs/heads/main": TIP, "refs/heads/feature/x": FEATURE},
        worktrees=(("/repo", "main"),),
        landings=(
            LandingStep(TIP, (SQUASH,), 2.0, "later", "p-later"),
            LandingStep(SQUASH, (MB,), 1.0, "feature (#1)", "pid-f"),
        ),
        merge_bases={frozenset((TIP, FEATURE)): MB},
        patch_ids={(MB, FEATURE): "pid-f"},
        **extra,
    )


def _maintenance(git, factory, **overrides) -> BranchMaintenance:
    fields = {
        "git": git,
        "uow_factory": factory,
        "base_resolver": RecordingBaseResolver(),
        "policy": POLICY,
        "lookback": 200,
        "rebuild_fulltext_index": RecordingFulltextRebuild(),
    }
    return BranchMaintenance(**(fields | overrides))


async def _seed(factory, *records: BranchRecord) -> None:
    async with factory() as uow:
        for record in records:
            await uow.branches.upsert_branch(record)
        await uow.commit()


async def test_a_squash_merged_branch_is_retired_with_its_landing() -> None:
    factory = make_fake_uow_factory()
    await _seed(factory, _checked_out_main(), _row("feature/x"))
    report = await _maintenance(_squash_git(), factory).run(now=100.0)
    assert report == MaintenanceReport(merged=("feature/x",))
    async with factory() as uow:
        row = await uow.branches.get_branch("feature/x")
        main = await uow.branches.get_branch("main")
    assert (row.status, row.merged_into, row.landing_sha) == (BranchStatus.MERGED, "main", SQUASH)
    assert row.merge_evidence is MergeEvidence.PATCH_ID_MATCH
    assert main == _checked_out_main()


async def test_a_single_checked_out_branch_spawns_no_landing_work() -> None:
    """Byte identity for a one-branch bundle: nothing to detect means no base
    resolution (no second ``base_branch_unresolved`` log), no landing stream,
    no patch-id cache rows."""
    factory = make_fake_uow_factory()
    await _seed(factory, _checked_out_main())
    git, resolver = _squash_git(), RecordingBaseResolver()
    report = await _maintenance(git, factory, base_resolver=resolver).run(now=1.0)
    assert report == MaintenanceReport()
    assert (resolver.calls, git.step_probe_calls, git.landing_calls) == (0, [], [])
    async with factory() as uow:
        assert await uow.branches.landing_patch_ids([TIP, SQUASH]) == {}


async def test_a_branch_checked_out_in_another_worktree_is_protected() -> None:
    """#316 safety (b): neither merged nor deleted while a worktree holds it."""
    factory = make_fake_uow_factory()
    # "unborn" is checked out but has no ref yet.
    await _seed(factory, _checked_out_main(), _row("feature/x"), _row("unborn"))
    git = _squash_git()
    git.worktrees = (("/repo", "main"), ("/wt", "feature/x"), ("/wt2", "unborn"))
    assert await _maintenance(git, factory).run(now=1.0) == MaintenanceReport()
    assert git.landing_calls == []


async def test_a_tracked_remote_ref_is_never_retired_as_merged_or_deleted() -> None:
    """Spec §6.8b layer 2 (#318): a ``track_refs`` entry has no local ref, so
    the deleted-ref retirement would read it as gone, and its commits land on
    the base like any branch's. Listed in ``track_refs`` it stays indexed."""

    async def run(tracked_remote_refs: frozenset[str]) -> MaintenanceReport:
        factory = make_fake_uow_factory()
        # origin/gone's head was gc'd: only the deleted-ref retirement moves it.
        gone = replace(_row("origin/gone"), head_sha=LANDED)
        await _seed(factory, _checked_out_main(), _row("origin/feature/x"), gone)
        git = _squash_git(objects={FEATURE})  # the squashed head, reachable by sha only
        maintenance = _maintenance(git, factory, tracked_remote_refs=tracked_remote_refs)
        return await maintenance.run(now=1.0)

    assert await run(frozenset()) == MaintenanceReport(
        merged=("origin/feature/x",), deleted=("origin/gone",)
    )
    assert await run(frozenset({"origin/feature/x", "origin/gone"})) == MaintenanceReport()


async def test_no_base_means_no_detection_but_deleted_refs_still_retire() -> None:
    factory = make_fake_uow_factory()
    await _seed(factory, _checked_out_main(), _row("feature/x"), _row("gone"))
    git = _squash_git()
    maintenance = _maintenance(git, factory, base_resolver=RecordingBaseResolver(base=None))
    assert await maintenance.run(now=1.0) == MaintenanceReport(deleted=("gone",))
    assert git.landing_calls == []


def _two_candidate_git() -> PatchIdFailsGitRepository:
    """``feature/a`` (head ``LANDED``) matches the squash and is judged first;
    ``feature/x`` then fails."""
    git = _squash_git(PatchIdFailsGitRepository)
    assert isinstance(git, PatchIdFailsGitRepository)
    git.refs["refs/heads/feature/a"] = LANDED
    git.merge_bases[frozenset((TIP, LANDED))] = MB
    git.patch_ids[(MB, LANDED)] = "pid-f"
    return git


async def _seed_detection_failure_bundle(factory) -> None:
    await _seed(
        factory,
        _checked_out_main(),
        replace(_row("feature/a"), head_sha=LANDED),
        _row("feature/x"),
        # Ref gone, head gc'd: only the deleted-ref retirement would move it.
        _row("gone"),
    )


def _maintenance_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    messages = (r.getMessage() for r in caplog.records)
    return [json.loads(m) for m in messages if "branch_maintenance" in m]


async def test_a_git_error_mid_detection_leaves_every_row_untouched(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#316 safety (f): the verdict already computed for ``feature/a`` is dropped
    with the rest, the deleted-ref retirement waits for the verdicts (a gone ref
    may have landed), and one structured event is logged."""
    db = tmp_path / "b.db"
    open_index_database(db).close()
    factory = build_sqlite_uow_factory(db)
    await _seed_detection_failure_bundle(factory)
    git = _two_candidate_git()
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        assert await _maintenance(git, factory).run(now=1.0) == MaintenanceReport()
    assert git.failures == [FEATURE]
    async with factory() as uow:
        rows = await uow.branches.list_branches()
    assert {r.name: (r.status, r.merge_evidence) for r in rows} == dict.fromkeys(
        ("main", "feature/a", "feature/x", "gone"), (BranchStatus.ACTIVE, None)
    )
    events = _maintenance_events(caplog)
    assert [e["event"] for e in events] == ["branch_maintenance_skipped"]
    assert "simulated" in str(events[0]["error"])


async def test_a_detection_error_keeps_the_landing_cache_so_no_pass_re_streams_it(
    tmp_path: Path,
) -> None:
    """A landing's patch id is immutable per sha: the cache commits before any
    verdict, so a recurring detection error never re-pays the lookback stream."""
    db = tmp_path / "b.db"
    open_index_database(db).close()
    factory = build_sqlite_uow_factory(db)
    await _seed_detection_failure_bundle(factory)
    git = _two_candidate_git()
    await _maintenance(git, factory).run(now=1.0)
    await _maintenance(git, factory).run(now=2.0)
    assert git.failures == [FEATURE, FEATURE] and len(git.landing_calls) == 1
    async with factory() as uow:
        cached = await uow.branches.landing_patch_ids([TIP, SQUASH])
    assert cached == {TIP: "p-later", SQUASH: "pid-f"}


async def _seed_due_deleted_branch(factory) -> None:
    async with factory() as uow:
        await uow.branches.upsert_branch(_checked_out_main())
        await uow.branches.upsert_branch(
            _row("gone", status=BranchStatus.DELETED, retired_at=1.0, purge_after=2.0)
        )
        (chunk_id,) = await uow.chunks.insert_returning_ids(
            (Chunk.from_test_inputs(package=PROJECT_PACKAGE_NAME, module="m", title="t", text="t"),)
        )
        await uow.branch_chunks.replace_membership(
            "gone", [ChunkMembership("gone", chunk_id, "a.py")]
        )
        await uow.commit()


async def test_a_detection_error_does_not_starve_the_grace_purge() -> None:
    """The purge needs no git: rows already past their window go even while
    detection keeps failing."""
    factory = make_fake_uow_factory()
    await _seed_due_deleted_branch(factory)
    await _seed(factory, _row("feature/x"))
    rebuild = RecordingFulltextRebuild()
    git = _squash_git(PatchIdFailsGitRepository)
    maintenance = _maintenance(git, factory, rebuild_fulltext_index=rebuild)
    assert await maintenance.run(now=3.0) == MaintenanceReport(purged=("gone",))
    assert git.failures == [FEATURE] and rebuild.calls == 1
    async with factory() as uow:
        assert (await uow.branches.get_branch("feature/x")).status is BranchStatus.ACTIVE


async def test_the_fulltext_index_is_rebuilt_only_after_a_purge(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Chunk deletes do not reach the external-content FTS index (db.remove_package):
    a purge that frees chunks must rebuild it, or freed rowids keep matching."""
    factory = make_fake_uow_factory()
    await _seed_due_deleted_branch(factory)
    rebuild = RecordingFulltextRebuild()
    maintenance = _maintenance(_squash_git(), factory, rebuild_fulltext_index=rebuild)
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        assert await maintenance.run(now=3.0) == MaintenanceReport(purged=("gone",))
        assert await maintenance.run(now=4.0) == MaintenanceReport()
    assert rebuild.calls == 1
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert [json.loads(r.getMessage()) for r in infos] == [
        {"event": "branch_maintenance", "merged": [], "deleted": [], "purged": ["gone"]}
    ]


async def test_the_null_maintenance_reports_nothing() -> None:
    # Wired when git is absent: an empty ref listing must never read as
    # "every indexed branch was deleted".
    assert await NullBranchMaintenance().run(now=1.0) == MaintenanceReport()


async def test_the_verb_runner_commits_the_verb_and_rebuilds_after_a_purge() -> None:
    factory = make_fake_uow_factory()
    await _seed_due_deleted_branch(factory)
    rebuild = RecordingFulltextRebuild()
    runner = BranchVerbRunner(uow_factory=factory, policy=POLICY, rebuild_fulltext_index=rebuild)
    assert await runner.run(BranchVerb.PIN, "gone", now=1.0) == "branches: pin gone"
    assert rebuild.calls == 0
    assert await runner.run(BranchVerb.PURGE, "gone", now=1.0) == "branches: purge gone"
    assert rebuild.calls == 1
    async with factory() as uow:
        row = await uow.branches.get_branch("gone")
        assert row.pinned is True and await uow.branch_chunks.count_for_branch("gone") == 0
    with pytest.raises(BranchVerbError, match="no indexed branch 'nope'"):
        await runner.run(BranchVerb.RETIRE, "nope", now=1.0)
