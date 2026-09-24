"""The maintenance pass over one bundle (spec §6.8a; the start-up half of §6.5's
re-check; #316) and the runner of the operator verbs.

Three units of work, so one failure cannot starve the rest:

1. the landing patch-id cache commits as soon as it is streamed: an id is
   immutable per sha, so a later git error never makes the next pass stream
   the whole lookback again;
2. the merge verdicts and the deleted-ref retirement commit together. A git
   error anywhere in detection leaves every branch row as it was and logs one
   structured ``branch_maintenance_skipped`` event (#316 safety f). The
   deleted-ref retirement waits for the verdicts because a branch whose ref is
   gone may have landed, and it must then retire as MERGED;
3. the grace purge needs no git, so it runs even after a detection error.

The trade-off of (2) being all or nothing: a git failure that recurs on one
candidate (a timeout on a huge diff) holds back every merge and deletion
verdict until it clears; ``branches --retire NAME`` is the manual path.
Detection runs with no unit of work open, so no write transaction waits on
git. Nothing here runs on the request path.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass
from typing import Protocol

from pydocs_mcp.application.branch_policy import BaseBranch
from pydocs_mcp.application.branch_retirement import (
    BranchVerb,
    RetirementPolicy,
    apply_branch_verb,
    apply_merge_verdicts,
    purge_due,
    retire_deleted,
)
from pydocs_mcp.application.merge_detection import (
    LandingIndex,
    MergeVerdict,
    detect_merges,
    load_landing_index,
    merge_candidates,
)
from pydocs_mcp.application.protocols import GitRepository
from pydocs_mcp.git.errors import GitCommandError
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

log = logging.getLogger("pydocs-mcp")


@dataclass(frozen=True, slots=True)
class MaintenanceReport:
    """The branch names one pass moved: ``MERGED``, ``DELETED``, and purged."""

    merged: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    purged: tuple[str, ...] = ()


class BranchMaintenanceRunner(Protocol):
    async def run(self, now: float | None = None) -> MaintenanceReport: ...


@dataclass(frozen=True, slots=True)
class NullBranchMaintenance:
    """Wired when git is off or absent: with no refs to read, an empty listing
    must never read as "every indexed branch was deleted"."""

    async def run(self, now: float | None = None) -> MaintenanceReport:
        return MaintenanceReport()


async def _no_fulltext_rebuild() -> None:
    return None


@dataclass(frozen=True, slots=True)
class _RefSnapshot:
    local_heads: Mapping[str, str]
    # Branches checked out in any worktree are never auto-retired (#316).
    checked_out: frozenset[str]


@dataclass(frozen=True, slots=True)
class _MergeFindings:
    """One detection's verdicts, with the base and the landings they cite."""

    base_name: str
    index: LandingIndex
    verdicts: tuple[MergeVerdict, ...]


def _read_ref_snapshot(git: GitRepository) -> _RefSnapshot:
    checked_out = frozenset(branch for _, branch in git.list_worktrees() if branch)
    return _RefSnapshot(dict(git.list_local_branches()), checked_out)


async def _protected_names(uow: UnitOfWork, checked_out: frozenset[str]) -> frozenset[str]:
    """The checked-out branches plus the served (default) row: never examined,
    retired or purged while checked out (#316 safety b)."""
    name = await uow.branches.default_branch_name()
    return checked_out | {name} if name else checked_out


def _log_report(report: MaintenanceReport) -> None:
    payload = {
        "event": "branch_maintenance",
        "merged": list(report.merged),
        "deleted": list(report.deleted),
        "purged": list(report.purged),
    }
    # An idle pass (every single-branch bundle) stays out of the INFO log.
    level = logging.DEBUG if report == MaintenanceReport() else logging.INFO
    log.log(level, json.dumps(payload))


def _log_skipped(exc: GitCommandError) -> None:
    log.warning(json.dumps({"event": "branch_maintenance_skipped", "error": str(exc)}))


@dataclass(frozen=True, slots=True)
class BranchMaintenance:
    """Merge detection, deleted-ref retirement and the grace purge for one bundle."""

    git: GitRepository
    uow_factory: Callable[[], UnitOfWork]
    # Resolved per pass, inside the git-error guard (``resolve_base_branch``).
    base_resolver: Callable[[GitRepository], BaseBranch | None]
    policy: RetirementPolicy
    lookback: int
    rebuild_fulltext_index: Callable[[], Awaitable[None]] = _no_fulltext_rebuild

    async def run(self, now: float | None = None) -> MaintenanceReport:
        at = time.time() if now is None else now
        try:
            refs = await asyncio.to_thread(_read_ref_snapshot, self.git)
        except GitCommandError as exc:
            # Without the worktree listing nothing is known to be protected.
            _log_skipped(exc)
            return MaintenanceReport()
        merged, deleted = await self._retire_or_skip(refs, at)
        report = MaintenanceReport(merged, deleted, await self._purge_due(refs.checked_out, at))
        if report.purged:
            # Chunk deletes bypass the external-content FTS index (see
            # db.remove_package): without a rebuild, freed rowids keep matching.
            await self.rebuild_fulltext_index()
        _log_report(report)
        return report

    async def _retire_or_skip(
        self, refs: _RefSnapshot, at: float
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        try:
            return await self._retire(refs, at)
        except GitCommandError as exc:
            _log_skipped(exc)
            return (), ()

    async def _retire(
        self, refs: _RefSnapshot, at: float
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The merge verdicts, then the deleted-ref retirement, in one unit of work."""
        findings = await self._find_merges(refs)
        async with self.uow_factory() as uow:
            protected = await _protected_names(uow, refs.checked_out)
            merged = await self._apply_findings(uow, findings, at)
            deleted = await retire_deleted(
                uow, tuple(refs.local_heads), now=at, policy=self.policy, protected=protected
            )
            await uow.commit()
        return merged, deleted

    async def _find_merges(self, refs: _RefSnapshot) -> _MergeFindings | None:
        async with self.uow_factory() as uow:
            protected = await _protected_names(uow, refs.checked_out)
            records = await uow.branches.list_branches()
            landings = await self._landings_if_needed(uow, records, protected)
            await uow.commit()  # the landing cache, before any verdict (module docstring)
        if landings is None:
            return None
        base, index = landings
        verdicts = await asyncio.to_thread(
            detect_merges,
            self.git,
            base,
            records,
            index,
            local_heads=refs.local_heads,
            protected=protected,
        )
        return _MergeFindings(base.name, index, verdicts)

    async def _apply_findings(
        self, uow: UnitOfWork, findings: _MergeFindings | None, at: float
    ) -> tuple[str, ...]:
        if findings is None:
            return ()
        return await apply_merge_verdicts(
            uow,
            findings.verdicts,
            base_name=findings.base_name,
            now=at,
            policy=self.policy,
            index=findings.index,
        )

    async def _purge_due(self, checked_out: frozenset[str], at: float) -> tuple[str, ...]:
        async with self.uow_factory() as uow:
            protected = await _protected_names(uow, checked_out)
            purged = await purge_due(uow, now=at, protected=protected)
            await uow.commit()
        return purged

    async def _landings_if_needed(
        self, uow: UnitOfWork, records: tuple[BranchRecord, ...], protected: Collection[str]
    ) -> tuple[BaseBranch, LandingIndex] | None:
        base = await self._base_if_needed(records, protected)
        if base is None:
            return None
        return base, await load_landing_index(self.git, uow, base, self.lookback)

    async def _base_if_needed(
        self, records: tuple[BranchRecord, ...], protected: Collection[str]
    ) -> BaseBranch | None:
        """The base, only when some row can be examined against it: a bundle
        holding just its checked-out branch reads no base and streams no
        landing, so its index pass stays what it was before #316."""
        if not merge_candidates(records, protected=protected):
            return None
        base = await asyncio.to_thread(self.base_resolver, self.git)
        if base is None:
            return None
        candidates = merge_candidates(records, protected=protected, base_name=base.name)
        return base if candidates else None


@dataclass(frozen=True, slots=True)
class BranchVerbRunner:
    """One operator verb (spec §6.9) in its own unit of work."""

    uow_factory: Callable[[], UnitOfWork]
    policy: RetirementPolicy
    rebuild_fulltext_index: Callable[[], Awaitable[None]] = _no_fulltext_rebuild

    async def run(self, verb: BranchVerb, name: str, now: float | None = None) -> str:
        """Apply ``verb`` to ``name``; the confirmation line the CLI prints.
        Raises :class:`BranchVerbError` for an unknown or refused name."""
        at = time.time() if now is None else now
        async with self.uow_factory() as uow:
            freed = await apply_branch_verb(uow, verb, name, now=at, policy=self.policy)
            await uow.commit()
        if freed:
            await self.rebuild_fulltext_index()
        return f"branches: {verb.value} {name}"


__all__ = (
    "BranchMaintenance",
    "BranchMaintenanceRunner",
    "BranchVerbRunner",
    "MaintenanceReport",
    "NullBranchMaintenance",
)
