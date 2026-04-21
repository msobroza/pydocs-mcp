"""Flatten DocumentNode tree -> flat Chunk rows for FTS.

Direct-text rule (spec §4.1.1): emit one Chunk per node IFF the node has
non-empty text AND is not in STRUCTURAL_ONLY_KINDS (PACKAGE, SUBPACKAGE).
Children recursively produce their own chunks.

Each emitted Chunk carries (spec §4.4):
- package, title, kind, origin, source_path, content_hash (standard metadata)
- module (from node.extra_metadata["module"] if present, else node.qualified_name)
- qualified_name (copied from node.qualified_name — first-class field, mirrored
  into metadata so downstream consumers / filters / sub-PR #5b reference graph
  don't need to peek into extra_metadata)
- any other keys from node.extra_metadata (merged WITHOUT overwriting required keys)

CODE_EXAMPLE nodes inherit their parent's origin (spec §4.2): a code example
under a FUNCTION is PYTHON_DEF; under a MARKDOWN_HEADING it's MARKDOWN_SECTION.
"""
from __future__ import annotations

from pydocs_mcp.extraction.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.models import Chunk, ChunkFilterField, ChunkOrigin

# NodeKind -> ChunkOrigin mapping (spec §4.2). CODE_EXAMPLE is deliberately
# absent — it inherits the parent node's origin at emission time.
_KIND_TO_ORIGIN: dict[NodeKind, ChunkOrigin] = {
    NodeKind.MODULE:                 ChunkOrigin.PYTHON_DEF,
    NodeKind.IMPORT_BLOCK:           ChunkOrigin.PYTHON_DEF,
    NodeKind.CLASS:                  ChunkOrigin.PYTHON_DEF,
    NodeKind.FUNCTION:               ChunkOrigin.PYTHON_DEF,
    NodeKind.METHOD:                 ChunkOrigin.PYTHON_DEF,
    NodeKind.MARKDOWN_HEADING:       ChunkOrigin.MARKDOWN_SECTION,
    NodeKind.NOTEBOOK_MARKDOWN_CELL: ChunkOrigin.NOTEBOOK_MARKDOWN_CELL,
    NodeKind.NOTEBOOK_CODE_CELL:     ChunkOrigin.NOTEBOOK_CODE_CELL,
}


def flatten_to_chunks(node: DocumentNode, package: str) -> list[Chunk]:
    """DocumentNode tree -> list of FTS-ready Chunks (direct-text rule, spec §4.1.1)."""
    chunks: list[Chunk] = []
    _visit(node, package, chunks, parent_origin=None)
    return chunks


def _visit(
    node: DocumentNode,
    package: str,
    acc: list[Chunk],
    parent_origin: ChunkOrigin | None,
) -> None:
    origin = _origin_for_node(node, parent_origin)
    if _should_emit(node):
        acc.append(_node_to_chunk(node, package, origin))
    # Children inherit this node's resolved origin — matters for CODE_EXAMPLE,
    # which is always nested under a MODULE / FUNCTION / CLASS / METHOD /
    # MARKDOWN_HEADING / notebook cell.
    for child in node.children:
        _visit(child, package, acc, parent_origin=origin)


def _should_emit(node: DocumentNode) -> bool:
    return (
        node.kind not in STRUCTURAL_ONLY_KINDS
        and bool(node.text.strip())
    )


def _origin_for_node(
    node: DocumentNode, parent_origin: ChunkOrigin | None,
) -> ChunkOrigin | None:
    if node.kind == NodeKind.CODE_EXAMPLE:
        return parent_origin
    return _KIND_TO_ORIGIN.get(node.kind)


def _node_to_chunk(
    node: DocumentNode, package: str, origin: ChunkOrigin | None,
) -> Chunk:
    metadata: dict[str, object] = {
        ChunkFilterField.PACKAGE.value:      package,
        ChunkFilterField.TITLE.value:        node.title,
        ChunkFilterField.SOURCE_PATH.value:  node.source_path,
        ChunkFilterField.CONTENT_HASH.value: node.content_hash,
        # ``kind`` lives under a plain string key (spec §4.4 table — extra_metadata
        # column); there is no ChunkFilterField.KIND enum member. Consumers that
        # need to filter by kind key on the literal string "kind".
        "kind": node.kind.value,
        # First-class mirror: Chunk consumers (sub-PR #5b ReferenceExtractionStage,
        # retrieval filters) can select on qualified_name without peeking into
        # extra_metadata or keeping a parallel tree in memory.
        "qualified_name": node.qualified_name,
    }
    if origin is not None:
        metadata[ChunkFilterField.ORIGIN.value] = origin.value
    # Module key — prefer explicit extra_metadata["module"] (strategies set this
    # for nested members so FTS filters still group under the dotted module
    # path), fall back to qualified_name.
    module = (
        node.extra_metadata.get("module") if node.extra_metadata else None
    )
    metadata[ChunkFilterField.MODULE.value] = module or node.qualified_name
    # Merge remaining extra_metadata keys without clobbering required keys.
    for k, v in (node.extra_metadata or {}).items():
        metadata.setdefault(k, v)
    return Chunk(text=node.text, metadata=metadata)
