"""The ``index --branch NAME`` / ``--all-branches`` driver (spec §6.9, #310): which
local branches get a git-objects pass, in which order, which are skipped and
why, and what an unknown name or a git failure does."""

from __future__ import annotations

import json
import logging
from dataclasses import replace

import pytest

from pydocs_mcp.application.branch_pass import BranchPassOutcome
from pydocs_mcp.application.extra_branch_passes import (
    ExtraBranchRequest,
    UnknownBranchNameError,
    UnselectableBranchNameError,
    require_known_branch_names,
    run_extra_branch_passes,
    remote_ref_pass_target,
    run_remote_ref_pass,
    run_watched_branch_pass,
)
from pydocs_mcp.git.errors import UnsafeBlobPathError
from pydocs_mcp.models import BranchIndexSource, BranchStatus
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._branch_pass_fakes import (
    FEATURE_TIP,
    IDLE_BRANCH_PASS,
    MAIN_TIP,
    RELEASE_TIP,
    WIP_TIP,
    RecordingBranchRefIndexer,
    local_branches_git,
)
from tests._fakes import FakeGitRepository, make_fake_uow_factory

A, B, C, D = MAIN_TIP, FEATURE_TIP, RELEASE_TIP, WIP_TIP
_IDLE = IDLE_BRANCH_PASS
_CHANGED = BranchPassOutcome(1, 0, 1, 1, 0, 0)


class _Rebuilds:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


async def _run(indexer: RecordingBranchRefIndexer, request: ExtraBranchRequest) -> _Rebuilds:
    rebuilds = _Rebuilds()
    await run_extra_branch_passes(indexer, request, rebuild_fulltext_index=rebuilds)
    return rebuilds


async def test_no_flag_touches_nothing() -> None:
    git = FakeGitRepository(fail=True)  # any git call would raise
    indexer = RecordingBranchRefIndexer(git)
    assert (
        await run_extra_branch_passes(
            indexer, ExtraBranchRequest(), rebuild_fulltext_index=_Rebuilds()
        )
        == ()
    )
    assert indexer.calls == []


async def test_named_branches_are_indexed_in_order_at_their_local_heads() -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git())
    await _run(indexer, ExtraBranchRequest(names=("release/1", "feature/x")))
    assert indexer.calls == [("release/1", C), ("feature/x", B)]


async def test_an_unknown_name_fails_before_any_pass_naming_the_local_branches() -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git())
    with pytest.raises(UnknownBranchNameError) as raised:
        await _run(indexer, ExtraBranchRequest(names=("feature/x", "nope")))
    assert str(raised.value) == (
        "no local branch named 'nope'; local branches: feature/x, main, release/1, wip"
    )
    assert indexer.calls == []


async def test_no_local_branch_at_all_says_so() -> None:
    """Git off or no repository: the Null adapter lists nothing."""
    indexer = RecordingBranchRefIndexer(FakeGitRepository())
    with pytest.raises(UnknownBranchNameError, match="local branches: none$"):
        await _run(indexer, ExtraBranchRequest(names=("feature/x",)))


async def test_the_checked_out_branch_is_left_to_the_working_tree_pass(caplog) -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git())
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(names=("main", "feature/x")))
    assert indexer.calls == [("feature/x", B)]
    skipped = [json.loads(r.message) for r in caplog.records if "branch_pass_skipped" in r.message]
    assert skipped == [{"event": "branch_pass_skipped", "branch": "main", "reason": "checked_out"}]


async def test_all_branches_adds_every_other_local_branch_after_the_named_ones() -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git())
    await _run(indexer, ExtraBranchRequest(names=("wip",), all_branches=True))
    assert indexer.calls == [("wip", D), ("feature/x", B), ("release/1", C)]


async def test_the_served_branch_is_never_indexed_from_git_objects() -> None:
    """A ``--skip-project`` run after a checkout: ``main`` is still the served
    row and not checked out — only the working-tree pass may rewrite it."""
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(
            BranchRecord("main", A, BranchIndexSource.WORKING_TREE, "p", 1.0, 1.0, is_default=True)
        )
        await uow.commit()
    indexer = RecordingBranchRefIndexer(
        local_branches_git(checked_out="feature/x"), uow_factory=factory
    )
    await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == [("release/1", C), ("wip", D)]


async def test_a_git_failure_skips_that_branch_and_the_rest_still_run(caplog) -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git(), failing=frozenset({"feature/x"}))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(names=("feature/x", "wip")))
    assert indexer.calls == [("feature/x", B), ("wip", D)]
    failed = [json.loads(r.message) for r in caplog.records if "branch_pass_failed" in r.message]
    assert [(e["branch"], "timeout" in e["error"]) for e in failed] == [("feature/x", True)]


async def test_an_unreadable_branch_list_skips_every_pass(caplog) -> None:
    indexer = RecordingBranchRefIndexer(FakeGitRepository(fail=True))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == []
    assert "extra_branches_unavailable" in caplog.text


@pytest.mark.parametrize(("outcome", "rebuilds"), [(_IDLE, 0), (_CHANGED, 1)])
async def test_the_fulltext_index_is_rebuilt_once_when_a_pass_moved_chunks(
    outcome: BranchPassOutcome, rebuilds: int
) -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git(), outcome=outcome)
    done = await _run(indexer, ExtraBranchRequest(names=("feature/x", "wip")))
    assert done.count == rebuilds


async def _factory_with_merged_row(name: str = "feature/x"):
    factory = make_fake_uow_factory()
    async with factory() as uow:
        await uow.branches.upsert_branch(
            BranchRecord(
                name,
                B,
                BranchIndexSource.GIT_OBJECTS,
                "p",
                1.0,
                1.0,
                status=BranchStatus.MERGED,
                merged_into="main",
                retired_at=2.0,
                purge_after=3.0,
            )
        )
        await uow.commit()
    return factory


async def test_all_branches_leaves_a_retired_row_to_its_grace_purge(caplog) -> None:
    """Spec §6.8a: re-activation takes an explicit ``index --branch NAME``; a
    sweep over every local branch re-indexing a merged one would push its
    purge back on every run, so it would never go."""
    indexer = RecordingBranchRefIndexer(
        local_branches_git(), uow_factory=await _factory_with_merged_row()
    )
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == [("release/1", C), ("wip", D)]
    skipped = [json.loads(r.message) for r in caplog.records if "branch_pass_skipped" in r.message]
    assert {"event": "branch_pass_skipped", "branch": "feature/x", "reason": "retired"} in skipped


async def test_naming_a_retired_branch_re_activates_it() -> None:
    indexer = RecordingBranchRefIndexer(
        local_branches_git(), uow_factory=await _factory_with_merged_row()
    )
    await _run(indexer, ExtraBranchRequest(names=("feature/x",), all_branches=True))
    assert indexer.calls == [("feature/x", B), ("release/1", C), ("wip", D)]


async def test_the_preflight_rejects_an_unknown_name_before_any_indexing() -> None:
    with pytest.raises(UnknownBranchNameError, match="no local branch named 'nope'"):
        await require_known_branch_names(
            local_branches_git(), ExtraBranchRequest(names=("main", "nope"))
        )
    # Known names, no name at all, or an unreadable list (R8: the run goes on) pass.
    await require_known_branch_names(local_branches_git(), ExtraBranchRequest(names=("feature/x",)))
    await require_known_branch_names(
        FakeGitRepository(fail=True), ExtraBranchRequest(all_branches=True)
    )
    await require_known_branch_names(FakeGitRepository(fail=True), ExtraBranchRequest(names=("x",)))


async def test_a_refused_blob_path_skips_that_branch_and_the_rest_still_run(caplog) -> None:
    """A crafted tree (a ``..`` entry) is the branch's own data: like a git
    failure, it costs that branch only (spec §6.11)."""
    refused = UnsafeBlobPathError("invalid blob path: got '../x.py'")
    indexer = RecordingBranchRefIndexer(local_branches_git(), raising={"feature/x": refused})
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(names=("feature/x", "wip")))
    assert indexer.calls == [("feature/x", B), ("wip", D)]
    failed = [json.loads(r.message) for r in caplog.records if "branch_pass_failed" in r.message]
    assert [e["branch"] for e in failed] == ["feature/x"]


async def test_the_fulltext_index_is_rebuilt_even_when_a_later_pass_raises() -> None:
    """A committed pass moved chunk rows: the external-content FTS index must
    follow them even when the next branch fails loudly (a config error)."""
    boom = ValueError("scratch tree inside the project")
    indexer = RecordingBranchRefIndexer(
        local_branches_git(), outcome=_CHANGED, raising={"wip": boom}
    )
    rebuilds = _Rebuilds()
    with pytest.raises(ValueError, match="scratch tree inside the project"):
        await run_extra_branch_passes(
            indexer,
            ExtraBranchRequest(names=("feature/x", "wip")),
            rebuild_fulltext_index=rebuilds,
        )
    assert rebuilds.count == 1


# ── #315: every branch a pass indexes is one the ``branch`` selector can name ──

_UNSELECTABLE = "fix#123"  # legal in git, outside the selector grammar


def _git_with_unselectable_branch() -> FakeGitRepository:
    git = local_branches_git()
    return FakeGitRepository(branch="main", refs={**git.refs, f"refs/heads/{_UNSELECTABLE}": B})


async def test_naming_a_branch_no_selector_could_name_fails_before_any_pass() -> None:
    """Indexing it would stamp rows no tool can select: refuse it up front, with
    the boundary's own message (the value and the accepted shapes)."""
    indexer = RecordingBranchRefIndexer(_git_with_unselectable_branch())
    for check in (
        lambda request: _run(indexer, request),
        lambda request: require_known_branch_names(indexer.git, request),
    ):
        with pytest.raises(UnselectableBranchNameError) as raised:
            await check(ExtraBranchRequest(names=("feature/x", _UNSELECTABLE)))
        message = str(raised.value)
        assert f"got {_UNSELECTABLE!r}" in message and "7-40 hex landing sha" in message
    assert indexer.calls == []


async def test_the_preflight_refuses_an_unselectable_name_even_without_git() -> None:
    """The grammar is pure: an unreadable branch list does not wave it through."""
    with pytest.raises(UnselectableBranchNameError):
        await require_known_branch_names(
            FakeGitRepository(fail=True), ExtraBranchRequest(names=(_UNSELECTABLE,))
        )


async def test_all_branches_skips_a_branch_no_selector_could_name(caplog) -> None:
    indexer = RecordingBranchRefIndexer(_git_with_unselectable_branch())
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == [("feature/x", B), ("release/1", C), ("wip", D)]
    skipped = [json.loads(r.message) for r in caplog.records if "branch_pass_skipped" in r.message]
    assert skipped == [
        {"event": "branch_pass_skipped", "branch": _UNSELECTABLE, "reason": "unselectable_name"}
    ]


def test_the_request_reads_the_cli_flags() -> None:
    assert ExtraBranchRequest.from_flags(None, False).is_empty
    request = ExtraBranchRequest.from_flags(["a", "b", "a"], True)
    assert (request.names, request.all_branches, request.is_empty) == (("a", "b"), True, False)


# ── The pass the ref watcher queues (#317) ─────────────────────────────────


def _skipped(caplog: pytest.LogCaptureFixture) -> list[dict[str, str]]:
    return [json.loads(r.message) for r in caplog.records if "branch_pass_skipped" in r.message]


@pytest.mark.parametrize(("outcome", "rebuilds"), [(_IDLE, 0), (_CHANGED, 1)])
async def test_a_watched_pass_indexes_the_branch_at_its_local_head(
    outcome: BranchPassOutcome, rebuilds: int
) -> None:
    indexer, done = RecordingBranchRefIndexer(local_branches_git(), outcome=outcome), _Rebuilds()
    assert await run_watched_branch_pass(indexer, "wip", rebuild_fulltext_index=done) == outcome
    assert (indexer.calls, done.count) == ([("wip", D)], rebuilds)


async def test_a_watched_pass_never_re_activates_a_retired_branch(caplog) -> None:
    """Only an explicit ``index --branch NAME`` re-activates (spec §6.8a): a ref
    move would otherwise push the grace purge back on every commit."""
    indexer = RecordingBranchRefIndexer(
        local_branches_git(), uow_factory=await _factory_with_merged_row()
    )
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await run_watched_branch_pass(indexer, "feature/x", rebuild_fulltext_index=_Rebuilds())
    assert indexer.calls == []
    assert _skipped(caplog) == [
        {"event": "branch_pass_skipped", "branch": "feature/x", "reason": "retired"}
    ]


async def test_a_watched_pass_for_a_ref_deleted_meanwhile_is_skipped(caplog) -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git())
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await run_watched_branch_pass(indexer, "gone", rebuild_fulltext_index=_Rebuilds())
    assert indexer.calls == []
    assert _skipped(caplog) == [
        {"event": "branch_pass_skipped", "branch": "gone", "reason": "no_local_ref"}
    ]


async def test_a_watched_pass_leaves_the_checked_out_branch_to_the_working_tree(caplog) -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git(checked_out="wip"))
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await run_watched_branch_pass(indexer, "wip", rebuild_fulltext_index=_Rebuilds())
    assert indexer.calls == []
    assert _skipped(caplog)[0]["reason"] == "checked_out"


async def test_a_watched_pass_git_failure_costs_that_pass_only(caplog) -> None:
    indexer = RecordingBranchRefIndexer(local_branches_git(), failing=frozenset({"wip"}))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        outcome = await run_watched_branch_pass(indexer, "wip", rebuild_fulltext_index=_Rebuilds())
    assert outcome is None and "branch_pass_failed" in caplog.text
    unreadable = RecordingBranchRefIndexer(FakeGitRepository(fail=True))
    assert (
        await run_watched_branch_pass(unreadable, "wip", rebuild_fulltext_index=_Rebuilds()) is None
    )


# ── The pass of a tracked remote-tracking ref (spec §6.8b layer 2, #318) ────


def _remote_git(sha: str | None = B) -> FakeGitRepository:
    git = local_branches_git()
    if sha is not None:
        git.refs["refs/remotes/origin/main"] = sha
    return git


async def _factory_with_row(name: str, head: str, status: BranchStatus):
    factory = make_fake_uow_factory()
    async with factory() as uow:
        record = BranchRecord(name, head, BranchIndexSource.GIT_OBJECTS, "p", 1.0, 1.0)
        await uow.branches.upsert_branch(replace(record, status=status))
        await uow.commit()
    return factory


async def test_a_remote_ref_pass_indexes_the_remote_tracking_ref_sha() -> None:
    """Not ``refs/heads/origin/main``: the name is the remote-tracking ref's."""
    git, factory = _remote_git(), make_fake_uow_factory()
    sha = await remote_ref_pass_target(git, factory, "origin/main")
    assert sha == B
    indexer, done = RecordingBranchRefIndexer(git, outcome=_CHANGED), _Rebuilds()
    assert await run_remote_ref_pass(indexer, "origin/main", sha, rebuild_fulltext_index=done)
    assert (indexer.calls, done.count) == ([("origin/main", B)], 1)


@pytest.mark.parametrize(
    ("sha", "row", "reason"),
    [
        # Pruned by a fetch since the job was queued.
        (None, None, "no_remote_tracking_ref"),
        # The start-up request of an already indexed ref: nothing moved.
        (B, ("origin/main", B, BranchStatus.ACTIVE), "already_indexed"),
        # Retired by hand (``branches --retire``): a move never re-activates it.
        (B, ("origin/main", A, BranchStatus.INACTIVE), "retired"),
    ],
)
async def test_a_remote_ref_pass_is_skipped_when_there_is_nothing_to_index(
    caplog: pytest.LogCaptureFixture,
    sha: str | None,
    row: tuple[str, str, BranchStatus] | None,
    reason: str,
) -> None:
    """Decided from one ref read and one row read, before any indexer exists:
    building it loads the embedder (#318 review)."""
    factory = await _factory_with_row(*row) if row else make_fake_uow_factory()
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        target = await remote_ref_pass_target(_remote_git(sha), factory, "origin/main")
    assert target is None
    assert _skipped(caplog) == [
        {"event": "branch_pass_skipped", "branch": "origin/main", "reason": reason}
    ]


async def test_a_remote_ref_pass_git_failure_costs_that_pass_only(caplog) -> None:
    git, factory = FakeGitRepository(fail=True), make_fake_uow_factory()
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        assert await remote_ref_pass_target(git, factory, "origin/main") is None
    assert "remote_ref_unreadable" in caplog.text


async def test_a_failing_remote_ref_pass_keeps_the_previous_membership(caplog) -> None:
    indexer = RecordingBranchRefIndexer(_remote_git(), failing=frozenset({"origin/main"}))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        outcome = await run_remote_ref_pass(
            indexer, "origin/main", B, rebuild_fulltext_index=_Rebuilds()
        )
    assert outcome is None and "branch_pass_failed" in caplog.text
