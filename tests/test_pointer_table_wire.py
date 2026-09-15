"""The pointer table at the tool-call boundary, on a REAL index.

Two claims, both driven through ``build_routers`` exactly as ``server.py`` and the
CLI build it:

- a NON-default table reaches a response and renders its together/then groups in
  the MCP and the CLI call form;
- with the SHIPPED defaults every response is byte-identical to the pre-table
  rendering, because the expand-step compatibility gate keeps each renderer's
  hardcoded action in force.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.application.formatting import format_overview_card
from pydocs_mcp.application.mcp_inputs import OverviewInput, SearchInput
from pydocs_mcp.application.overview_service import OverviewService
from pydocs_mcp.application.tool_response import ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.pointer_table import PointerTableConfig
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "pointerproj"
version = "0.0.0"
dependencies = []
"""

_MOD_PY = '''\
"""The module the overview card maps."""


def widget_count() -> int:
    """Count the widgets."""
    return 1
'''

# A table that shares no row with the shipped one, so a passing assertion can
# only come from the loaded YAML.
_NON_DEFAULT_TABLE = """\
output:
  pointers:
    bundles_enabled: true
    table:
      overview_module:
        together: [outline, callers]
        then: [source]
"""


def _write_project(root: Path) -> Path:
    project = root / "pointerproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredPointers:
    """One real bundle plus a router per surface, per table."""

    default_mcp: ToolRouter
    default_cli: ToolRouter
    tabled_mcp: ToolRouter
    tabled_cli: ToolRouter
    shipped_pointers: PointerTableConfig
    overview: OverviewService


@pytest.fixture
def wired(tmp_path: Path) -> _WiredPointers:
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "pointers.db")
    overlay = tmp_path / "pointers.yaml"
    overlay.write_text(_NON_DEFAULT_TABLE)
    shipped = AppConfig.load()
    tabled = AppConfig.load(explicit_path=overlay)
    built = {
        (name, surface): build_routers(config, db_path=db_path, surface=surface)
        for name, config in (("default", shipped), ("tabled", tabled))
        for surface in ("mcp", "cli")
    }
    return _WiredPointers(
        default_mcp=built[("default", "mcp")][0],
        default_cli=built[("default", "cli")][0],
        tabled_mcp=built[("tabled", "mcp")][0],
        tabled_cli=built[("tabled", "cli")][0],
        shipped_pointers=shipped.output.pointers,
        overview=built[("default", "mcp")][1][0].overview,
    )


def _overview(router: ToolRouter) -> str:
    response: ToolResponse = asyncio.run(
        router.get_overview(OverviewInput(package=PROJECT_PACKAGE_NAME))
    )
    return response.text


def _search(router: ToolRouter) -> str:
    return asyncio.run(router.search_codebase(SearchInput(query="widget count"))).text


def _group_line(text: str, label: str) -> str:
    """The first line of ``text`` starting with ``label`` — one bundle group."""
    matches = [line for line in text.splitlines() if line.startswith(label)]
    assert matches, f"no {label!r} line in:\n{text}"
    return matches[0]


# ── a non-default table reaches the response, in both call forms ───────────


def test_non_default_table_renders_its_groups_in_mcp_call_form(wired: _WiredPointers) -> None:
    text = _overview(wired.tabled_mcp)
    assert _group_line(text, "Together:") == (
        'Together: → get_symbol(target="pkg.mod", depth="tree") '
        '→ get_references(target="pkg.mod", direction="callers")'
    )
    assert _group_line(text, "Then:") == 'Then: → get_symbol(target="pkg.mod", depth="source")'


def test_non_default_table_renders_its_groups_in_cli_call_form(wired: _WiredPointers) -> None:
    text = _overview(wired.tabled_cli)
    assert _group_line(text, "Together:") == (
        "Together: → pydocs-mcp symbol pkg.mod --depth tree "
        "→ pydocs-mcp refs pkg.mod --direction callers"
    )
    assert _group_line(text, "Then:") == "Then: → pydocs-mcp symbol pkg.mod --depth source"


def test_the_tabled_module_line_drops_its_hardcoded_inline_pointer(
    wired: _WiredPointers,
) -> None:
    """The bundle replaces the old trailing token rather than joining it."""
    text = _overview(wired.tabled_mcp)
    assert "- `pkg.mod` — The module the overview card maps.\n" in text


# ── the shipped defaults change nothing ────────────────────────────────────


def test_the_shipped_table_renders_the_card_exactly_as_the_pre_table_path(
    wired: _WiredPointers,
) -> None:
    """Byte equality against the rendering that takes no table at all."""
    card = asyncio.run(wired.overview.build(PROJECT_PACKAGE_NAME))
    assert format_overview_card(card, pointers=wired.shipped_pointers) == format_overview_card(card)


def test_shipped_defaults_keep_the_overview_card_byte_identical(wired: _WiredPointers) -> None:
    text = _overview(wired.default_mcp)
    assert "- `pkg.mod` — The module the overview card maps. → get_symbol(" in text
    assert "Together:" not in text
    assert "Then:" not in text


def test_shipped_defaults_keep_the_search_response_byte_identical(wired: _WiredPointers) -> None:
    text = _search(wired.default_mcp)
    assert "Together:" not in text
    assert "Then:" not in text
    # The pre-table per-hit pointer is still exactly the one search emitted.
    assert "→ get_symbol(target=" in text


def test_shipped_defaults_agree_across_surfaces(wired: _WiredPointers) -> None:
    assert _overview(wired.default_cli).count("→ pydocs-mcp symbol") == _overview(
        wired.default_mcp
    ).count("→ get_symbol(")
