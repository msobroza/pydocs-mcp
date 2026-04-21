"""build_package_tree — module-path trie assembly (spec §12.2).

Assembles a package arborescence from flat ``dict[module_name → MODULE DocumentNode]``
by interpreting dotted module names as a path trie. Creates synthetic
PACKAGE + SUBPACKAGE nodes for intermediate path segments; the input
DocumentNodes become leaves.

Example::

    trees = {
        "requests.adapters": <MODULE node for requests/adapters.py>,
        "requests.sessions": <MODULE node for requests/sessions.py>,
        "requests.auth.basic": <MODULE node for requests/auth/basic.py>,
    }
    root = build_package_tree("requests", trees)
    # root:
    #   PACKAGE requests
    #     MODULE requests.adapters (actual loaded tree)
    #     SUBPACKAGE requests.auth
    #       MODULE requests.auth.basic (actual loaded tree)
    #     MODULE requests.sessions (actual loaded tree)

STRUCTURAL_ONLY_KINDS (PACKAGE, SUBPACKAGE) synthesized here never carry
text — they are pure path scaffolding, consistent with the rule in
``document_node.py``.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping

from pydocs_mcp.extraction.document_node import DocumentNode, NodeKind


def build_package_tree(
    package: str, trees: Mapping[str, DocumentNode],
) -> DocumentNode:
    """Build a PACKAGE arborescence root that nests the given modules.

    ``package`` is the top-level dotted name of the arborescence root (typically
    the distribution's import name, or ``"__project__"`` for the project itself).
    ``trees`` maps dotted module names to the MODULE ``DocumentNode`` produced
    by the per-file chunker.
    """
    children = _build_tree_nodes(trees, current_prefix=package)
    return DocumentNode(
        node_id=package,
        qualified_name=package,
        title=package,
        kind=NodeKind.PACKAGE,
        source_path=package,
        start_line=1,
        end_line=1,
        text="",
        content_hash="",
        children=tuple(children),
        extra_metadata={"module": package, "module_count": len(trees)},
    )


def _build_tree_nodes(
    trees: Mapping[str, DocumentNode],
    *,
    current_prefix: str,
) -> list[DocumentNode]:
    """Recursive trie walk. ``current_prefix`` is the dotted path so far."""
    # Group modules by the next path segment after current_prefix. A module
    # at exactly current_prefix becomes a leaf at this level (key ""); deeper
    # dotted names group under the next segment, which either becomes a
    # SUBPACKAGE (multiple descendants) or a MODULE leaf (single descendant
    # at exactly next_prefix).
    by_next_segment: defaultdict[str, dict[str, DocumentNode]] = defaultdict(dict)
    for module_name, node in trees.items():
        if module_name != current_prefix and not module_name.startswith(
            current_prefix + ".",
        ):
            continue
        if module_name == current_prefix:
            by_next_segment[""][module_name] = node
        else:
            rest = module_name[len(current_prefix) + 1:]
            next_seg = rest.split(".", 1)[0]
            by_next_segment[next_seg][module_name] = node

    children: list[DocumentNode] = []
    for seg, seg_modules in sorted(by_next_segment.items()):
        if seg == "":
            # Leaf module at this level — emit the MODULE node directly.
            for _name, node in seg_modules.items():
                children.append(node)
            continue
        next_prefix = f"{current_prefix}.{seg}"
        # Pure leaf: next_prefix itself is present AND it's the only descendant
        # under this segment — no __init__-plus-submodule case.
        if next_prefix in seg_modules and len(seg_modules) == 1:
            children.append(seg_modules[next_prefix])
            continue
        # Has descendants → SUBPACKAGE with recursive children. If next_prefix
        # itself is a MODULE in seg_modules (package __init__), it becomes a
        # leaf under the SUBPACKAGE via the empty-seg branch one level down.
        sub_children = _build_tree_nodes(seg_modules, current_prefix=next_prefix)
        children.append(DocumentNode(
            node_id=next_prefix,
            qualified_name=next_prefix,
            title=seg,
            kind=NodeKind.SUBPACKAGE,
            source_path=next_prefix.replace(".", "/"),
            start_line=1,
            end_line=1,
            text="",
            content_hash="",
            children=tuple(sub_children),
            extra_metadata={"module": next_prefix},
        ))
    return children


__all__ = ("build_package_tree",)
