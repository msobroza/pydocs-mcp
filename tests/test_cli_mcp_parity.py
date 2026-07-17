"""CLI/MCP parity (R3, docs/tool-contracts.md §6 note 4 + note 3).

The nine CLI subcommands are canonically named exactly like the MCP tools;
the historical short verbs stay as argparse aliases. Help/description prose
comes from the ``TOOL_DOCS`` single source, the top-level CLI description is
``SERVER_INSTRUCTIONS``, and the enum vocabularies (kind/scope/depth/
direction/output_mode) are single-sourced in ``mcp_inputs`` Literal aliases
shared by the pydantic models, the MCP handler signatures (so the advertised
inputSchema carries the enums), and the argparse ``choices``.
"""

from __future__ import annotations

import argparse
from typing import get_args

import pytest

from pydocs_mcp.application.mcp_inputs import (
    DepthLiteral,
    DirectionLiteral,
    GrepInput,
    KindLiteral,
    OutputModeLiteral,
    ReferencesInput,
    ScopeLiteral,
    SearchInput,
    SymbolInput,
)
from pydocs_mcp.application.tool_docs import SERVER_INSTRUCTIONS, TOOL_DOCS

# canonical subcommand -> aliases (contract §6 note 4). The filesystem trio
# was born canonical (tool name == verb) and has no alias.
_CANONICAL_ALIASES: dict[str, tuple[str, ...]] = {
    "get_overview": ("overview",),
    "search_codebase": ("search",),
    "get_symbol": ("symbol",),
    "get_context": ("context",),
    "get_references": ("refs",),
    "get_why": ("why",),
    "grep": (),
    "glob": (),
    "read_file": (),
}


def _subparsers_action() -> argparse._SubParsersAction:
    from pydocs_mcp.__main__ import _build_parser

    parser = _build_parser()
    return next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))


# ── canonical names + aliases ──────────────────────────────────────────────


def test_all_nine_canonical_subcommands_registered() -> None:
    choices = _subparsers_action().choices
    for canonical in _CANONICAL_ALIASES:
        assert canonical in choices, f"missing canonical subcommand {canonical}"


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [(c, a) for c, aliases in _CANONICAL_ALIASES.items() for a in aliases],
)
def test_alias_maps_to_the_canonical_parser(canonical: str, alias: str) -> None:
    choices = _subparsers_action().choices
    assert choices[alias] is choices[canonical]


def test_cmd_table_covers_every_registered_subcommand() -> None:
    """Every parseable verb (canonical + alias + ops verbs) has a dispatcher —
    argparse stores the TYPED name in ``args.cmd``, so aliases need entries."""
    from pydocs_mcp.__main__ import _CMD_TABLE

    assert set(_subparsers_action().choices) == set(_CMD_TABLE)


@pytest.mark.parametrize(
    ("canonical", "alias"),
    [(c, a) for c, aliases in _CANONICAL_ALIASES.items() for a in aliases],
)
def test_cmd_table_alias_and_canonical_share_a_handler(canonical: str, alias: str) -> None:
    from pydocs_mcp.__main__ import _CMD_TABLE

    assert _CMD_TABLE[alias] is _CMD_TABLE[canonical]


# ── TOOL_DOCS-sourced prose ────────────────────────────────────────────────


@pytest.mark.parametrize("tool", sorted(_CANONICAL_ALIASES))
def test_subcommand_description_is_full_tool_docs_text(tool: str) -> None:
    sub = _subparsers_action().choices[tool]
    assert sub.description == TOOL_DOCS[tool]


@pytest.mark.parametrize("tool", sorted(_CANONICAL_ALIASES))
def test_subcommand_help_is_tool_docs_first_line(tool: str) -> None:
    action = _subparsers_action()
    pseudo = next(c for c in action._choices_actions if c.dest == tool)
    assert pseudo.help == TOOL_DOCS[tool].splitlines()[0]


def test_top_level_description_is_server_instructions() -> None:
    from pydocs_mcp.__main__ import _build_parser

    assert _build_parser().description == SERVER_INSTRUCTIONS


# ── YAML-wired limit: no argparse default literal (contract §6 note 4) ─────


def test_search_codebase_limit_has_no_argparse_default() -> None:
    sub = _subparsers_action().choices["search_codebase"]
    limit = next(a for a in sub._actions if a.dest == "limit")
    assert limit.default is None
    assert "default: 10" not in (limit.help or "")


# ── enum single-sourcing: pydantic Literal == argparse choices == schema ───

_ENUM_PARITY: list[tuple[str, str, object, object]] = [
    ("search_codebase", "kind", KindLiteral, SearchInput),
    ("search_codebase", "scope", ScopeLiteral, SearchInput),
    ("get_symbol", "depth", DepthLiteral, SymbolInput),
    ("get_references", "direction", DirectionLiteral, ReferencesInput),
    ("grep", "output_mode", OutputModeLiteral, GrepInput),
    ("grep", "scope", ScopeLiteral, GrepInput),
]


class _RecorderMCP:
    """Registration-only FastMCP double — captures handler fns by tool name."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, **kwargs: object):
        def deco(fn):
            self.tools[str(kwargs["name"])] = fn
            return fn

        return deco


@pytest.fixture(scope="module")
def registered_handlers() -> dict[str, object]:
    from pydocs_mcp.server import _register_tools

    rec = _RecorderMCP()
    _register_tools(rec, tools=None)
    return rec.tools


@pytest.mark.parametrize(("tool", "param", "literal", "model"), _ENUM_PARITY)
def test_enum_values_are_single_sourced(
    registered_handlers: dict[str, object], tool: str, param: str, literal: object, model: object
) -> None:
    from mcp.server.fastmcp.utilities.func_metadata import func_metadata

    values = list(get_args(literal))

    # pydantic input model references the shared alias.
    assert model.model_fields[param].annotation == literal

    # argparse choices carry the same values in the same order.
    sub = _subparsers_action().choices[tool]
    dest = param
    action = next(a for a in sub._actions if a.dest == dest)
    assert list(action.choices) == values

    # The MCP handler's advertised inputSchema carries the enum (§6 note 3).
    fn = registered_handlers[tool]
    props = func_metadata(fn).arg_model.model_json_schema(by_alias=True)["properties"]
    assert props[param].get("enum") == values, f"{tool}.{param} inputSchema advertises no enum"
