"""The ``index --branch NAME`` / ``--all-branches`` driver (spec §6.9, #310): which
local branches get a git-objects pass, in which order, which are skipped and
why, and what an unknown name or a git failure does."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import pytest

from pydocs_mcp.application.branch_pass import BranchPassOutcome
from pydocs_mcp.application.extra_branch_passes import (
    ExtraBranchRequest,
    UnknownBranchNameError,
    require_known_branch_names,
    run_extra_branch_passes,
)
from pydocs_mcp.git.errors import GitCommandError, UnsafeBlobPathError
from pydocs_mcp.models import BranchIndexSource, BranchStatus
from pydocs_mcp.storage.branch_records import BranchRecord
from tests._fakes import FakeGitRepository, make_fake_uow_factory

A, B, C, D = "a" * 40, "b" * 40, "c" * 40, "d" * 40
_IDLE = BranchPassOutcome(1, 1, 0, 0, 1, 0)
_CHANGED = BranchPassOutcome(1, 0, 1, 1, 0, 0)


@dataclass
class _RecordingIndexer:
    git: FakeGitRepository
    uow_factory: object = field(default_factory=make_fake_uow_factory)
    failing: frozenset[str] = frozenset()
    # Branch name -> the exception its pass raises (beyond the git failures).
    raising: dict[str, Exception] = field(default_factory=dict)
    outcome: BranchPassOutcome = _IDLE
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def index_ref(
        self, name: str, ref_sha: str, *, source: BranchIndexSource = BranchIndexSource.GIT_OBJECTS
    ) -> BranchPassOutcome:
        self.calls.append((name, ref_sha))
        if name in self.failing:
            raise GitCommandError(("git", "ls-tree", ref_sha), "timeout after 30s")
        if name in self.raising:
            raise self.raising[name]
        return self.outcome


def _git(checked_out: str | None = "main") -> FakeGitRepository:
    refs = {"main": A, "feature/x": B, "release/1": C, "wip": D}
    return FakeGitRepository(
        branch=checked_out, refs={f"refs/heads/{n}": s for n, s in refs.items()}
    )


class _Rebuilds:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self) -> None:
        self.count += 1


async def _run(indexer: _RecordingIndexer, request: ExtraBranchRequest) -> _Rebuilds:
    rebuilds = _Rebuilds()
    await run_extra_branch_passes(indexer, request, rebuild_fulltext_index=rebuilds)
    return rebuilds


async def test_no_flag_touches_nothing() -> None:
    git = FakeGitRepository(fail=True)  # any git call would raise
    indexer = _RecordingIndexer(git)
    assert (
        await run_extra_branch_passes(
            indexer, ExtraBranchRequest(), rebuild_fulltext_index=_Rebuilds()
        )
        == ()
    )
    assert indexer.calls == []


async def test_named_branches_are_indexed_in_order_at_their_local_heads() -> None:
    indexer = _RecordingIndexer(_git())
    await _run(indexer, ExtraBranchRequest(names=("release/1", "feature/x")))
    assert indexer.calls == [("release/1", C), ("feature/x", B)]


async def test_an_unknown_name_fails_before_any_pass_naming_the_local_branches() -> None:
    indexer = _RecordingIndexer(_git())
    with pytest.raises(UnknownBranchNameError) as raised:
        await _run(indexer, ExtraBranchRequest(names=("feature/x", "nope")))
    assert str(raised.value) == (
        "no local branch named 'nope'; local branches: feature/x, main, release/1, wip"
    )
    assert indexer.calls == []


async def test_no_local_branch_at_all_says_so() -> None:
    """Git off or no repository: the Null adapter lists nothing."""
    indexer = _RecordingIndexer(FakeGitRepository())
    with pytest.raises(UnknownBranchNameError, match="local branches: none$"):
        await _run(indexer, ExtraBranchRequest(names=("feature/x",)))


async def test_the_checked_out_branch_is_left_to_the_working_tree_pass(caplog) -> None:
    indexer = _RecordingIndexer(_git())
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(names=("main", "feature/x")))
    assert indexer.calls == [("feature/x", B)]
    skipped = [json.loads(r.message) for r in caplog.records if "branch_pass_skipped" in r.message]
    assert skipped == [{"event": "branch_pass_skipped", "branch": "main", "reason": "checked_out"}]


async def test_all_branches_adds_every_other_local_branch_after_the_named_ones() -> None:
    indexer = _RecordingIndexer(_git())
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
    indexer = _RecordingIndexer(_git(checked_out="feature/x"), uow_factory=factory)
    await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == [("release/1", C), ("wip", D)]


async def test_a_git_failure_skips_that_branch_and_the_rest_still_run(caplog) -> None:
    indexer = _RecordingIndexer(_git(), failing=frozenset({"feature/x"}))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(names=("feature/x", "wip")))
    assert indexer.calls == [("feature/x", B), ("wip", D)]
    failed = [json.loads(r.message) for r in caplog.records if "branch_pass_failed" in r.message]
    assert [(e["branch"], "timeout" in e["error"]) for e in failed] == [("feature/x", True)]


async def test_an_unreadable_branch_list_skips_every_pass(caplog) -> None:
    indexer = _RecordingIndexer(FakeGitRepository(fail=True))
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == []
    assert "extra_branches_unavailable" in caplog.text


@pytest.mark.parametrize(("outcome", "rebuilds"), [(_IDLE, 0), (_CHANGED, 1)])
async def test_the_fulltext_index_is_rebuilt_once_when_a_pass_moved_chunks(
    outcome: BranchPassOutcome, rebuilds: int
) -> None:
    indexer = _RecordingIndexer(_git(), outcome=outcome)
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
    indexer = _RecordingIndexer(_git(), uow_factory=await _factory_with_merged_row())
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(all_branches=True))
    assert indexer.calls == [("release/1", C), ("wip", D)]
    skipped = [json.loads(r.message) for r in caplog.records if "branch_pass_skipped" in r.message]
    assert {"event": "branch_pass_skipped", "branch": "feature/x", "reason": "retired"} in skipped


async def test_naming_a_retired_branch_re_activates_it() -> None:
    indexer = _RecordingIndexer(_git(), uow_factory=await _factory_with_merged_row())
    await _run(indexer, ExtraBranchRequest(names=("feature/x",), all_branches=True))
    assert indexer.calls == [("feature/x", B), ("release/1", C), ("wip", D)]


async def test_the_preflight_rejects_an_unknown_name_before_any_indexing() -> None:
    with pytest.raises(UnknownBranchNameError, match="no local branch named 'nope'"):
        await require_known_branch_names(_git(), ExtraBranchRequest(names=("main", "nope")))
    # Known names, no name at all, or an unreadable list (R8: the run goes on) pass.
    await require_known_branch_names(_git(), ExtraBranchRequest(names=("feature/x",)))
    await require_known_branch_names(
        FakeGitRepository(fail=True), ExtraBranchRequest(all_branches=True)
    )
    await require_known_branch_names(FakeGitRepository(fail=True), ExtraBranchRequest(names=("x",)))


async def test_a_refused_blob_path_skips_that_branch_and_the_rest_still_run(caplog) -> None:
    """A crafted tree (a ``..`` entry) is the branch's own data: like a git
    failure, it costs that branch only (spec §6.11)."""
    refused = UnsafeBlobPathError("invalid blob path: got '../x.py'")
    indexer = _RecordingIndexer(_git(), raising={"feature/x": refused})
    with caplog.at_level(logging.WARNING, logger="pydocs-mcp"):
        await _run(indexer, ExtraBranchRequest(names=("feature/x", "wip")))
    assert indexer.calls == [("feature/x", B), ("wip", D)]
    failed = [json.loads(r.message) for r in caplog.records if "branch_pass_failed" in r.message]
    assert [e["branch"] for e in failed] == ["feature/x"]


async def test_the_fulltext_index_is_rebuilt_even_when_a_later_pass_raises() -> None:
    """A committed pass moved chunk rows: the external-content FTS index must
    follow them even when the next branch fails loudly (a config error)."""
    boom = ValueError("scratch tree inside the project")
    indexer = _RecordingIndexer(_git(), outcome=_CHANGED, raising={"wip": boom})
    rebuilds = _Rebuilds()
    with pytest.raises(ValueError, match="scratch tree inside the project"):
        await run_extra_branch_passes(
            indexer,
            ExtraBranchRequest(names=("feature/x", "wip")),
            rebuild_fulltext_index=rebuilds,
        )
    assert rebuilds.count == 1


def test_the_request_reads_the_cli_flags() -> None:
    assert ExtraBranchRequest.from_flags(None, False).is_empty
    request = ExtraBranchRequest.from_flags(["a", "b", "a"], True)
    assert (request.names, request.all_branches, request.is_empty) == (("a", "b"), True, False)
