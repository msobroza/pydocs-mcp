"""Golden layout for ``format_overview_card`` — the §D17 structural card.

Pure rendering test: builds an ``OverviewCard`` fixture and asserts the H1,
the one stats line, the four H2 blocks in §D17 order, and the per-entry
next-step pointer tokens. A second test pins the enablement hint that replaces
the communities block when ``node_scores`` is disabled.
"""

from __future__ import annotations

from pydocs_mcp.application.formatting import format_overview_card
from pydocs_mcp.application.overview_service import (
    CommunityEntry,
    EntryPoint,
    ModuleEntry,
    OverviewCard,
)


def _card_fixture(*, node_scores_available: bool = True) -> OverviewCard:
    return OverviewCard(
        package="__project__",
        package_count=2,
        module_count=3,
        symbol_count=12,
        doc_coverage=2 / 3,  # 67% documented
        modules=(
            ModuleEntry("proj.core", "Core module.", 0.9),
            ModuleEntry("proj.api", "API layer.", 0.7),
        ),
        entry_points=(
            EntryPoint("demo-cli", "script"),
            EntryPoint("proj.__main__", "module"),
        ),
        communities=(CommunityEntry("proj.core", 2, 0.5, "proj.core"),),
        dependency_profile=(("numpy", 2),),
        node_scores_available=node_scores_available,
    )


def test_golden_card_layout() -> None:
    out = format_overview_card(_card_fixture())
    assert out.startswith("# Overview — __project__\n")
    assert "[2 packages · 3 modules · 12 symbols · 67% documented]" in out
    assert "## Module map" in out and "[[next:lookup-show:proj.core:context]]" in out
    assert "## Entry points" in out and "[[next:lookup:proj.__main__]]" in out
    assert "## Structure communities" in out and "cohesion 0.50" in out
    assert "## Dependency profile" in out and "numpy (2 imports)" in out


def test_communities_hint_when_scores_disabled() -> None:
    out = format_overview_card(_card_fixture(node_scores_available=False))
    assert "enable reference_graph.node_scores" in out
