"""Wire-level checks for the tool-surface bug batch, on a REAL index.

Indexes a tiny project through ``tests/_index_fixture`` and drives the frozen
tools end to end — MCP through ``ToolRouter`` plus the envelope validator the
server itself uses, CLI through ``main()``.

``get_references`` on a MODULE target (spec §1) used to return the PageIndex
outline, which fails ``ReferencesEnvelope`` validation on MCP (surfacing as
``ServiceUnavailableError``) while the CLI printed the JSON and exited 0.
AC1.1 / AC1.2 / AC1.4 / AC1.6 / AC1.7 pin the fix on both surfaces.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pydocs_mcp.application.formatting import strip_pointers
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.mcp_inputs import ReferencesInput
from pydocs_mcp.application.tool_response import ReferencesEnvelope, ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import _to_call_tool_result, build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "wireproj"
version = "0.0.0"
dependencies = []
"""

_MOD_PY = '''\
"""The imported module."""


class Alpha:
    """A class other modules import."""

    def run(self) -> int:
        return beta()


def beta() -> int:
    """A function other modules import."""
    return 1
'''

_USER_PY = '''\
"""Imports both the module and one of its members."""

import pkg.mod
from pkg.mod import Alpha


def use() -> int:
    """Uses both import shapes."""
    return Alpha().run() + pkg.mod.beta()
'''

_README_MD = """\
# Wire fixture

A markdown module, so the resolution channel has a non-`.py` syntactic case.
"""

_GRAPH_DIRECTIONS = ("callers", "callees", "impact", "governed_by")


def _write_project(root: Path) -> Path:
    """``pkg.mod`` (class + function) and ``pkg.user``, which imports both ways."""
    project = root / "wireproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (project / "README.md").write_text(_README_MD)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_MOD_PY)
    (pkg / "user.py").write_text(_USER_PY)
    return project


@dataclass(frozen=True, slots=True)
class _WiredIndex:
    """One real bundle plus a router per surface (pointers differ by surface)."""

    db_path: Path
    mcp_router: ToolRouter
    cli_router: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredIndex:
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "wire.db")
    config = AppConfig.load()
    mcp_router, _m = build_routers(config, db_path=db_path, surface="mcp")
    cli_router, _c = build_routers(config, db_path=db_path, surface="cli")
    return _WiredIndex(db_path=db_path, mcp_router=mcp_router, cli_router=cli_router)


def _references(router: ToolRouter, target: str, direction: str) -> ToolResponse:
    return asyncio.run(router.get_references(ReferencesInput(target=target, direction=direction)))


def _validated(response: ToolResponse) -> ReferencesEnvelope:
    """Validate exactly as ``server.py`` does, then re-read the typed envelope."""
    _to_call_tool_result(response, ReferencesEnvelope)
    return ReferencesEnvelope.model_validate(response.structured())


def _cli_exit_code(db_path: Path, target: str, direction: str) -> int:
    from pydocs_mcp.__main__ import main

    argv = shlex.split(f"pydocs-mcp refs {target} --direction {direction}")
    with patch("sys.argv", argv + ["--db", str(db_path)]):
        return main()


# ── AC1.1 / AC1.7: every graph direction validates on a module target ──────


@pytest.mark.parametrize("direction", _GRAPH_DIRECTIONS)
def test_module_target_envelope_validates_for_every_direction(
    wired: _WiredIndex, direction: str
) -> None:
    envelope = _validated(_references(wired.mcp_router, "pkg.mod", direction))
    assert not envelope.text.lstrip().startswith("{")  # never the PageIndex JSON
    assert envelope.meta.resolution == "syntactic"


def test_module_callers_name_the_importing_module(wired: _WiredIndex) -> None:
    """AC1.2 on real edges: ``import pkg.mod`` and ``from pkg.mod import Alpha``."""
    envelope = _validated(_references(wired.mcp_router, "pkg.mod", "callers"))
    assert [i.kind for i in envelope.items] == ["imports"] * len(envelope.items)
    assert "pkg.user" in {i.from_qualified_name for i in envelope.items}


def test_markdown_module_root_reports_syntactic_resolution(wired: _WiredIndex) -> None:
    """AC1.7: a ``.md`` root is syntactic; its graph is simply empty."""
    envelope = _validated(_references(wired.mcp_router, "README", "callers"))
    assert envelope.meta.resolution == "syntactic"


# ── AC1.4: inherits on a module is an InvalidArgumentError ─────────────────


def test_module_inherits_raises_invalid_argument(wired: _WiredIndex) -> None:
    with pytest.raises(InvalidArgumentError) as exc:
        _references(wired.mcp_router, "pkg.mod", "inherits")
    assert "pkg.mod" in str(exc.value)
    assert "module" in str(exc.value)


# ── AC1.6: the two surfaces agree ──────────────────────────────────────────


@pytest.mark.parametrize("direction", _GRAPH_DIRECTIONS)
def test_cli_and_mcp_render_the_same_module_references(wired: _WiredIndex, direction: str) -> None:
    mcp = _references(wired.mcp_router, "pkg.mod", direction)
    cli = _references(wired.cli_router, "pkg.mod", direction)
    assert strip_pointers(cli.text) == strip_pointers(mcp.text)
    assert cli.items == mcp.items


def test_cli_exit_codes_are_zero_on_success_and_one_on_a_rejected_direction(
    wired: _WiredIndex,
) -> None:
    assert _cli_exit_code(wired.db_path, "pkg.mod", "callers") == 0
    assert _cli_exit_code(wired.db_path, "pkg.mod", "inherits") == 1
