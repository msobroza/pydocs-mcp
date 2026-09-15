"""The pointer table at the tool-call boundary, on a REAL index.

Four claims, all driven through ``build_routers`` exactly as ``server.py`` and the
CLI build it:

- the SHIPPED table reaches every migrated response kind — a code search hit, a
  prose search hit, an overview module row and a decision row — and renders its
  together/then groups in the MCP and the CLI call form;
- no response points at the span it just rendered;
- every call a response advertises executes;
- a NON-default table replaces the shipped rows, so the rows a response offers
  come from YAML and nowhere else.

Response kinds whose renderer has not migrated yet (symbol, references, impact,
context, grep, read) keep their hardcoded pointer and are not asserted here.
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import (
    ContextInput,
    GrepInput,
    OverviewInput,
    ReadFileInput,
    ReferencesInput,
    SearchInput,
    SymbolInput,
    WhyInput,
)
from pydocs_mcp.application.tool_response import (
    ReferencesEnvelope,
    SymbolEnvelope,
    ToolResponse,
)
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.models import PROJECT_PACKAGE_NAME
from pydocs_mcp.pointer_table import ResponseKind
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import _to_call_tool_result, build_routers
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
    # DECISION: widget_count returns an int so the report never formats None.
    return 1
'''

# A prose file: its hit carries no call graph, so its row offers no callers.
_GUIDE_MD = """\
# Widget guide

The widget census explains how many widgets a deployment holds.
"""

# A table that shares no row with the shipped one, so a passing assertion can
# only come from the loaded YAML.
_NON_DEFAULT_TABLE = """\
output:
  pointers:
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
    (project / "guide.md").write_text(_GUIDE_MD)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredPointers:
    """One real bundle plus a router per surface, per table."""

    shipped_mcp: ToolRouter
    shipped_cli: ToolRouter
    tabled_mcp: ToolRouter
    tabled_cli: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredPointers:
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "pointers.db")
    overlay = tmp_path / "pointers.yaml"
    overlay.write_text(_NON_DEFAULT_TABLE)
    built = {
        (name, surface): build_routers(config, db_path=db_path, surface=surface)[0]
        for name, config in (
            ("shipped", AppConfig.load()),
            ("tabled", AppConfig.load(explicit_path=overlay)),
        )
        for surface in ("mcp", "cli")
    }
    return _WiredPointers(
        shipped_mcp=built[("shipped", "mcp")],
        shipped_cli=built[("shipped", "cli")],
        tabled_mcp=built[("tabled", "mcp")],
        tabled_cli=built[("tabled", "cli")],
    )


def _overview(router: ToolRouter) -> str:
    response: ToolResponse = asyncio.run(
        router.get_overview(OverviewInput(package=PROJECT_PACKAGE_NAME))
    )
    return response.text


def _search(router: ToolRouter, query: str) -> str:
    return asyncio.run(router.search_codebase(SearchInput(query=query, kind="docs"))).text


def _why(router: ToolRouter) -> str:
    return asyncio.run(router.get_why(WhyInput(query="widget_count"))).text


def _group_line(text: str, label: str) -> str:
    """The first line of ``text`` starting with ``label`` — one bundle group."""
    matches = [line for line in text.splitlines() if line.startswith(label)]
    assert matches, f"no {label!r} line in:\n{text}"
    return matches[0]


def _group_lines(text: str, label: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(label)]


def _hit_block(text: str, heading: str) -> str:
    """One search hit's block — from its ``## heading`` to the next one.

    A search page renders several hits, each with its own bundle; asserting on
    the first group line in the page would pin whichever hit happened to rank
    first.
    """
    lines = text.splitlines()
    assert heading in lines, f"no {heading!r} block in:\n{text}"
    rest = lines[lines.index(heading) + 1 :]
    end = next((i for i, line in enumerate(rest) if line.startswith("## ")), len(rest))
    return "\n".join(rest[:end])


# ── search hits ────────────────────────────────────────────────────────────


def test_a_code_hit_offers_its_card_and_callers_together_then_the_source(
    wired: _WiredPointers,
) -> None:
    """A module hit renders only its docstring, so its source still deepens."""
    block = _hit_block(_search(wired.shipped_mcp, "the module the overview maps"), "## pkg.mod")
    assert _group_line(block, "Together:") == (
        'Together: → get_symbol(target="pkg.mod") '
        '→ get_references(target="pkg.mod", direction="callers")'
    )
    assert _group_line(block, "Then:") == 'Then: → get_symbol(target="pkg.mod", depth="source")'


def test_a_code_hit_renders_the_same_bundle_in_cli_form(wired: _WiredPointers) -> None:
    block = _hit_block(_search(wired.shipped_cli, "the module the overview maps"), "## pkg.mod")
    assert _group_line(block, "Together:") == (
        "Together: → pydocs-mcp symbol pkg.mod → pydocs-mcp refs pkg.mod --direction callers"
    )
    assert _group_line(block, "Then:") == "Then: → pydocs-mcp symbol pkg.mod --depth source"


def test_a_prose_hit_offers_the_card_alone_because_it_has_no_call_graph(
    wired: _WiredPointers,
) -> None:
    block = _hit_block(_search(wired.shipped_mcp, "widget census deployment"), "## Widget guide")
    together = _group_line(block, "Together:")
    # The card names the heading anchor the hit rendered, not the whole
    # document — the widened target grammar resolves it (ADR 0023 (e)), and
    # ``test_every_call_a_hit_advertises_executes`` runs this very call.
    assert together == 'Together: → get_symbol(target="guide.md#widget-guide")'
    # No callers pointer: prose carries no CALLS edge to follow.
    assert "direction=" not in together
    # The hit rendered the section BODY, while the source call returns the file
    # span the heading covers — a real deepening, so the row keeps it.
    assert _group_line(block, "Then:") == (
        'Then: → get_symbol(target="guide.md#widget-guide", depth="source")'
    )


def test_a_hit_never_points_at_the_span_it_just_rendered(wired: _WiredPointers) -> None:
    """A def chunk IS its whole span, so its source call would hand the agent
    back exactly the lines the hit rendered — the table's ``then`` row is
    dropped for that hit rather than repeating it."""
    block = _hit_block(_search(wired.shipped_mcp, "count the widgets"), "## def widget_count()")
    assert _group_line(block, "Together:") == (
        'Together: → get_symbol(target="pkg.mod.widget_count") '
        '→ get_references(target="pkg.mod.widget_count", direction="callers")'
    )
    assert "Then:" not in block


def test_no_hit_re_runs_the_search_that_produced_it(wired: _WiredPointers) -> None:
    text = _search(wired.shipped_mcp, "count the widgets")
    assert "→ search_codebase(" not in text
    block = _hit_block(text, "## def widget_count()")
    calls = [call for line in block.splitlines() for call in line.split(" → ")[1:]]
    assert len(calls) == len(set(calls)), calls


# ── every advertised call executes ─────────────────────────────────────────

# Pointers render at the end of a bundle line as well as on their own line, and
# a group line carries several calls — so the call pattern stops at the next
# arrow rather than running to end of line.
_MCP_CALL_RE = re.compile(r"→ (\w+\([^\n→]*\))")

_TOOL_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "get_symbol": (mcp_inputs.SymbolInput, SymbolEnvelope),
    "get_references": (mcp_inputs.ReferencesInput, ReferencesEnvelope),
}


def _run_mcp_call(router: ToolRouter, call_src: str) -> None:
    """Validate input → await the router method → validate the output envelope."""
    call = ast.parse(call_src, mode="eval").body
    assert isinstance(call, ast.Call) and isinstance(call.func, ast.Name), call_src
    kwargs: dict[str, Any] = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
    input_model, envelope = _TOOL_MODELS[call.func.id]
    response = asyncio.run(getattr(router, call.func.id)(input_model(**kwargs)))
    _to_call_tool_result(response, envelope)


@pytest.mark.parametrize(
    ("query", "heading"),
    [
        pytest.param("count the widgets", "## def widget_count()", id="code_hit"),
        pytest.param("the module the overview maps", "## pkg.mod", id="module_hit"),
        pytest.param("widget census deployment", "## Widget guide", id="prose_hit"),
    ],
)
def test_every_call_a_hit_advertises_executes(
    wired: _WiredPointers, query: str, heading: str
) -> None:
    """A pointer is a ready-made call; one that raises would cost the agent the
    turn it was meant to save."""
    block = _hit_block(_search(wired.shipped_mcp, query), heading)
    calls = _MCP_CALL_RE.findall(block)
    assert calls, block
    for call_src in calls:
        _run_mcp_call(wired.shipped_mcp, call_src)


def test_every_call_a_decision_card_advertises_executes(wired: _WiredPointers) -> None:
    calls = _MCP_CALL_RE.findall(_why(wired.shipped_mcp))
    assert calls
    for call_src in calls:
        _run_mcp_call(wired.shipped_mcp, call_src)


# ── overview module rows ───────────────────────────────────────────────────


def test_an_overview_module_row_deepens_into_that_module(wired: _WiredPointers) -> None:
    text = _overview(wired.shipped_mcp)
    assert "- `pkg.mod` — The module the overview card maps.\n" in text
    assert 'Together: → get_symbol(target="pkg.mod", depth="tree")' in text


def test_an_overview_module_row_renders_in_cli_form(wired: _WiredPointers) -> None:
    text = _overview(wired.shipped_cli)
    assert "Together: → pydocs-mcp symbol pkg.mod --depth tree" in text


# ── decision rows ──────────────────────────────────────────────────────────


def test_a_decision_row_points_at_the_symbols_it_governs(wired: _WiredPointers) -> None:
    text = _why(wired.shipped_mcp)
    assert _group_line(text, "Together:") == 'Together: → get_symbol(target="pkg.mod")'
    # The decision row has no dependent follow-up: the cards it governs are all
    # independent of each other.
    assert "Then:" not in text


def test_a_decision_row_renders_in_cli_form(wired: _WiredPointers) -> None:
    text = _why(wired.shipped_cli)
    assert _group_line(text, "Together:") == "Together: → pydocs-mcp symbol pkg.mod"


# ── the two surfaces advertise the same calls ──────────────────────────────


@pytest.mark.parametrize(
    "render",
    [
        pytest.param(lambda r: _search(r, "count the widgets"), id="search_hit"),
        pytest.param(_overview, id="overview_module"),
        pytest.param(_why, id="decision"),
    ],
)
def test_the_cli_and_mcp_surfaces_advertise_the_same_calls(wired: _WiredPointers, render) -> None:
    mcp_text, cli_text = render(wired.shipped_mcp), render(wired.shipped_cli)
    for label in ("Together:", "Then:"):
        mcp_lines = _group_lines(mcp_text, label)
        cli_lines = _group_lines(cli_text, label)
        assert len(mcp_lines) == len(cli_lines)
        for mcp_line, cli_line in zip(mcp_lines, cli_lines, strict=True):
            assert mcp_line.count(" → ") == cli_line.count(" → ")


# ── a non-default table replaces the shipped rows ──────────────────────────


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


def test_the_module_line_carries_no_inline_pointer_of_its_own(wired: _WiredPointers) -> None:
    """The bundle replaces the old trailing token rather than joining it."""
    text = _overview(wired.tabled_mcp)
    assert "- `pkg.mod` — The module the overview card maps.\n" in text


# ── every response kind, on both surfaces ──────────────────────────────────
#
# One response per row of the table, produced through the routers ``server.py``
# and the CLI build, so a kind whose renderer stops reading the table — or
# whose advertised call stops resolving — fails here rather than in an agent's
# turn. ``_RESPONSE_BY_KIND`` is pinned against ``ResponseKind`` itself, so a
# row added without a wire case fails the build too.

_KINDS_PYPROJECT = """\
[project]
name = "kindsproj"
version = "0.0.0"
dependencies = []

[project.scripts]
kinds-cli = "pkg.mod:widget_count"
"""

# 500 lines past the 400-line source cap, so ``depth="source"`` is cut and
# offers the ``read`` window that resumes it.
_LONG_BODY = "\n".join(f"    step_{i} = {i}" for i in range(500))

_KINDS_MOD_PY = f'''\
"""The module every response-kind test reads."""

import bigdep


def widget_count() -> int:
    """Count the widgets."""
    # DECISION: widget_count returns an int so the report never formats None.
    return 1


def long_walk() -> int:
    """A body longer than the source cap."""
{_LONG_BODY}
    return step_0
'''

_KINDS_CALLERS_PY = '"""Callers of the counter."""\n\nfrom pkg.mod import widget_count\n\n\n' + (
    "\n\n".join(
        f'def c{i}() -> int:\n    """Caller {i}."""\n    return widget_count()' for i in range(4)
    )
)

_KINDS_GUIDE_MD = """\
# Widget guide

The widget census explains how many widgets a deployment holds.
"""

# A package the project imports AND the index holds, so the dependency row
# carries a follow-up; its doc is over the package-doc char cap, so the
# response also carries the cap's recovery pointer.
_SEEDED_PACKAGE = "bigdep"
_SEEDED_DOC = "prose about the seeded dependency. " * 1200


def _write_kinds_project(root: Path) -> Path:
    project = root / "kindsproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_KINDS_PYPROJECT)
    (project / "guide.md").write_text(_KINDS_GUIDE_MD)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_KINDS_MOD_PY)
    (pkg / "callers.py").write_text(_KINDS_CALLERS_PY)
    return project


def _seed_indexed_dependency(db_path: Path) -> None:
    """Add one indexed dependency with an over-cap doc to a real bundle.

    The fixture indexes no dependencies (resolving site-packages would make it
    slow and machine-dependent), but two rows of the table only render for an
    indexed package: the dependency profile's follow-up and the package doc's
    recovery pointer. Writing the package through the repo's own unit of work
    is the cheapest way to reach both at the tool-call boundary.
    """
    from pydocs_mcp.models import Chunk, ChunkFilterField, Package, PackageOrigin
    from pydocs_mcp.storage.factories import build_sqlite_uow_factory

    package = Package(
        name=_SEEDED_PACKAGE,
        version="1.0.0",
        summary="A seeded dependency.",
        homepage="",
        dependencies=(),
        content_hash="seeded",
        origin=PackageOrigin.DEPENDENCY,
    )
    chunk = Chunk(
        text=_SEEDED_DOC,
        metadata={
            ChunkFilterField.PACKAGE.value: _SEEDED_PACKAGE,
            ChunkFilterField.TITLE.value: "Overview",
        },
    )

    async def _write() -> None:
        async with build_sqlite_uow_factory(db_path)() as uow:
            await uow.packages.upsert(package)
            await uow.chunks.upsert((chunk,))
            await uow.commit()

    asyncio.run(_write())


@dataclass(frozen=True, slots=True)
class _WiredKinds:
    """One router per surface over a single project, and over a workspace."""

    mcp: ToolRouter
    cli: ToolRouter
    workspace_mcp: ToolRouter
    workspace_cli: ToolRouter


@pytest.fixture(scope="module")
def kinds(tmp_path_factory: pytest.TempPathFactory) -> _WiredKinds:
    # Module-scoped: the routers are read-only, and indexing three bundles per
    # parametrized case would dominate this file's runtime.
    tmp_path = tmp_path_factory.mktemp("kinds")
    db_path = index_project_to_db(_write_kinds_project(tmp_path), tmp_path / "kinds.db")
    _seed_indexed_dependency(db_path)
    sibling = index_project_to_db(_write_project(tmp_path), tmp_path / "sibling.db")
    config = AppConfig.load()
    return _WiredKinds(
        mcp=build_routers(config, db_path=db_path, surface="mcp")[0],
        cli=build_routers(config, db_path=db_path, surface="cli")[0],
        workspace_mcp=build_routers(config, db_paths=[db_path, sibling], surface="mcp")[0],
        workspace_cli=build_routers(config, db_paths=[db_path, sibling], surface="cli")[0],
    )


def _run(router: ToolRouter, tool: str, payload: BaseModel) -> str:
    return asyncio.run(getattr(router, tool)(payload)).text


_SOURCE_TARGET = "pkg.mod.long_walk"
_COUNTER = "pkg.mod.widget_count"

# One response per row of the table. Each entry renders the kind's bundle
# somewhere in its text; the kind's own content is pinned by the batch, read
# and search wire suites — what this map pins is that EVERY row reaches a
# response, on both surfaces, advertising calls that parse.
_RESPONSE_BY_KIND: dict[ResponseKind, Callable[[_WiredKinds, str], str]] = {
    ResponseKind.SEARCH_HIT_CODE: lambda k, s: _run(
        getattr(k, s), "search_codebase", SearchInput(query="count the widgets", kind="docs")
    ),
    ResponseKind.SEARCH_HIT_PROSE: lambda k, s: _run(
        getattr(k, s), "search_codebase", SearchInput(query="widget census deployment", kind="docs")
    ),
    ResponseKind.SYMBOL_CARD: lambda k, s: _run(
        getattr(k, s), "get_symbol", SymbolInput(target=_COUNTER)
    ),
    ResponseKind.OUTLINE: lambda k, s: _run(
        getattr(k, s), "get_symbol", SymbolInput(target="pkg.mod", depth="tree")
    ),
    ResponseKind.SOURCE: lambda k, s: _run(
        getattr(k, s), "get_symbol", SymbolInput(target=_SOURCE_TARGET, depth="source")
    ),
    ResponseKind.REFERENCE_ROW: lambda k, s: _run(
        getattr(k, s), "get_references", ReferencesInput(target=_COUNTER, direction="callers")
    ),
    ResponseKind.IMPACT_ROW: lambda k, s: _run(
        getattr(k, s), "get_references", ReferencesInput(target=_COUNTER, direction="impact")
    ),
    # From a CALLER, so the closure holds a signature-only block: a block that
    # printed its body has nothing to offer (the source call would hand back the
    # lines above it), which is the self-pointing rule, not a missing row.
    ResponseKind.CONTEXT_SKELETON_BLOCK: lambda k, s: _run(
        getattr(k, s), "get_context", ContextInput(targets=["pkg.callers.c0"])
    ),
    ResponseKind.DECISION: lambda k, s: _run(
        getattr(k, s), "get_why", WhyInput(query="widget_count")
    ),
    ResponseKind.OVERVIEW_MODULE: lambda k, s: _run(
        getattr(k, s), "get_overview", OverviewInput(package=PROJECT_PACKAGE_NAME)
    ),
    ResponseKind.OVERVIEW_ENTRY_POINT: lambda k, s: _run(
        getattr(k, s), "get_overview", OverviewInput(package=PROJECT_PACKAGE_NAME)
    ),
    ResponseKind.OVERVIEW_DEPENDENCY: lambda k, s: _run(
        getattr(k, s), "get_overview", OverviewInput(package=PROJECT_PACKAGE_NAME)
    ),
    ResponseKind.OVERVIEW_DECISIONS: lambda k, s: _run(
        getattr(k, s), "get_overview", OverviewInput(package=PROJECT_PACKAGE_NAME)
    ),
    ResponseKind.PACKAGE_DOC: lambda k, s: _run(
        getattr(k, s), "get_symbol", SymbolInput(target=_SEEDED_PACKAGE)
    ),
    ResponseKind.GREP_HIT: lambda k, s: _run(
        getattr(k, s), "grep", GrepInput(pattern="widget_count", output_mode="content")
    ),
    ResponseKind.READ_CONTINUATION: lambda k, s: _run(
        getattr(k, s), "read_file", ReadFileInput(file_path="pkg/mod.py", limit=3)
    ),
    ResponseKind.ZERO_HIT: lambda k, s: _run(
        getattr(k, s), "search_codebase", SearchInput(query="zzz unmatchable zzz", kind="docs")
    ),
    ResponseKind.WORKSPACE_PROJECT: lambda k, s: _run(
        getattr(k, f"workspace_{s}"), "get_overview", OverviewInput()
    ),
}

# The calls a response advertises, per surface: the MCP form is a call
# expression, the CLI form a command line. Both start at the pointer arrow.
_MCP_CALL_ON_LINE = re.compile(r"→ (\w+\([^\n→]*\))")
_CLI_CALL_ON_LINE = re.compile(r"→ (pydocs-mcp [^\n→]*)")


def test_the_map_covers_every_row_of_the_table() -> None:
    """A row added without a wire case would ship unexercised on both surfaces."""
    assert set(_RESPONSE_BY_KIND) == set(ResponseKind)


@pytest.mark.parametrize("kind", list(ResponseKind), ids=lambda k: k.value)
def test_every_response_kind_advertises_the_same_calls_on_both_surfaces(
    kinds: _WiredKinds, kind: ResponseKind
) -> None:
    """CLI and MCP are one rendering path over one table: same calls, two forms."""
    render = _RESPONSE_BY_KIND[kind]
    mcp_text, cli_text = render(kinds, "mcp"), render(kinds, "cli")
    mcp_calls = _MCP_CALL_ON_LINE.findall(mcp_text)
    cli_calls = _CLI_CALL_ON_LINE.findall(cli_text)
    assert mcp_calls, f"{kind.value} advertised no call:\n{mcp_text}"
    assert len(mcp_calls) == len(cli_calls), f"{kind.value}\nMCP:\n{mcp_text}\nCLI:\n{cli_text}"
    for label in ("Together:", "Then:"):
        assert len(_group_lines(mcp_text, label)) == len(_group_lines(cli_text, label))


@pytest.mark.parametrize("kind", list(ResponseKind), ids=lambda k: k.value)
def test_no_response_kind_leaks_a_raw_pointer_token(kinds: _WiredKinds, kind: ResponseKind) -> None:
    """A token that reached a client unresolved is a call nothing can issue."""
    for surface in ("mcp", "cli"):
        assert "[[next:" not in _RESPONSE_BY_KIND[kind](kinds, surface)
