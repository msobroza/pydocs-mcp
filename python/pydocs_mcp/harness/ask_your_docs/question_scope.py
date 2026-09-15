"""The per-question scope of the ask-your-docs agent (UI spec §6.1–§6.2).

Two kinds: DEFAULT (soft — fills what the model left empty; one cell whose
branch is resolved per call) and PIN (hard — overwrites and fans out over
its cells). Light module by contract: no streamlit / langgraph imports.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeVar

from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.retrieval.config.ask_your_docs_models import (
    ANY_PROJECT,
    ScopeBranchDefault,
    ScopeCode,
    ScopeDefaultsConfig,
    ScopeSlice,
)

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

# Server spellings: multi-branch spec §6.5 for the slices; today's agent rule
# for the code filter (OWN is the server's "project").
SLICE_SERVER_VALUES: dict[ScopeSlice, str] = {
    ScopeSlice.CHANGED_FILES: "changed",
    ScopeSlice.DIFF_HUNKS: "diff",
}
CODE_SERVER_VALUES: dict[ScopeCode, str] = {ScopeCode.OWN: "project", ScopeCode.DEPS: "deps"}
# Human labels shared by the footer, the chips, the caption and the pinned note.
SLICE_LABELS: dict[ScopeSlice, str] = {
    ScopeSlice.WHOLE_BRANCH: "whole branch",
    ScopeSlice.CHANGED_FILES: "changed files",
    ScopeSlice.DIFF_HUNKS: "diff hunks",
}
CODE_LABELS: dict[ScopeCode, str] = {
    ScopeCode.ALL: "all code",
    ScopeCode.OWN: "own code only",
    ScopeCode.DEPS: "dependencies only",
}


def log_scope_event(event: str, **fields: object) -> None:
    """One structured JSON log line per scope event (named fields, sorted keys)."""
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def _ordered_unique(values: Iterable[_T]) -> tuple[_T, ...]:
    return tuple(dict.fromkeys(values))


class ScopeKind(StrEnum):
    """Soft defaults (fill what the model left empty) vs a hard per-question pin."""

    DEFAULT = "default"
    PIN = "pin"


@dataclass(frozen=True, slots=True)
class ScopeCell:
    """One ``(project, branch)`` target a pinned call fans out to."""

    project: str  # "" = union across loaded projects (mcp_inputs SearchInput.project)
    branch: str  # "" = let the server resolve


def listing_cell(listing: WorkspaceBranchListing, project: str, branch: str = "") -> ScopeCell:
    """The one U0 cell shape for ``project`` (UI spec §6.4a): the stamped default row.

    WHY one helper: a strip target, an ``in:`` token and a graph attach must build the
    SAME cell, or ``QuestionScope.with_cells``'s set semantics, the chips, the caption
    and the footer's ``head_sha(project, branch)`` disagree — ``(backend, "")`` and
    ``(backend, "main")`` are two distinct cells to the value object, so one project
    would fan out twice. The branch stays ``""`` only on E8: a project whose bundle
    carries no branch row at all.
    """
    if branch:
        return ScopeCell(project, branch)
    row = listing.default_row(project)
    return ScopeCell(project, row.name if row else "")


@dataclass(frozen=True, slots=True)
class QuestionScope:
    """Exactly one is active per question (UI spec §6.1)."""

    kind: ScopeKind
    cells: tuple[ScopeCell, ...]
    slice: ScopeSlice = ScopeSlice.WHOLE_BRANCH
    code: ScopeCode = ScopeCode.ALL
    package: str = ""
    # DEFAULT only: the branch is resolved lazily per call against the
    # effective project (resolve_default_branch). Ignored under PIN.
    branch_default: ScopeBranchDefault = ScopeBranchDefault.BASE
    branch_name: str = ""

    def __post_init__(self) -> None:
        if not self.cells:
            raise ValueError(
                f"QuestionScope.cells is empty; expected at least one (project, branch) "
                f"cell for kind={self.kind.value!r}"
            )
        if len(set(self.cells)) != len(self.cells):
            raise ValueError(f"QuestionScope.cells has duplicates: {self.cells!r}")
        if self.kind is ScopeKind.DEFAULT and (len(self.cells) != 1 or self.cells[0].branch):
            raise ValueError(
                "a DEFAULT scope holds exactly one cell with an empty branch "
                f"(resolved per call), got cells={self.cells!r}"
            )
        if self.slice is not ScopeSlice.WHOLE_BRANCH and self.code is ScopeCode.DEPS:
            raise ValueError(
                f"slice={self.slice.value!r} cannot combine with code={self.code.value!r}; "
                "expected code 'all' or 'own' with a slice"
            )

    @property
    def is_multi_branch(self) -> bool:
        return len(self.cells) > 1

    @property
    def default_project(self) -> str:
        return self.cells[0].project

    def projects(self) -> tuple[str, ...]:
        return _ordered_unique(c.project for c in self.cells if c.project)

    def branches_for(self, project: str) -> tuple[str, ...]:
        return tuple(c.branch for c in self.cells if c.project == project and c.branch)

    def with_cells(self, cells: Iterable[ScopeCell]) -> QuestionScope:
        """This scope plus the cells it lacks (cells are a set; order kept)."""
        missing = tuple(c for c in _ordered_unique(cells) if c not in self.cells)
        return replace(self, cells=(*self.cells, *missing)) if missing else self

    def without_cell(self, cell: ScopeCell) -> QuestionScope | None:
        """This scope minus ``cell``; ``None`` when that was the last cell."""
        rest = tuple(c for c in self.cells if c != cell)
        return replace(self, cells=rest) if rest else None


@dataclass(frozen=True, slots=True)
class ScopeDefaultsOverride:
    """The "Scope defaults" panel's session values; ``None`` = use YAML."""

    project: str | None = None
    branch_default: ScopeBranchDefault | None = None
    branch_name: str | None = None
    slice: ScopeSlice | None = None
    code: ScopeCode | None = None
    package: str | None = None


def _pick(override: _T | None, default: _T) -> _T:
    return default if override is None else override


def resolve_question_scope_defaults(
    config: ScopeDefaultsConfig,
    session: ScopeDefaultsOverride,
    listing: WorkspaceBranchListing,
) -> QuestionScope:
    """YAML + panel override + listing -> the DEFAULT scope (one project cell).

    A named project the listing does not know falls back to the union and
    logs; an empty listing (nothing scanned) passes the name through.
    """
    project = _pick(session.project, config.project)
    cell_project = "" if project == ANY_PROJECT else project
    if cell_project and listing.has_projects and not listing.knows_project(cell_project):
        log_scope_event(
            "scope_default_replaced",
            tool="",
            argument="project",
            passed=cell_project,
            replacement="",
        )
        cell_project = ""
    return QuestionScope(
        kind=ScopeKind.DEFAULT,
        cells=(ScopeCell(cell_project, ""),),
        slice=_pick(session.slice, config.slice),
        code=_pick(session.code, config.code),
        package=_pick(session.package, config.package),
        branch_default=_pick(session.branch_default, config.branch_default),
        branch_name=_pick(session.branch_name, config.branch_name),
    )


def resolve_default_branch(
    scope: QuestionScope, project: str, listing: WorkspaceBranchListing
) -> str:
    """The branch to inject for a DEFAULT call on ``project``; ``""`` = nothing.

    Rules (UI spec §6.2): union -> nothing; a listed ``branch_name`` -> itself
    (unlisted -> nothing + log); BASE -> the default row's ``base_name`` when
    listed and different from the default row; CHECKED_OUT -> nothing.
    """
    if not project:
        return ""
    if scope.branch_name:
        return _listed_named_default(scope.branch_name, project, listing)
    if scope.branch_default is ScopeBranchDefault.CHECKED_OUT:
        return ""
    row = listing.default_row(project)
    if row is None or not row.base_name or row.base_name == row.name:
        return ""
    return row.base_name if listing.has_branch(project, row.base_name) else ""


def _listed_named_default(branch_name: str, project: str, listing: WorkspaceBranchListing) -> str:
    """``branch_name`` when the listing has it on ``project``; else "" plus one log line."""
    if listing.has_branch(project, branch_name):
        return branch_name
    log_scope_event(
        "scope_default_replaced",
        tool="",
        argument="branch_name",
        passed=branch_name,
        replacement="",
    )
    return ""


def _named_parts(scope: QuestionScope) -> list[str]:
    parts: list[str] = []
    projects = scope.projects()
    if len(projects) == 1:
        parts.append(f"project={projects[0]}")
    elif projects:
        parts.append(f"projects={', '.join(projects)}")
    branches = _ordered_unique(c.branch for c in scope.cells if c.branch)
    if len(branches) == 1:
        parts.append(f"branch={branches[0]}")
    elif branches:
        parts.append(f"branches={', '.join(branches)}")
    return parts


def scope_prefix(scope: QuestionScope | None) -> str:
    """The "[pinned scope: ...]" note prepended to a question, or "" (defaults are silent)."""
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    parts = _named_parts(scope)
    if scope.package:
        parts.append(f"package={scope.package}")
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        parts.append(SLICE_LABELS[scope.slice])
    if scope.code is not ScopeCode.ALL:
        parts.append(CODE_LABELS[scope.code])
    return f"[pinned scope: {', '.join(parts)}] " if parts else ""


def scope_caption_text(scope: QuestionScope | None) -> str:
    """The transcript's scope chip: ``backend · main, feature/retry · diff hunks``."""
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    groups: list[str] = []
    for project in scope.projects() or ("",):
        branches = scope.branches_for(project)
        label = project or "all projects"
        groups.append(f"{label} · {', '.join(branches)}" if branches else label)
    text = " | ".join(groups)
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        text = f"{text} · {SLICE_LABELS[scope.slice]}"
    return text


def pin_summary_label(pin: QuestionScope | None) -> str:
    """The popover button's label while a pin is active (UI spec §6.4a)."""
    if pin is None:
        return ""
    projects = pin.projects()
    if len(projects) > 1:
        return f"{len(projects)} projects"
    project = projects[0] if projects else "all projects"
    branches = pin.branches_for(project)
    if len(branches) > 1:
        return f"{project} · {len(branches)} branches"
    label = f"{project} · {branches[0]}" if branches else project
    if pin.slice is not ScopeSlice.WHOLE_BRANCH:
        label = f"{label} · {SLICE_LABELS[pin.slice]}"
    return label


def code_compatible_with_slice(slice_value: ScopeSlice, code: ScopeCode) -> ScopeCode:
    """E11: a slice never combines with dependencies-only — widen to ALL."""
    if slice_value is not ScopeSlice.WHOLE_BRANCH and code is ScopeCode.DEPS:
        return ScopeCode.ALL
    return code


def pin_with_attached_symbols(
    pin: QuestionScope | None,
    attached: Sequence[AttachedSymbol | str],
    defaults: QuestionScope,
) -> QuestionScope | None:
    """Fold attached symbols' cells into the pin (UI spec §6.11, AC-30).

    No pin + attached cells -> a one-shot PIN over those cells (slice / code /
    package from ``defaults``); an active pin gains each cell once.
    """
    cells = _ordered_unique(
        ScopeCell(a.project, a.branch)
        for a in attached
        if isinstance(a, AttachedSymbol) and a.project
    )
    if not cells:
        return pin
    if pin is not None:
        return pin.with_cells(cells)
    return QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=defaults.slice,
        code=defaults.code,
        package=defaults.package,
    )


def snapshot_pin_for_send(
    pin: QuestionScope | None,
    keep: bool,
    attached: Sequence[AttachedSymbol | str],
    defaults: QuestionScope,
) -> tuple[QuestionScope, QuestionScope | None]:
    """(the scope this question is sent under, the pin that stays active after).

    A one-shot pin (``keep`` false) is gone before ``ask()`` runs — the
    transcript's scope chip is its only trace (UI spec §6.7 "Pin lifecycle").
    Attached cells ride the sent scope only; the kept pin never grows by them.
    """
    scope = pin_with_attached_symbols(pin, attached, defaults) or defaults
    return scope, (pin if pin is not None and keep else None)


__all__ = (
    "CODE_LABELS",
    "CODE_SERVER_VALUES",
    "SLICE_LABELS",
    "SLICE_SERVER_VALUES",
    "QuestionScope",
    "ScopeBranchDefault",
    "ScopeCell",
    "ScopeCode",
    "ScopeDefaultsOverride",
    "ScopeKind",
    "ScopeSlice",
    "code_compatible_with_slice",
    "listing_cell",
    "log_scope_event",
    "pin_summary_label",
    "pin_with_attached_symbols",
    "resolve_default_branch",
    "resolve_question_scope_defaults",
    "scope_caption_text",
    "scope_prefix",
    "snapshot_pin_for_send",
)
