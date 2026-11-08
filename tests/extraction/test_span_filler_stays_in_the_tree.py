"""The span filler is tree-only: it never reaches a chunk (issue #380).

A chunk is what gets embedded, filtered and searched. The filler is rendering
data for one class or module source view, so neither key may ride into chunk
metadata (``tree_flatten`` would otherwise merge every ``extra_metadata`` key)
or a chunk row — and chunk text and content hashes must not move, which is
what makes the ``chunk-trees/2`` upgrade cost one re-extract and no re-embed.
"""

from __future__ import annotations

from pathlib import Path

from pydocs_mcp.extraction.model import DocumentNode, flatten_to_chunks
from pydocs_mcp.extraction.model.span_filler import (
    SPAN_FILLER_COMPLETE_KEY,
    SPAN_FILLER_KEY,
    TREE_ONLY_METADATA_KEYS,
    span_filler_is_complete,
    span_filler_lines,
)
from pydocs_mcp.extraction.strategies.chunkers import AstPythonChunker
from pydocs_mcp.storage.sqlite import (
    _chunk_to_row,
    _deserialize_tree_from_json,
    _serialize_tree_to_json,
)

_SOURCE = '''\
"""Module doc."""

__all__ = ("K",)

# a comment


@decorated
class K:
    """Class doc."""

    def a(self):
        return 1

    # between
    @staticmethod
    def b():
        return 2
'''


def _tree(*, span_filler: bool = True) -> DocumentNode:
    chunker = AstPythonChunker(span_filler=span_filler)
    return chunker.build_tree("pkg/mod.py", _SOURCE, "pkg", Path("/tmp/fake_root"))


def _walk(node: DocumentNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def test_the_tree_does_carry_a_filler() -> None:
    """Guard: the assertions below are vacuous on a tree with no filler."""
    assert any(SPAN_FILLER_KEY in node.extra_metadata for node in _walk(_tree()))


def test_no_filler_key_reaches_chunk_metadata() -> None:
    for chunk in flatten_to_chunks(_tree(), "pkg"):
        assert TREE_ONLY_METADATA_KEYS.isdisjoint(chunk.metadata), chunk.metadata


def test_no_filler_key_reaches_a_chunk_row() -> None:
    for chunk in flatten_to_chunks(_tree(), "pkg"):
        assert TREE_ONLY_METADATA_KEYS.isdisjoint(_chunk_to_row(chunk))


def test_chunks_are_identical_with_and_without_the_filler() -> None:
    """Text, metadata and hash per chunk: the embed stage sees an empty diff."""
    assert flatten_to_chunks(_tree(), "pkg") == flatten_to_chunks(_tree(span_filler=False), "pkg")


def test_the_tree_only_keys_are_exactly_the_two_filler_keys() -> None:
    assert frozenset({SPAN_FILLER_KEY, SPAN_FILLER_COMPLETE_KEY}) == TREE_ONLY_METADATA_KEYS


def test_the_filler_survives_the_document_tree_json_round_trip() -> None:
    """JSON has no tuple: the stored filler comes back as lists, and the typed
    accessor reads it back to the very pairs the chunker built."""
    tree = _tree()
    restored = _deserialize_tree_from_json(_serialize_tree_to_json(tree))
    for before, after in zip(_walk(tree), _walk(restored), strict=True):
        assert span_filler_lines(after) == span_filler_lines(before)
        assert span_filler_is_complete(after) is span_filler_is_complete(before)
