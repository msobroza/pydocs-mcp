"""Rebuild a CLASS or MODULE span from indexed node text (spec §2).

A chunk carries only a node's DIRECT text: a class chunk stops before its first
method, a module chunk is the dedented docstring. Rendering that as
``get_symbol(depth="source")`` while ``items[0]`` reports the whole node span
tells the reader a class is eight lines long when it is sixty-five.

This module stitches the span back together from the document tree, which
stores raw file lines at their true numbers. Every line the index actually
holds is rendered verbatim inside a ``python`` fence; every line it does not
hold becomes a marker OUTSIDE the fences, so a fenced line is never confused
with a comment the file does not contain. Nothing here reads the filesystem —
the index serves the index pass, the file tools serve live disk
(``docs/tool-contracts.md`` §4.2).

Gap markers are body text, never a truncation: ``meta.truncated`` keeps its
frozen §2.1 meaning of "cut by a limit", which here is only the line cap.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.model import NodeKind

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

log = logging.getLogger(__name__)

# Targets whose chunk text is a fragment of their span, so the span is rebuilt.
# Every other kind's chunk already IS its span (a def slice, a heading body).
SPAN_SOURCE_KINDS: frozenset[NodeKind] = frozenset({NodeKind.CLASS, NodeKind.MODULE})

# Kinds whose own ``text`` is a verbatim file slice starting at ``start_line``.
# MODULE is absent on purpose: its text is the DEDENTED docstring, which would
# render as code that is not in the file. CODE_EXAMPLE is absent because its
# span is stubbed to 1-1 (the fence offset inside a docstring is opaque).
_VERBATIM_TEXT_KINDS: frozenset[NodeKind] = frozenset(
    {NodeKind.CLASS, NodeKind.FUNCTION, NodeKind.METHOD, NodeKind.IMPORT_BLOCK}
)


def _walk(node: DocumentNode) -> Iterable[DocumentNode]:
    """The node and every descendant, parents first."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _first_child_line(node: DocumentNode) -> int | None:
    """Where ``node``'s own text must stop, or None when it owns its whole span.

    Only a child that will place lines of its own, at a real line below the
    parent, can claim the region: a CODE_EXAMPLE's stubbed 1-1 span would
    otherwise clip a class to nothing.
    """
    starts = [
        child.start_line
        for child in node.children
        if child.kind in _VERBATIM_TEXT_KINDS and child.start_line > node.start_line
    ]
    return min(starts) if starts else None


def _line_budget(node: DocumentNode) -> int:
    """How many lines of ``node.text`` may be placed.

    Only a CLASS is clipped at its first child: its own text is the header
    slice, and a stale or over-long one would silently swallow the gaps
    between its methods. A def's text IS its whole slice — nested defs are
    re-placed verbatim by their own nodes — so it gets the full span.
    """
    if node.kind is not NodeKind.CLASS:
        return node.end_line - node.start_line + 1
    ceiling = _first_child_line(node)
    end = node.end_line if ceiling is None else ceiling - 1
    return end - node.start_line + 1


def _place(placed: dict[int, str], node: DocumentNode, *, lo: int, hi: int) -> None:
    """Write ``node``'s own lines at their file numbers, clamped to ``lo..hi``."""
    lines = (node.text or "").splitlines()
    budget = _line_budget(node)
    if len(lines) > budget and node.kind is not NodeKind.CLASS:
        _log_skip(node, len(lines), budget)
        return
    for offset, text in enumerate(lines[:budget]):
        number = node.start_line + offset
        if lo <= number <= hi:
            placed[number] = text


def _log_skip(node: DocumentNode, line_count: int, budget: int) -> None:
    """One debug line per node whose text cannot be trusted to be its span."""
    log.debug(
        json.dumps(
            {
                "event": "symbol_source_span_skip",
                "node": node.qualified_name,
                "lines": line_count,
                "span_lines": budget,
            }
        )
    )


def indexed_lines_by_number(node: DocumentNode) -> dict[int, str]:
    """Every line of ``node``'s span the index actually stores, keyed by file line.

    Example::

        indexed_lines_by_number(class_node)  # {22: "class Foo:", 23: "    pass"}
    """
    placed: dict[int, str] = {}
    for descendant in _walk(node):
        if descendant.kind not in _VERBATIM_TEXT_KINDS:
            continue
        if descendant.source_path != node.source_path:
            continue
        _place(placed, descendant, lo=node.start_line, hi=node.end_line)
    return placed


def span_runs(indexed: Mapping[int, str], start: int, end: int) -> list[tuple[bool, int, int]]:
    """Tile ``start..end`` into maximal ``(covered, first, last)`` runs."""
    runs: list[tuple[bool, int, int]] = []
    for number in range(start, end + 1):
        covered = number in indexed
        if runs and runs[-1][0] == covered:
            runs[-1] = (covered, runs[-1][1], number)
            continue
        runs.append((covered, number, number))
    return runs


def window_end(start: int, end: int, max_lines: int) -> int:
    """Last line of the span WINDOW — the cap counts gap lines too (spec §2.3)."""
    return min(end, start + max_lines - 1)


def _gap_marker(first: int, last: int) -> str:
    span = f"line {first}" if first == last else f"lines {first}-{last}"
    return f"[{span} not in the index]"


def _gap_note(gaps: int, path: str, first: int, last: int) -> str:
    """The one closing line that names the whole span a reader can go read."""
    return (
        f"[{gaps} lines of this span are not in the index — "
        f"read {path or 'the source file'} lines {first}-{last} for the full text]"
    )


def render_span(
    runs: Sequence[tuple[bool, int, int]],
    indexed: Mapping[int, str],
    path: str,
    target: str,
) -> tuple[str, int]:
    """Render the tiled span as fences plus markers; return it and the gap count."""
    header = f"# Source — `{target}`" + (f"  ·  {path}" if path else "")
    out: list[str] = [header, ""]
    gaps = 0
    for covered, first, last in runs:
        if not covered:
            gaps += last - first + 1
            out.append(_gap_marker(first, last))
            continue
        out.extend(["```python", *(indexed[n] for n in range(first, last + 1)), "```"])
    if gaps:
        out.append(_gap_note(gaps, path, runs[0][1], runs[-1][2]))
    return "\n".join(out) + "\n", gaps
