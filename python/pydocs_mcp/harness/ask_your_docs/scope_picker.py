"""The "Where to search" picker — ONE keyed popover shared by the chat page (the strip's
"Change…" trigger) and the graph page (its sidebar popover), with the same widget keys
(UI spec §6.7, §6.10, §6.11). Streamlit-only; the decisions are strip_state's.

Every write to the strip or to a picker widget's key happens in an ``on_click`` /
``on_change`` callback — the only place a widget's own key may be written, because
callbacks run before the next run instantiates it (P15).

Example:
    render_where_to_search_picker(PICKER_KEY, "Change…", state, cfg, catalog, listing, caps)
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TypeVar

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import (
    CODE_LABELS,
    SLICE_LABELS,
    ScopeCell,
    ScopeCode,
    ScopeDefaultsOverride,
    ScopeSlice,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.ask_your_docs.scope_panel import branch_caption
from pydocs_mcp.harness.ask_your_docs.strip_state import (
    StripState,
    StripTarget,
    initial_strip_state,
    ordered_targets,
    strip_cells,
    strip_target_for,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

_T = TypeVar("_T")

PICKER_KEY = "scope_picker"  # the chat page's popover; the graph page keys its own
STRIP_STATE_KEY = "scope_strip"
ONLY_THESE_KEY = "scope_strip_only_these"
PICKER_TITLE = "Where to search"
USE_THESE_LABEL = "Use these"
RESET_LABEL = "Reset"
MORE_LABEL = "More"
ALL_PACKAGES_LABEL = "All packages"
ALL_PROJECTS_WORDS = "all projects"
NO_PROJECTS_CAPTION = "No indexed projects in this workspace."
# The YAML key the over-cap captions name (spec §6.4a, AC-44): one spelling, two pages.
MAX_CELLS_YAML_KEY = "ask_your_docs.scope.max_cells"
_WIDGET_PREFIX = "scope_picker_"


def _widget_key(name: str) -> str:
    """``scope_picker_<name>`` — the body's keys are app-wide, the popover's is per page."""
    return f"{_WIDGET_PREFIX}{name}"


def forget_picker_widgets() -> None:
    """Pop every picker widget key so the next run re-seeds it from the strip and the YAML.
    Callbacks only: popping a key after its widget instantiated silently discards that value."""
    for key in [k for k in st.session_state if str(k).startswith(_WIDGET_PREFIX)]:
        st.session_state.pop(key, None)


def seed_widget_once(key: str, value: object) -> None:
    """Seed a widget's key BEFORE it instantiates (the strip's row 2 uses it too) —
    ``setdefault``, never ``value=``, or Streamlit warns "created with a default value"."""
    st.session_state.setdefault(key, value)


def cell_label(cell: ScopeCell) -> str:
    """``backend · main``, or the bare project on a branchless (E8) cell."""
    return f"{cell.project} · {cell.branch}" if cell.branch else cell.project


def _layered(chosen: _T | None, yaml_value: _T) -> _T:
    """A "More" value the session chose, else the YAML one."""
    return yaml_value if chosen is None else chosen


def _render_branch_row(
    name: str,
    target: StripTarget | None,
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> StripTarget:
    if not capabilities.branch_selector:  # U0: informational — nothing branch-shaped is sent
        st.caption(branch_caption(name, listing))
        return strip_target_for(name, listing)
    names = [r.name for r in listing.pickable(name)]  # U2 (Task 17): the merged group follows
    key = _widget_key(f"branches_{name}")
    wanted = target.branches if target else strip_target_for(name, listing).branches
    seed_widget_once(key, [b for b in wanted if b in names])
    # A closed list by construction: pills accept no free text (R6).
    picked = st.pills("Branches", names, selection_mode="multi", key=key)
    return StripTarget(name, tuple(picked))


def _render_project_rows(
    state: StripState, listing: WorkspaceBranchListing, capabilities: ScopeCapabilities
) -> tuple[StripTarget, ...]:
    current = {t.project: t for t in state.targets}
    ticked: list[StripTarget] = []
    for name in listing.project_names:
        key = _widget_key(f"project_{name}")
        seed_widget_once(key, name in current)
        if st.checkbox(name, key=key):
            ticked.append(_render_branch_row(name, current.get(name), listing, capabilities))
    return tuple(ticked)


def _render_code_radio(more: ScopeDefaultsOverride, config: ScopeDefaultsConfig) -> ScopeCode:
    key = _widget_key("code")
    seed_widget_once(key, _layered(more.code, config.code))
    return ScopeCode(
        st.radio("Code", list(ScopeCode), format_func=CODE_LABELS.get, horizontal=True, key=key)
    )


def _package_pool(projects: Sequence[str], catalog: dict[str, list[str]]) -> list[str]:
    """The ticked projects' dependency packages (every project's with no tick)."""
    return sorted(
        {p for name, pkgs in catalog.items() if not projects or name in projects for p in pkgs}
    )


def _render_package_picker(
    projects: Sequence[str],
    code: ScopeCode,
    catalog: dict[str, list[str]],
    more: ScopeDefaultsOverride,
    config: ScopeDefaultsConfig,
) -> str | None:
    if code is ScopeCode.OWN:  # packages are dependencies (today's rule)
        # Never "": a hidden control chooses nothing, and "" would overwrite the held package.
        return more.package
    pool = _package_pool(projects, catalog)
    if not pool:
        return ""
    key = _widget_key("package")
    wanted = _layered(more.package, config.package)
    seed_widget_once(key, wanted if wanted in pool else "")
    # A tick change can shrink the pool under a chosen package: a keyed selectbox whose
    # session value left its options falls back to the first one ("" = every package).
    return st.selectbox(
        "Package", ["", *pool], format_func=lambda p: p or ALL_PACKAGES_LABEL, key=key
    )


def _slice_options(capabilities: ScopeCapabilities) -> list[ScopeSlice]:
    options = [ScopeSlice.WHOLE_BRANCH]
    if capabilities.changed_slice:
        options.append(ScopeSlice.CHANGED_FILES)
    if capabilities.diff_slice:
        options.append(ScopeSlice.DIFF_HUNKS)
    return options


def _render_files_radio(
    more: ScopeDefaultsOverride,
    config: ScopeDefaultsConfig,
    capabilities: ScopeCapabilities,
    *,
    disabled: bool,
) -> ScopeSlice | None:
    """ "Which files" — absent (None: YAML answers) until the server advertises a slice
    value (U2); disabled, and everything on the branch, while code is dependencies-only
    (E11)."""
    options = _slice_options(capabilities)
    if len(options) == 1:
        return None
    key = _widget_key("files")
    wanted = _layered(more.slice, config.slice)
    seed_widget_once(key, wanted if wanted in options else ScopeSlice.WHOLE_BRANCH)
    picked = st.radio(
        "Which files", options, format_func=SLICE_LABELS.get, key=key, disabled=disabled
    )
    return ScopeSlice.WHOLE_BRANCH if disabled else ScopeSlice(picked)


def _render_more(
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    ticked: tuple[StripTarget, ...],
    capabilities: ScopeCapabilities,
) -> ScopeDefaultsOverride:
    with st.expander(MORE_LABEL):
        code = _render_code_radio(state.more, config)
        projects = tuple(t.project for t in ticked)
        package = _render_package_picker(projects, code, catalog, state.more, config)
        slice_value = _render_files_radio(
            state.more, config, capabilities, disabled=code is ScopeCode.DEPS
        )
    return ScopeDefaultsOverride(slice=slice_value, code=code, package=package)


def _adopt_strip(popover_key: str, state: StripState) -> None:
    # Both buttons' write, in a callback: the one place a widget's own key may be set (P15).
    st.session_state[STRIP_STATE_KEY] = state
    st.session_state.pop(ONLY_THESE_KEY, None)  # row 2 re-seeds the box from the state
    forget_picker_widgets()
    st.session_state[popover_key] = False  # only here: the callback precedes the rerun (V2)


def _use_these(
    popover_key: str, targets: tuple[StripTarget, ...], more: ScopeDefaultsOverride
) -> None:
    # on_click: every ticked row is KEPT (never replaced) and the person's "Only these"
    # tick survives — the held state is read here, at click time, not at render.
    current = st.session_state.get(STRIP_STATE_KEY, StripState())
    _adopt_strip(popover_key, replace(current, targets=targets, more=more))


def _reset_picker(
    popover_key: str, config: ScopeDefaultsConfig, listing: WorkspaceBranchListing
) -> None:
    # on_click: the YAML seed of first load (R3), "Only these" off, every widget re-seeded.
    _adopt_strip(popover_key, initial_strip_state(config, listing))


def _forget_when_closed(popover_key: str) -> None:
    # on_change of the popover itself: a close without "Use these" (an outside click) throws the
    # half-edited rows away, so the next opening shows the strip (§6.7). That close is the one
    # path AppTest cannot drive — this callback is pinned in test_scope_state_writes.py.
    if not st.session_state.get(popover_key):
        forget_picker_widgets()


def _render_preview(cells: Sequence[ScopeCell], max_cells: int) -> bool:
    """The live count line (§6.4a); True when the selection is over the cap."""
    count = max(len(cells), 1)  # no target still runs one search: the union
    noun = "search" if count == 1 else "searches"
    shown = ", ".join(cell_label(c) for c in cells) or ALL_PROJECTS_WORDS
    st.caption(f"Next question runs {count} {noun}: {shown} · {count} of {max_cells}")
    if count <= max_cells:
        return False
    st.caption(
        f"{count} searches is over the limit of {max_cells} ({MAX_CELLS_YAML_KEY}) — "
        "untick a project or a branch"
    )
    return True


def render_where_to_search_picker(
    popover_key: str,
    label: str,
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> None:
    """One component, two pages: the keyed popover IS its trigger button (§6.10), its
    body the rows, "More", the preview and the two buttons (§6.7). The body always
    executes (V3), so its widgets are addressable in AppTest without opening it — never
    gate it on ``.open``. ``width="stretch"`` (not the deprecated ``use_container_width``)
    fills the column the caller gives the trigger, keeping its label on one line."""
    with st.popover(
        label, key=popover_key, width="stretch", on_change=_forget_when_closed, args=(popover_key,)
    ):
        st.markdown(f"**{PICKER_TITLE}**")
        if not listing.has_projects:
            st.caption(NO_PROJECTS_CAPTION)
            return
        ticked = _render_project_rows(state, listing, capabilities)
        more = _render_more(state, config, catalog, ticked, capabilities)
        targets = ordered_targets(ticked, listing)
        over = _render_preview(strip_cells(targets), config.max_cells)
        use, reset = st.columns(2)
        use_args = (popover_key, targets, more)
        use.button(
            USE_THESE_LABEL,
            key=_widget_key("use"),
            disabled=over,
            on_click=_use_these,
            args=use_args,
        )
        reset_args = (popover_key, config, listing)
        reset.button(RESET_LABEL, key=_widget_key("reset"), on_click=_reset_picker, args=reset_args)


__all__ = (
    "MAX_CELLS_YAML_KEY",
    "ONLY_THESE_KEY",
    "PICKER_KEY",
    "PICKER_TITLE",
    "STRIP_STATE_KEY",
    "cell_label",
    "forget_picker_widgets",
    "render_where_to_search_picker",
    "seed_widget_once",
)
