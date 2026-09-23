"""Read side of the ``branches`` CLI verb (spec §6.9): one summary per indexed branch,
with its base beside it once a pass has stamped one (#308), and a merged branch's
base and landing in its status (#316)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from pydocs_mcp.application.branch_manifest import SHORT_SHA_LEN
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.storage.branch_records import BranchRecord
from pydocs_mcp.storage.protocols import UnitOfWork

_HEADER = ("branch", "status", "head", "indexed", "files", "chunks")
_BASE_HEADER = "base"
_EMPTY_CELL = "-"
_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_DAY = 86400


@dataclass(frozen=True, slots=True)
class BranchSummary:
    """One rendered row of the ``branches`` table: identity, base, and its two counts."""

    name: str
    status: BranchStatus
    head_sha: str
    indexed_at: float
    is_default: bool
    file_count: int
    chunk_count: int
    # #308: the stamped base branch; None when no base resolved.
    base_name: str | None = None
    # #316: set on a MERGED row — the base name (O18) and the landing sha.
    merged_into: str | None = None
    landing_sha: str | None = None


async def _summary(uow: UnitOfWork, record: BranchRecord) -> BranchSummary:
    return BranchSummary(
        name=record.name,
        status=record.status,
        head_sha=record.head_sha,
        indexed_at=record.indexed_at,
        is_default=record.is_default,
        file_count=await uow.branches.count_files(record.name),
        chunk_count=await uow.branch_chunks.count_for_branch(record.name),
        base_name=record.base_name,
        merged_into=record.merged_into,
        landing_sha=record.landing_sha,
    )


async def list_branch_summaries(
    uow_factory: Callable[[], UnitOfWork],
) -> tuple[BranchSummary, ...]:
    """Summarize every branch stamped in the bundle, default-first then by name.

    Landing units (rows keyed by a landing sha, spec §6.5b) are not branches
    and are not listed; P2's retention window lists them under "landed".
    """
    async with uow_factory() as uow:
        records = await uow.branches.list_branches()
        summaries = [await _summary(uow, r) for r in records if not r.is_landing_unit]
    return tuple(summaries)


def _age_label(seconds: float) -> str:
    """Coarsest single-unit age — operators scan for staleness, not precision."""
    if seconds < _SECONDS_PER_HOUR:
        return f"{int(seconds // _SECONDS_PER_MINUTE)}m"
    if seconds < _SECONDS_PER_DAY:
        return f"{int(seconds // _SECONDS_PER_HOUR)}h"
    return f"{int(seconds // _SECONDS_PER_DAY)}d"


def _status_label(summary: BranchSummary) -> str:
    """``merged into main @3e1a9c2`` for a merged branch (#316); the status otherwise."""
    if summary.status is not BranchStatus.MERGED or not summary.merged_into:
        return summary.status.value
    landing = f" @{summary.landing_sha[:SHORT_SHA_LEN]}" if summary.landing_sha else ""
    return f"merged into {summary.merged_into}{landing}"


def _summary_row(summary: BranchSummary, now: float) -> tuple[str, ...]:
    return (
        f"{'*' if summary.is_default else ' '} {summary.name}",
        _status_label(summary),
        summary.head_sha[:SHORT_SHA_LEN] or _EMPTY_CELL,
        f"{_age_label(max(0.0, now - summary.indexed_at))} ago",
        str(summary.file_count),
        str(summary.chunk_count),
    )


def _table_rows(summaries: tuple[BranchSummary, ...], now: float) -> list[tuple[str, ...]]:
    rows = [_HEADER, *(_summary_row(summary, now) for summary in summaries)]
    # The base sits beside the branch name, and only once some branch carries
    # one: a bundle with no base stamped lists exactly as before (#308).
    if not any(summary.base_name for summary in summaries):
        return rows
    bases = [_BASE_HEADER, *(summary.base_name or _EMPTY_CELL for summary in summaries)]
    return [(row[0], base, *row[1:]) for row, base in zip(rows, bases, strict=True)]


def format_branch_summaries(summaries: tuple[BranchSummary, ...], now: float) -> str:
    """Plain-text table for the CLI; ``*`` marks the default branch.

    ``now`` is a parameter, not a ``time.time()`` call, so the rendering stays
    pure and testable without freezing the clock.
    """
    rows = _table_rows(summaries, now)
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in rows
    )


__all__ = ("BranchSummary", "format_branch_summaries", "list_branch_summaries")
