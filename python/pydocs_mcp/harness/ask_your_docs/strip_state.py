"""The "Searching in …" strip's state and its compiler (UI spec §6.1, §6.7, AC-35).

Pure by contract — no streamlit, no langchain (a subprocess pin holds it). The
strip is the page's sticky "where to search"; ``compile_strip_scope`` is the ONE
way it becomes a ``QuestionScope``, so the screen and the engine can never drift.

Example:
    scope = compile_strip_scope((StripTarget("backend", ("main",)),), True, config, listing)
    scope.kind is ScopeKind.PIN and scope.cells == (ScopeCell("backend", "main"),)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    ANY_PROJECT,
    QuestionScope,
    ScopeCell,
    ScopeDefaultsOverride,
    ScopeKind,
    code_compatible_with_slice,
    listing_cell,
    log_scope_event,
    ordered_unique,
    resolve_question_scope_defaults,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

# The chip's remove affordance (UI spec §6.7 row 1); one source so the strip and
# its tests never spell it twice.
_CHIP_REMOVE_MARK = "✕"
# "No 'More' value chosen this session" — every field None, so YAML answers for
# each one. A module-level singleton because the value object is frozen and a
# call in a default is refused by the linter (RUF009 / B008).
_NO_MORE_VALUES = ScopeDefaultsOverride()


@dataclass(frozen=True, slots=True)
class StripTarget:
    """One ticked project in the "Searching in" strip and its chosen branches.

    On U0 the picker fills the listing's STAMPED branch (informational: the
    interceptor drops it while ``branch_selector`` is off); ``()`` is a bundle
    with no branch rows at all (E8), which pins the bare project.
    """

    project: str
    branches: tuple[str, ...] = ()

    def cells(self) -> tuple[ScopeCell, ...]:
        """This target's cells — one per branch, or the bare project on E8."""
        return tuple(ScopeCell(self.project, b) for b in self.branches) or (
            ScopeCell(self.project, ""),
        )


@dataclass(frozen=True, slots=True)
class StripState:
    """The sticky strip (UI spec §6.7): targets, the user's own "Only these"
    tick, and the picker's "More" values.

    ``only_these`` is what the person ticked — held here, not on the widget key,
    so it survives a page that does not render the strip (Streamlit drops the
    keys of unrendered widgets). The forced-on state at two or more cells is a
    rendering rule (§6.1, E16) and is never written into this field.
    """

    targets: tuple[StripTarget, ...] = ()
    only_these: bool = False
    more: ScopeDefaultsOverride = _NO_MORE_VALUES

    def projects(self) -> tuple[str, ...]:
        return tuple(t.project for t in self.targets)

    def with_target(self, target: StripTarget) -> StripState:
        """This state plus ``target``; an existing project gains the new branches only."""
        mine = next((t for t in self.targets if t.project == target.project), None)
        if mine is None:
            return replace(self, targets=(*self.targets, target))
        grown = StripTarget(mine.project, ordered_unique((*mine.branches, *target.branches)))
        return replace(self, targets=tuple(grown if t is mine else t for t in self.targets))

    def without_cell(self, cell: ScopeCell) -> StripState:
        """This state minus one chip; a project whose last branch goes leaves the strip."""
        narrowed = (_target_without_cell(t, cell) for t in self.targets)
        return replace(self, targets=tuple(t for t in narrowed if t is not None))

    def cleared(self) -> StripState:
        """No target and no "Only these" (moot without a target); "More" survives a Clear."""
        return StripState(more=self.more)


def _target_without_cell(target: StripTarget, cell: ScopeCell) -> StripTarget | None:
    """``target`` minus ``cell``'s branch; ``None`` when nothing of it is left.

    Position is preserved by the caller's comprehension: the strip renders in
    target order, so removing one chip must not reshuffle the row.
    """
    if target.project != cell.project:
        return target
    rest = tuple(b for b in target.branches if b != cell.branch)
    return StripTarget(target.project, rest) if rest else None


def strip_cells(targets: Sequence[StripTarget]) -> tuple[ScopeCell, ...]:
    """Every target's cells, duplicates collapsed (the cell matrix of §6.4a)."""
    return ordered_unique(cell for target in targets for cell in target.cells())


def ordered_targets(
    targets: Sequence[StripTarget], listing: WorkspaceBranchListing
) -> tuple[StripTarget, ...]:
    """Listing order (spec §6.1: fan-out runs in listing order); unknown projects last."""
    names = listing.project_names
    return tuple(
        sorted(targets, key=lambda t: names.index(t.project) if t.project in names else len(names))
    )


def strip_target_for(project: str, listing: WorkspaceBranchListing) -> StripTarget:
    """A target on the project's stamped row — the only branch U0 can show.

    ``project`` may arrive as a bundle stem (the ``project=`` selector's second
    spelling); it is normalized to the project name here, for the same reason
    ``in:`` tokens are normalized at parse time (§6.10a): chips, captions and
    ``head_sha(project, branch)`` must all carry one spelling, or the same
    project reaches the engine as two cells.
    """
    name = listing.project_for(project) or project
    branch = listing_cell(listing, name).branch
    return StripTarget(name, (branch,) if branch else ())


def initial_strip_state(config: ScopeDefaultsConfig, listing: WorkspaceBranchListing) -> StripState:
    """The YAML seed of the strip (R3's seed rule, applied once at first load).

    ``project: any`` or a name the listing does not know seeds no target — the
    same replacement AC-3 applies per call, logged the same way so an operator's
    typo in YAML is still visible. An empty listing (nothing scanned yet) passes
    the name through exactly as ``resolve_question_scope_defaults`` does, so the
    strip and the per-call path never disagree about an unscanned workspace.
    """
    if config.project == ANY_PROJECT:
        return StripState()
    if listing.has_projects and not listing.knows_project(config.project):
        log_scope_event(
            "scope_default_replaced",
            tool="",
            argument="project",
            passed=config.project,
            replacement="",
        )
        return StripState()
    return StripState(targets=(strip_target_for(config.project, listing),))


def _compatible_more(
    more: ScopeDefaultsOverride, config: ScopeDefaultsConfig
) -> ScopeDefaultsOverride:
    """E11 at the strip's edge: widen dependencies-only when a slice is chosen.

    ``resolve_question_scope_defaults`` builds a ``QuestionScope``, whose
    invariant REFUSES that pair with a ValueError — so the widening has to
    happen before the value object sees it, not after, and it has to happen on
    the effective values (the "More" override layered over YAML).
    """
    chosen_slice = more.slice if more.slice is not None else config.slice
    chosen_code = more.code if more.code is not None else config.code
    return replace(more, code=code_compatible_with_slice(chosen_slice, chosen_code))


def _soft_override(
    more: ScopeDefaultsOverride,
    targets: Sequence[StripTarget],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> ScopeDefaultsOverride:
    """The DEFAULT cases' override: no target -> the union; one target -> that
    project and, once the server takes a branch, the ONE branch the person picked
    (so the chip and the sent branch never disagree — U1); on U0 the branch stays
    YAML's, because nothing branch-shaped is sent anyway."""
    if not targets:
        return replace(more, project=ANY_PROJECT)
    target = targets[0]
    chosen = target.branches[0] if len(target.branches) == 1 else ""
    if capabilities.branch_selector and chosen and listing.has_branch(target.project, chosen):
        return replace(more, project=target.project, branch_name=chosen)
    return replace(more, project=target.project)


def compile_strip_scope(
    targets: Sequence[StripTarget],
    only_these: bool,
    config: ScopeDefaultsConfig,
    listing: WorkspaceBranchListing,
    more: ScopeDefaultsOverride = _NO_MORE_VALUES,
    capabilities: ScopeCapabilities = NO_SCOPE_CAPABILITIES,
) -> QuestionScope:
    """The strip -> the engine (UI spec §6.1, AC-35).

    No target -> DEFAULT over the union (today's no-pin path, byte-identical,
    whatever YAML's project holds: "Clear" promises all projects and must send
    exactly that); one cell with "Only these" off -> DEFAULT for that project
    (branch resolved per call; on U1 the picked branch rides as ``branch_name``);
    one cell with it on, or two or more cells -> PIN. Two or more cells imply PIN
    whatever the checkbox holds — the strip mirrors that by forcing the box on
    (§12 O10) — and ``max_cells`` is refused by the callers, never here.
    """
    compatible = _compatible_more(more, config)
    cells = strip_cells(ordered_targets(targets, listing))
    if not targets or (len(cells) == 1 and not only_these):
        override = _soft_override(compatible, targets, listing, capabilities)
        return resolve_question_scope_defaults(config, override, listing)
    soft = resolve_question_scope_defaults(
        config, replace(compatible, project=ANY_PROJECT), listing
    )
    return QuestionScope(
        kind=ScopeKind.PIN, cells=cells, slice=soft.slice, code=soft.code, package=soft.package
    )


def missing_strip_cells(
    state: StripState, listing: WorkspaceBranchListing
) -> tuple[ScopeCell, ...]:
    """The cells a reloaded listing no longer has — dropped one by one (E12)."""
    return tuple(
        cell
        for cell in strip_cells(state.targets)
        if not listing.knows_project(cell.project)
        or (cell.branch and not listing.has_branch(cell.project, cell.branch))
    )


def strip_chip_label(cell: ScopeCell) -> str:
    """One strip chip: ``backend · main ✕``, or ``backend ✕`` on a branchless cell."""
    if cell.branch:
        return f"{cell.project} · {cell.branch} {_CHIP_REMOVE_MARK}"
    return f"{cell.project} {_CHIP_REMOVE_MARK}"


__all__ = (
    "StripState",
    "StripTarget",
    "compile_strip_scope",
    "initial_strip_state",
    "missing_strip_cells",
    "ordered_targets",
    "strip_cells",
    "strip_chip_label",
    "strip_target_for",
)
