"""What the request path knows about branches (spec §6.4, §6.5c read-time rules; #311).

One TTL-cached snapshot per loaded bundle: the ``branches`` rows plus the live
working-tree branch and each selectable row's live ref sha, all read through
the plumbing readers of ``git/refs.py`` — a tool call never spawns git (AC-31)
and never writes (``touch`` keeps ``last_used_at`` in memory for the next
index pass, spec §6.4). A bundle removed from under the server reads as the
empty snapshot, the freshness probe's degrade.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from pydocs_mcp.application.upstream_status import UpstreamStatus, no_upstream_statuses
from pydocs_mcp.git.refs import (
    HEADS_PREFIX,
    locate_gitdir,
    read_last_fetch_time,
    resolve_git_branch,
    resolve_symref,
)
from pydocs_mcp.models import LIVE_BRANCH_STATUSES
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.errors import is_bundle_gone_error
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger(__name__)


def is_selectable_branch_row(record: BranchRecord) -> bool:
    """A branch row (never a landing unit) the request path may answer from.

    Live statuses only (spec §6.8a): a MERGED or DELETED row raises the
    retired-branch error instead, so its live ref is never read. Unlike
    ``merge_detection.is_live_branch_row``, the non-git placeholder and
    detached rows count: the empty selector answers from them.
    """
    return not record.is_landing_unit and record.status in LIVE_BRANCH_STATUSES


@dataclass(frozen=True, slots=True)
class BranchSnapshot:
    """Everything the request path knows about branches, read once per TTL."""

    records: tuple[BranchRecord, ...]
    default_name: str | None
    live_branch: str | None
    live_heads: Mapping[str, str]
    # #318: branch name -> its status against its upstream, as the remote lane
    # last computed it off the request path (spec §6.8b layer 1).
    upstream: Mapping[str, UpstreamStatus] = field(default_factory=dict)

    def branch_rows(self) -> tuple[BranchRecord, ...]:
        return tuple(r for r in self.records if not r.is_landing_unit)

    def landing_units(self) -> tuple[BranchRecord, ...]:
        return tuple(r for r in self.records if r.is_landing_unit)

    def selectable_rows(self) -> tuple[BranchRecord, ...]:
        """Branch rows a name may select: ``ACTIVE`` or ``INACTIVE``."""
        return tuple(r for r in self.records if is_selectable_branch_row(r))


EMPTY_SNAPSHOT = BranchSnapshot((), None, None, {})


class BranchDirectoryReader(Protocol):
    """The per-bundle branch view the router resolves selectors against."""

    async def snapshot(self) -> BranchSnapshot: ...

    def touch(self, name: str) -> None: ...


def _with_current_fetch_age(
    project_root: Path | None, statuses: tuple[UpstreamStatus, ...]
) -> tuple[UpstreamStatus, ...]:
    """The lane's statuses with the fetch age read now (#318 review): a fetch
    that moved no ref rewrites ``FETCH_HEAD`` yet moves nothing the lane
    listens to. A ``stat`` of a plumbing file (AC-31); no fetch, no change."""
    gitdir = None if project_root is None else locate_gitdir(project_root)
    fetched_at = None if gitdir is None else read_last_fetch_time(gitdir)
    if fetched_at is None:
        return statuses
    return tuple(replace(status, fetched_at=fetched_at) for status in statuses)


def _read_live_branch_facts(
    project_root: Path | None, names: tuple[str, ...]
) -> tuple[str | None, dict[str, str]]:
    """The checked-out branch and each name's ``refs/heads`` sha — plumbing only.

    No root (a read-only bundle), no repository, or an unresolvable ref yields
    no fact rather than an error: this runs on the request path.
    """
    if project_root is None:
        return None, {}
    gitdir = locate_gitdir(project_root)
    if gitdir is None:
        return None, {}
    heads = {name: resolve_symref(gitdir, f"{HEADS_PREFIX}{name}") for name in names}
    live_heads = {name: sha for name, sha in heads.items() if sha}
    return resolve_git_branch(project_root), live_heads


@dataclass(slots=True)
class BranchDirectory:
    """TTL-cached :class:`BranchSnapshot` of one bundle.

    NOT frozen — ``_cache`` and ``used_at`` are deliberate instance state, the
    freshness probe's discipline (one per loaded bundle; the TTL bounds re-reads).
    """

    uow_factory: Callable[[], UnitOfWork]
    project_root: Path | None
    ttl_seconds: float
    now: Callable[[], float] = time.time
    # #318: the last statuses the remote lane computed — a callable returning a
    # tuple, never a git process on the request path (AC-31).
    upstream_status_provider: Callable[[], tuple[UpstreamStatus, ...]] = no_upstream_statuses
    used_at: dict[str, float] = field(default_factory=dict)
    _cache: tuple[float, BranchSnapshot] | None = field(default=None, init=False)

    async def snapshot(self) -> BranchSnapshot:
        current = self.now()
        if self._cache is not None and current - self._cache[0] < self.ttl_seconds:
            return self._cache[1]
        snap = await self._read()
        self._cache = (current, snap)
        return snap

    async def _read(self) -> BranchSnapshot:
        try:
            records, default_name = await self._read_rows()
        except Exception as exc:
            if not is_bundle_gone_error(exc):
                raise
            # #311: every tool — grep / glob / read_file, which never needed
            # the index, included — keeps answering when the bundle is removed
            # from under the server: the freshness probe's degrade, meta null.
            log.debug(json.dumps({"event": "branch_directory_bundle_gone", "error": str(exc)}))
            return EMPTY_SNAPSHOT
        if not records:
            return EMPTY_SNAPSHOT
        names = tuple(r.name for r in records if is_selectable_branch_row(r))
        live_branch, heads = await asyncio.to_thread(
            _read_live_branch_facts, self.project_root, names
        )
        upstream = {status.branch: status for status in await self._upstream_statuses()}
        return BranchSnapshot(records, default_name, live_branch, heads, upstream)

    async def _upstream_statuses(self) -> tuple[UpstreamStatus, ...]:
        statuses = self.upstream_status_provider()
        if not statuses:  # no lane, or no branch with an upstream: no read at all
            return statuses
        return await asyncio.to_thread(_with_current_fetch_age, self.project_root, statuses)

    async def _read_rows(self) -> tuple[tuple[BranchRecord, ...], str | None]:
        async with self.uow_factory() as uow:
            records = await uow.branches.list_branches()
            default_name = await uow.branches.default_branch_name()
        return records, default_name

    def touch(self, name: str) -> None:
        """Remember a use in memory only: a request never writes (spec §6.4) —
        ``last_used_at`` is the index pass's to persist."""
        self.used_at[name] = self.now()


@dataclass(frozen=True, slots=True)
class NullBranchDirectory:
    """The Null Object for a bundle served without a branch dimension."""

    async def snapshot(self) -> BranchSnapshot:
        return EMPTY_SNAPSHOT

    def touch(self, name: str) -> None:
        return None


__all__ = (
    "EMPTY_SNAPSHOT",
    "BranchDirectory",
    "BranchDirectoryReader",
    "BranchSnapshot",
    "NullBranchDirectory",
    "is_selectable_branch_row",
)
