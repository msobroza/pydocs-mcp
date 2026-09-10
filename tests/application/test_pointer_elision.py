"""Line-aware pointer elision — suppressed / stripped ``[[next:...]]`` tokens.

The overview and workspace renderers put a token inline at the end of a
bullet. Eliding the token must keep that bullet's line break (otherwise every
bullet whose pointer is suppressed merges into the next one), while an
own-line column-0 token still removes its whole line, byte-identical to the
pre-fix output. ``resolve_pointers``' suppression pass and ``strip_pointers``
share the one elision span, so both paths produce the same bytes.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from pydocs_mcp.application.formatting import (
    format_overview_card,
    format_workspace_overview_card,
    resolve_pointers,
    strip_pointers,
)
from pydocs_mcp.application.overview_service import (
    CommunityEntry,
    EntryPoint,
    ModuleEntry,
    OverviewCard,
    WorkspaceProjectEntry,
)

# A lookup target the symbol tools reject (dash + leading digit in a segment),
# so resolve_pointers suppresses it on both surfaces.
_TOKEN = "[[next:lookup:docs.0001-a.md]]"

# shape id -> (input, expected output after elision)
_SHAPES: dict[str, tuple[str, str]] = {
    "own_line_col0": (f"a\n{_TOKEN}\nb\n", "a\nb\n"),
    "inline_before_eol": (f"- a {_TOKEN}\nb\n", "- a\nb\n"),
    "two_inline_tokens": (f"- a {_TOKEN} {_TOKEN}\nb\n", "- a\nb\n"),
    "indented_own_line": (f"a\n  {_TOKEN}\nb\n", "a\nb\n"),
    "end_of_text_with_eol": (f"- a {_TOKEN}\n", "- a\n"),
    "end_of_text_without_eol": (f"- a {_TOKEN}", "- a"),
}

_Render = Callable[[str], str]
_RENDERERS: dict[str, _Render] = {
    "resolve_mcp": lambda text: resolve_pointers(text, "mcp"),
    "resolve_cli": lambda text: resolve_pointers(text, "cli"),
    "strip": strip_pointers,
}


@pytest.mark.parametrize("renderer", sorted(_RENDERERS))
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_elision_shape_table(shape: str, renderer: str) -> None:
    text, expected = _SHAPES[shape]
    assert _RENDERERS[renderer](text) == expected


@pytest.mark.parametrize("surface", ["mcp", "cli"])
@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_resolve_suppression_bytes_equal_strip_bytes(shape: str, surface: str) -> None:
    text, _ = _SHAPES[shape]
    assert resolve_pointers(text, surface) == strip_pointers(text)


def _overview_card() -> OverviewCard:
    return OverviewCard(
        package="__project__",
        package_count=3,
        module_count=3,
        symbol_count=9,
        doc_coverage=0.5,
        modules=(
            ModuleEntry("proj.core", "Core module.", 0.9),
            ModuleEntry(".cfg-x.toml", "", 0.5),  # empty doc + suppressed pointer
            ModuleEntry("docs.0001-a.md", "Decision a.", 0.4),  # suppressed pointer
        ),
        entry_points=(
            EntryPoint("demo-cli", "script"),  # dashed name: suppressed pointer
            EntryPoint("proj.cli", "root"),
        ),
        communities=(CommunityEntry("proj.core", 2, 0.5, "proj.core"),),
        dependency_profile=(("numpy", 3), ("scikit-learn", 1)),
        node_scores_available=True,
    )


def _entry_count(card: OverviewCard) -> int:
    return (
        len(card.modules)
        + len(card.entry_points)
        + len(card.communities)
        + len(card.dependency_profile)
    )


@pytest.mark.parametrize("renderer", sorted(_RENDERERS))
def test_overview_keeps_one_line_per_bullet(renderer: str) -> None:
    card = _overview_card()
    lines = _RENDERERS[renderer](format_overview_card(card)).split("\n")
    assert len([line for line in lines if line.startswith("- ")]) == _entry_count(card)
    assert all(line.count("- `") <= 1 for line in lines), lines
    assert not [line for line in lines if line.endswith(" ") or line.endswith("— ")]
    heading_rows = [i for i, line in enumerate(lines) if line.startswith("## ")]
    assert all(lines[i - 1] == "" for i in heading_rows[1:]), lines


def test_overview_stripped_card_renders_expected_bullets() -> None:
    lines = strip_pointers(format_overview_card(_overview_card())).split("\n")
    assert "- `.cfg-x.toml`" in lines
    assert "- `demo-cli` (script)" in lines
    assert "- scikit-learn (1 imports)" in lines


def test_workspace_card_without_pointers_keeps_one_line_per_bullet() -> None:
    card = format_workspace_overview_card(
        (
            WorkspaceProjectEntry(name="backend", package_count=12),
            WorkspaceProjectEntry(name="frontend", package_count=7),
        )
    )
    assert strip_pointers(card).endswith(
        "## Projects\n- **backend** — 12 packages\n- **frontend** — 7 packages\n"
    )


def test_empty_first_doc_line_renders_no_dangling_em_dash() -> None:
    card = OverviewCard(
        package="__project__",
        package_count=1,
        module_count=1,
        symbol_count=0,
        doc_coverage=0.0,
        modules=(ModuleEntry("x", "", 0.5),),
        entry_points=(),
        communities=(),
        dependency_profile=(),
        node_scores_available=False,
    )
    out = format_overview_card(card)
    assert "- `x` [[next:lookup-show:x:context]]\n" in out
    assert "- `x`\n" in strip_pointers(out)
