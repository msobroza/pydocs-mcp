"""End-to-end: every pointer ``get_overview`` emits actually resolves (spec §5).

Indexes a real fixture project through the shared ``tests/_index_fixture``
helper, renders the §D17 card on BOTH surfaces, then RUNS every emitted
follow-up: each MCP pointer is parsed back into ``tool(**kwargs)``, validated
through that tool's input model, awaited on the router and re-validated through
its output envelope; each CLI pointer is split back into argv and driven through
``main()``, which must exit 0.

The bug this pins (AC5.4): the card used to advertise ``get_context`` for module
targets (rejected by ``_resolve_context_target``), ``get_symbol`` for console
script *names* and for unindexed dependency distributions — 19 of the 21
pointers on the owner's repro were dead calls.
"""

from __future__ import annotations

import ast
import asyncio
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from pydocs_mcp.application import mcp_inputs
from pydocs_mcp.application.mcp_inputs import OverviewInput
from pydocs_mcp.application.tool_response import (
    ContextEnvelope,
    OverviewEnvelope,
    ReferencesEnvelope,
    SearchEnvelope,
    SymbolEnvelope,
    WhyEnvelope,
)
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import _to_call_tool_result, build_routers
from tests._index_fixture import index_project_to_db

# ── the fixture project (spec §5 "Index setup") ────────────────────────────

_PYPROJECT = """\
[project]
name = "demoproj"
version = "0.0.0"
dependencies = []

[project.scripts]
demo = "demo.cli:main"
demo-tool = "demo.cli:main"
app-cli = "demo.cli:app"
reexp = "demo.cli:helper"
ghost = "notindexed.x:main"
"""

_CORE_PY = '''\
"""Core module."""

import typing


class Engine:
    """The engine."""

    def run(self) -> None:
        return None


def helper(value: typing.Any) -> typing.Any:
    """Help."""
    # DECISION: helper stays pure so the engine can be tested headless.
    return value
'''

_CLI_PY = '''\
"""CLI module."""

import requests

from demo.core import Engine, helper

app = object()


def main() -> int:
    """Entry point."""
    return helper(Engine().run()) or 0
'''


def _write_project(root: Path) -> Path:
    """The spec's fixture tree: two modules, five scripts, a dot-config TOML."""
    project = root / "demoproj"
    pkg = project / "demo"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (project / ".demo-config.toml").write_text("[tool.demo]\nverbose = true\n")
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text(_CORE_PY)
    (pkg / "cli.py").write_text(_CLI_PY)
    return project


# ── pointer harness ────────────────────────────────────────────────────────

# tool name → (input model, output envelope). The router method shares the name.
_TOOL_MODELS: dict[str, tuple[type[BaseModel], type[BaseModel]]] = {
    "get_overview": (mcp_inputs.OverviewInput, OverviewEnvelope),
    "get_symbol": (mcp_inputs.SymbolInput, SymbolEnvelope),
    "get_context": (mcp_inputs.ContextInput, ContextEnvelope),
    "get_references": (mcp_inputs.ReferencesInput, ReferencesEnvelope),
    "get_why": (mcp_inputs.WhyInput, WhyEnvelope),
    "search_codebase": (mcp_inputs.SearchInput, SearchEnvelope),
}

# Pointers render at the END of a bullet as well as on their own line, so these
# are deliberately unanchored — an ``^``-anchored pattern silently matched only
# the own-line tokens and made this whole file vacuous.
_MCP_POINTER_RE = re.compile(r"→ (\w+\([^\n]*\))")
_CLI_POINTER_RE = re.compile(r"→ (pydocs-mcp [^\n]*)")


@dataclass(frozen=True, slots=True)
class _OverviewRender:
    """One indexed fixture plus its card text on both surfaces."""

    db_path: Path
    mcp_text: str
    cli_text: str


@pytest.fixture
def rendered(tmp_path: Path) -> _OverviewRender:
    """Index the fixture project, then render get_overview on both surfaces."""
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "demo.db")
    return _OverviewRender(
        db_path=db_path,
        mcp_text=_render_overview(db_path, "mcp"),
        cli_text=_render_overview(db_path, "cli"),
    )


def _render_overview(db_path: Path, surface: str) -> str:
    router, _services = build_routers(AppConfig.load(), db_path=db_path, surface=surface)
    return asyncio.run(router.get_overview(OverviewInput())).text


def _call_kwargs(call_src: str) -> tuple[str, dict[str, Any]]:
    """``get_symbol(target="x", depth="tree")`` → ``("get_symbol", {...})``."""
    call = ast.parse(call_src, mode="eval").body
    assert isinstance(call, ast.Call), call_src
    assert isinstance(call.func, ast.Name), call_src
    kwargs = {kw.arg: ast.literal_eval(kw.value) for kw in call.keywords if kw.arg}
    return call.func.id, kwargs


def _run_mcp_pointer(db_path: Path, call_src: str) -> None:
    """Validate input → await the router method → validate the output envelope."""
    name, kwargs = _call_kwargs(call_src)
    input_model, envelope = _TOOL_MODELS[name]
    router, _services = build_routers(AppConfig.load(), db_path=db_path, surface="mcp")
    payload = input_model(**kwargs)
    response = asyncio.run(getattr(router, name)(payload))
    _to_call_tool_result(response, envelope)


def _run_cli_pointer(db_path: Path, command: str) -> int:
    """Drive the rendered CLI command through ``main()``; return its exit code."""
    from pydocs_mcp.__main__ import main

    argv = shlex.split(command) + ["--db", str(db_path)]
    with patch("sys.argv", argv):
        return main()


# ── AC5.4: every emitted pointer resolves, on both surfaces ────────────────


def test_every_mcp_overview_pointer_executes(rendered: _OverviewRender) -> None:
    pointers = _MCP_POINTER_RE.findall(rendered.mcp_text)
    assert pointers, rendered.mcp_text
    for call_src in pointers:
        _run_mcp_pointer(rendered.db_path, call_src)


def test_every_cli_overview_pointer_exits_zero(rendered: _OverviewRender) -> None:
    pointers = _CLI_POINTER_RE.findall(rendered.cli_text)
    assert pointers, rendered.cli_text
    for command in pointers:
        assert _run_cli_pointer(rendered.db_path, command) == 0, command


def test_mcp_and_cli_pointer_counts_are_equal(rendered: _OverviewRender) -> None:
    assert len(_MCP_POINTER_RE.findall(rendered.mcp_text)) == len(
        _CLI_POINTER_RE.findall(rendered.cli_text)
    )


# ── non-vacuity + the dead-pointer targets that must stay silent ───────────


def test_pointer_kinds_are_all_present_and_dead_targets_are_silent(
    rendered: _OverviewRender,
) -> None:
    """Guard against a vacuous pass: each pointer kind fires, and each target
    the fix deliberately drops (non-node callable, unindexed module, unindexed
    dependency, dash-named config module) advertises nothing."""
    text = rendered.mcp_text
    assert 'get_symbol(target="demo.core", depth="tree")' in text  # module map
    assert 'get_symbol(target="demo.cli.main")' in text  # script callable + root
    assert "get_why()" in text  # decisions block
    for dead in ('target="app-cli"', 'target="reexp"', 'target="ghost"'):
        assert dead not in text
    for dead in ('target="requests"', 'target="typing"', '".demo-config.toml"'):
        assert dead not in text
