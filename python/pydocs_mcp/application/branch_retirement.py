"""Branch retirement (spec §6.8a, §6.5b; #316): the soft record, hard rows after
the grace window, the landing-unit link at the ``MERGED`` transition, and the
four operator verbs.

Functions over an OPEN ``uow``: the maintenance pass and the verb runner
(``branch_maintenance.py``) own the transaction.
"""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from pydocs_mcp.application.branch_manifest import SHORT_SHA_LEN
from pydocs_mcp.application.branch_membership import purge_branch_rows
from pydocs_mcp.application.merge_detection import (
    LandingIndex,
    MergeVerdict,
    is_live_branch_row,
)
from pydocs_mcp.models import BranchIndexSource, BranchSlice, BranchStatus, LandingKind
from pydocs_mcp.retrieval.config.git_models import BranchRetentionConfig
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

_MERGE_PARENT_COUNT = 2
# The grace purge empties the two retirements plus the operator's manual
# retire, which leaves the row INACTIVE — queryable until purged (spec §6.8a).
_PURGEABLE = frozenset({BranchStatus.INACTIVE, BranchStatus.MERGED, BranchStatus.DELETED})


class BranchVerb(StrEnum):
    """The ``pydocs-mcp branches`` verbs (spec §6.9), spelled ``--<verb> NAME``."""

    RETIRE = "retire"
    PURGE = "purge"
    PIN = "pin"
    UNPIN = "unpin"


class BranchVerbError(KeyError):
    """A verb named no indexed branch, or a row it must not act on.

    A ``KeyError`` — the plan's contract for an unknown name — whose text
    prints without the quotes ``KeyError.__str__`` adds.
    """

    def __str__(self) -> str:
        return str(self.args[0]) if self.args else ""


@dataclass(frozen=True, slots=True)
class RetirementPolicy:
    """``git.branches.retention`` as the retirement functions read it."""

    grace_days: int
    auto_retire_merged: bool
    auto_retire_deleted: bool

    @classmethod
    def from_config(cls, retention: BranchRetentionConfig) -> RetirementPolicy:
        return cls(
            retention.grace_days, retention.auto_retire_merged, retention.auto_retire_deleted
        )

    def purge_after(self, now: float) -> float:
        return now + timedelta(days=self.grace_days).total_seconds()


def _retired(
    record: BranchRecord, status: BranchStatus, now: float, policy: RetirementPolicy
) -> BranchRecord:
    return replace(record, status=status, retired_at=now, purge_after=policy.purge_after(now))


async def _indexed_names(uow: UnitOfWork) -> str:
    names = sorted(r.name for r in await uow.branches.list_branches() if not r.is_landing_unit)
    return ", ".join(names) or "(none)"


async def _require(uow: UnitOfWork, name: str) -> BranchRecord:
    record = await uow.branches.get_branch(name)
    if record is None:
        raise BranchVerbError(f"no indexed branch {name!r}; indexed: {await _indexed_names(uow)}")
    return record


# ── The MERGED transition and its landing unit ──


def _landing_kind(parent_count: int, snapshot: tuple[str, str] | None) -> LandingKind:
    if snapshot is not None:
        return LandingKind.LINEAR_SNAPSHOT
    if parent_count >= _MERGE_PARENT_COUNT:
        return LandingKind.MERGE_COMMIT
    return LandingKind.SINGLE_COMMIT


def _pre_landing_sha(verdict: MergeVerdict, parents: Sequence[str]) -> str | None:
    """The base just before the landing: a snapshot's ``pre``, else the first parent."""
    if verdict.snapshot is not None:
        return verdict.snapshot[0]
    return parents[0] if parents else None


def _landing_unit_record(
    verdict: MergeVerdict, index: LandingIndex, branch: BranchRecord, now: float
) -> BranchRecord:
    """The unit row of spec §6.5b Storage: keyed by the landing (``post``) sha."""
    step = index.step(verdict.landing_sha)
    parents = step.parent_shas if step is not None else ()
    return BranchRecord(
        name=verdict.landing_sha,
        head_sha=verdict.landing_sha,
        source=BranchIndexSource.GIT_OBJECTS,
        pipeline_hash=branch.pipeline_hash,
        indexed_at=now,
        last_used_at=now,
        base_name=branch.merged_into,
        merge_base_sha=_pre_landing_sha(verdict, parents),
        landing_kind=_landing_kind(len(parents), verdict.snapshot),
        landed_at=step.landed_at if step is not None else None,
    )


async def _link_landing_unit(
    uow: UnitOfWork, verdict: MergeVerdict, index: LandingIndex, merged: BranchRecord, now: float
) -> None:
    existing = await uow.branches.get_branch(verdict.landing_sha)
    if existing is not None and not existing.is_landing_unit:
        # A BRANCH literally named like the sha keeps its row and gets no copy.
        return
    if existing is None:
        await uow.branches.upsert_branch(_landing_unit_record(verdict, index, merged, now))
    # Link, never overwrite: a unit may already exist (another branch landed in
    # the same commit, a pin, P2's history walk). Coexistence (§6.5b): the unit
    # inherits the branch's DIFF rows by content — zero rows until P2 generates
    # the slice — so nothing re-embeds.
    await uow.branch_chunks.copy_membership(
        verdict.branch, verdict.landing_sha, slice=BranchSlice.DIFF
    )


async def _apply_verdict(
    uow: UnitOfWork,
    verdict: MergeVerdict,
    *,
    base_name: str,
    now: float,
    policy: RetirementPolicy,
    index: LandingIndex,
) -> bool:
    record = await _require(uow, verdict.branch)
    stamped = replace(record, merge_evidence=verdict.evidence, landing_sha=verdict.landing_sha)
    if record.pinned or not policy.auto_retire_merged:
        # Evidence is stamped either way and shown on the card (spec §6.8a).
        await uow.branches.upsert_branch(stamped)
        return False
    # O18: merged_into keeps the base NAME; the landing sha has its own column.
    merged = replace(_retired(stamped, BranchStatus.MERGED, now, policy), merged_into=base_name)
    await uow.branches.upsert_branch(merged)
    await _link_landing_unit(uow, verdict, index, merged, now)
    return True


async def apply_merge_verdicts(
    uow: UnitOfWork,
    verdicts: Sequence[MergeVerdict],
    *,
    base_name: str,
    now: float,
    policy: RetirementPolicy,
    index: LandingIndex,
) -> tuple[str, ...]:
    """Stamp every verdict's evidence; move unpinned rows to ``MERGED`` when the
    policy allows, creating or linking the landing unit. Returns the moved names."""
    transitioned: list[str] = []
    for verdict in verdicts:
        moved = await _apply_verdict(
            uow, verdict, base_name=base_name, now=now, policy=policy, index=index
        )
        if moved:
            transitioned.append(verdict.branch)
    return tuple(transitioned)


# ── Deleted refs and the grace purge ──


def _is_retirable_when_gone(record: BranchRecord) -> bool:
    return is_live_branch_row(record) and not record.pinned


async def retire_deleted(
    uow: UnitOfWork,
    local_branch_names: Sequence[str],
    *,
    now: float,
    policy: RetirementPolicy,
    protected: Collection[str] = (),
) -> tuple[str, ...]:
    """``DELETED`` for live branch rows whose local ref is gone (spec §6.8a).

    Never a pinned, ``protected`` (checked-out — an unborn branch has no ref
    yet) or landing-unit row, the non-git sentinel or a detached row (#316).
    """
    if not policy.auto_retire_deleted:
        return ()
    kept = {*local_branch_names, *protected}
    records = await uow.branches.list_branches()
    gone = [r for r in records if _is_retirable_when_gone(r) and r.name not in kept]
    for record in gone:
        await uow.branches.upsert_branch(_retired(record, BranchStatus.DELETED, now, policy))
    return tuple(record.name for record in gone)


def _is_purge_due(record: BranchRecord, now: float, protected: Collection[str]) -> bool:
    if record.is_landing_unit or record.pinned or record.name in protected:
        return False
    due = record.purge_after is not None and record.purge_after <= now
    return due and record.status in _PURGEABLE


async def _owns_rows(uow: UnitOfWork, name: str) -> bool:
    if await uow.branch_chunks.count_for_branch(name):
        return True
    return await uow.branches.count_files(name) > 0


async def purge_due(
    uow: UnitOfWork, *, now: float, protected: Collection[str] = ()
) -> tuple[str, ...]:
    """Hard-delete the rows of every retired branch past its grace window (spec
    §6.8a purge), then the refcount GC; the record stays as the tombstone.
    Pinned and ``protected`` rows are spared; an already purged row is skipped."""
    purged: list[str] = []
    for record in await uow.branches.list_branches():
        if _is_purge_due(record, now, protected) and await _owns_rows(uow, record.name):
            await purge_branch_rows(uow, record.name)
            purged.append(record.name)
    return tuple(purged)


# ── The operator verbs (spec §6.9) ──


async def _refuse_outside_the_lifecycle(
    uow: UnitOfWork, verb: BranchVerb, record: BranchRecord
) -> None:
    if record.is_landing_unit:
        raise BranchVerbError(
            f"refusing to {verb.value} {record.name!r}: it is a landing unit; "
            "only pin and unpin take a landing sha"
        )
    # The served (checked-out) row: the next pass's cache check compares only
    # its stamped head, so a retired or purged checkout would be served with
    # no index until its files change (#316).
    if record.name == await uow.branches.default_branch_name():
        raise BranchVerbError(
            f"refusing to {verb.value} the checked-out branch {record.name!r}; "
            "check out another branch first"
        )


def _manually_retired(record: BranchRecord, now: float, purge_after: float) -> BranchRecord:
    # The operator's retire keeps a row queryable until purged, not refreshed
    # (INACTIVE); a row already MERGED or DELETED keeps its status.
    status = BranchStatus.INACTIVE if record.status is BranchStatus.ACTIVE else record.status
    return replace(record, status=status, retired_at=now, purge_after=purge_after)


async def retire_branch(
    uow: UnitOfWork, name: str, *, now: float, policy: RetirementPolicy
) -> None:
    record = await _require(uow, name)
    await _refuse_outside_the_lifecycle(uow, BranchVerb.RETIRE, record)
    await uow.branches.upsert_branch(_manually_retired(record, now, policy.purge_after(now)))


async def purge_branch(uow: UnitOfWork, name: str, *, now: float) -> tuple[int, ...]:
    """Purge at once (spec §6.8a); returns the freed chunk ids. A live row
    becomes INACTIVE: it has no index left to serve."""
    record = await _require(uow, name)
    await _refuse_outside_the_lifecycle(uow, BranchVerb.PURGE, record)
    if record.status is BranchStatus.ACTIVE:
        await uow.branches.upsert_branch(_manually_retired(record, now, now))
    return await purge_branch_rows(uow, name)


async def set_pinned(uow: UnitOfWork, name: str, pinned: bool) -> None:
    """Pin or unpin a branch or a landing unit (exempt from every automatic removal)."""
    record = await _require(uow, name)
    await uow.branches.upsert_branch(replace(record, pinned=pinned))


async def apply_branch_verb(
    uow: UnitOfWork, verb: BranchVerb, name: str, *, now: float, policy: RetirementPolicy
) -> tuple[int, ...]:
    """Run one verb; returns the chunk ids a purge freed (``()`` otherwise)."""
    if verb is BranchVerb.PURGE:
        return await purge_branch(uow, name, now=now)
    if verb is BranchVerb.RETIRE:
        await retire_branch(uow, name, now=now, policy=policy)
    else:
        await set_pinned(uow, name, verb is BranchVerb.PIN)
    return ()


def retired_branch_message(record: BranchRecord) -> str:
    """The error text for a request naming a retired branch (spec §6.8a)."""
    when = time.strftime("%Y-%m-%d", time.gmtime(record.retired_at or 0.0))
    reindex = f"pydocs-mcp index . --branch {record.name}"
    if record.status is BranchStatus.MERGED:
        landing = (record.landing_sha or "")[:SHORT_SHA_LEN]
        return (
            f"branch {record.name!r} was merged into {record.merged_into} at {landing} "
            f"({when}); its index was retired. Search {record.merged_into}, or run: {reindex}"
        )
    if record.status is BranchStatus.DELETED:
        return (
            f"branch {record.name!r} was deleted locally ({when}); its index was retired. "
            f"Run: {reindex} after recreating it"
        )
    return f"branch {record.name!r} was retired ({when}); run: {reindex}"


__all__ = (
    "BranchVerb",
    "BranchVerbError",
    "RetirementPolicy",
    "apply_branch_verb",
    "apply_merge_verdicts",
    "purge_branch",
    "purge_due",
    "retire_branch",
    "retire_deleted",
    "retired_branch_message",
    "set_pinned",
)
