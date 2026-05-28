"""Domain model for the extraction subsystem — :class:`DocumentNode`,
:class:`NodeKind`, the tree-to-chunks flatten helper, and the package
tree builder.
"""

from pydocs_mcp.extraction.model.document_node import (
    STRUCTURAL_ONLY_KINDS,
    DocumentNode,
    NodeKind,
)
from pydocs_mcp.extraction.model.package_tree import build_package_tree
from pydocs_mcp.extraction.model.tree_flatten import flatten_to_chunks

__all__ = [
    "STRUCTURAL_ONLY_KINDS",
    "DocumentNode",
    "NodeKind",
    "build_package_tree",
    "flatten_to_chunks",
]
