"""The one line-splitting rule behind every 1-indexed row this package reports.

Import-free on purpose: the tree-sitter chunker, the analyzers' attribution
index and the decision-marker miner all count rows with it, and none of them
should have to import another's module for a string function.
"""

from __future__ import annotations


def split_newline_rows(text: str) -> list[str]:
    """``text`` split on ``\\n`` only — the rows tree-sitter reports, and the rows
    every chunker joins chunk text with (issue #246 item 4).

    ``str.splitlines()`` also breaks on ``\\r`` alone, ``\\x0b``, ``\\x0c``,
    ``\\x1c``-``\\x1e``, ``\\x85``, ``\\u2028`` and ``\\u2029``; after any of
    those its list ran one element ahead of the rows, so chunk text was sliced
    a line early and a decision marker's locator landed a line late. Here such
    a character stays part of its row.

    Byte-identical to ``splitlines()`` for LF and CRLF input — the shapes that
    carry real chunk and evidence hashes: the empty element a final ``\\n``
    produces is dropped, and exactly one trailing ``\\r`` is stripped per row,
    which is what ``splitlines()`` did with a CRLF (raw-content module nodes
    keep the file's CRLF bytes, so the miner needs that rule too). A drift
    here would re-embed every project for nothing;
    ``tests/extraction/test_multilang_line_rows.py`` pins the identity by
    example and on node hashes recorded before the rule changed. Example::

        split_newline_rows("a\\r\\nb\\x0cc\\n")  # ["a", "b\\x0cc"]
    """
    rows = text.split("\n")
    if rows[-1] == "":
        rows.pop()
    return [row.removesuffix("\r") for row in rows]
