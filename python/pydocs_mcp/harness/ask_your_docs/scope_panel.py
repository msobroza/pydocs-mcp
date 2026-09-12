"""Streamlit fragments for the scope UI (UI spec §6.7, §6.9, §6.10, §6.11).

Streamlit-only by design: every decision is made by the pure modules
(question_scope, answer_footer); this file renders widgets and writes
session state. Callbacks (``on_click``) are the only place a widget's own
session-state key is written, because they run before the next run
instantiates the widget.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.answer_footer import FollowUpChip
from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_LABELS,
    SLICE_LABELS,
    QuestionScope,
    ScopeBranchDefault,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeKind,
    ScopeSlice,
    code_compatible_with_slice,
    pin_summary_label,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.retrieval.config.ask_your_docs_models import ANY_PROJECT, ScopeDefaultsConfig

if TYPE_CHECKING:
    from streamlit.elements.widgets.chat import ChatInputValue

SOFT_DEFAULTS_CAPTION = (
    "Soft defaults — they fill in what the agent leaves unspecified. The agent may "
    "pick another indexed project or branch when the question asks for it."
)
_DEFAULTS_WIDGET_KEYS = (
    "scope_defaults_project",
    "scope_defaults_branch",
    "scope_defaults_slice",
    "scope_defaults_code",
    "scope_defaults_package",
)
_NAME_PREFIX = "name:"  # selectbox option ids for named branches
_NO_COMPARE = "(none)"


# --- shared helpers ----------------------------------------------------------


def _branch_caption(project: str, listing: WorkspaceBranchListing) -> str:
    row = listing.default_row(project)
    if row is None:
        return "no branch information"
    return f"branch: {row.name} @{row.head_sha[:7]} (checked out)"


def _slice_options(capabilities: ScopeCapabilities) -> list[ScopeSlice]:
    options = [ScopeSlice.WHOLE_BRANCH]
    if capabilities.changed_slice:
        options.append(ScopeSlice.CHANGED_FILES)
    if capabilities.diff_slice:
        options.append(ScopeSlice.DIFF_HUNKS)
    return options


def _render_slice_radio(
    key: str, initial: ScopeSlice, capabilities: ScopeCapabilities, *, disabled: bool
) -> ScopeSlice:
    """The Slice radio — hidden until the server advertises a slice value (U2);
    disabled (and whole-branch) while the code filter is dependencies-only (E11)."""
    options = _slice_options(capabilities)
    if len(options) == 1:
        return ScopeSlice.WHOLE_BRANCH
    index = options.index(initial) if initial in options else 0
    picked = st.radio(
        "Slice",
        options,
        index=index,
        format_func=SLICE_LABELS.get,
        horizontal=True,
        key=key,
        disabled=disabled,
    )
    return ScopeSlice.WHOLE_BRANCH if disabled else ScopeSlice(picked)


# --- "Scope defaults" button + panel (§6.7 state 2) --------------------------


def render_scope_defaults_button() -> None:
    """The one sidebar button; the panel stays open for the session once clicked."""
    if st.button("Scope defaults", key="scope_defaults_button"):
        st.session_state["scope_defaults_open"] = not st.session_state.get(
            "scope_defaults_open", False
        )
        st.rerun()


def _reset_defaults_widgets() -> None:
    # Popped BEFORE the widgets render this run, so each re-seeds from the YAML value.
    for key in _DEFAULTS_WIDGET_KEYS:
        st.session_state.pop(key, None)


def _branch_option_labels(project: str, listing: WorkspaceBranchListing) -> dict[str, str]:
    """option id -> label: the two symbolic entries, then the pickable names
    (a named entry needs a project; under "any" only the symbolic ones)."""
    default = listing.default_row(project) if project != ANY_PROJECT else None
    base = default.base_name if default and default.base_name else "main"
    labels = {
        ScopeBranchDefault.BASE.value: f"{base} (base branch)",
        ScopeBranchDefault.CHECKED_OUT.value: "checked-out branch",
    }
    if project != ANY_PROJECT:
        labels.update({f"{_NAME_PREFIX}{r.name}": r.name for r in listing.pickable(project)})
    return labels


def _render_branch_default_row(
    project: str,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    config: ScopeDefaultsConfig,
) -> tuple[ScopeBranchDefault, str]:
    if not capabilities.branch_selector:  # U0: informational, nothing can be sent
        names = listing.project_names if project == ANY_PROJECT else (project,)
        for name in names:
            st.caption(f"{name} — {_branch_caption(name, listing)}")
        return config.branch_default, config.branch_name
    labels = _branch_option_labels(project, listing)
    options = list(labels)
    initial = (
        f"{_NAME_PREFIX}{config.branch_name}" if config.branch_name else config.branch_default.value
    )
    index = options.index(initial) if initial in options else 0
    picked = st.selectbox(
        "Branch", options, index=index, format_func=labels.get, key="scope_defaults_branch"
    )
    if picked.startswith(_NAME_PREFIX):
        return ScopeBranchDefault.BASE, picked.removeprefix(_NAME_PREFIX)
    return ScopeBranchDefault(picked), ""


def _render_package_picker(
    project: str, code: ScopeCode, catalog: dict[str, list[str]], config: ScopeDefaultsConfig
) -> str:
    if code is ScopeCode.OWN:  # packages are dependencies (today's rule)
        return ""
    pool = sorted(
        {
            p
            for name, pkgs in catalog.items()
            if project == ANY_PROJECT or name == project
            for p in pkgs
        }
    )
    if not pool:
        return ""
    options = ["", *pool]
    index = options.index(config.package) if config.package in options else 0
    return st.selectbox(
        "Package",
        options,
        index=index,
        format_func=lambda p: p or "All packages",
        key="scope_defaults_package",
    )


def _render_code_radio(config: ScopeDefaultsConfig) -> ScopeCode:
    codes = list(ScopeCode)
    picked = st.radio(
        "Code",
        codes,
        index=codes.index(config.code),
        format_func=CODE_LABELS.get,
        horizontal=True,
        key="scope_defaults_code",
    )
    return ScopeCode(picked)


def render_scope_defaults_panel(
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> ScopeDefaultsOverride:
    """The panel below the button; ``ScopeDefaultsOverride()`` (all None) while closed."""
    if not st.session_state.get("scope_defaults_open"):
        return ScopeDefaultsOverride()
    if st.button("Reset to shipped", key="scope_defaults_reset"):
        _reset_defaults_widgets()
        st.rerun()
    projects = [ANY_PROJECT, *listing.project_names]
    index = projects.index(config.project) if config.project in projects else 0
    project = st.selectbox("Project", projects, index=index, key="scope_defaults_project")
    branch_default, branch_name = _render_branch_default_row(project, listing, capabilities, config)
    code = _render_code_radio(config)
    slice_value = _render_slice_radio(
        "scope_defaults_slice", config.slice, capabilities, disabled=code is ScopeCode.DEPS
    )
    package = _render_package_picker(project, code, catalog, config)
    st.caption(SOFT_DEFAULTS_CAPTION)
    return ScopeDefaultsOverride(
        project=project,
        branch_default=branch_default,
        branch_name=branch_name,
        slice=slice_value,
        code=code,
        package=package,
    )


# --- the pin popover (§6.10) --------------------------------------------------


def _apply_pin(
    project: str, branches: tuple[str, ...], slice_value: ScopeSlice, defaults: QuestionScope
) -> None:
    # on_click callback: runs before the rerun, so the popover's own key is writable.
    cells = tuple(ScopeCell(project, b) for b in branches) or (ScopeCell(project, ""),)
    st.session_state["scope_pin"] = QuestionScope(
        kind=ScopeKind.PIN,
        cells=cells,
        slice=slice_value,
        code=code_compatible_with_slice(slice_value, defaults.code),
        package=defaults.package,
    )
    st.session_state["scope_pin_popover"] = False


def _clear_pin() -> None:
    st.session_state["scope_pin"] = None
    st.session_state["scope_pin_popover"] = False


def _render_pin_branches(
    project: str, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> tuple[str, ...]:
    if not capabilities.branch_selector:
        st.caption(_branch_caption(project, listing))
        return ()
    options = [r.name for r in listing.pickable(project)]
    # A closed list by construction: st.multiselect accepts no free text (R6).
    return tuple(st.multiselect("Branches", options, key="scope_pin_branches"))


def _render_pin_controls(
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    defaults: QuestionScope,
    max_cells: int,
) -> None:
    projects = list(listing.project_names)
    if not projects:
        st.caption("No indexed projects in this workspace.")
        return
    project = st.selectbox("Project", projects, key="scope_pin_project")
    branches = _render_pin_branches(project, listing, capabilities)
    slice_value = _render_slice_radio(
        "scope_pin_slice",
        ScopeSlice.WHOLE_BRANCH,
        capabilities,
        disabled=defaults.code is ScopeCode.DEPS,
    )
    st.toggle("keep for next", key="scope_pin_keep")
    _render_pin_buttons(project, branches, slice_value, defaults, max_cells)


def _render_pin_buttons(
    project: str,
    branches: tuple[str, ...],
    slice_value: ScopeSlice,
    defaults: QuestionScope,
    max_cells: int,
) -> None:
    """Pin (disabled past the fan-out cap, E4) and Clear — both on_click callbacks."""
    count = max(len(branches), 1)
    too_many = count > max_cells
    if too_many:
        st.caption(f"{count} cells exceed max_cells={max_cells} (ask_your_docs.scope.max_cells)")
    pin_col, clear_col = st.columns(2)
    pin_col.button(
        "Pin",
        key="scope_pin_apply",
        disabled=too_many,
        on_click=_apply_pin,
        args=(project, branches, slice_value, defaults),
    )
    clear_col.button("Clear", key="scope_pin_clear", on_click=_clear_pin)


def render_scope_pin_popover(
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    defaults: QuestionScope,
    max_cells: int,
) -> None:
    """The icon button left of the chat input; its label is the pin summary."""
    label = pin_summary_label(st.session_state.get("scope_pin")) or "scope"
    with st.popover(label, key="scope_pin_popover", on_change="rerun", icon=":material/tune:"):
        _render_pin_controls(listing, capabilities, defaults, max_cells)


def render_composer_row(
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
    defaults: QuestionScope,
    max_cells: int,
) -> str | ChatInputValue | None:
    """The pin popover left of the chat input, pinned by the bottom container
    (Streamlit >= 1.57); returns the chat input's submission, if any."""
    with st.bottom:
        pin_col, input_col = st.columns([1, 12])
        with pin_col:
            render_scope_pin_popover(listing, capabilities, defaults, max_cells)
        with input_col:
            return st.chat_input(
                "Ask about your indexed projects…",
                accept_file="multiple",
                file_type=["png", "jpg", "jpeg", "webp", "gif"],
            )


# --- the chip row (§6.7 state 3) ---------------------------------------------


def _symbol_of(attachment: AttachedSymbol | str) -> str:
    return attachment.symbol if isinstance(attachment, AttachedSymbol) else attachment


def _pin_chips(pin: QuestionScope | None) -> list[tuple[str, str, ScopeCell | None]]:
    """(label, key, cell) per pin element; ``None`` cell = the slice chip."""
    if pin is None:
        return []
    chips = [
        (f"✕ {cell.branch or cell.project}", f"scope_chip_{cell.project}_{cell.branch}", cell)
        for cell in pin.cells
    ]
    if pin.slice is not ScopeSlice.WHOLE_BRANCH:
        chips.append((f"✕ {SLICE_LABELS[pin.slice]}", "scope_chip_slice", None))
    return chips


def _remove_pin_element(pin: QuestionScope, cell: ScopeCell | None) -> None:
    st.session_state["scope_pin"] = (
        replace(pin, slice=ScopeSlice.WHOLE_BRANCH) if cell is None else pin.without_cell(cell)
    )


def render_scope_chip_row(attached: list[AttachedSymbol | str], pin: QuestionScope | None) -> None:
    """Pin element chips first, then attached symbols, then "clear all" (both)."""
    pin_chips = _pin_chips(pin)
    if not pin_chips and not attached:
        return
    cols = st.columns(len(pin_chips) + len(attached) + 1)
    for col, (label, key, cell) in zip(cols, pin_chips, strict=False):
        if col.button(label, key=key):
            _remove_pin_element(pin, cell)
            st.rerun()
    for col, attachment in zip(cols[len(pin_chips) :], list(attached), strict=False):
        symbol = _symbol_of(attachment)
        if col.button(f"✕ {symbol.rsplit('.', 1)[-1]}", key=f"chip_{symbol}"):
            attached.remove(attachment)
            st.rerun()
    if cols[-1].button("clear all", key="chip_clear"):
        attached.clear()
        st.session_state["scope_pin"] = None
        st.rerun()


# --- follow-up chips (§6.9) --------------------------------------------------


def render_follow_up_chips(index: int, chips: Sequence[FollowUpChip]) -> FollowUpChip | None:
    """Small buttons under the footer; returns the clicked chip, if any."""
    if not chips:
        return None
    clicked: FollowUpChip | None = None
    for col, chip in zip(st.columns(len(chips)), chips, strict=True):
        if col.button(chip.label, key=f"follow_up_{index}_{chip.kind.value}"):
            clicked = chip
    return clicked


# --- graph page row (§6.11) --------------------------------------------------


@dataclass(frozen=True, slots=True)
class GraphBranchSelection:
    branch: str
    compare_with: str | None
    changed_only: bool


def render_graph_branch_row(
    listing: WorkspaceBranchListing,
    project: str,
    capabilities: ScopeCapabilities,
    default_branch: str,
) -> GraphBranchSelection:
    """Branch selectbox (+ "Compare with" and "changed only" on U1); a caption on U0."""
    if not capabilities.branch_selector:
        st.caption(_branch_caption(project, listing))
        row = listing.default_row(project)
        return GraphBranchSelection(row.name if row else "", None, False)
    names = [r.name for r in listing.pickable(project)] or ["—"]
    index = names.index(default_branch) if default_branch in names else 0
    branch = st.selectbox("Branch", names, index=index, key="graph_branch")
    compare_options = [_NO_COMPARE, *[n for n in names if n != branch]]
    compare = st.selectbox("Compare with", compare_options, key="graph_compare_with")
    changed_only = st.checkbox(
        "changed only", value=False, key="graph_changed_only", disabled=compare == _NO_COMPARE
    )
    return GraphBranchSelection(branch, None if compare == _NO_COMPARE else compare, changed_only)


# --- pin lifecycle (§6.7 rule iv) --------------------------------------------


def _missing_cell(pin: QuestionScope, listing: WorkspaceBranchListing) -> ScopeCell | None:
    for cell in pin.cells:
        if not listing.knows_project(cell.project):
            return cell
        if cell.branch and not listing.has_branch(cell.project, cell.branch):
            return cell
    return None


def drop_pin_if_listing_changed(listing: WorkspaceBranchListing, workspace: str) -> None:
    """A workspace change reloads the listing; a pin with a cell the new
    listing lacks is dropped whole, with a toast naming the missing cell (E12)."""
    previous = st.session_state.get("scope_listing_workspace")
    st.session_state["scope_listing_workspace"] = workspace
    pin = st.session_state.get("scope_pin")
    if previous is None or previous == workspace or pin is None:
        return
    missing = _missing_cell(pin, listing)
    if missing is not None:
        st.session_state["scope_pin"] = None
        st.toast(
            f"Pin dropped: {missing.project} · {missing.branch or 'default'} "
            "is not in this workspace"
        )


__all__ = (
    "SOFT_DEFAULTS_CAPTION",
    "GraphBranchSelection",
    "drop_pin_if_listing_changed",
    "render_composer_row",
    "render_follow_up_chips",
    "render_graph_branch_row",
    "render_scope_chip_row",
    "render_scope_defaults_button",
    "render_scope_defaults_panel",
    "render_scope_pin_popover",
)
