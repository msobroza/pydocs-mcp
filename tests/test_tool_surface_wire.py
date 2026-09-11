"""Wire-level checks for the tool-surface bug batch, on a REAL index.

Indexes a tiny project through ``tests/_index_fixture`` and drives the frozen
tools end to end — MCP through ``ToolRouter`` plus the envelope validator the
server itself uses, CLI through ``main()``.

``get_references`` on a MODULE target (spec §1) used to return the PageIndex
outline, which fails ``ReferencesEnvelope`` validation on MCP (surfacing as
``ServiceUnavailableError``) while the CLI printed the JSON and exited 0.
AC1.1 / AC1.2 / AC1.4 / AC1.6 / AC1.7 pin the fix on both surfaces.

``get_symbol(depth="source")`` on a CLASS (spec §2) used to render only the
class-header chunk while ``items[0]`` claimed the whole span. AC2.1 / AC2.2 /
AC2.6 pin the rebuilt span end to end, on the very decorator and comment lines
the index does not store.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

from pydocs_mcp.application.formatting import strip_pointers
from pydocs_mcp.application.mcp_errors import InvalidArgumentError
from pydocs_mcp.application.mcp_inputs import ReferencesInput, SymbolInput
from pydocs_mcp.application.tool_response import (
    ReferencesEnvelope,
    SymbolEnvelope,
    ToolResponse,
)
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


@final
class Alpha:
    """A class other modules import."""

    ATTR = 1

    def run(self) -> int:
        return beta()

    # A comment between methods — never a tree node.
    @staticmethod
    def stat() -> int:
        return 2


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
    project: Path
    mcp_router: ToolRouter
    cli_router: ToolRouter


@pytest.fixture
def wired(tmp_path: Path) -> _WiredIndex:
    project = _write_project(tmp_path)
    db_path = index_project_to_db(project, tmp_path / "wire.db")
    config = AppConfig.load()
    mcp_router, _m = build_routers(config, db_path=db_path, surface="mcp")
    cli_router, _c = build_routers(config, db_path=db_path, surface="cli")
    return _WiredIndex(
        db_path=db_path, project=project, mcp_router=mcp_router, cli_router=cli_router
    )


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


# ── AC2.1 / AC2.2 / AC2.6: depth="source" rebuilds a class's whole span ────

_MARKER_RE = re.compile(r"^\[lines? (\d+)(?:-(\d+))? not in the index\]$")


def _source(router: ToolRouter, target: str) -> ToolResponse:
    return asyncio.run(router.get_symbol(SymbolInput(target=target, depth="source")))


def _fenced_lines_by_number(body: str, start: int) -> dict[int, str]:
    """Assign each fenced line the file line number the rendering claims for it."""
    numbered: dict[int, str] = {}
    cursor, inside = start, False
    for line in body.splitlines():
        if line.startswith("```"):
            inside = line != "```"
        elif inside:
            numbered[cursor] = line
            cursor += 1
        elif (match := _MARKER_RE.match(line)) is not None:
            assert int(match.group(1)) == cursor, f"marker {line!r} does not resume at {cursor}"
            cursor = int(match.group(2) or match.group(1)) + 1
    return numbered


def test_class_source_lines_match_the_indexed_file_on_disk(wired: _WiredIndex) -> None:
    """Every fenced line is the project file's line at the number it is rendered at."""
    response = _source(wired.mcp_router, "pkg.mod.Alpha")
    envelope = SymbolEnvelope.model_validate(response.structured())
    lines = (wired.project / "pkg" / "mod.py").read_text().splitlines()
    numbered = _fenced_lines_by_number(envelope.text, envelope.items[0].start_line or 0)
    assert "        return 2" in numbered.values()  # the last method, past the class chunk
    assert all(text == lines[number - 1] for number, text in numbered.items())


def test_class_source_marks_exactly_the_lines_the_index_lacks(wired: _WiredIndex) -> None:
    """The comment and the ``@staticmethod`` decorator are gaps; nothing else is."""
    response = _source(wired.mcp_router, "pkg.mod.Alpha")
    envelope = SymbolEnvelope.model_validate(response.structured())
    start, end = envelope.items[0].start_line or 0, envelope.items[0].end_line or 0
    lines = (wired.project / "pkg" / "mod.py").read_text().splitlines()
    gaps = set(range(start, end + 1)) - set(_fenced_lines_by_number(envelope.text, start))
    # Blank lines can also fall in a gap (a trailing one is not recoverable from
    # a "\n"-joined slice); the CODE the index lacks is what this pins.
    assert {lines[n - 1].strip() for n in gaps if lines[n - 1].strip()} == {
        "# A comment between methods — never a tree node.",
        "@staticmethod",
    }
    assert envelope.meta.truncated is False


def test_cli_and_mcp_render_the_same_class_source(wired: _WiredIndex) -> None:
    mcp = _source(wired.mcp_router, "pkg.mod.Alpha")
    cli = _source(wired.cli_router, "pkg.mod.Alpha")
    assert strip_pointers(cli.text) == strip_pointers(mcp.text)
    assert cli.items == mcp.items
