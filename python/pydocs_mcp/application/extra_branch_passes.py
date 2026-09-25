"""The ``--branch NAME`` / ``--all-branches`` driver of ``index``, ``serve`` and
``watch`` (spec §6.9, #310).

Which local branches get a git-objects pass, in which order, which are left to
the working-tree pass or to their retirement, and what an unknown name or a
per-branch failure does. The CLI checks the names before the working-tree pass
(:func:`require_known_branch_names`), runs the passes after it and before the
branch maintenance (#316), so the maintenance sees every branch this run indexed.
The refresh queue runs the same passes for a branch the ref watcher saw move
(:func:`run_watched_branch_pass`, #317) and for a tracked remote-tracking ref
(:func:`remote_ref_pass_target` then :func:`run_remote_ref_pass`, #318).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydocs_mcp.application.branch_pass import BranchPassOutcome
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.exceptions import PydocsMCPError
from pydocs_mcp.git.errors import GitCommandError, UnsafeBlobPathError
from pydocs_mcp.git.refs import REMOTES_PREFIX
from pydocs_mcp.models import BranchIndexSource, BranchStatus
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")

# The listing an unknown-name error prints when there is no local branch at all
# (git off, no repository, an unborn repository).
_NO_LOCAL_BRANCHES = "none"
# What costs one branch its pass, never the run (spec §6.11): its objects are
# unreadable (git) or unusable (a crafted tree entry). Anything else — a temp
# dir inside the project, a full disk, a bug — fails the run loudly.
_PER_BRANCH_FAILURES = (GitCommandError, UnsafeBlobPathError)


class BranchPassSkipReason(StrEnum):
    """Why a requested branch gets no git-objects pass."""

    # Indexed from disk by the working-tree pass of the same run.
    CHECKED_OUT = "checked_out"
    # The served row (a ``--skip-project`` run after a checkout): only the
    # working-tree pass may rewrite it.
    SERVED = "served"
    # A merged, deleted or inactive row that only ``--all-branches`` (or a ref
    # move, #317) reached. Spec §6.8a gives re-activation to an explicit
    # ``--branch NAME``: a sweep re-indexing it would re-activate it, the
    # maintenance would retire it again with a fresh deadline, and its grace
    # purge would never come.
    RETIRED = "retired"
    # The ref watcher queued a pass for a branch whose ref is gone by the time
    # it runs (#317): the maintenance retires it, nothing is indexed.
    NO_LOCAL_REF = "no_local_ref"
    # A tracked remote-tracking ref (``git.remote.track_refs``, #318) a prune
    # fetch removed before its queued pass ran.
    NO_REMOTE_TRACKING_REF = "no_remote_tracking_ref"
    # A tracked remote-tracking ref whose row already carries its sha: the
    # lane asks for every one at start, and most have not moved (#318).
    ALREADY_INDEXED = "already_indexed"


class UnknownBranchNameError(PydocsMCPError, ValueError):
    """A ``--branch`` name no local branch carries; the message lists the local ones."""


@dataclass(frozen=True, slots=True)
class ExtraBranchRequest:
    """The branches the flags asked for, beyond the checked-out one."""

    names: tuple[str, ...] = ()
    all_branches: bool = False

    @classmethod
    def from_flags(cls, names: Sequence[str] | None, all_branches: bool) -> ExtraBranchRequest:
        # dict.fromkeys: a repeated --branch runs once, in first-seen order.
        return cls(tuple(dict.fromkeys(names or ())), all_branches)

    @property
    def is_empty(self) -> bool:
        return not (self.names or self.all_branches)


# A ref-watcher pass names no branch explicitly: the RETIRED skip applies (#317).
_NO_EXPLICIT_NAMES = ExtraBranchRequest()


class BranchRefIndexer(Protocol):
    """The slice of ``BranchIndexer`` the driver uses."""

    @property
    def git(self) -> GitRepository: ...

    @property
    def uow_factory(self) -> Callable[[], UnitOfWork]: ...

    async def index_ref(
        self, name: str, ref_sha: str, *, source: BranchIndexSource = ...
    ) -> BranchPassOutcome: ...


@dataclass(frozen=True, slots=True)
class _LocalRefs:
    heads: Mapping[str, str]
    checked_out: str | None


@dataclass(frozen=True, slots=True)
class _StampedRows:
    """What the bundle's ``branches`` rows say about the requested names."""

    served: str | None
    retired: frozenset[str]


async def require_known_branch_names(git: GitRepository, request: ExtraBranchRequest) -> None:
    """:class:`UnknownBranchNameError` for a ``--branch`` name no local branch carries.

    The CLI's pre-flight (#310): run before the working-tree pass, so a typo
    costs one git call instead of a full index. An unreadable branch list
    passes — the driver logs it and skips every pass (R8).
    """
    if not request.names:
        return
    try:
        heads = await asyncio.to_thread(git.list_local_branches)
    except GitCommandError:
        return
    _require_local_names(request.names, dict(heads))


async def run_extra_branch_passes(
    indexer: BranchRefIndexer,
    request: ExtraBranchRequest,
    *,
    rebuild_fulltext_index: Callable[[], Awaitable[None]],
) -> tuple[BranchPassOutcome, ...]:
    """One git-objects pass per requested branch; the outcomes of those that ran.

    Raises :class:`UnknownBranchNameError` before any pass when a name is not
    a local branch. A git failure or a refused blob path skips that one branch
    (spec §6.11); an unreadable branch list skips them all — the run's
    working-tree index stands.
    """
    if request.is_empty:
        return ()
    refs = await _read_local_refs(indexer.git)
    if refs is None:
        return ()
    _require_local_names(request.names, refs.heads)
    rows = await _stamped_rows(indexer.uow_factory)
    outcomes: list[BranchPassOutcome] = []
    try:
        await _run_passes(indexer, request, refs, rows, outcomes)
    finally:
        # Chunk inserts and GC deletes bypass the external-content FTS index.
        # In a finally: the passes before a failing one have committed.
        if any(outcome.moved_chunks for outcome in outcomes):
            await rebuild_fulltext_index()
    return tuple(outcomes)


async def run_watched_branch_pass(
    indexer: BranchRefIndexer,
    name: str,
    *,
    rebuild_fulltext_index: Callable[[], Awaitable[None]],
) -> BranchPassOutcome | None:
    """The git-objects pass the ref watcher queued for a tracked branch that
    moved (spec §6.8, #317); its outcome, or ``None`` when skipped or failed.

    The #310 skip rules with no explicit name, so a ref move never re-activates
    a retired row; a ref gone by the time the job runs is skipped, not an error.
    """
    refs = await _read_local_refs(indexer.git)
    if refs is None:
        return None
    reason = await _watched_skip_reason(indexer, name, refs)
    outcome = await _pass_or_skip(indexer, name, refs.heads.get(name, ""), reason)
    if outcome is not None and outcome.moved_chunks:
        # Chunk inserts and GC deletes bypass the external-content FTS index.
        await rebuild_fulltext_index()
    return outcome


async def remote_ref_pass_target(
    git: GitRepository, uow_factory: Callable[[], UnitOfWork], name: str
) -> str | None:
    """The sha the pass of a tracked remote-tracking ref such as ``origin/main``
    indexes (spec §6.8b layer 2, #318): ``refs/remotes/<name>``'s — never a
    local ref's. ``None`` when there is nothing to index (logged: pruned, a
    hand-retired row, or already indexed at that sha) or git failed.

    One ref read and one row read, so the job runner decides before it builds
    the git-objects indexer, which loads the embedder: the lane asks for every
    tracked ref at each start, and most have not moved (#318 review).
    """
    sha = await _remote_tracking_sha(git, name)
    if sha is None:
        return None
    reason = await _remote_ref_skip_reason(uow_factory, name, sha)
    if reason is None:
        return sha
    _log_event(logging.INFO, "branch_pass_skipped", branch=name, reason=reason.value)
    return None


async def run_remote_ref_pass(
    indexer: BranchRefIndexer,
    name: str,
    sha: str,
    *,
    rebuild_fulltext_index: Callable[[], Awaitable[None]],
) -> BranchPassOutcome | None:
    """The git-objects pass of a tracked remote-tracking ref at ``sha`` (from
    :func:`remote_ref_pass_target`), like any branch that is not checked out;
    its outcome, or ``None`` when it failed (spec §6.11: the previous
    membership stands)."""
    outcome = await _pass_or_skip(indexer, name, sha, None)
    if outcome is not None and outcome.moved_chunks:
        # Chunk inserts and GC deletes bypass the external-content FTS index.
        await rebuild_fulltext_index()
    return outcome


async def _remote_tracking_sha(git: GitRepository, name: str) -> str | None:
    """``""`` for a pruned ref (its pass is skipped); ``None`` when git failed."""
    try:
        sha = await asyncio.to_thread(git.head_sha, f"{REMOTES_PREFIX}{name}")
    except GitCommandError as exc:
        _log_event(logging.WARNING, "remote_ref_unreadable", branch=name, error=str(exc))
        return None
    return sha or ""


async def _remote_ref_skip_reason(
    uow_factory: Callable[[], UnitOfWork], name: str, sha: str
) -> BranchPassSkipReason | None:
    if not sha:
        return BranchPassSkipReason.NO_REMOTE_TRACKING_REF
    async with uow_factory() as uow:
        row = await uow.branches.get_branch(name)
    if row is None:
        return None
    if row.status is not BranchStatus.ACTIVE:
        return BranchPassSkipReason.RETIRED
    return BranchPassSkipReason.ALREADY_INDEXED if row.head_sha == sha else None


async def _watched_skip_reason(
    indexer: BranchRefIndexer, name: str, refs: _LocalRefs
) -> BranchPassSkipReason | None:
    if name not in refs.heads:
        return BranchPassSkipReason.NO_LOCAL_REF
    rows = await _stamped_rows(indexer.uow_factory)
    return _skip_reason(name, _NO_EXPLICIT_NAMES, refs, rows)


async def _run_passes(
    indexer: BranchRefIndexer,
    request: ExtraBranchRequest,
    refs: _LocalRefs,
    rows: _StampedRows,
    outcomes: list[BranchPassOutcome],
) -> None:
    """Append each pass's outcome as it commits, so a failure keeps the earlier ones."""
    for name in _pass_order(request, refs):
        reason = _skip_reason(name, request, refs, rows)
        outcome = await _pass_or_skip(indexer, name, refs.heads[name], reason)
        if outcome is not None:
            outcomes.append(outcome)


async def _read_local_refs(git: GitRepository) -> _LocalRefs | None:
    try:
        return await asyncio.to_thread(_local_refs, git)
    except GitCommandError as exc:
        # R8: a git hiccup never fails the run; the working tree is indexed.
        _log_event(logging.WARNING, "extra_branches_unavailable", error=str(exc))
        return None


def _local_refs(git: GitRepository) -> _LocalRefs:
    return _LocalRefs(dict(git.list_local_branches()), git.current_branch())


def _require_local_names(names: Sequence[str], heads: Mapping[str, str]) -> None:
    unknown = [name for name in names if name not in heads]
    if not unknown:
        return
    local = ", ".join(sorted(heads)) or _NO_LOCAL_BRANCHES
    raise UnknownBranchNameError(
        f"no local branch named {', '.join(map(repr, unknown))}; local branches: {local}"
    )


async def _stamped_rows(uow_factory: Callable[[], UnitOfWork]) -> _StampedRows:
    async with uow_factory() as uow:
        served = await uow.branches.default_branch_name()
        records = await uow.branches.list_branches()
    retired = frozenset(r.name for r in records if r.status is not BranchStatus.ACTIVE)
    return _StampedRows(served, retired)


def _pass_order(request: ExtraBranchRequest, refs: _LocalRefs) -> tuple[str, ...]:
    """The named branches in the order given, then — with ``--all-branches`` —
    every other local branch but the checked-out one, by name."""
    if not request.all_branches:
        return request.names
    rest = sorted(
        name for name in refs.heads if name != refs.checked_out and name not in request.names
    )
    return (*request.names, *rest)


def _skip_reason(
    name: str, request: ExtraBranchRequest, refs: _LocalRefs, rows: _StampedRows
) -> BranchPassSkipReason | None:
    if name == refs.checked_out:
        return BranchPassSkipReason.CHECKED_OUT
    if name == rows.served:
        return BranchPassSkipReason.SERVED
    if name in rows.retired and name not in request.names:
        return BranchPassSkipReason.RETIRED
    return None


async def _pass_or_skip(
    indexer: BranchRefIndexer, name: str, head_sha: str, reason: BranchPassSkipReason | None
) -> BranchPassOutcome | None:
    if reason is not None:
        _log_event(logging.INFO, "branch_pass_skipped", branch=name, reason=reason.value)
        return None
    try:
        return await indexer.index_ref(name, head_sha)
    except _PER_BRANCH_FAILURES as exc:
        # Spec §6.11: the pass is aborted before its transaction commits, so
        # the branch keeps its previous membership; the next branch still runs.
        _log_event(logging.WARNING, "branch_pass_failed", branch=name, error=str(exc))
        return None


def _log_event(level: int, event: str, **fields: str) -> None:
    log.log(level, json.dumps({"event": event, **fields}))


__all__ = (
    "BranchPassSkipReason",
    "BranchRefIndexer",
    "ExtraBranchRequest",
    "UnknownBranchNameError",
    "remote_ref_pass_target",
    "require_known_branch_names",
    "run_extra_branch_passes",
    "run_remote_ref_pass",
    "run_watched_branch_pass",
)
