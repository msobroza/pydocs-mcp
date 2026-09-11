"""Domain model for the extraction subsystem — :class:`DocumentNode`,
:class:`NodeKind`, the tree-to-chunks flatten helper, the package tree
builder, and the one row-splitting rule behind every node span.
"""

from pydocs_mcp.extraction.model.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.extraction.model.line_rows import split_newline_rows
from pydocs_mcp.extraction.model.package_tree import build_package_tree
from pydocs_mcp.extraction.model.tree_flatten import flatten_to_chunks

__all__ = [
    "STRUCTURAL_ONLY_KINDS",
    "DocumentNode",
    "NodeKind",
    "build_package_tree",
    "flatten_to_chunks",
    "split_newline_rows",
]
