"""The refresh loop's composition root (spec §6.8, #317): the queue, its runner,
the ref watcher, the file watcher and the remote lane (spec §6.8b, #318), wired
from the loaded config.

It lives beside the loop rather than in ``__main__.py`` / ``storage/factories.py``
because both are past the 500-line ceiling (call sites only there). The CLI
builds :class:`RefreshWiring` from its arguments — the working-tree reindex
callback, the lazily built indexer bundle, this run's ``--branch`` flags — and
awaits :func:`run_refresh`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.branch_policy import resolve_base_branch
from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest, remote_ref_pass_target
from pydocs_mcp.application.merge_base_recheck import MergeBaseRecheck
from pydocs_mcp.application.served_branch_head import read_served_branch_head
from pydocs_mcp.application.upstream_status import (
    UpstreamStatusBoard,
    behind_upstream_hint_applies,
    upstream_status_board_for,
)
from pydocs_mcp.git.factory import git_is_available, git_repository_factory
from pydocs_mcp.git.refs import locate_gitdir
from pydocs_mcp.serve.index_job_runner import (
    IndexerWithRebuild,
    IndexJobRunner,
    WatchedBranchPasses,
)
from pydocs_mcp.serve.index_jobs import IndexJobQueue
from pydocs_mcp.serve.ref_watcher import RefWatcher
from pydocs_mcp.serve.refresh_jobs import BranchTracking
from pydocs_mcp.serve.refresh_loop import FileWatchSource, RefreshSubmissions, run_refresh_loop
from pydocs_mcp.serve.remote_sync import RemoteSyncScheduler

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_maintenance import BranchMaintenanceRunner
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.retrieval.config.git_models import RemoteConfig
    from pydocs_mcp.storage.factories import IndexerBundle
    from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True, kw_only=True)
class RefreshWiring:
    """What the CLI hands the refresh loop."""

    config: AppConfig
    project_root: Path
    db_path: Path
    # The working-tree pass a save or a checkout runs: the CLI's watch-cycle
    # reindex (no --force, no maintenance, no --branch passes; #310, #316).
    reindex_working_tree: Callable[[], Awaitable[None]]
    # Called once, on the first git-objects pass: it loads the embedder.
    bundle_factory: Callable[[], IndexerBundle]
    extra_branches: ExtraBranchRequest
    file_watcher: FileWatchSource | None = None
    # Whether an MCP server in this process answers requests (#318 review): the
    # standalone ``watch`` runs none, so nothing there reads layer 1's board.
    answers_requests: bool = True


def ref_watch_applies(config: AppConfig, project_root: Path) -> bool:
    """Spec §6.8: on by default for every ``serve`` / ``watch`` with a readable
    repository; ``git.ref_watch.enabled: false`` or no git (AC-12) turns it off."""
    return config.git.ref_watch.enabled and git_is_available(config.git, project_root)


_REF_WATCH_OFF_REASON = "git.ref_watch.enabled is false"
_NO_REPOSITORY_REASON = "no readable git repository"


def log_remote_lane_unavailable(config: AppConfig) -> None:
    """One line when a remote layer beyond the default signal is configured but
    no remote lane will run — it runs beside the ref watcher only (#318 review)."""
    remote = config.git.remote
    opted_in = remote.auto_fetch.enabled or remote.fast_forward_branches_without_worktree
    if not (opted_in or remote.track_refs):
        return
    reason = _NO_REPOSITORY_REASON if config.git.ref_watch.enabled else _REF_WATCH_OFF_REASON
    log.warning(json.dumps({"event": "remote_sync_unavailable", "reason": reason}))


def _ref_watched_gitdir(config: AppConfig, gitdir: Path | None) -> Path | None:
    """The gitdir the ref watcher follows; ``None`` when no watcher runs, and
    then no remote lane either (#318 review): a fast-forward would never be
    reindexed, and the signal never refreshed after anyone's fetch."""
    return gitdir if gitdir is not None and config.git.ref_watch.enabled else None


@dataclass(frozen=True, slots=True)
class RefreshParts:
    """The queue's submissions and the remote lane of one refresh loop."""

    submissions: RefreshSubmissions
    # ``None`` without a readable repository or without the ref watcher.
    remote_lane: RemoteSyncScheduler | None


async def run_refresh(
    wiring: RefreshWiring, *, serve: Callable[[], Awaitable[None]] | None = None
) -> None:
    """Run the queue and its sources while ``serve`` runs (until cancelled without one)."""
    gitdir = _readable_gitdir(wiring)
    parts = build_refresh_parts(wiring, gitdir)
    watched = _ref_watched_gitdir(wiring.config, gitdir)
    ref_watcher = None if watched is None else _ref_watcher(wiring, watched)
    await run_refresh_loop(
        parts.submissions,
        ref_watcher=ref_watcher,
        file_watcher=wiring.file_watcher,
        remote_lane=parts.remote_lane,
        serve=serve,
    )


def build_refresh_parts(wiring: RefreshWiring, gitdir: Path | None) -> RefreshParts:
    """One queue and its runner, the remote lane beside it (#318) when the ref
    watcher runs, and the submissions that feed both from the watchers."""
    tracking = BranchTracking.for_run(gitdir, wiring.config.git.branches, wiring.extra_branches)
    queue = IndexJobQueue(build_index_job_runner(wiring, tracking))
    track_refs = frozenset(wiring.config.git.remote.track_refs)
    submissions = RefreshSubmissions(queue, tracking, track_refs=track_refs)
    watched = _ref_watched_gitdir(wiring.config, gitdir)
    if watched is None:
        log_remote_lane_unavailable(wiring.config)
        return RefreshParts(submissions, None)
    lane = build_remote_sync(
        wiring.config,
        wiring.project_root,
        queue,
        watched,
        tracking=tracking,
        board=upstream_status_board_for(wiring.db_path),
        answers_requests=wiring.answers_requests,
    )
    return RefreshParts(replace(submissions, after_ref_events=lane.on_ref_events), lane)


def build_remote_sync(
    config: AppConfig,
    project_root: Path,
    queue: IndexJobQueue,
    gitdir: Path,
    *,
    tracking: BranchTracking,
    board: UpstreamStatusBoard,
    answers_requests: bool = True,
) -> RemoteSyncScheduler:
    """The remote lane of one repository (spec §6.8b, #318): the port bounds
    ``ls-remote`` by ``auto_fetch.ls_remote_timeout_seconds`` and the fetch by
    ``timeout_seconds``, with no hook; the statuses go to ``board``."""
    return RemoteSyncScheduler(
        git=git_repository_factory(config.git)(project_root),
        config=_remote_config_for_the_lane(config, answers_requests),
        queue=queue,
        gitdir=gitdir,
        tracked=tracking.followed_local_branches,
        board=board,
    )


def _remote_config_for_the_lane(config: AppConfig, answers_requests: bool) -> RemoteConfig:
    """``git.remote`` with ``behind_hint`` as the lane applies it: off wherever
    no response could carry the signal — the ADR 0007 rule flag off, or no
    server in this process — so layer 1 spawns nothing for nobody (#318 review)."""
    hint = behind_upstream_hint_applies(config) and answers_requests
    return config.git.remote.model_copy(update={"behind_hint": hint})


def _readable_gitdir(wiring: RefreshWiring) -> Path | None:
    """With git off or absent the working tree is the non-git row: jobs are keyed
    as the working-tree pass names it, and no ref is ever read."""
    if not git_is_available(wiring.config.git, wiring.project_root):
        return None
    return locate_gitdir(wiring.project_root)


def build_index_job_runner(wiring: RefreshWiring, tracking: BranchTracking) -> IndexJobRunner:
    """The queue's worker: the working-tree pass, the git-objects pass of a
    tracked branch (#310), the merge-base re-check (#316's maintenance plus the
    base re-stamp)."""
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    recheck = build_merge_base_recheck(wiring.config, wiring.db_path, wiring.project_root)
    # The branches rows are SQLite-only: no sidecar for these reads.
    rows = build_sqlite_uow_factory(wiring.db_path)
    passes = WatchedBranchPasses(
        partial(_git_objects_indexer, wiring),
        remote_ref_target=partial(_remote_ref_target, wiring, rows),
    )
    return IndexJobRunner(
        tracking=tracking,
        reindex_working_tree=wiring.reindex_working_tree,
        index_other_branch=passes,
        recheck_merge_bases=recheck.run,
        served_head=partial(read_served_branch_head, rows),
        tracked_remote_refs=frozenset(wiring.config.git.remote.track_refs),
        index_remote_ref=passes.remote_ref,
    )


async def _remote_ref_target(
    wiring: RefreshWiring, rows: Callable[[], UnitOfWork], name: str
) -> str | None:
    """A tracked remote ref's sha to index, decided through the port and the
    rows without the git-objects indexer and its embedder (#318 review)."""
    git = git_repository_factory(wiring.config.git)(wiring.project_root)
    return await remote_ref_pass_target(git, rows, name)


def build_merge_base_recheck(
    config: AppConfig, db_path: Path, project_root: Path
) -> BranchMaintenanceRunner:
    """The ``MERGE_BASE_RECHECK`` job (spec §6.5, #317): every live branch's base
    re-stamped, then #316's maintenance over the same resolved base; without git,
    the maintenance alone (its Null runner)."""
    from pydocs_mcp.storage.factories import build_branch_maintenance, build_sqlite_uow_factory

    if not git_is_available(config.git, project_root):
        return build_branch_maintenance(config, db_path, project_root)
    return MergeBaseRecheck(
        git=git_repository_factory(config.git)(project_root),
        # The stamp is a branches-row write: SQLite alone, no sidecar.
        uow_factory=build_sqlite_uow_factory(db_path),
        base_resolver=lambda repo: resolve_base_branch(repo, config.git),
        maintenance_for=lambda resolver: build_branch_maintenance(
            config, db_path, project_root, base_resolver=resolver
        ),
    )


def _git_objects_indexer(wiring: RefreshWiring) -> IndexerWithRebuild:
    from pydocs_mcp.storage.factories import build_branch_indexer

    bundle = wiring.bundle_factory()
    return build_branch_indexer(wiring.config, wiring.project_root, bundle), bundle.rebuild_fts


def _ref_watcher(wiring: RefreshWiring, gitdir: Path) -> RefWatcher:
    """No base is resolved here: the watcher picks the base tip from each ref
    snapshot through the plumbing (#317), so a remote added later is followed."""
    git_config = wiring.config.git
    watcher = RefWatcher(
        gitdir,
        git_config.branches.base,
        git_config.remote.name,
        git_config.ref_watch.debounce_ms,
        git_config.ref_watch.reconcile_seconds,
    )
    payload = {
        "event": "ref_watch_started",
        "gitdir": str(watcher.gitdir),
        "base": git_config.branches.base,
        "debounce_ms": git_config.ref_watch.debounce_ms,
    }
    log.info(json.dumps(payload))
    return watcher


__all__ = (
    "RefreshParts",
    "RefreshWiring",
    "build_index_job_runner",
    "build_merge_base_recheck",
    "build_refresh_parts",
    "build_remote_sync",
    "log_remote_lane_unavailable",
    "ref_watch_applies",
    "run_refresh",
)
