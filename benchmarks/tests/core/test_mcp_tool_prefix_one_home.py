"""One home for the MCP tool-name prefix.

``mcp__`` used to be declared three times — in ``agent_track/_parse.py``,
``trajectory/stream_reader.py`` and ``trajectory/pointer_lines.py`` — and two of
those copies called themselves the single source of truth. All three now read
``pydocs_eval.tool_names.MCP_TOOL_PREFIX``; this test is what keeps a fourth
copy from growing back.

The identity assertion alone would be a weak pin: CPython interns
identifier-shaped string literals, so a re-added local ``"mcp__"`` would still
satisfy ``is``. The load-bearing check is the source scan for a re-declared
literal.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import pytest

from pydocs_eval import tool_names
from pydocs_eval.agent_track import _parse
from pydocs_eval.tool_names import MCP_TOOL_PREFIX
from pydocs_eval.trajectory import pointer_lines, stream_reader

# Every module that classifies a tool name by its MCP prefix.
PREFIX_READERS = (_parse, stream_reader, pointer_lines)

# The declaration form a re-added local copy would take. Prose mentions of the
# prefix in these files sit inside double backticks, never this quoted spelling.
_PREFIX_DECLARATION = '"mcp__"'


def _source_of(module: ModuleType) -> str:
    return Path(inspect.getfile(module)).read_text(encoding="utf-8")


def _imported_module_names(module: ModuleType) -> set[str]:
    tree = ast.parse(_source_of(module))
    plain = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    from_targets = {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    return plain | from_targets


def test_shared_prefix_value_is_unchanged() -> None:
    assert MCP_TOOL_PREFIX == "mcp__"


@pytest.mark.parametrize("module", PREFIX_READERS, ids=lambda m: m.__name__)
def test_reader_uses_the_shared_prefix_object(module: ModuleType) -> None:
    assert module.MCP_TOOL_PREFIX is MCP_TOOL_PREFIX


@pytest.mark.parametrize("module", PREFIX_READERS, ids=lambda m: m.__name__)
def test_reader_declares_no_local_prefix_literal(module: ModuleType) -> None:
    assert _PREFIX_DECLARATION not in _source_of(module)


def test_shared_home_stays_a_leaf_module() -> None:
    """No ``pydocs_eval`` import may enter the shared home, so no subpackage
    importing it can close a cycle."""
    inside_the_package = {
        n for n in _imported_module_names(tool_names) if n.startswith("pydocs_eval")
    }
    assert inside_the_package == set()


def test_shared_home_keeps_the_zero_product_import_floor() -> None:
    """ADR 0009: the eval package imports no ``pydocs_mcp``, so the prefix is
    mirrored here rather than taken from the product's CLI-agent adapter."""
    from_product = {n for n in _imported_module_names(tool_names) if n.startswith("pydocs_mcp")}
    assert from_product == set()
