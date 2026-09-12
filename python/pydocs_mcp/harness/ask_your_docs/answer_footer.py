"""The answer footer and the follow-up chips (UI spec §6.8–§6.9).

Pure and deterministic: observations in, one caption line and at most
``len(FollowUpKind)`` chips out. Streamlit rendering lives in scope_panel.py.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    SLICE_LABELS,
    QuestionScope,
    ScopeCell,
    ScopeKind,
    ScopeSlice,
    code_compatible_with_slice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    BranchOrigin,
    CellObservation,
    ScopeObservations,
)
from pydocs_mcp.models import BranchStatus

ORIGIN_LABELS: dict[BranchOrigin, str] = {
    BranchOrigin.DEFAULT: "default",
    BranchOrigin.PINNED: "pinned",
    BranchOrigin.AGENT_CHOSEN: "agent-chosen",
    BranchOrigin.SERVER: "server default",
}
# When one cell mixes origins (a model-passed branch equal to the default on
# one call, omitted on another), the most specific one names the segment.
_ORIGIN_PRECEDENCE = (
    BranchOrigin.PINNED,
    BranchOrigin.AGENT_CHOSEN,
    BranchOrigin.DEFAULT,
    BranchOrigin.SERVER,
)
NO_BRANCH = "no branch"
ALL_PROJECTS = "all projects"
_TOMBSTONE_STATUSES = (BranchStatus.MERGED, BranchStatus.DELETED)


# --- footer ------------------------------------------------------------------


def _shown_project(
    project: str, meta: Mapping[str, object], listing: WorkspaceBranchListing
) -> str:
    """A union request spans every bundle while ``meta.project`` names only the
    first loaded one — so a multi-project listing reads ``all projects``."""
    if project:
        return project
    if listing.project_count > 1:
        return ALL_PROJECTS
    return str(meta.get("project") or "") or ALL_PROJECTS


def _origin_text(records: tuple[CellObservation, ...]) -> str:
    if any(r.replaced for r in records):
        return "agent-chosen → default"
    origin = min((r.branch_origin for r in records), key=_ORIGIN_PRECEDENCE.index)
    return ORIGIN_LABELS[origin]


def _slice_text(records: tuple[CellObservation, ...]) -> str:
    slices = sorted({r.slice for r in records}, key=list(ScopeSlice).index)
    return ", ".join(SLICE_LABELS[s] for s in slices)


def _segment(
    cell: tuple[str, str], records: tuple[CellObservation, ...], listing: WorkspaceBranchListing
) -> str:
    project, branch = cell
    meta = records[0].meta
    shown_branch = branch or str(meta.get("branch") or "") or NO_BRANCH
    # The listing's sha is exact per bundle and branch when a cell was sent;
    # otherwise the server's probe (bundle #1 on multi-bundle servers, E6).
    sha = listing.head_sha(project, branch) if project and branch else ""
    sha = sha or str(meta.get("indexed_git_head") or "")
    head = f"answered from {_shown_project(project, meta, listing)} · {shown_branch}"
    parts = [f"{head} @{sha[:7]}" if sha else head]
    if shown_branch != NO_BRANCH:  # slices are branch-relative
        parts.append(_slice_text(records))
    parts.append(_origin_text(records))
    if any(bool(r.meta.get("index_stale")) for r in records):
        parts.append("index stale")  # R10: never hidden
    return " · ".join(parts)


def render_answer_footer(observations: ScopeObservations, listing: WorkspaceBranchListing) -> str:
    """One caption line: a segment per distinct sent cell, sorted, joined by `` | ``."""
    groups = observations.by_cell()
    if not groups:
        return "answered without tool calls"
    return " | ".join(_segment(cell, records, listing) for cell, records in groups.items())


# --- chips -------------------------------------------------------------------


class FollowUpKind(StrEnum):
    COMPARE_WITH = "compare_with"
    SHOW_DIFF = "show_diff"
    PIN_BRANCH = "pin_branch"


@dataclass(frozen=True, slots=True)
class FollowUpChip:
    kind: FollowUpKind
    label: str
    project: str
    branches: tuple[str, ...]
    slice: ScopeSlice
    question: str  # "" for PIN_BRANCH (sends nothing)


def _resolved_cell(record: CellObservation, listing: WorkspaceBranchListing) -> ScopeCell | None:
    """The cell a record answered from, or None when it cannot be named
    unambiguously (a union answer on a multi-project workspace)."""
    single = listing.project_names[0] if listing.project_count == 1 else ""
    project = record.project or single
    branch = record.branch or str(record.meta.get("branch") or "")
    return ScopeCell(project, branch) if project and branch else None


def answered_cells(
    observations: ScopeObservations, listing: WorkspaceBranchListing
) -> dict[ScopeCell, tuple[CellObservation, ...]]:
    grouped: dict[ScopeCell, list[CellObservation]] = {}
    for record in observations.records():
        cell = _resolved_cell(record, listing)
        if cell is not None:
            grouped.setdefault(cell, []).append(record)
    ordered = sorted(grouped, key=lambda c: (c.project, c.branch))
    return {cell: tuple(grouped[cell]) for cell in ordered}


def _compare_chip(
    cell: ScopeCell, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> FollowUpChip | None:
    if not capabilities.branch_selector:
        return None
    row = listing.row(cell.project, cell.branch)
    base = row.base_name if row else None
    if not base or base == cell.branch or not listing.has_branch(cell.project, base):
        return None
    return FollowUpChip(
        kind=FollowUpKind.COMPARE_WITH,
        label=f"compare with {base}",
        project=cell.project,
        branches=(cell.branch, base),
        slice=ScopeSlice.WHOLE_BRANCH,
        question=f"Compare the previous answer between {cell.branch} and {base}: what differs?",
    )


def _diff_target(cell: ScopeCell, listing: WorkspaceBranchListing) -> str:
    """The branch a "show the diff" chip pins: the cell's branch while it is
    pickable, a merged tombstone's landing sha, "" when neither."""
    row = listing.row(cell.project, cell.branch)
    if row is None:
        return ""
    # A merged tombstone answers scope=diff through its landing sha only.
    if row.status in _TOMBSTONE_STATUSES and row.merged_into:
        return str(row.merged_into)
    return cell.branch if row in listing.pickable(cell.project) else ""


def _show_diff_chip(
    cell: ScopeCell,
    records: tuple[CellObservation, ...],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> FollowUpChip | None:
    if not capabilities.diff_slice or any(r.slice is ScopeSlice.DIFF_HUNKS for r in records):
        return None
    target = _diff_target(cell, listing)
    if not target:
        return None
    return FollowUpChip(
        kind=FollowUpKind.SHOW_DIFF,
        label="show the diff",
        project=cell.project,
        branches=(target,),
        slice=ScopeSlice.DIFF_HUNKS,
        question="Show the diff hunks behind the previous answer.",
    )


def _pin_chip_wanted(
    cell: ScopeCell, records: tuple[CellObservation, ...], kept_pin: QuestionScope | None
) -> bool:
    """A cell already pinned (by origin or by the kept pin) has nothing to pin."""
    if all(r.branch_origin is BranchOrigin.PINNED for r in records):
        return False
    return kept_pin is None or cell not in kept_pin.cells


def _pin_chip(cell: ScopeCell) -> FollowUpChip:
    return FollowUpChip(
        kind=FollowUpKind.PIN_BRANCH,
        label=f"pin {cell.branch}",
        project=cell.project,
        branches=(cell.branch,),
        slice=ScopeSlice.WHOLE_BRANCH,
        question="",
    )


def _pin_chips(
    cells: dict[ScopeCell, tuple[CellObservation, ...]],
    kept_pin: QuestionScope | None,
    capabilities: ScopeCapabilities,
) -> tuple[FollowUpChip, ...]:
    if not capabilities.branch_selector:
        return ()
    return tuple(
        _pin_chip(cell)
        for cell, records in cells.items()
        if _pin_chip_wanted(cell, records, kept_pin)
    )


def derive_follow_up_chips(
    observations: ScopeObservations,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    kept_pin: QuestionScope | None,
) -> tuple[FollowUpChip, ...]:
    """At most one chip per kind (UI spec §6.9); the cap is the member count."""
    cells = answered_cells(observations, listing)
    chips: list[FollowUpChip] = []
    if len(cells) == 1:
        ((cell, records),) = cells.items()
        compare = _compare_chip(cell, listing, capabilities)
        diff = _show_diff_chip(cell, records, listing, capabilities)
        chips.extend(c for c in (compare, diff) if c is not None)
    chips.extend(_pin_chips(cells, kept_pin, capabilities))
    return tuple(chips[: len(FollowUpKind)])


def _grown_kept_pin(
    cells: tuple[ScopeCell, ...], kept_pin: QuestionScope | None, defaults: QuestionScope
) -> QuestionScope:
    """The kept pin plus ``cells``, or a fresh pin over them carrying the
    session defaults' slice / code / package."""
    if kept_pin is not None:
        return kept_pin.with_cells(cells)
    return QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=defaults.slice,
        code=defaults.code,
        package=defaults.package,
    )


def apply_follow_up_chip(
    chip: FollowUpChip, kept_pin: QuestionScope | None, defaults: QuestionScope
) -> tuple[str | None, QuestionScope | None]:
    """(question to send, pin to send it under). COMPARE_WITH / SHOW_DIFF build
    a one-shot pin and leave the kept pin alone; PIN_BRANCH returns no question
    and the kept pin grown by the cell (slice / code / package from ``defaults``)."""
    cells = tuple(ScopeCell(chip.project, branch) for branch in chip.branches)
    if chip.kind is FollowUpKind.PIN_BRANCH:
        return None, _grown_kept_pin(cells, kept_pin, defaults)
    one_shot = QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=chip.slice,
        code=code_compatible_with_slice(chip.slice, defaults.code),
        package=defaults.package,
    )
    return chip.question, one_shot


__all__ = (
    "ALL_PROJECTS",
    "NO_BRANCH",
    "ORIGIN_LABELS",
    "FollowUpChip",
    "FollowUpKind",
    "answered_cells",
    "apply_follow_up_chip",
    "derive_follow_up_chips",
    "render_answer_footer",
)
