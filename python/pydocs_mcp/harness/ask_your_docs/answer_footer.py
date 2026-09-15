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
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.harness.ask_your_docs.scope_interceptor import (
    BranchOrigin,
    CellObservation,
    ScopeObservations,
)
from pydocs_mcp.harness.ask_your_docs.scope_tokens import (
    BRANCH_TOKEN_PREFIX,
    PROJECT_TOKEN_PREFIX,
)
from pydocs_mcp.harness.ask_your_docs.strip_state import StripState, StripTarget
from pydocs_mcp.models import BranchStatus
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

ORIGIN_LABELS: dict[BranchOrigin, str] = {
    BranchOrigin.DEFAULT: "your default",
    BranchOrigin.PINNED: "only these",
    BranchOrigin.AGENT_CHOSEN: "the agent's choice",
    BranchOrigin.SERVER: "the server's default",
}
FRESH = "index up to date"
BEHIND = "index behind your checkout — reindex to search it"
_SEARCHED = "Searched "
# The shipped defaults, read once: the pages pass the deployment's real config and cap
# (never a repeated literal — CLAUDE.md §Default values).
_SHIPPED_SCOPE_DEFAULTS = ScopeDefaultsConfig()
_SHIPPED_MAX_CELLS = _SHIPPED_SCOPE_DEFAULTS.max_cells
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
        return f"{ORIGIN_LABELS[BranchOrigin.AGENT_CHOSEN]} → {ORIGIN_LABELS[BranchOrigin.DEFAULT]}"
    origin = min((r.branch_origin for r in records), key=_ORIGIN_PRECEDENCE.index)
    return ORIGIN_LABELS[origin]


def _slice_text(records: tuple[CellObservation, ...]) -> str:
    """Distinct non-default slices in enum order; "" when every call ran the default."""
    slices = sorted(
        {r.slice for r in records} - {ScopeSlice.WHOLE_BRANCH}, key=list(ScopeSlice).index
    )
    return ", ".join(SLICE_LABELS[s] for s in slices)


def _segment_sha(
    project: str, branch: str, meta: Mapping[str, object], listing: WorkspaceBranchListing
) -> str:
    """The listing's sha is exact per bundle and branch when a cell was sent; otherwise
    the server's probe (bundle #1 on multi-bundle servers, E6); "" when neither."""
    sha = listing.head_sha(project, branch) if project and branch else ""
    return sha or str(meta.get("indexed_git_head") or "")


def _segment(
    cell: tuple[str, str], records: tuple[CellObservation, ...], listing: WorkspaceBranchListing
) -> str:
    project, branch = cell
    meta = records[0].meta
    shown_branch = branch or str(meta.get("branch") or "") or NO_BRANCH
    head = f"{_shown_project(project, meta, listing)} · {shown_branch}"
    sha = _segment_sha(project, branch, meta, listing)
    origin = f"({_origin_text(records)})"
    parts = [f"{head} @{sha[:7]} {origin}" if sha else f"{head} {origin}"]
    slices = _slice_text(records)
    if shown_branch != NO_BRANCH and slices:  # slices are branch-relative
        parts.append(slices)
    stale = any(bool(r.meta.get("index_stale")) for r in records)
    parts.append(BEHIND if stale else FRESH)  # R10: never hidden, in either state
    return " · ".join(parts)


def _unsearched_project(
    groups: Mapping[tuple[str, str], object], listing: WorkspaceBranchListing
) -> str:
    """The first listed project no cell of the answer searched, "" when none.

    A cell whose project is "" is a union request and searched EVERY listed project,
    so a union answer never names one (UI spec §6.8).
    """
    if any(not project for project, _ in groups):
        return ""
    searched = {project for project, _ in groups}
    return next((p for p in listing.project_names if p not in searched), "")


def _indexed_base_of_one_cell(
    groups: Mapping[tuple[str, str], object],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> str:
    """The base branch the U1 ``on:`` hint teaches: the single answered cell's own base
    when the listing indexes it; "" whenever the hint does not apply."""
    if not capabilities.branch_selector or len(groups) != 1:
        return ""
    ((project, branch),) = groups
    row = listing.row(project, branch) if project else None
    base = str(row.base_name or "") if row else ""
    if not base or base == branch or not listing.has_branch(project, base):
        return ""
    return base


def _teaching_hint(
    groups: Mapping[tuple[str, str], object],
    listing: WorkspaceBranchListing,
    config: ScopeDefaultsConfig,
    capabilities: ScopeCapabilities,
) -> str:
    """The typed-token hint, taught at the moment it is useful (UI spec §6.8): the
    ``on:`` form for one answered cell with an indexed base, else the ``in:`` form for
    the first unsearched project; "" when either YAML key is off."""
    if not (config.tokens_enabled and config.footer_hint):
        return ""
    base = _indexed_base_of_one_cell(groups, listing, capabilities)
    if base:
        return f"add {BRANCH_TOKEN_PREFIX}{base} to compare with {base}"
    name = _unsearched_project(groups, listing)
    return f"add {PROJECT_TOKEN_PREFIX}{name} to search there too" if name else ""


def render_answer_footer(
    observations: ScopeObservations,
    listing: WorkspaceBranchListing,
    config: ScopeDefaultsConfig = _SHIPPED_SCOPE_DEFAULTS,
    capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
) -> str:
    """One caption line: ``Searched `` once, a segment per distinct sent cell, sorted,
    joined by `` | ``, then the teaching hint when the two YAML keys allow it."""
    groups = observations.by_cell()
    if not groups:
        return "answered without tool calls"
    line = _SEARCHED + " | ".join(
        _segment(cell, records, listing) for cell, records in groups.items()
    )
    hint = _teaching_hint(groups, listing, config, capabilities)
    return f"{line} · {hint}" if hint else line


# --- chips -------------------------------------------------------------------


class FollowUpKind(StrEnum):
    ASK_ON = "ask_on"
    COMPARE_WITH = "compare_with"
    PIN_BRANCH = "pin_branch"
    SHOW_DIFF = "show_diff"


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


def _ask_on_branch(
    cell: ScopeCell,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    asked: str,
    compare_base: str,
) -> str:
    """The branch "Ask this on <branch> too" names: the first OTHER pickable branch of the
    project in listing order, never the base a "Compare with" chip of the same answer names
    — so a two-branch project shows "Compare with main" alone (UI spec §6.9). "" when the
    capability is off, the answer's text is unknown, or no such branch is listed."""
    if not capabilities.branch_selector or not asked:
        return ""
    already_named = {cell.branch, compare_base}
    return next((r.name for r in listing.pickable(cell.project) if r.name not in already_named), "")


def _ask_on_chip(cell: ScopeCell, other: str, asked: str) -> FollowUpChip | None:
    """ "Ask this on <branch> too" — it re-sends the stripped text the answer was produced
    from, so an ``in:`` / ``on:`` token question never hands its syntax back to the model."""
    if not other:
        return None
    return FollowUpChip(
        kind=FollowUpKind.ASK_ON,
        label=f"Ask this on {other} too",
        project=cell.project,
        branches=(other,),
        slice=ScopeSlice.WHOLE_BRANCH,
        question=asked,
    )


def _compare_base(
    cell: ScopeCell, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> str:
    """The base "Compare with <base>" names: the answered branch's own base while the listing
    indexes it and it differs from the branch itself; "" when the chip does not apply. It is
    also the ONE branch an "Ask this on" chip of the same answer must not name (§6.9)."""
    if not capabilities.branch_selector:
        return ""
    row = listing.row(cell.project, cell.branch)
    base = str(row.base_name or "") if row else ""
    if not base or base == cell.branch or not listing.has_branch(cell.project, base):
        return ""
    return base


def _compare_chip(cell: ScopeCell, base: str) -> FollowUpChip | None:
    if not base:
        return None
    return FollowUpChip(
        kind=FollowUpKind.COMPARE_WITH,
        label=f"Compare with {base}",
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
        label="Show what changed",
        project=cell.project,
        branches=(target,),
        slice=ScopeSlice.DIFF_HUNKS,
        question="Show the diff hunks behind the previous answer.",
    )


def _strip_cells(strip_scope: QuestionScope | None) -> tuple[ScopeCell, ...]:
    """The strip's own cells; a no-target strip compiles to DEFAULT and holds none."""
    if strip_scope is None or strip_scope.kind is not ScopeKind.PIN:
        return ()
    return strip_scope.cells


def _keep_chip_wanted(
    cell: ScopeCell, records: tuple[CellObservation, ...], strip_cells: tuple[ScopeCell, ...]
) -> bool:
    """A cell the question already pinned, or the strip already holds, has nothing to add."""
    if all(r.branch_origin is BranchOrigin.PINNED for r in records):
        return False
    return cell not in strip_cells


def _keep_chip_cell(
    cells: dict[ScopeCell, tuple[CellObservation, ...]],
    strip_cells: tuple[ScopeCell, ...],
    capabilities: ScopeCapabilities,
    max_cells: int,
) -> ScopeCell | None:
    """The cell "Keep searching <branch>" would add: the FIRST wanted one in
    ``(project, branch)`` order.

    None when the grown strip would pass ``max_cells``: the picker refuses such a strip,
    so the chip must too, or every later send would fail at E4 (UI spec §6.9).
    """
    if not capabilities.branch_selector or len(strip_cells) + 1 > max_cells:
        return None
    wanted = (c for c, records in cells.items() if _keep_chip_wanted(c, records, strip_cells))
    return next(wanted, None)


def _keep_chip(
    cells: dict[ScopeCell, tuple[CellObservation, ...]],
    strip_scope: QuestionScope | None,
    capabilities: ScopeCapabilities,
    max_cells: int,
) -> FollowUpChip | None:
    """ "Keep searching <branch>": the one chip that grows the sticky strip (UI spec §6.9)."""
    cell = _keep_chip_cell(cells, _strip_cells(strip_scope), capabilities, max_cells)
    if cell is None:
        return None
    return FollowUpChip(
        kind=FollowUpKind.PIN_BRANCH,
        label=f"Keep searching {cell.branch}",
        project=cell.project,
        branches=(cell.branch,),
        slice=ScopeSlice.WHOLE_BRANCH,
        question="",
    )


def _one_cell_chips(
    cell: ScopeCell,
    records: tuple[CellObservation, ...],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    asked: str,
) -> tuple[FollowUpChip, ...]:
    """The three chips only one distinct answered cell can carry, in screen order."""
    compare_base = _compare_base(cell, listing, capabilities)
    other = _ask_on_branch(cell, listing, capabilities, asked, compare_base)
    ask_on = _ask_on_chip(cell, other, asked)
    diff = _show_diff_chip(cell, records, listing, capabilities)
    return tuple(c for c in (ask_on, _compare_chip(cell, compare_base), diff) if c is not None)


def derive_follow_up_chips(
    observations: ScopeObservations,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    strip_scope: QuestionScope | None,
    asked: str = "",
    *,
    max_cells: int = _SHIPPED_MAX_CELLS,
) -> tuple[FollowUpChip, ...]:
    """At most one chip per kind (UI spec §6.9); ``len(FollowUpKind)`` is the ceiling,
    never reached through cells. ``asked`` is the stripped text an ASK_ON chip re-sends.
    """
    cells = answered_cells(observations, listing)
    chips: list[FollowUpChip] = []
    if len(cells) == 1:
        ((cell, records),) = cells.items()
        chips.extend(_one_cell_chips(cell, records, listing, capabilities, asked))
    keep = _keep_chip(cells, strip_scope, capabilities, max_cells)
    return tuple(chips) + ((keep,) if keep is not None else ())


def apply_follow_up_chip(
    chip: FollowUpChip, strip: StripState, defaults: QuestionScope
) -> tuple[str | None, QuestionScope | None, StripState]:
    """(question to send, one-shot pin to send it under, the strip after the click) — AC-31.

    PIN_BRANCH sends nothing and returns the strip grown by the chip's cell — a new target
    for a project the strip lacked, one more branch on its existing one otherwise — with
    ``only_these`` kept (the >= 2 rule forces the checkbox on screen, never in the state).
    ASK_ON / COMPARE_WITH / SHOW_DIFF build the one-shot pin and hand the strip back
    untouched. A STRIP comes back rather than a grown scope because ``compile_strip_scope``
    is one-way: the page could not turn a scope back into targets (UI spec §6.9).
    """
    if chip.kind is FollowUpKind.PIN_BRANCH:
        return None, None, strip.with_target(StripTarget(chip.project, chip.branches))
    one_shot = QuestionScope(
        kind=ScopeKind.PIN,
        cells=tuple(ScopeCell(chip.project, branch) for branch in chip.branches),
        slice=chip.slice,
        code=code_compatible_with_slice(chip.slice, defaults.code),
        package=defaults.package,
    )
    return chip.question, one_shot, strip


__all__ = (
    "ALL_PROJECTS",
    "BEHIND",
    "FRESH",
    "NO_BRANCH",
    "ORIGIN_LABELS",
    "FollowUpChip",
    "FollowUpKind",
    "answered_cells",
    "apply_follow_up_chip",
    "derive_follow_up_chips",
    "render_answer_footer",
)
