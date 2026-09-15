"""Advertised identifiers round-trip, on a REAL index (ADR 0023 decision (e)).

A search response advertises one `qualified_name` per structured row. Heading
and text-section rows carry the `module#slug` names their chunkers store
(`README.md#install-steps`, `settings.toml#tool-widget`) — names the
symbol-target validator used to reject, so the follow-up call an agent was
handed could never succeed.

These checks drive the frozen tools end to end through `ToolRouter`: whatever
`search_codebase` advertises must be accepted by `get_symbol` and resolve to
the node it names, at every depth, and `get_references` must stay honest about
a target whose language has no analyzer.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.application.formatting import strip_pointers
from pydocs_mcp.application.mcp_errors import NotFoundError
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
)
from pydocs_mcp.application.tool_response import (
    ReferencesEnvelope,
    SearchEnvelope,
    SymbolEnvelope,
    ToolResponse,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "roundtripproj"
version = "0.0.0"
dependencies = []
"""

# Two headings, so the advertised name is a heading anchor and not the whole file.
_README_MD = """\
# Round trip fixture

A markdown module whose headings are addressable.

## Install steps

Run the installer to configure the widget before the first launch.
"""

# The T2 text-section chunker stores key-path anchors on a .toml file, and the
# module id keeps its file suffix — the other half of the widened grammar.
_SETTINGS_TOML = """\
[tool.widget]
mode = "fast"
"""

_MOD_PY = '''\
"""The code module, so a code row shares the page with the prose rows."""


def configure() -> int:
    """Configure the widget."""
    return 1
'''

# What the chunkers store for the fixture above; the tools must accept all three.
_HEADING_TARGET = "README.md#install-steps"
_SECTION_TARGET = "settings.toml#tool-widget"
_MODULE_TARGET = "README.md"


def _write_project(root: Path) -> Path:
    project = root / "roundtripproj"
    project.mkdir()
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (project / "README.md").write_text(_README_MD)
    (project / "settings.toml").write_text(_SETTINGS_TOML)
    (project / "mod.py").write_text(_MOD_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredIndex:
    """One real bundle plus a router per surface (pointers differ by surface)."""

    db_path: Path
    project: Path
    router: ToolRouter
    cli_router: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredIndex:
    project = _write_project(tmp_path)
    db_path = index_project_to_db(project, tmp_path / "roundtrip.db")
    config = AppConfig.load()
    router, _handlers = build_routers(config, db_path=db_path, surface="mcp")
    cli_router, _cli_handlers = build_routers(config, db_path=db_path, surface="cli")
    return _WiredIndex(db_path=db_path, project=project, router=router, cli_router=cli_router)


def _search(router: ToolRouter, query: str) -> SearchEnvelope:
    response = asyncio.run(router.search_codebase(SearchInput(query=query, limit=20)))
    return SearchEnvelope.model_validate(response.structured())


def _symbol(router: ToolRouter, target: str, depth: str = "summary") -> ToolResponse:
    payload = SymbolInput(target=target, depth=depth)  # type: ignore[arg-type]
    return asyncio.run(router.get_symbol(payload))


def _advertised_names(envelope: SearchEnvelope) -> list[str]:
    return [row.qualified_name for row in envelope.items if row.qualified_name]


# ── THE ROUND TRIP: what a search advertises, the symbol tool accepts ──────


def test_search_advertises_the_heading_anchor_the_chunker_stored(wired: _WiredIndex) -> None:
    """Premise pin: the row names the heading, not its parent document."""
    names = _advertised_names(_search(wired.router, "install steps widget"))
    assert _HEADING_TARGET in names, names


def test_every_advertised_name_is_accepted_by_the_symbol_tool(wired: _WiredIndex) -> None:
    """The invariant: no row names a call the tool's own validator refuses."""
    for name in _advertised_names(_search(wired.router, "install steps widget")):
        SymbolInput(target=name)  # must not raise
        ContextInput(targets=[name])  # must not raise
        ReferencesInput(target=name)  # must not raise


@pytest.mark.parametrize("depth", ["summary", "tree", "source"])
def test_advertised_heading_anchor_resolves_at_every_depth(wired: _WiredIndex, depth: str) -> None:
    """Card, outline and source all answer for the name the row advertised."""
    envelope = SymbolEnvelope.model_validate(
        _symbol(wired.router, _HEADING_TARGET, depth).structured()
    )
    assert envelope.items, envelope.text
    assert envelope.items[0].qualified_name == _HEADING_TARGET
    assert "Install steps" in envelope.text or "installer" in envelope.text


@pytest.mark.parametrize("depth", ["summary", "tree", "source"])
def test_advertised_text_section_anchor_resolves_at_every_depth(
    wired: _WiredIndex, depth: str
) -> None:
    """A suffixed module id (`settings.toml`) plus a key-path anchor."""
    envelope = SymbolEnvelope.model_validate(
        _symbol(wired.router, _SECTION_TARGET, depth).structured()
    )
    assert envelope.items, envelope.text
    assert envelope.items[0].qualified_name == _SECTION_TARGET


def test_heading_anchor_resolves_to_the_heading_not_its_parent(wired: _WiredIndex) -> None:
    """The anchor must not silently widen to the whole document."""
    anchor = SymbolEnvelope.model_validate(_symbol(wired.router, _HEADING_TARGET).structured())
    parent = SymbolEnvelope.model_validate(_symbol(wired.router, _MODULE_TARGET).structured())
    assert anchor.items[0].qualified_name != parent.items[0].qualified_name
    assert len(anchor.items) < len(parent.items)


def test_search_hit_pointer_names_the_heading_it_rendered(wired: _WiredIndex) -> None:
    """The text pointer stops stripping the fragment now that it round-trips."""
    response = asyncio.run(
        wired.router.search_codebase(SearchInput(query="install steps widget", limit=20))
    )
    assert f'get_symbol(target="{_HEADING_TARGET}")' in response.text, response.text


# ── get_references stays honest on a target with no analyzer ───────────────


@pytest.mark.parametrize(
    ("target", "resolution"),
    [
        # `.md` carries a MENTIONS analyzer (ADR 0021 Decision 6); `.toml` does
        # not. The anchor must not change either verdict — it names a node in
        # the same file, so the declared level is the file's.
        (_HEADING_TARGET, "syntactic"),
        (_SECTION_TARGET, "unavailable"),
    ],
)
def test_references_on_an_anchor_declares_its_language_honestly(
    wired: _WiredIndex, target: str, resolution: str
) -> None:
    response = asyncio.run(
        wired.router.get_references(ReferencesInput(target=target, direction="callers"))
    )
    envelope = ReferencesEnvelope.model_validate(response.structured())
    assert envelope.meta.resolution == resolution
    assert envelope.items == []


def test_context_on_a_heading_anchor_resolves(wired: _WiredIndex) -> None:
    response = asyncio.run(wired.router.get_context(ContextInput(targets=[_HEADING_TARGET])))
    assert _HEADING_TARGET in response.text


@pytest.mark.parametrize("depth", ["summary", "tree", "source"])
def test_cli_and_mcp_render_the_same_anchor_symbol(wired: _WiredIndex, depth: str) -> None:
    mcp = _symbol(wired.router, _HEADING_TARGET, depth)
    cli = _symbol(wired.cli_router, _HEADING_TARGET, depth)
    assert strip_pointers(cli.text) == strip_pointers(mcp.text)
    assert cli.items == mcp.items


def test_an_unresolvable_anchor_is_a_miss_not_a_package_card(wired: _WiredIndex) -> None:
    """An anchor names a node inside a module, so a head that happens to spell
    an indexed package must not answer with that package's overview."""
    with pytest.raises(NotFoundError) as exc:
        _symbol(wired.router, "__project__#install-steps")
    assert "__project__#install-steps" in str(exc.value)
