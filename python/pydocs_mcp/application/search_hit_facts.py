"""Search-hit facts — what a rendered search hit states beyond its chunk row.

A chunk row carries its qualified name, module and span, but not which kind of
node it stands for, nor that node's children. The ``Members (N): …`` line on a
class or module hit (issue #393, ADR 0023 (j)) needs both, so they are read
from the hit's document tree — each tree at most once per search response —
and handed to the budgeted chunk renderer as one :class:`SearchHitFacts` per
hit that has something to say.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SearchHitFacts:
    """The facts one rendered search hit carries beyond its chunk row.

    ``members_line`` is the symbol card's own members line for the hit's node
    — the same text ``get_symbol(depth="summary")`` renders, so the search hit
    and the card cannot disagree about what the target contains.
    """

    members_line: str


__all__ = ("SearchHitFacts",)
