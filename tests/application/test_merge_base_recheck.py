"""The merge-base re-check job (spec §6.5 re-check rule, #317): one ``merge_base``
per live branch, the base stamp #308 writes refreshed where its pair moved, then
the #316 maintenance — all over ONE base resolution."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, replace

import pytest

from pydocs_mcp.application.branch_maintenance import MaintenanceReport
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.merge_base_recheck import MergeBaseRecheck, refresh_base_stamps
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import BranchIndexSource, BranchStatus, LandingKind
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository, InMemoryBranchStore, make_fake_uow_factory

NEW_TIP, H_MAIN, FX, FY, ORPHAN, GONE = (c * 40 for c in "abcdef")
OLD_MB, NEW_MB, MB_Y = "1" * 40, "2" * 40, "3" * 40
BASE = BaseBranch("main", NEW_TIP, "refs/remotes/origin/main")


@dataclass
class CountingGit(FakeGitRepository):
    """Records every ``merge_base`` call; ``on_merge_base`` runs inside it."""

    merge_base_calls: list[tuple[str, str]] = field(default_factory=list)
    on_merge_base: object = None

    def merge_base(self, a: str, b: str) -> str | None:
        self.merge_base_calls.append((a, b))
        if callable(self.on_merge_base):
            self.on_merge_base(b)
        return super().merge_base(a, b)


@dataclass
class RecordingResolver:
    base: BaseBranch | None = BASE
    error: GitCommandError | None = None
    calls: int = 0

    def __call__(self, git) -> BaseBranch | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.base


@dataclass
class RecordingMaintenance:
    """What the #316 maintenance saw when it asked its resolver for the base."""

    resolver: object
    seen: list[object]

    async def run(self, now: float | None = None) -> MaintenanceReport:
        try:
            self.seen.append(self.resolver(None))  # type: ignore[operator]
        except GitCommandError as exc:
            self.seen.append(exc)
        return MaintenanceReport(merged=("seen",))


def _row(name: str, head: str, **overrides: object) -> BranchRecord:
    fields = {"base_name": "main"} | overrides
    return BranchRecord(name, head, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0, **fields)


def _bundle() -> tuple[InMemoryBranchStore, object]:
    store = InMemoryBranchStore()
    rows = (
        replace(_row("main", H_MAIN, merge_base_sha=H_MAIN), is_default=True),
        _row("feature/x", FX, merge_base_sha=OLD_MB),
        _row("feature/y", FY, merge_base_sha=MB_Y),
        _row("orphan", ORPHAN, base_name=None),
        _row("gone", GONE, status=BranchStatus.MERGED, merge_base_sha=OLD_MB),
        _row(NEW_MB, NEW_MB, landing_kind=LandingKind.SINGLE_COMMIT),
        replace(_row("no git", ""), base_name=None),
    )
    store.records.update({row.name: row for row in rows})
    return store, make_fake_uow_factory(branches=store)


def _git(**extra: object) -> CountingGit:
    merge_bases = {
        frozenset((NEW_TIP, H_MAIN)): H_MAIN,
        frozenset((NEW_TIP, FX)): NEW_MB,
        frozenset((NEW_TIP, FY)): MB_Y,
    }
    return CountingGit(merge_bases=merge_bases, **extra)


def _recheck(git, factory, resolver, seen: list[object]) -> MergeBaseRecheck:
    return MergeBaseRecheck(
        git=git,
        uow_factory=factory,
        base_resolver=resolver,
        maintenance_for=lambda base_resolver: RecordingMaintenance(base_resolver, seen),
    )


async def test_a_base_tip_move_re_checks_every_live_branch_once() -> None:
    store, factory = _bundle()
    git, resolver, seen = _git(), RecordingResolver(), []
    report = await _recheck(git, factory, resolver, seen).run(now=5.0)
    # One merge_base per live branch; retired rows, landing units and the
    # head-less non-git row are not re-checked (AC-22).
    assert git.merge_base_calls == [(NEW_TIP, h) for h in (H_MAIN, FX, FY, ORPHAN)]
    stamps = {n: (r.base_name, r.merge_base_sha) for n, r in store.records.items()}
    assert stamps["feature/x"] == ("main", NEW_MB)
    assert stamps["orphan"] == ("main", "")  # no common ancestor (spec §6.5)
    assert stamps["feature/y"] == ("main", MB_Y) and stamps["main"] == ("main", H_MAIN)
    assert stamps["gone"] == ("main", OLD_MB)
    upserts = [c.payload.name for c in store.calls if c.method == "upsert_branch"]
    assert upserts == ["feature/x", "orphan"]  # rows whose pair moved, nothing else
    # The maintenance ran once and read the SAME base: one resolution per job.
    assert (report, seen, resolver.calls) == (MaintenanceReport(merged=("seen",)), [BASE], 1)


async def test_a_changed_base_in_yaml_is_applied() -> None:
    """AC-22's last clause: the stamped base name differs from the resolved one."""
    store, factory = _bundle()
    store.records["feature/y"] = replace(store.records["feature/y"], base_name="master")
    assert await refresh_base_stamps(_git(), factory, BASE) == ("feature/x", "feature/y", "orphan")
    assert store.records["feature/y"].base_name == "main"


async def test_a_head_that_moved_meanwhile_is_left_to_its_own_pass() -> None:
    """Detection runs with no unit of work open; a row whose head moved before
    the write was stamped by the pass that moved it."""
    store, factory = _bundle()

    def move_feature_x(head: str) -> None:
        if head == FX:
            store.records["feature/x"] = replace(store.records["feature/x"], head_sha=FY)

    moved = await refresh_base_stamps(_git(on_merge_base=move_feature_x), factory, BASE)
    assert moved == ("orphan",)
    assert store.records["feature/x"].merge_base_sha == OLD_MB


async def test_a_git_error_leaves_every_stamp_and_still_runs_the_maintenance(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, factory = _bundle()
    before = dict(store.records)
    seen: list[object] = []
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _recheck(_git(fail=True), factory, RecordingResolver(), seen).run()
    assert store.records == before
    assert seen == [BASE]
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert [e["event"] for e in events] == ["base_restamp_skipped"]


async def test_a_failed_base_read_is_replayed_to_the_maintenance() -> None:
    """Not "no base": the maintenance must skip its verdicts, or a gone ref that
    landed would retire as DELETED instead of MERGED (#316 safety f)."""
    store, factory = _bundle()
    before = dict(store.records)
    failure = GitCommandError(("git", "symbolic-ref"), "timeout after 30s")
    git, seen = _git(), []
    await _recheck(git, factory, RecordingResolver(error=failure), seen).run()
    assert (store.records, git.merge_base_calls, seen) == (before, [], [failure])


async def test_no_base_re_stamps_nothing() -> None:
    store, factory = _bundle()
    git, seen = _git(), []
    await _recheck(git, factory, RecordingResolver(base=None), seen).run()
    assert (git.merge_base_calls, seen) == ([], [None])
