"""The queue's one worker (spec §6.8, #317): which pass each job runs — the
working-tree pass, a git-objects pass, the merge-base re-check, or nothing yet."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

from pydocs_mcp.application.served_branch_head import ServedBranchHead
from pydocs_mcp.retrieval.config.git_models import GitBranchesConfig
from pydocs_mcp.serve.index_job_runner import IndexJobRunner, WatchedBranchPasses
from pydocs_mcp.serve.index_jobs import IndexJob, IndexJobKind
from pydocs_mcp.serve.refresh_jobs import BranchTracking
from tests._branch_pass_fakes import RecordingBranchRefIndexer, local_branches_git

A, B = "a" * 40, "b" * 40
BRANCH = IndexJobKind.BRANCH_INDEX


class Recorder:
    def __init__(self, served: ServedBranchHead | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.served = served

    async def served_head(self) -> ServedBranchHead | None:
        return self.served

    async def reindex(self) -> None:
        self.calls.append(("working_tree",))

    async def other(self, name: str) -> None:
        self.calls.append(("git_objects", name))

    async def recheck(self) -> None:
        self.calls.append(("recheck",))


def _gitdir(tmp_path: Path, head: str = "ref: refs/heads/main") -> Path:
    gitdir = tmp_path / ".git"
    for name in ("main", "feature/x", "wip"):
        ref = gitdir / "refs" / "heads" / name
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(A + "\n", encoding="utf-8")
    (gitdir / "HEAD").write_text(head + "\n", encoding="utf-8")
    return gitdir


def _runner(tmp_path: Path, recorder: Recorder, track: list[str]) -> IndexJobRunner:
    tracking = BranchTracking(_gitdir(tmp_path), GitBranchesConfig(track=track))
    return IndexJobRunner(
        tracking=tracking,
        reindex_working_tree=recorder.reindex,
        index_other_branch=recorder.other,
        recheck_merge_bases=recorder.recheck,
        served_head=recorder.served_head,
    )


async def test_the_working_tree_branch_runs_the_working_tree_pass(tmp_path: Path) -> None:
    """Whatever ``track`` says: the working tree is the served index."""
    recorder = Recorder()
    await _runner(tmp_path, recorder, ["wip"])(IndexJob(IndexJobKind.BRANCH_INDEX, "main"))
    assert recorder.calls == [("working_tree",)]


async def test_a_ref_job_the_served_row_already_carries_runs_no_pass(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """#317 AC 1 under ``serve --watch``: the checkout's file events ran the pass
    first; the ref watcher's job for the same branch and head is then dropped."""
    recorder = Recorder(served=ServedBranchHead("main", A))
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _runner(tmp_path, recorder, ["checked_out"])(IndexJob(BRANCH, "main", ref_head_sha=A))
    assert recorder.calls == []
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events == [{"event": "index_job_skipped", "branch": "main", "reason": "already_served"}]


@pytest.mark.parametrize(
    ("served", "job"),
    [
        # A save (or a save merged into a ref job): edits no pass has read yet.
        (ServedBranchHead("main", A), IndexJob(BRANCH, "main")),
        # The ref moved past the served head.
        (ServedBranchHead("main", B), IndexJob(BRANCH, "main", ref_head_sha=A)),
        # Another branch is served: a checkout the index has not followed yet.
        (ServedBranchHead("wip", A), IndexJob(BRANCH, "main", ref_head_sha=A)),
        # Nothing stamped yet.
        (None, IndexJob(BRANCH, "main", ref_head_sha=A)),
    ],
)
async def test_a_job_the_served_row_does_not_carry_runs_the_pass(
    tmp_path: Path, served: ServedBranchHead | None, job: IndexJob
) -> None:
    recorder = Recorder(served=served)
    await _runner(tmp_path, recorder, ["checked_out"])(job)
    assert recorder.calls == [("working_tree",)]


async def test_another_tracked_branch_runs_a_git_objects_pass(tmp_path: Path) -> None:
    recorder = Recorder()
    await _runner(tmp_path, recorder, ["checked_out", "feature/*"])(
        IndexJob(IndexJobKind.BRANCH_INDEX, "feature/x")
    )
    assert recorder.calls == [("git_objects", "feature/x")]


async def test_a_tracked_remote_ref_runs_the_remote_ref_pass(tmp_path: Path) -> None:
    """Spec §6.8b layer 2 (#318): ``origin/main`` is neither the working tree
    nor a local branch; it is indexed from its remote-tracking ref."""
    recorder, remote = Recorder(), []

    async def remote_pass(name: str) -> None:
        remote.append(name)

    runner = replace(
        _runner(tmp_path, recorder, ["checked_out"]),
        tracked_remote_refs=frozenset({"origin/main"}),
        index_remote_ref=remote_pass,
    )
    await runner(IndexJob(BRANCH, "origin/main"))
    await runner(IndexJob(BRANCH, "origin/other"))  # not tracked: skipped as before
    assert (recorder.calls, remote) == ([], ["origin/main"])


async def test_a_job_for_a_branch_no_longer_tracked_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A save queued just before a checkout names the branch that was checked
    out: by the time it runs that branch is neither the working tree nor tracked."""
    recorder = Recorder()
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _runner(tmp_path, recorder, ["checked_out"])(
            IndexJob(IndexJobKind.BRANCH_INDEX, "wip")
        )
    assert recorder.calls == []
    events = [json.loads(r.getMessage()) for r in caplog.records]
    assert events == [{"event": "index_job_skipped", "branch": "wip", "reason": "untracked"}]


async def test_the_merge_base_recheck_runs_the_recheck(tmp_path: Path) -> None:
    recorder = Recorder()
    await _runner(tmp_path, recorder, ["checked_out"])(IndexJob(IndexJobKind.MERGE_BASE_RECHECK))
    assert recorder.calls == [("recheck",)]


@pytest.mark.parametrize("kind", [IndexJobKind.RETENTION_WINDOW, IndexJobKind.DIFF_SLICE])
async def test_the_diff_slice_jobs_are_logged_until_landing_units_carry_diffs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, kind: IndexJobKind
) -> None:
    recorder = Recorder()
    with caplog.at_level(logging.INFO, logger="pydocs-mcp"):
        await _runner(tmp_path, recorder, ["checked_out"])(IndexJob(kind))
    assert recorder.calls == []
    assert json.loads(caplog.records[0].getMessage())["event"] == "index_job_deferred"


async def test_the_git_objects_indexer_is_built_once_on_first_use() -> None:
    """Building it loads the embedder: a bundle whose refresh never leaves the
    working tree never pays for it."""
    indexer, builds = RecordingBranchRefIndexer(local_branches_git()), []

    async def rebuild() -> None:
        return None

    def build():
        builds.append(1)
        return indexer, rebuild

    passes = WatchedBranchPasses(build)
    assert builds == []
    await passes("feature/x")
    await passes("wip")
    assert (len(builds), [name for name, _ in indexer.calls]) == (1, ["feature/x", "wip"])


async def test_a_remote_ref_pass_shares_the_one_git_objects_indexer() -> None:
    git = local_branches_git()
    git.refs["refs/remotes/origin/main"] = B
    indexer, builds = RecordingBranchRefIndexer(git), []

    async def rebuild() -> None:
        return None

    def build():
        builds.append(1)
        return indexer, rebuild

    passes = WatchedBranchPasses(build, remote_ref_target=_targets({"origin/main": B}))
    await passes("wip")
    await passes.remote_ref("origin/main")
    assert (len(builds), indexer.calls[-1]) == (1, ("origin/main", B))


def _targets(shas: dict[str, str | None]):
    async def target(name: str) -> str | None:
        return shas.get(name)

    return target


async def test_a_remote_ref_with_nothing_to_index_never_builds_the_indexer() -> None:
    """#318 review: the lane asks for every tracked remote ref at each start,
    and most are already indexed at their sha; deciding that must not load the
    embedder, which building the git-objects indexer does."""

    def build():
        raise AssertionError("the indexer was built")

    passes = WatchedBranchPasses(build, remote_ref_target=_targets({"origin/main": None}))
    await passes.remote_ref("origin/main")
