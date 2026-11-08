"""The span filler: lines of a CLASS / MODULE span that no child node claims.

A class or module node stores only its DIRECT text (a class header, a dedented
module docstring); its children store their own slices. Every other line of the
span — docstring lines, ``__all__``, constants, comments, decorators — was
stored nowhere, so ``get_symbol(depth="source")`` rendered each one as a
``[lines a-b not in the index]`` marker (issue #380). ``AstPythonChunker``
now records those lines on the node, keyed by their 1-indexed file line:

- :data:`SPAN_FILLER_KEY` → ``((line, text), …)``, every non-empty line (a
  whitespace-only line included, so a string literal keeps its exact text),
  sorted by line.
- :data:`SPAN_FILLER_COMPLETE_KEY` → ``True`` when every non-empty unclaimed
  line of the span is in the filler, so a line still missing after placement is
  an empty one; ``False`` when the per-node cap dropped some.

Both keys are tree-only: they reach the ``document_trees`` JSON, never a chunk
row (``tree_flatten`` drops :data:`TREE_ONLY_METADATA_KEYS`). A node written
before the filler existed carries neither key, and the accessors answer for it
exactly as the renderer behaved before: no lines, not complete.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydocs_mcp.extraction.model.document_node import DocumentNode, NodeKind

SPAN_FILLER_KEY = "span_filler"
SPAN_FILLER_COMPLETE_KEY = "span_filler_complete"

# extra_metadata keys that describe the TREE and must never become chunk
# metadata: a chunk row is what gets embedded and filtered, and the filler is
# rendering data a class or module view needs, nothing a search should see.
TREE_ONLY_METADATA_KEYS: frozenset[str] = frozenset({SPAN_FILLER_KEY, SPAN_FILLER_COMPLETE_KEY})


# The claim model, shared by the producer (``python_span_filler``) and the
# renderer (``application/symbol_source_span``). The renderer blank-fills every
# unplaced line of a complete node, so the two MUST agree on which lines a child
# places; one copy here is what keeps a later edit from printing real code as
# empty lines.
#
# Nodes whose span the source view rebuilds from the tree — the only nodes that
# carry a filler.
SPAN_NODE_KINDS: frozenset[NodeKind] = frozenset({NodeKind.CLASS, NodeKind.MODULE})

# Kinds whose own ``text`` is a verbatim file slice starting at ``start_line``,
# so the renderer places them and their lines are claimed. MODULE is absent on
# purpose: its text is the DEDENTED docstring, which would render as code that
# is not in the file. CODE_EXAMPLE is absent because its span is stubbed to 1-1
# (the fence offset inside a docstring is opaque).
VERBATIM_TEXT_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.IMPORT_BLOCK}
)


def first_verbatim_child_line(node: DocumentNode) -> int | None:
    """Where ``node``'s own text must stop, or None when it owns its whole span.

    Only a child that places lines of its own, at a real line below the parent,
    can claim the region: a CODE_EXAMPLE's stubbed 1-1 span would otherwise clip
    a class to nothing. For a class this is its first method, so the class's
    direct text — its header — is ``start_line`` up to the line before it.
    """
    starts = [
        child.start_line
        for child in node.children
        if child.kind in VERBATIM_TEXT_KINDS and child.start_line > node.start_line
    ]
    return min(starts) if starts else None


def lines_claimed_by_verbatim_children(node: DocumentNode) -> set[int]:
    """Every line of ``node``'s span that one of its verbatim children places.

    Those lines are the child's business: a filler never holds them, and the
    filler of ``node`` therefore vouches for none of them either way.
    """
    return {
        number
        for child in node.children
        if child.kind in VERBATIM_TEXT_KINDS
        for number in range(child.start_line, child.end_line + 1)
    }


def span_filler_lines(node: DocumentNode) -> tuple[tuple[int, str], ...]:
    """``node``'s filler as ``(line, text)`` pairs; ``()`` for a node without one.

    Accepts both shapes the filler takes: the tuple of tuples the chunker
    builds and the list of lists a ``document_trees`` JSON round trip returns
    (JSON has no tuple). An entry that is not a ``(int, str)`` pair is skipped,
    so a malformed tree can never inject text at an invented line.

    Example::

        span_filler_lines(class_node)  # ((14, "    # a comment"),)
    """
    raw = node.extra_metadata.get(SPAN_FILLER_KEY, ())
    if not isinstance(raw, Sequence):
        return ()
    return tuple(pair for pair in (_as_pair(entry) for entry in raw) if pair is not None)


def _as_pair(entry: object) -> tuple[int, str] | None:
    """One filler entry as ``(line, text)``, or None when it is not that shape."""
    if not isinstance(entry, Sequence) or isinstance(entry, str) or len(entry) != 2:
        return None
    number, text = entry
    if isinstance(number, bool) or not isinstance(number, int) or not isinstance(text, str):
        return None
    return number, text


def span_filler_is_complete(node: DocumentNode) -> bool:
    """True only when ``node`` says its filler holds every unclaimed non-empty line."""
    return node.extra_metadata.get(SPAN_FILLER_COMPLETE_KEY) is True


def span_filler_vouched_through(node: DocumentNode) -> int:
    """The last line up to which ``node``'s filler holds every unclaimed non-empty line.

    ``end_line`` for a complete filler. A capped one kept the FIRST lines in
    file order, so it still vouches for everything up to its last kept line —
    the cap only takes away what lies past it. ``start_line - 1`` (nothing) for a
    node with no filler at all, which is every node of an old bundle.
    """
    if span_filler_is_complete(node):
        return node.end_line
    kept = span_filler_lines(node)
    return kept[-1][0] if kept else node.start_line - 1


__all__ = (
    "SPAN_FILLER_COMPLETE_KEY",
    "SPAN_FILLER_KEY",
    "SPAN_NODE_KINDS",
    "TREE_ONLY_METADATA_KEYS",
    "VERBATIM_TEXT_KINDS",
    "first_verbatim_child_line",
    "lines_claimed_by_verbatim_children",
    "span_filler_is_complete",
    "span_filler_lines",
    "span_filler_vouched_through",
)
