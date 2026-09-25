"""The refresh loop's composition root (spec §6.8, #317): the queue, its runner,
the ref watcher and the file watcher, wired from the loaded config.

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
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.branch_policy import resolve_base_branch
from pydocs_mcp.application.extra_branch_passes import ExtraBranchRequest
from pydocs_mcp.application.merge_base_recheck import MergeBaseRecheck
from pydocs_mcp.application.served_branch_head import read_served_branch_head
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

if TYPE_CHECKING:
    from pydocs_mcp.application.branch_maintenance import BranchMaintenanceRunner
    from pydocs_mcp.retrieval.config import AppConfig
    from pydocs_mcp.storage.factories import IndexerBundle

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


def ref_watch_applies(config: AppConfig, project_root: Path) -> bool:
    """Spec §6.8: on by default for every ``serve`` / ``watch`` with a readable
    repository; ``git.ref_watch.enabled: false`` or no git (AC-12) turns it off."""
    return config.git.ref_watch.enabled and git_is_available(config.git, project_root)


async def run_refresh(
    wiring: RefreshWiring, *, serve: Callable[[], Awaitable[None]] | None = None
) -> None:
    """Run the queue and its sources while ``serve`` runs (until cancelled without one)."""
    gitdir = _readable_gitdir(wiring)
    tracking = BranchTracking.for_run(gitdir, wiring.config.git.branches, wiring.extra_branches)
    queue = IndexJobQueue(build_index_job_runner(wiring, tracking))
    submissions = RefreshSubmissions(queue, tracking)
    ref_watcher = (
        _ref_watcher(wiring, gitdir)
        if gitdir is not None and wiring.config.git.ref_watch.enabled
        else None
    )
    await run_refresh_loop(
        submissions, ref_watcher=ref_watcher, file_watcher=wiring.file_watcher, serve=serve
    )


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
    return IndexJobRunner(
        tracking=tracking,
        reindex_working_tree=wiring.reindex_working_tree,
        index_other_branch=WatchedBranchPasses(partial(_git_objects_indexer, wiring)),
        recheck_merge_bases=recheck.run,
        served_head=partial(read_served_branch_head, build_sqlite_uow_factory(wiring.db_path)),
    )


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
    "RefreshWiring",
    "build_index_job_runner",
    "build_merge_base_recheck",
    "ref_watch_applies",
    "run_refresh",
)
