"""The remote lane (spec §6.8b, #318): what happens when nobody runs ``git pull``.

Four layers, from "tell" to "act", each one YAML switch under ``git.remote``:

1. **the behind-upstream signal** (``behind_hint``, on): each followed branch
   against ``@{upstream}`` — ``rev-list --left-right --count`` over the
   remote-tracking ref alone, the fetch age off ``FETCH_HEAD``, where the
   branch is checked out off the worktree plumbing — computed at start and
   after every ref move the watcher reports, on the lane's own task: never on
   the watcher's path, never on the request path. Published on the bundle's
   board for ``BranchDirectory.upstream_status_provider``;
2. **remote refs as branches** (``track_refs``, empty): asked for at start,
   and again after a fetch that moved them (the ref watcher queues the moves
   it sees too; the queue coalesces the two);
3. **change-detect, then fetch** (``auto_fetch.enabled``, off — O14): every
   ``interval_seconds`` one ``ls-remote --heads``; ``fetch --prune`` only when
   a head moved; exponential backoff with jitter while the remote is
   unreachable, one log line per state change;
4. **fast-forward branches nobody has checked out**
   (``fast_forward_branches_without_worktree``, off): after a fetch, a
   compare-and-swap ``update-ref`` per followed branch whose tip is an
   ancestor of its upstream; the ref watcher then reindexes it. Never a branch
   a worktree holds — checked out, or being rebased or bisected.

Local first: the lane is a task of its own — never a queue job, never the
queue's lock — and submits jobs only after a fetch succeeded, so a network
failure cannot delay, cancel or fail a local pass. It runs beside the ref
watcher only (``refresh_wiring``). With auto-fetch off it spawns no network
process at all (AC 1). Every git call goes through the port: no hook runs,
every call is bounded, and none runs on the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.application.upstream_status import (
    CheckoutPlace,
    UpstreamStatus,
    UpstreamStatusBoard,
    behind_upstream_suggestion,
)
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.git.refs import (
    HEADS_PREFIX,
    REMOTES_PREFIX,
    SYMREF_PREFIX,
    list_refs,
    read_branches_held_by_worktrees,
    read_last_fetch_time,
)
from pydocs_mcp.retrieval.config.git_models import RemoteConfig
from pydocs_mcp.serve.index_jobs import REMOTE_PRIORITY, IndexJob, IndexJobKind, IndexJobQueue
from pydocs_mcp.serve.ref_watcher import RefEventKind, RefEvents

log = logging.getLogger("pydocs-mcp")

# A backoff wait is shortened by up to this fraction, never lengthened: the
# jitter spreads the retries and never probes past ``backoff_max_seconds``.
_BACKOFF_JITTER_FRACTION = 0.1
# What git prints when the remote refuses the credentials (spec §6.8b: back
# off to the ceiling at once and say how to fix it). ``GIT_TERMINAL_PROMPT=0``
# turns a missing https credential into "could not read Username".
_AUTHENTICATION_MARKERS = ("Authentication failed", "could not read Username", "Permission denied")
_AUTHENTICATION_HINT = (
    "the remote refused the credentials: configure a credential helper or an ssh key "
    "that works without a prompt; retrying every git.remote.auto_fetch.backoff_max_seconds"
)
# The moves that can change a branch's ahead/behind or where it is checked
# out: its own ref, a checkout, its upstream — a remote-tracking ref, reported
# as the base tip's when it is one. Any fetch that moves one counts, whoever ran it.
_UPSTREAM_MOVES = frozenset(
    {
        RefEventKind.BRANCH_MOVED,
        RefEventKind.BRANCH_DELETED,
        RefEventKind.HEAD_MOVED,
        RefEventKind.REMOTE_MOVED,
        RefEventKind.BASE_TIP_MOVED,
    }
)


class RemoteSyncState(StrEnum):
    """Whether the lane's last network check reached the remote."""

    ONLINE = "online"
    OFFLINE = "offline"


def _log_event(level: int, event: str, **fields: object) -> None:
    log.log(level, json.dumps({"event": event, **fields}))


def _remote_tracking_heads(gitdir: Path, remote: str) -> dict[str, str]:
    """``refs/remotes/<remote>/*`` as branch → sha: what the last fetch left.
    Plumbing reads; the ``HEAD`` symref names no branch of its own."""
    prefix = f"{REMOTES_PREFIX}{remote}/"
    return {
        ref.removeprefix(prefix): sha
        for ref, sha in list_refs(gitdir, prefix).items()
        if not sha.startswith(SYMREF_PREFIX)
    }


def _moved_heads(heads: dict[str, str], known: dict[str, str]) -> frozenset[str]:
    """Branches created, moved or deleted on the remote since ``known``."""
    return frozenset(
        name for name in heads.keys() | known.keys() if heads.get(name) != known.get(name)
    )


def _is_authentication_failure(exc: GitCommandError) -> bool:
    return any(marker in exc.stderr_tail for marker in _AUTHENTICATION_MARKERS)


def _log_offline(remote: str, exc: GitCommandError, authentication: bool) -> None:
    fields: dict[str, object] = {
        "remote": remote,
        "reason": exc.reason,
        "error": exc.stderr_tail,
        "authentication": authentication,
    }
    if authentication:
        fields["hint"] = _AUTHENTICATION_HINT
    _log_event(logging.WARNING, "remote_sync_offline", **fields)


@dataclass(frozen=True, slots=True)
class _LocalFacts:
    """What one layer-1 refresh reads from the plumbing once, for every branch:
    the fetch age and which worktree holds which branch."""

    fetched_at: float | None
    held: Mapping[str, Path]
    this_worktree: Path

    @classmethod
    def read(cls, gitdir: Path) -> _LocalFacts:
        held = read_branches_held_by_worktrees(gitdir)
        return cls(read_last_fetch_time(gitdir), held, gitdir.resolve())

    def checkout_place(self, branch: str) -> CheckoutPlace:
        holder = self.held.get(branch)
        if holder is None:
            return CheckoutPlace.NOWHERE
        if holder == self.this_worktree:
            return CheckoutPlace.THIS_WORKTREE
        return CheckoutPlace.OTHER_WORKTREE


@dataclass(slots=True)
class RemoteSyncScheduler:
    """The remote lane of one repository (spec §6.8b).

    NOT frozen: ``state``, ``statuses`` and the backoff are the lane's own
    running state, touched by its task and the ref watcher's hand-off.
    """

    git: GitRepository
    config: RemoteConfig
    queue: IndexJobQueue
    gitdir: Path
    # The local branches to follow, re-read on every use (plumbing reads):
    # layer 1's rows and layer 4's candidates.
    tracked: Callable[[], tuple[str, ...]]
    board: UpstreamStatusBoard = field(default_factory=UpstreamStatusBoard)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    jitter: Callable[[], float] = random.random
    state: RemoteSyncState = field(default=RemoteSyncState.ONLINE, init=False)
    statuses: tuple[UpstreamStatus, ...] = field(default=(), init=False)
    # The remote's heads at the last successful check; ``None`` until then,
    # when the first check compares with the remote-tracking refs on disk, so
    # a restart re-fetches nothing already fetched.
    _known_remote_heads: dict[str, str] | None = field(default=None, init=False, repr=False)
    _backoff_seconds: float = field(default=0.0, init=False, repr=False)
    # Whether the failure that keeps the lane OFFLINE refused the credentials:
    # one that does after a network failure is logged too — only it has a fix.
    _offline_for_authentication: bool = field(default=False, init=False, repr=False)
    # Set by the ref watcher's hand-off, served by the lane's own task: the
    # watcher never waits on git for the signal (#318 review, "local first").
    _upstream_moved: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    # One layer-1 refresh at a time: the follower and a post-fetch refresh
    # would otherwise race to publish.
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)

    # ── Layer 1: the behind-upstream signal ──────────────────────────────

    def refresh_upstream_status(self) -> tuple[UpstreamStatus, ...]:
        """Each followed branch against its upstream, from the remote-tracking
        ref alone — no ls-remote, no fetch (AC 2) — kept in ``statuses`` and
        published for the request path. Blocking git reads: off the loop."""
        if not self.config.behind_hint:
            return ()
        facts = _LocalFacts.read(self.gitdir)
        rows = (self._upstream_status(branch, facts) for branch in self.tracked())
        self.statuses = tuple(row for row in rows if row is not None)
        self._publish()
        return self.statuses

    def _upstream_status(self, branch: str, facts: _LocalFacts) -> UpstreamStatus | None:
        try:
            return self._read_upstream_status(branch, facts)
        except GitCommandError as exc:
            # One branch's pruned upstream must not hide the others' status.
            _log_event(logging.DEBUG, "upstream_status_unreadable", branch=branch, error=str(exc))
            return None

    def _read_upstream_status(self, branch: str, facts: _LocalFacts) -> UpstreamStatus | None:
        upstream = self.git.upstream_of(branch)
        if upstream is None:
            return None
        ahead, behind = self.git.ahead_behind(branch, upstream)
        place = facts.checkout_place(branch)
        return UpstreamStatus(branch, upstream, ahead, behind, facts.fetched_at, place)

    async def refresh_upstream_status_off_loop(self) -> None:
        async with self._refresh_lock:
            await asyncio.to_thread(self.refresh_upstream_status)

    async def on_ref_events(self, events: RefEvents) -> None:
        """The ref watcher's hand-off, after its jobs are queued: a move that
        can change a branch's ahead/behind — anyone's fetch — flags a layer-1
        refresh for the lane's own task. No git runs here: the watcher takes
        its next snapshot at once (spec §6.8b "local first")."""
        if self.config.behind_hint and any(event.kind in _UPSTREAM_MOVES for event in events):
            self._upstream_moved.set()

    @property
    def upstream_refresh_pending(self) -> bool:
        """A move was reported and its layer-1 refresh has not started yet."""
        return self._upstream_moved.is_set()

    async def refresh_upstream_status_when_moved(self) -> None:
        """One layer-1 refresh after the next reported move; the moves reported
        before it starts coalesce into it, a move during it asks for another."""
        await self._upstream_moved.wait()
        self._upstream_moved.clear()
        await self.refresh_upstream_status_off_loop()

    async def _follow_upstream_moves(self) -> None:
        while True:
            try:
                await self.refresh_upstream_status_when_moved()
            except Exception:
                # A failed refresh must not end the lane: the next move retries.
                log.exception(json.dumps({"event": "upstream_status_refresh_failed"}))

    def _publish(self) -> None:
        # Spec §6.8b offline rule: while the remote is unreachable no suggestion
        # tells the agent to pull. The suggestion is the signal's only reader
        # in P1, so the board goes quiet; the statuses come back on recovery.
        self.board.publish(() if self.state is RemoteSyncState.OFFLINE else self.statuses)

    # ── The lane: layers 1 and 2 at start, then the moves and layer 3 ────

    async def run_until_cancelled(self) -> None:
        """Layer 1 and the layer-2 requests from what is already fetched (no
        network); then, until cancelled, a layer-1 refresh per reported move
        and — only with auto-fetch on (O14) — one layer-3 check per interval."""
        await self.start_from_last_fetch()
        follower = asyncio.create_task(self._follow_upstream_moves(), name="upstream-status")
        try:
            if self.config.auto_fetch.enabled:
                await self._check_remote_every_interval()
            else:
                await follower
        finally:
            follower.cancel()
            await asyncio.gather(follower, return_exceptions=True)

    async def start_from_last_fetch(self) -> None:
        """Layer 1 and a layer-2 job per ``track_refs`` entry, from the refs the
        last fetch left: no network, whatever the switches say."""
        await self.refresh_upstream_status_off_loop()
        await self._request_tracked_remote_refs(moved=None)

    async def _check_remote_every_interval(self) -> None:
        while True:
            await self.check_remote_once()
            await self.sleep(self._next_wait())

    async def check_remote_once(self) -> None:
        """Layer 3 once: ``ls-remote``, and ``fetch --prune`` only when a head
        moved; after a successful fetch, layers 4, 1 and 2. A failure backs the
        lane off; the state change is logged once (AC 3)."""
        try:
            moved = await asyncio.to_thread(self._fetch_when_a_remote_head_moved)
        except GitCommandError as exc:
            self._go_offline(exc)
            return
        self._come_online()
        if moved:
            await self._after_fetch(moved)

    def _fetch_when_a_remote_head_moved(self) -> frozenset[str]:
        remote = self.config.name
        heads = dict(self.git.ls_remote_heads(remote))
        known = self._known_remote_heads
        if known is None:
            known = _remote_tracking_heads(self.gitdir, remote)
        moved = _moved_heads(heads, known)
        if moved:
            self.git.fetch(remote, prune=True)
        # Only after the fetch succeeded: a failed one is retried next check.
        self._known_remote_heads = heads
        return moved

    async def _after_fetch(self, moved: frozenset[str]) -> None:
        # Layer 4 before layer 1: a branch just fast-forwarded is no longer behind.
        if self.config.fast_forward_branches_without_worktree:
            await asyncio.to_thread(self.fast_forward_branches_without_worktree)
        await self.refresh_upstream_status_off_loop()
        await self._request_tracked_remote_refs(moved=moved)

    async def _request_tracked_remote_refs(self, *, moved: Collection[str] | None) -> None:
        """Layer 2: a job per ``track_refs`` entry — every one (``moved`` None,
        at start) or those whose remote head moved — behind any local job."""
        prefix = f"{self.config.name}/"
        for ref in self.config.track_refs:
            if moved is None or ref.removeprefix(prefix) in moved:
                job = IndexJob(IndexJobKind.BRANCH_INDEX, ref, priority=REMOTE_PRIORITY)
                await self.queue.submit(job)

    # ── Layer 4: fast-forward branches nobody has checked out ────────────

    def fast_forward_branches_without_worktree(self) -> tuple[str, ...]:
        """Move each followed branch no worktree holds to its upstream when that
        is a fast-forward; the branches moved. Blocking."""
        held = self._branches_held_by_a_worktree()
        if held is None:
            return ()
        candidates = (name for name in self.tracked() if name not in held)
        return tuple(name for name in candidates if self._fast_forward_one(name))

    def _branches_held_by_a_worktree(self) -> frozenset[str] | None:
        """git's worktree listing, plus the branches a rebase or a bisect in
        progress holds: the listing reports those worktrees as detached, and
        git ends a rebase with a compare-and-swap on its branch (#318 review).
        ``None`` without the listing: then no branch is known safe to move."""
        try:
            listed = {name for _, name in self.git.list_worktrees() if name}
        except GitCommandError as exc:
            _log_event(logging.WARNING, "remote_sync_fast_forward_skipped", error=str(exc))
            return None
        return frozenset(listed | read_branches_held_by_worktrees(self.gitdir).keys())

    def _fast_forward_one(self, branch: str) -> bool:
        # #306: update-ref RAISES on a refusal of an unmoved ref (a held lock, a
        # missing object). That is one branch's failure, not the remote's:
        # caught per branch, the next branch still moves and a fetch that
        # succeeded never reads as OFFLINE.
        try:
            return self._fast_forward(branch)
        except GitCommandError as exc:
            _log_event(
                logging.WARNING,
                "remote_sync_fast_forward_failed",
                branch=branch,
                reason=exc.reason,
                error=exc.stderr_tail,
            )
            return False

    def _fast_forward(self, branch: str) -> bool:
        target = self._fast_forward_target(branch)
        if target is None:
            return False
        upstream, local, tip = target
        message = f"pydocs-mcp: fast-forward to {upstream}"
        # A compare-and-swap: ``False`` when the ref moved meanwhile (a lost race).
        moved = self.git.update_ref_if_unchanged(f"{HEADS_PREFIX}{branch}", tip, local, message)
        if moved:
            _log_event(logging.INFO, "remote_sync_fast_forwarded", branch=branch, to=tip)
        return moved

    def _fast_forward_target(self, branch: str) -> tuple[str, str, str] | None:
        """``(upstream, local sha, upstream sha)`` when ``branch`` can
        fast-forward; a diverged branch is logged and left alone."""
        upstream = self.git.upstream_of(branch)
        if upstream is None:
            return None
        local = self.git.head_sha(f"{HEADS_PREFIX}{branch}")
        tip = self.git.head_sha(upstream)
        if not local or not tip or local == tip:
            return None
        if not self.git.is_ancestor(local, tip):
            _log_event(logging.INFO, "remote_sync_diverged", branch=branch, upstream=upstream)
            return None
        return upstream, local, tip

    # ── Offline: backoff and the state changes ───────────────────────────

    def _go_offline(self, exc: GitCommandError) -> None:
        """Spec §6.8b: network and timeout failures double the wait from the
        interval up to the ceiling; an authentication failure takes the ceiling
        at once and logs how to fix it. Logged once per state change — and once
        more when an authentication failure follows network ones."""
        ceiling = float(self.config.auto_fetch.backoff_max_seconds)
        authentication = _is_authentication_failure(exc)
        self._backoff_seconds = ceiling if authentication else self._doubled_wait(ceiling)
        newly_authentication = authentication and not self._offline_for_authentication
        self._offline_for_authentication = authentication
        if self.state is RemoteSyncState.OFFLINE and not newly_authentication:
            _log_event(logging.DEBUG, "remote_sync_still_offline", reason=exc.reason)
            return
        self.state = RemoteSyncState.OFFLINE
        self._publish()
        _log_offline(self.config.name, exc, authentication)

    def _doubled_wait(self, ceiling: float) -> float:
        if self.state is RemoteSyncState.ONLINE:
            return min(float(self.config.auto_fetch.interval_seconds), ceiling)
        return min(self._backoff_seconds * 2, ceiling)

    def _come_online(self) -> None:
        self._offline_for_authentication = False
        if self.state is RemoteSyncState.ONLINE:
            return
        self.state = RemoteSyncState.ONLINE
        self._publish()
        _log_event(logging.INFO, "remote_sync_online", remote=self.config.name)

    def _next_wait(self) -> float:
        if self.state is RemoteSyncState.ONLINE:
            return float(self.config.auto_fetch.interval_seconds)
        return self._backoff_seconds * (1.0 - _BACKOFF_JITTER_FRACTION * self.jitter())


__all__ = (
    "RemoteSyncScheduler",
    "RemoteSyncState",
    "UpstreamStatus",
    "behind_upstream_suggestion",
)
