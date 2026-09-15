"""The ``read`` pointer action at the tool-call boundary, on a REAL index.

Sibling of ``tests/test_pointer_table_wire.py``, scoped to the three responses
that hand an agent a concrete line window instead of asking it to guess one: a
``grep`` content hit, a ``read_file`` cut by its own limit, and a
``get_symbol(depth="source")`` body cut by the line cap. Every claim is made on
the rendered response of a router built exactly as ``server.py`` and the CLI
build it, in both call forms.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from pydocs_mcp.application.mcp_inputs import (
    GlobInput,
    GrepInput,
    ReadFileInput,
    SymbolInput,
)
from pydocs_mcp.application.tool_response import ToolResponse
from pydocs_mcp.application.tool_router import ToolRouter
from pydocs_mcp.retrieval.config import AppConfig
from pydocs_mcp.server import build_routers
from tests._index_fixture import index_project_to_db

_PYPROJECT = """\
[project]
name = "readproj"
version = "0.0.0"
dependencies = []
"""

# The needle sits at line 20 of a 57-line file, far enough from both ends that
# the shipped 40-line window is neither clamped at line 1 nor at the last line.
_NEEDLE = "read_pointer_needle"
_NEEDLE_LINE = 20
_WINDOW_START = 10  # _NEEDLE_LINE - the 10-line lead
_FILE_LINES = 57
# get_symbol(depth="source") renders this many lines before the cap bites, so
# long_fn's 54-line body is cut at its 34th remaining line.
_SOURCE_CAP = 20
_SOURCE_ELIDED = 34
# long_fn starts at file line 4, so the cut body resumes just past the cap.
_SOURCE_RESUME = 4 + _SOURCE_CAP

_SMALL_WINDOW = f"""\
symbol_source:
  max_lines: {_SOURCE_CAP}
"""

_NO_READ_POINTERS = (
    _SMALL_WINDOW
    + """\
output:
  pointers:
    table:
      grep_hit: { together: [], then: [] }
      read_continuation: { together: [], then: [] }
      source: { together: [], then: [] }
"""
)


def _mod_py() -> str:
    """A module whose one function is longer than the source line cap."""
    head = [
        '"""The module the read pointer reaches into."""',
        "",
        "",
        "def long_fn() -> int:",
        '    """Longer than the cap, so depth="source" is cut."""',
        "    total = 0",
    ]
    body = [f"    total += {i}  # step {i}" for i in range(1, 51)]
    body[_NEEDLE_LINE - len(head) - 1] = f"    total += 14  # {_NEEDLE}"
    return "\n".join([*head, *body, "    return total", ""])


def _write_project(root: Path) -> Path:
    project = root / "readproj"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (project / "pyproject.toml").write_text(_PYPROJECT)
    (pkg / "__init__.py").write_text("")
    (pkg / "mod.py").write_text(_mod_py())
    return project


@dataclass(frozen=True, slots=True)
class _WiredRead:
    """One real bundle, a router per surface, and one with the rows cleared."""

    mcp: ToolRouter
    cli: ToolRouter
    cleared: ToolRouter


def _router(config: AppConfig, db_path: Path, surface: str) -> ToolRouter:
    router, _services = build_routers(config, db_path=db_path, surface=surface)
    return router


@pytest.fixture
def wired(tmp_path: Path) -> _WiredRead:
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "read.db")
    capped = tmp_path / "capped.yaml"
    capped.write_text(_SMALL_WINDOW)
    cleared = tmp_path / "cleared.yaml"
    cleared.write_text(_NO_READ_POINTERS)
    return _WiredRead(
        mcp=_router(AppConfig.load(explicit_path=capped), db_path, "mcp"),
        cli=_router(AppConfig.load(explicit_path=capped), db_path, "cli"),
        cleared=_router(AppConfig.load(explicit_path=cleared), db_path, "mcp"),
    )


def _grep(router: ToolRouter, **kwargs: object) -> ToolResponse:
    payload = GrepInput(pattern=_NEEDLE, output_mode="content", **kwargs)  # type: ignore[arg-type]
    return asyncio.run(router.grep(payload))


def _read(router: ToolRouter, limit: int) -> ToolResponse:
    return asyncio.run(router.read_file(ReadFileInput(file_path="pkg/mod.py", limit=limit)))


def _source(router: ToolRouter) -> ToolResponse:
    return asyncio.run(router.get_symbol(SymbolInput(target="pkg.mod.long_fn", depth="source")))


def _last_line(text: str) -> str:
    return text.rstrip("\n").splitlines()[-1]


# ── a grep content hit hands over the window around the match ──────────────


def test_grep_content_hit_ends_with_a_read_pointer_in_mcp_form(wired: _WiredRead) -> None:
    assert _last_line(_grep(wired.mcp).text) == (
        'Together: → read_file(file_path="pkg/mod.py", offset=10, limit=40)'
    )


def test_grep_content_hit_ends_with_a_read_pointer_in_cli_form(wired: _WiredRead) -> None:
    assert _last_line(_grep(wired.cli).text) == (
        "Together: → pydocs-mcp read_file pkg/mod.py --offset 10 --limit 40"
    )


def test_the_window_covers_the_matching_line_and_what_leads_up_to_it(
    wired: _WiredRead,
) -> None:
    """The advertised call is the one an agent would have had to construct."""
    text = _grep(wired.mcp).text
    assert f"pkg/mod.py:{_NEEDLE_LINE}:" in text
    assert _WINDOW_START < _NEEDLE_LINE < _WINDOW_START + 40


def test_a_grep_hit_whose_context_already_covers_the_window_offers_none(
    wired: _WiredRead,
) -> None:
    """Self-pointing: never a read call for lines this response just rendered."""
    text = _grep(wired.mcp, context=_FILE_LINES).text
    assert "read_file(" not in text


def test_glob_offers_no_pointer(wired: _WiredRead) -> None:
    response = asyncio.run(wired.mcp.glob(GlobInput(pattern="**/*.py")))
    assert "pkg/mod.py" in response.text
    assert "read_file(" not in response.text
    assert "Together:" not in response.text


# ── a read cut by its own limit continues from the next line ───────────────


def test_read_at_its_limit_continues_from_the_next_line(wired: _WiredRead) -> None:
    text = _read(wired.mcp, limit=3).text
    assert _last_line(text) == (
        '- 54 more lines of pkg/mod.py after line 3 → read_file(file_path="pkg/mod.py", '
        "offset=4, limit=3)"
    )


def test_read_at_its_limit_continues_in_cli_form(wired: _WiredRead) -> None:
    assert _last_line(_read(wired.cli, limit=3).text).endswith(
        "→ pydocs-mcp read_file pkg/mod.py --offset 4 --limit 3"
    )


def test_a_cut_read_still_reports_truncation(wired: _WiredRead) -> None:
    """The continuation IS the ledger entry, so the flag rides the same cut."""
    response = _read(wired.mcp, limit=3)
    assert response.meta["truncated"] is True
    assert "file continues" not in response.text


def test_a_read_that_reaches_the_end_offers_no_continuation(wired: _WiredRead) -> None:
    response = _read(wired.mcp, limit=_FILE_LINES)
    assert "read_file(" not in response.text
    assert response.meta["truncated"] is False


# ── a source body cut by the line cap continues from the cut line ──────────


def test_source_at_its_cap_continues_from_the_cut_line(wired: _WiredRead) -> None:
    text = _source(wired.mcp).text
    assert _last_line(text).endswith(
        f'→ read_file(file_path="pkg/mod.py", offset={_SOURCE_RESUME}, limit={_SOURCE_ELIDED})'
    )


def test_source_at_its_cap_continues_in_cli_form(wired: _WiredRead) -> None:
    assert _last_line(_source(wired.cli).text).endswith(
        f"→ pydocs-mcp read_file pkg/mod.py --offset {_SOURCE_RESUME} --limit {_SOURCE_ELIDED}"
    )


def test_the_prose_read_the_file_directly_footer_is_gone(wired: _WiredRead) -> None:
    response = _source(wired.mcp)
    assert "directly]" not in response.text
    assert response.meta["truncated"] is True
    assert f"{_SOURCE_ELIDED} source lines beyond the {_SOURCE_CAP}-line cap" in response.text


def test_an_uncut_source_body_offers_no_read_pointer(tmp_path: Path) -> None:
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "uncut.db")
    router = _router(AppConfig.load(), db_path, "mcp")
    assert "read_file(" not in _source(router).text


# ── a deployment can take the window away ──────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [lambda r: _grep(r), lambda r: _read(r, limit=3), _source],
    ids=["grep", "read", "source"],
)
def test_clearing_the_rows_removes_every_read_pointer(wired: _WiredRead, call) -> None:  # type: ignore[no-untyped-def]
    text = call(wired.cleared).text
    assert "read_file(" not in text
    assert "[[next:read:" not in text


def test_a_deployment_with_pointers_off_leaks_no_call_syntax(tmp_path: Path) -> None:
    """``output.next_pointers.enabled: false`` strips the group line with it."""
    db_path = index_project_to_db(_write_project(tmp_path), tmp_path / "stripped.db")
    overlay = tmp_path / "stripped.yaml"
    overlay.write_text(_SMALL_WINDOW + "output:\n  next_pointers:\n    enabled: false\n")
    router = _router(AppConfig.load(explicit_path=overlay), db_path, "mcp")
    for text in (_grep(router).text, _read(router, limit=3).text, _source(router).text):
        assert "read_file(" not in text
        assert "[[next:" not in text
        assert "Together:" not in text


def test_clearing_the_source_row_keeps_the_cut_itself_reported(wired: _WiredRead) -> None:
    """The pointer is optional; knowing the body was cut is not."""
    response = _source(wired.cleared)
    assert response.meta["truncated"] is True
    assert f"{_SOURCE_ELIDED} source lines beyond the {_SOURCE_CAP}-line cap" in response.text
