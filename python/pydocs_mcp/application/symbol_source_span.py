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
from dataclasses import dataclass, replace
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


def _class_header_lines(node: DocumentNode, lines: list[str]) -> list[str]:
    """A class header, clipped so it never reaches its first child's line.

    Its own text is the header slice; a stale or over-long one would silently
    swallow the gaps between its methods.
    """
    ceiling = _first_child_line(node)
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

    Example::

        indexed_lines_by_number(class_node)  # {22: "class Foo:", 23: "    pass"}
    """
    placed: dict[int, str] = {}
    own_span = range(node.start_line, node.end_line + 1)
    for descendant in _walk(node):
        lines = _own_lines(descendant) if descendant.source_path == node.source_path else None
        if lines is not None:
            _place(placed, descendant.start_line, lines, within=own_span)
    return placed


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
