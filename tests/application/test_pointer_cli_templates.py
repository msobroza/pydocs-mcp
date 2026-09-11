"""Static guard: every CLI pointer template renders argv the real parser accepts.

``formatting._SHOW_TO_TOOL`` and ``formatting._POINTER_RENDERERS`` hold the CLI
half of every next-step pointer. A template that names a subcommand or a flag
``_build_parser`` does not define renders a follow-up no shell can run — and no
runtime test catches it, because pointers are plain text. This walks both tables
and parses each rendered command, so a renamed subcommand or flag fails here
instead of in a user's terminal.
"""

from __future__ import annotations

import shlex

import pytest

from pydocs_mcp.__main__ import _build_parser
from pydocs_mcp.application.formatting import _POINTER_RENDERERS, _SHOW_TO_TOOL

_TARGET = "pkg.mod.Symbol"


def _parse(command: str) -> None:
    """``pydocs-mcp symbol X --depth tree`` must parse as argv (argv[0] dropped)."""
    argv = shlex.split(command.removeprefix("→ ").strip())
    assert argv[0] == "pydocs-mcp", command
    _build_parser().parse_args(argv[1:])


@pytest.mark.parametrize("show", sorted(_SHOW_TO_TOOL))
def test_show_to_tool_cli_template_parses(show: str) -> None:
    _mcp_fmt, cli_fmt = _SHOW_TO_TOOL[show]
    _parse(cli_fmt.format(t=_TARGET))


@pytest.mark.parametrize("action", sorted(_POINTER_RENDERERS))
@pytest.mark.parametrize("target", ["", _TARGET])
def test_pointer_renderer_cli_template_parses(action: str, target: str) -> None:
    # Both target shapes: overview/why render a DIFFERENT command when the
    # target is empty (the whole-scope card / the governance dashboard).
    cli_render, _mcp_render = _POINTER_RENDERERS[action]
    _parse(cli_render(target))
