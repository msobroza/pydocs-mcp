"""Wire-level checks for ``search_codebase(limit=…)`` on a REAL index (#271).

The client's limit used to stop at the application layer: the retrieval
pipeline capped at its own YAML default of eight rows and the post-hoc cap
could only shrink that, so ``limit=30`` returned eight and the cut left
``meta.truncated`` false — a partial listing that read as a complete one.

These tests drive the frozen tool end to end through the same wired index +
routers the other wire suites use (``tests/test_tool_surface_wire.py``), on
both surfaces, and pin: the requested limit is honoured, an omitted limit
falls back to the YAML default, a request above the configured maximum is
clamped and marked, a cut sets ``meta.truncated``, an uncut listing does not,
and the CLI renders exactly what MCP renders.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.application.formatting import strip_pointers
from pydocs_mcp.application.mcp_inputs import SearchInput
from pydocs_mcp.application.tool_response import SearchEnvelope, ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "limitproj"
version = "0.0.0"
dependencies = []
"""

# Enough functions that every limit under test bites, and small enough that
# the composite formatter's own token budget never elides (a budget elision
# writes its own ledger entry and would mask the limit's marking).
_FUNCTION_COUNT = 40

# What the whole corpus yields: one chunk per function plus the module's own
# documentation chunk. Pinned so a chunker change that shifts the census
# fails here loudly instead of silently weakening the "nothing was cut" case.
_CORPUS_CHUNKS = _FUNCTION_COUNT + 2

# The shipped ``search.output`` bounds (``defaults/default_config.yaml``).
_YAML_DEFAULT_LIMIT = 10
_YAML_MAX_LIMIT = 1000


def _write_project(root: Path) -> Path:
    """One module holding ``_FUNCTION_COUNT`` tiny, individually-chunked functions."""
    project = root / "limitproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (pkg / "__init__.py").write_text("")
    body = ['"""Widget helpers."""\n']
    body += [
        f'\n\ndef widget_{i:02d}() -> int:\n    """Widget {i:02d} helper."""\n    return {i}\n'
        for i in range(_FUNCTION_COUNT)
    ]
    (pkg / "widgets.py").write_text("".join(body))
    return project


@dataclass(frozen=True, slots=True)
class _WiredIndex:
    """One real bundle plus a router per surface (pointers differ by surface)."""

    db_path: Path
    mcp_router: ToolRouter
    cli_router: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredIndex:
    project = _write_project(tmp_path)
    db_path = index_project_to_db(project, tmp_path / "limit.db")
    config = AppConfig.load()
    mcp_router, _m = build_routers(config, db_path=db_path, surface="mcp")
    cli_router, _c = build_routers(config, db_path=db_path, surface="cli")
    return _WiredIndex(db_path=db_path, mcp_router=mcp_router, cli_router=cli_router)


def _search(router: ToolRouter, **fields: object) -> ToolResponse:
    return asyncio.run(router.search_codebase(SearchInput(query="widget", **fields)))


def _envelope(router: ToolRouter, **fields: object) -> SearchEnvelope:
    return SearchEnvelope.model_validate(_search(router, **fields).structured())


# ── AC1: the requested limit reaches the pipeline ──────────────────────────


def test_chunk_search_returns_the_requested_thirty_rows(wired: _WiredIndex) -> None:
    """Thirty asked for, thirty returned — the pipeline's own default was 8."""
    envelope = _envelope(wired.mcp_router, kind="docs", limit=30)
    assert len(envelope.items) == 30


def test_member_search_returns_the_requested_thirty_rows(wired: _WiredIndex) -> None:
    """The member pipeline's YAML cap is 15; the request outranks it too."""
    envelope = _envelope(wired.mcp_router, kind="api", limit=30)
    assert len(envelope.items) == 30


def test_omitted_limit_returns_the_configured_default(wired: _WiredIndex) -> None:
    """No ``limit=`` → ``search.output.default_limit`` rows, not the step's 8."""
    envelope = _envelope(wired.mcp_router, kind="docs")
    assert len(envelope.items) == _YAML_DEFAULT_LIMIT


# ── AC2: a limit above the configured maximum is clamped, not rejected ─────


def test_limit_above_the_maximum_is_clamped_and_marked_truncated(
    wired: _WiredIndex,
) -> None:
    envelope = _envelope(wired.mcp_router, kind="docs", limit=_YAML_MAX_LIMIT + 1)
    assert len(envelope.items) == _CORPUS_CHUNKS  # the whole corpus, under the ceiling
    assert envelope.meta.truncated is True
    assert str(_YAML_MAX_LIMIT) in envelope.text


# ── AC3: a cut is marked; an uncut listing is not ─────────────────────────


def test_capped_search_marks_truncation_and_names_the_cut(wired: _WiredIndex) -> None:
    envelope = _envelope(wired.mcp_router, kind="docs", limit=5)
    assert len(envelope.items) == 5
    assert envelope.meta.truncated is True
    assert "limit=5" in envelope.text


def test_uncut_search_is_not_marked_truncated(wired: _WiredIndex) -> None:
    """A limit no cap reaches leaves the response reading as complete."""
    envelope = _envelope(wired.mcp_router, kind="docs", limit=100)
    assert len(envelope.items) == _CORPUS_CHUNKS
    assert envelope.meta.truncated is False


# ── AC4: the two surfaces agree ───────────────────────────────────────────


def _without_pointer_lines(text: str) -> str:
    """The response minus its follow-up pointers, which render per surface
    (``→ get_symbol(...)`` on MCP, ``→ pydocs-mcp symbol ...`` on the CLI) and
    so are the one part of a search body the two surfaces spell differently."""
    return "\n".join(line for line in strip_pointers(text).splitlines() if not line.startswith("→"))


@pytest.mark.parametrize("limit", [5, 30])
def test_cli_and_mcp_render_the_same_rows_and_footer(wired: _WiredIndex, limit: int) -> None:
    mcp = _search(wired.mcp_router, kind="docs", limit=limit)
    cli = _search(wired.cli_router, kind="docs", limit=limit)
    assert _without_pointer_lines(cli.text) == _without_pointer_lines(mcp.text)
    assert cli.items == mcp.items
    assert cli.meta["truncated"] == mcp.meta["truncated"]
