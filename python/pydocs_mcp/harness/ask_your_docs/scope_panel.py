"""Streamlit fragments shared by the pages: the attachment chip row, the follow-up
chips, the graph page's branch row and the branch caption (UI spec §6.9, §6.10, §6.11).
The strip and the picker live in scope_strip / scope_picker.

Streamlit-only by design: every decision is made by the pure modules
(question_scope, answer_footer); this file renders widgets and writes session state.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import streamlit as st

from pydocs_mcp.harness.ask_your_docs.answer_footer import FollowUpChip
from pydocs_mcp.harness.ask_your_docs.attachments import AttachedSymbol
from pydocs_mcp.harness.ask_your_docs.catalog import WorkspaceBranchListing
from pydocs_mcp.harness.ask_your_docs.scope_capabilities import ScopeCapabilities

_NO_COMPARE = "(none)"
NO_BRANCH_INFORMATION = "no branch information"


def branch_caption(project: str, listing: WorkspaceBranchListing) -> str:
    """The read-only U0 line under a project: the stamped branch, nothing sendable."""
    row = listing.default_row(project)
    if row is None:
        return NO_BRANCH_INFORMATION
    return f"indexed on {row.name} @{row.head_sha[:7]}"


# --- the attachment chip row (§6.10) -----------------------------------------


def _symbol_of(attachment: AttachedSymbol | str) -> str:
    return attachment.symbol if isinstance(attachment, AttachedSymbol) else attachment


def render_attachment_chip_row(attached: list[AttachedSymbol | str]) -> None:
    """Attached-symbol chips and "clear all" — attachments only; the strip owns its
    own chips and its own "Clear" (§6.10)."""
    if not attached:
        return
    cols = st.columns(len(attached) + 1)
    for col, attachment in zip(cols, list(attached), strict=False):
        symbol = _symbol_of(attachment)
        if col.button(f"✕ {symbol.rsplit('.', 1)[-1]}", key=f"chip_{symbol}"):
            attached.remove(attachment)
            st.rerun()
    if cols[-1].button("clear all", key="chip_clear"):
        attached.clear()
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
        st.caption(branch_caption(project, listing))
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


__all__ = (
    "GraphBranchSelection",
    "branch_caption",
    "render_attachment_chip_row",
    "render_follow_up_chips",
    "render_graph_branch_row",
)
