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

The span filler (issue #380) closes the gaps on a re-indexed bundle: a CLASS
or MODULE node records the non-empty lines no child claims, so every line it
vouches for that is still unplaced is an empty one and renders as such. A
bundle written before the filler carries no filler key and renders exactly as
it did; a line the renderer cannot vouch for (past a capped filler's last kept
line, or anywhere in a span whose child text it had to skip) keeps its marker.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.extraction.model.span_filler import (
    SPAN_NODE_KINDS,
    VERBATIM_TEXT_KINDS,
    first_verbatim_child_line,
    lines_claimed_by_verbatim_children,
    span_filler_lines,
    span_filler_vouched_through,
)

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode

log = logging.getLogger(__name__)

# Targets whose chunk text is a fragment of their span, so the span is rebuilt.
# Every other kind's chunk already IS its span (a def slice, a heading body).
# Both sets are the span filler's claim model (``extraction/model/span_filler``):
# the chunker decides what to record from the very rule this renderer places by.
SPAN_SOURCE_KINDS: frozenset[NodeKind] = SPAN_NODE_KINDS
_VERBATIM_TEXT_KINDS: frozenset[NodeKind] = VERBATIM_TEXT_KINDS


@dataclass(frozen=True, slots=True)
class SpanRun:
    """One maximal stretch of the span that is either all indexed or all missing."""

    covered: bool
    first: int
    last: int

    @property
    def length(self) -> int:
        return self.last - self.first + 1


def _walk(node: DocumentNode) -> Iterable[DocumentNode]:
    """The node and every descendant, parents first."""
    yield node
    for child in node.children:
        yield from _walk(child)


def _class_header_lines(node: DocumentNode, lines: list[str]) -> list[str]:
    """A class header, clipped so it never reaches its first child's line.

    Its own text is the header slice; a stale or over-long one would silently
    swallow the gaps between its methods.
    """
    ceiling = first_verbatim_child_line(node)
    end = node.end_line if ceiling is None else ceiling - 1
    return lines[: end - node.start_line + 1]


def _own_lines(node: DocumentNode) -> list[str] | None:
    """The lines ``node`` contributes at its own numbers, or None for none at all.

    A def's text IS its whole slice — nested defs re-place their own lines
    verbatim — so it is taken whole or, when it overruns its span and can no
    longer be trusted to start at ``start_line``, not at all.
    """
    if node.kind not in _VERBATIM_TEXT_KINDS:
        return None
    lines = (node.text or "").splitlines()
    if node.kind is NodeKind.CLASS:
        return _class_header_lines(node, lines)
    if len(lines) <= node.end_line - node.start_line + 1:
        return lines
    _log_skip(node, len(lines))
    return None


def _log_skip(node: DocumentNode, line_count: int) -> None:
    """One debug line per node whose text cannot be trusted to be its span."""
    log.debug(
        json.dumps(
            {
                "event": "symbol_source_span_skip",
                "node": node.qualified_name,
                "lines": line_count,
                "span_lines": node.end_line - node.start_line + 1,
            }
        )
    )


def _place(placed: dict[int, str], start: int, lines: list[str], *, within: range) -> None:
    """Write ``lines`` at their file numbers, dropping anything outside ``within``."""
    for offset, text in enumerate(lines):
        number = start + offset
        if number in within:
            placed[number] = text


def indexed_lines_by_number(node: DocumentNode) -> dict[int, str]:
    """Every line of ``node``'s span the index actually stores, keyed by file line.

    Descendants place their verbatim text first; then every CLASS / MODULE node
    of the span places its filler (a module's view holds its classes' comments
    and decorators too); then each line still unplaced that every filler around
    it vouches for is an empty line, and is placed as ``""``.

    Example::

        indexed_lines_by_number(class_node)  # {22: "class Foo:", 23: "    pass"}
    """
    placed: dict[int, str] = {}
    own_span = range(node.start_line, node.end_line + 1)
    every_claim_placed = True
    for descendant in _walk(node):
        lines = _own_lines(descendant) if descendant.source_path == node.source_path else None
        if lines is not None:
            _place(placed, descendant.start_line, lines, within=own_span)
        elif descendant.kind in _VERBATIM_TEXT_KINDS:
            every_claim_placed = False
    _place_span_filler(placed, node, within=own_span)
    if every_claim_placed:
        _place_vouched_empty_lines(placed, node, within=own_span)
    return placed


def _unvouched_lines(span_node: DocumentNode) -> set[int]:
    """The lines ``span_node`` owns that its filler cannot vouch for."""
    vouched_through = span_filler_vouched_through(span_node)
    beyond = range(vouched_through + 1, span_node.end_line + 1)
    if not beyond:
        return set()
    return set(beyond) - lines_claimed_by_verbatim_children(span_node)


def _span_nodes_on_path(node: DocumentNode) -> Iterable[DocumentNode]:
    """``node`` and each CLASS / MODULE below it that renders lines of the same file."""
    return (
        each
        for each in _walk(node)
        if each.kind in SPAN_SOURCE_KINDS and each.source_path == node.source_path
    )


def _place_span_filler(placed: dict[int, str], node: DocumentNode, *, within: range) -> None:
    """Place every span node's filler at its line, never over a child's own text."""
    for span_node in _span_nodes_on_path(node):
        for number, text in span_filler_lines(span_node):
            if number in within:
                placed.setdefault(number, text)


def _place_vouched_empty_lines(
    placed: dict[int, str], node: DocumentNode, *, within: range
) -> None:
    """Place ``""`` on each unplaced line that every enclosing filler vouches for.

    A filler vouches for a line when it holds every unclaimed non-empty line up
    to it (``span_filler_vouched_through``), so an unplaced line there is empty.
    A line past a capped filler's last kept line, or inside a node with no
    filler at all (an old bundle), is not vouched for and keeps its marker.
    Each span node answers only for the lines it owns: a line one of its
    verbatim children places (a class inside a module) is that child's.
    """
    unvouched: set[int] = set()
    for span_node in _span_nodes_on_path(node):
        unvouched.update(_unvouched_lines(span_node))
    for number in within:
        if number not in unvouched:
            placed.setdefault(number, "")


def span_runs(indexed: Mapping[int, str], start: int, end: int) -> list[SpanRun]:
    """Tile ``start..end`` into maximal all-indexed / all-missing runs."""
    runs: list[SpanRun] = []
    for number in range(start, end + 1):
        covered = number in indexed
        if runs and runs[-1].covered == covered:
            runs[-1] = replace(runs[-1], last=number)
            continue
        runs.append(SpanRun(covered, number, number))
    return runs


def window_end(start: int, end: int, max_lines: int) -> int:
    """Last line of the span WINDOW — the cap counts gap lines too (spec §2.3)."""
    return min(end, start + max_lines - 1)


def _gap_marker(run: SpanRun) -> str:
    lines = f"line {run.first}" if run.length == 1 else f"lines {run.first}-{run.last}"
    return f"[{lines} not in the index]"


def _gap_note(gaps: int, path: str, runs: Sequence[SpanRun]) -> str:
    """The one closing line that names the whole span a reader can go read."""
    return (
        f"[{gaps} lines of this span are not in the index — read {path or 'the source file'} "
        f"lines {runs[0].first}-{runs[-1].last} for the full text]"
    )


def _render_run(run: SpanRun, indexed: Mapping[int, str]) -> list[str]:
    """One fence for an indexed run, one marker line for a missing one."""
    if not run.covered:
        return [_gap_marker(run)]
    return ["```python", *(indexed[n] for n in range(run.first, run.last + 1)), "```"]


def render_span(
    runs: Sequence[SpanRun],
    indexed: Mapping[int, str],
    path: str,
) -> tuple[str, int]:
    """Render the tiled span as fences plus markers; return it and the gap count."""
    out: list[str] = []
    gaps = sum(run.length for run in runs if not run.covered)
    for run in runs:
        out.extend(_render_run(run, indexed))
    if gaps:
        out.append(_gap_note(gaps, path, runs))
    return "\n".join(out) + "\n", gaps
