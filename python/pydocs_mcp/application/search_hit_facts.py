"""Search-hit facts — what a rendered search hit states beyond its chunk row.

A chunk row carries its qualified name, module and span, but not which kind of
node it stands for, nor that node's children. The ``Members (N): …`` line on a
class or module hit (issue #393, ADR 0023 (j)) needs both, so they are read
from the hit's document tree — each tree at most once per search response —
and handed to the budgeted chunk renderer as one :class:`SearchHitFacts` per
hit that has something to say.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pydocs_mcp.application.mcp_errors import ServiceUnavailableError
from pydocs_mcp.application.symbol_views import members_line
from pydocs_mcp.extraction.model import NodeKind
from pydocs_mcp.models import ChunkFilterField

if TYPE_CHECKING:
    from pydocs_mcp.application.multi_project_search import ProjectServices
    from pydocs_mcp.application.protocols import TreeNavigator
    from pydocs_mcp.extraction.model import DocumentNode
    from pydocs_mcp.models import Chunk

# The node kinds whose hits name their children (ADR 0023 (j)): a container an
# agent picks a member out of. Every other kind is already a member. The node's
# kind decides, whatever the language: a markdown, notebook or config file's
# root is a module too, and names its sections the way its card does.
_MEMBERS_LINE_KINDS: frozenset[NodeKind] = frozenset({NodeKind.CLASS, NodeKind.MODULE})


@dataclass(frozen=True, slots=True)
class SearchHitFacts:
    """The facts one rendered search hit carries beyond its chunk row.

    ``members_line`` is the symbol card's own members line for the hit's node
    — the same text ``get_symbol(depth="summary")`` renders, so the search hit
    and the card cannot disagree about what the target contains.
    """

    members_line: str


class SearchResponseTrees:
    """The document trees one search response reads — each at most once.

    Keyed by the owning bundle's db path plus ``(package, module)``: within one
    response a bundle reads one branch (the one it pins), so a key names one
    tree however many times its owner was bound to that branch along the way.
    """

    def __init__(self) -> None:
        self._read: dict[tuple[Path, str, str], DocumentNode | None] = {}

    async def tree(self, owner: ProjectServices, package: str, module: str) -> DocumentNode | None:
        """``(package, module)``'s tree in ``owner``'s bundle, read on first ask only."""
        key = (owner.project.db_path, package, module)
        if key not in self._read:
            self._read[key] = await _read_tree(owner, package, module)
        return self._read[key]


async def _read_tree(owner: ProjectServices, package: str, module: str) -> DocumentNode | None:
    """One read through ``owner``'s tree navigator; ``None`` where there is no tree.

    ``getattr`` guard: ``owner.lookup`` is typed :class:`LookupService` (which
    always carries ``tree_svc``), but test doubles duck-type the lookup seam,
    and what a search row reads from a tree is advisory — a navigator-less
    lookup degrades to no tree instead of demanding the full surface.
    """
    navigator: TreeNavigator | None = getattr(owner.lookup, "tree_svc", None)
    if navigator is None:
        return None
    try:
        return await navigator.get_tree(package, module)
    except ServiceUnavailableError:
        # NullTreeService deployment (no tree index): degrade to no tree
        # rather than failing the search over an advisory read.
        return None


async def members_line_facts(
    hits: Sequence[tuple[ProjectServices, Chunk]],
    trees: SearchResponseTrees,
    *,
    child_cap: int,
) -> dict[int, SearchHitFacts]:
    """The Members line of every class or module hit, keyed by the hit's rank.

    ``hits`` pairs each ranked chunk with the bundle it came from, bound to
    the branch that bundle searched. ``child_cap`` is ``symbol_card.child_cap``,
    so a hit names exactly the children its card names.
    """
    facts: dict[int, SearchHitFacts] = {}
    for rank, (owner, hit) in enumerate(hits):
        node = await _hit_node(owner, hit, trees)
        if node is not None and node.kind in _MEMBERS_LINE_KINDS:
            facts[rank] = SearchHitFacts(members_line=members_line(node, child_cap=child_cap))
    return facts


async def _hit_node(
    owner: ProjectServices, hit: Chunk, trees: SearchResponseTrees
) -> DocumentNode | None:
    """The tree node a chunk hit stands for, or ``None`` when no tree holds it."""
    metadata = hit.metadata
    qualified_name = str(metadata.get("qualified_name") or "")
    module = str(metadata.get(ChunkFilterField.MODULE.value) or "")
    if not (qualified_name and module):
        return None
    package = str(metadata.get(ChunkFilterField.PACKAGE.value) or "")
    tree = await trees.tree(owner, package, module)
    return tree.find_node_by_qualified_name(qualified_name) if tree is not None else None


__all__ = ("SearchHitFacts", "SearchResponseTrees", "members_line_facts")
