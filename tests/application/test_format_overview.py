"""Golden layout for ``format_overview_card`` — the §D17 structural card.

Pure rendering test: builds an ``OverviewCard`` fixture and asserts the H1,
the one stats line, the four H2 blocks in §D17 order, and the per-entry
next-step pointer tokens. A second test pins the enablement hint that replaces
the communities block when ``node_scores`` is disabled.
"""

from __future__ import annotations

from dataclasses import replace

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
            EntryPoint("demo-cli", "script", target="proj.cli.main"),
            EntryPoint("proj.__main__", "module"),
        ),
        communities=(CommunityEntry("proj.core", 2, 0.5, "proj.core"),),
        dependency_profile=(("numpy", 2),),
        node_scores_available=node_scores_available,
        indexed_packages=frozenset({"numpy"}),
    )


def test_golden_card_layout() -> None:
    out = format_overview_card(_card_fixture())
    assert out.startswith("# Overview — __project__\n")
    assert "[2 packages · 3 modules · 12 symbols · 67% documented]" in out
    # The module map deepens via get_symbol(depth="tree"): get_context rejects
    # module targets, so the old ``:context`` token advertised a dead call.
    assert "## Module map" in out and "[[next:lookup-show:proj.core:tree]]" in out
    assert "## Entry points" in out and "[[next:lookup:proj.__main__]]" in out
    assert "## Structure communities" in out and "cohesion 0.50" in out
    assert "## Dependency profile" in out and "numpy (2 imports)" in out


def test_communities_hint_when_scores_disabled() -> None:
    out = format_overview_card(_card_fixture(node_scores_available=False))
    assert "enable reference_graph.node_scores" in out


# ── AC5.3: a script points at its verified callable, never at its name ─────


def test_script_entry_point_points_at_its_dotted_callable() -> None:
    out = format_overview_card(_card_fixture())
    assert "- `demo-cli` (script) [[next:lookup:proj.cli.main]]\n" in out


def test_script_without_a_resolved_callable_emits_no_token() -> None:
    """An empty ``target`` (non-node attribute, re-export, unindexed module)
    must drop the token AND its separating blank — no trailing space, no
    pointer-shaped leftover."""
    card = _card_fixture()
    card = replace(card, entry_points=(EntryPoint("ghost", "script"),))
    out = format_overview_card(card)
    assert "- `ghost` (script)\n" in out
    assert "next:lookup:ghost" not in out


def test_module_and_root_entry_points_still_point_at_their_own_name() -> None:
    card = replace(_card_fixture(), entry_points=(EntryPoint("proj.cli", "root"),))
    assert "- `proj.cli` (root) [[next:lookup:proj.cli]]\n" in format_overview_card(card)


# ── AC5.2: a dependency pointer appears iff the package is indexed ─────────


def test_indexed_dependency_keeps_its_pointer() -> None:
    assert "- numpy (2 imports) [[next:lookup:numpy]]\n" in format_overview_card(_card_fixture())


def test_unindexed_dependency_renders_without_a_pointer() -> None:
    """``typing`` / a distribution whose import name differs from its package
    name is never indexed under that name — get_symbol would 404 on it."""
    card = replace(
        _card_fixture(),
        dependency_profile=(("typing", 3),),
        indexed_packages=frozenset({"numpy"}),
    )
    out = format_overview_card(card)
    assert "- typing (3 imports)\n" in out
    assert "next:lookup:typing" not in out
