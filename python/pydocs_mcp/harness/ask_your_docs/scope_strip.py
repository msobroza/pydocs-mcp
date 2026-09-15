"""The "Searching in …" strip above the chat input (UI spec §6.7, §6.10).

Two rows inside ``st.bottom``, then a plain ``st.chat_input``. The state is sticky
(session state) and compiles to the question scope through ``compile_strip_scope``.
Every mutation is an ``on_click`` / ``on_change`` callback (see scope_picker); the one
render-path write is row 2's forcing of the "Only these" key, which precedes that
checkbox's instantiation (P15).

Example:
    strip = current_strip_state(config.scope, listing)
    submission = render_composer_row(strip, config.scope, catalog, listing, capabilities)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.question_scope import ScopeCell
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities
from pydocs_mcp.harness.ask_your_docs.scope_picker import (
    MAX_CELLS_YAML_KEY,
    ONLY_THESE_KEY,
    PICKER_KEY,
    STRIP_STATE_KEY,
    cell_label,
    forget_picker_widgets,
    render_where_to_search_picker,
    seed_widget_once,
)
from pydocs_mcp.harness.ask_your_docs.strip_state import (
    StripState,
    initial_strip_state,
    missing_strip_cells,
    strip_cells,
    strip_chip_label,
    strip_target_for,
)
from pydocs_mcp.retrieval.config.ask_your_docs_models import ScopeDefaultsConfig

if TYPE_CHECKING:
    from streamlit.elements.widgets.chat import ChatInputValue

NO_TARGET_SENTENCE = "Searching in all projects, each on its indexed branch"
SEARCHING_IN_LABEL = "Searching in"
ONLY_THESE_LABEL = "Only these — the agent never searches elsewhere"
FORCED_HINT = "several targets always run as separate searches"
OVER_CAP_HINT = "remove a target before asking"
CHANGE_LABEL = "Change…"
CLEAR_LABEL = "Clear"
CLEAR_KEY = "scope_strip_clear"
# The workspace the strip was last checked against (E12): not a widget key.
WORKSPACE_MARK_KEY = "scope_listing_workspace"
CHAT_INPUT_PLACEHOLDER = "Ask about your indexed projects…"
IMAGE_FILE_TYPES = ["png", "jpg", "jpeg", "webp", "gif"]


def current_strip_state(config: ScopeDefaultsConfig, listing: WorkspaceBranchListing) -> StripState:
    """The sticky state, seeded from YAML on a session's first run (R3)."""
    if STRIP_STATE_KEY not in st.session_state:
        st.session_state[STRIP_STATE_KEY] = initial_strip_state(config, listing)
    return st.session_state[STRIP_STATE_KEY]


def _write_strip(state: StripState) -> None:
    # Before any strip / picker widget instantiates (a callback, or the page's pre-render
    # handlers): the picker's rows AND row 2's checkbox re-seed from the state on the
    # next run — the box's key is dropped here so a forced value never sticks.
    st.session_state[STRIP_STATE_KEY] = state
    st.session_state.pop(ONLY_THESE_KEY, None)
    forget_picker_widgets()


def _remove_cell(state: StripState, cell: ScopeCell) -> None:
    _write_strip(state.without_cell(cell))


def _clear_strip(state: StripState) -> None:
    _write_strip(state.cleared())  # cleared() also resets only_these


def _set_only_these(state: StripState) -> None:
    # on_change of the checkbox: the person's own tick lives in the STATE, so it survives
    # a page that does not render the strip (Streamlit drops unrendered widget keys).
    # Never reached while the box is forced (a disabled widget fires no on_change).
    st.session_state[STRIP_STATE_KEY] = replace(
        state, only_these=bool(st.session_state[ONLY_THESE_KEY])
    )


def store_strip_state(state: StripState) -> None:
    """The strip a follow-up chip handed back, written whatever the chip's kind (§6.9, AC-31).

    A state a "Keep searching" chip grew goes through the full re-seed, so the picker's rows
    and row 2's checkbox follow it; a state the other chips returned untouched is written
    as-is, so sending a canned question never discards picker ticks the person has not
    pressed "Use these" on yet.
    """
    if state == st.session_state.get(STRIP_STATE_KEY):
        st.session_state[STRIP_STATE_KEY] = state
        return
    _write_strip(state)


# --- a workspace change (E12) -------------------------------------------------


def _narrowed_by_project(
    state: StripState, listing: WorkspaceBranchListing
) -> tuple[tuple[ScopeCell, ...], StripState]:
    """U0: a target is matched by PROJECT only — nothing branch-shaped is sent — and a
    project the new listing still indexes follows its new stamped row (AC-41)."""
    known = [t for t in state.targets if listing.knows_project(t.project)]
    dropped = tuple(c for t in state.targets if t not in known for c in t.cells())
    refreshed = tuple(strip_target_for(t.project, listing) for t in known)
    return dropped, replace(state, targets=refreshed)


def _narrowed_by_cell(
    state: StripState, listing: WorkspaceBranchListing
) -> tuple[tuple[ScopeCell, ...], StripState]:
    """U1: a target is matched as ``(project, branch)``; each missing cell goes alone."""
    dropped = missing_strip_cells(state, listing)
    kept = state
    for cell in dropped:
        kept = kept.without_cell(cell)
    return dropped, kept


def drop_missing_targets(
    listing: WorkspaceBranchListing, workspace: str, capabilities: ScopeCapabilities
) -> None:
    """A workspace change reloads the listing; each cell the new listing lacks is
    removed with its own toast — the others stay. Runs before the strip renders.

    The FIRST sight of a workspace narrows too: a state can predate the mark (the graph
    page seeds one, and a scan that failed seeds it against an empty listing), and such a
    state carries targets no listing ever checked. A chat page whose own first run seeds
    the state returns here on ``state is None`` instead, so nothing narrows a YAML seed.
    """
    previous = st.session_state.get(WORKSPACE_MARK_KEY)
    st.session_state[WORKSPACE_MARK_KEY] = workspace
    state = st.session_state.get(STRIP_STATE_KEY)
    if previous == workspace or state is None:
        return
    narrow = _narrowed_by_cell if capabilities.branch_selector else _narrowed_by_project
    dropped, kept = narrow(state, listing)
    for cell in dropped:
        st.toast(f"{cell_label(cell)} is no longer indexed — removed from where to search")
    if kept != state:
        _write_strip(kept)


# --- rendering ---------------------------------------------------------------


def _render_row_1(
    state: StripState, cells: tuple[ScopeCell, ...], picker: Callable[[], None]
) -> None:
    # Column weights are sized for the widest label each column carries: the trigger's
    # own ("Change…" plus the popover caret) wrapped onto two lines at the narrower
    # weights it had, and a two-target strip must still fit `project · feature/branch ✕`
    # on one line at the default (centered) page width. No test can see a wrap.
    if not cells:
        sentence, change = st.columns([4, 1])
        sentence.markdown(NO_TARGET_SENTENCE)
        with change:
            picker()
        return
    columns = st.columns([2, *([4] * len(cells)), 3])
    columns[0].markdown(SEARCHING_IN_LABEL)
    for column, cell in zip(columns[1:-1], cells, strict=True):
        column.button(
            strip_chip_label(cell),
            key=f"scope_chip_{cell.project}_{cell.branch}",
            on_click=_remove_cell,
            args=(state, cell),
        )
    with columns[-1]:
        picker()


def _render_count_line(cells: tuple[ScopeCell, ...], max_cells: int) -> None:
    if len(cells) >= 2:
        st.caption(f"{len(cells)} searches per question · limit {max_cells}")
    if len(cells) > max_cells:  # a cap lowered under an existing strip: every send hits E4
        st.caption(
            f"{len(cells)} searches is over the limit of {max_cells} ({MAX_CELLS_YAML_KEY}) — "
            f"{OVER_CAP_HINT}"
        )


def _render_row_2(state: StripState, cells: tuple[ScopeCell, ...], max_cells: int) -> None:
    forced = len(cells) >= 2
    seed_widget_once(ONLY_THESE_KEY, state.only_these)  # the person's own tick, from the state
    if forced:
        # Two or more cells imply PIN in the engine (§6.1); the box mirrors it. This is
        # the ONE non-callback write (P15): it precedes the checkbox's instantiation in
        # this run, which Streamlit allows (V2), and it never reaches StripState.only_these.
        st.session_state[ONLY_THESE_KEY] = True
    box, count, clear = st.columns([5, 3, 1])
    box.checkbox(
        ONLY_THESE_LABEL,
        key=ONLY_THESE_KEY,
        disabled=forced,
        help=FORCED_HINT if forced else None,
        on_change=_set_only_these,
        args=(state,),
    )
    with count:
        _render_count_line(cells, max_cells)
    clear.button(CLEAR_LABEL, key=CLEAR_KEY, on_click=_clear_strip, args=(state,))


def render_where_to_search_strip(
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> None:
    """Row 1 (label, chips, "Change…"), row 2 only with a target (§6.7)."""

    def picker() -> None:
        render_where_to_search_picker(
            PICKER_KEY, CHANGE_LABEL, state, config, catalog, listing, capabilities
        )

    cells = strip_cells(state.targets)
    _render_row_1(state, cells, picker)
    if cells:
        _render_row_2(state, cells, config.max_cells)


def render_composer_row(
    state: StripState,
    config: ScopeDefaultsConfig,
    catalog: dict[str, list[str]],
    listing: WorkspaceBranchListing,
    capabilities: ScopeCapabilities,
) -> str | ChatInputValue | None:
    """The strip, then a plain chat input, both pinned by ``st.bottom`` (>= 1.57);
    returns the input's submission, if any. Built in the main flow: ``st.bottom``
    refuses the sidebar and dialogs."""
    with st.bottom:
        render_where_to_search_strip(state, config, catalog, listing, capabilities)
        return st.chat_input(
            CHAT_INPUT_PLACEHOLDER, accept_file="multiple", file_type=IMAGE_FILE_TYPES
        )


__all__ = (
    "CHANGE_LABEL",
    "FORCED_HINT",
    "NO_TARGET_SENTENCE",
    "ONLY_THESE_LABEL",
    "OVER_CAP_HINT",
    "WORKSPACE_MARK_KEY",
    "current_strip_state",
    "drop_missing_targets",
    "render_composer_row",
    "render_where_to_search_strip",
    "store_strip_state",
)
