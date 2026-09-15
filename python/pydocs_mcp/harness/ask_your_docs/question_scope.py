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
from pydocs_mcp.harness.ask_your_docs.catalog import EMPTY_BRANCH_LISTING, WorkspaceBranchListing
from pydocs_mcp.models import NON_GIT_BRANCH_NAME
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
# The model-facing "[pinned scope: ...]" note keeps today's words: its bytes are
# frozen and it is never on screen (UI spec §6.7), so the D14 vocabulary rewrite
# below must not reach it. Only scope_prefix reads these two tables.
MODEL_NOTE_SLICE_WORDS: dict[ScopeSlice, str] = {
    ScopeSlice.WHOLE_BRANCH: "whole branch",
    ScopeSlice.CHANGED_FILES: "changed files",
    ScopeSlice.DIFF_HUNKS: "diff hunks",
}
MODEL_NOTE_CODE_WORDS: dict[ScopeCode, str] = {
    ScopeCode.ALL: "all code",
    ScopeCode.OWN: "own code only",
    ScopeCode.DEPS: "dependencies only",
}
# On-screen words (D14, UI spec §6.7): picker, strip, footer, transcript caption.
SLICE_LABELS: dict[ScopeSlice, str] = {
    ScopeSlice.WHOLE_BRANCH: "everything on the branch",
    ScopeSlice.CHANGED_FILES: "only files this branch changed",
    ScopeSlice.DIFF_HUNKS: "only the changes themselves",
}
CODE_LABELS: dict[ScopeCode, str] = {
    ScopeCode.ALL: "project code and dependencies",
    ScopeCode.OWN: "project code only",
    ScopeCode.DEPS: "dependencies only",
}


def log_scope_event(event: str, **fields: object) -> None:
    """One structured JSON log line per scope event (named fields, sorted keys)."""
    logger.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def log_scope_default_replaced(argument: str, passed: str) -> None:
    """One ``scope_default_replaced`` line: a configured name the listing rejects.

    WHY one writer: the strip's YAML seed, the per-call resolve and the named-branch
    default all report the same replacement, and an operator greps for one event.
    """
    log_scope_event(
        "scope_default_replaced", tool="", argument=argument, passed=passed, replacement=""
    )


def ordered_unique(values: Iterable[_T]) -> tuple[_T, ...]:
    """First-occurrence order, duplicates dropped (public: ``strip_state`` shares it)."""
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
        return ordered_unique(c.project for c in self.cells if c.project)

    def branches_for(self, project: str) -> tuple[str, ...]:
        return tuple(c.branch for c in self.cells if c.project == project and c.branch)

    def with_cells(self, cells: Iterable[ScopeCell]) -> QuestionScope:
        """This scope plus the cells it lacks (cells are a set; order kept)."""
        missing = tuple(c for c in ordered_unique(cells) if c not in self.cells)
        return replace(self, cells=(*self.cells, *missing)) if missing else self

    def without_cell(self, cell: ScopeCell) -> QuestionScope | None:
        """This scope minus ``cell``; ``None`` when that was the last cell."""
        rest = tuple(c for c in self.cells if c != cell)
        return replace(self, cells=rest) if rest else None


@dataclass(frozen=True, slots=True)
class ScopeDefaultsOverride:
    """The strip's "More" values for the session; ``None`` = use YAML."""

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
        log_scope_default_replaced("project", cell_project)
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
    log_scope_default_replaced("branch_name", branch_name)
    return ""


def _named_group(values: tuple[str, ...], singular: str, plural: str) -> str:
    """``project=backend`` for one value, ``projects=a, b`` for several, ``""`` for none."""
    if not values:
        return ""
    if len(values) == 1:
        return f"{singular}={values[0]}"
    return f"{plural}={', '.join(values)}"


def _named_parts(scope: QuestionScope) -> list[str]:
    """The note's project and branch groups, each singular or plural (AC-28 bytes)."""
    branches = ordered_unique(c.branch for c in scope.cells if c.branch)
    groups = (
        _named_group(scope.projects(), "project", "projects"),
        _named_group(branches, "branch", "branches"),
    )
    return [group for group in groups if group]


def scope_prefix(scope: QuestionScope | None) -> str:
    """The "[pinned scope: ...]" note prepended to a question, or "" (defaults are silent)."""
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    parts = _named_parts(scope)
    if scope.package:
        parts.append(f"package={scope.package}")
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        parts.append(MODEL_NOTE_SLICE_WORDS[scope.slice])
    if scope.code is not ScopeCode.ALL:
        parts.append(MODEL_NOTE_CODE_WORDS[scope.code])
    return f"[pinned scope: {', '.join(parts)}] " if parts else ""


def branch_for_display(name: str) -> str:
    """The branch name the screen shows: ``""`` for the non-git sentinel.

    A project indexed outside any git repository is stamped with the sentinel
    row ``NON_GIT_BRANCH_NAME`` so the engine still has one cell per project;
    on screen that row reads as "no branch" (chips, captions, the footer), never
    as a branch called "no git".
    """
    return "" if name == NON_GIT_BRANCH_NAME else name


def _caption_group(scope: QuestionScope, project: str) -> str:
    """One project's caption group: ``backend · main, feature/retry``, or its bare label."""
    branches = tuple(b for b in scope.branches_for(project) if branch_for_display(b))
    label = project or "all projects"
    return f"{label} · {', '.join(branches)}" if branches else label


def scope_caption_text(scope: QuestionScope | None, *, from_question: bool = False) -> str:
    """The transcript caption above a pinned question (UI spec §6.7, AC-39).

    ``searched in: backend · main, feature/retry | tooling · main``; cells typed
    as ``in:`` / ``on:`` tokens add ``(from your question)``, so a typed scope
    reads apart from the strip's.
    """
    if scope is None or scope.kind is ScopeKind.DEFAULT:
        return ""
    groups = [_caption_group(scope, project) for project in scope.projects() or ("",)]
    text = f"searched in: {' | '.join(groups)}"
    if scope.slice is not ScopeSlice.WHOLE_BRANCH:
        text = f"{text} · {SLICE_LABELS[scope.slice]}"
    return f"{text} (from your question)" if from_question else text


def code_compatible_with_slice(slice_value: ScopeSlice, code: ScopeCode) -> ScopeCode:
    """E11: a slice never combines with dependencies-only — widen to ALL."""
    if slice_value is not ScopeSlice.WHOLE_BRANCH and code is ScopeCode.DEPS:
        return ScopeCode.ALL
    return code


def pin_with_attached_symbols(
    pin: QuestionScope | None,
    attached: Sequence[AttachedSymbol | str],
    defaults: QuestionScope,
    listing: WorkspaceBranchListing = EMPTY_BRANCH_LISTING,
) -> QuestionScope | None:
    """Fold attached symbols' cells into the pin (UI spec §6.11, AC-30).

    No pin + attached cells -> a one-shot PIN over those cells (slice / code /
    package from ``defaults``); an active pin gains each cell once. Cells go
    through ``listing_cell`` (§6.4a): a branchless attach would otherwise mint
    ``(project, "")`` where the strip and the tokens carry the stamped row, and
    the project would fan out twice. No listing = today's shape, no branch.
    """
    cells = ordered_unique(
        listing_cell(listing, a.project, a.branch)
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


def pin_or_none(scope: QuestionScope) -> QuestionScope | None:
    """The pin inside an active scope, ``None`` under DEFAULT (UI spec §6.9).

    The strip always compiles to a scope, never to ``None``; the chip derivation
    and the attachment fold still ask "is a pin active?" — this is that question.
    """
    return scope if scope.kind is ScopeKind.PIN else None


def token_scope(cells: Sequence[ScopeCell], active: QuestionScope) -> QuestionScope:
    """A typed-token question's one-shot PIN (UI spec §6.10a).

    The token cells, ``code`` / ``package`` from the active scope (the picker's
    "More" values), and ALWAYS the whole branch as the slice (owner decision D14
    §4): a diff slice chosen in "More" belongs to the strip's own questions, and
    leaking it narrows a scope the person spelled out in full.
    """
    return QuestionScope(
        kind=ScopeKind.PIN,
        cells=tuple(cells),
        slice=ScopeSlice.WHOLE_BRANCH,
        code=active.code,
        package=active.package,
    )


def snapshot_pin_for_send(
    active: QuestionScope,
    attached: Sequence[AttachedSymbol | str],
    listing: WorkspaceBranchListing = EMPTY_BRANCH_LISTING,
) -> QuestionScope:
    """The scope this question is sent under (UI spec §6.7, AC-30).

    The strip's active scope, grown by the attached symbols' cells for this send
    only. No second return value: the strip is sticky and never grows by an
    attachment, so nothing has to be handed back as "what stays active after".
    """
    return pin_with_attached_symbols(pin_or_none(active), attached, active, listing) or active


__all__ = (
    "CODE_LABELS",
    "CODE_SERVER_VALUES",
    "MODEL_NOTE_CODE_WORDS",
    "MODEL_NOTE_SLICE_WORDS",
    "SLICE_LABELS",
    "SLICE_SERVER_VALUES",
    "QuestionScope",
    "ScopeBranchDefault",
    "ScopeCell",
    "ScopeCode",
    "ScopeDefaultsOverride",
    "ScopeKind",
    "ScopeSlice",
    "branch_for_display",
    "code_compatible_with_slice",
    "listing_cell",
    "log_scope_default_replaced",
    "log_scope_event",
    "ordered_unique",
    "pin_or_none",
    "pin_with_attached_symbols",
    "resolve_default_branch",
    "resolve_question_scope_defaults",
    "scope_caption_text",
    "scope_prefix",
    "snapshot_pin_for_send",
    "token_scope",
)
