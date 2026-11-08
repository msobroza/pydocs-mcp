"""Record each Python CLASS / MODULE node's unclaimed lines as its span filler (issue #380).

A post-pass over the tree :class:`AstPythonChunker` already built, rather than
a change inside each node builder: the builders stay byte-identical, so
``span_filler: false`` is HEAD's tree by construction, and the filler can only
ADD two ``extra_metadata`` keys — it never moves a span, a title, a text or a
content hash (so no def/class line shifts and nothing re-embeds).

What a node CLAIMS is exactly what the source-view renderer places for it
(``application/symbol_source_span.py``) — both read the one claim model in
``extraction/model/span_filler.py``: a class's direct text (its header,
``start`` up to the line before its first method), and every child whose text
is a verbatim slice (class, function, method, import block). A CODE_EXAMPLE
child claims nothing — its span is stubbed to 1-1. The module's own text is the
DEDENTED docstring, which the renderer never places, so the docstring's file
lines are filler like any other unclaimed line.

Every NON-EMPTY unclaimed line is kept, a whitespace-only one included: the
renderer fills each vouched-for line it still lacks with ``""``, so only a line
that really is empty may be left out, or a string literal's indented blank
line would render without its whitespace.
"""

from __future__ import annotations

from dataclasses import replace

from pydocs_mcp.extraction.model import DocumentNode, NodeKind
from pydocs_mcp.extraction.model.span_filler import (
    SPAN_FILLER_COMPLETE_KEY,
    SPAN_FILLER_KEY,
    SPAN_NODE_KINDS,
    first_verbatim_child_line,
    lines_claimed_by_verbatim_children,
)

# Characters ``str.splitlines()`` breaks on and the Python tokenizer does not.
# After one, the chunker's line list runs a row ahead of every ``ast`` line
# number, so a filler would place text at the wrong number. Such a file keeps
# the pre-filler tree (and its gap markers): honest beats gap-free.
_SPLITLINES_ONLY_BREAKS = frozenset("\x0b\x0c\x1c\x1d\x1e\x85  ")


def lines_match_ast_rows(content: str) -> bool:
    """True when ``content.splitlines()`` numbers lines exactly as ``ast`` does."""
    return _SPLITLINES_ONLY_BREAKS.isdisjoint(content)


def with_span_filler(root: DocumentNode, lines: list[str], max_lines: int) -> DocumentNode:
    """``root`` with every CLASS / MODULE node carrying its filler and completeness.

    ``lines`` is the list the chunker sliced every node's text from
    (``content.splitlines()``), so a filler line is the very text the file holds
    at that number. At most ``max_lines`` lines are kept per node, the first
    ones in file order; a node whose filler was cut is marked incomplete.
    """
    children = tuple(with_span_filler(child, lines, max_lines) for child in root.children)
    if root.kind not in SPAN_NODE_KINDS:
        return replace(root, children=children) if children != root.children else root
    filler = _unclaimed_non_empty_lines(root, lines)
    metadata = {
        **root.extra_metadata,
        SPAN_FILLER_KEY: filler[:max_lines],
        SPAN_FILLER_COMPLETE_KEY: len(filler) <= max_lines,
    }
    return replace(root, extra_metadata=metadata, children=children)


def _unclaimed_non_empty_lines(node: DocumentNode, lines: list[str]) -> tuple[tuple[int, str], ...]:
    """Every non-empty line of ``node``'s span that neither it nor a child places."""
    claimed = _claimed_line_numbers(node)
    last = min(node.end_line, len(lines))
    return tuple(
        (number, lines[number - 1])
        for number in range(max(node.start_line, 1), last + 1)
        if number not in claimed and lines[number - 1] != ""
    )


def _claimed_line_numbers(node: DocumentNode) -> set[int]:
    """The lines the renderer places for ``node`` or one of its children."""
    claimed = lines_claimed_by_verbatim_children(node)
    if node.kind is NodeKind.CLASS:
        # The class header: the same ceiling the renderer clips the header to.
        ceiling = first_verbatim_child_line(node)
        header_end = node.end_line if ceiling is None else ceiling - 1
        claimed.update(range(node.start_line, header_end + 1))
    return claimed


__all__ = ("lines_match_ast_rows", "with_span_filler")
