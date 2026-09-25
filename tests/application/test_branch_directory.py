"""BranchDirectory: rows + live heads through the plumbing readers, TTL-cached (#311)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from pydocs_mcp.application.branch_directory import (
    EMPTY_SNAPSHOT,
    BranchDirectory,
    NullBranchDirectory,
)
from pydocs_mcp.application.upstream_status import UpstreamStatus, UpstreamStatusBoard
from pydocs_mcp.models import BranchIndexSource, BranchStatus, LandingKind
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.factories import build_sqlite_uow_factory
from tests._fakes import make_fake_uow_factory
from tests._git_sandbox import NoProcessSpawned

A, B, C = "a" * 40, "b" * 40, "c" * 40


def _row(name: str, head: str = A, **kw: object) -> BranchRecord:
    return BranchRecord(name, head, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, **kw)


def _unit(sha: str) -> BranchRecord:
    return BranchRecord(
        sha,
        sha,
        BranchIndexSource.GIT_OBJECTS,
        "p",
        1.0,
        1.0,
        landing_kind=LandingKind.SINGLE_COMMIT,
    )


def _gitdir(root: Path, branch: str, sha: str) -> Path:
    gitdir = root / ".git"
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    (gitdir / "refs" / "heads" / branch).write_text(sha + "\n", encoding="utf-8")
    return gitdir


async def _seeded(*records: BranchRecord):
    factory = make_fake_uow_factory()
    async with factory() as uow:
        for record in records:
            await uow.branches.upsert_branch(record)
        await uow.commit()
    return factory


async def test_snapshot_reads_rows_live_branch_and_live_heads(tmp_path: Path) -> None:
    _gitdir(tmp_path, "main", B)
    factory = await _seeded(_row("main", is_default=True))
    clock = [0.0]
    directory = BranchDirectory(factory, tmp_path, ttl_seconds=10.0, now=lambda: clock[0])
    snap = await directory.snapshot()
    assert snap.default_name == "main" and snap.live_branch == "main"
    assert snap.live_heads == {"main": B}
    (tmp_path / ".git" / "refs" / "heads" / "main").write_text(A + "\n", encoding="utf-8")
    assert (await directory.snapshot()).live_heads == {"main": B}  # cached
    clock[0] = 11.0
    assert (await directory.snapshot()).live_heads == {"main": A}  # TTL elapsed


async def test_the_upstream_statuses_come_from_the_provider_the_lane_publishes_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 2 (#318): the request path reads the last tuple the remote lane
    computed — a callable, never a git process — keyed by branch name."""
    _gitdir(tmp_path, "main", B)
    factory = await _seeded(_row("main", is_default=True))
    board = UpstreamStatusBoard()
    status = UpstreamStatus("main", "origin/main", 0, 2, 100.0)
    board.publish((status,))
    directory = BranchDirectory(
        factory, tmp_path, ttl_seconds=0.0, upstream_status_provider=board.latest
    )
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    assert (await directory.snapshot()).upstream == {"main": status}
    board.publish(())
    assert (await directory.snapshot()).upstream == {}


async def test_the_fetch_age_is_read_when_the_snapshot_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#318 review: a fetch that moves no ref rewrites FETCH_HEAD but moves no
    ref the lane listens to, so the age is re-read with the snapshot — a stat
    on the plumbing, never a process (AC-31)."""
    gitdir = _gitdir(tmp_path, "main", B)
    factory = await _seeded(_row("main", is_default=True))
    board = UpstreamStatusBoard()
    board.publish((UpstreamStatus("main", "origin/main", 0, 2, 100.0),))
    directory = BranchDirectory(
        factory, tmp_path, ttl_seconds=0.0, upstream_status_provider=board.latest
    )
    monkeypatch.setattr(subprocess, "Popen", NoProcessSpawned)
    assert (await directory.snapshot()).upstream["main"].fetched_at == 100.0  # never fetched
    (gitdir / "FETCH_HEAD").write_text("", encoding="utf-8")
    os.utime(gitdir / "FETCH_HEAD", (5000.0, 5000.0))
    assert (await directory.snapshot()).upstream["main"].fetched_at == 5000.0
    os.utime(gitdir / "FETCH_HEAD", (6000.0, 6000.0))  # a fetch that moved nothing
    assert (await directory.snapshot()).upstream["main"].fetched_at == 6000.0


async def test_without_a_provider_no_branch_has_an_upstream_status(tmp_path: Path) -> None:
    _gitdir(tmp_path, "main", B)
    factory = await _seeded(_row("main", is_default=True))
    directory = BranchDirectory(factory, tmp_path, ttl_seconds=0.0)
    assert (await directory.snapshot()).upstream == {} and EMPTY_SNAPSHOT.upstream == {}


async def test_live_heads_come_from_loose_then_packed_refs_for_live_rows_only(
    tmp_path: Path,
) -> None:
    gitdir = _gitdir(tmp_path, "main", B)
    (gitdir / "packed-refs").write_text(
        f"# pack-refs with: peeled\n{C} refs/heads/feature/x\n{A} refs/heads/old\n",
        encoding="utf-8",
    )
    factory = await _seeded(
        _row("main", is_default=True),
        _row("feature/x"),
        # A retired row can never answer, and a landing unit has no local ref
        # (spec §6.5b): neither costs a plumbing read.
        _row("old", status=BranchStatus.MERGED, retired_at=0.0),
        _unit(C),
    )
    snap = await BranchDirectory(factory, tmp_path, ttl_seconds=0.0).snapshot()
    assert snap.live_heads == {"main": B, "feature/x": C}
    assert [r.name for r in snap.branch_rows()] == ["main", "feature/x", "old"]
    assert [r.name for r in snap.landing_units()] == [C]


async def test_a_bundle_without_a_project_root_reads_rows_but_no_live_facts() -> None:
    factory = await _seeded(_row("main", is_default=True))
    snap = await BranchDirectory(factory, None, ttl_seconds=0.0).snapshot()
    assert snap.default_name == "main" and snap.live_branch is None and snap.live_heads == {}


async def test_a_root_outside_git_has_no_live_facts(tmp_path: Path) -> None:
    factory = await _seeded(_row("main", is_default=True))
    snap = await BranchDirectory(factory, tmp_path, ttl_seconds=0.0).snapshot()
    assert snap.live_branch is None and snap.live_heads == {}


async def test_an_empty_branches_table_is_the_empty_snapshot() -> None:
    snap = await BranchDirectory(make_fake_uow_factory(), None, ttl_seconds=0.0).snapshot()
    assert snap == EMPTY_SNAPSHOT


async def test_touch_remembers_the_use_in_memory_and_never_writes() -> None:
    factory = make_fake_uow_factory()
    directory = BranchDirectory(factory, None, ttl_seconds=10.0, now=lambda: 42.0)
    directory.touch("main")
    assert directory.used_at == {"main": 42.0}
    async with factory() as uow:
        assert await uow.branches.list_branches() == ()


async def test_null_directory_is_the_empty_snapshot_whatever_was_touched() -> None:
    directory = NullBranchDirectory()
    directory.touch("main")
    assert await directory.snapshot() == EMPTY_SNAPSHOT


def _empty_file(path: Path) -> None:
    # What a plain ``sqlite3.connect`` (the freshness probe's) leaves where a
    # removed bundle was: a file that holds no table at all.
    path.write_bytes(b"")


@pytest.mark.parametrize("vanish", [lambda path: None, _empty_file], ids=["removed", "emptied"])
async def test_a_bundle_gone_from_under_the_server_is_the_empty_snapshot(
    tmp_path: Path, vanish: Callable[[Path], None]
) -> None:
    """#311: the nine tools, grep / glob / read_file included, must keep
    answering when the served bundle disappears — the freshness probe's and
    ``multirepo.current_metadata``'s degrade, not a CacheNotIndexedError."""
    bundle = tmp_path / "gone.db"
    vanish(bundle)
    directory = BranchDirectory(build_sqlite_uow_factory(bundle), tmp_path, ttl_seconds=0.0)
    assert await directory.snapshot() == EMPTY_SNAPSHOT


class _BrokenUnitOfWork:
    async def __aenter__(self) -> _BrokenUnitOfWork:
        raise RuntimeError("a defect, not a vanished bundle")

    async def __aexit__(self, *exc: object) -> None:
        return None


async def test_any_other_read_failure_still_raises() -> None:
    directory = BranchDirectory(_BrokenUnitOfWork, None, ttl_seconds=0.0)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="a defect"):
        await directory.snapshot()
