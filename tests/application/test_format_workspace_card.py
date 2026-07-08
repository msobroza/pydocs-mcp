"""Golden layout for ``format_workspace_overview_card`` — the multi-repo
workspace orientation card.

Pure rendering test mirroring ``test_format_overview.py``: builds
``WorkspaceProjectEntry`` fixtures and asserts the H1, the one census line,
the ``## Projects`` block, and the per-project ``overview`` pointer tokens
that deepen into each project's §D17 card via ``get_overview(project=...)``.
"""

from __future__ import annotations

import pytest

from pydocs_mcp.application.formatting import (
    format_workspace_overview_card,
    resolve_pointers,
)
from pydocs_mcp.application.overview_service import WorkspaceProjectEntry


def test_golden_workspace_card_layout() -> None:
    out = format_workspace_overview_card(
        (
            WorkspaceProjectEntry(name="backend", package_count=12),
            WorkspaceProjectEntry(name="frontend", package_count=7),
        )
    )
    assert out.startswith("# Workspace overview\n")
    assert "[2 projects · 19 packages]" in out
    assert "## Projects" in out
    assert "- **backend** — 12 packages [[next:overview:backend]]" in out
    assert "- **frontend** — 7 packages [[next:overview:frontend]]" in out
    assert out.endswith("\n") and not out.endswith("\n\n")


def test_workspace_card_preserves_loaded_order() -> None:
    # Entries render in loaded (workspace-glob) order, not re-sorted — the card
    # must stay deterministic and match what discover_workspace loaded.
    out = format_workspace_overview_card(
        (
            WorkspaceProjectEntry(name="zeta", package_count=1),
            WorkspaceProjectEntry(name="alpha", package_count=1),
        )
    )
    assert out.index("zeta") < out.index("alpha")


@pytest.mark.parametrize("surface", ["mcp", "cli"])
@pytest.mark.parametrize("bad_name", ["foo:bar", "a]b", "has space"])
def test_selector_unsafe_name_emits_no_pointer(bad_name: str, surface: str) -> None:
    # Project names come from filesystem basenames and bypass the project=
    # selector validator. A name that isn't a valid selector must NOT get a
    # deepening pointer: the token would be malformed/leaked AND the target
    # would be rejected by get_overview(project=...). The census line stays.
    card = format_workspace_overview_card((WorkspaceProjectEntry(name=bad_name, package_count=4),))
    resolved = resolve_pointers(card, surface)
    assert "[[next:" not in resolved  # no raw/leaked token survives resolution
    assert "→ " not in resolved  # and no deepening pointer was emitted at all
    assert f"**{bad_name}** — 4 packages" in card  # census line still renders


def test_selector_safe_name_still_gets_pointer() -> None:
    # The guard must not suppress pointers for ordinary identifier-style names.
    card = format_workspace_overview_card((WorkspaceProjectEntry(name="my_proj", package_count=1),))
    assert "[[next:overview:my_proj]]" in card
    assert resolve_pointers(card, "mcp").rstrip().endswith('→ get_overview(project="my_proj")')
