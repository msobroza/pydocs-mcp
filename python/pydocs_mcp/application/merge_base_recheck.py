"""The merge-base re-check job (spec §6.5 re-check rule, §6.8; #317).

A base-tip move — a local commit on the base, a fetch that moves its
remote-tracking ref — re-checks every live branch: one ``merge_base`` per
branch, no parse, no embedding. Where a branch's ``(base_name, merge_base_sha)``
stamp moved, the row is re-stamped: the "repair a drifted base" #308 left to this
job (its full-pass stamp is never rewritten by a cached pass, and a base can move
while the head does not). Then the #316 maintenance runs — merge detection,
deleted-ref retirement, the grace purge — over the SAME base, resolved once per
job (#308 review: a second resolution logs ``base_branch_unresolved`` twice).

Git runs with no unit of work open, so no write transaction waits on it (the
#316 rule). Nothing here runs on the request path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace

from pydocs_mcp.application.branch_maintenance import BranchMaintenanceRunner, MaintenanceReport
from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.models import LIVE_BRANCH_STATUSES
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")

BaseResolver = Callable[[GitRepository], BaseBranch | None]
# name -> (the head the merge-base was computed against, that merge-base).
_MergeBases = Mapping[str, tuple[str, str]]


def _is_rechecked(record: BranchRecord) -> bool:
    """A live branch with a commit: retired rows keep the base they retired
    under, a landing unit carries no base, and the non-git row has no head."""
    return (
        record.status in LIVE_BRANCH_STATUSES
        and not record.is_landing_unit
        and bool(record.head_sha)
    )


def _merge_bases(git: GitRepository, base: BaseBranch, rows: Sequence[BranchRecord]) -> _MergeBases:
    # "" = no common ancestor with the base (an orphan branch, spec §6.5), the
    # value read_base_stamp stamps on a full pass.
    return {r.name: (r.head_sha, git.merge_base(base.tip_sha, r.head_sha) or "") for r in rows}


def _restamped(
    current: BranchRecord | None, head: str, base_name: str, merge_base: str
) -> BranchRecord | None:
    """The re-stamped row, or ``None`` when nothing moved — or when the head moved
    since the merge-base was computed: the pass that moved it stamped its own."""
    if current is None or current.head_sha != head:
        return None
    if (current.base_name, current.merge_base_sha) == (base_name, merge_base):
        return None
    return replace(current, base_name=base_name, merge_base_sha=merge_base)


async def refresh_base_stamps(
    git: GitRepository, uow_factory: Callable[[], UnitOfWork], base: BaseBranch
) -> tuple[str, ...]:
    """Re-stamp every live branch whose base pair moved; the names re-stamped.

    Raises ``GitCommandError`` before any write: a failed read never replaces a
    good stamp (#308's data-loss path).
    """
    async with uow_factory() as uow:
        rows = tuple(r for r in await uow.branches.list_branches() if _is_rechecked(r))
    merge_bases = await asyncio.to_thread(_merge_bases, git, base, rows)
    async with uow_factory() as uow:
        moved = await _write_moved_stamps(uow, base.name, merge_bases)
        await uow.commit()
    return moved


async def _write_moved_stamps(
    uow: UnitOfWork, base_name: str, merge_bases: _MergeBases
) -> tuple[str, ...]:
    moved: list[str] = []
    for name, (head, merge_base) in merge_bases.items():
        record = _restamped(await uow.branches.get_branch(name), head, base_name, merge_base)
        if record is not None:
            await uow.branches.upsert_branch(record)
            moved.append(name)
    return tuple(moved)


@dataclass(frozen=True, slots=True)
class _ResolvedBase:
    """One job's base read, replayed to the maintenance instead of re-resolved."""

    base: BaseBranch | None
    error: GitCommandError | None = None

    def replay(self, git: GitRepository) -> BaseBranch | None:
        # A failed read is re-raised, never turned into "no base": the
        # maintenance then skips its verdicts instead of retiring a gone ref
        # that may have landed as DELETED (#316 safety f).
        if self.error is not None:
            raise self.error
        return self.base


def _log_restamp(moved: tuple[str, ...]) -> None:
    level = logging.INFO if moved else logging.DEBUG
    log.log(level, json.dumps({"event": "base_restamped", "branches": list(moved)}))


@dataclass(frozen=True, slots=True)
class MergeBaseRecheck:
    """Re-stamp every live branch's base, then run the #316 maintenance."""

    git: GitRepository
    uow_factory: Callable[[], UnitOfWork]
    base_resolver: BaseResolver
    # Builds the maintenance around this job's resolved base (the composition
    # root closes over its write set and policy).
    maintenance_for: Callable[[BaseResolver], BranchMaintenanceRunner]

    async def run(self, now: float | None = None) -> MaintenanceReport:
        resolved = await self._resolve_base()
        if resolved.base is not None:
            await self._restamp_or_skip(resolved.base)
        return await self.maintenance_for(resolved.replay).run(now)

    async def _resolve_base(self) -> _ResolvedBase:
        try:
            return _ResolvedBase(await asyncio.to_thread(self.base_resolver, self.git))
        except GitCommandError as exc:
            return _ResolvedBase(None, exc)

    async def _restamp_or_skip(self, base: BaseBranch) -> None:
        try:
            _log_restamp(await refresh_base_stamps(self.git, self.uow_factory, base))
        except GitCommandError as exc:
            log.warning(json.dumps({"event": "base_restamp_skipped", "error": str(exc)}))


__all__ = ("BaseResolver", "MergeBaseRecheck", "refresh_base_stamps")
