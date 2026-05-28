"""NodeReference value object — one row of the reference graph (spec §4.2).

Immutable. ``to_node_id`` is ``None`` for unresolved edges (stdlib refs,
external packages not yet indexed, aliased re-exports we can't trace).
Unresolved edges stay queryable by ``to_name`` so users see the intent
even when the target isn't in the index.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydocs_mcp.extraction.reference_kind import ReferenceKind


@dataclass(frozen=True, slots=True)
class NodeReference:
    """One row of the cross-node reference graph (spec §4.2).

    Identity is the natural PK ``(from_package, from_node_id, to_name,
    kind)`` — matches the SQLite ``node_references`` PRIMARY KEY (spec
    §6.1). ``to_node_id`` is the resolved target's ``qualified_name``
    when the resolver found one in the indexed-qname universe, else
    ``None``.
    """

    from_package: str
    from_node_id: str
    to_name: str
    to_node_id: str | None
    kind: ReferenceKind
