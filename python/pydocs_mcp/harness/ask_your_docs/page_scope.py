"""The chat page's scope plumbing: the workspace scan, the capability record, the footer.

Split out of ``app.py`` (its line budget): what the page READS for the scope UI —
the cached branch listing beside the catalog, the server's capability record, and the
footer + chips an answered turn leaves in the transcript. The widgets live in
``scope_panel``; the decisions in ``question_scope`` / ``answer_footer``.

Example:
    catalog, listing = scan_workspace(workspace, load_catalog)
    footer, chips = answer_footer_and_chips(turn, handle, listing)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.answer_footer import (
    FollowUpChip,
    derive_follow_up_chips,
    render_answer_footer,
)
from pydocs_mcp.harness.ask_your_docs.catalog import (
    EMPTY_BRANCH_LISTING,
    WorkspaceBranchListing,
    workspace_branch_listing,
)
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import (
    NO_SCOPE_CAPABILITIES,
    ScopeCapabilities,
)

if TYPE_CHECKING:
    from pydocs_mcp.harness.ask_your_docs.page_agent import PageAgentHandle
    from pydocs_mcp.harness.ask_your_docs.page_turn import AskTurn

SCOPE_CAPABILITIES_KEY = "scope_capabilities"  # seeded by tests, else learned per turn


@st.cache_resource
def load_branch_listing(workspace: str) -> WorkspaceBranchListing:
    # Same lifetime as the page's catalog cache; feeds the panel, the popover and the
    # footer. Read-only — never mutates the bundles.
    return workspace_branch_listing(workspace)


def scan_workspace(
    workspace: str, load_catalog: Callable[[str], dict[str, list[str]]]
) -> tuple[dict[str, list[str]], WorkspaceBranchListing]:
    """The catalog and the branch listing, or empty ones plus a warning (never an error)."""
    if not workspace:
        return {}, EMPTY_BRANCH_LISTING
    try:
        return load_catalog(workspace), load_branch_listing(workspace)
    except Exception as exc:  # unreadable dir, no bundles, corrupt db
        st.warning(f"Couldn't scan workspace: {exc}")
        return {}, EMPTY_BRANCH_LISTING


def page_scope_capabilities() -> ScopeCapabilities:
    """The server's scope capabilities for this page (UI spec §6.12).

    A seeded ``st.session_state["scope_capabilities"]`` (tests) wins; otherwise the
    record the last turn learned from the page's held session — the agent is lazy, so
    nothing spawns at render and the first turn's answer teaches the sidebar. Absent
    both, every branch control stays hidden (R10: never an error from a capability).
    """
    seeded = st.session_state.get(SCOPE_CAPABILITIES_KEY)
    return seeded if isinstance(seeded, ScopeCapabilities) else NO_SCOPE_CAPABILITIES


def answer_footer_and_chips(
    turn: AskTurn, handle: PageAgentHandle | None, listing: WorkspaceBranchListing
) -> tuple[str, tuple[FollowUpChip, ...]]:
    """The footer line and follow-up chips from the turn's observations (§6.8–§6.9).

    The held session's capability record is stored for the next run's sidebar and the
    graph page; the kept pin active AFTER the send decides which cells can still be pinned.
    """
    caps = handle.scope_capabilities if handle is not None else NO_SCOPE_CAPABILITIES
    st.session_state[SCOPE_CAPABILITIES_KEY] = caps
    footer = render_answer_footer(turn.observations, listing)
    kept_pin = st.session_state.get("scope_pin")
    return footer, derive_follow_up_chips(turn.observations, listing, caps, kept_pin)


__all__ = (
    "SCOPE_CAPABILITIES_KEY",
    "answer_footer_and_chips",
    "load_branch_listing",
    "page_scope_capabilities",
    "scan_workspace",
)
