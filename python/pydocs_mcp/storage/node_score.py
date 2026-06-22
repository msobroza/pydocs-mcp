"""NodeScore value object — one row of the ``node_scores`` table.

Immutable per-node graph signals computed at index time over the reference
graph (``node_references``): ``in_degree`` (how many resolved edges point at
the node), ``pagerank`` (global importance), and ``community`` (Louvain
community id, -1 if unassigned). Consumed by the centrality-prior and
community-diversity rerank steps, keyed on ``qualified_name``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeScore:
    """One ``node_scores`` row. Identity = ``(package, qualified_name)``."""

    package: str
    qualified_name: str
    in_degree: int = 0
    pagerank: float = 0.0
    community: int = -1
