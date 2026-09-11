"""One ``QueryCursor.matches()`` row → one top-level symbol.

Split out of ``multilang_treesitter.py`` so the chunker file stays small and
this mapping — the part both the chunker and the analyzers' attribution index
read (multilang spec §4.4) — is greppable on its own. Pure functions over the
capture dict: no grammar loading, no caches, no I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tree_sitter import Node

    from pydocs_mcp.extraction.model import NodeKind

    _Captures = dict[str, list[Node]]  # one ``matches()`` row: item / name / wrapper

# (kind, name, start_line, end_line) for one extracted top-level symbol.
_Symbol = tuple["NodeKind", str, int, int]

# A tree-sitter ``(row, column)`` point, both 0-indexed; end points are
# exclusive. ``_PositionedSymbol`` carries a symbol plus its attribution
# span's start/end points (``_attribution_node``): the chunker needs only the
# symbol, while the analyzers' attribution index needs the columns to tell
# apart top-level items that share one line (minified JS/TS, one-line C).
_TreePoint = tuple[int, int]
_PositionedSymbol = tuple[_Symbol, _TreePoint, _TreePoint]

# Item types whose ONE statement can name several top-level symbols
# (`const a = …, b = …`, terser `join_vars` output). Their attribution span is
# the symbol's own declarator, not the shared statement: with the statement's
# points every capture in it bisected to the LAST declarator — a wrong edge.
# Attribution only: ``_Symbol`` keeps the whole statement's rows (``_symbol_extent_node``).
_PER_DECLARATOR_ITEM_TYPES = frozenset({"lexical_declaration"})


def _symbol_extent_node(captures: _Captures) -> Any:
    """The outermost node of one top-level symbol: the ``@wrapper``
    (an ``export_statement``) when captured, else ``@item``. A decorator
    above ``export`` hangs on the statement in both grammars and ``export
    default`` may take a row of its own; spanning the declaration alone dropped
    those rows from every chunk (issue #246 item 1 review — Angular's
    ``@Component(...)`` vanished from search, its calls went to the module).
    """
    wrapper = captures.get("wrapper")
    return wrapper[0] if wrapper else captures["item"][0]


def _attribution_node(captures: _Captures) -> Any:
    """The node whose points bound one symbol's attribution span: the @name
    node's own ``variable_declarator`` for a multi-declarator statement
    (``_PER_DECLARATOR_ITEM_TYPES``), else the symbol's extent node."""
    item = captures["item"][0]
    names = captures.get("name")
    if item.type in _PER_DECLARATOR_ITEM_TYPES and names:
        return names[0].parent
    return _symbol_extent_node(captures)


def _tree_point(point: Any) -> _TreePoint:
    """A plain ``(row, column)`` tuple from a tree-sitter ``Point``."""
    return (point[0], point[1])


def _symbol_from_match(captures: _Captures, kinds: Any) -> _Symbol | None:
    item = captures.get("item")
    if not item:
        return None
    kind = kinds.get(item[0].type)
    if kind is None:
        return None
    span = _symbol_extent_node(captures)
    start = span.start_point[0] + 1
    end = span.end_point[0] + 1
    return (kind, _capture_name(captures), start, end)


def _capture_name(captures: _Captures) -> str:
    name = captures.get("name")
    if not name:
        return ""
    text = name[0].text
    if text is None:  # only a tree parsed without its source; ours never is
        raise ValueError(f"_capture_name: {name[0].type!r} node carries no source text")
    return str(text.decode("utf-8", "replace"))
