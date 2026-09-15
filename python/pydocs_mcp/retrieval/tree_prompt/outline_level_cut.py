"""Level cut — fitting a ``get_symbol`` outline to a token budget by whole levels.

The sibling of :mod:`.tree_budget_fitter`, and deliberately NOT a change to it:
that fitter bounds the LLM-visible pageindex forest content-first (docs, then
BFS node pruning) for a model's context window, while this one bounds the
*outline text* ``get_symbol(depth="tree")`` returns for an agent (ADR 0023 (b)).
The two share :func:`count_tokens` and nothing else, so tuning one can never
move the other.

WHY a level cut rather than "keep the first N nodes": an outline is read for
structure. Stopping mid-tree leaves the reader unable to tell whether the
absent nodes are the rest of a level or the rest of the tree, whereas "levels L
of D shown" states exactly what is missing and the recovery pointers say where
to get it. Only when even one level overflows does the cut trim children per
parent, and then it marks each trim inline with ``and N more``.

Everything here is pure: the caller supplies the renderer's token count through
``measure``, so the cut never imports the renderer and is unit-testable against
a fake counter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydocs_mcp.extraction.model import DocumentNode


@dataclass(frozen=True, slots=True)
class OutlineRow:
    """One node the outline shows, at ``depth`` levels below the target.

    ``trimmed`` is how many of THIS node's children the per-parent trim dropped
    (0 when none were) — the count the renderer prints as ``and N more``.
    """

    node: DocumentNode
    depth: int
    trimmed: int = 0


@dataclass(frozen=True, slots=True)
class LevelCut:
    """One candidate outline shape: the rows it shows and what it left out.

    ``rows`` is pre-order, so a renderer walks it once and a caller's ``items[]``
    carry exactly the node set the text shows (contract §3.3). ``levels_shown``
    counts the levels PRESENT in ``rows`` (the target alone is 1 level) and
    ``total_levels`` the levels of the whole tree, which is the ``L of D`` the
    footer reports. ``elided`` is 0 exactly when nothing was cut.
    """

    rows: tuple[OutlineRow, ...]
    levels_shown: int
    total_levels: int
    elided: int


def cut_outline_to_budget(
    root: DocumentNode,
    *,
    max_tokens: int,
    measure: Callable[[LevelCut], int],
) -> LevelCut:
    """Deepest whole-level cut of ``root`` whose rendering fits ``max_tokens``.

    ``measure`` returns the token count of the text a candidate would render —
    footer and inline trim markers included, since those are what an over-budget
    response actually costs. ``max_tokens <= 0`` turns fitting off and returns
    the whole tree.

    Example::

        cut = cut_outline_to_budget(module_root, max_tokens=2048, measure=count)
        cut.levels_shown, cut.elided       # (2, 300)
    """
    shape = _shape(root)
    whole = _level_cut(root, levels=shape.levels, shape=shape)
    if max_tokens <= 0 or measure(whole) <= max_tokens:
        return whole
    for levels in range(shape.levels - 1, 1, -1):
        candidate = _level_cut(root, levels=levels, shape=shape)
        if measure(candidate) <= max_tokens:
            return candidate
    return _trim_children(root, shape=shape, max_tokens=max_tokens, measure=measure)


def elided_subtrees(cut: LevelCut) -> tuple[tuple[DocumentNode, int], ...]:
    """Shown nodes whose descendants the cut dropped, largest subtree first.

    Each one is a recovery-pointer target: asking for its outline at tree depth
    renders, with a fresh budget, exactly the subtree this response elided.
    Ranked by elided-descendant count, ties by document order.

    The target itself (``depth == 0``) is never a candidate — a pointer at it
    would re-issue the very call that produced the response (ADR 0023 (d)).
    """
    shown = {id(row.node) for row in cut.rows}
    ranked = [
        (dropped, order, row.node)
        for order, row in enumerate(cut.rows)
        if row.depth > 0 and (dropped := _elided_descendants(row.node, shown)) > 0
    ]
    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple((node, dropped) for dropped, _order, node in ranked)


@dataclass(frozen=True, slots=True)
class _TreeShape:
    """The whole tree's size, measured once and reused by every candidate."""

    nodes: int
    levels: int


def _shape(root: DocumentNode) -> _TreeShape:
    """Node count and level count of the whole tree, iteratively.

    Iterative for the same reason as
    ``DocumentNode.find_node_by_qualified_name``: a deep tree must not risk the
    recursion limit on a read path.
    """
    nodes = 0
    levels = 0
    stack: list[tuple[DocumentNode, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        nodes += 1
        levels = max(levels, depth)
        stack.extend((child, depth + 1) for child in node.children)
    return _TreeShape(nodes=nodes, levels=levels)


def _level_cut(root: DocumentNode, *, levels: int, shape: _TreeShape) -> LevelCut:
    """The candidate that keeps every node above depth ``levels``."""
    rows = _rows_to_depth(root, levels=levels)
    return LevelCut(
        rows=rows,
        levels_shown=levels,
        total_levels=shape.levels,
        elided=shape.nodes - len(rows),
    )


def _rows_to_depth(root: DocumentNode, *, levels: int) -> tuple[OutlineRow, ...]:
    """Pre-order rows down to (excluding) depth ``levels``."""
    rows: list[OutlineRow] = []
    stack: list[tuple[DocumentNode, int]] = [(root, 0)]
    while stack:
        node, depth = stack.pop()
        rows.append(OutlineRow(node=node, depth=depth))
        if depth + 1 < levels:
            stack.extend((child, depth + 1) for child in reversed(node.children))
    return tuple(rows)


def _trim_children(
    root: DocumentNode,
    *,
    shape: _TreeShape,
    max_tokens: int,
    measure: Callable[[LevelCut], int],
) -> LevelCut:
    """Target plus the longest prefix of its children that fits.

    Reached only when the target and its children together overflow the budget,
    so the only parent left to trim is the target. Binary search rather than a
    descending scan: a 300-member module costs ~9 measurements instead of 300,
    and each measurement renders the candidate.
    """
    low, high = 0, len(root.children)
    best = 0
    while low <= high:
        mid = (low + high) // 2
        if measure(_trimmed_cut(root, keep=mid, shape=shape)) <= max_tokens:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return _trimmed_cut(root, keep=best, shape=shape)


def _trimmed_cut(root: DocumentNode, *, keep: int, shape: _TreeShape) -> LevelCut:
    """The target plus its first ``keep`` children, the rest marked on the root."""
    listed = root.children[:keep]
    rows = (
        OutlineRow(node=root, depth=0, trimmed=len(root.children) - len(listed)),
        *(OutlineRow(node=child, depth=1) for child in listed),
    )
    return LevelCut(
        rows=rows,
        levels_shown=2 if listed else 1,
        total_levels=shape.levels,
        elided=shape.nodes - len(rows),
    )


def _elided_descendants(node: DocumentNode, shown: set[int]) -> int:
    """How many of ``node``'s descendants the cut left out."""
    dropped = 0
    stack: list[DocumentNode] = list(node.children)
    while stack:
        child = stack.pop()
        if id(child) not in shown:
            dropped += 1
        stack.extend(child.children)
    return dropped


__all__ = ("LevelCut", "OutlineRow", "cut_outline_to_budget", "elided_subtrees")
