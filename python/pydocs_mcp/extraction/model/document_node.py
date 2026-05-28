"""DocumentNode value object + NodeKind enum (spec §4.2-§4.4).

``DocumentNode`` is the uniform tree representation emitted by every
:class:`~pydocs_mcp.extraction.protocols.Chunker`. Flat chunks (for FTS)
come from ``tree_flatten.flatten_to_chunks`` walking the tree; tree
structure persists separately in the ``document_trees`` table for
``get_document_tree`` / ``get_package_tree``.

``qualified_name`` is a FIRST-CLASS field (not stored under
``extra_metadata``). It equals ``node_id`` for code nodes (dotted path like
``"requests.adapters.HTTPAdapter"``) and for structural scaffolding;
synthetic IDs for markdown/notebook nodes may differ from
``qualified_name``.

Direct-text rule (spec §4.1.1): each node's ``.text`` contains ONLY prose
between this node's start and its first child's start. ``STRUCTURAL_ONLY_KINDS``
(PACKAGE, SUBPACKAGE) never carry text — they're path scaffolding produced
by ``build_package_tree``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class NodeKind(StrEnum):
    PACKAGE = "package"
    SUBPACKAGE = "subpackage"
    MODULE = "module"
    IMPORT_BLOCK = "import_block"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    MARKDOWN_HEADING = "markdown_heading"
    NOTEBOOK_MARKDOWN_CELL = "notebook_markdown_cell"
    NOTEBOOK_CODE_CELL = "notebook_code_cell"
    CODE_EXAMPLE = "code_example"


# Pure path scaffolding — never persisted in document_trees, never flattened
# to Chunks. Only appear in the arborescence assembled by build_package_tree.
STRUCTURAL_ONLY_KINDS: frozenset[NodeKind] = frozenset(
    {
        NodeKind.PACKAGE,
        NodeKind.SUBPACKAGE,
    }
)


@dataclass(frozen=True, slots=True)
class DocumentNode:
    """One node in the extracted document tree (spec §4.3).

    Frozen + slotted: immutable value semantics + typo guard on attribute
    names. ``children`` is a tuple (not list) so the whole tree is deeply
    immutable and safely shareable across async tasks.
    """

    node_id: str
    qualified_name: str
    title: str
    kind: NodeKind
    source_path: str
    start_line: int
    end_line: int
    text: str
    content_hash: str
    summary: str = ""
    extra_metadata: Mapping[str, Any] = field(default_factory=dict)
    parent_id: str | None = None
    # Self-reference is stringified by `from __future__ import annotations`;
    # no explicit quotes needed.
    children: tuple[DocumentNode, ...] = ()

    def to_pageindex_json(self) -> dict[str, Any]:
        """Serialize as PageIndex-style JSON dict (spec §4.3).

        Recursive over children. Field renames per spec: ``start_line`` →
        ``start_index``, ``end_line`` → ``end_index``. ``kind`` is emitted
        as its string value (``NodeKind.value``) so consumers don't need to
        know the enum. ``nodes`` holds recursive children. Used by
        ``LookupService`` for the ``lookup`` MCP tool response shape.
        """
        return {
            "title": self.title,
            "node_id": self.node_id,
            "kind": self.kind.value,
            "source_path": self.source_path,
            "start_index": self.start_line,
            "end_index": self.end_line,
            "summary": self.summary,
            "nodes": [child.to_pageindex_json() for child in self.children],
        }

    def find_node_by_qualified_name(self, target: str) -> DocumentNode | None:
        """BFS search for first node whose qualified_name matches.

        Iterative + ``collections.deque`` for O(1) popleft — avoids both
        Python's recursion limit (deep subpackage chains) and the O(n²)
        behavior of ``list.pop(0)``. Returns the shallowest match
        (level-order); siblings tie-break left-to-right.
        """
        from collections import deque

        if self.qualified_name == target:
            return self
        queue: deque[DocumentNode] = deque(self.children)
        while queue:
            node = queue.popleft()
            if node.qualified_name == target:
                return node
            queue.extend(node.children)
        return None
